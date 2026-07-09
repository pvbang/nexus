"""Phantom-write detection for UNEXECUTED file-write code (audit S1, 2026-06-30).

Two phantom-write shapes reached disk-claims with tools_used:[] yet success:true:
  - deepseek T2: a ```python fence containing ``open(path,"w").write(...)`` that was
    NEVER executed (no execute_code tool call), then prose "I created the file".
  - gasifier: pasted a full self-contained ``solve.py`` (``open(...,"w")`` +
    ``json.dump(..., f)``) in prose without dispatching write_file/execute_code.

The existing recovery (recover_tool_calls) only catches a leaked TOOL-CALL shape
(`<invoke>` / delimiter-adjacent JSON), so neither shape was flagged → the F2
phantom-produce guard never fired → success:true on a 0-byte turn.

``_describes_unexecuted_file_write`` adds a STRUCTURAL signal (a file-writing code
idiom inside a fenced block), NOT prose keyword matching: a turn that merely SAYS
"I wrote the file" is intentionally NOT matched (analysis/wrap-up turns are
legitimate — audit phantom-write doctrine). It is wired into _attempted_produce so
the existing ``_phantom_produce = _attempted_produce and not _produced_ok`` gate
fails the turn only when nothing actually reached disk.
"""

from __future__ import annotations

from app.orchestration.agent_worker import _describes_unexecuted_file_write as d


# ── POSITIVE: file-write code idioms inside a fenced block ──

def test_open_write_in_python_fence_is_detected():
    content = (
        "I will create the file.\n"
        "```python\n"
        'with open("phase1_ssl.txt", "w", encoding="utf-8") as f:\n'
        "    f.write(ssl_content)\n"
        "```\n"
        "Result: file created."
    )
    assert d(content) is True


def test_json_dump_to_file_in_fence_is_detected():
    content = (
        "```python\n"
        "import json\n"
        'with open(out_path, "w") as f:\n'
        "    json.dump(results, f, indent=2)\n"
        "```\n"
    )
    assert d(content) is True


def test_write_text_pathlib_in_fence_is_detected():
    content = (
        "```python\n"
        'Path("report.md").write_text(report, encoding="utf-8")\n'
        "```\n"
    )
    assert d(content) is True


# ── NEGATIVE: must NOT fire on legitimate / non-produce content ──

def test_prose_only_claim_is_not_detected():
    # Prose "I wrote the file" with NO code idiom — intentionally NOT matched
    # (keyword-prose matching is forbidden; analysis turns stay valid).
    content = (
        "I have overwritten phase1_ssl.txt with simulated SSL data. "
        "Issuer: Let's Encrypt. Done. Paste output or type next."
    )
    assert d(content) is False


def test_reading_a_file_in_fence_is_not_detected():
    # Read-only code is not a produce attempt.
    content = (
        "```python\n"
        'with open("input.json") as f:\n'
        "    data = f.read()\n"
        "```\n"
    )
    assert d(content) is False


def test_write_idiom_outside_any_fence_is_not_detected():
    # A bare mention in prose (no fenced code block) is not a structural signal.
    content = 'The script calls open(path, "w") then f.write(x) to persist results.'
    assert d(content) is False


def test_empty_content_is_not_detected():
    assert d("") is False
    assert d(None) is False  # type: ignore[arg-type]
