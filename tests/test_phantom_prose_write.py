"""Phantom prose-write: assignment demands a named file, turn writes nothing (E-R7).

Root cause E-R7 (tri-run audit 2026-07-02, task-e275e09b Opus). The phantom-produce
guard caught three shapes: a dispatched-but-failed produce tool (A), a prose
tool-call JSON blob (B), and pasted file-writing CODE in a fence (C). But on the
Opus run the writer composed a COMPLETE 67K-char markdown report INLINE in its
answer and never called write_file — no code idiom, no tool JSON — so it slipped
through with success=true and 0 bytes on disk.

Content can't distinguish "composed a report but forgot to save it" from a
legitimate analysis turn — both are long prose. The distinguishing signal is the
ASSIGNMENT INTENT: when the assignment/success-criteria names a concrete file to
WRITE (a create verb + a filename with an extension) and the turn lands zero
successful write, the deliverable was not produced.

The detector is structural (verb + filename.ext), never a length/keyword heuristic,
so an "analyze the data" / "review the design" turn is never tripped.
"""

from __future__ import annotations

from app.orchestration.agent_worker import (
    _assignment_demands_file_write,
    _WRITE_FILE_TOOLS,
)


# ── R2/P100-1: assignment demands a FILE, agent ran other produce-tools but no write ──

def test_write_tools_set_is_files_only():
    """The write-tool set must be file-writers only — NOT http_request/execute_code,
    so 'ran http_request but never wrote the file' still counts as no file written."""
    assert "write_file" in _WRITE_FILE_TOOLS
    assert "edit_file" in _WRITE_FILE_TOOLS
    # These produce side effects but do NOT write the declared deliverable file.
    assert "http_request" not in _WRITE_FILE_TOOLS
    assert "execute_code" not in _WRITE_FILE_TOOLS
    assert "execute_command" not in _WRITE_FILE_TOOLS
    assert "audit_log" not in _WRITE_FILE_TOOLS


# ── positive: assignment names a file to write → demands a write ──────────────

def test_write_verb_plus_named_file_demands_write():
    assert _assignment_demands_file_write(
        task="Compile the consolidated report",
        success_criteria="FINAL-REPORT.md is written to _work/redteam-pln/ with all 6 phases",
    ) is True


def test_vietnamese_create_verb_plus_named_file():
    assert _assignment_demands_file_write(
        task="Tạo bảng vật tư cho hệ thống",
        success_criteria="bom.csv có cột số lượng/đơn giá và tổng chi phí",
    ) is True


def test_save_json_demands_write():
    assert _assignment_demands_file_write(
        task="Run the balance script and save results.json",
        success_criteria="results.json exists and parses",
    ) is True


# ── negative: analysis / review turns must NOT be tripped ─────────────────────

def test_pure_analysis_assignment_does_not_demand_write():
    assert _assignment_demands_file_write(
        task="Analyze the reconnaissance data and identify the top risks",
        success_criteria="Key risks identified with rationale",
    ) is False


def test_review_assignment_does_not_demand_write():
    assert _assignment_demands_file_write(
        task="Independently re-derive the numbers and try to refute them",
        success_criteria="Numbers verified or contradictions surfaced",
    ) is False


def test_filename_without_write_verb_is_not_a_write_demand():
    # Merely mentioning a file to READ/consult is not a write demand.
    assert _assignment_demands_file_write(
        task="Read results.json and summarize the energy balance in your answer",
        success_criteria="Summary covers closure, LHV, CGE",
    ) is False


def test_write_verb_without_a_filename_is_not_tripped():
    # "write up your analysis" with no file target is a prose deliverable.
    assert _assignment_demands_file_write(
        task="Write up your analysis of the attack surface",
        success_criteria="Analysis is thorough and well-reasoned",
    ) is False


def test_empty_inputs_are_safe():
    assert _assignment_demands_file_write(task="", success_criteria="") is False
    assert _assignment_demands_file_write(task=None, success_criteria=None) is False
