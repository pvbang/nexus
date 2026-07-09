"""Agent THINKING prompt must defer authorization to the tool gate (3c44a93f follow-up).

The agent's decision to attack-or-refuse forms during the THINKING phase. That
prompt still said: "If it would require attacking infrastructure the user does
not own ... halting is the right call." For an in-scope partner engagement
(pln.co.id — a real company's own site, authorized by contract) the agent reads
"the user does not own this" and refuses — even though the EXECUTION prompt
(shown later) correctly says authorization is handled by the tool gate.

The two prompts must agree: the thinking prompt should defer target
authorization to the runtime gate (which permits in-scope targets and blocks the
rest), and reserve halting for genuine safety/harm — not for "ownership" the
agent cannot and need not judge.
"""

from __future__ import annotations

from app.orchestration.prompts import build_agent_thinking_prompt


def _thinking_prompt() -> str:
    return build_agent_thinking_prompt(
        agent_name="Recon Lead",
        agent_role="recon_specialist",
        personality="meticulous",
        expertise=["recon"],
        thinking_style="systematic",
        assignment="Recon the in-scope target",
        success_criteria="subdomains enumerated",
        shared_context="",
        previous_turns="",
        roe_context="## RULES OF ENGAGEMENT\nAuthorized engagement.",
    )


def test_thinking_prompt_defers_authorization_to_tool_gate():
    p = _thinking_prompt().lower()
    # Must tell the agent the gate handles authorization (consistent w/ execution).
    assert "gate" in p
    assert "authoriz" in p


def test_thinking_prompt_does_not_tell_agent_to_halt_on_ownership():
    """The brittle 'does not own → halt' line must be gone — ownership is not the
    agent's call, and it caused refusal of in-scope contracted targets."""
    p = _thinking_prompt().lower()
    assert "does not own" not in p


def test_thinking_prompt_still_allows_genuine_safety_halt():
    """Halting for genuine safety/harm is still legitimate — we defer AUTHORIZATION
    to the gate, we do not remove the safety valve."""
    p = _thinking_prompt().lower()
    assert "safe" in p or "harm" in p
