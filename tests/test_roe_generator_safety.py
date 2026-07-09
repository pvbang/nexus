"""P0-A (CC-10): ROE generator must not launder authorization or auto-arm.

Audit 2026-06-22 found the standing-authorization block telling agents that
"individual requests need not re-declare ownership" + "the operator is
responsible for holding this authorization in fact" — blanket self-granted
scope (authorization laundering). It also found ``generate_roe`` defaulting an
unclassifiable ("none") security task to the full pentest exploitation playbook.

These tests pin the safe behaviour: the standing block records WHO is
accountable without removing per-target ownership, public targets still demand
scope, and an unclassified task never renders the attack playbook.
"""

from __future__ import annotations

import types

import pytest

from app.security import roe_generator


def _fake_settings(standing: bool, org: str = "", basis: str = ""):
    return types.SimpleNamespace(
        roe_standing_authorization=standing,
        roe_authorizing_org=org,
        roe_authorization_basis=basis,
    )


@pytest.fixture
def standing_on(monkeypatch):
    """Enable deployment standing authorization with a (gov-sounding) org."""
    import app.config

    monkeypatch.setattr(
        app.config,
        "get_settings",
        lambda: _fake_settings(True, "National Cyber Security Operations",
                               "Standing government engagement mandate"),
    )


# --- _build_authorization_block --------------------------------------------

def test_standing_block_omits_laundering_clauses(standing_on):
    block = roe_generator._build_authorization_block()
    low = block.lower()
    assert "need not re-declare" not in low
    assert "holding this authorization in fact" not in low


def test_standing_block_still_records_accountable_org(standing_on):
    # Recording WHO is accountable is legitimate — we only strip the blanket
    # "no need to verify ownership" laundering, not the accountability record.
    block = roe_generator._build_authorization_block()
    assert "National Cyber Security Operations" in block


def test_standing_block_public_target_demands_scope(standing_on):
    # A real public domain must NOT be silently swept under standing scope —
    # the block has to surface a per-target ownership/scope requirement.
    block = roe_generator._build_authorization_block(target="hanoi.edu.vn")
    low = block.lower()
    assert "scope" in low or "ownership" in low
    # And it must not claim blanket coverage for that public target.
    assert "covered by this deployment's standing" not in low


# --- generate_roe: unclassified must not auto-arm --------------------------

class _NoneLLM:
    """LLM stub whose classifier always returns 'none'."""

    async def chat(self, messages, temperature=0.0, max_tokens=512):
        return {"content": '{"roe_type": "none", "engagement_type": "", '
                           '"engagement_goal": "", "operation_type": ""}'}


@pytest.mark.asyncio
async def test_generate_roe_none_does_not_render_attack_playbook():
    roe = await roe_generator.generate_roe(
        task_description="đánh giá website của tôi",
        task_id="testtask",
        llm=_NoneLLM(),
        task_type="security",
    )
    # The pentest exploitation playbook must never be auto-assigned to an
    # unclassified task.
    assert "Phase 3: Exploitation" not in roe
    assert "Phase 4: Post-Exploitation" not in roe
    assert "reverse shell" not in roe.lower()
    assert "/etc/shadow" not in roe


@pytest.mark.asyncio
async def test_generate_roe_explicit_pentest_still_full(standing_on):
    # Regression guard: a legitimately-classified pentest engagement must STILL
    # get the full playbook (the fix must not disarm authorized work).
    roe = await roe_generator.generate_roe(
        task_description="red team full scope against 10.0.0.5",
        task_id="testtask",
        roe_type="pentest",
        engagement_type="Red Team (Full Scope)",
        engagement_goal="RCE",
        task_type="security",
    )
    assert "Phase 3: Exploitation" in roe


# --- scope-aware authorization block (audit b2c44b12) ----------------------
# The agent reads ROE.md. For a public target that IS in the declared pentest
# scope, the ROE must say "authorized — proceed", NOT "standing authz does not
# cover it, need per-target proof" — otherwise the agent believes it is
# unauthorized and produces a simulation/playbook instead of the real engagement.
# An out-of-scope public target must STILL demand per-target proof (no laundering).


@pytest.fixture
def scope_with_pln(tmp_path, monkeypatch):
    import textwrap
    from app.security import pentest_scope as ps
    f = tmp_path / "pentest_scope.yaml"
    f.write_text(textwrap.dedent("""
        - org: "PT PLN Bug Bounty"
          document: "PLN-SEC-2026/06 ngày 22/06/2026"
          targets: ["*.pln.co.id"]
    """), encoding="utf-8")
    monkeypatch.setenv("PENTEST_SCOPE_FILE", str(f))
    ps.reload_scope()
    yield f
    monkeypatch.delenv("PENTEST_SCOPE_FILE", raising=False)
    ps.reload_scope()


def test_authorization_block_in_scope_public_target_says_authorized(standing_on, scope_with_pln):
    block = roe_generator._build_authorization_block(target="www.pln.co.id")
    low = block.lower()
    # Must affirm the per-target authorization exists (cite org/document).
    assert "authorized" in low
    assert "PT PLN Bug Bounty" in block or "PLN-SEC" in block
    # Must NOT tell the agent the target is uncovered / needs proof it already has.
    assert "does not cover" not in low
    assert "proceed only with per-target proof" not in low


def test_authorization_block_out_of_scope_public_target_still_demands_proof(standing_on, scope_with_pln):
    # google.com is NOT in this scope file → must keep the per-target proof demand.
    block = roe_generator._build_authorization_block(target="google.com")
    low = block.lower()
    assert "does not cover" in low or "per-target proof" in low
    assert "PT PLN" not in block
