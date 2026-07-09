from app.core.token_governor import (
    TokenEstimator, _char_estimate, _content_text, BudgetTracker, BudgetStatus,
    get_task_budget, clear_task_budget, LoopMonitor, _stable_tool_key,
)


def _msgs(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def test_content_text_includes_tool_call_arguments():
    # tool_call arguments cost tokens too — _content_text must fold them in so the
    # estimate isn't blind to a message that's mostly tool args with little content.
    plain = {"role": "assistant", "content": "ok"}
    with_tools = {
        "role": "assistant",
        "content": "ok",
        "tool_calls": [
            {"function": {"name": "web_fetch", "arguments": '{"url": "http://example.com/very/long/path"}'}},
        ],
    }
    assert "example.com" in _content_text(with_tools)
    assert len(_content_text(with_tools)) > len(_content_text(plain))


def test_content_text_handles_block_list_content():
    # content may be a list of blocks (Anthropic shape) — extract their text.
    msg = {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
    out = _content_text(msg)
    assert "hello" in out and "world" in out


def test_char_estimate_latin_vs_cjk():
    # Equal CHARACTER COUNT, different script: CJK (~2 chars/token) must estimate
    # more tokens than Latin (~4 chars/token) for the SAME number of characters.
    latin_text = "a" * 600
    cjk_text = "正" * 600
    assert len(latin_text) == len(cjk_text)  # guard the premise: equal char count
    latin = _char_estimate(_msgs(latin_text))
    cjk = _char_estimate(_msgs(cjk_text))
    assert latin > 0 and cjk > 0
    assert cjk > latin
    assert cjk / latin > 1.8  # ~2:1 density ratio (Latin 4.0 vs CJK 2.0 chars/token)


def test_estimate_returns_positive_and_handles_empty():
    est = TokenEstimator(use_tiktoken=False)  # force char fallback (deterministic)
    assert est.estimate(_msgs("some content here")) > 0
    assert est.estimate([]) == 0


def test_observe_calibrates_toward_real():
    est = TokenEstimator(use_tiktoken=False)
    model = "claude-opus-4-8"
    # Pretend our raw estimate is consistently HALF of real tokens.
    for _ in range(30):
        raw = est._raw_estimate(_msgs("abcd " * 100), model)  # uncalibrated baseline
        est.observe(model, est_tokens=raw, real_prompt_tokens=raw * 2)
    # After calibration, estimate() ≈ 2× the raw baseline (correction → ~2.0)
    raw_now = est._raw_estimate(_msgs("abcd " * 100), model)
    calibrated = est.estimate(_msgs("abcd " * 100), model)
    assert calibrated >= raw_now * 1.7  # converged most of the way to 2.0


def test_observe_clamps_correction():
    est = TokenEstimator(use_tiktoken=False)
    model = "weird"
    # Feed an absurd ratio → correction must clamp to [0.5, 2.0]
    for _ in range(50):
        est.observe(model, est_tokens=10, real_prompt_tokens=10_000)
    assert est._correction[model] <= 2.0


def test_budget_levels():
    bt = BudgetTracker(limit=1000, warn_ratio=0.80, hard_stop_ratio=0.95)
    assert bt.status().level == "ok"
    bt.add({"total_tokens": 850})
    assert bt.status().level == "warn"
    bt.add({"total_tokens": 120})  # 970/1000
    assert bt.status().level == "hard_stop"
    assert bt.used == 970


def test_budget_monotonic_and_failopen():
    bt = BudgetTracker(limit=1000)
    bt.add(None)            # fail-open, no crash
    bt.add({})              # no total_tokens key
    bt.add({"total_tokens": 100})
    bt.add({"total_tokens": 0})
    assert bt.used == 100   # only real usage counts, never decreases


def test_budget_reset_clears_cumulative_effort():
    # E1 root cause: a reused AgentWorker carries ONE BudgetTracker across every
    # turn. Without a per-turn reset, `_used` accumulates monotonically until it
    # trips hard_stop "before the first tool call" on later turns even though each
    # individual turn stayed well under budget. reset() gives the caller a way to
    # scope the cumulative-effort budget to one execute()/react-loop.
    bt = BudgetTracker(limit=1000, warn_ratio=0.80, hard_stop_ratio=0.95)
    bt.add({"total_tokens": 970})
    assert bt.status().level == "hard_stop"
    bt.reset()
    assert bt.used == 0
    assert bt.status().level == "ok"
    # after reset the tracker behaves like a fresh one
    bt.add({"total_tokens": 100})
    assert bt.used == 100
    assert bt.status().level == "ok"


def test_budget_survives_many_turns_when_reset_each_turn():
    # Reproduces the budget-death scenario: 9 turns, each consuming 70k tokens,
    # against a 400k per-react-loop budget. WITHOUT reset the tracker trips
    # hard_stop from ~turn 6 onward (6×70k=420k > 380k). WITH a per-turn reset the
    # tracker never trips because no single turn exceeds the limit.
    bt = BudgetTracker(limit=400_000, warn_ratio=0.80, hard_stop_ratio=0.95)
    for _turn in range(9):
        bt.reset()  # per-turn scoping (what the worker's reset block must do)
        bt.add({"total_tokens": 70_000})
        assert bt.status().level == "ok", f"turn {_turn} tripped despite low per-turn use"


def test_task_budget_registry_shared():
    clear_task_budget("task-X")
    a = get_task_budget("task-X", limit=5000)
    b = get_task_budget("task-X", limit=5000)
    assert a is b           # same instance for same task_id
    a.add({"total_tokens": 50})
    assert b.used == 50
    clear_task_budget("task-X")
    c = get_task_budget("task-X", limit=5000)
    assert c.used == 0      # cleared → fresh


def test_stable_key_ignores_volatile_and_buckets_identifier():
    # Same identifying field (url) → same key even if other args differ
    k1 = _stable_tool_key("web_fetch", {"url": "http://x.com", "timeout": 5})
    k2 = _stable_tool_key("web_fetch", {"url": "http://x.com", "timeout": 99})
    assert k1 == k2
    k3 = _stable_tool_key("web_fetch", {"url": "http://y.com"})
    assert k1 != k3


def test_loop_monitor_warn_then_hardstop():
    lm = LoopMonitor(warn_count=3, hard_stop_count=5)
    args = {"path": "/a/b.py"}
    levels = [lm.record("read_file", args).level for _ in range(5)]
    assert levels == ["ok", "ok", "warn", "ok", "hard_stop"]


def test_loop_monitor_distinct_tools_dont_accumulate():
    lm = LoopMonitor(warn_count=3, hard_stop_count=5)
    assert lm.record("read_file", {"path": "/a"}).level == "ok"
    assert lm.record("read_file", {"path": "/b"}).level == "ok"
    assert lm.record("web_search", {"query": "x"}).level == "ok"
    # none repeated 3× → all ok


def test_token_budget_exhausted_is_honest_halt():
    # A 'complete' at low progress is normally blocked (CC-7), but WITH a
    # blocked justification (token_budget_exhausted) it must be allowed.
    from app.orchestration.termination import evaluate_completion
    d = evaluate_completion(
        llm_decision="complete",
        progress_pct=40,
        turn_count=8,
        progress_history=[35, 38, 40],
        prev_progress=38,
        is_security_task=False,
        has_blocked_justification=True,   # token_budget_exhausted supplies this
    )
    assert d.complete is True
