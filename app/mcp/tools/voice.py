"""
MekongAI Nexus - Text-to-Speech & Speech-to-Text tools.

Supports: Edge TTS (free), ElevenLabs (premium), Whisper (STT).
Inspired by: hermes-agent Edge/ElevenLabs TTS + Whisper/Groq STT

Usage::

    # Auto-registered tools:
    #   text_to_speech  — convert text to audio file (.mp3)
    #   speech_to_text  — transcribe audio file to text
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from app.config import get_settings
from app.mcp.tool_registry import registry
from app.mcp.tools.filesystem import get_task_workspace, WORKSPACE_DIR

logger = structlog.get_logger(__name__)


def _get_openai_api_key() -> str:
    """Best-effort OpenAI key resolution.

    The project config often uses OPENAI_API_KEY (settings.openai_api_key) rather
    than CORE_LLM_API_KEY. Keep Whisper usable without forcing duplicate env vars.
    """
    s = get_settings()
    return (s.openai_api_key or s.core_llm_api_key or os.environ.get("OPENAI_API_KEY", "")).strip()


# ------------------------------------------------------------------
# Dependency checks (graceful imports)
# ------------------------------------------------------------------

def _edge_tts_available() -> bool:
    """Check if edge-tts library is installed."""
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def _elevenlabs_available() -> bool:
    """Check if ElevenLabs API is configured."""
    try:
        import httpx  # noqa: F401 — used for ElevenLabs REST calls
        return bool(os.environ.get("ELEVENLABS_API_KEY"))
    except ImportError:
        return False


def _whisper_available() -> bool:
    """Check if OpenAI Whisper API is available."""
    try:
        import openai  # noqa: F401
        return bool(_get_openai_api_key())
    except ImportError:
        return False


def _tts_check() -> bool:
    """At least one TTS provider must be available."""
    return _edge_tts_available() or _elevenlabs_available()


def _stt_check() -> bool:
    """At least one STT provider must be available."""
    return _whisper_available()


# ------------------------------------------------------------------
# TTS Handlers
# ------------------------------------------------------------------

async def _tts_edge(text: str, voice: str, output_path: Path) -> Path:
    """Generate speech using Microsoft Edge TTS (free, no API key)."""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output_path))
    return output_path


async def _tts_elevenlabs(text: str, voice: str, output_path: Path) -> Path:
    """Generate speech using ElevenLabs API (premium quality)."""
    import httpx

    api_key = os.environ["ELEVENLABS_API_KEY"]
    # Default voice_id mapping — use voice name as voice_id directly
    voice_id = voice if len(voice) < 30 else "21m00Tcm4TlvDq8ikWAM"  # Rachel

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
    return output_path


async def handle_text_to_speech(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Convert text to speech audio file.

    Providers (tried in order):
    1. Edge TTS (free, no API key)
    2. ElevenLabs (premium, requires ELEVENLABS_API_KEY)
    """
    text = params.get("text", "")
    if not text:
        return {"success": False, "output": "No text provided", "data": None, "artifacts": []}

    voice = params.get("voice", "en-US-AriaNeural")
    output_filename = params.get("output_filename", "tts_output.mp3")

    ws = get_task_workspace()
    audio_dir = ws / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_path = audio_dir / output_filename

    try:
        if _edge_tts_available():
            await _tts_edge(text, voice, output_path)
            provider = "edge_tts"
        elif _elevenlabs_available():
            await _tts_elevenlabs(text, voice, output_path)
            provider = "elevenlabs"
        else:
            return {
                "success": False,
                "output": "No TTS provider available. Install edge-tts: pip install edge-tts",
                "data": None,
                "artifacts": [],
            }

        file_size = output_path.stat().st_size
        relative_path = output_path.relative_to(WORKSPACE_DIR)
        download_url = f"/api/v1/artifacts/download/{relative_path}"
        logger.info("tts_completed", provider=provider, voice=voice,
                     chars=len(text), file_size=file_size)
        return {
            "success": True,
            "output": f"Audio generated: {output_filename} ({file_size} bytes, provider={provider})",
            "data": {
                "path": str(relative_path),
                "download_url": download_url,
                "provider": provider,
                "voice": voice,
                "text_length": len(text),
                "file_size": file_size,
            },
            "artifacts": [{"type": "audio", "path": str(relative_path), "format": "mp3"}],
        }
    except Exception as exc:
        logger.error("tts_error", error=str(exc))
        return {"success": False, "output": f"TTS failed: {exc}", "data": None, "artifacts": []}


# ------------------------------------------------------------------
# STT Handlers
# ------------------------------------------------------------------

async def handle_speech_to_text(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Transcribe an audio file to text.

    Providers:
    1. OpenAI Whisper API (requires OPENAI_API_KEY)
    """
    audio_file = params.get("audio_file", "")
    language = params.get("language", "en")

    if not audio_file:
        return {"success": False, "output": "No audio_file provided", "data": None, "artifacts": []}

    # Resolve file path — check task workspace first, then global
    file_path = Path(audio_file)
    if not file_path.exists():
        ws = get_task_workspace()
        file_path = ws / audio_file
    if not file_path.exists():
        file_path = WORKSPACE_DIR / audio_file
    if not file_path.exists():
        return {
            "success": False,
            "output": f"Audio file not found: {audio_file}",
            "data": None,
            "artifacts": [],
        }

    if not _whisper_available():
        return {
            "success": False,
            "output": "No STT provider available. Set OPENAI_API_KEY for Whisper transcription.",
            "data": None,
            "artifacts": [],
        }

    try:
        import openai

        api_key = _get_openai_api_key()
        if not api_key:
            return {
                "success": False,
                "output": "No STT provider available. Set OPENAI_API_KEY for Whisper transcription.",
                "data": None,
                "artifacts": [],
            }

        client = openai.AsyncOpenAI(api_key=api_key)

        with open(file_path, "rb") as f:
            transcription = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=language,
            )

        text = transcription.text
        logger.info("stt_completed", file=audio_file, language=language, chars=len(text))
        return {
            "success": True,
            "output": text,
            "data": {
                "audio_file": audio_file,
                "language": language,
                "text_length": len(text),
                "provider": "openai_whisper",
            },
            "artifacts": [],
        }
    except Exception as exc:
        logger.error("stt_error", file=audio_file, error=str(exc))
        return {"success": False, "output": f"STT failed: {exc}", "data": None, "artifacts": []}


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "voice"

registry.register(
    name="text_to_speech",
    toolset=_TOOLSET,
    schema={
        "description": "Convert text to speech audio file (.mp3). Uses Edge TTS (free) or ElevenLabs.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to convert to speech",
                },
                "voice": {
                    "type": "string",
                    "description": "Voice identifier (default: en-US-AriaNeural for Edge TTS)",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Output filename (default: tts_output.mp3)",
                },
            },
            "required": ["text"],
        },
    },
    handler=handle_text_to_speech,
    check_fn=_tts_check,
    is_async=True,
    emoji="🔊",
)

registry.register(
    name="speech_to_text",
    toolset=_TOOLSET,
    schema={
        "description": "Transcribe an audio file to text using OpenAI Whisper API.",
        "parameters": {
            "type": "object",
            "properties": {
                "audio_file": {
                    "type": "string",
                    "description": "Path to the audio file (in workspace)",
                },
                "language": {
                    "type": "string",
                    "description": "Language code (default: en)",
                },
            },
            "required": ["audio_file"],
        },
    },
    handler=handle_speech_to_text,
    check_fn=_stt_check,
    is_async=True,
    emoji="🎤",
)
