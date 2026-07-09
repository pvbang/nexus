"""
MekongAI Nexus - Activity Search Tool.

Allows agents to search/retrieve content from ACTIVITY.md when tool outputs
were truncated in the LLM context window.

CONTEXT RECOVERY:
- Tool outputs > 20KB are truncated in LLM messages but stored in full in ACTIVITY.md
- This tool lets agents retrieve the full content when they need it
- Agents see "[OUTPUT TRUNCATED]" notes with instructions to call this tool

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import os
import re
import difflib
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))

# Maximum content returned per search (no hard limit — agents need full data)
_MAX_RESULT_CHARS = 100_000


def _get_activity_path() -> Path | None:
    """Get ACTIVITY.md path for the current task from execution context."""
    try:
        from app.core.execution_context import current_task_id
        from app.core import task_paths
        task_id = current_task_id.get(None)
        if not task_id:
            return None
        return task_paths.context_file(task_id, "ACTIVITY.md")
    except Exception:
        return None


def _extract_turn_section(content: str, turn: int) -> str:
    """Extract content belonging to a specific turn from ACTIVITY.md."""
    # Pattern: ## Turn N ... until next ## Turn, "Task Completed", or EOF.
    # Avoid relying on emoji markers (encoding/display can vary).
    pattern = re.compile(
        rf"^## Turn {turn}\b.*?(?=^## Turn \d+\b|^## .*Task Completed\b|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    if match:
        return match.group(0).strip()
    return ""


def _extract_turn_range_sections(content: str, turn_from: int, turn_to: int) -> str:
    """Extract a range of turns [turn_from, turn_to] from ACTIVITY.md.

    turn_to=0 means "to the end". If the range yields no turns, returns "".
    """
    if turn_from <= 0 and turn_to <= 0:
        return content

    headers: list[tuple[int, int]] = []
    for m in re.finditer(r"^## Turn (\d+)\b", content, flags=re.MULTILINE):
        try:
            n = int(m.group(1))
        except Exception:
            continue
        headers.append((n, m.start()))

    if not headers:
        return ""

    slices: list[tuple[int, int, int]] = []
    for idx, (n, start) in enumerate(headers):
        end = headers[idx + 1][1] if idx + 1 < len(headers) else len(content)
        slices.append((n, start, end))

    lo = turn_from if turn_from > 0 else min(n for n, _, _ in slices)
    hi = turn_to if turn_to > 0 else max(n for n, _, _ in slices)
    if hi < lo:
        lo, hi = hi, lo

    out: list[str] = []
    for n, start, end in slices:
        if lo <= n <= hi:
            out.append(content[start:end].strip())

    return "\n\n---\n\n".join(out).strip()


def _extract_section_type(content: str, section: str) -> str:
    """Filter content by section type (tool_calls, thinking, output).

    BUG FIX: The old regex `(?=^####|\\Z)` stopped at ANY line starting with
    '####', including markdown headers INSIDE tool output code blocks.
    Fix: only stop at KNOWN section headers that activity_logger.py writes.
    """
    section_lower = section.lower()

    # Known #### headers written by activity_logger.py are the ONLY valid
    # section boundaries. Anything else (e.g. #### inside a code block from
    # tool output) should NOT terminate the match.
    _SECTION_BOUNDARY = (
        r"(?=^#### (?:Thinking|Challenges|Supports|Discussion Response|Tool Calls|Output)\b"
        # Stop at known higher-level headers written by the logger.
        r"|^### (?:Agent:|Orchestrator Plan|Orchestrator Evaluation)\b"
        r"|^## (?:Turn \d+\b|.*Task Completed\b)"
        r"|\Z)"
    )

    if section_lower == "tool_calls":
        # Extract "#### Tool Calls ..." sections
        pattern = re.compile(
            rf"^#### Tool Calls.*?{_SECTION_BOUNDARY}",
            re.MULTILINE | re.DOTALL,
        )
    elif section_lower == "thinking":
        # Extract "#### Thinking" sections
        pattern = re.compile(
            rf"^#### Thinking.*?{_SECTION_BOUNDARY}",
            re.MULTILINE | re.DOTALL,
        )
    elif section_lower == "output":
        # Extract "#### Output ..." sections
        pattern = re.compile(
            rf"^#### Output.*?{_SECTION_BOUNDARY}",
            re.MULTILINE | re.DOTALL,
        )
    elif section_lower == "challenges":
        pattern = re.compile(
            rf"^#### Challenges.*?{_SECTION_BOUNDARY}",
            re.MULTILINE | re.DOTALL,
        )
    elif section_lower == "supports":
        pattern = re.compile(
            rf"^#### Supports.*?{_SECTION_BOUNDARY}",
            re.MULTILINE | re.DOTALL,
        )
    elif section_lower in ("discussion", "discussion_response"):
        pattern = re.compile(
            rf"^#### Discussion Response.*?{_SECTION_BOUNDARY}",
            re.MULTILINE | re.DOTALL,
        )
    else:
        return content  # Unknown section type, return as-is

    matches = pattern.findall(content)
    if matches:
        return "\n\n---\n\n".join(m.strip() for m in matches)
    return ""


def _iter_agent_blocks(content: str) -> list[str]:
    """Return all agent blocks (each starts with '### Agent: ...')."""
    pattern = re.compile(
        r"^### Agent: .*?(?=^### Agent: |^### Orchestrator Evaluation\b|^## Turn \d+\b|^## .*Task Completed\b|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    return [m.group(0).strip() for m in pattern.finditer(content)]


def _extract_agent_header(block: str) -> tuple[str, str]:
    """Parse '### Agent: Name (role)' into (name, role). Best-effort."""
    if not block:
        return "", ""
    first = block.splitlines()[0].strip()
    m = re.match(r"^### Agent:\s*(.*?)\s*(?:\((.*?)\))?\s*$", first)
    if not m:
        return first, ""
    name = (m.group(1) or "").strip()
    role = (m.group(2) or "").strip()
    return name, role


def _suggest_agents(query: str, blocks: list[str], limit: int = 5) -> list[dict[str, Any]]:
    """Return fuzzy suggestions for agent headers."""
    q = (query or "").lower().strip()
    if not q or not blocks:
        return []

    scored: list[tuple[float, str, str]] = []
    for b in blocks:
        name, role = _extract_agent_header(b)
        candidates = [name.lower(), role.lower(), f"{name} ({role})".lower()]
        best = 0.0
        for c in candidates:
            if not c:
                continue
            best = max(best, difflib.SequenceMatcher(None, q, c).ratio())
        if best > 0:
            scored.append((best, name, role))

    scored.sort(key=lambda t: t[0], reverse=True)
    out: list[dict[str, Any]] = []
    for score, name, role in scored[:limit]:
        out.append({"score": round(score, 3), "name": name, "role": role})
    return out


def _extract_agent_sections(
    content: str,
    agent: str,
    *,
    mode: str = "auto",
    fallback: str = "suggest",
) -> tuple[str, dict[str, Any]]:
    """Extract one or more agent blocks from ACTIVITY.md content.

    Non-failing by design:
    - If matched: return filtered content.
    - If not matched:
      - fallback="suggest": return empty content with suggestions (safer default).
      - fallback="unfiltered": return original content with suggestions.
      - fallback="empty": return empty content (no suggestions).
    """
    if not agent:
        return content, {"agent_filter_applied": False}

    agent_lower = agent.lower().strip()
    mode = (mode or "auto").lower().strip()
    fallback = (fallback or "suggest").lower().strip()

    blocks = _iter_agent_blocks(content)
    if not blocks:
        return "", {"agent_filter_applied": True, "matched": False, "suggestions": []}

    def _matches(block: str) -> bool:
        name, role = _extract_agent_header(block)
        header = block.splitlines()[0].strip().lower() if block else ""
        if mode == "exact":
            return agent_lower in {name.lower(), role.lower()}
        if mode == "contains":
            return agent_lower in header
        if mode == "fuzzy":
            return False  # handled below
        # auto
        return agent_lower in header or agent_lower in {name.lower(), role.lower()}

    matched = [b for b in blocks if _matches(b)]

    meta: dict[str, Any] = {"agent_filter_applied": True, "query": agent, "mode": mode}

    if not matched:
        suggestions = _suggest_agents(agent, blocks, limit=5)
        meta["suggestions"] = suggestions

        if mode in ("fuzzy", "auto") and suggestions:
            best = suggestions[0]
            if float(best.get("score") or 0) >= 0.82:
                target = (best.get("role") or best.get("name") or "").lower().strip()
                if target:
                    matched = [
                        b for b in blocks
                        if target in (b.splitlines()[0].strip().lower() if b else "")
                    ]
                    meta["auto_selected"] = best

    if matched:
        meta["matched"] = True
        meta["matched_count"] = len(matched)
        return "\n\n---\n\n".join(matched), meta

    meta["matched"] = False
    meta["fallback"] = fallback
    if fallback == "unfiltered":
        return content, meta
    if fallback == "empty":
        return "", meta
    return "", meta


def _list_agents(content: str) -> list[dict[str, Any]]:
    """List agents seen in the provided ACTIVITY slice (best-effort)."""
    blocks = _iter_agent_blocks(content)
    counts: dict[str, dict[str, Any]] = {}
    for b in blocks:
        name, role = _extract_agent_header(b)
        key = f"{name} ({role})" if role else name
        if key not in counts:
            counts[key] = {"name": name, "role": role, "count": 0}
        counts[key]["count"] += 1

    out = list(counts.values())
    out.sort(key=lambda x: int(x.get("count", 0)), reverse=True)
    return out


def _list_tools(content: str) -> list[dict[str, Any]]:
    """List tool names seen in tool call sections (best-effort)."""
    # Prefer extracting only tool call sections to reduce noise.
    tool_calls = _extract_section_type(content, "tool_calls")
    if not tool_calls:
        tool_calls = content

    counts: dict[str, int] = {}

    # Pattern A: markdown inline code tool names: ✅ `web_search` (1210ms)
    for m in re.findall(r"`([a-zA-Z0-9_]+)`", tool_calls):
        counts[m] = counts.get(m, 0) + 1

    # Pattern B: partial data synthesis: "- web_search: ..."
    for m in re.findall(r"(?m)^\s*-\s*([a-zA-Z0-9_]+)\s*:", tool_calls):
        counts[m] = counts.get(m, 0) + 1

    out = [{"tool": k, "count": v} for k, v in counts.items()]
    out.sort(key=lambda x: int(x.get("count", 0)), reverse=True)
    return out


def _search_keyword_with_context(
    content: str,
    keyword: str,
    context_chars: int = 2000,
    *,
    max_matches: int = 10,
) -> str:
    """Find all occurrences of keyword and return surrounding context."""
    if not keyword:
        return content

    results: list[str] = []
    keyword_lower = keyword.lower()
    content_lower = content.lower()

    start_pos = 0
    while True:
        idx = content_lower.find(keyword_lower, start_pos)
        if idx == -1:
            break

        # Extract context window around the match
        ctx_start = max(0, idx - context_chars)
        ctx_end = min(len(content), idx + len(keyword) + context_chars)

        snippet = content[ctx_start:ctx_end]

        # Add position indicator
        prefix = "..." if ctx_start > 0 else ""
        suffix = "..." if ctx_end < len(content) else ""
        results.append(f"{prefix}{snippet}{suffix}")

        start_pos = idx + len(keyword)

        # Cap matches to prevent excessive output
        if len(results) >= max_matches:
            break

    if results:
        return f"Found {len(results)} match(es) for '{keyword}':\n\n" + "\n\n---\n\n".join(results)
    return ""


def _get_ledger():
    """Get ContextLedger instance for the current task."""
    try:
        from app.core.execution_context import current_task_id
        from app.orchestration.context_ledger import ContextLedger
        task_id = current_task_id.get(None)
        if not task_id:
            return None
        return ContextLedger(task_id)
    except Exception:
        return None


def _query_ledger(params: dict[str, Any], list_progress: bool = False) -> str:
    """Query the structured Context Ledger directly.

    Fast path for agents who want structured progress info without
    parsing the full ACTIVITY.md file.
    """
    ledger = _get_ledger()
    if not ledger:
        return tool_error("No active task context or ledger not available.")

    turn = int(params.get("turn", 0) or 0)
    turn_from = int(params.get("turn_from", 0) or 0)
    turn_to = int(params.get("turn_to", 0) or 0)
    agent = params.get("agent", "").strip()
    keyword = params.get("keyword", "").strip()

    # list_progress: show accumulated key findings + per-turn summaries
    if list_progress:
        key_data = ledger.query_key_data(since_turn=turn_from, limit=30)
        entries = ledger.query(
            since_turn=turn_from or 1,
            until_turn=turn_to or 999999,
            role=agent,
            limit=50,
        )

        lines: list[str] = []
        lines.append("## Task Progress (from Ledger)\n")

        if key_data:
            lines.append("### Key Findings Accumulated")
            for kd in key_data:
                lines.append(f"- {kd}")
            lines.append("")

        if entries:
            lines.append("### Turn-by-Turn Results")
            current_group = -1
            for e in sorted(entries, key=lambda x: x.turn):
                if e.turn != current_group:
                    current_group = e.turn
                    lines.append(f"\n**Turn {e.turn}:**")
                status = "OK" if e.success else "FAIL"
                line = f"  [{status}] {e.agent_role}: {e.did_what}"
                if e.key_data:
                    line += f" | {'; '.join(e.key_data[:3])}"
                if e.blockers:
                    line += f" | BLOCKED: {e.blockers}"
                lines.append(line)

        # Include epochs
        epochs = ledger._read_epochs()
        if epochs:
            lines.append("\n### Epoch Summaries (compressed history)")
            for ep in epochs:
                lines.append(f"- Turns {ep.turns_range}: {ep.summary[:200]}")

        output = "\n".join(lines) if lines else "(Ledger is empty)"
        return tool_result(success=True, output=output, data={"source": "ledger", "entries": len(entries)})

    # Specific turn query
    if turn > 0:
        entries = ledger.query(since_turn=turn, until_turn=turn, role=agent, limit=20)
    elif turn_from or turn_to:
        entries = ledger.query(since_turn=turn_from or 1, until_turn=turn_to or 999999, role=agent, limit=50)
    elif agent:
        entries = ledger.query(role=agent, limit=20)
    else:
        entries = ledger.query(limit=30)

    # Filter by keyword if provided
    if keyword and entries:
        kw_lower = keyword.lower()
        entries = [
            e for e in entries
            if kw_lower in e.did_what.lower()
            or any(kw_lower in kd.lower() for kd in e.key_data)
            or kw_lower in e.agent_role.lower()
            or kw_lower in e.agent_name.lower()
        ]

    if not entries:
        return tool_result(success=True, output="No matching entries in ledger.", data={"source": "ledger", "found": False})

    lines = [f"## Ledger Results ({len(entries)} entries)\n"]
    for e in entries:
        status = "OK" if e.success else "FAIL"
        line = f"- T{e.turn} [{status}] **{e.agent_role}**: {e.did_what}"
        if e.key_data:
            line += f"\n  Data: {'; '.join(e.key_data[:5])}"
        if e.tools_used:
            line += f"\n  Tools: {', '.join(e.tools_used[:5])}"
        if e.blockers:
            line += f"\n  Blocker: {e.blockers}"
        lines.append(line)

    return tool_result(success=True, output="\n".join(lines), data={"source": "ledger", "entries": len(entries)})


def _query_ledger_keyword(keyword: str, turn: int = 0, turn_from: int = 0, turn_to: int = 0) -> str:
    """Search ledger for keyword matches. Returns formatted string or empty."""
    ledger = _get_ledger()
    if not ledger:
        return ""

    entries = ledger.query(
        since_turn=turn or turn_from or 1,
        until_turn=turn or turn_to or 999999,
        limit=100,
    )

    if not entries:
        return ""

    kw_lower = keyword.lower()
    matches = [
        e for e in entries
        if kw_lower in e.did_what.lower()
        or any(kw_lower in kd.lower() for kd in e.key_data)
        or kw_lower in e.blockers.lower()
    ]

    if not matches:
        return ""

    lines: list[str] = []
    for e in matches[:10]:
        status = "OK" if e.success else "FAIL"
        line = f"- T{e.turn} [{status}] {e.agent_role}: {e.did_what}"
        if e.key_data:
            line += f" | {'; '.join(e.key_data[:3])}"
        lines.append(line)

    return "\n".join(lines)


async def _activity_search(params: dict[str, Any], **kwargs: Any) -> str:
    """Search ACTIVITY.md and/or LEDGER for specific content.

    Useful when tool outputs were truncated in context and agent needs full data.
    Also provides quick access to structured progress via the Context Ledger.
    """
    keyword = params.get("keyword", "").strip()
    turn = params.get("turn", 0)
    turn_from = int(params.get("turn_from", 0) or 0)
    turn_to = int(params.get("turn_to", 0) or 0)
    section = params.get("section", "").strip()
    agent = params.get("agent", "").strip()
    agent_mode = params.get("agent_mode", "auto").strip()
    agent_fallback = params.get("agent_fallback", "suggest")
    tool = params.get("tool", "").strip()
    max_matches = int(params.get("max_matches", 10) or 10)
    list_agents = bool(params.get("list_agents", False))
    list_tools = bool(params.get("list_tools", False))
    source = params.get("source", "auto").strip().lower()
    list_progress = bool(params.get("list_progress", False))

    # --- Ledger-first paths (fast structured queries) ---
    if source == "ledger" or list_progress:
        return _query_ledger(params, list_progress=list_progress)

    # If source is "ledger", handled above. "activity" or "auto" continues below.

    activity_path = _get_activity_path()
    if activity_path is None:
        return tool_error(
            "No active task context. activity_search is only available during task execution."
        )

    if not activity_path.exists():
        return tool_error(
            f"ACTIVITY.md not found for current task. No activity has been logged yet."
        )

    try:
        # Read current ACTIVITY.md + any rotated epoch files (ACTIVITY_epoch1.md, etc.)
        # Epochs contain older turns that were rotated out of the main file.
        content = ""
        context_dir = activity_path.parent
        epoch_files = sorted(context_dir.glob("ACTIVITY_epoch*.md"))
        for epoch_file in epoch_files:
            content += epoch_file.read_text(encoding="utf-8", errors="replace")
        content += activity_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return tool_error(f"Failed to read ACTIVITY.md: {e}")

    if not content.strip():
        return tool_result(
            success=True,
            output="ACTIVITY.md is empty — no activity logged yet.",
        )

    # Step 1: Filter by turn if specified
    if turn > 0:
        content = _extract_turn_section(content, turn)
        if not content:
            return tool_result(
                success=True,
                output=f"No content found for Turn {turn} in ACTIVITY.md.",
                data={"turn": turn, "found": False},
            )
    elif turn_from > 0 or turn_to > 0:
        ranged = _extract_turn_range_sections(content, turn_from, turn_to)
        if not ranged:
            return tool_result(
                success=True,
                output=f"No content found for Turn range {turn_from}..{turn_to or 'end'} in ACTIVITY.md.",
                data={"turn_from": turn_from, "turn_to": turn_to, "found": False},
            )
        content = ranged

    # Step 2: Filter by agent block if specified
    if agent:
        filtered, agent_meta = _extract_agent_sections(
            content,
            agent,
            mode=agent_mode,
            fallback=str(agent_fallback),
        )
        # If agent didn't match and fallback is "suggest"/"empty", but the caller
        # provided other filters (keyword/tool/section), continue searching the
        # unfiltered content. This avoids a "hard miss" while preventing
        # accidental full-slice dumps.
        if agent_meta.get("matched") is False and (keyword or tool or section or list_agents or list_tools):
            content = content
        else:
            content = filtered
    else:
        agent_meta = {"agent_filter_applied": False}

    # If caller requested an agent filter but it didn't match, and they didn't
    # provide any other filters, return a focused suggestion payload.
    if agent and agent_meta.get("agent_filter_applied") and agent_meta.get("matched") is False:
        if (
            str(agent_fallback).lower() != "unfiltered"
            and not keyword
            and not tool
            and not section
            and not list_agents
            and not list_tools
        ):
            sugg = agent_meta.get("suggestions") or []
            lines = [f"[activity_search] agent='{agent}' did not match any Agent blocks."]
            if sugg:
                lines.append("Suggestions (call again with agent=...):")
                for s in sugg[:5]:
                    name = s.get("name", "")
                    role = s.get("role", "")
                    score = s.get("score", "")
                    label = f"{name} ({role})" if role else name
                    lines.append(f"- {label} score={score}")
            lines.append("Tip: call activity_search(list_agents=true, turn_from=..., turn_to=...) to see exact names.")
            return tool_result(
                success=True,
                output="\n".join(lines),
                data={"agent": agent, "found": False, "agent_meta": agent_meta},
            )

    # Optional: indexing helpers (return early)
    if list_agents:
        agents = _list_agents(content)
        lines = [f"Agents found: {len(agents)}"]
        for a in agents[:50]:
            name = a.get("name", "")
            role = a.get("role", "")
            cnt = a.get("count", 0)
            label = f"{name} ({role})" if role else name
            lines.append(f"- {label}: {cnt}")
        return tool_result(
            success=True,
            output="\n".join(lines),
            data={
                "turn": turn or None,
                "turn_from": turn_from or None,
                "turn_to": turn_to or None,
                "agents": agents,
            },
        )

    if list_tools:
        tools = _list_tools(content)
        lines = [f"Tools found: {len(tools)}"]
        for t in tools[:80]:
            lines.append(f"- {t.get('tool')}: {t.get('count')}")
        return tool_result(
            success=True,
            output="\n".join(lines),
            data={
                "turn": turn or None,
                "turn_from": turn_from or None,
                "turn_to": turn_to or None,
                "tools": tools,
            },
        )

    # Step 3: Filter by section type if specified
    if section:
        content = _extract_section_type(content, section)
        if not content:
            return tool_result(
                success=True,
                output=f"No '{section}' sections found in the filtered content.",
                data={"section": section, "found": False},
            )

    # Step 4: Filter by tool name (best-effort; defaults to searching tool_calls section)
    if tool:
        if not section:
            content = _extract_section_type(content, "tool_calls")
        content = _search_keyword_with_context(content, tool, max_matches=max_matches)
        if not content:
            return tool_result(
                success=True,
                output=f"No matches found for tool '{tool}'.",
                data={"tool": tool, "found": False, "agent_meta": agent_meta},
            )

    # Step 5: Search by keyword if specified
    if keyword:
        content = _search_keyword_with_context(content, keyword, max_matches=max_matches)
        if not content:
            # Auto-fallback to ledger search when ACTIVITY has no matches
            if source == "auto":
                ledger_result = _query_ledger_keyword(keyword, turn=turn, turn_from=turn_from, turn_to=turn_to)
                if ledger_result:
                    return tool_result(
                        success=True,
                        output=f"No matches in ACTIVITY.md, but found in LEDGER:\n\n{ledger_result}",
                        data={"keyword": keyword, "found": True, "source": "ledger", "agent_meta": agent_meta},
                    )
            return tool_result(
                success=True,
                output=f"No matches found for keyword '{keyword}'.",
                data={"keyword": keyword, "found": False, "agent_meta": agent_meta},
            )

    # Cap result size (generous — agents need full data for recovery)
    if len(content) > _MAX_RESULT_CHARS:
        content = content[:_MAX_RESULT_CHARS] + f"\n\n[... {len(content) - _MAX_RESULT_CHARS} more chars available]"

    # Human-readable note: agent filter is best-effort and never hard-fails.
    note_lines: list[str] = []
    if agent_meta.get("agent_filter_applied"):
        if not agent_meta.get("matched", True):
            sugg = agent_meta.get("suggestions") or []
            if sugg:
                formatted = ", ".join(
                    f"{s.get('name','')} ({s.get('role','')}) score={s.get('score','')}"
                    for s in sugg[:5]
                )
                note_lines.append(f"[activity_search] agent='{agent}' did not match exactly. Suggestions: {formatted}")
            else:
                note_lines.append(f"[activity_search] agent='{agent}' did not match exactly. Returning unfiltered results.")
        elif agent_meta.get("auto_selected"):
            s = agent_meta["auto_selected"]
            note_lines.append(
                f"[activity_search] agent='{agent}' auto-selected: {s.get('name','')} ({s.get('role','')}) score={s.get('score','')}"
            )

    if note_lines:
        content = "\n".join(note_lines) + "\n\n" + content

    return tool_result(
        success=True,
        output=content,
        data={
            "chars_returned": len(content),
            "keyword": keyword or None,
            "turn": turn or None,
            "turn_from": turn_from or None,
            "turn_to": turn_to or None,
            "section": section or None,
            "agent": agent or None,
            "agent_mode": agent_mode or None,
            "agent_fallback": str(agent_fallback) if agent else None,
            "agent_meta": agent_meta,
            "tool": tool or None,
            "max_matches": max_matches,
        },
    )


# ── Self-Registration ────────────────────────────────────────────────────

registry.register(
    name="activity_search",
    toolset="task_docs",
    schema={
        "description": (
            "Search ACTIVITY.md and Context Ledger for task history. Use this when you see "
            "'[OUTPUT TRUNCATED]' in a tool result, or when you need to recall what was "
            "accomplished in previous turns. "
            "Quick usage: activity_search(list_progress=true) for structured overview. "
            "Detailed: search by keyword/tool, turn (single or range), agent (best-effort), "
            "or section type (tool_calls, thinking, output, discussion, supports, challenges). "
            "source='ledger' for fast structured queries, 'activity' for full-text search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": (
                        "Text to search for. Can be a tool name, "
                        "URL, specific term, or any text you saw partially in "
                        "truncated output. Searches both ACTIVITY.md and Ledger."
                    ),
                },
                "turn": {
                    "type": "integer",
                    "description": (
                        "Turn number to filter (e.g., 1, 2, 3). "
                        "If specified, only searches within that turn's content. "
                        "0 or omitted = search all turns."
                    ),
                    "default": 0,
                },
                "turn_from": {
                    "type": "integer",
                    "description": "Turn range start (inclusive). Used when 'turn' is 0.",
                    "default": 0,
                },
                "turn_to": {
                    "type": "integer",
                    "description": "Turn range end (inclusive). 0 = to the end. Used when 'turn' is 0.",
                    "default": 0,
                },
                "section": {
                    "type": "string",
                    "enum": ["tool_calls", "thinking", "output", "challenges", "supports", "discussion", ""],
                    "description": (
                        "Section type filter. 'tool_calls' = find tool inputs/outputs. "
                        "'thinking' = find agent reasoning. 'output' = find agent final outputs. "
                        "'discussion' = discussion response content. "
                        "'challenges'/'supports' = peer feedback. "
                        "Empty or omitted = search all sections."
                    ),
                    "default": "",
                },
                "agent": {
                    "type": "string",
                    "description": (
                        "Optional agent filter. For ledger: matches agent_role exactly. "
                        "For activity: case-insensitive substring match against agent header."
                    ),
                    "default": "",
                },
                "agent_mode": {
                    "type": "string",
                    "enum": ["auto", "exact", "contains", "fuzzy"],
                    "description": "How to match agent filter (default auto).",
                    "default": "auto",
                },
                "agent_fallback": {
                    "type": "string",
                    "enum": ["suggest", "unfiltered", "empty"],
                    "description": (
                        "What to return when agent filter doesn't match any blocks. "
                        "'suggest' (default) returns only suggestions (safer). "
                        "'unfiltered' returns the unfiltered slice. "
                        "'empty' returns empty content."
                    ),
                    "default": "suggest",
                },
                "tool": {
                    "type": "string",
                    "description": "Optional tool name filter (best-effort). If set and section is empty, tool_calls is used.",
                    "default": "",
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Max number of matches to return (default 10).",
                    "default": 10,
                },
                "list_agents": {
                    "type": "boolean",
                    "description": "If true, list agent names/roles found in the filtered slice (returns early).",
                    "default": False,
                },
                "list_tools": {
                    "type": "boolean",
                    "description": "If true, list tool names found in tool call sections (returns early).",
                    "default": False,
                },
                "source": {
                    "type": "string",
                    "enum": ["auto", "activity", "ledger"],
                    "description": (
                        "'ledger' = fast structured query (key findings, per-turn summaries). "
                        "'activity' = full-text search in ACTIVITY.md (detailed tool outputs). "
                        "'auto' (default) = try activity first, fallback to ledger if no matches."
                    ),
                    "default": "auto",
                },
                "list_progress": {
                    "type": "boolean",
                    "description": (
                        "If true, return a structured overview of task progress from the Ledger: "
                        "key findings accumulated, per-turn results, and epoch summaries. "
                        "This is the FASTEST way to understand what has been accomplished. "
                        "Combine with turn_from/turn_to/agent to filter."
                    ),
                    "default": False,
                },
            },
        },
    },
    handler=_activity_search,
    emoji="🔍",
    category="task_docs",
)
