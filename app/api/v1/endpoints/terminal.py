"""
Nexus - Terminal Endpoint with Persistent Session Tracking.

Provides shell command execution with:
- Session-based working directory persistence (cd tracking)
- Per-session cwd state stored in memory
- Session list/delete management endpoints
- Proper timeout and error handling

Uses the built-in execute_command tool when Hermes terminal is unavailable.
"""

import asyncio
import os
import re
import time
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings

logger = structlog.get_logger(__name__)

async def require_terminal_auth(x_terminal_key: str = Header(default="")) -> None:
    """Guard terminal endpoints with API key when configured."""
    _settings = get_settings()
    if not _settings.terminal_api_key:
        return  # No key configured — development mode, allow access
    if x_terminal_key != _settings.terminal_api_key:
        raise HTTPException(status_code=403, detail="Invalid terminal API key")


router = APIRouter(
    prefix="/terminal",
    tags=["Terminal"],
    dependencies=[Depends(require_terminal_auth)],
)


# ---------------------------------------------------------------------------
# Session Store (in-memory)
# ---------------------------------------------------------------------------

class _TerminalSession:
    """Tracks per-session state for terminal commands."""

    __slots__ = ("session_id", "cwd", "created_at", "last_used_at", "command_count")

    def __init__(self, session_id: str, cwd: str) -> None:
        self.session_id = session_id
        self.cwd = cwd
        self.created_at = time.time()
        self.last_used_at = time.time()
        self.command_count = 0

    def touch(self) -> None:
        self.last_used_at = time.time()
        self.command_count += 1


# Module-level session store — survives across requests
_sessions: dict[str, _TerminalSession] = {}

# Maximum number of concurrent sessions to prevent memory leak
_MAX_SESSIONS = 100

# Session TTL in seconds (2 hours)
_SESSION_TTL = 7200


def _get_or_create_session(session_id: Optional[str], cwd: Optional[str] = None) -> _TerminalSession:
    """Get existing session or create a new one."""
    # Prune expired sessions periodically
    _prune_expired_sessions()

    if session_id and session_id in _sessions:
        session = _sessions[session_id]
        # Allow explicit cwd override even on existing session
        if cwd:
            session.cwd = cwd
        return session

    # Create new session
    sid = session_id or str(uuid.uuid4())

    if len(_sessions) >= _MAX_SESSIONS:
        # Evict oldest session
        oldest_key = min(_sessions, key=lambda k: _sessions[k].last_used_at)
        del _sessions[oldest_key]
        logger.debug("terminal_session_evicted", evicted=oldest_key)

    default_cwd = cwd or os.getcwd()
    session = _TerminalSession(sid, default_cwd)
    _sessions[sid] = session
    logger.info("terminal_session_created", session_id=sid, cwd=default_cwd)
    return session


def _prune_expired_sessions() -> int:
    """Remove sessions older than TTL. Returns count removed."""
    now = time.time()
    expired = [k for k, v in _sessions.items() if now - v.last_used_at > _SESSION_TTL]
    for k in expired:
        del _sessions[k]
    if expired:
        logger.debug("terminal_sessions_pruned", count=len(expired))
    return len(expired)


# ---------------------------------------------------------------------------
# cwd Detection from Command Output
# ---------------------------------------------------------------------------

_CD_PATTERN = re.compile(
    r"^\s*cd\s+(.+)", re.IGNORECASE
)


def _detect_cd_target(command: str) -> Optional[str]:
    """Extract target directory from 'cd' commands.

    Handles:
      cd /path/to/dir
      cd ~/some/dir
      cd ..
      cd -  (not tracked)
    """
    m = _CD_PATTERN.match(command.strip())
    if not m:
        return None
    target = m.group(1).strip().strip('"').strip("'")
    # 'cd -' is unsupported (requires tracking previous dir)
    if target == "-":
        return None
    return target


def _resolve_cwd(current_cwd: str, command: str) -> Optional[str]:
    """Resolve the new cwd after a command, if it contains 'cd'.

    Handles chained commands like 'cd /tmp && ls' by extracting the LAST cd.
    """
    # Split on && and ; to find cd commands
    # Process in order — the last successful cd determines new cwd
    parts = re.split(r"&&|;", command)
    new_cwd = None
    working = current_cwd

    for part in parts:
        target = _detect_cd_target(part)
        if target:
            if os.path.isabs(target):
                candidate = target
            elif target.startswith("~"):
                candidate = os.path.expanduser(target)
            else:
                candidate = os.path.normpath(os.path.join(working, target))

            # Only update if directory exists
            if os.path.isdir(candidate):
                working = candidate
                new_cwd = candidate

    return new_cwd


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class TerminalRequest(BaseModel):
    command: str = Field(..., description="Shell command to execute")
    timeout: float = Field(90, description="Execution timeout in seconds", ge=1, le=600)
    session_id: Optional[str] = Field(None, description="Session ID for persistent cwd tracking")
    cwd: Optional[str] = Field(None, description="Override working directory for this command")


class TerminalResponse(BaseModel):
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    success: bool = True
    cwd: str = ""
    session_id: str = ""


class CompleteRequest(BaseModel):
    partial: str = Field("", description="Partial path/filename to complete")
    session_id: Optional[str] = Field(None, description="Session ID to resolve cwd from")
    cwd: Optional[str] = Field(None, description="Override working directory")


class CompleteItem(BaseModel):
    name: str
    is_dir: bool
    full_path: str


class CompleteResponse(BaseModel):
    items: list[CompleteItem]
    base_dir: str


class SessionInfo(BaseModel):
    session_id: str
    cwd: str
    created_at: float
    last_used_at: float
    command_count: int


class SessionListResponse(BaseModel):
    sessions: list[SessionInfo]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/execute", response_model=TerminalResponse)
async def execute_terminal(req: TerminalRequest):
    """Execute a shell command with persistent session tracking.

    - If ``session_id`` is provided, reuses that session's working directory.
    - ``cd`` commands within the shell update the session's cwd for subsequent calls.
    - ``cwd`` parameter overrides the session cwd for this single call.
    - Response includes current ``cwd`` so the frontend can display the prompt.
    """
    session = _get_or_create_session(req.session_id, req.cwd)
    effective_cwd = req.cwd or session.cwd

    # Ensure the cwd exists
    if not os.path.isdir(effective_cwd):
        effective_cwd = os.getcwd()
        session.cwd = effective_cwd

    try:
        proc = await asyncio.create_subprocess_shell(
            req.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=req.timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            session.touch()
            return TerminalResponse(
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {req.timeout}s",
                success=False,
                cwd=session.cwd,
                session_id=session.session_id,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        exit_code = proc.returncode or 0
        success = exit_code == 0

        # Track cd commands — update session cwd
        if success:
            new_cwd = _resolve_cwd(effective_cwd, req.command)
            if new_cwd:
                session.cwd = new_cwd
                logger.debug(
                    "terminal_cwd_updated",
                    session_id=session.session_id,
                    old_cwd=effective_cwd,
                    new_cwd=new_cwd,
                )

        session.touch()

        return TerminalResponse(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            success=success,
            cwd=session.cwd,
            session_id=session.session_id,
        )

    except Exception as e:
        logger.error("terminal_execute_error", error=str(e), command=req.command[:100])
        session.touch()
        return TerminalResponse(
            exit_code=1,
            stderr=str(e),
            success=False,
            cwd=session.cwd,
            session_id=session.session_id,
        )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions():
    """List all active terminal sessions."""
    _prune_expired_sessions()
    sessions = [
        SessionInfo(
            session_id=s.session_id,
            cwd=s.cwd,
            created_at=s.created_at,
            last_used_at=s.last_used_at,
            command_count=s.command_count,
        )
        for s in _sessions.values()
    ]
    return SessionListResponse(sessions=sessions, total=len(sessions))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a terminal session."""
    if session_id in _sessions:
        del _sessions[session_id]
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@router.delete("/sessions")
async def delete_all_sessions():
    """Delete all terminal sessions."""
    count = len(_sessions)
    _sessions.clear()
    return {"status": "cleared", "deleted_count": count}


@router.post("/complete", response_model=CompleteResponse)
async def complete_path(req: CompleteRequest):
    """Autocomplete file/directory names relative to session cwd.

    Given a ``partial`` string (which may include path separators),
    lists matching entries in the resolved directory.  Used by the
    frontend terminal to provide Tab-completion for paths.
    """
    # Resolve base directory from session
    session = _get_or_create_session(req.session_id, req.cwd)
    base_cwd = req.cwd or session.cwd or os.getcwd()

    if not os.path.isdir(base_cwd):
        base_cwd = os.getcwd()

    partial = req.partial or ""

    # Split partial into directory part and prefix
    # e.g. "src/comp" → dir_part="src", prefix="comp"
    # e.g. "READ"    → dir_part="",    prefix="READ"
    # e.g. "src/"    → dir_part="src",  prefix=""
    normalized = partial.replace("\\", "/")
    if "/" in normalized:
        last_slash = normalized.rfind("/")
        dir_part = partial[:last_slash + 1]  # keep original separators
        prefix = partial[last_slash + 1:]
    else:
        dir_part = ""
        prefix = partial

    # Resolve the directory to scan
    if dir_part:
        scan_dir = os.path.normpath(os.path.join(base_cwd, dir_part))
    else:
        scan_dir = base_cwd

    if not os.path.isdir(scan_dir):
        return CompleteResponse(items=[], base_dir=base_cwd)

    try:
        entries = os.listdir(scan_dir)
    except PermissionError:
        return CompleteResponse(items=[], base_dir=base_cwd)

    prefix_lower = prefix.lower()
    items: list[CompleteItem] = []

    for entry in sorted(entries):
        # Skip hidden files unless user explicitly typed a dot prefix
        if entry.startswith(".") and not prefix.startswith("."):
            continue
        if entry.lower().startswith(prefix_lower):
            full = os.path.join(scan_dir, entry)
            is_dir = os.path.isdir(full)
            # Return path relative to cwd for display
            display_name = (dir_part + entry) if dir_part else entry
            items.append(CompleteItem(
                name=display_name + ("/" if is_dir else ""),
                is_dir=is_dir,
                full_path=full,
            ))

        if len(items) >= 30:  # limit results
            break

    return CompleteResponse(items=items, base_dir=scan_dir)
