"""
Nexus - Image Generation Tool (Hermes-agent inspired).

Generate images using AI models (FLUX via fal.ai, DALL-E 3 via OpenAI).
Self-registers with the tool registry at import time.

Inspired by: hermes-agent/tools/image_generation_tool.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from app.config import get_settings
from app.mcp.tool_registry import registry, tool_result, tool_error
from app.mcp.tools.filesystem import get_task_workspace, WORKSPACE_DIR

logger = structlog.get_logger(__name__)


def _get_images_dir() -> Path:
    """Get generated_images directory within current task workspace."""
    ws = get_task_workspace()
    d = ws / "generated_images"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _image_generate(args: dict[str, Any]) -> str:
    """Generate an image from a text prompt."""
    prompt = args.get("prompt", "").strip()
    if not prompt:
        return tool_error("Image prompt is required")

    provider = args.get("provider", "auto").lower()
    size = args.get("size", "1024x1024")
    filename = args.get("filename", f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")

    _get_images_dir()  # Ensure directory exists

    # Try fal.ai FLUX first, then OpenAI DALL-E
    if provider in ("auto", "flux", "fal"):
        result = await _generate_flux(prompt, size, filename)
        if result is not None:
            return result

    if provider in ("auto", "openai", "dalle"):
        result = await _generate_dalle(prompt, size, filename)
        if result is not None:
            return result

    return tool_error(
        f"Image generation failed. No provider available. "
        f"Set FAL_KEY or OPENAI_API_KEY environment variable."
    )


async def _generate_flux(prompt: str, size: str, filename: str) -> str | None:
    """Generate image via FLUX on fal.ai."""
    fal_key = os.environ.get("FAL_KEY", "")
    if not fal_key:
        return None

    try:
        import httpx

        width, height = _parse_size(size)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://fal.run/fal-ai/flux/schnell",
                headers={"Authorization": f"Key {fal_key}"},
                json={
                    "prompt": prompt,
                    "image_size": {"width": width, "height": height},
                    "num_images": 1,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        images = data.get("images", [])
        if not images:
            return tool_error("FLUX returned no images")

        image_url = images[0].get("url", "")
        if not image_url:
            return tool_error("FLUX returned empty image URL")

        # Download the image
        async with httpx.AsyncClient(timeout=60.0) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()

        output_path = _get_images_dir() / filename
        output_path.write_bytes(img_resp.content)

        relative_path = output_path.relative_to(WORKSPACE_DIR)
        download_url = f"/api/v1/artifacts/download/{relative_path}"
        return tool_result(
            success=True,
            output=f"🎨 Image generated via FLUX:\n- File: {filename}\n- Size: {size}\n- Prompt: {prompt[:100]}",
            data={"path": str(relative_path), "download_url": download_url, "url": image_url, "provider": "flux"},
        )

    except Exception as e:
        logger.warning("flux_generation_failed", error=str(e))
        return None


async def _generate_dalle(prompt: str, size: str, filename: str) -> str | None:
    """Generate image via OpenAI DALL-E 3."""
    s = get_settings()
    api_key = (s.openai_api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not api_key:
        return None

    try:
        import httpx

        valid_sizes = {"1024x1024", "1792x1024", "1024x1792"}
        dalle_size = size if size in valid_sizes else "1024x1024"

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "dall-e-3",
                    "prompt": prompt,
                    "n": 1,
                    "size": dalle_size,
                    "response_format": "url",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        images = data.get("data", [])
        if not images:
            return tool_error("DALL-E returned no images")

        image_url = images[0].get("url", "")
        if not image_url:
            return tool_error("DALL-E returned empty URL")

        # Download
        async with httpx.AsyncClient(timeout=60.0) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()

        output_path = _get_images_dir() / filename
        output_path.write_bytes(img_resp.content)

        relative_path = output_path.relative_to(WORKSPACE_DIR)
        download_url = f"/api/v1/artifacts/download/{relative_path}"
        return tool_result(
            success=True,
            output=f"🎨 Image generated via DALL-E 3:\n- File: {filename}\n- Size: {dalle_size}\n- Prompt: {prompt[:100]}",
            data={"path": str(relative_path), "download_url": download_url, "provider": "dalle"},
        )

    except Exception as e:
        logger.warning("dalle_generation_failed", error=str(e))
        return None


def _parse_size(size: str) -> tuple[int, int]:
    """Parse WxH size string."""
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 1024, 1024


# ── Self-registration ────────────────────────────────────────────────────

registry.register(
    name="image_generate",
    toolset="image_gen",
    schema={
        "description": "Generate an image from a text prompt using AI (FLUX or DALL-E 3).",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed description of the image to generate"},
                "size": {"type": "string", "description": "Image size (e.g. 1024x1024)", "default": "1024x1024"},
                "provider": {"type": "string", "enum": ["auto", "flux", "dalle"], "default": "auto"},
                "filename": {"type": "string", "description": "Output filename (optional)"},
            },
            "required": ["prompt"],
        },
    },
    handler=_image_generate,
    is_async=True,
    emoji="🎨",
    check_fn=lambda: bool(os.environ.get("FAL_KEY") or get_settings().openai_api_key or os.environ.get("OPENAI_API_KEY")),
    requires_env=["FAL_KEY", "OPENAI_API_KEY"],
)
