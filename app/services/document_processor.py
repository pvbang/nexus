"""
MekongAI Nexus - Document Processor.

Sử dụng MarkItDown để chuyển đổi đa dạng tài liệu (PDF, DOCX, PPTX,
XLSX, images, HTML, CSV, JSON, ZIP...) sang Markdown text để agents
có context tham khảo.

Hỗ trợ:
- PDF, DOCX, PPTX, XLSX, XLS
- Images (PNG, JPG, GIF, BMP, TIFF) — OCR/description
- HTML, CSV, JSON, XML
- Audio (WAV, MP3) — transcription
- ZIP archives
"""

import os
import uuid
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Staging directory for uploads. Uploads happen BEFORE a task exists (the
# frontend uploads, then sends the message that creates the task), so files are
# first staged under the conversation id and later "materialized" into the
# task's _work/attachments/ when the task starts (see materialize_attachments).
# WORKSPACE_DIR is read from env so it matches the rest of the workspace layout
# (Docker uses an absolute path) — see [[workspace-path-inconsistency]].
_WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
UPLOAD_DIR = _WORKSPACE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Max file size: 50 MB
MAX_FILE_SIZE = 50 * 1024 * 1024

# Supported extensions
SUPPORTED_EXTENSIONS = {
    # Documents
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    # Text
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".html", ".htm", ".rst", ".log",
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp",
    # Audio
    ".wav", ".mp3",
    # Archives
    ".zip",
    # Code
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp",
    ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".bat",
}


@dataclass
class ProcessedDocument:
    """Kết quả xử lý tài liệu."""
    filename: str
    original_path: str
    file_size: int
    mime_type: str
    markdown_content: str
    char_count: int
    truncated: bool = False
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


def _get_mime_type(ext: str) -> str:
    """Map extension to MIME type."""
    mime_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".csv": "text/csv",
        ".json": "application/json",
        ".xml": "application/xml",
        ".html": "text/html",
        ".htm": "text/html",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".zip": "application/zip",
    }
    return mime_map.get(ext.lower(), "application/octet-stream")


def _sanitize_filename(name: str) -> str:
    """Remove special characters from filename, keep alphanumeric, dash, underscore."""
    import re
    # Keep only safe chars: alphanumeric, dash, underscore, dot
    return re.sub(r'[^\w\-.]', '_', name)


async def save_upload(
    file_content: bytes,
    filename: str,
    conversation_id: str = "",
) -> Path:
    """Stage an uploaded file under the conversation's staging dir.

    Files are keyed by conversation_id so they can later be materialized into
    the task's _work/attachments/ once the task exists. Falling back to the
    flat UPLOAD_DIR when no conversation_id is given keeps older callers working.
    """
    ext = Path(filename).suffix.lower()
    stem = _sanitize_filename(Path(filename).stem)
    # Keep the readable original name; uniquify only on collision so the agent
    # sees "report.pdf", not "a1b2c3_report.pdf".
    base_dir = (UPLOAD_DIR / _sanitize_filename(conversation_id)) if conversation_id else UPLOAD_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    dest = base_dir / f"{stem}{ext}"
    if dest.exists():
        dest = base_dir / f"{stem}_{uuid.uuid4().hex[:6]}{ext}"
    dest.write_bytes(file_content)
    logger.info(
        "file_staged",
        filename=filename, path=str(dest), size=len(file_content),
        conversation_id=conversation_id or "(none)",
    )
    return dest


def materialize_attachments(conversation_id: str, work_dir: Path) -> list[dict[str, object]]:
    """Copy a conversation's staged uploads into the task's _work/attachments/.

    Called when a task starts (it's the first point where both the
    conversation_id and the task's _work dir are known). Copies — never moves —
    so a re-run or a second task in the same conversation still finds the files.
    Writes an _ATTACHMENTS.md manifest the agent can read.

    Returns a list of {name, rel_path, size} for the materialized files
    (empty if there were no staged uploads).
    """
    if not conversation_id:
        return []

    staging = UPLOAD_DIR / _sanitize_filename(conversation_id)
    if not staging.is_dir():
        return []

    dest_dir = work_dir / "attachments"
    dest_dir.mkdir(parents=True, exist_ok=True)

    from app.core.upload_safety import safe_copy_no_symlink, PathTraversalError

    materialized: list[dict[str, object]] = []
    for src in sorted(staging.iterdir()):
        if not src.is_file():
            continue
        dst = dest_dir / src.name
        try:
            if not dst.exists():
                # Refuse symlinked staged entries (escape to files outside the
                # upload tree) and keep the copy inside dest_dir.
                safe_copy_no_symlink(src, dst, base=dest_dir)
            materialized.append({
                "name": src.name,
                "rel_path": f"attachments/{src.name}",
                "size": dst.stat().st_size,
            })
        except (OSError, PathTraversalError) as e:
            logger.warning("materialize_copy_failed", file=src.name, error=str(e))

    if materialized:
        _write_attachments_manifest(dest_dir, materialized)
        logger.info(
            "attachments_materialized",
            conversation_id=conversation_id,
            count=len(materialized),
            dest=str(dest_dir),
        )
    return materialized


def _write_attachments_manifest(dest_dir: Path, files: list[dict[str, object]]) -> None:
    """Write _ATTACHMENTS.md so the agent knows what's available and how to use it."""
    lines = [
        "# Attached Files",
        "",
        "The user uploaded these files. They live in `attachments/` in your workspace.",
        "Inline summaries in the conversation may be truncated or incomplete — when you",
        "need exact details, OPEN or SEARCH these originals; do not guess.",
        "",
        "| File | How to read it |",
        "| --- | --- |",
    ]
    for f in files:
        name = str(f["name"])
        ext = Path(name).suffix.lower()
        if ext in {".xlsx", ".xlsm"}:
            how = f"`read_spreadsheet path=\"{f['rel_path']}\"`"
        elif ext in {".docx"}:
            how = f"`read_document path=\"{f['rel_path']}\"`"
        elif ext in {".pdf", ".pptx", ".ppt", ".doc", ".html", ".htm"}:
            how = f"`convert_document path=\"{f['rel_path']}\"`"
        elif ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}:
            how = f"`analyze_image` / `extract_text_from_image image=\"{f['rel_path']}\"`"
        else:
            how = f"`read_file path=\"{f['rel_path']}\"`"
        size_kb = int(f.get("size", 0)) / 1024
        lines.append(f"| `{f['rel_path']}` ({size_kb:.1f} KB) | {how} |")
    lines += [
        "",
        "To search across all uploaded content semantically, use `rag_search` "
        "(documents are auto-indexed with contextual retrieval + reranking).",
    ]
    (dest_dir / "_ATTACHMENTS.md").write_text("\n".join(lines), encoding="utf-8")


# Image extensions that should use vision tools instead of text extraction
_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp",
}


async def _auto_analyze_image(
    file_path: Path,
    filename: str,
    timeout_seconds: float = 30.0,
) -> Optional[str]:
    """Tự động phân tích ảnh bằng vision tool khi upload.

    Returns mô tả chi tiết hoặc None nếu thất bại hoặc timeout.
    Timeout mặc định 30s để upload không bị treo quá lâu.
    """
    import asyncio

    try:
        from app.mcp.tools.vision import handle_describe_image
        result = await asyncio.wait_for(
            handle_describe_image({"image": str(file_path)}),
            timeout=timeout_seconds,
        )
        if result.get("success") and result.get("output"):
            logger.info("image_auto_analyzed", filename=filename, chars=len(result["output"]))
            return result["output"]
        logger.warning(
            "image_auto_analyze_failed",
            filename=filename,
            error=result.get("output", "unknown"),
        )
        return None
    except asyncio.TimeoutError:
        logger.warning("image_auto_analyze_timeout", filename=filename, timeout=timeout_seconds)
        return None
    except Exception as e:
        logger.warning("image_auto_analyze_error", filename=filename, error=str(e))
        return None


async def process_document(
    file_path: Path,
    original_filename: str,
    max_chars: int = 50_000,
) -> ProcessedDocument:
    """
    Xử lý tài liệu bằng MarkItDown.

    Chuyển đổi file thành Markdown text, truncate nếu quá dài.
    Ảnh được xử lý đặc biệt: thêm file path + instruction cho vision tools.
    """
    ext = Path(original_filename).suffix.lower()
    file_size = file_path.stat().st_size
    mime_type = _get_mime_type(ext)

    logger.info(
        "processing_document",
        filename=original_filename,
        ext=ext,
        size=file_size,
        mime_type=mime_type,
    )

    # Validate
    if ext not in SUPPORTED_EXTENSIONS:
        return ProcessedDocument(
            filename=original_filename,
            original_path=str(file_path),
            file_size=file_size,
            mime_type=mime_type,
            markdown_content="",
            char_count=0,
            error=f"Unsupported file type: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    if file_size > MAX_FILE_SIZE:
        return ProcessedDocument(
            filename=original_filename,
            original_path=str(file_path),
            file_size=file_size,
            mime_type=mime_type,
            markdown_content="",
            char_count=0,
            error=f"File too large ({file_size / 1024 / 1024:.1f} MB). Max: {MAX_FILE_SIZE / 1024 / 1024:.0f} MB",
        )

    # ── Special handling for images: auto-analyze with vision tool ──
    if ext in _IMAGE_EXTENSIONS:
        image_description = await _auto_analyze_image(file_path, original_filename)
        image_context = (
            f"[IMAGE: {original_filename}]\n"
            f"File path: {file_path}\n"
            f"Size: {file_size / 1024:.1f} KB | Type: {mime_type}\n\n"
        )
        if image_description:
            image_context += f"**Image Analysis:**\n{image_description}"
        else:
            # Fallback: provide hints for agent to use vision tools manually
            image_context += (
                f"⚠️ Auto-analysis unavailable. To analyze this image, use vision tools:\n"
                f"  - `analyze_image` with image=\"{file_path}\" and a question\n"
                f"  - `describe_image` with image=\"{file_path}\" for detailed description"
            )
        return ProcessedDocument(
            filename=original_filename,
            original_path=str(file_path),
            file_size=file_size,
            mime_type=mime_type,
            markdown_content=image_context,
            char_count=len(image_context),
            truncated=False,
            metadata={
                "extension": ext,
                "processing_method": "vision_auto_analyze" if image_description else "vision_tool_hint",
                "is_image": True,
            },
        )

    # ── Standard document processing with MarkItDown ──
    try:
        markdown_text = await _convert_with_markitdown(file_path)
    except Exception as e:
        logger.error("markitdown_failed", filename=original_filename, error=str(e))
        # Fallback: đọc raw text cho text files
        markdown_text = _fallback_read(file_path, ext)
        if not markdown_text:
            return ProcessedDocument(
                filename=original_filename,
                original_path=str(file_path),
                file_size=file_size,
                mime_type=mime_type,
                markdown_content="",
                char_count=0,
                error=f"Failed to process: {e}",
            )

    # Truncate nếu quá dài
    truncated = False
    if len(markdown_text) > max_chars:
        markdown_text = markdown_text[:max_chars] + f"\n\n... [Truncated at {max_chars:,} chars, original: {len(markdown_text):,} chars]"
        truncated = True

    return ProcessedDocument(
        filename=original_filename,
        original_path=str(file_path),
        file_size=file_size,
        mime_type=mime_type,
        markdown_content=markdown_text,
        char_count=len(markdown_text),
        truncated=truncated,
        metadata={
            "extension": ext,
            "processing_method": "markitdown",
        },
    )


async def _convert_with_markitdown(file_path: Path) -> str:
    """Chuyển đổi file bằng MarkItDown library."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        logger.warning("markitdown_not_installed", hint="pip install 'markitdown[all]'")
        raise ImportError(
            "markitdown is not installed. Run: pip install 'markitdown[all]'"
        )

    md = MarkItDown()
    result = md.convert(str(file_path))
    return result.text_content or ""


def _fallback_read(file_path: Path, ext: str) -> str:
    """Fallback: đọc raw text cho text-based files."""
    text_extensions = {
        ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
        ".html", ".htm", ".rst", ".log",
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp",
        ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".bat",
    }
    if ext in text_extensions:
        try:
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            try:
                return file_path.read_text(encoding="latin-1", errors="replace")
            except Exception:
                return ""
    return ""


async def process_multiple_documents(
    files: list[tuple[bytes, str]],
    max_chars_per_file: int = 30_000,
) -> list[ProcessedDocument]:
    """Xử lý nhiều files cùng lúc."""
    results = []
    for content, filename in files:
        path = await save_upload(content, filename)
        doc = await process_document(path, filename, max_chars=max_chars_per_file)
        results.append(doc)
    return results


def format_attachments_for_context(docs: list[ProcessedDocument]) -> str:
    """Format processed documents thành context string cho LLM."""
    if not docs:
        return ""

    parts = []
    for doc in docs:
        if doc.error:
            parts.append(
                f"📎 **{doc.filename}** — ⚠️ Error: {doc.error}"
            )
            continue

        header = f"📎 **{doc.filename}** ({doc.file_size / 1024:.1f} KB, {doc.mime_type})"
        if doc.truncated:
            header += " [truncated]"

        parts.append(f"{header}\n\n{doc.markdown_content}")

    return "\n\n---\n\n".join(parts)
