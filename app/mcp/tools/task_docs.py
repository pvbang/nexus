"""
MekongAI Nexus - Task Document Tools.

Expose per-task working memory operations as agent tools:
- task_doc_read: Read context document or section
- task_doc_write: Write/append/replace content in context doc
- task_doc_edit: Surgical search-replace edit (Roo-code apply_diff pattern)
- task_doc_search: Full-text search across all task context docs
- task_doc_delete: Delete a context document
- task_doc_list: List all context documents

**Task Isolation:**
All operations are scoped to the current task_id (from execution context).
Two concurrent tasks CANNOT access each other's documents.

**Flexible naming:**
Agents can use predefined docs (TASK_BRIEF, FINDINGS, DECISIONS,
SCRATCHPAD) OR create custom docs with any name (e.g. analysis_results,
agent_notes). All files are stored in the task's _context/ directory.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import re as _re
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)


def _get_twm() -> Any:
    """Get TaskWorkingMemory for current task from execution context.

    Returns None if no task context is available (e.g., during simple chat).
    """
    from app.memory.task_working_memory import get_task_working_memory
    return get_task_working_memory()


# ── Smart Section Update ─────────────────────────────────────────────────


def _smart_section_update(existing_content: str, new_section: str) -> tuple[str, str | None]:
    """Replace stale section if same header exists, otherwise append.

    Args:
        existing_content: Current document content.
        new_section: New section content (must start with ## or ### header).

    Returns:
        Tuple of (updated_content, replaced_header_or_None).
    """
    # Extract header from new section
    new_lines = new_section.strip().split("\n")
    if not new_lines:
        return existing_content + new_section, None

    first_line = new_lines[0].strip()
    # Check if it's a markdown header
    header_match = _re.match(r"^(#{2,3})\s+(.+)$", first_line)
    if not header_match:
        # Not a section header — just append
        return existing_content + new_section, None

    header_level = len(header_match.group(1))
    header_title = header_match.group(2).strip()

    # Find matching section in existing content
    pattern = _re.compile(
        r"^(#{2," + str(header_level) + r"})\s+" + _re.escape(header_title) + r"\s*$",
        _re.MULTILINE,
    )
    match = pattern.search(existing_content)
    if not match:
        # No existing section with same name — append
        return existing_content + new_section, None

    # Found matching section — find its end
    section_start = match.start()
    rest_content = existing_content[match.end():]
    next_header_pattern = _re.compile(
        r"^#{2," + str(header_level) + r"}\s+",
        _re.MULTILINE,
    )
    next_match = next_header_pattern.search(rest_content)
    if next_match:
        section_end = match.end() + next_match.start()
    else:
        section_end = len(existing_content)

    # Replace old section with new section
    updated = existing_content[:section_start] + new_section.strip() + "\n\n" + existing_content[section_end:]
    return updated, header_title


# ── Tool Handlers ────────────────────────────────────────────────────────


async def _task_doc_read(params: dict[str, Any], **kwargs: Any) -> str:
    """Read a task context document with smart navigation.

    Modes:
    - Default (no offset): If doc is small (<8000), returns full content.
      If large, returns TOC + first chunk with navigation hints.
    - Chunked (offset+limit): Returns positioned chunk with structure context.
    - Section (section param): Extracts specific markdown section.

    Cross-task access:
    - If task_id param is provided, reads from THAT task's context instead.
      This allows agents to reference findings from previous tasks in the session.

    The agent always knows:
    - Total document size
    - Document structure (section headers)
    - Current reading position
    - How to continue reading
    """
    # CONTEXT-CONTINUITY: Allow reading from a different task (previous in session)
    target_task_id = params.get("task_id")
    if target_task_id:
        from app.memory.task_working_memory import TaskWorkingMemory
        twm = TaskWorkingMemory(target_task_id)
    else:
        twm = _get_twm()
    if twm is None:
        return tool_error(
            "No active task context. Task documents are only available during task execution."
        )

    doc = params.get("doc", "TASK_BRIEF").replace(".md", "").replace(".MD", "")
    section = params.get("section")  # Optional section header
    offset = params.get("offset")  # Optional char offset for chunked reading
    limit = params.get("limit")  # Optional char limit for chunked reading

    try:
        # Get document size first
        doc_size = await twm.get_doc_size(doc)

        # ── Chunked reading mode (for large documents) ──
        if offset is not None or limit is not None:
            _offset = int(offset) if offset is not None else 0
            _limit = int(limit) if limit is not None else 8000

            chunk, total_chars, has_more = await twm.read_doc_chunked(
                doc, offset=_offset, limit=_limit,
            )

            if not chunk:
                return tool_result(
                    success=True,
                    output=f"📄 **{doc}.md** is empty or offset beyond end (total: {total_chars} chars).",
                    data={"doc": doc, "content": "", "total_chars": total_chars, "has_more": False, "task_id": twm.task_id_short},
                )

            next_offset = _offset + _limit

            # Build structure context — show TOC so agent knows WHERE it is
            toc_context = ""
            try:
                toc = await twm.read_doc_toc(doc)
                if toc:
                    # Find which section the current offset falls into
                    current_section = "beginning"
                    for h in toc:
                        if h["char_offset"] <= _offset:
                            current_section = h["title"]
                        else:
                            break

                    toc_lines = []
                    for h in toc[:20]:  # Max 20 headers in TOC
                        marker = "→" if h["char_offset"] <= _offset < (h["char_offset"] + _limit * 2) else " "
                        toc_lines.append(f"  {marker} {'#' * h['level']} {h['title']} (offset: {h['char_offset']})")

                    toc_context = (
                        f"\n📋 **Document Structure** ({len(toc)} sections):\n"
                        + "\n".join(toc_lines)
                        + f"\n\n📍 **Currently reading near**: \"{current_section}\"\n"
                    )
            except Exception:
                pass

            nav_hint = ""
            if has_more:
                remaining = total_chars - next_offset
                nav_hint = (
                    f"\n\n---\n📖 **{remaining:,} chars remaining** ({remaining * 100 // total_chars}% left). "
                    f"Continue: `task_doc_read(doc=\"{doc}\", offset={next_offset}, limit={_limit})`"
                )
            else:
                nav_hint = "\n\n---\n✅ **End of document reached.**"

            return tool_result(
                success=True,
                output=(
                    f"📄 **{doc}.md** — Reading chars {_offset:,}–{_offset + len(chunk):,} of {total_chars:,} "
                    f"({(_offset * 100 // total_chars) if total_chars > 0 else 0}%–"
                    f"{((_offset + len(chunk)) * 100 // total_chars) if total_chars > 0 else 100}%)"
                    f"{toc_context}\n\n"
                    f"{chunk}{nav_hint}"
                ),
                data={
                    "doc": doc,
                    "content": chunk,
                    "offset": _offset,
                    "limit": _limit,
                    "total_chars": total_chars,
                    "has_more": has_more,
                    "next_offset": next_offset if has_more else None,
                    "reading_progress_pct": round((_offset + len(chunk)) * 100 / total_chars) if total_chars > 0 else 100,
                    "task_id": twm.task_id_short,
                },
            )

        # ── Section reading mode ──
        if section:
            content = await twm.read_doc(doc, section=section)
            if not content:
                return tool_result(
                    success=True,
                    output=f"📄 **{doc}.md** — section '{section}' not found.",
                    data={"doc": doc, "content": "", "task_id": twm.task_id_short},
                )
            return tool_result(
                success=True,
                output=f"📄 **{doc}.md** § \"{section}\" ({len(content)} chars):\n\n{content}",
                data={
                    "doc": doc,
                    "content": content,
                    "section": section,
                    "chars": len(content),
                    "task_id": twm.task_id_short,
                },
            )

        # ── Smart default read mode ──
        # Small docs: return full content
        # Large docs: return TOC + first chunk with navigation
        SMART_THRESHOLD = 8000  # ~2000 tokens — safe for context

        if doc_size <= SMART_THRESHOLD:
            # Small doc — return full content
            content = await twm.read_doc(doc)
            if not content:
                return tool_result(
                    success=True,
                    output=f"📄 **{doc}.md** is empty.",
                    data={"doc": doc, "content": "", "task_id": twm.task_id_short},
                )
            return tool_result(
                success=True,
                output=f"📄 **{doc}.md** ({len(content)} chars — full document):\n\n{content}",
                data={
                    "doc": doc,
                    "content": content,
                    "chars": len(content),
                    "is_complete": True,
                    "task_id": twm.task_id_short,
                },
            )
        else:
            # Large doc — return TOC + first chunk with navigation instructions
            toc = await twm.read_doc_toc(doc)
            toc_text = ""
            if toc:
                toc_lines = [
                    f"  {'#' * h['level']} {h['title']} (offset: {h['char_offset']})"
                    for h in toc[:30]
                ]
                toc_text = "\n".join(toc_lines)
            else:
                toc_text = "(no markdown headers found)"

            # Read first chunk
            first_chunk, total_chars, has_more = await twm.read_doc_chunked(doc, offset=0, limit=SMART_THRESHOLD)

            nav_instructions = (
                f"\n\n---\n"
                f"📖 **Document is large ({total_chars:,} chars)**. Showing first {SMART_THRESHOLD:,} chars.\n"
                f"To continue reading:\n"
                f"- Next chunk: `task_doc_read(doc=\"{doc}\", offset={SMART_THRESHOLD}, limit=8000)`\n"
                f"- Read specific section: `task_doc_read(doc=\"{doc}\", section=\"Section Title\")`\n"
                f"- Jump to offset: `task_doc_read(doc=\"{doc}\", offset=<char_offset_from_toc>)`"
            )

            return tool_result(
                success=True,
                output=(
                    f"📄 **{doc}.md** ({total_chars:,} chars total — showing first {len(first_chunk):,})\n\n"
                    f"📋 **Table of Contents** ({len(toc) if toc else 0} sections):\n{toc_text}\n\n"
                    f"---\n\n"
                    f"{first_chunk}"
                    f"{nav_instructions}"
                ),
                data={
                    "doc": doc,
                    "content": first_chunk,
                    "total_chars": total_chars,
                    "has_more": has_more,
                    "is_complete": False,
                    "toc": [{"level": h["level"], "title": h["title"], "offset": h["char_offset"]} for h in (toc or [])],
                    "next_offset": SMART_THRESHOLD if has_more else None,
                    "task_id": twm.task_id_short,
                },
            )

    except Exception as e:
        logger.warning("task_doc_read_error", doc=doc, error=str(e))
        return tool_error(f"Failed to read {doc}: {e}")


async def _task_doc_write(params: dict[str, Any], **kwargs: Any) -> str:
    """Write content to a task context document."""
    twm = _get_twm()
    if twm is None:
        return tool_error(
            "No active task context. Task documents are only available during task execution."
        )

    doc = params.get("doc", "SCRATCHPAD").replace(".md", "").replace(".MD", "")
    content = params.get("content", "").strip()
    mode = params.get("mode", "append").lower()

    # Pipeline-managed docs — restrict direct writes/edits/deletes
    RESTRICTED_DOCS = {"PROGRESS", "TASK_BRIEF", "ROE", "TODO", "ACTIVITY", "FINAL_REPORT", "FINDINGS", "DECISIONS"}
    if doc.upper() in RESTRICTED_DOCS:
        return tool_error(
            "This document is managed by the pipeline and cannot be modified by agents directly."
        )

    if not content:
        return tool_error("Content is required.")

    if mode not in ("append", "replace", "prepend"):
        return tool_error(f"Invalid mode '{mode}'. Use: append, replace, or prepend.")

    # Smart section update: when appending content with section headers,
    # replace stale sections instead of duplicating them
    if mode == "append":
        first_line = content.strip().split("\n")[0].strip() if content.strip() else ""
        if _re.match(r"^#{2,3}\s+", first_line):
            try:
                existing = await twm.read_doc(doc)
                if existing:
                    updated, replaced_header = _smart_section_update(existing, content)
                    if replaced_header:
                        logger.info(
                            "smart_section_replaced",
                            doc=doc,
                            header=replaced_header,
                        )
                        # Use replace mode with the fully updated content
                        await twm.write_doc(doc, updated, mode="replace")

                        sizes = await twm.get_file_sizes()
                        doc_size = sizes.get(doc, 0)

                        return tool_result(
                            success=True,
                            output=(
                                f"✅ Written to **{doc}.md** (mode: append→smart_replace, "
                                f"replaced stale section '{replaced_header}', "
                                f"doc size: {doc_size:,} bytes).\n\n"
                                f"Task: `{twm.task_id_short}`"
                            ),
                            data={
                                "doc": doc,
                                "mode": "smart_replace",
                                "replaced_section": replaced_header,
                                "size": doc_size,
                                "task_id": twm.task_id_short,
                            },
                        )
            except Exception:
                pass  # Fall through to normal append

    try:
        await twm.write_doc(doc, content, mode=mode)

        sizes = await twm.get_file_sizes()
        doc_size = sizes.get(doc, 0)

        return tool_result(
            success=True,
            output=(
                f"✅ Written to **{doc}.md** (mode: {mode}, "
                f"doc size: {doc_size:,} bytes).\n\n"
                f"Task: `{twm.task_id_short}`"
            ),
            data={
                "doc": doc,
                "mode": mode,
                "size": doc_size,
                "task_id": twm.task_id_short,
            },
        )
    except Exception as e:
        logger.warning("task_doc_write_error", doc=doc, error=str(e))
        return tool_error(f"Failed to write {doc}: {e}")


async def _task_doc_edit(params: dict[str, Any], **kwargs: Any) -> str:
    """Surgical search-replace edit on a task context document.

    Like Roo-code's apply_diff: finds exact text and replaces it.
    Falls back to whitespace-fuzzy matching if exact match fails.
    """
    twm = _get_twm()
    if twm is None:
        return tool_error(
            "No active task context. Task documents are only available during task execution."
        )

    doc = params.get("doc", "").replace(".md", "").replace(".MD", "")
    search = params.get("search", "")
    replace = params.get("replace", "")

    if not doc:
        return tool_error("Document name is required (doc parameter).")
    if not search:
        return tool_error("Search text is required.")

    # Pipeline-managed docs — restrict direct edits
    RESTRICTED_DOCS = {"PROGRESS", "TASK_BRIEF", "ROE", "TODO", "ACTIVITY", "FINAL_REPORT", "FINDINGS", "DECISIONS"}
    if doc.upper() in RESTRICTED_DOCS:
        return tool_error(
            f"{doc}.md is managed by the Nexus Orchestrator pipeline and cannot be edited directly. "
            "Pipeline-managed files: TASK_BRIEF, ROE, TODO, ACTIVITY, FINAL_REPORT, PROGRESS."
        )

    try:
        result = await twm.edit_doc(doc, search, replace)

        if result.success:
            return tool_result(
                success=True,
                output=(
                    f"✅ **{doc}.md** edited successfully.\n"
                    f"- {result.message}\n"
                    f"- Lines affected: {result.line_range[0]}-{result.line_range[1]}\n\n"
                    f"Task: `{twm.task_id_short}`"
                ),
                data={
                    "doc": doc,
                    "success": True,
                    "line_range": list(result.line_range),
                    "task_id": twm.task_id_short,
                },
            )
        else:
            return tool_result(
                success=False,
                output=f"❌ Edit failed: {result.message}",
                data={"doc": doc, "success": False},
            )
    except Exception as e:
        logger.warning("task_doc_edit_error", doc=doc, error=str(e))
        return tool_error(f"Failed to edit {doc}: {e}")


async def _task_doc_search(params: dict[str, Any], **kwargs: Any) -> str:
    """Search across all task context documents."""
    twm = _get_twm()
    if twm is None:
        return tool_error(
            "No active task context. Task documents are only available during task execution."
        )

    query = params.get("query", "").strip()
    if not query:
        return tool_error("Search query is required.")

    # Optional: limit to specific docs
    docs = params.get("docs")  # e.g., ["FINDINGS", "DECISIONS", "my_notes"]
    if docs and isinstance(docs, list):
        docs = [d.replace(".md", "").replace(".MD", "") for d in docs]

    try:
        hits = await twm.search_docs(query, doc_names=docs)

        if not hits:
            return tool_result(
                success=True,
                output=f"🔍 No matches for '{query}' in task context docs.",
                data={"query": query, "hits": [], "task_id": twm.task_id_short},
            )

        lines = [f"🔍 **Search results for '{query}'** ({len(hits)} matches):\n"]
        for hit in hits:
            lines.append(
                f"**{hit.doc_name}.md** line {hit.line_num}: "
                f"`{hit.line_content[:120]}`"
            )

        return tool_result(
            success=True,
            output="\n".join(lines),
            data={
                "query": query,
                "hits": [
                    {
                        "doc": h.doc_name,
                        "line": h.line_num,
                        "content": h.line_content,
                        "context": h.context,
                    }
                    for h in hits
                ],
                "task_id": twm.task_id_short,
            },
        )
    except Exception as e:
        logger.warning("task_doc_search_error", query=query, error=str(e))
        return tool_error(f"Search failed: {e}")


async def _task_doc_list(params: dict[str, Any], **kwargs: Any) -> str:
    """List all task context documents with metadata."""
    # CONTEXT-CONTINUITY: Support listing files from a different task
    target_task_id = params.get("task_id")
    if target_task_id:
        from app.memory.task_working_memory import TaskWorkingMemory
        twm = TaskWorkingMemory(target_task_id)
    else:
        twm = _get_twm()
    if twm is None:
        return tool_error(
            "No active task context. Task documents are only available during task execution."
        )

    try:
        docs = await twm.list_docs()

        if not docs:
            return tool_result(
                success=True,
                output=f"📂 No context documents found for task `{twm.task_id_short}`.",
                data={"docs": [], "task_id": twm.task_id_short},
            )

        lines = [f"📂 **Task Context Documents** (task: `{twm.task_id_short}`):\n"]
        for doc in docs:
            size_kb = doc["size_bytes"] / 1024
            lines.append(
                f"- **{doc['name']}.md** — {size_kb:.1f} KB "
                f"(modified: {doc['modified_at'][:19]})"
            )

        return tool_result(
            success=True,
            output="\n".join(lines),
            data={"docs": docs, "task_id": twm.task_id_short},
        )
    except Exception as e:
        logger.warning("task_doc_list_error", error=str(e))
        return tool_error(f"Failed to list docs: {e}")


async def _task_doc_delete(params: dict[str, Any], **kwargs: Any) -> str:
    """Delete a task context document."""
    twm = _get_twm()
    if twm is None:
        return tool_error(
            "No active task context. Task documents are only available during task execution."
        )

    doc = params.get("doc", "").replace(".md", "").replace(".MD", "")
    if not doc:
        return tool_error("Document name is required (doc parameter).")

    # Pipeline-managed docs — restrict direct deletes
    RESTRICTED_DOCS = {"PROGRESS", "TASK_BRIEF", "ROE", "TODO", "ACTIVITY", "FINAL_REPORT", "FINDINGS", "DECISIONS"}
    if doc.upper() in RESTRICTED_DOCS:
        return tool_error(
            f"{doc}.md is managed by the Nexus Orchestrator pipeline and cannot be deleted. "
            "Pipeline-managed files: TASK_BRIEF, ROE, TODO, ACTIVITY, FINAL_REPORT, PROGRESS."
        )

    try:
        deleted = await twm.delete_doc(doc)
        if deleted:
            return tool_result(
                success=True,
                output=(
                    f"🗑️ **{doc}.md** deleted successfully.\n\n"
                    f"Task: `{twm.task_id_short}`"
                ),
                data={"doc": doc, "deleted": True, "task_id": twm.task_id_short},
            )
        else:
            return tool_result(
                success=False,
                output=f"❌ **{doc}.md** does not exist.",
                data={"doc": doc, "deleted": False, "task_id": twm.task_id_short},
            )
    except Exception as e:
        logger.warning("task_doc_delete_error", doc=doc, error=str(e))
        return tool_error(f"Failed to delete {doc}: {e}")


# ── Self-Registration ────────────────────────────────────────────────────

_DOC_NAME_DESCRIPTION = (
    "Document name (without .md extension). "
    "Predefined: TASK_BRIEF, FINDINGS, DECISIONS, SCRATCHPAD. "
    "You can also use any custom name (e.g. analysis_results, agent_notes, "
    "research_data). Names are sanitized to alphanumeric + underscore."
)

registry.register(
    name="task_doc_read",
    toolset="task_docs",
    schema={
        "description": (
            "Read a task context document, specific section, or chunked portion. "
            "Works with predefined docs (TASK_BRIEF, FINDINGS, "
            "DECISIONS, SCRATCHPAD, KNOWLEDGE, FINAL_REPORT) and any custom docs. "
            "For LARGE files (>4000 chars): use offset+limit to read in chunks. "
            "KNOWLEDGE.md is auto-populated with all tool results and debate data. "
            "To read from a PREVIOUS task in the same session, pass task_id parameter."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doc": {
                    "type": "string",
                    "description": _DOC_NAME_DESCRIPTION,
                    "default": "TASK_BRIEF",
                },
                "task_id": {
                    "type": "string",
                    "description": (
                        "Optional: read from a DIFFERENT task's context (e.g., previous task in session). "
                        "Use the short task ID (first 8 chars). If omitted, reads from current task."
                    ),
                },
                "section": {
                    "type": "string",
                    "description": (
                        "Optional: read only a specific section by its markdown header. "
                        "Example: '## Goals' or '## Phase: RESEARCH'"
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Optional: character offset to start reading from (for chunked reading). "
                        "Use with 'limit' to read large documents in manageable chunks. "
                        "Default: 0 (start of file)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Optional: maximum characters to return (for chunked reading). "
                        "Use with 'offset' to paginate through large documents. "
                        "Default: 16000 chars (~4000 tokens). Set higher (e.g. 32000) if needed."
                    ),
                },
            },
        },
    },
    handler=_task_doc_read,
    emoji="📄",
)

registry.register(
    name="task_doc_write",
    toolset="task_docs",
    schema={
        "description": (
            "Write content to a task context document. Use to record findings, "
            "decisions, notes, or progress updates that persist across phases. "
            "Supports append (add to end), replace (overwrite), or prepend (add to start). "
            "You can write to predefined docs OR create new custom docs with any name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doc": {
                    "type": "string",
                    "description": _DOC_NAME_DESCRIPTION,
                    "default": "SCRATCHPAD",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write (markdown supported).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["append", "replace", "prepend"],
                    "description": "Write mode: append (default), replace, or prepend.",
                    "default": "append",
                },
            },
            "required": ["content"],
        },
    },
    handler=_task_doc_write,
    emoji="✏️",
)

registry.register(
    name="task_doc_edit",
    toolset="task_docs",
    schema={
        "description": (
            "Surgical edit: find exact text in a task document and replace it. "
            "Like apply_diff — searches for the exact 'search' text and replaces "
            "with 'replace' text. Use for precise updates without rewriting the whole document. "
            "Works with any document (predefined or custom)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doc": {
                    "type": "string",
                    "description": _DOC_NAME_DESCRIPTION,
                },
                "search": {
                    "type": "string",
                    "description": "Exact text to find (multi-line supported). Must match existing content.",
                },
                "replace": {
                    "type": "string",
                    "description": "New text to replace the found text with.",
                },
            },
            "required": ["doc", "search", "replace"],
        },
    },
    handler=_task_doc_edit,
    emoji="🔧",
)

registry.register(
    name="task_doc_search",
    toolset="task_docs",
    schema={
        "description": (
            "Search across all task context documents for specific text. "
            "Returns matching lines with document name and line number. "
            "Searches all documents (predefined + custom) by default."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for (case-insensitive).",
                },
                "docs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: limit search to specific documents. "
                        "Accepts both predefined and custom doc names."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    handler=_task_doc_search,
    emoji="🔍",
)

registry.register(
    name="task_doc_list",
    toolset="task_docs",
    schema={
        "description": (
            "List all task context documents with their sizes and modification times. "
            "Shows both predefined and custom documents. "
            "Pass task_id to list files from a PREVIOUS task in the same session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "Optional: list files from a different task (previous in session). "
                        "Use the short task ID (first 8 chars). If omitted, lists current task's docs."
                    ),
                },
            },
        },
    },
    handler=_task_doc_list,
    emoji="📂",
)

registry.register(
    name="task_doc_delete",
    toolset="task_docs",
    schema={
        "description": (
            "Delete a task context document. Use to clean up documents that are "
            "no longer needed. Works with both predefined and custom documents. "
            "Note: predefined docs may be recreated by auto-context hooks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doc": {
                    "type": "string",
                    "description": _DOC_NAME_DESCRIPTION,
                },
            },
            "required": ["doc"],
        },
    },
    handler=_task_doc_delete,
    emoji="🗑️",
)
