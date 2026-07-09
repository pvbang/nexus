"""Deferred tool index + tool_search: expose names cheaply, promote schemas on demand."""

from app.mcp.tool_search import build_tool_index, search_tools


_SCHEMAS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web across multiple engines.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Fetch and clean a web page.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "run_python",
        "description": "Execute a Python snippet in the sandbox.",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
    }},
]


def test_build_index_strips_parameters():
    index = build_tool_index(_SCHEMAS)
    assert len(index) == 3
    for entry in index:
        assert set(entry.keys()) == {"name", "description"}
    names = {e["name"] for e in index}
    assert names == {"web_search", "fetch_url", "run_python"}


def test_index_is_smaller_than_full_schema():
    import json
    full = len(json.dumps(_SCHEMAS))
    index = len(json.dumps(build_tool_index(_SCHEMAS)))
    assert index < full  # the whole point: cheaper to keep in context


def test_search_select_form():
    out = search_tools("select:web_search,run_python", _SCHEMAS)
    names = {s["function"]["name"] for s in out}
    assert names == {"web_search", "run_python"}
    # promoted entries carry full parameters again
    for s in out:
        assert "parameters" in s["function"]


def test_search_keyword_form_ranks_by_match():
    out = search_tools("web", _SCHEMAS, max_results=5)
    names = [s["function"]["name"] for s in out]
    # both web_search and fetch_url mention web; run_python should not appear
    assert "web_search" in names
    assert "fetch_url" in names
    assert "run_python" not in names


def test_search_unknown_select_returns_empty():
    out = search_tools("select:does_not_exist", _SCHEMAS)
    assert out == []


def test_search_keyword_respects_max_results():
    out = search_tools("e", _SCHEMAS, max_results=1)  # 'e' matches many
    assert len(out) <= 1
