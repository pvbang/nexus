"""
MekongAI Nexus CLI — command-line interface.

Usage:
    python -m app.cli chat "Hello, how are you?"
    python -m app.cli chat --interactive
    python -m app.cli status
    python -m app.cli skills list
    python -m app.cli skills search "python"
    python -m app.cli doctor
    python -m app.cli models
    python -m app.cli profiles list
    python -m app.cli profiles set coder
    nexus chat "Hello"        # via console_scripts entry point

HTTP client: httpx (sync) calling the running API server.
Base URL: NEXUS_API_URL env var, default http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import time
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_VERSION = "0.1.0"
_APP_NAME = "MekongAI Nexus"
_DEFAULT_BASE_URL = "http://localhost:8000"
_API_PREFIX = "/api/v1"
_REQUEST_TIMEOUT = 60.0
_STREAM_TIMEOUT = 120.0

# ---------------------------------------------------------------------------
# ANSI helpers (no external dependency)
# ---------------------------------------------------------------------------
_NO_COLOR = os.environ.get("NO_COLOR") is not None


def _ansi(code: str, text: str) -> str:
    """Wrap *text* in ANSI escape code unless NO_COLOR is set."""
    if _NO_COLOR or not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _bold(t: str) -> str:
    return _ansi("1", t)


def _green(t: str) -> str:
    return _ansi("32", t)


def _red(t: str) -> str:
    return _ansi("31", t)


def _cyan(t: str) -> str:
    return _ansi("36", t)


def _yellow(t: str) -> str:
    return _ansi("33", t)


def _dim(t: str) -> str:
    return _ansi("2", t)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _base_url() -> str:
    return os.environ.get("NEXUS_API_URL", _DEFAULT_BASE_URL).rstrip("/")


def _url(path: str) -> str:
    """Build full API URL."""
    return f"{_base_url()}{_API_PREFIX}{path}"


def _get(path: str, *, timeout: float = _REQUEST_TIMEOUT) -> dict[str, Any]:
    """Perform GET and return parsed JSON."""
    resp = httpx.get(_url(path), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _post(
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = _REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """Perform POST and return parsed JSON."""
    resp = httpx.post(_url(path), json=payload or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _stream_post(
    path: str,
    payload: dict[str, Any],
) -> None:
    """POST with SSE streaming — print tokens as they arrive."""
    with httpx.stream(
        "POST",
        _url(path),
        json=payload,
        timeout=httpx.Timeout(_STREAM_TIMEOUT, connect=10.0),
        headers={"Accept": "text/event-stream"},
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            etype = event.get("type", "")
            if etype == "token":
                sys.stdout.write(event.get("content", ""))
                sys.stdout.flush()
            elif etype == "tool_call":
                name = event.get("name", "?")
                sys.stdout.write(f"\n{_dim(f'[tool: {name}]')}\n")
                sys.stdout.flush()
            elif etype == "tool_result":
                output = event.get("output", "")
                preview = output[:200] if isinstance(output, str) else str(output)[:200]
                sys.stdout.write(f"{_dim(preview)}\n")
                sys.stdout.flush()
            elif etype == "completed":
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif etype == "error":
                msg = event.get("message", "Unknown error")
                sys.stdout.write(f"\n{_red(f'Error: {msg}')}\n")
                sys.stdout.flush()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_json(data: Any) -> None:
    """Pretty-print JSON to stdout."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Minimal table printer using ANSI."""
    if not rows:
        print(_dim("  (no results)"))
        return
    # Compute column widths
    widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            val = str(row.get(c, ""))
            widths[c] = max(widths[c], min(len(val), 60))
    # Header
    header = "  ".join(_bold(c.ljust(widths[c])) for c in columns)
    print(header)
    print("  ".join("─" * widths[c] for c in columns))
    # Rows
    for row in rows:
        line = "  ".join(str(row.get(c, "")).ljust(widths[c])[:widths[c]] for c in columns)
        print(line)


def _render_markdown_simple(text: str) -> str:
    """Minimal terminal markdown: bold headers, dim code blocks."""
    lines: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
            lines.append(_dim(line))
        elif in_code:
            lines.append(_dim(line))
        elif line.startswith("# "):
            lines.append(_bold(line[2:]))
        elif line.startswith("## "):
            lines.append(_bold(line[3:]))
        elif line.startswith("### "):
            lines.append(_bold(line[4:]))
        elif line.startswith("- ") or line.startswith("* "):
            lines.append(f"  • {line[2:]}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _error(msg: str) -> None:
    """Print error and exit."""
    print(f"{_red('Error:')} {msg}", file=sys.stderr)


def _success(msg: str) -> None:
    print(f"{_green('✓')} {msg}")


def _info(msg: str) -> None:
    print(f"{_cyan('ℹ')} {msg}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_chat(args: argparse.Namespace) -> int:
    """Handle `nexus chat` command."""
    if args.interactive:
        return _interactive_chat(args)

    if not args.message:
        _error("Cần cung cấp message hoặc dùng --interactive. Ví dụ: nexus chat \"Hello\"")
        return 1

    message = " ".join(args.message)
    payload: dict[str, Any] = {
        "message": message,
        "user_id": "cli_user",
        "conversation_id": args.session or None,
        "use_free": use_free,
    }

    if args.stream:
        try:
            _stream_post("/chat/stream", payload)
        except httpx.HTTPStatusError as exc:
            _error(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
            return 1
        return 0

    data = _post("/chat/simple", payload, timeout=_STREAM_TIMEOUT)
    if args.json:
        _print_json(data)
    else:
        content = data.get("message") or data.get("response", "")
        print(_render_markdown_simple(content))
    return 0


def _interactive_chat(args: argparse.Namespace) -> int:
    """REPL loop for interactive chat."""
    session_id = args.session or f"cli-{int(time.time())}"
    model = getattr(args, "model", None) or ""
    profile = getattr(args, "profile", None) or ""
    use_free = getattr(args, "free", False)

    print(_bold(f"  {_APP_NAME} Interactive Chat"))
    print(_dim(f"  Session: {session_id}"))
    if model:
        print(_dim(f"  Model: {model}"))
    elif use_free:
        print(_dim(f"  Model: free (Kilo Gateway anonymous)"))
    if profile:
        print(_dim(f"  Profile: {profile}"))
    print(_dim("  Gõ /help để xem lệnh. /quit để thoát.\n"))

    use_stream = getattr(args, "stream", False)

    while True:
        try:
            user_input = input(f"{_cyan('nexus')}> ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{_dim('Goodbye!')}")
            return 0

        if not user_input:
            continue

        # Slash commands
        if user_input.startswith("/"):
            rc, new_sid = _handle_slash_command(user_input, session_id)
            if rc == -1:  # quit signal
                return 0
            if new_sid:
                session_id = new_sid
            continue

        payload: dict[str, Any] = {
            "message": user_input,
            "user_id": "cli_user",
            "conversation_id": session_id,
            "use_free": use_free,
        }

        try:
            if use_stream:
                _stream_post("/chat/stream", payload)
            else:
                data = _post("/chat/simple", payload, timeout=_STREAM_TIMEOUT)
                content = data.get("message") or data.get("response", "")
                print(f"\n{_render_markdown_simple(content)}\n")
        except httpx.ConnectError:
            _error(f"Không kết nối được tới server tại {_base_url()}")
            _info("Đảm bảo server đang chạy: uvicorn app.main:app")
        except httpx.HTTPStatusError as exc:
            _error(f"HTTP {exc.response.status_code}: {exc.response.text[:300]}")
        except KeyboardInterrupt:
            print(f"\n{_dim('(interrupted)')}")

    return 0  # unreachable, but keeps type-checkers happy


def _handle_slash_command(cmd: str, session_id: str) -> tuple[int, str | None]:
    """Process interactive-mode slash commands.

    Returns:
        Tuple of (return_code, new_session_id).
        return_code -1 means quit. new_session_id is set when /clear resets session.
    """
    parts = cmd.strip().split(None, 1)
    verb = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if verb in ("/quit", "/exit", "/q"):
        print(_dim("Goodbye!"))
        return -1, None

    if verb == "/help":
        print(_dim(textwrap.dedent("""\
            Slash commands:
              /quit, /exit     Thoát interactive mode
              /clear           Tạo session mới
              /status          Kiểm tra server status
              /model           Hiển thị danh sách models
              /profile         Hiển thị danh sách profiles
              /think           Bật extended thinking (prefix message)
              /help            Hiển thị help này
        """)))
        return 0, None

    if verb == "/clear":
        new_sid = f"cli-{int(time.time())}"
        _info(f"Session mới: {new_sid}")
        return 0, new_sid

    if verb == "/status":
        try:
            data = _get("/system/info")
            _print_status(data, as_json=False)
        except Exception as exc:
            _error(str(exc))
        return 0, None

    if verb == "/model":
        try:
            models = _get("/chat/models")
            for m in models:
                print(f"  {_cyan(m.get('name', '?'))}  {_dim(m.get('provider', ''))}")
        except Exception as exc:
            _error(str(exc))
        return 0, None

    if verb == "/profile":
        try:
            profiles = _get("/chat/profiles")
            for p in profiles:
                print(f"  {_cyan(p.get('name', '?'))}  {_dim(p.get('description', ''))}")
        except Exception as exc:
            _error(str(exc))
        return 0, None

    print(_dim(f"Lệnh không hợp lệ: {verb}. Gõ /help để xem danh sách."))
    return 0, None


def cmd_status(args: argparse.Namespace) -> int:
    """Handle `nexus status` command."""
    data = _get("/system/info")
    if args.json:
        _print_json(data)
    else:
        _print_status(data, as_json=False)
    return 0


def _print_status(data: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        _print_json(data)
        return
    print(_bold(f"\n  {_APP_NAME} Status\n"))
    print(f"  {'Version:':<20} {_cyan(data.get('version', '?'))}")
    print(f"  {'Environment:':<20} {data.get('env', '?')}")
    print(f"  {'Core LLM:':<20} {data.get('core_llm', '?')}")
    print(f"  {'Agent LLM:':<20} {data.get('agent_llm', '?')}")
    print(f"  {'Local LLM:':<20} {_green('enabled') if data.get('local_llm_enabled') else _dim('disabled')}")
    print(f"  {'Docker Sandbox:':<20} {_green('enabled') if data.get('docker_sandbox') else _dim('disabled')}")
    print(f"  {'IronClaw:':<20} {_green('enabled') if data.get('ironclaw_enabled') else _dim('disabled')}")
    print()


def cmd_doctor(args: argparse.Namespace) -> int:
    """Handle `nexus doctor` command."""
    _info("Running system diagnostics...\n")
    data = _get("/doctor", timeout=30.0)

    if args.json:
        _print_json(data)
        return 0

    summary = data.get("summary", {})
    results = data.get("results", [])

    print(_bold(f"  {_APP_NAME} Doctor\n"))

    status_icon = {
        "ok": _green("✓"),
        "warning": _yellow("⚠"),
        "error": _red("✗"),
    }

    for r in results:
        icon = status_icon.get(r.get("status", ""), "?")
        check = r.get("check", "unknown")
        message = r.get("message", "")
        hint = r.get("fix_hint", "")
        print(f"  {icon} {check:<30} {message}")
        if hint and r.get("status") != "ok":
            print(f"    {_dim(f'Fix: {hint}')}")

    total = summary.get("total", 0)
    ok = summary.get("ok", 0)
    warnings = summary.get("warnings", 0)
    errors = summary.get("errors", 0)
    print(f"\n  Total: {total}  {_green(f'OK: {ok}')}  {_yellow(f'Warnings: {warnings}')}  {_red(f'Errors: {errors}')}\n")

    return 1 if errors > 0 else 0


def cmd_models(args: argparse.Namespace) -> int:
    """Handle `nexus models` command."""
    data = _get("/chat/models")

    if args.json:
        _print_json(data)
        return 0

    print(_bold(f"\n  Available Models\n"))
    if isinstance(data, list):
        _print_table(data, ["name", "provider", "description"])
    else:
        _print_json(data)
    print()
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    """Handle `nexus skills` command."""
    action = args.skills_action

    if action == "list":
        data = _get("/skills/")
        if args.json:
            _print_json(data)
            return 0
        print(_bold(f"\n  Installed Skills\n"))
        if isinstance(data, list):
            rows = [
                {
                    "skill_id": s.get("skill_id", ""),
                    "name": s.get("name", ""),
                    "category": s.get("category", ""),
                    "version": s.get("version", ""),
                    "enabled": _green("✓") if s.get("enabled") else _red("✗"),
                }
                for s in data
            ]
            _print_table(rows, ["skill_id", "name", "category", "version", "enabled"])
        else:
            _print_json(data)
        print()
        return 0

    if action == "search":
        if not args.query:
            _error("Cần cung cấp query. Ví dụ: nexus skills search \"python\"")
            return 1
        query = " ".join(args.query)
        data = _post("/skills/search", {"query": query})
        if args.json:
            _print_json(data)
            return 0
        print(_bold(f"\n  Search Results: \"{query}\"\n"))
        if isinstance(data, list):
            rows = [
                {
                    "skill_id": s.get("skill_id", ""),
                    "name": s.get("name", ""),
                    "category": s.get("category", ""),
                    "description": (s.get("description", "") or "")[:50],
                }
                for s in data
            ]
            _print_table(rows, ["skill_id", "name", "category", "description"])
        else:
            _print_json(data)
        print()
        return 0

    if action == "info":
        if not args.skill_id:
            _error("Cần cung cấp skill_id. Ví dụ: nexus skills info python-developer")
            return 1
        data = _get(f"/skills/{args.skill_id}")
        if args.json:
            _print_json(data)
            return 0
        print(_bold(f"\n  Skill: {data.get('name', args.skill_id)}\n"))
        for key in ("skill_id", "version", "author", "category", "description", "source", "enabled"):
            val = data.get(key, "")
            print(f"  {key:<20} {val}")
        tags = data.get("tags", [])
        if tags:
            print(f"  {'tags':<20} {', '.join(tags)}")
        tools = data.get("tools_required", [])
        if tools:
            print(f"  {'tools_required':<20} {', '.join(tools)}")
        prompt = data.get("system_prompt", "")
        if prompt:
            print(f"\n  {_bold('System Prompt:')}")
            print(textwrap.indent(prompt[:500], "    "))
        print()
        return 0

    _error(f"Unknown skills action: {action}. Use: list, search, info")
    return 1


def cmd_profiles(args: argparse.Namespace) -> int:
    """Handle `nexus profiles` command."""
    action = args.profiles_action

    if action == "list":
        data = _get("/chat/profiles")
        if args.json:
            _print_json(data)
            return 0
        print(_bold(f"\n  Conversation Profiles\n"))
        if isinstance(data, list):
            rows = [
                {
                    "name": p.get("name", ""),
                    "description": (p.get("description", "") or "")[:50],
                    "temperature": str(p.get("temperature", "")),
                }
                for p in data
            ]
            _print_table(rows, ["name", "description", "temperature"])
        else:
            _print_json(data)
        print()
        return 0

    if action == "set":
        if not args.name:
            _error("Cần cung cấp profile name. Ví dụ: nexus profiles set coder")
            return 1
        session_id = args.session or f"cli-{int(time.time())}"
        data = _post(
            f"/chat/profiles/{session_id}",
            {"profile_name": args.name},
        )
        if args.json:
            _print_json(data)
        else:
            _success(f"Profile '{args.name}' applied to session '{session_id}'")
        return 0

    _error(f"Unknown profiles action: {action}. Use: list, set")
    return 1


def cmd_tools(args: argparse.Namespace) -> int:
    """Handle `nexus tools` command."""
    data = _get("/tools/")
    if args.json:
        _print_json(data)
        return 0
    print(_bold(f"\n  Available Tools\n"))
    if isinstance(data, list):
        rows = [
            {
                "name": t.get("name", ""),
                "category": t.get("category", ""),
                "description": (t.get("description", "") or "")[:50],
            }
            for t in data
        ]
        _print_table(rows, ["name", "category", "description"])
    else:
        _print_json(data)
    print()
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Handle `nexus config` — show current configuration."""
    data = _get("/system/info")
    if args.json:
        _print_json(data)
        return 0
    print(_bold(f"\n  Configuration\n"))
    print(f"  {'API URL:':<20} {_cyan(_base_url())}")
    for k, v in data.items():
        print(f"  {k + ':':<20} {v}")
    print()
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Handle `nexus version`."""
    if args.json:
        _print_json({"name": _APP_NAME, "version": _VERSION})
    else:
        print(f"{_bold(_APP_NAME)} v{_VERSION}")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexus",
        description=f"{_APP_NAME} — CLI interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              nexus chat "Hello, how are you?"
              nexus chat -i
              nexus chat --stream "Tell me a story"
              nexus status
              nexus doctor
              nexus models
              nexus skills list
              nexus skills search "python"
              nexus skills info python-developer
              nexus profiles list
              nexus profiles set coder
              nexus tools
              nexus config
              nexus version
        """),
    )
    parser.add_argument(
        "--json", action="store_true", default=False,
        help="Output raw JSON (machine-readable)",
    )
    parser.add_argument(
        "--base-url", type=str, default=None,
        help=f"API base URL (default: $NEXUS_API_URL or {_DEFAULT_BASE_URL})",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ── chat ──────────────────────────────────────────────────────
    p_chat = sub.add_parser("chat", help="Send a message or start interactive chat")
    p_chat.add_argument("message", nargs="*", help="Message text")
    p_chat.add_argument(
        "-i", "--interactive", action="store_true",
        help="Interactive chat mode (REPL)",
    )
    p_chat.add_argument("--session", type=str, default=None, help="Session/conversation ID")
    p_chat.add_argument("--stream", action="store_true", help="Stream response via SSE")
    p_chat.add_argument("--model", type=str, default=None, help="Use specific model")
    p_chat.add_argument("--profile", type=str, default=None, help="Use specific profile")
    p_chat.add_argument(
        "--free", action="store_true",
        help="Use free model via Kilo Gateway (anonymous, no API key required)",
    )

    # ── status ────────────────────────────────────────────────────
    sub.add_parser("status", help="Show system status")

    # ── doctor ────────────────────────────────────────────────────
    sub.add_parser("doctor", help="Run system diagnostics")

    # ── models ────────────────────────────────────────────────────
    sub.add_parser("models", help="List available LLM models")

    # ── skills ────────────────────────────────────────────────────
    p_skills = sub.add_parser("skills", help="Skill management")
    skills_sub = p_skills.add_subparsers(dest="skills_action")
    skills_sub.add_parser("list", help="List all skills")
    p_ss = skills_sub.add_parser("search", help="Search skills")
    p_ss.add_argument("query", nargs="*", help="Search query")
    p_si = skills_sub.add_parser("info", help="Show skill detail")
    p_si.add_argument("skill_id", help="Skill ID")

    # ── profiles ──────────────────────────────────────────────────
    p_prof = sub.add_parser("profiles", help="Profile management")
    prof_sub = p_prof.add_subparsers(dest="profiles_action")
    prof_sub.add_parser("list", help="List conversation profiles")
    p_ps = prof_sub.add_parser("set", help="Set active profile")
    p_ps.add_argument("name", help="Profile name")
    p_ps.add_argument("--session", type=str, default=None, help="Session ID")

    # ── tools ─────────────────────────────────────────────────────
    sub.add_parser("tools", help="List available tools")

    # ── config ────────────────────────────────────────────────────
    sub.add_parser("config", help="Show current configuration")

    # ── version ───────────────────────────────────────────────────
    sub.add_parser("version", help="Show version info")

    # ── security ──────────────────────────────────────────────────
    p_sec = sub.add_parser("security", help="Security operations")
    sec_sub = p_sec.add_subparsers(dest="security_action")
    p_sec_audit = sec_sub.add_parser("audit", help="Run security audit")
    p_sec_audit.add_argument("--deep", action="store_true", help="Deep audit mode")
    sec_sub.add_parser("fix", help="Auto-fix fixable findings")

    # ── policy ────────────────────────────────────────────────────
    p_pol = sub.add_parser("policy", help="Policy management")
    pol_sub = p_pol.add_subparsers(dest="policy_action")
    pol_sub.add_parser("list", help="Show all policy rules")
    p_pol_add = pol_sub.add_parser("add", help="Add a policy rule")
    p_pol_add.add_argument("tool", help="Tool name")
    p_pol_add.add_argument("action", help="Action (allow/deny/ask)")
    p_pol_add.add_argument("reason", nargs="?", default="", help="Reason for rule")
    p_pol_rm = pol_sub.add_parser("remove", help="Remove a policy rule")
    p_pol_rm.add_argument("tool", help="Tool name to remove rule for")

    # ── memory ────────────────────────────────────────────────────
    p_mem = sub.add_parser("memory", help="Active memory management")
    mem_sub = p_mem.add_subparsers(dest="memory_action")
    p_mem_show = mem_sub.add_parser("show", help="Show agent memory")
    p_mem_show.add_argument("agent_id", nargs="?", default=None, help="Agent ID")
    p_mem_search = mem_sub.add_parser("search", help="Search memories")
    p_mem_search.add_argument("query", help="Search query")
    p_mem_search.add_argument("agent_id", nargs="?", default=None, help="Agent ID")
    p_mem_stats = mem_sub.add_parser("stats", help="Memory statistics")
    p_mem_stats.add_argument("agent_id", nargs="?", default=None, help="Agent ID")
    p_mem_clear = mem_sub.add_parser("clear", help="Clear agent memory")
    p_mem_clear.add_argument("agent_id", help="Agent ID to clear")

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

# Security & memory CLI (OpenClaw-inspired)
from app.cli_security import cmd_security, cmd_policy, cmd_memory

_COMMANDS: dict[str, Any] = {
    "chat": cmd_chat,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "models": cmd_models,
    "skills": cmd_skills,
    "profiles": cmd_profiles,
    "tools": cmd_tools,
    "config": cmd_config,
    "version": cmd_version,
    "security": cmd_security,
    "policy": cmd_policy,
    "memory": cmd_memory,
}


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Override base URL if provided via --base-url flag
    if args.base_url:
        os.environ["NEXUS_API_URL"] = args.base_url

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        exit_code = handler(args)
    except httpx.ConnectError:
        _error(f"Không kết nối được tới server tại {_base_url()}")
        _info("Đảm bảo server đang chạy: uvicorn app.main:app --reload")
        _info(f"Hoặc đặt NEXUS_API_URL nếu server ở địa chỉ khác.")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        _error(f"HTTP {exc.response.status_code}")
        try:
            detail = exc.response.json()
            if isinstance(detail, dict) and "detail" in detail:
                print(f"  {detail['detail']}", file=sys.stderr)
            else:
                _print_json(detail)
        except Exception:
            print(f"  {exc.response.text[:500]}", file=sys.stderr)
        sys.exit(1)
    except httpx.TimeoutException:
        _error("Request timed out. Server có thể đang bận hoặc không phản hồi.")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n{_dim('Interrupted.')}")
        sys.exit(130)

    sys.exit(exit_code or 0)


if __name__ == "__main__":
    main()
