"""_merge_into must not let a terse eval-note clobber the agent's own conclusion (W-3).

Context-arch audit (2026-07-03): the end-of-turn write sets did_what from the
orchestrator's eval notes, and _merge_into does `if final.did_what: draft.did_what =
final.did_what` — an UNCONDITIONAL overwrite. When the eval note is terse ("Ran the
script.") it clobbers the agent's richer extracted conclusion ("...closure=4.197%,
CGE=62.93%, net=20.0kW — all in range"), losing the self-computed numbers. Keep the
richer text (or fold the agent's conclusion in) instead of blind overwrite.
"""

from __future__ import annotations

from app.orchestration.context_ledger import LedgerEntry, ContextLedger


def _entry(did_what, key_data=None):
    return LedgerEntry(turn=1, agent_role="physicist", agent_name="P",
                       did_what=did_what, key_data=key_data or [])


def test_terse_final_does_not_erase_rich_draft():
    draft = _entry(
        "Wrote design_basis.py and ran it: closure=4.197%, CGE=62.93%, net=20.0kW — "
        "all within the documented ranges; single source design_results.json."
    )
    final = _entry("Ran the script.")
    ContextLedger._merge_into(draft, final)
    # The self-computed numbers must survive.
    assert "4.197" in draft.did_what
    assert "CGE" in draft.did_what


def test_rich_final_still_wins():
    draft = _entry("Did some work.")
    final = _entry(
        "Completed the energy balance with closure 4.2% and CGE 63% verified against "
        "the reference; net electrical output 20 kW after parasitic load."
    )
    ContextLedger._merge_into(draft, final)
    assert "closure 4.2" in draft.did_what


def test_empty_final_keeps_draft():
    draft = _entry("Agent's detailed conclusion with the key result = 42 kW.")
    final = _entry("")
    ContextLedger._merge_into(draft, final)
    assert "42 kW" in draft.did_what
