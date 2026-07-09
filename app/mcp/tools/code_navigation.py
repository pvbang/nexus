"""
MekongAI Nexus - Code Navigation Tools.

Provides IDE-like code intelligence for agents:
  - list_code_definitions: list functions/classes/methods in a file (AST)
  - codebase_search: semantic search across workspace files
  - find_definitions: go-to-definition for a symbol
  - find_references: find all usages of a symbol
  - find_imports: trace import chains and dependencies

Uses tree-sitter for AST parsing (with regex fallback) and TF-IDF
for semantic search (no external embedding service required).
"""

from __future__ import annotations

import fnmatch
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

import structlog

from app.core.command_guard import check_path_safety
from app.mcp.tool_registry import registry
from app.mcp.tools.filesystem import get_task_workspace, WORKSPACE_DIR

logger = structlog.get_logger(__name__)

# Limits
_MAX_FILES = 500
_MAX_RESULTS = 30
_MAX_FILE_SIZE = 1_000_000  # 1MB

# File extension to language mapping
_EXT_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
}

# Default code file patterns
_CODE_PATTERNS = (
    "*.py", "*.js", "*.ts", "*.tsx", "*.jsx", "*.java",
    "*.go", "*.rs", "*.rb", "*.php", "*.c", "*.h",
    "*.cpp", "*.hpp", "*.cs", "*.mjs", "*.mts",
)

# Directories to skip
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".tox",
    ".eggs", "*.egg-info", ".next", ".nuxt",
}


def _should_skip_dir(dirname: str) -> bool:
    """Check if directory should be skipped."""
    return dirname in _SKIP_DIRS or dirname.startswith(".")


def _is_code_file(filename: str) -> bool:
    """Check if file matches code patterns."""
    return any(fnmatch.fnmatch(filename, p) for p in _CODE_PATTERNS)


def _resolve_workspace_path(raw_path: str) -> Optional[Path]:
    """Resolve and validate a path within workspace.

    Uses the shared ``filesystem._smart_resolve_path`` so every path shape
    (relative, ``workspace_data/...`` prefix, ``tasks/...`` prefix, absolute)
    resolves the SAME place read_file/write_file do — task 3c44a93f bug #3.
    """
    ws = get_task_workspace()
    verdict = check_path_safety(raw_path, str(ws))
    if not verdict.safe:
        return None
    try:
        from app.mcp.tools.filesystem import _smart_resolve_path
        path = _smart_resolve_path(raw_path, ws, WORKSPACE_DIR)
    except Exception:
        path = ws / raw_path
    if not path.exists() and ws != WORKSPACE_DIR:
        path = WORKSPACE_DIR / raw_path
    if not str(path.resolve()).startswith(str(WORKSPACE_DIR.resolve())):
        return None
    return path


# ------------------------------------------------------------------
# 1. list_code_definitions — AST-based symbol listing
# ------------------------------------------------------------------

# Regex fallback patterns per language
_DEFINITION_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"^(\s*)(async\s+)?def\s+(\w+)\s*\(", "function"),
        (r"^(\s*)class\s+(\w+)", "class"),
    ],
    "javascript": [
        (r"^(\s*)(export\s+)?(async\s+)?function\s+(\w+)", "function"),
        (r"^(\s*)(export\s+)?class\s+(\w+)", "class"),
        (r"^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?\(", "function"),
        (r"^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*\(", "function"),
    ],
    "typescript": [
        (r"^(\s*)(export\s+)?(async\s+)?function\s+(\w+)", "function"),
        (r"^(\s*)(export\s+)?class\s+(\w+)", "class"),
        (r"^(\s*)(export\s+)?interface\s+(\w+)", "interface"),
        (r"^(\s*)(export\s+)?type\s+(\w+)", "type"),
        (r"^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?\(", "function"),
        (r"^(\s*)(export\s+)?enum\s+(\w+)", "enum"),
    ],
    "java": [
        (r"^\s*(public|private|protected)?\s*(static)?\s*(class|interface|enum)\s+(\w+)", "class"),
        (r"^\s*(public|private|protected)?\s*(static)?\s*\w+\s+(\w+)\s*\(", "method"),
    ],
    "go": [
        (r"^func\s+(\w+)\s*\(", "function"),
        (r"^func\s+\(\w+\s+\*?\w+\)\s+(\w+)\s*\(", "method"),
        (r"^type\s+(\w+)\s+(struct|interface)", "type"),
    ],
}

# Add same patterns for related extensions
_DEFINITION_PATTERNS["jsx"] = _DEFINITION_PATTERNS["javascript"]
_DEFINITION_PATTERNS["tsx"] = _DEFINITION_PATTERNS["typescript"]


def _extract_definitions_regex(content: str, language: str) -> list[dict[str, Any]]:
    """Extract definitions using regex patterns (fallback)."""
    patterns = _DEFINITION_PATTERNS.get(language, _DEFINITION_PATTERNS.get("python", []))
    definitions: list[dict[str, Any]] = []
    lines = content.split("\n")

    for i, line in enumerate(lines):
        for pattern, kind in patterns:
            match = re.match(pattern, line)
            if match:
                # Extract name from last named group
                groups = [g for g in match.groups() if g and not g.isspace()]
                name = groups[-1] if groups else ""
                if not name or name in ("async", "export", "const", "let", "var", "static", "public", "private", "protected"):
                    continue

                # Determine indent level for scope
                indent = len(line) - len(line.lstrip())

                # Find end line (next def at same or lower indent, or EOF)
                end_line = i
                for j in range(i + 1, min(i + 500, len(lines))):
                    stripped = lines[j].strip()
                    if not stripped:
                        continue
                    j_indent = len(lines[j]) - len(lines[j].lstrip())
                    if j_indent <= indent and stripped:
                        # Check if this is a new definition
                        is_def = any(re.match(p, lines[j]) for p, _ in patterns)
                        if is_def:
                            break
                    end_line = j

                definitions.append({
                    "name": name,
                    "kind": kind,
                    "line": i + 1,
                    "end_line": end_line + 1,
                    "indent": indent,
                    "signature": line.rstrip()[:200],
                })
                break  # One match per line

    return definitions


def _extract_definitions_tree_sitter(content: str, file_path: str) -> Optional[list[dict[str, Any]]]:
    """Extract definitions using tree-sitter AST (more accurate)."""
    try:
        from app.core.tree_sitter_parser import is_available
        if not is_available():
            return None
    except ImportError:
        return None

    ext = Path(file_path).suffix.lower()
    if ext != ".py":
        return None  # Only Python supported via tree-sitter currently

    try:
        from tree_sitter import Language, Parser
        import tree_sitter_python as tspython

        parser = Parser()
        parser.language = Language(tspython.language())
        tree = parser.parse(bytes(content, "utf-8"))
        root = tree.root_node
    except (ImportError, Exception):
        return None

    definitions: list[dict[str, Any]] = []
    _walk_python_ast(root, content.split("\n"), definitions, depth=0)
    return definitions


def _walk_python_ast(
    node, lines: list[str], definitions: list[dict[str, Any]], depth: int
):
    """Recursively walk Python AST to find definitions."""
    for child in node.children:
        start_line = child.start_point[0]
        end_line = child.end_point[0]

        if child.type == "class_definition":
            name_node = child.child_by_field_name("name")
            name = name_node.text.decode() if name_node else ""
            definitions.append({
                "name": name,
                "kind": "class",
                "line": start_line + 1,
                "end_line": end_line + 1,
                "indent": depth * 4,
                "signature": lines[start_line].rstrip()[:200],
            })
            # Recurse into class body
            body = child.child_by_field_name("body")
            if body:
                _walk_python_ast(body, lines, definitions, depth + 1)

        elif child.type == "function_definition":
            name_node = child.child_by_field_name("name")
            name = name_node.text.decode() if name_node else ""
            kind = "method" if depth > 0 else "function"
            definitions.append({
                "name": name,
                "kind": kind,
                "line": start_line + 1,
                "end_line": end_line + 1,
                "indent": depth * 4,
                "signature": lines[start_line].rstrip()[:200],
            })

        elif child.type == "decorated_definition":
            for sub in child.children:
                if sub.type in ("function_definition", "class_definition"):
                    sub_start = sub.start_point[0]
                    sub_end = sub.end_point[0]
                    name_node = sub.child_by_field_name("name")
                    name = name_node.text.decode() if name_node else ""
                    if sub.type == "class_definition":
                        kind = "class"
                    else:
                        kind = "method" if depth > 0 else "function"
                    definitions.append({
                        "name": name,
                        "kind": kind,
                        "line": sub_start + 1,
                        "end_line": sub_end + 1,
                        "indent": depth * 4,
                        "signature": lines[sub_start].rstrip()[:200],
                    })
                    if sub.type == "class_definition":
                        body = sub.child_by_field_name("body")
                        if body:
                            _walk_python_ast(body, lines, definitions, depth + 1)


async def handle_list_code_definitions(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """List all function/class/method definitions in a file."""
    raw_path = params.get("path", "")

    if not raw_path:
        return {"success": False, "output": "No file path provided", "data": None, "artifacts": []}

    path = _resolve_workspace_path(raw_path)
    if path is None:
        return {"success": False, "output": f"⚠️ Path blocked or invalid: {raw_path}", "data": None, "artifacts": []}
    if not path.exists():
        return {"success": False, "output": f"File not found: {raw_path}", "data": None, "artifacts": []}
    if not path.is_file():
        return {"success": False, "output": f"Not a file: {raw_path}", "data": None, "artifacts": []}

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"success": False, "output": f"Cannot read file: {e}", "data": None, "artifacts": []}

    ext = path.suffix.lower()
    language = _EXT_LANG_MAP.get(ext, "python")

    # Try tree-sitter first, fallback to regex
    definitions = _extract_definitions_tree_sitter(content, str(path))
    if definitions is None:
        definitions = _extract_definitions_regex(content, language)

    if not definitions:
        return {
            "success": True,
            "output": f"No definitions found in {raw_path}",
            "data": {"path": raw_path, "definitions": [], "language": language},
            "artifacts": [],
        }

    # Format output
    output_lines = [f"📋 Definitions in {raw_path} ({language}, {len(definitions)} symbols):\n"]
    for d in definitions:
        indent = "  " * (d["indent"] // 4)
        line_range = f"{d['line']}--{d['end_line']}" if d["end_line"] > d["line"] else str(d["line"])
        icon = {"class": "🏗️", "function": "🔧", "method": "⚙️", "interface": "📐", "type": "📎", "enum": "📊"}.get(d["kind"], "•")
        output_lines.append(f"  {line_range:>10} | {indent}{icon} {d['kind']} {d['name']}")

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "data": {
            "path": raw_path,
            "language": language,
            "definitions": definitions,
            "count": len(definitions),
        },
        "artifacts": [],
    }


# ------------------------------------------------------------------
# 2. codebase_search — semantic/meaning-based search
# ------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: lowercase, split on non-alpha, remove short tokens."""
    tokens = re.findall(r"[a-z][a-z0-9_]*", text.lower())
    # Also split camelCase
    expanded: list[str] = []
    for t in tokens:
        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).lower().split()
        expanded.extend(parts)
    return [t for t in expanded if len(t) > 2]


def _compute_tfidf_scores(
    query_tokens: list[str],
    documents: list[tuple[str, str]],  # (path, content)
) -> list[tuple[float, str, str]]:
    """Compute TF-IDF scores for query against documents.

    Returns sorted list of (score, path, best_matching_snippet).
    """
    if not query_tokens or not documents:
        return []

    # Build document frequency
    doc_freq: Counter = Counter()
    doc_tokens_list: list[list[str]] = []

    for _, content in documents:
        tokens = set(_tokenize(content[:10000]))  # Limit content for perf
        doc_tokens_list.append(list(tokens))
        for t in tokens:
            doc_freq[t] += 1

    n_docs = len(documents)
    results: list[tuple[float, str, str]] = []

    for idx, (path, content) in enumerate(documents):
        doc_tokens = doc_tokens_list[idx]
        token_counts = Counter(doc_tokens)
        score = 0.0

        for qt in query_tokens:
            tf = token_counts.get(qt, 0)
            if tf > 0:
                df = doc_freq.get(qt, 1)
                idf = math.log(n_docs / df) + 1
                score += tf * idf

        if score > 0:
            # Find best matching snippet
            snippet = _find_best_snippet(content, query_tokens)
            results.append((score, path, snippet))

    results.sort(key=lambda x: x[0], reverse=True)
    return results


def _find_best_snippet(content: str, query_tokens: list[str]) -> str:
    """Find the most relevant 3-line snippet from content."""
    lines = content.split("\n")
    best_score = 0
    best_idx = 0

    for i, line in enumerate(lines):
        line_lower = line.lower()
        score = sum(1 for t in query_tokens if t in line_lower)
        if score > best_score:
            best_score = score
            best_idx = i

    start = max(0, best_idx - 1)
    end = min(len(lines), best_idx + 2)
    snippet_lines = lines[start:end]
    return "\n".join(f"{start + j + 1}: {l.rstrip()[:150]}" for j, l in enumerate(snippet_lines))


async def handle_codebase_search(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Semantic search across workspace code files.

    Uses TF-IDF based relevance scoring to find files and code
    matching a natural language query. More intelligent than grep.
    """
    query = params.get("query", "").strip()
    path = params.get("path", ".")
    file_pattern = params.get("file_pattern", "")
    max_results = params.get("max_results", _MAX_RESULTS)

    if not query:
        return {"success": False, "output": "No search query provided", "data": None, "artifacts": []}

    ws = get_task_workspace()
    verdict = check_path_safety(path, str(ws))
    if not verdict.safe:
        return {"success": False, "output": f"⚠️ Path blocked: {verdict.reason}", "data": None, "artifacts": []}

    # Shared resolver: every path shape lands the same place read_file does
    # and is task-scoped by default (task 3c44a93f bug #3).
    try:
        from app.mcp.tools.filesystem import _smart_resolve_path
        search_dir = _smart_resolve_path(path, ws, WORKSPACE_DIR)
    except Exception:
        search_dir = ws / path
    # Pick the display root the resolved dir is actually under, so matched-file
    # rel paths render relative to it without raising.
    try:
        search_dir.relative_to(ws)
        ws_root = ws
    except ValueError:
        ws_root = WORKSPACE_DIR
    if not search_dir.exists():
        return {"success": False, "output": f"Directory not found: {path}", "data": None, "artifacts": []}

    # Collect code files
    documents: list[tuple[str, str]] = []
    files_scanned = 0

    for root, dirs, files in os.walk(search_dir):
        # Skip unwanted directories
        dirs[:] = [d for d in dirs if not _should_skip_dir(d)]

        for filename in files:
            if files_scanned >= _MAX_FILES:
                break

            # Filter by pattern if provided
            if file_pattern:
                if not fnmatch.fnmatch(filename, file_pattern):
                    continue
            elif not _is_code_file(filename):
                continue

            filepath = Path(root) / filename
            if filepath.stat().st_size > _MAX_FILE_SIZE:
                continue

            files_scanned += 1
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                rel_path = str(filepath.relative_to(ws_root))
                documents.append((rel_path, content))
            except (OSError, UnicodeDecodeError):
                continue

        if files_scanned >= _MAX_FILES:
            break

    if not documents:
        return {
            "success": True,
            "output": f"No code files found in {path}",
            "data": {"results": [], "files_scanned": 0},
            "artifacts": [],
        }

    # Compute relevance
    query_tokens = _tokenize(query)
    scored = _compute_tfidf_scores(query_tokens, documents)
    top_results = scored[:max_results]

    if not top_results:
        return {
            "success": True,
            "output": f"No relevant results for '{query}' ({files_scanned} files scanned)",
            "data": {"results": [], "files_scanned": files_scanned, "query": query},
            "artifacts": [],
        }

    # Format output
    output_lines = [f"🔍 Found {len(top_results)} relevant files for '{query}' ({files_scanned} files scanned):\n"]
    results_data: list[dict[str, Any]] = []

    for score, fpath, snippet in top_results:
        output_lines.append(f"  📄 {fpath} (score: {score:.2f})")
        output_lines.append(f"     {snippet.split(chr(10))[0]}")
        output_lines.append("")
        results_data.append({
            "file": fpath,
            "score": round(score, 3),
            "snippet": snippet,
        })

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "data": {
            "results": results_data,
            "total_results": len(top_results),
            "files_scanned": files_scanned,
            "query": query,
        },
        "artifacts": [],
    }


# ------------------------------------------------------------------
# 3. find_definitions — go-to-definition
# ------------------------------------------------------------------

# Patterns to identify where a symbol is DEFINED (not just used)
_DEFINITION_REGEX: dict[str, list[str]] = {
    "python": [
        r"^(\s*)(async\s+)?def\s+{symbol}\s*\(",
        r"^(\s*)class\s+{symbol}\s*[\(:]",
        r"^{symbol}\s*=",
        r"^(\s*){symbol}\s*:\s*",  # type annotation assignment
    ],
    "javascript": [
        r"^(\s*)(export\s+)?(async\s+)?function\s+{symbol}\s*\(",
        r"^(\s*)(export\s+)?class\s+{symbol}\s",
        r"^(\s*)(export\s+)?(const|let|var)\s+{symbol}\s*=",
    ],
    "typescript": [
        r"^(\s*)(export\s+)?(async\s+)?function\s+{symbol}\s*[<(]",
        r"^(\s*)(export\s+)?class\s+{symbol}\s",
        r"^(\s*)(export\s+)?interface\s+{symbol}\s",
        r"^(\s*)(export\s+)?type\s+{symbol}\s*=",
        r"^(\s*)(export\s+)?(const|let|var)\s+{symbol}\s*[=:]",
        r"^(\s*)(export\s+)?enum\s+{symbol}\s",
    ],
}
_DEFINITION_REGEX["jsx"] = _DEFINITION_REGEX["javascript"]
_DEFINITION_REGEX["tsx"] = _DEFINITION_REGEX["typescript"]


async def handle_find_definitions(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Find where a symbol (function/class/variable) is defined."""
    symbol = params.get("symbol", "").strip()
    path = params.get("path", ".")
    file_pattern = params.get("file_pattern", "")

    if not symbol:
        return {"success": False, "output": "No symbol name provided", "data": None, "artifacts": []}

    if not re.match(r"^[a-zA-Z_]\w*$", symbol):
        return {"success": False, "output": f"Invalid symbol name: {symbol}", "data": None, "artifacts": []}

    ws = get_task_workspace()
    verdict = check_path_safety(path, str(ws))
    if not verdict.safe:
        return {"success": False, "output": f"⚠️ Path blocked: {verdict.reason}", "data": None, "artifacts": []}

    # Shared resolver: every path shape lands the same place read_file does
    # and is task-scoped by default (task 3c44a93f bug #3).
    try:
        from app.mcp.tools.filesystem import _smart_resolve_path
        search_dir = _smart_resolve_path(path, ws, WORKSPACE_DIR)
    except Exception:
        search_dir = ws / path
    # Pick the display root the resolved dir is actually under, so matched-file
    # rel paths render relative to it without raising.
    try:
        search_dir.relative_to(ws)
        ws_root = ws
    except ValueError:
        ws_root = WORKSPACE_DIR
    if not search_dir.exists():
        return {"success": False, "output": f"Directory not found: {path}", "data": None, "artifacts": []}

    definitions_found: list[dict[str, Any]] = []
    files_searched = 0

    for root, dirs, files in os.walk(search_dir):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d)]

        for filename in files:
            if files_searched >= _MAX_FILES:
                break
            if file_pattern:
                if not fnmatch.fnmatch(filename, file_pattern):
                    continue
            elif not _is_code_file(filename):
                continue

            filepath = Path(root) / filename
            if filepath.stat().st_size > _MAX_FILE_SIZE:
                continue

            files_searched += 1
            ext = filepath.suffix.lower()
            language = _EXT_LANG_MAP.get(ext, "python")

            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                lines = content.split("\n")
            except (OSError, UnicodeDecodeError):
                continue

            # Get patterns for this language
            patterns = _DEFINITION_REGEX.get(language, _DEFINITION_REGEX["python"])

            for i, line in enumerate(lines):
                for pat_template in patterns:
                    pat = pat_template.format(symbol=re.escape(symbol))
                    if re.match(pat, line):
                        rel_path = str(filepath.relative_to(ws_root))
                        # Get context (2 lines after)
                        context_end = min(len(lines), i + 3)
                        context = "\n".join(lines[i:context_end])

                        definitions_found.append({
                            "file": rel_path,
                            "line": i + 1,
                            "content": line.rstrip()[:300],
                            "context": context[:500],
                            "language": language,
                        })
                        break  # One match per line

            if len(definitions_found) >= _MAX_RESULTS:
                break
        if len(definitions_found) >= _MAX_RESULTS:
            break

    if not definitions_found:
        return {
            "success": True,
            "output": f"No definitions found for '{symbol}' ({files_searched} files searched)",
            "data": {"symbol": symbol, "definitions": [], "files_searched": files_searched},
            "artifacts": [],
        }

    # Format output
    output_lines = [f"📍 Found {len(definitions_found)} definition(s) for '{symbol}':\n"]
    for d in definitions_found:
        output_lines.append(f"  {d['file']}:{d['line']}")
        output_lines.append(f"    {d['content']}")
        output_lines.append("")

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "data": {
            "symbol": symbol,
            "definitions": definitions_found,
            "count": len(definitions_found),
            "files_searched": files_searched,
        },
        "artifacts": [],
    }


# ------------------------------------------------------------------
# 4. find_references — find all usages of a symbol
# ------------------------------------------------------------------


async def handle_find_references(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Find all references/usages of a symbol across the workspace."""
    symbol = params.get("symbol", "").strip()
    path = params.get("path", ".")
    file_pattern = params.get("file_pattern", "")
    include_definitions = params.get("include_definitions", True)
    max_results = params.get("max_results", _MAX_RESULTS)

    if not symbol:
        return {"success": False, "output": "No symbol name provided", "data": None, "artifacts": []}

    if not re.match(r"^[a-zA-Z_]\w*$", symbol):
        return {"success": False, "output": f"Invalid symbol name: {symbol}", "data": None, "artifacts": []}

    ws = get_task_workspace()
    verdict = check_path_safety(path, str(ws))
    if not verdict.safe:
        return {"success": False, "output": f"⚠️ Path blocked: {verdict.reason}", "data": None, "artifacts": []}

    # Shared resolver: every path shape lands the same place read_file does
    # and is task-scoped by default (task 3c44a93f bug #3).
    try:
        from app.mcp.tools.filesystem import _smart_resolve_path
        search_dir = _smart_resolve_path(path, ws, WORKSPACE_DIR)
    except Exception:
        search_dir = ws / path
    # Pick the display root the resolved dir is actually under, so matched-file
    # rel paths render relative to it without raising.
    try:
        search_dir.relative_to(ws)
        ws_root = ws
    except ValueError:
        ws_root = WORKSPACE_DIR
    if not search_dir.exists():
        return {"success": False, "output": f"Directory not found: {path}", "data": None, "artifacts": []}

    # Word-boundary pattern to find symbol usages
    # Use \b for word boundary to avoid partial matches
    usage_pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")

    references: list[dict[str, Any]] = []
    files_searched = 0
    files_with_refs = 0

    for root, dirs, files in os.walk(search_dir):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d)]

        for filename in files:
            if files_searched >= _MAX_FILES:
                break
            if file_pattern:
                if not fnmatch.fnmatch(filename, file_pattern):
                    continue
            elif not _is_code_file(filename):
                continue

            filepath = Path(root) / filename
            if filepath.stat().st_size > _MAX_FILE_SIZE:
                continue

            files_searched += 1
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                lines = content.split("\n")
            except (OSError, UnicodeDecodeError):
                continue

            file_has_ref = False
            rel_path = str(filepath.relative_to(ws_root))
            ext = filepath.suffix.lower()
            language = _EXT_LANG_MAP.get(ext, "python")

            for i, line in enumerate(lines):
                if len(references) >= max_results:
                    break
                if usage_pattern.search(line):
                    # Classify: is this a definition or a reference?
                    is_def = False
                    if not include_definitions:
                        patterns = _DEFINITION_REGEX.get(language, _DEFINITION_REGEX["python"])
                        for pat_template in patterns:
                            pat = pat_template.format(symbol=re.escape(symbol))
                            if re.match(pat, line):
                                is_def = True
                                break
                        if is_def:
                            continue

                    ref_kind = "definition" if is_def else "reference"
                    references.append({
                        "file": rel_path,
                        "line": i + 1,
                        "content": line.rstrip()[:300],
                        "kind": ref_kind,
                    })
                    file_has_ref = True

            if file_has_ref:
                files_with_refs += 1

        if len(references) >= max_results:
            break

    if not references:
        return {
            "success": True,
            "output": f"No references found for '{symbol}' ({files_searched} files searched)",
            "data": {"symbol": symbol, "references": [], "files_searched": files_searched},
            "artifacts": [],
        }

    # Format output
    output_lines = [f"🔗 Found {len(references)} reference(s) for '{symbol}' in {files_with_refs} file(s):\n"]
    for r in references:
        icon = "📍" if r["kind"] == "definition" else "→"
        output_lines.append(f"  {icon} {r['file']}:{r['line']}: {r['content']}")

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "data": {
            "symbol": symbol,
            "references": references,
            "count": len(references),
            "files_searched": files_searched,
            "files_with_references": files_with_refs,
        },
        "artifacts": [],
    }


# ------------------------------------------------------------------
# 5. find_imports — trace import chains
# ------------------------------------------------------------------

# Import patterns by language
_IMPORT_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"^\s*import\s+(.+)", "import"),
        (r"^\s*from\s+(\S+)\s+import\s+(.+)", "from_import"),
    ],
    "javascript": [
        (r"""^\s*import\s+.+\s+from\s+['"](.+?)['"]""", "import"),
        (r"""^\s*const\s+.+\s*=\s*require\s*\(\s*['"](.+?)['"]\s*\)""", "require"),
        (r"""^\s*import\s*\(\s*['"](.+?)['"]\s*\)""", "dynamic_import"),
    ],
    "typescript": [
        (r"""^\s*import\s+.+\s+from\s+['"](.+?)['"]""", "import"),
        (r"""^\s*import\s+type\s+.+\s+from\s+['"](.+?)['"]""", "type_import"),
        (r"""^\s*import\s*\(\s*['"](.+?)['"]\s*\)""", "dynamic_import"),
    ],
}
_IMPORT_PATTERNS["jsx"] = _IMPORT_PATTERNS["javascript"]
_IMPORT_PATTERNS["tsx"] = _IMPORT_PATTERNS["typescript"]


def _parse_python_imports(content: str) -> list[dict[str, Any]]:
    """Parse Python import statements."""
    imports: list[dict[str, Any]] = []
    lines = content.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()

        # import module / import module as alias
        match = re.match(r"^import\s+(.+)", stripped)
        if match:
            modules = [m.strip().split(" as ")[0].strip() for m in match.group(1).split(",")]
            for mod in modules:
                imports.append({
                    "module": mod,
                    "names": [],
                    "kind": "import",
                    "line": i + 1,
                })
            continue

        # from module import name1, name2
        match = re.match(r"^from\s+(\S+)\s+import\s+(.+)", stripped)
        if match:
            module = match.group(1)
            names_raw = match.group(2)
            # Handle multi-line imports (parenthesized)
            if "(" in names_raw and ")" not in names_raw:
                # Collect continuation lines
                j = i + 1
                while j < len(lines) and ")" not in lines[j]:
                    names_raw += " " + lines[j].strip()
                    j += 1
                if j < len(lines):
                    names_raw += " " + lines[j].strip()
            names_raw = names_raw.strip("() ")
            names = [n.strip().split(" as ")[0].strip() for n in names_raw.split(",") if n.strip()]
            imports.append({
                "module": module,
                "names": names,
                "kind": "from_import",
                "line": i + 1,
            })

    return imports


def _parse_js_ts_imports(content: str, language: str) -> list[dict[str, Any]]:
    """Parse JavaScript/TypeScript import statements."""
    imports: list[dict[str, Any]] = []
    lines = content.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()

        # import ... from 'module'
        match = re.match(r"""import\s+(.+?)\s+from\s+['"](.+?)['"]""", stripped)
        if match:
            names_part = match.group(1)
            module = match.group(2)
            # Extract named imports
            named_match = re.search(r"\{(.+?)\}", names_part)
            names = []
            if named_match:
                names = [n.strip().split(" as ")[0].strip() for n in named_match.group(1).split(",")]
            imports.append({
                "module": module,
                "names": names,
                "kind": "import",
                "line": i + 1,
            })
            continue

        # const ... = require('module')
        match = re.match(r"""(?:const|let|var)\s+(.+?)\s*=\s*require\s*\(\s*['"](.+?)['"]\s*\)""", stripped)
        if match:
            module = match.group(2)
            imports.append({
                "module": module,
                "names": [],
                "kind": "require",
                "line": i + 1,
            })

    return imports


async def handle_find_imports(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Trace imports and dependencies for a file or module.

    Can show:
    - All imports IN a specific file
    - All files that IMPORT a specific module/symbol
    """
    file_path = params.get("path", "")
    module_name = params.get("module", "")
    direction = params.get("direction", "outgoing")  # "outgoing" (imports in file) or "incoming" (who imports this)

    if not file_path and not module_name:
        return {"success": False, "output": "Provide either 'path' (to list imports) or 'module' (to find importers)", "data": None, "artifacts": []}

    # Case 1: List all imports in a specific file
    if file_path and direction == "outgoing":
        path = _resolve_workspace_path(file_path)
        if path is None:
            return {"success": False, "output": f"⚠️ Path blocked: {file_path}", "data": None, "artifacts": []}
        if not path.exists():
            return {"success": False, "output": f"File not found: {file_path}", "data": None, "artifacts": []}

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"success": False, "output": f"Cannot read file: {e}", "data": None, "artifacts": []}

        ext = path.suffix.lower()
        language = _EXT_LANG_MAP.get(ext, "python")

        if language == "python":
            imports = _parse_python_imports(content)
        elif language in ("javascript", "typescript"):
            imports = _parse_js_ts_imports(content, language)
        else:
            imports = _parse_python_imports(content)  # Fallback

        if not imports:
            return {
                "success": True,
                "output": f"No imports found in {file_path}",
                "data": {"path": file_path, "imports": [], "direction": "outgoing"},
                "artifacts": [],
            }

        output_lines = [f"📦 Imports in {file_path} ({len(imports)} statements):\n"]
        for imp in imports:
            if imp["names"]:
                names_str = ", ".join(imp["names"][:10])
                if len(imp["names"]) > 10:
                    names_str += f" ... (+{len(imp['names']) - 10} more)"
                output_lines.append(f"  L{imp['line']:4d} | from {imp['module']} → {names_str}")
            else:
                output_lines.append(f"  L{imp['line']:4d} | import {imp['module']}")

        return {
            "success": True,
            "output": "\n".join(output_lines),
            "data": {
                "path": file_path,
                "imports": imports,
                "count": len(imports),
                "direction": "outgoing",
            },
            "artifacts": [],
        }

    # Case 2: Find all files that import a given module/symbol
    search_target = module_name or file_path
    ws = get_task_workspace()
    search_dir = ws

    # Build regex to find imports of this module
    escaped = re.escape(search_target)
    # Also match partial paths (e.g., "app.core.auth" should match "from app.core.auth import ...")
    import_patterns = [
        re.compile(r"^\s*import\s+" + escaped, re.MULTILINE),
        re.compile(r"^\s*from\s+" + escaped + r"\s+import", re.MULTILINE),
        re.compile(r"""from\s+['"].*""" + escaped + r""".*['"]""", re.MULTILINE),
        re.compile(r"""require\s*\(\s*['"].*""" + escaped + r""".*['"]""", re.MULTILINE),
    ]

    importers: list[dict[str, Any]] = []
    files_searched = 0

    for root, dirs, files in os.walk(search_dir):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d)]

        for filename in files:
            if files_searched >= _MAX_FILES:
                break
            if not _is_code_file(filename):
                continue

            filepath = Path(root) / filename
            if filepath.stat().st_size > _MAX_FILE_SIZE:
                continue

            files_searched += 1
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            lines = content.split("\n")
            rel_path = str(filepath.relative_to(ws))

            for i, line in enumerate(lines):
                for pat in import_patterns:
                    if pat.search(line):
                        importers.append({
                            "file": rel_path,
                            "line": i + 1,
                            "content": line.strip()[:300],
                        })
                        break  # One match per line

        if files_searched >= _MAX_FILES:
            break

    if not importers:
        return {
            "success": True,
            "output": f"No files import '{search_target}' ({files_searched} files searched)",
            "data": {"module": search_target, "importers": [], "direction": "incoming", "files_searched": files_searched},
            "artifacts": [],
        }

    output_lines = [f"📥 {len(importers)} file(s) import '{search_target}':\n"]
    for imp in importers[:_MAX_RESULTS]:
        output_lines.append(f"  {imp['file']}:{imp['line']}")
        output_lines.append(f"    {imp['content']}")

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "data": {
            "module": search_target,
            "importers": importers[:_MAX_RESULTS],
            "count": len(importers),
            "direction": "incoming",
            "files_searched": files_searched,
        },
        "artifacts": [],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "code_navigation"

registry.register(
    name="list_code_definitions",
    toolset=_TOOLSET,
    schema={
        "description": (
            "List all function, class, method, and type definitions in a file. "
            "Uses AST parsing (tree-sitter) for Python, regex for other languages. "
            "Returns symbol names, kinds, line ranges, and signatures."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
            },
            "required": ["path"],
        },
    },
    handler=handle_list_code_definitions,
    is_async=True,
    emoji="📋",
)

registry.register(
    name="codebase_search",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Semantic search across workspace code files using natural language queries. "
            "More intelligent than grep — finds code by meaning, not just text match. "
            "Example: 'authentication middleware' finds auth-related code even if those "
            "exact words aren't in the code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query describing what you're looking for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (relative to workspace, default: '.')",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g., '*.py'). Default: all code files",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 30)",
                },
            },
            "required": ["query"],
        },
    },
    handler=handle_codebase_search,
    is_async=True,
    emoji="🔍",
)

registry.register(
    name="find_definitions",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Find where a symbol (function, class, variable, type) is defined. "
            "Like IDE 'Go to Definition'. Searches across the workspace for the "
            "declaration/definition of the given symbol name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Name of the function, class, variable, or type to find",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: '.' = entire workspace)",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g., '*.py')",
                },
            },
            "required": ["symbol"],
        },
    },
    handler=handle_find_definitions,
    is_async=True,
    emoji="📍",
)

registry.register(
    name="find_references",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Find all references/usages of a symbol across the workspace. "
            "Like IDE 'Find All References'. Shows every place a function, class, "
            "or variable is used."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Name of the symbol to find references for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: '.' = entire workspace)",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g., '*.py')",
                },
                "include_definitions": {
                    "type": "boolean",
                    "description": "Include definition sites in results (default: true)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 30)",
                },
            },
            "required": ["symbol"],
        },
    },
    handler=handle_find_references,
    is_async=True,
    emoji="🔗",
)

registry.register(
    name="find_imports",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Trace import chains and dependencies. Two modes:\n"
            "1. 'outgoing': List all imports IN a specific file (what does this file depend on?)\n"
            "2. 'incoming': Find all files that IMPORT a specific module (who depends on this?)\n"
            "Supports Python, JavaScript, and TypeScript import syntax."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to analyze imports for (outgoing mode)",
                },
                "module": {
                    "type": "string",
                    "description": "Module/package name to find importers of (incoming mode)",
                },
                "direction": {
                    "type": "string",
                    "enum": ["outgoing", "incoming"],
                    "description": "outgoing = imports in file, incoming = who imports this module (default: outgoing)",
                },
            },
        },
    },
    handler=handle_find_imports,
    is_async=True,
    emoji="📦",
)
