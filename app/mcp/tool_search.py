"""Deferred tool search — expose tool names cheaply, promote schemas on demand.

When an agent has access to ~180 tools, binding every full JSON schema into the
prompt every call is expensive. Deferred tool search keeps a compact *index*
(name + one-line description) in context and lets the model call ``tool_search``
to fetch the full schemas of only the tools it actually needs.

This module provides the pure pieces:
- ``build_tool_index``: strip schemas down to {name, description}.
- ``search_tools``: resolve a query to full schemas — either ``select:a,b,c``
  (exact names) or a keyword search ranked by name/description match.

Mirrors deer-flow's DeferredToolFilterMiddleware + tool_search query forms.
"""

from __future__ import annotations

from typing import Any


def _fn(schema: dict) -> dict:
    return schema.get("function", schema)


def build_tool_index(schemas: list[dict]) -> list[dict[str, str]]:
    """Reduce full tool schemas to a compact {name, description} index."""
    index: list[dict[str, str]] = []
    for s in schemas:
        fn = _fn(s)
        name = fn.get("name", "")
        if not name:
            continue
        index.append({"name": name, "description": fn.get("description", "")})
    return index


def search_tools(query: str, schemas: list[dict], *, max_results: int = 10) -> list[dict]:
    """Resolve a query to full tool schemas.

    Query forms:
    - ``select:a,b,c`` — return exactly those tools by name (any that exist).
    - ``+term ...`` — require the first term in the name, rank rest by match.
    - free text — keyword match against name + description, ranked.
    """
    query = (query or "").strip()
    by_name = {_fn(s).get("name", ""): s for s in schemas}

    if query.startswith("select:"):
        wanted = [n.strip() for n in query[len("select:"):].split(",") if n.strip()]
        return [by_name[n] for n in wanted if n in by_name]

    require = None
    terms = query
    if query.startswith("+"):
        parts = query[1:].split(None, 1)
        require = parts[0].lower()
        terms = parts[1] if len(parts) > 1 else ""

    term_list = [t.lower() for t in terms.split() if t]
    scored: list[tuple[int, str, dict]] = []
    for s in schemas:
        fn = _fn(s)
        name = fn.get("name", "")
        haystack = f"{name} {fn.get('description', '')}".lower()
        if require and require not in name.lower():
            continue
        score = sum(haystack.count(t) for t in term_list) if term_list else (1 if require else 0)
        if score > 0 or (require and not term_list):
            scored.append((score, name, s))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, _, s in scored[:max_results]]
