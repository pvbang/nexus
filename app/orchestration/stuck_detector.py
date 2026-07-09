"""Stuck-loop detection for agent execution.

Extracted from ``AgentWorker`` (audit C1: split the 1991-line god-object; C3: this
14-pattern heuristic had zero tests). The detector reads ONE thing — the recent
tool-call records — so it's a pure function: given the call history, return a
description of the stuck pattern (empty string = not stuck).

Each ``recent_tool_calls`` entry is a dict produced by the worker with keys:
``name``, ``args_hash``, ``success``, ``output_snippet``, ``output``/``result``,
``elapsed_s``. All reads are defensive (``.get`` with defaults) so a partial
record never raises.

The keyword lists here (error indicators, read/produce tool names) are the
brittle keyword-matching the audit flags as M2 — isolating them in one pure,
tested function is the prerequisite for replacing them with structured signals.
"""

from __future__ import annotations

_ERROR_INDICATORS = (
    "503 service", "502 bad gateway", "500 internal",
    "connection refused", "connection timed out",
    "no route to host", "name or service not known",
    "service temporarily unavailable", "service unavailable",
    "timeout",
    "http_code: 503", "http:503", "http_code:503",
)

_READ_TOOLS = {
    "read_file", "list_files", "search_file", "find_workspace_file",
    "get_workspace_info", "read_workspace_file", "search_files",
    "activity_search", "task_doc_read", "task_doc_list", "glob_files",
}
_PRODUCE_TOOLS = {
    "execute_code", "execute_command", "http_request", "fetch_url",
    "write_file", "edit_file", "docker_run", "docker_exec",
    "port_scan", "network_probe", "database_query",
}


def detect_stuck(recent: list[dict]) -> str:
    """Detect if an agent is stuck in a loop (14 patterns).

    Patterns 1-5: structural (tool name/args/success).
    Patterns 6-8: semantic (output content and timing).
    Patterns 9-14: advanced (cumulative error ratio, workspace loop, semantic
    repetition, analysis paralysis, semantic loop, diminishing returns).

    Returns a description of the stuck pattern, or "" if not stuck.
    """
    if len(recent) < 2:
        return ""

    # Pattern 1: Same tool + same args called 15+ times
    if (
        len(recent) >= 15
        and all(
            recent[-1]["name"] == recent[-i]["name"]
            and recent[-1]["args_hash"] == recent[-i]["args_hash"]
            for i in range(1, 16)
        )
    ):
        return (
            f"Repeated identical call: {recent[-1]['name']} "
            "with same arguments"
        )

    # Pattern 2: Same tool called 30+ times in a row (different args)
    if len(recent) >= 30:
        last_30_names = [r["name"] for r in recent[-30:]]
        if len(set(last_30_names)) == 1:
            return (
                f"Same tool '{last_30_names[0]}' called 30 times in a row "
                "— try a different tool or report blocker"
            )

    # Pattern 3: Same 1 tool for last 30 calls
    if len(recent) >= 30:
        last_30_names = set(r["name"] for r in recent[-30:])
        if len(last_30_names) <= 1:
            return (
                f"Only using '{list(last_30_names)[0]}' for last 30 "
                "calls — diversify your approach"
            )

    # Pattern 4: Alternating A→B→A→B pattern
    if len(recent) >= 4:
        last_4 = recent[-4:]
        names_4 = [r["name"] for r in last_4]
        if (
            names_4[0] == names_4[2]
            and names_4[1] == names_4[3]
            and names_4[0] != names_4[1]
        ):
            return (
                f"Alternating between '{names_4[0]}' and '{names_4[1]}' "
                "without progress — try a different approach"
            )

    # Pattern 5: Last 20 tool calls all failed
    if len(recent) >= 20:
        last_20 = recent[-20:]
        if all(not r.get("success", True) for r in last_20):
            return "Last 20 tool calls all failed — reassess strategy"

    # Pattern 6: Semantic failure loop — same error response repeated.
    # Tool "succeeds" (exit 0) but output repeatedly contains error indicators.
    if len(recent) >= 6:
        last_6 = recent[-6:]
        error_hits = 0
        for r in last_6:
            snippet = r.get("output_snippet", "")
            if any(indicator in snippet for indicator in _ERROR_INDICATORS):
                error_hits += 1
        if error_hits >= 4:
            return (
                "Target is consistently returning errors (503/unreachable/timeout) "
                "— stop retrying the same endpoint and either pivot strategy "
                "or report the blocker"
            )

    # Pattern 7: Sleep/wait abuse — per-call long waits OR cumulative wait time.
    if len(recent) >= 5:
        last_7 = recent[-min(7, len(recent)):]
        long_waits = sum(1 for r in last_7 if r.get("elapsed_s", 0) > 30)
        total_elapsed = sum(r.get("elapsed_s", 0) for r in last_7)
        if long_waits >= 3 or total_elapsed > 600:
            return (
                "Excessive waiting/sleeping detected "
                f"({long_waits} calls >30s, {int(total_elapsed)}s total "
                f"in last {len(last_7)} calls) "
                "— the target is not coming up. Report this blocker "
                "and move to an alternative approach or conclude"
            )

    # Pattern 8: Repeated HTTP polling — same URL polled with unchanged response.
    if len(recent) >= 8:
        last_8 = recent[-8:]
        snippets = [r.get("output_snippet", "") for r in last_8]
        snippet_prefixes = [s[:200] for s in snippets if s]
        if len(snippet_prefixes) >= 5:
            most_common = max(set(snippet_prefixes), key=snippet_prefixes.count)
            if snippet_prefixes.count(most_common) >= 5:
                return (
                    "Repeated identical responses from target (polling with "
                    "no change) — target state is not changing. Stop polling "
                    "and report the blocker or try a completely different approach"
                )

    # Pattern 9: Cumulative error ratio — 15+ calls, >60% errors/failures.
    if len(recent) >= 15:
        error_count = 0
        for r in recent:
            snippet = r.get("output_snippet", "")
            is_failure = not r.get("success", True)
            has_error_content = any(
                ind in snippet for ind in (
                    "503", "502", "500", "timeout", "connection refused",
                    "service unavailable", "unreachable",
                )
            )
            if is_failure or has_error_content:
                error_count += 1
        error_ratio = error_count / len(recent)
        if error_ratio > 0.6:
            return (
                f"Cumulative failure rate {error_ratio:.0%} across {len(recent)} calls "
                "— target is consistently unreachable. Stop retrying and report blocker"
            )

    # Pattern 10: Workspace path loop — mkdir → list_files → mkdir cycle.
    if len(recent) >= 6:
        last_6_names = [r.get("name", "") for r in recent[-6:]]
        dir_keywords = {"mkdir", "create_directory", "list_files", "read_file"}
        dir_hits = sum(1 for n in last_6_names if any(d in n for d in dir_keywords))
        if dir_hits >= 4:
            not_found_count = 0
            for r in recent[-6:]:
                output = (r.get("output", "") or r.get("result", "") or "").lower()
                _nf = ("not found", "empty", "không tồn tại", "chưa tồn tại")
                if any(kw in output for kw in _nf):
                    not_found_count += 1
            if not_found_count >= 3:
                return (
                    "Workspace path loop: repeatedly creating directories and checking files "
                    "but tools still report 'not found'. This is a PATH RESOLUTION mismatch "
                    "(shell CWD != tool workspace). Run 'get_workspace_info' to debug, "
                    "then use 'find_workspace_file' to locate actual files."
                )

    # Pattern 11: Semantic repetition — same error output across consecutive calls.
    if len(recent) >= 3:
        last_3_outputs = []
        for r in recent[-3:]:
            output = (r.get("output", "") or r.get("result", "") or "").lower().strip()[:200]
            last_3_outputs.append(output)
        if all(last_3_outputs) and len(set(last_3_outputs)) == 1:
            return (
                f"Semantic repetition: last 3 tool calls produced identical output. "
                f"Pattern: {last_3_outputs[0][:100]} — stop retrying and pivot strategy"
            )

    # Pattern 12: Analysis paralysis — only READS, never PRODUCES.
    if len(recent) >= 10:
        last_10 = recent[-10:]
        read_count = sum(1 for r in last_10 if r.get("name", "") in _READ_TOOLS)
        produce_count = sum(1 for r in last_10 if r.get("name", "") in _PRODUCE_TOOLS)
        if read_count >= 8 and produce_count == 0:
            return (
                "Analysis paralysis: last 10 tool calls are ALL reads/searches with "
                "ZERO productive output (no execution, no writes, no requests). "
                "You have sufficient context — STOP READING and START PRODUCING. "
                "Execute scripts, write deliverables, send requests, run tests."
            )

    # Pattern 13: Semantic loop — one tool dominates with convergent outputs.
    if len(recent) >= 10:
        last_10 = recent[-10:]
        tool_names = [r["name"] for r in last_10]
        most_common_tool = max(set(tool_names), key=tool_names.count)
        dominance = tool_names.count(most_common_tool) / len(tool_names)
        if dominance >= 0.8:
            outputs = [
                r.get("output_snippet", "")[:200]
                for r in last_10 if r["name"] == most_common_tool
            ]
            if len(outputs) >= 5:
                unique_prefixes = set(o[:100] for o in outputs)
                # Fewer than 30% unique output prefixes → convergent results
                if len(unique_prefixes) < len(outputs) * 0.3:
                    return (
                        f"Semantic loop: '{most_common_tool}' dominates {dominance:.0%} of "
                        f"last {len(last_10)} calls with convergent outputs "
                        f"({len(unique_prefixes)} unique results). "
                        "Switch to a DIFFERENT tool or conclude with current data."
                    )

    # Pattern 14: Diminishing information gain — last 5 calls produce <500 new chars.
    if len(recent) >= 8:
        last_5 = recent[-5:]
        all_prior_content = set()
        for r in recent[:-5]:
            snippet = r.get("output_snippet", "")
            if snippet:
                all_prior_content.add(snippet[:150])
        new_content_chars = 0
        for r in last_5:
            snippet = r.get("output_snippet", "")
            if snippet and snippet[:150] not in all_prior_content:
                new_content_chars += len(snippet)
                all_prior_content.add(snippet[:150])
        if new_content_chars < 500 and all(r.get("success", True) for r in last_5):
            return (
                f"Diminishing returns: last 5 calls produced only ~{new_content_chars} chars "
                "of NEW information. You are re-discovering known data. "
                "Consolidate findings and move to the next phase."
            )

    return ""
