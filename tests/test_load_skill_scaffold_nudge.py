"""load_skill on a MISSING skill must nudge the agent to research + scaffold it,
not dead-end (prompt-audit follow-up 2026-07-01).

CONTRACTs list skills_needed (e.g. "thermodynamics", "cad-design") that often do
NOT exist yet — so load_skill fails and the agent silently proceeds WITHOUT the
domain know-how (root cause of the gasifier throat being physically implausible).
The failure message should point at the self-evolve path the project already has:
research the domain, then scaffold_skill to capture it — turning a dead-end into
an action, per CLAUDE.md ("if a needed skill does not exist, create it")."""

from __future__ import annotations

import pytest

from app.mcp.tools.skill_loader import _handle_load_skill


@pytest.mark.asyncio
async def test_missing_skill_nudges_scaffold():
    out = await _handle_load_skill({"skill_name": "thermodynamics"})
    low = out.lower()
    assert "not found" in low  # still reports the miss
    assert "scaffold_skill" in low  # points at the create path
    # and tells the agent to build it from real knowledge, not guess
    assert "research" in low or "web_search" in low


@pytest.mark.asyncio
async def test_existing_skill_still_loads_without_nudge():
    # A real skill loads normally; no scaffold nudge on success.
    out = await _handle_load_skill({"skill_name": "research"})
    assert "SKILL LOADED" in out
    assert "scaffold_skill" not in out.lower()
