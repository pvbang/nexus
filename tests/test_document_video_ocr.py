"""Tests for Word .docx read/edit, video tool gating, and OCR engine selection."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.mcp.tools.filesystem as fs
    importlib.reload(fs)
    import app.mcp.tools.data as data
    importlib.reload(data)
    return data, tmp_path


# ──────────────────────────────────────────────────────────────────
# Word .docx read / edit
# ──────────────────────────────────────────────────────────────────


def _make_docx(path, paragraphs):
    from docx import Document
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))


async def test_read_document(ws_env):
    data, tmp = ws_env
    _make_docx(tmp / "doc.docx", ["Hello world", "Second line", "Third"])
    res = await data.handle_read_document({"path": "doc.docx"})
    assert res["success"], res["output"]
    paras = res["data"]["paragraphs"]
    assert "Hello world" in paras
    assert res["data"]["total_paragraphs"] == 3


async def test_read_document_missing(ws_env):
    data, tmp = ws_env
    res = await data.handle_read_document({"path": "ghost.docx"})
    assert not res["success"]
    assert "not found" in res["output"].lower()


async def test_edit_document_find_replace(ws_env):
    data, tmp = ws_env
    _make_docx(tmp / "doc.docx", ["The quick brown fox", "jumps over"])
    res = await data.handle_edit_document(
        {"path": "doc.docx", "edits": [{"find": "quick", "replace": "slow"}]}
    )
    assert res["success"], res["output"]
    read = await data.handle_read_document({"path": "doc.docx"})
    assert any("slow brown fox" in p for p in read["data"]["paragraphs"])
    assert not any("quick" in p for p in read["data"]["paragraphs"])


async def test_edit_document_paragraph_overwrite(ws_env):
    data, tmp = ws_env
    _make_docx(tmp / "doc.docx", ["original a", "original b"])
    res = await data.handle_edit_document(
        {"path": "doc.docx", "edits": [{"paragraph": 1, "text": "REPLACED"}]}
    )
    assert res["success"], res["output"]
    read = await data.handle_read_document({"path": "doc.docx"})
    assert read["data"]["paragraphs"][1] == "REPLACED"
    assert read["data"]["paragraphs"][0] == "original a"


async def test_edit_document_paragraph_out_of_range(ws_env):
    data, tmp = ws_env
    _make_docx(tmp / "doc.docx", ["only one"])
    res = await data.handle_edit_document(
        {"path": "doc.docx", "edits": [{"paragraph": 99, "text": "x"}]}
    )
    assert not res["success"]
    assert "out of range" in res["output"]


async def test_edit_document_rejects_empty_edits(ws_env):
    data, tmp = ws_env
    _make_docx(tmp / "doc.docx", ["a"])
    res = await data.handle_edit_document({"path": "doc.docx", "edits": []})
    assert not res["success"]
    assert "non-empty" in res["output"]


# ──────────────────────────────────────────────────────────────────
# Video tools — honest gating when ffmpeg absent
# ──────────────────────────────────────────────────────────────────


async def test_video_info_honest_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.mcp.tools.video as video
    importlib.reload(video)
    # Force the "ffmpeg missing" branch regardless of host.
    monkeypatch.setattr(video, "_ffmpeg_available", lambda: False)
    res = await video.handle_video_info({"path": "clip.mp4"})
    assert not res["success"]
    assert "ffmpeg" in res["output"].lower()
    assert "no video processing was performed" in res["output"].lower()


def test_video_check_fn_reflects_ffmpeg():
    import app.mcp.tools.video as video
    import shutil
    expected = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
    assert video._ffmpeg_available() is expected


# ──────────────────────────────────────────────────────────────────
# OCR — engine selection is honest about which engine ran
# ──────────────────────────────────────────────────────────────────


async def test_ocr_requires_source():
    import app.mcp.tools.vision as vision
    res = await vision.handle_extract_text_from_image({})
    assert not res["success"]
    assert "no image source" in res["output"].lower()
