"""
MekongAI Nexus - Video Tools.

Real video processing via ffmpeg/ffprobe (shelled out). Capabilities:
    - video_info     : probe metadata (duration, streams, codec, resolution, fps)
    - extract_frames : pull frames as images (every N seconds, or a single timestamp)
    - extract_audio  : demux/transcode the audio track to a file (feed to speech_to_text)

These tools require ffmpeg on PATH. When ffmpeg is absent the tools are gated out
by check_fn (so agents aren't offered a capability the host can't honor) and, if
called anyway, return an honest error rather than fabricating results.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))

_TOOLSET = "video"


def _ffmpeg_available() -> bool:
    """True only if both ffmpeg and ffprobe are on PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _resolve_video(rel_path: str) -> Path | None:
    """Resolve a video path inside the workspace (None if it escapes)."""
    from app.mcp.tools.filesystem import (
        WORKSPACE_DIR as FS_WORKSPACE_DIR,
        _smart_resolve_path,
        get_task_workspace,
    )

    ws = get_task_workspace()
    path = _smart_resolve_path(rel_path, ws, FS_WORKSPACE_DIR)
    try:
        if not str(path.resolve()).startswith(str(FS_WORKSPACE_DIR.resolve())):
            return None
    except OSError:
        return None
    if not path.exists() and ws != FS_WORKSPACE_DIR:
        alt = FS_WORKSPACE_DIR / rel_path
        if alt.exists():
            return alt
    return path


async def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Run a subprocess, returning (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"timed out after {timeout}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _ffmpeg_missing_result() -> dict[str, Any]:
    return {
        "success": False,
        "output": (
            "ffmpeg/ffprobe not found on PATH. Video tools need ffmpeg. "
            "Install it (e.g. `apt-get install -y ffmpeg`) then retry. "
            "No video processing was performed."
        ),
        "data": None,
        "artifacts": [],
    }


async def handle_video_info(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Probe a video file for real metadata via ffprobe."""
    rel_path = params.get("path", "")
    if not rel_path:
        return {"success": False, "output": "⚠️ 'path' is required.", "data": None, "artifacts": []}
    if not _ffmpeg_available():
        return _ffmpeg_missing_result()

    path = _resolve_video(rel_path)
    if path is None:
        return {"success": False, "output": "Access denied: path resolves outside workspace.", "data": None, "artifacts": []}
    if not path.exists():
        return {"success": False, "output": f"File not found: {rel_path}", "data": None, "artifacts": []}

    rc, out, err = await _run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if rc != 0:
        return {"success": False, "output": f"ffprobe failed: {err[:300]}", "data": None, "artifacts": []}

    try:
        probe = json.loads(out)
    except json.JSONDecodeError as e:
        return {"success": False, "output": f"Cannot parse ffprobe output: {e}", "data": None, "artifacts": []}

    fmt = probe.get("format", {})
    streams = probe.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})

    duration = float(fmt.get("duration", 0) or 0)
    fps = 0.0
    if v.get("r_frame_rate") and "/" in v["r_frame_rate"]:
        num, den = v["r_frame_rate"].split("/")
        fps = (float(num) / float(den)) if float(den) else 0.0

    summary = [
        f"🎬 {rel_path}",
        f"   Duration: {duration:.1f}s | Size: {int(fmt.get('size', 0)):,} bytes",
        f"   Video: {v.get('codec_name', '?')} {v.get('width', '?')}x{v.get('height', '?')} @ {fps:.2f}fps" if v else "   Video: (none)",
        f"   Audio: {a.get('codec_name', '?')} {a.get('sample_rate', '?')}Hz {a.get('channels', '?')}ch" if a else "   Audio: (none)",
    ]
    return {
        "success": True,
        "output": "\n".join(summary),
        "data": {
            "path": rel_path,
            "duration_seconds": duration,
            "size_bytes": int(fmt.get("size", 0) or 0),
            "video": {"codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"), "fps": fps} if v else None,
            "audio": {"codec": a.get("codec_name"), "sample_rate": a.get("sample_rate"), "channels": a.get("channels")} if a else None,
        },
        "artifacts": [],
    }


async def handle_extract_frames(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Extract frame(s) from a video as image files via ffmpeg.

    Either a single 'timestamp' (seconds) → one frame, or 'every_seconds' → a
    frame every N seconds. Output PNGs land in 'output_dir' under the workspace.
    """
    rel_path = params.get("path", "")
    timestamp = params.get("timestamp")
    every_seconds = params.get("every_seconds")
    output_dir = params.get("output_dir", "frames")
    max_frames = params.get("max_frames", 60)

    if not rel_path:
        return {"success": False, "output": "⚠️ 'path' is required.", "data": None, "artifacts": []}
    if not _ffmpeg_available():
        return _ffmpeg_missing_result()

    path = _resolve_video(rel_path)
    if path is None:
        return {"success": False, "output": "Access denied: path resolves outside workspace.", "data": None, "artifacts": []}
    if not path.exists():
        return {"success": False, "output": f"File not found: {rel_path}", "data": None, "artifacts": []}

    out_dir = _resolve_video(output_dir)
    if out_dir is None:
        return {"success": False, "output": "Access denied: output_dir outside workspace.", "data": None, "artifacts": []}
    out_dir.mkdir(parents=True, exist_ok=True)

    if timestamp is not None:
        # Single frame at the given timestamp.
        out_file = out_dir / f"frame_{float(timestamp):.0f}s.png"
        cmd = ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(path),
               "-frames:v", "1", str(out_file)]
    else:
        # Periodic frames. fps = 1/every_seconds.
        n = float(every_seconds) if every_seconds else 5.0
        if n <= 0:
            return {"success": False, "output": "every_seconds must be > 0.", "data": None, "artifacts": []}
        out_pattern = out_dir / "frame_%04d.png"
        cmd = ["ffmpeg", "-y", "-i", str(path), "-vf", f"fps=1/{n}",
               "-frames:v", str(int(max_frames)), str(out_pattern)]

    rc, _, err = await _run(cmd, timeout=300)
    if rc != 0:
        return {"success": False, "output": f"ffmpeg frame extraction failed: {err[-300:]}", "data": None, "artifacts": []}

    frames = sorted(p.name for p in out_dir.glob("frame_*.png"))
    rel_out = str(out_dir.relative_to(WORKSPACE_DIR)) if str(out_dir).startswith(str(WORKSPACE_DIR)) else output_dir
    return {
        "success": True,
        "output": f"🖼️ Extracted {len(frames)} frame(s) to {rel_out}/",
        "data": {"output_dir": rel_out, "frames": frames, "count": len(frames)},
        "artifacts": [{"type": "dir", "path": rel_out}],
    }


async def handle_extract_audio(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Extract the audio track of a video to a file (default .mp3) via ffmpeg.

    The result can be fed to speech_to_text for transcription.
    """
    rel_path = params.get("path", "")
    out_name = params.get("output", "")

    if not rel_path:
        return {"success": False, "output": "⚠️ 'path' is required.", "data": None, "artifacts": []}
    if not _ffmpeg_available():
        return _ffmpeg_missing_result()

    path = _resolve_video(rel_path)
    if path is None:
        return {"success": False, "output": "Access denied: path resolves outside workspace.", "data": None, "artifacts": []}
    if not path.exists():
        return {"success": False, "output": f"File not found: {rel_path}", "data": None, "artifacts": []}

    if not out_name:
        out_name = path.stem + ".mp3"
    out_path = _resolve_video(out_name)
    if out_path is None:
        return {"success": False, "output": "Access denied: output outside workspace.", "data": None, "artifacts": []}
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # -vn drops video; let ffmpeg pick codec from the output extension.
    rc, _, err = await _run([
        "ffmpeg", "-y", "-i", str(path), "-vn", "-acodec",
        "libmp3lame" if out_path.suffix.lower() == ".mp3" else "copy",
        str(out_path),
    ], timeout=300)
    if rc != 0:
        return {"success": False, "output": f"ffmpeg audio extraction failed: {err[-300:]}", "data": None, "artifacts": []}

    rel_out = str(out_path.relative_to(WORKSPACE_DIR)) if str(out_path).startswith(str(WORKSPACE_DIR)) else out_name
    size = out_path.stat().st_size if out_path.exists() else 0
    return {
        "success": True,
        "output": f"🔊 Extracted audio → {rel_out} ({size:,} bytes). Feed it to speech_to_text to transcribe.",
        "data": {"output": rel_out, "size_bytes": size},
        "artifacts": [{"type": "file", "path": rel_out, "size": size}],
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="video_info",
    toolset=_TOOLSET,
    handler=handle_video_info,
    description=(
        "Probe a video file for metadata (duration, resolution, fps, codecs, audio track) "
        "via ffprobe. Use before extracting frames/audio to know the video's properties. "
        "Requires ffmpeg installed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Video path relative to workspace"},
        },
        "required": ["path"],
    },
    emoji="🎬",
    category="video",
    check_fn=_ffmpeg_available,
)

registry.register(
    name="extract_frames",
    toolset=_TOOLSET,
    handler=handle_extract_frames,
    description=(
        "Extract frame(s) from a video as PNG images via ffmpeg. Provide 'timestamp' "
        "(seconds) for a single frame, or 'every_seconds' to sample a frame every N seconds "
        "(up to max_frames). Pair with analyze_image/extract_text_from_image to understand "
        "video content. Requires ffmpeg installed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Video path relative to workspace"},
            "timestamp": {"type": "number", "description": "Single-frame mode: seconds into the video"},
            "every_seconds": {"type": "number", "description": "Periodic mode: extract a frame every N seconds"},
            "output_dir": {"type": "string", "description": "Workspace dir for frames (default 'frames')", "default": "frames"},
            "max_frames": {"type": "integer", "description": "Max frames in periodic mode (default 60)", "default": 60},
        },
        "required": ["path"],
    },
    emoji="🖼️",
    category="video",
    check_fn=_ffmpeg_available,
)

registry.register(
    name="extract_audio",
    toolset=_TOOLSET,
    handler=handle_extract_audio,
    description=(
        "Extract the audio track from a video into an audio file (default .mp3) via ffmpeg. "
        "The output can be transcribed with speech_to_text. Requires ffmpeg installed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Video path relative to workspace"},
            "output": {"type": "string", "description": "Output audio filename (default: <video>.mp3)"},
        },
        "required": ["path"],
    },
    emoji="🔊",
    category="video",
    check_fn=_ffmpeg_available,
)
