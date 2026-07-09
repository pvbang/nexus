"""Tests for attachment materialization + advanced-RAG building blocks.

Offline-only: file copy/manifest, reranker honest fallback, contextual gating.
DB- and LLM-dependent paths (indexing, retrieval) are covered by their own
integration suites; here we pin the pure logic and the never-fabricate fallbacks.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def dp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.services.document_processor as dp
    importlib.reload(dp)
    return dp, tmp_path


# ──────────────────────────────────────────────────────────────────
# save_upload — staged by conversation, readable names
# ──────────────────────────────────────────────────────────────────


async def test_save_upload_staged_by_conversation(dp_env):
    dp, tmp = dp_env
    path = await dp.save_upload(b"hello", "report.txt", conversation_id="conv-123")
    assert path.exists()
    assert path.read_bytes() == b"hello"
    # Keeps a readable name, staged under the conversation.
    assert path.name == "report.txt"
    assert "conv-123" in str(path.parent)


async def test_save_upload_collision_uniquifies(dp_env):
    dp, tmp = dp_env
    p1 = await dp.save_upload(b"a", "doc.txt", conversation_id="c1")
    p2 = await dp.save_upload(b"b", "doc.txt", conversation_id="c1")
    assert p1 != p2  # second one is uniquified
    assert p1.read_bytes() == b"a"
    assert p2.read_bytes() == b"b"


# ──────────────────────────────────────────────────────────────────
# materialize_attachments — copy into _work/attachments + manifest
# ──────────────────────────────────────────────────────────────────


async def test_materialize_copies_and_writes_manifest(dp_env):
    dp, tmp = dp_env
    await dp.save_upload(b"col1,col2\n1,2\n", "data.csv", conversation_id="conv-x")
    await dp.save_upload(b"PDFDATA", "spec.pdf", conversation_id="conv-x")

    work_dir = tmp / "tasks" / "abcd1234" / "_work"
    work_dir.mkdir(parents=True)

    result = dp.materialize_attachments("conv-x", work_dir)

    assert len(result) == 2
    att_dir = work_dir / "attachments"
    assert (att_dir / "data.csv").exists()
    assert (att_dir / "spec.pdf").exists()
    # Originals preserved (copy, not move).
    staging = tmp / "uploads" / "conv-x"
    assert (staging / "data.csv").exists()

    manifest = (att_dir / "_ATTACHMENTS.md").read_text(encoding="utf-8")
    assert "Attached Files" in manifest
    assert "data.csv" in manifest
    assert "convert_document" in manifest  # pdf → convert_document hint
    assert "rag_search" in manifest


async def test_materialize_no_uploads_is_noop(dp_env):
    dp, tmp = dp_env
    work_dir = tmp / "tasks" / "deadbeef" / "_work"
    work_dir.mkdir(parents=True)
    result = dp.materialize_attachments("never-uploaded", work_dir)
    assert result == []
    assert not (work_dir / "attachments").exists()


async def test_materialize_empty_conversation_id(dp_env):
    dp, tmp = dp_env
    work_dir = tmp / "w"
    work_dir.mkdir()
    assert dp.materialize_attachments("", work_dir) == []


def test_manifest_picks_right_tool_per_type(dp_env):
    dp, tmp = dp_env
    dest = tmp / "att"
    dest.mkdir()
    files = [
        {"name": "a.xlsx", "rel_path": "attachments/a.xlsx", "size": 1024},
        {"name": "b.docx", "rel_path": "attachments/b.docx", "size": 2048},
        {"name": "c.png", "rel_path": "attachments/c.png", "size": 512},
        {"name": "d.unknownext", "rel_path": "attachments/d.unknownext", "size": 10},
    ]
    dp._write_attachments_manifest(dest, files)
    md = (dest / "_ATTACHMENTS.md").read_text(encoding="utf-8")
    assert "read_spreadsheet" in md      # xlsx
    assert "read_document" in md         # docx
    assert "extract_text_from_image" in md  # png
    assert "read_file" in md             # fallback


# ──────────────────────────────────────────────────────────────────
# Contextual gating
# ──────────────────────────────────────────────────────────────────


def test_should_contextualize_threshold(monkeypatch):
    import app.rag.contextual as ctx
    small = "x" * 100
    big = "x" * 10_000
    assert ctx.should_contextualize(small) is False
    assert ctx.should_contextualize(big) is True


async def test_contextualize_degrades_without_llm(monkeypatch):
    """If the LLM can't be constructed, chunks come back unchanged (no fabrication)."""
    import app.rag.contextual as ctx
    from app.rag.models import Chunk

    def _boom():
        raise RuntimeError("no llm")

    monkeypatch.setattr("app.services.llm_client.get_core_llm", _boom)
    chunks = [Chunk(content="original text", doc_id="d1")]
    out = await ctx.contextualize_chunks(chunks, full_document="doc " * 2000)
    assert out[0].content == "original text"  # unchanged
    assert "context" not in out[0].metadata


# ──────────────────────────────────────────────────────────────────
# Reranker — honest no-op when model unavailable
# ──────────────────────────────────────────────────────────────────


async def test_rerank_honest_noop_when_unavailable(monkeypatch):
    import app.rag.reranker as rr
    from app.rag.models import SearchResult

    async def _no_model(_name):
        return None

    monkeypatch.setattr(rr, "_get_model", _no_model)
    results = [
        SearchResult(content="first", score=0.9),
        SearchResult(content="second", score=0.8),
        SearchResult(content="third", score=0.7),
    ]
    out = await rr.rerank("query", results, top_k=2)
    # Unchanged order, just trimmed to top_k.
    assert [r.content for r in out] == ["first", "second"]


async def test_rerank_reorders_with_fake_model(monkeypatch):
    import app.rag.reranker as rr
    from app.rag.models import SearchResult

    class _FakeModel:
        def predict(self, pairs):
            # Score by length of chunk text → "longest" wins, to prove reordering.
            return [len(c) for _, c in pairs]

    async def _fake(_name):
        return _FakeModel()

    monkeypatch.setattr(rr, "_get_model", _fake)
    results = [
        SearchResult(content="aa", score=0.1),
        SearchResult(content="aaaaaa", score=0.1),
        SearchResult(content="aaaa", score=0.1),
    ]
    out = await rr.rerank("q", results, top_k=3)
    assert [r.content for r in out] == ["aaaaaa", "aaaa", "aa"]
    assert out[0].retrieval_method.endswith("+rerank")
