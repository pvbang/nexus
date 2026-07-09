"""The seed domain skill biomass-gasifier-sizing must be discoverable + loadable.

Written as a concrete example of capturing domain know-how as a skill (the path
CONTRACTs assume via skills_needed). It encodes the throat-vs-body sizing rule
whose absence produced the physically implausible 265-437mm throat in the
gasifier reruns."""

from __future__ import annotations

import pytest

from app.mcp.tools.skill_loader import _handle_load_skill


@pytest.mark.asyncio
async def test_gasifier_skill_loads_and_has_throat_rule():
    out = await _handle_load_skill({"skill_name": "biomass-gasifier-sizing"})
    assert "SKILL LOADED" in out
    # the core correction: throat sized by hearth load B_h, body by SGR — not max()
    assert "B_h" in out and "SGR" in out
    assert "80" in out and "120" in out  # the 80-120mm sanity range for ~20 kWe
