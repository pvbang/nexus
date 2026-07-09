"""Tests for new INPUT capabilities + honest-stub fixes.

- read_spreadsheet / edit_spreadsheet: real .xlsx round-trip in a temp workspace.
- mobile_forensics_extract: real SQLite parsing + honest reporting on containers.
- wallet_cluster: honest refusal for unsupported chains (no fabrication).
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture
def ws_env(tmp_path, monkeypatch):
    """Temp WORKSPACE_DIR with filesystem + data modules reloaded against it."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.mcp.tools.filesystem as fs
    importlib.reload(fs)
    import app.mcp.tools.data as data
    importlib.reload(data)
    return data, tmp_path


# ──────────────────────────────────────────────────────────────────
# Spreadsheet read / edit
# ──────────────────────────────────────────────────────────────────


def _make_xlsx(path, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    wb.save(str(path))


async def test_read_spreadsheet(ws_env):
    data, tmp = ws_env
    _make_xlsx(tmp / "book.xlsx", [["name", "age"], ["alice", 30], ["bob", 25]])
    res = await data.handle_read_spreadsheet({"path": "book.xlsx"})
    assert res["success"], res["output"]
    rows = res["data"]["rows"]
    assert rows[0] == ["name", "age"]
    assert rows[1] == ["alice", 30]
    assert res["data"]["sheets"] == ["Data"]


async def test_read_spreadsheet_missing(ws_env):
    data, tmp = ws_env
    res = await data.handle_read_spreadsheet({"path": "nope.xlsx"})
    assert not res["success"]
    assert "not found" in res["output"].lower()


async def test_edit_spreadsheet_by_cell(ws_env):
    data, tmp = ws_env
    _make_xlsx(tmp / "book.xlsx", [["name", "age"], ["alice", 30]])
    res = await data.handle_edit_spreadsheet(
        {"path": "book.xlsx", "edits": [{"cell": "B2", "value": 99}]}
    )
    assert res["success"], res["output"]
    # Re-read to confirm the change persisted and the rest is intact.
    read = await data.handle_read_spreadsheet({"path": "book.xlsx"})
    assert read["data"]["rows"][1] == ["alice", 99]
    assert read["data"]["rows"][0] == ["name", "age"]


async def test_edit_spreadsheet_by_row_col(ws_env):
    data, tmp = ws_env
    _make_xlsx(tmp / "book.xlsx", [["x"], ["y"]])
    res = await data.handle_edit_spreadsheet(
        {"path": "book.xlsx", "edits": [{"row": 1, "col": 1, "value": "Z"}]}
    )
    assert res["success"], res["output"]
    read = await data.handle_read_spreadsheet({"path": "book.xlsx"})
    assert read["data"]["rows"][0] == ["Z"]


async def test_edit_spreadsheet_rejects_empty_edits(ws_env):
    data, tmp = ws_env
    _make_xlsx(tmp / "book.xlsx", [["a"]])
    res = await data.handle_edit_spreadsheet({"path": "book.xlsx", "edits": []})
    assert not res["success"]
    assert "non-empty" in res["output"]


async def test_edit_spreadsheet_bad_sheet(ws_env):
    data, tmp = ws_env
    _make_xlsx(tmp / "book.xlsx", [["a"]])
    res = await data.handle_edit_spreadsheet(
        {"path": "book.xlsx", "sheet": "Ghost", "edits": [{"cell": "A1", "value": 1}]}
    )
    assert not res["success"]
    assert "not found" in res["output"].lower()


# ──────────────────────────────────────────────────────────────────
# Mobile forensics — real SQLite parsing + honest container handling
# ──────────────────────────────────────────────────────────────────


async def test_forensics_parses_real_sms_db(tmp_path):
    import app.mcp.tools.malware_tools as mt
    db = tmp_path / "mmssms.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sms (address TEXT, body TEXT, date INTEGER)")
    conn.execute("INSERT INTO sms VALUES ('+123', 'hello', 1700000000)")
    conn.execute("INSERT INTO sms VALUES ('+456', 'world', 1700000100)")
    conn.commit()
    conn.close()

    out = await mt._handle_mobile_forensics_extract({"source_path": str(db)})
    assert "2 row(s) extracted" in out
    assert "hello" in out
    assert "SHA-256" in out  # chain of custody present


async def test_forensics_honest_about_container(tmp_path):
    import app.mcp.tools.malware_tools as mt
    backup = tmp_path / "phone.ab"
    backup.write_bytes(b"ANDROID BACKUP\n...")
    out = await mt._handle_mobile_forensics_extract({"source_path": str(backup)})
    assert "NO records" in out or "decode" in out.lower()
    assert "abe.jar" in out  # gives the real decode path, doesn't fabricate data


async def test_forensics_no_db_found(tmp_path):
    import app.mcp.tools.malware_tools as mt
    folder = tmp_path / "empty"
    folder.mkdir()
    out = await mt._handle_mobile_forensics_extract({"source_path": str(folder)})
    assert "No SQLite artifacts found" in out


async def test_malware_static_only_no_fake_dynamic(tmp_path):
    import app.mcp.tools.malware_tools as mt
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"MZ\x90\x00" + b"http://evil.example/c2 " * 3 + b"VirtualAlloc")
    out = await mt._handle_malware_sandbox_analyze({"file_path": str(sample)})
    # Must NOT claim a sandbox ran; must be explicit it's static only.
    assert "NOT PERFORMED" in out
    assert "CONFIGURED (Docker isolation)" not in out
    # Static results still present.
    assert "SHA-256" in out


# ──────────────────────────────────────────────────────────────────
# Wallet cluster — honest refusal, never fabricate
# ──────────────────────────────────────────────────────────────────


async def test_wallet_cluster_refuses_unsupported_chain():
    import app.mcp.tools.blockchain_tools as bt
    out = await bt._handle_wallet_cluster(
        {"addresses": ["0xaaa", "0xbbb"], "chain": "ethereum", "method": "common_input"}
    )
    assert "only" in out.lower()
    assert "will not fabricate" in out.lower()


async def test_wallet_cluster_needs_two_addresses():
    import app.mcp.tools.blockchain_tools as bt
    out = await bt._handle_wallet_cluster({"addresses": ["1abc"], "chain": "bitcoin"})
    assert "at least 2" in out
