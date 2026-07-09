"""P0-B (CC-10/CC-12): ROE generator must honor the planner's intent.

Audit 2026-06-22 found the ROE generator running an INDEPENDENT LLM
classification, divorced from the planner's deliverable contract. So a task the
planner scoped as RESEARCH/ANALYZE/BUILD (knowledge or an own-infra artifact)
could still be handed the full offensive pentest playbook — e.g. `6fd9f235`
(CONTRACT=RESEARCH but ROE=attack template) and `d1143db6` (BUILD on own infra
but ROE target=github.com with exploitation phases).

Fix (fail-closed): the planner's contract mode is fed to `generate_roe`. When
the planner says the deliverable is RESEARCH/ANALYZE/BUILD, the offensive
pentest playbook is NOT rendered even if the generator's own classifier says
"pentest" — the safe branch wins on disagreement. An explicit offensive
engagement (mode MIXED/None) is unaffected.
"""

from __future__ import annotations

import pytest

from app.security import roe_generator


class _PentestLLM:
    """Generator's own classifier insists every task is an offensive pentest."""

    async def chat(self, messages, temperature=0.0, max_tokens=512):
        return {"content": '{"roe_type": "pentest", '
                           '"engagement_type": "Red Team (Full Scope)", '
                           '"engagement_goal": "RCE", "operation_type": ""}'}


_ATTACK_MARKERS = ("Phase 3: Exploitation", "Phase 4: Post-Exploitation")


def _has_attack_playbook(roe: str) -> bool:
    low = roe.lower()
    return any(m in roe for m in _ATTACK_MARKERS) or "reverse shell" in low


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["RESEARCH", "ANALYZE", "BUILD"])
async def test_research_intent_blocks_attack_playbook(mode):
    # Planner scoped this as knowledge/own-artifact work; the generator's own
    # classifier disagrees and says "pentest". Safe branch must win.
    roe = await roe_generator.generate_roe(
        task_description="kiểm tra bảo mật website của tôi",
        task_id="testtask",
        llm=_PentestLLM(),
        task_type="security",
        contract_mode=mode,
    )
    assert not _has_attack_playbook(roe), f"mode={mode} still rendered attack playbook"


@pytest.mark.asyncio
async def test_offensive_engagement_still_full_playbook():
    # MIXED/no contract mode = a genuine offensive engagement → full playbook.
    roe = await roe_generator.generate_roe(
        task_description="red team full scope against 10.0.0.5",
        task_id="testtask",
        llm=_PentestLLM(),
        task_type="security",
        contract_mode="MIXED",
    )
    assert _has_attack_playbook(roe)


@pytest.mark.asyncio
async def test_write_roe_passes_contract_mode(monkeypatch, workspace_dir):
    """write_roe must forward the planner's contract mode to generate_roe."""
    from app.orchestration.nexus_orchestrator import NexusOrchestrator

    captured = {}

    async def _fake_generate_roe(**kwargs):
        captured.update(kwargs)
        return "ROE STUB"

    monkeypatch.setattr(
        "app.security.roe_generator.generate_roe", _fake_generate_roe
    )

    orch = NexusOrchestrator(llm=object())
    plan_data = {
        "task_type": "security",
        "deliverable_contract": {"mode": "RESEARCH"},
    }
    await orch.write_roe("testtask", plan_data, user_query="đánh giá web của tôi")

    assert captured.get("contract_mode") == "RESEARCH"
