"""
Nexus - Document Converter MCP Tool.

Cho phép agents chủ động convert bất kỳ tài liệu nào sang Markdown
trong quá trình thực thi — hỗ trợ local files và remote URLs.

Features:
- 50+ định dạng: PDF, Word, Excel, PowerPoint, images, audio, HTML, ZIP...
- Tải file từ URL (có SSRF protection)
- OCR plugin (extract text từ ảnh embedded trong document)
- LLM Vision (mô tả ảnh bằng AI)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path("workspace")
TEMP_DOWNLOAD_DIR = WORKSPACE_DIR / "temp_downloads"


async def _fetch_url_to_file(url: str, timeout: float = 30.0) -> Path:
    """Tải file từ URL về temp dir, trả về local path."""
    from app.security.ssrf_guard import get_ssrf_guard

    guard = get_ssrf_guard()
    result = await guard.validate_with_dns(url)
    if result.blocked:
        raise ValueError(f"URL blocked (SSRF protection): {result.reason}")

    import httpx

    TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower() or ".bin"
    filename = f"{uuid.uuid4().hex[:12]}{ext}"
    dest = TEMP_DOWNLOAD_DIR / filename

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()

        max_size = 50 * 1024 * 1024  # 50MB
        if int(resp.headers.get("content-length", 0)) > max_size:
            raise ValueError(f"File too large (>{max_size // 1024 // 1024}MB)")

        dest.write_bytes(resp.content)

    return dest


def _is_url(path: str) -> bool:
    """Detect whether input is a URL or local path."""
    return path.startswith("http://") or path.startswith("https://")


async def handle_convert_document(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Convert any document to Markdown text."""
    path_or_url = params.get("path", "").strip()
    if not path_or_url:
        return {"success": False, "output": "Error: 'path' parameter is required", "data": {}, "artifacts": []}

    enable_ocr = params.get("enable_ocr", False)
    enable_vision = params.get("enable_vision", False)
    max_chars = params.get("max_chars", 50_000)

    source_type = "url" if _is_url(path_or_url) else "local"
    local_path: Path | None = None
    temp_file = False

    try:
        if source_type == "url":
            local_path = await _fetch_url_to_file(path_or_url)
            temp_file = True
        else:
            local_path = Path(path_or_url)
            if not local_path.exists():
                return {
                    "success": False,
                    "output": f"Error: File not found: {path_or_url}",
                    "data": {}, "artifacts": [],
                }

        file_size = local_path.stat().st_size
        ext = local_path.suffix.lower()

        from markitdown import MarkItDown

        md_kwargs: dict[str, Any] = {}

        if enable_ocr:
            md_kwargs["enable_plugins"] = True

        if enable_vision or enable_ocr:
            try:
                from app.services.llm_client import get_core_llm
                llm = get_core_llm()
                if hasattr(llm, "client"):
                    md_kwargs["llm_client"] = llm.client
                    md_kwargs["llm_model"] = getattr(llm, "model", None) or "gpt-4o"
            except Exception:
                pass

        md = MarkItDown(**md_kwargs)
        result = md.convert(str(local_path))
        text = result.text_content or ""

        truncated = False
        original_chars = len(text)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n... [Truncated at {max_chars:,} chars, original: {original_chars:,} chars]"
            truncated = True

        mime_type = _guess_mime_type(ext)

        return {
            "success": True,
            "output": text,
            "data": {
                "source_type": source_type,
                "original_path": path_or_url,
                "file_size": file_size,
                "char_count": len(text),
                "original_chars": original_chars,
                "truncated": truncated,
                "mime_type": mime_type,
                "extension": ext,
                "ocr_enabled": enable_ocr,
                "vision_enabled": enable_vision,
            },
            "artifacts": [],
        }

    except ImportError:
        return {
            "success": False,
            "output": "Error: markitdown not installed. Run: pip install 'markitdown[all]'",
            "data": {}, "artifacts": [],
        }
    except Exception as e:
        logger.error("convert_document_failed", path=path_or_url, error=str(e))
        return {
            "success": False,
            "output": f"Error converting document: {type(e).__name__}: {e}",
            "data": {}, "artifacts": [],
        }
    finally:
        if temp_file and local_path and local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


def _guess_mime_type(ext: str) -> str:
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
        ".webp": "image/webp",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".zip": "application/zip",
        ".epub": "application/epub+zip",
    }
    return mime_map.get(ext, "application/octet-stream")


# ─── Registration ────────────────────────────────────────────────────────────

registry.register(
    name="convert_document",
    toolset="document",
    schema={
        "description": (
            "Convert any document or file to Markdown text. "
            "Supports 50+ formats: PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), "
            "images (with OCR/vision), audio (with transcription), HTML, CSV, JSON, XML, "
            "ZIP, YouTube URLs, EPub, and more. "
            "Accepts local file paths or remote URLs (will download automatically). "
            "Use enable_ocr=true to extract text from images embedded in documents. "
            "Use enable_vision=true to get AI descriptions of images."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Local file path or URL to convert (e.g. 'workspace/uploads/report.pdf' or 'https://example.com/doc.pdf')",
                },
                "enable_ocr": {
                    "type": "boolean",
                    "description": "Enable OCR to extract text from images embedded in documents (requires markitdown-ocr plugin)",
                    "default": False,
                },
                "enable_vision": {
                    "type": "boolean",
                    "description": "Use LLM vision to describe images found in the document",
                    "default": False,
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (truncates if longer)",
                    "default": 50000,
                },
            },
            "required": ["path"],
        },
    },
    handler=handle_convert_document,
    is_async=True,
    emoji="📄",
    max_result_size_chars=60000,
)
