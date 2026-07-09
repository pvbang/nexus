"""
MekongAI Nexus - Tree-Sitter Based Code Structure Extraction.

Extracts function signatures, class declarations, imports using AST parsing
instead of regex. Provides much more accurate file folding during context
condensation.

Inspired by: Roo-Code src/services/tree-sitter/ + foldedFileContext.ts

Dependencies (optional):
    pip install tree-sitter tree-sitter-python

Falls back to regex extraction when tree-sitter unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import structlog
    logger = structlog.get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Lazy-initialized parser state
_PARSER = None
_LANGUAGES: dict[str, object] = {}
_TREE_SITTER_AVAILABLE: Optional[bool] = None


def _check_tree_sitter_available() -> bool:
    """Check if tree-sitter is installed."""
    global _TREE_SITTER_AVAILABLE
    if _TREE_SITTER_AVAILABLE is None:
        try:
            import tree_sitter  # noqa: F401
            _TREE_SITTER_AVAILABLE = True
        except ImportError:
            _TREE_SITTER_AVAILABLE = False
    return _TREE_SITTER_AVAILABLE


def _get_parser(language: str):
    """Get or create parser for language. Returns None if unavailable."""
    global _PARSER, _LANGUAGES

    if not _check_tree_sitter_available():
        return None

    from tree_sitter import Language, Parser

    if _PARSER is None:
        _PARSER = Parser()

    if language not in _LANGUAGES:
        if language == "python":
            try:
                import tree_sitter_python as tspython
                _LANGUAGES["python"] = Language(tspython.language())
            except ImportError:
                logger.debug("tree_sitter_python_not_installed")
                return None
        else:
            return None  # Other languages not yet supported

    lang = _LANGUAGES.get(language)
    if lang:
        _PARSER.language = lang
        return _PARSER
    return None


def extract_signatures_tree_sitter(file_content: str, file_path: str) -> Optional[str]:
    """
    Extract structural signatures using tree-sitter AST.

    Returns compact representation with line ranges,
    or None if language not supported (caller falls back to regex).

    Args:
        file_content: Source code content.
        file_path: File path (used for language detection via extension).

    Returns:
        Formatted signatures string, or None if not supported.
    """
    ext = Path(file_path).suffix.lower()
    language_map = {
        ".py": "python",
    }

    language = language_map.get(ext)
    if not language:
        return None  # Unsupported language → fallback to regex

    parser = _get_parser(language)
    if not parser:
        return None  # tree-sitter not available → fallback to regex

    try:
        tree = parser.parse(bytes(file_content, "utf-8"))
        root = tree.root_node
    except Exception as e:
        logger.debug("tree_sitter_parse_error", file=file_path, error=str(e))
        return None

    signatures: list[str] = []
    lines = file_content.split("\n")

    if language == "python":
        _extract_python_signatures(root, lines, signatures)

    if not signatures:
        return None

    return "\n".join(signatures)


def _extract_python_signatures(node, lines: list[str], signatures: list[str], depth: int = 0):
    """
    Extract Python function/class/import signatures from AST.

    Produces output like:
        1      | import os
        3--45  | class MyService:
        5--10  |   def __init__(self, db):
        12--30 |   async def process(self, data: dict) -> Result:
    """
    for child in node.children:
        start_line = child.start_point[0]
        end_line = child.end_point[0]
        indent = "  " * depth

        if child.type == "import_statement" or child.type == "import_from_statement":
            line_text = lines[start_line].rstrip()
            signatures.append(f"{start_line + 1:4d}      | {indent}{line_text}")

        elif child.type == "class_definition":
            line_text = lines[start_line].rstrip()
            signatures.append(f"{start_line + 1:4d}--{end_line + 1:<4d} | {indent}{line_text}")

            # Extract docstring
            _extract_docstring(child, lines, signatures, depth + 1)

            # Recurse into class body for methods
            body = child.child_by_field_name("body")
            if body:
                _extract_python_signatures(body, lines, signatures, depth + 1)

        elif child.type == "function_definition":
            line_text = lines[start_line].rstrip()
            signatures.append(f"{start_line + 1:4d}--{end_line + 1:<4d} | {indent}{line_text}")

            # Extract docstring
            _extract_docstring(child, lines, signatures, depth + 1)

        elif child.type == "decorated_definition":
            # Get the decorator and the definition
            for sub in child.children:
                if sub.type == "decorator":
                    dec_line = lines[sub.start_point[0]].rstrip()
                    signatures.append(f"{sub.start_point[0] + 1:4d}      | {indent}{dec_line}")
                elif sub.type in ("function_definition", "class_definition"):
                    sub_start = sub.start_point[0]
                    sub_end = sub.end_point[0]
                    sub_text = lines[sub_start].rstrip()
                    signatures.append(f"{sub_start + 1:4d}--{sub_end + 1:<4d} | {indent}{sub_text}")

                    if sub.type == "class_definition":
                        body = sub.child_by_field_name("body")
                        if body:
                            _extract_python_signatures(body, lines, signatures, depth + 1)


def _extract_docstring(node, lines: list[str], signatures: list[str], depth: int):
    """Extract single-line docstring if present."""
    body = node.child_by_field_name("body")
    if not body or not body.children:
        return

    first_stmt = body.children[0]
    if first_stmt.type == "expression_statement" and first_stmt.children:
        expr = first_stmt.children[0]
        if expr.type == "string":
            doc_line = lines[first_stmt.start_point[0]].strip()
            if len(doc_line) < 100:  # Only single-line docstrings
                indent = "  " * depth
                signatures.append(f"{'':>10}  {indent}{doc_line}")


def is_available() -> bool:
    """Check if tree-sitter parsing is available."""
    return _check_tree_sitter_available()
