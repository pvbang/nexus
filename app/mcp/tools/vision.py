"""
MekongAI Nexus - Vision / Image Analysis Tool.

Send images to LLM for analysis (GPT-4V, Claude Vision, Gemini Vision).
Supports both local file paths (workspace) and remote URLs pointing to
actual image files (.png, .jpg, .gif, .webp).

Usage::

    # Auto-registered tools:
    #   analyze_image  — send image to LLM with a question
    #   describe_image — get a detailed description of an image
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Optional

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

# ------------------------------------------------------------------
# Mime-type helpers
# ------------------------------------------------------------------

_SUPPORTED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _guess_mime(path_or_url: str) -> str:
    """Guess MIME type from filename or URL, fallback to png."""
    mime, _ = mimetypes.guess_type(path_or_url)
    if mime and mime in _SUPPORTED_TYPES:
        return mime
    return "image/png"


def _is_image_url(url: str) -> bool:
    """Check if a URL likely points to an image file (not a webpage)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.lower()
        # Check if path ends with a known image extension
        for ext in _IMAGE_EXTENSIONS:
            if path.endswith(ext):
                return True
        return False
    except Exception:
        return False


# ------------------------------------------------------------------
# Image loading
# ------------------------------------------------------------------


async def _load_image_as_data_url(source: str) -> tuple[Optional[str], Optional[str]]:
    """Return (data_url, error_message) for the given source.

    - Local file path → reads and base64-encodes
    - Remote URL pointing to image file → fetches and base64-encodes
    - Remote URL pointing to webpage → returns error (not an image)
    """
    if source.startswith(("http://", "https://")):
        # Check if URL looks like a webpage (not an image)
        if not _is_image_url(source):
            return None, (
                f"'{source}' appears to be a webpage URL, not an image file. "
                "analyze_image requires a direct link to an image file (.png, .jpg, .jpeg, .gif, .webp). "
                "To analyze a webpage, use fetch_url to get its content as text instead. "
                "If the page has screenshots, they must be downloaded as image files first."
            )

        # Fetch the image from URL
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(source, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").split(";")[0].strip()

                if content_type not in _SUPPORTED_TYPES:
                    return None, (
                        f"URL returned content-type '{content_type}', not a supported image format. "
                        f"Supported formats: {sorted(_SUPPORTED_TYPES)}"
                    )

                b64 = base64.b64encode(resp.content).decode("ascii")
                return f"data:{content_type};base64,{b64}", None
        except Exception as e:
            return None, f"Failed to fetch image from URL: {e}"

    # Local file
    path = Path(source)
    if not path.exists():
        # Try relative to known workspaces
        try:
            from app.mcp.tools.filesystem import WORKSPACE_DIR

            ws_path = WORKSPACE_DIR / source
            if ws_path.exists():
                path = ws_path
            else:
                workspace_path = Path("workspace") / source
                if workspace_path.exists():
                    path = workspace_path
                else:
                    return None, f"Image file not found: {source}"
        except Exception:
            workspace_path = Path("workspace") / source
            if workspace_path.exists():
                path = workspace_path
            else:
                return None, f"Image file not found: {source}"

    mime = _guess_mime(str(path))
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}", None


def _build_vision_messages(
    image_url: str,
    prompt: str,
    system: str = "You are a helpful vision assistant that analyzes images.",
) -> list[dict[str, Any]]:
    """Build an OpenAI-compatible message list with an image_url content part."""
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": image_url, "detail": "auto"},
                },
            ],
        },
    ]


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_analyze_image(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Send an image to the LLM with a user question and return the analysis.

    NOTE: 'image' must be a direct URL to an image file (.png, .jpg, .gif, .webp)
    or a local file path. Webpage URLs are not supported — use fetch_url instead.
    """
    source = params.get("image", "") or params.get("source", "")
    question = params.get("question", "What do you see in this image?")

    if not source:
        return {"success": False, "output": "No image source provided", "data": None, "artifacts": []}

    image_url, error = await _load_image_as_data_url(source)
    if error or image_url is None:
        return {
            "success": False,
            "output": error or f"Image not found or unreadable: {source}",
            "data": None,
            "artifacts": [],
        }

    try:
        from app.services.llm_client import get_core_llm

        llm = get_core_llm()
        messages = _build_vision_messages(image_url, question)
        result = await llm.chat(messages=messages, max_tokens=2048)
        content = result.get("content", "")

        return {
            "success": True,
            "output": content,
            "data": {
                "source": source,
                "question": question,
                "usage": result.get("usage", {}),
            },
            "artifacts": [],
        }
    except Exception as exc:
        logger.error("vision_analyze_error", source=source[:200], error=str(exc))
        return {"success": False, "output": f"Image analysis failed: {exc}", "data": None, "artifacts": []}


async def handle_describe_image(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Get a detailed description of an image."""
    source = params.get("image", "") or params.get("source", "")

    if not source:
        return {"success": False, "output": "No image source provided", "data": None, "artifacts": []}

    image_url, error = await _load_image_as_data_url(source)
    if error or image_url is None:
        return {
            "success": False,
            "output": error or f"Image not found or unreadable: {source}",
            "data": None,
            "artifacts": [],
        }

    try:
        from app.services.llm_client import get_core_llm

        llm = get_core_llm()
        prompt = (
            "Describe this image in detail. Include:\n"
            "1. Main subjects and objects\n"
            "2. Colors, lighting, composition\n"
            "3. Text if any is visible\n"
            "4. Overall context and mood\n"
            "Be thorough but concise."
        )
        messages = _build_vision_messages(image_url, prompt)
        result = await llm.chat(messages=messages, max_tokens=2048)
        content = result.get("content", "")

        return {
            "success": True,
            "output": content,
            "data": {
                "source": source,
                "usage": result.get("usage", {}),
            },
            "artifacts": [],
        }
    except Exception as exc:
        logger.error("vision_describe_error", source=source[:200], error=str(exc))
        return {"success": False, "output": f"Image description failed: {exc}", "data": None, "artifacts": []}


def _resolve_local_image(source: str) -> Optional[Path]:
    """Resolve a local image path within workspace; None if remote/not found."""
    if source.startswith(("http://", "https://")):
        return None
    path = Path(source)
    if path.exists():
        return path
    try:
        from app.mcp.tools.filesystem import WORKSPACE_DIR, _smart_resolve_path, get_task_workspace
        candidate = _smart_resolve_path(source, get_task_workspace(), WORKSPACE_DIR)
        if candidate.exists():
            return candidate
        alt = WORKSPACE_DIR / source
        if alt.exists():
            return alt
    except Exception:
        pass
    return None


async def handle_extract_text_from_image(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """OCR — extract text from a standalone image file.

    Engine selection (honest, no fabrication):
    1. pytesseract (local Tesseract OCR) when installed — deterministic, fast.
    2. Otherwise fall back to the vision LLM with a strict transcription prompt.
    The response states which engine produced the text.
    """
    source = params.get("image", "") or params.get("source", "")
    lang = params.get("lang", "eng")
    if not source:
        return {"success": False, "output": "No image source provided", "data": None, "artifacts": []}

    # ── Engine 1: local Tesseract via pytesseract ──
    local_path = _resolve_local_image(source)
    try:
        import pytesseract  # type: ignore
        from PIL import Image

        if local_path is not None:
            import asyncio

            def _ocr() -> str:
                with Image.open(local_path) as img:
                    return pytesseract.image_to_string(img, lang=lang)

            text = (await asyncio.to_thread(_ocr)).strip()
            return {
                "success": True,
                "output": text or "(no text detected)",
                "data": {"source": source, "engine": "tesseract", "lang": lang, "chars": len(text)},
                "artifacts": [],
            }
    except ImportError:
        pass  # No pytesseract — fall through to vision LLM.
    except Exception as exc:
        logger.warning("ocr_tesseract_failed", error=str(exc))
        # Fall through to vision fallback rather than failing outright.

    # ── Engine 2: vision LLM fallback ──
    image_url, error = await _load_image_as_data_url(source)
    if error or image_url is None:
        return {"success": False, "output": error or f"Image not found: {source}", "data": None, "artifacts": []}

    try:
        from app.services.llm_client import get_core_llm

        llm = get_core_llm()
        prompt = (
            "Transcribe ALL text visible in this image verbatim. Preserve line "
            "breaks and reading order. Output ONLY the transcribed text, with no "
            "commentary. If there is no text, reply exactly: (no text detected)."
        )
        messages = _build_vision_messages(image_url, prompt)
        result = await llm.chat(messages=messages, max_tokens=2048)
        content = (result.get("content", "") or "").strip()
        return {
            "success": True,
            "output": content or "(no text detected)",
            "data": {
                "source": source,
                "engine": "vision_llm",
                "note": "Tesseract not installed — used vision model transcription",
                "usage": result.get("usage", {}),
            },
            "artifacts": [],
        }
    except Exception as exc:
        logger.error("ocr_vision_error", source=source[:200], error=str(exc))
        return {"success": False, "output": f"OCR failed: {exc}", "data": None, "artifacts": []}


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "vision"

registry.register(
    name="analyze_image",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Analyze a LOCAL IMAGE FILE with a specific question using vision AI. "
            "The 'image' parameter must be a direct URL to an image file (.png, .jpg, .jpeg, .gif, .webp) "
            "OR a local workspace file path. "
            "⚠️ IMPORTANT: Do NOT pass webpage URLs (e.g. github.com, google.com) — "
            "those are not image files. Use fetch_url for webpages. "
            "Use this ONLY for actual image/photo files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": (
                        "Direct URL to an image file (must end in .png, .jpg, .jpeg, .gif, .webp) "
                        "OR local workspace file path. NOT a webpage URL."
                    ),
                },
                "question": {
                    "type": "string",
                    "description": "Specific question to ask about the image content",
                },
            },
            "required": ["image"],
        },
    },
    handler=handle_analyze_image,
    is_async=True,
    emoji="👁️",
)

registry.register(
    name="describe_image",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Get a detailed description of an IMAGE FILE using vision AI. "
            "Only works with actual image files (.png, .jpg, .jpeg, .gif, .webp), "
            "not webpage URLs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Direct URL to image file OR local workspace path",
                },
            },
            "required": ["image"],
        },
    },
    handler=handle_describe_image,
    is_async=True,
    emoji="🖼️",
)

registry.register(
    name="extract_text_from_image",
    toolset=_TOOLSET,
    schema={
        "description": (
            "OCR — extract/transcribe text from a standalone image file (screenshot, "
            "scanned page, photo of a document). Use this when you need the TEXT out of "
            "an image, not a description of it. Accepts a local workspace image path or a "
            "direct image URL (.png/.jpg/.jpeg/.gif/.webp). Uses local Tesseract when "
            "available, otherwise a vision model; the result reports which engine was used. "
            "For text embedded inside PDFs/Office docs, use convert_document with enable_ocr instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Local workspace image path OR direct image-file URL",
                },
                "lang": {
                    "type": "string",
                    "description": "Tesseract language code(s), e.g. 'eng', 'vie', 'eng+vie' (default 'eng')",
                    "default": "eng",
                },
            },
            "required": ["image"],
        },
    },
    handler=handle_extract_text_from_image,
    is_async=True,
    emoji="🔡",
)
