"""
MekongAI Nexus - Attachments Upload Endpoint.

Cho phép upload tài liệu/ảnh, xử lý bằng MarkItDown,
trả về markdown content để inject vào chat context.
"""

from typing import Any, Optional

import structlog
from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.services.document_processor import (
    save_upload,
    process_document,
    process_multiple_documents,
    format_attachments_for_context,
    SUPPORTED_EXTENSIONS,
    MAX_FILE_SIZE,
)

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/upload")
async def upload_attachments(
    files: list[UploadFile] = File(...),
    conversation_id: Optional[str] = Form(default=None),
) -> dict[str, Any]:
    """
    Upload một hoặc nhiều tài liệu/ảnh.

    Hỗ trợ: PDF, DOCX, PPTX, XLSX, images, CSV, JSON, HTML, code files...
    Trả về markdown content đã convert bằng MarkItDown.

    File được stage theo conversation_id để sau đó materialize vào _work/
    attachments/ của task — agent có thể đọc lại file gốc bất cứ lúc nào.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Max 10 files per upload")

    conv_id = (conversation_id or "").strip()

    results = []
    for f in files:
        # Validate size
        content = await f.read()
        if len(content) > MAX_FILE_SIZE:
            results.append({
                "filename": f.filename,
                "error": f"File too large ({len(content) / 1024 / 1024:.1f} MB). Max: {MAX_FILE_SIZE / 1024 / 1024:.0f} MB",
                "success": False,
            })
            continue

        # Save (staged by conversation) and process
        path = await save_upload(content, f.filename or "unknown", conversation_id=conv_id)
        doc = await process_document(path, f.filename or "unknown")

        results.append({
            "filename": doc.filename,
            "file_size": doc.file_size,
            "mime_type": doc.mime_type,
            "char_count": doc.char_count,
            "truncated": doc.truncated,
            "markdown_content": doc.markdown_content,
            "error": doc.error,
            "success": doc.error is None,
            "path": doc.original_path,
        })

    # Tổng hợp context string
    successful = [r for r in results if r["success"]]
    context = ""
    indexed_docs: list[dict[str, Any]] = []

    if successful:
        from app.services.document_processor import ProcessedDocument
        docs = [
            ProcessedDocument(
                filename=r["filename"],
                original_path=r["path"],
                file_size=r["file_size"],
                mime_type=r["mime_type"],
                markdown_content=r["markdown_content"],
                char_count=r["char_count"],
                truncated=r["truncated"],
            )
            for r in successful
        ]
        context = format_attachments_for_context(docs)

        # Auto-index large documents into RAG vector store (fire-and-forget)
        try:
            from app.rag.indexer import DocumentIndexer, should_auto_index
            from app.core.dependencies import _session_factory

            if _session_factory:
                for r in successful:
                    if should_auto_index(r.get("markdown_content", "")):
                        async with _session_factory() as db:
                            indexer = DocumentIndexer(db)
                            idx_result = await indexer.index_document(
                                content=r["markdown_content"],
                                filename=r["filename"],
                                session_id=conv_id,
                                original_path=r.get("path", ""),
                                file_size=r.get("file_size", 0),
                                mime_type=r.get("mime_type", ""),
                                # Scope to the conversation so rag_search in this
                                # chat retrieves its own uploads first.
                                collection=conv_id or "",
                            )
                            if idx_result.status == "indexed":
                                indexed_docs.append({
                                    "filename": r["filename"],
                                    "doc_id": idx_result.doc_id,
                                    "chunks": idx_result.chunk_count,
                                })
                                logger.info(
                                    "auto_indexed_document",
                                    filename=r["filename"],
                                    chunks=idx_result.chunk_count,
                                    doc_id=idx_result.doc_id,
                                )
                            await db.commit()
        except Exception as e:
            logger.warning("auto_index_failed", error=str(e))
            # Non-blocking: upload still succeeds even if indexing fails

    return {
        "files": results,
        "total": len(results),
        "successful": len(successful),
        "context": context,
        "indexed": indexed_docs,
    }


@router.get("/supported-formats")
async def get_supported_formats() -> dict[str, Any]:
    """Trả về danh sách formats hỗ trợ."""
    categories = {
        "documents": [".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"],
        "text": [".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".html"],
        "images": [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"],
        "audio": [".wav", ".mp3"],
        "code": [".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".go", ".rs", ".sql"],
        "archives": [".zip"],
    }
    return {
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "categories": categories,
        "max_file_size_mb": MAX_FILE_SIZE / 1024 / 1024,
        "max_files_per_upload": 10,
    }
