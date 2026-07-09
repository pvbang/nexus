"""Tests for lenient JSON parsing of LLM planning output (audit A8 root cause).

Both audited tasks fell back to a 1-researcher generic plan because plan_task's
JSON parse failed. _parse_json (and its twin json_parse.parse_json_response) only
handled clean / fenced / greedy-first-brace JSON, so common LLM output shapes —
truncated JSON (max_tokens hit on a big plan), prose containing a brace before the
real object, trailing commas — fell through to the fallback plan, which destroys
team decomposition + deliverable_contract for a complex task.

These pin the lenient recovery behaviour without an LLM.
"""

from __future__ import annotations

from app.orchestration.json_parse import parse_json_response


# ───────────────────────── still works: existing shapes ─────────────────────────


def test_clean_json():
    assert parse_json_response('{"task_type": "security", "complexity": "expert"}') == {
        "task_type": "security",
        "complexity": "expert",
    }


def test_fenced_json():
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_then_json():
    assert parse_json_response("Here is the plan:\n{\"a\": 1}") == {"a": 1}


def test_nonsense_returns_none():
    # Contract preserved: genuinely unparseable input still returns None.
    assert parse_json_response("this has no json at all") is None
    assert parse_json_response("") is None


# ───────────────────────── new: lenient recovery ─────────────────────────


def test_trailing_comma_is_repaired():
    # LLMs frequently emit trailing commas; json.loads rejects them.
    out = parse_json_response('{"a": 1, "b": 2,}')
    assert out == {"a": 1, "b": 2}


def test_trailing_comma_in_array():
    out = parse_json_response('{"agents": ["a", "b",], "n": 3}')
    assert out == {"agents": ["a", "b"], "n": 3}


def test_prose_with_brace_before_real_object():
    # Greedy {...} grabbed from the first brace in prose → invalid. Balanced
    # extraction must find the real top-level object instead.
    text = 'I will model it as {role: x} then output the plan:\n{"task_type": "security", "ok": true}'
    out = parse_json_response(text)
    assert out is not None
    assert out.get("task_type") == "security"


def test_truncated_object_recovers_complete_prefix():
    # max_tokens cut the response mid-way through execution_plan. The earlier,
    # fully-formed keys (task_type, complexity, selected_agents) must survive so a
    # complex task keeps its real team instead of collapsing to the fallback.
    truncated = (
        '{"task_type": "security", "complexity": "expert", '
        '"selected_agents": [{"role": "pentester", "name": "Pentester"}], '
        '"execution_plan": [{"turn": 1, "objective": "recon", "assign'
    )
    out = parse_json_response(truncated)
    assert out is not None
    assert out.get("task_type") == "security"
    assert out.get("complexity") == "expert"
    assert isinstance(out.get("selected_agents"), list)
    assert out["selected_agents"][0]["role"] == "pentester"


def test_truncated_inside_string_still_recovers_prior_keys():
    truncated = '{"task_type": "research", "intent_analysis": "design a biomass gas'
    out = parse_json_response(truncated)
    assert out is not None
    assert out.get("task_type") == "research"


def test_fenced_but_unclosed_fence_recovers():
    # Opened ```json fence, JSON truncated, fence never closed.
    text = '```json\n{"task_type": "analysis", "complexity": "complex"'
    out = parse_json_response(text)
    assert out is not None
    assert out.get("task_type") == "analysis"


# ───────────────────────── orchestrator _parse_json now delegates (A8) ─────────────────────────


def test_trailing_comma_repair_does_not_corrupt_string_values():
    # Review CRITICAL #1: stripping trailing commas must be string-aware. A comma
    # inside a string value followed by ] or } must survive untouched.
    out = parse_json_response('{"cmd": "sed -i s/x,]/y/ file", "ok": true,}')
    assert out == {"cmd": "sed -i s/x,]/y/ file", "ok": True}
    out2 = parse_json_response('{"note": "list ends ,] here", "n": 1,}')
    assert out2["note"] == "list ends ,] here"


def test_valid_preamble_object_does_not_shadow_the_real_plan():
    # Review #2: a small valid object in the preamble must not be returned instead
    # of the larger real plan that follows.
    text = 'I considered {} and decided. Plan:\n{"task_type": "security", "n": 5}'
    out = parse_json_response(text)
    assert out.get("task_type") == "security"
    text2 = 'Format: {"role": "x"}. Plan:\n{"task_type": "security", "selected_agents": [1, 2, 3]}'
    out2 = parse_json_response(text2)
    assert out2.get("task_type") == "security"


def test_two_top_level_objects_prefers_richer_one():
    # When several balanced objects parse, prefer the one with more keys (the plan),
    # not merely the first.
    out = parse_json_response('{"ack": true}\n{"task_type": "research", "complexity": "complex"}')
    assert out.get("task_type") == "research"


def test_orchestrator_parse_json_recovers_truncated_plan():
    # The whole point of A8: a truncated planning response must NOT collapse the
    # orchestrator to its 1-researcher fallback plan — the prefix keys survive.
    from app.orchestration.nexus_orchestrator import _parse_json

    truncated = (
        '{"task_type": "security", "complexity": "expert", '
        '"selected_agents": [{"role": "pentester"}, {"role": "exploit_developer"}], '
        '"deliverable_contract": {"definition_of_done": "live root proven"'
    )
    out = _parse_json(truncated)
    assert out is not None
    assert out["task_type"] == "security"
    assert len(out["selected_agents"]) == 2  # real team kept, not collapsed to 1


def test_truncation_dropping_selected_agents_is_backfilled_not_worse_than_fallback():
    # Review #3: when truncation lands INSIDE selected_agents the whole key is
    # dropped. A recovered plan missing core fields must be backfilled so it is
    # never worse than the plain fallback (which always has >=1 agent + 1 turn).
    from app.orchestration.nexus_orchestrator import _build_fallback_plan, _parse_json

    truncated = '{"task_type": "research", "complexity": "complex", "selected_agents": [{"role": "a"}, {"role": "exploit_d'
    recovered = _parse_json(truncated)
    assert recovered is not None
    assert "selected_agents" not in recovered  # the cut key was dropped honestly

    # The fallback always supplies the core fields used for the backfill.
    fb = _build_fallback_plan("do something")
    assert fb["selected_agents"] and fb["execution_plan"]
