"""Regression for the ``## Turn`` marker family (task 3c44a93f bug #2, Bước 2).

DECISIONS.md entries start with a line-anchored ``## Turn N``. The reader/writer
split entries with ``re.split(r"(?=## Turn \\d+)", body)`` — a NON-anchored
lookahead. The substring ``## Turn N`` is contained inside ``### Turn N`` (3
hashes), so if any entry's *reasoning text* quotes a ``### Turn N`` plan/phase
heading (exactly what the 3c44a93f orchestrator did, repeatedly), the split
fragments mid-entry and corrupts the kept-entries window.

These tests pin the line-anchored behaviour: an embedded ``### Turn N`` inside
reasoning must NOT be treated as an entry boundary.
"""

from __future__ import annotations

import app.orchestration.nexus_orchestrator as no
from app.core import task_paths
from app.orchestration.nexus_orchestrator import NexusOrchestrator


_DECISIONS_WITH_EMBEDDED_PLAN_HEADING = """# Orchestrator & Evolution Decisions

## Turn 1
### Nexus Orchestrator
**Progress:** 10% | **Decision:** continue
**Reasoning:** early work

## Turn 2
### Nexus Orchestrator
**Progress:** 20% | **Decision:** continue
**Reasoning:** The plan says
### Turn 6: PHASE 6 — Cleanup
but we are still at phase 1. This is one coherent entry.
"""


def test_read_decisions_does_not_fragment_on_embedded_plan_heading(
    tmp_path, monkeypatch, workspace_dir
):
    """An embedded ``### Turn 6`` in reasoning must stay inside its entry.

    Forces the backstop-trim path (which runs the split) with a tiny backstop,
    then asserts the most recent kept entry is the whole Turn 2 block — not a
    fragment cut at the embedded ``### Turn 6`` line.
    """
    # Tiny backstop so the split/trim path runs deterministically.
    monkeypatch.setattr(no, "_DECISIONS_READ_BACKSTOP", 120, raising=True)

    task_id = "dec00001"
    path = task_paths.context_file(task_id, "DECISIONS.md")
    path.write_text(_DECISIONS_WITH_EMBEDDED_PLAN_HEADING, encoding="utf-8")

    orch = NexusOrchestrator(llm=None)
    body = orch._read_decisions(task_id)

    # The kept window must contain Turn 2's reasoning intact, including the
    # embedded heading line and the sentence AFTER it — proving the entry was
    # not split at "### Turn 6".
    assert "This is one coherent entry." in body
    assert "The plan says" in body
    # And it must not have produced a standalone fragment that begins at the
    # embedded heading (i.e. the heading is not promoted to an entry boundary).
    assert not body.lstrip().startswith("### Turn 6")
