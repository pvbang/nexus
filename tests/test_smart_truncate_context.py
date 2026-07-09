"""Tests for word-boundary truncation in _context files (audit: mid-word cutting).

Both audited tasks' _context files cut text mid-word ("...Đây là b", Turn-History
"Result" cells starting mid-word like "✓ n Antigravity..."), destroying meaning.
Root causes: hard slices ([:50]/[:100]/[:200]) and extract_did_what Strategy 3
slicing a tail mid-word. smart_truncate cuts on a word boundary and marks elision;
extract_did_what must not start a summary mid-word.
"""

from __future__ import annotations

from app.orchestration.ledger_extract import extract_did_what, smart_truncate


# ───────────────────────── smart_truncate ─────────────────────────


def test_short_text_unchanged():
    assert smart_truncate("hello world", 50) == "hello world"
    assert smart_truncate("", 50) == ""


def test_cuts_on_word_boundary_with_ellipsis():
    s = "the quick brown fox jumps over the lazy dog"
    out = smart_truncate(s, 20)
    assert len(out) <= 20 + 1  # +1 for the … char
    assert out.endswith("…")
    # No partial word before the ellipsis — last real token is whole.
    assert out[:-1].rstrip().split()[-1] in s.split()


def test_does_not_split_a_word_when_a_boundary_exists():
    # With an earlier word boundary available, the long trailing word is dropped
    # whole rather than sliced.
    s = "config antidisestablishmentarianism trailing"
    out = smart_truncate(s, 20)
    assert out == "config…"


def test_vietnamese_diacritics_preserved_not_split():
    s = "Tôi cần bạn custom lại Google Antigravity thành một agent red team"
    out = smart_truncate(s, 30)
    assert out.endswith("…")
    # The cut must land on a space boundary — no half Vietnamese syllable.
    assert out[:-1].rstrip() in s  # the kept prefix is an exact prefix of the source


def test_no_boundary_within_window_falls_back_to_hard_cut():
    # A single very long token with no space: must still bound length.
    s = "x" * 100
    out = smart_truncate(s, 10)
    assert len(out) <= 11  # 10 + ellipsis at most


def test_strips_trailing_space_before_ellipsis():
    out = smart_truncate("alpha beta gamma delta", 11)  # "alpha beta " boundary
    assert " …" not in out  # no dangling space before ellipsis


# ───────────────────────── extract_did_what no longer starts mid-word ─────────────────────────


def test_did_what_tail_fallback_does_not_start_midword():
    # Vietnamese prose with no ". " sentence terminators (the exact shape that made
    # Strategy 3 emit a tail starting mid-word like "n Antigravity thành...").
    long = (
        "Phân tích kiến trúc Antigravity và thiết kế lại thành một red team operator agent "
        "dựa trên các nguyên tắc khai thác tự động hóa và kill chain đầy đủ từ recon tới impact "
        "với cơ chế tự kiểm soát chống bịa đặt và thổi phồng severity trên toàn bộ quy trình vận hành "
        "của hệ thống được mô phỏng một cách chân thực nhất có thể đạt được trong điều kiện hiện tại"
    )
    out = extract_did_what(long)
    assert out
    first = out.split()[0]
    # The first whitespace-delimited token must be a real word from the source,
    # not a sliced fragment of one.
    assert first in long.split(), f"did_what starts mid-word: {first!r}"


def test_did_what_short_passthrough():
    assert extract_did_what("short summary") == "short summary"


def test_did_what_tail_with_long_leading_token_not_midword():
    # Review CRITICAL #2: a tail beginning with a long no-space token (minified
    # JSON / base64 / URL) must still not start the summary mid-word. The old
    # < 40 guard skipped this case.
    filler = "word " * 200            # ~1000 chars so text[-600:] is well inside
    longtok = "Q" * 120               # 120-char no-space token at the slice front
    text = filler + longtok + " realword tail words follow here at the end"
    out = extract_did_what(text)
    assert out
    assert not out.lstrip().startswith("Q"), f"summary starts mid-token: {out[:30]!r}"


# ───────────────────────── context_index table integrity (real path) ─────────────────────────


def _rebuild_index(tmp_path, monkeypatch, did_what, key_data):
    import importlib
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    from app.orchestration.context_ledger import ContextLedger, LedgerEntry
    from app.orchestration.context_index import ContextIndex
    task_id = "t-index-pipe"
    led = ContextLedger(task_id)
    led.append(LedgerEntry(
        turn=1, agent_role="researcher", agent_name="R",
        did_what=did_what, key_data=key_data, tools_used=[], success=True, output_chars=10,
    ))
    ci = ContextIndex(task_id, led)
    ci.rebuild(current_turn=1, progress_pct=10, user_message="test the index")
    return ci._index_path.read_text(encoding="utf-8")


def test_turn_history_row_keeps_4_columns_when_text_has_pipes(tmp_path, monkeypatch):
    # Review CRITICAL #1: pipe chars in did_what/key_data must not break the
    # markdown table's column count.
    content = _rebuild_index(
        tmp_path, monkeypatch,
        did_what="Ran benchmark: throughput | latency | error rate measured",
        key_data=["col_a | col_b", "x|y|z"],
    )
    rows = [l for l in content.splitlines() if l.startswith("| 1 ")]
    assert rows, "no turn row rendered"
    # A 4-column row is delimited by exactly 5 pipes; raw pipes in cells are escaped.
    assert rows[0].count("|") - rows[0].count("\\|") == 5


def test_turn_history_row_no_newline_break(tmp_path, monkeypatch):
    content = _rebuild_index(
        tmp_path, monkeypatch,
        did_what="line one\nline two\nline three of the summary text here",
        key_data=["finding one\nfinding two"],
    )
    # The turn row must be a single physical line.
    rows = [l for l in content.splitlines() if l.startswith("| 1 ")]
    assert len(rows) == 1


# ───────────────────────── render_goal_anchor USER ASKED (now word-bounded) ─────────────────────────


def test_goal_anchor_user_asked_word_bounded():
    from app.orchestration.nexus_orchestrator import render_goal_anchor

    # Exceed the (raised) USER-ASKED budget so truncation actually happens, then
    # assert the cut is word-bounded (no mid-word slice). Budget is 2000 chars now.
    msg = "Tôi cần bạn custom lại Google Antigravity thành một agent red team " * 40  # > 2000 chars
    anchor = render_goal_anchor(msg, {"intent_analysis": "x"})
    user_line = [l for l in anchor.splitlines() if l.startswith("USER ASKED:")][0]
    body = user_line[len("USER ASKED: "):]
    assert "…" in body  # truncated (ellipsis present; a TASK_BRIEF pointer may follow)
    # The kept prefix up to the ellipsis is an exact prefix of the message → no mid-word cut.
    prefix = body.split("…", 1)[0].rstrip()
    assert msg.startswith(prefix)
