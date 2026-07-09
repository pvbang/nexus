"""P2-D (CC-8): cap FINAL_REPORT growth + dedup repeated blocks.

Audit 2026-06-22: the report was built by appending one synthesized section per
ACTIVITY chunk with NO cap (`final_report.py` comment: "no _MAX_REPORT_SECTIONS
limit"), and the dedup compared HEADINGS only. Reports ballooned to 200-534 KB
with the same block pasted 13-18×.

These tests pin the assembly-level guards (pure helpers, no LLM):
  - _cap_report_parts bounds total size and drops near-duplicate sections.
  - a duplicate section pasted many times survives at most twice.
"""

from __future__ import annotations

from app.orchestration.final_report import FinalReportGenerator, _MAX_REPORT_CHARS


def _gen():
    return FinalReportGenerator(llm=None, task_id="t-bloat")


def test_cap_total_report_size():
    # 200 sections of 5 KB each = ~1 MB of parts; the cap must bound the result.
    parts = [f"## Section {i}\n" + ("x" * 5000) for i in range(200)]
    capped = _gen()._cap_report_parts(parts)
    assert len("\n\n".join(capped)) <= _MAX_REPORT_CHARS


def test_dedup_repeated_block_kept_at_most_twice():
    block = "## Findings\nThe rice gasifier outputs 20.0 kWe at steady state.\n"
    parts = [block] * 10 + ["## Unique\nsomething else\n"]
    capped = _gen()._cap_report_parts(parts)
    joined = "\n\n".join(capped)
    assert joined.count("The rice gasifier outputs 20.0 kWe at steady state.") <= 2
    assert "## Unique" in joined


def test_small_report_unchanged():
    parts = ["## A\nshort", "## B\nalso short"]
    capped = _gen()._cap_report_parts(parts)
    assert capped == parts
