"""
MekongAI Nexus - Smart Context Condensation.

Enhances context compression by preserving file structure information.
When conversation history is condensed, this module:
1. Extracts and preserves a file map (function signatures, class names, key structures)
2. Prioritizes recently accessed files
3. Maintains a character budget for preserved context
4. Generates a "folded context" that allows agent to continue working accurately

Inspired by: Roo-Code v3.45.0 Smart Code Folding
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


class SmartCondensation:
    """
    Smart context condensation that preserves file structure.

    When context is condensed (due to token limits), this class:
    1. Scans messages for file content that was read/edited
    2. Extracts structural signatures (function defs, class defs, imports)
    3. Builds a compact "file map" preserving key references
    4. Appends this map to the condensed summary
    """

    # Character budget for folded file context (default ~50k chars)
    DEFAULT_FOLD_BUDGET = 50_000

    def __init__(self, fold_budget: int = DEFAULT_FOLD_BUDGET) -> None:
        self.fold_budget = fold_budget

    def extract_file_signatures(self, file_content: str, file_path: str) -> str:
        """
        Extract structural signatures from file content.

        Strategy: tree-sitter AST first (accurate), regex fallback (broad language support).

        For Python files: class defs, function defs, import statements, decorators
        For Markdown: headers (##)
        For TypeScript/JavaScript: exports, functions, classes, interfaces
        For config files: top-level keys
        For other: first few lines as preview

        Returns compact representation with line numbers.
        """
        # Phase 3: Try tree-sitter first (AST-based, more accurate)
        try:
            from app.core.tree_sitter_parser import extract_signatures_tree_sitter
            result = extract_signatures_tree_sitter(file_content, file_path)
            if result:
                return result
        except ImportError:
            pass  # tree-sitter not installed, use regex fallback

        # Regex fallback (broad language support)
        lines = file_content.split("\n")
        ext = Path(file_path).suffix.lower()
        signatures: list[str] = []

        if ext in (".py",):
            # Python: imports, class defs, function defs, decorators
            for i, line in enumerate(lines):
                stripped = line.rstrip()
                if (
                    stripped.startswith("import ")
                    or stripped.startswith("from ")
                    or stripped.startswith("class ")
                    or stripped.startswith("def ")
                    or stripped.startswith("async def ")
                    or stripped.startswith("@")
                ):
                    signatures.append(f"{i + 1:4d} | {stripped}")
                # Also capture docstrings after class/def
                elif stripped.startswith('"""') and i > 0:
                    prev = lines[i - 1].rstrip() if i > 0 else ""
                    if "class " in prev or "def " in prev or "async def" in prev:
                        # Include single-line docstring
                        if stripped.count('"""') >= 2:
                            signatures.append(f"{i + 1:4d} | {stripped}")
                        else:
                            # Multi-line: include up to closing """
                            doc_lines = [f"{i + 1:4d} | {stripped}"]
                            for j in range(i + 1, min(i + 5, len(lines))):
                                doc_lines.append(f"{j + 1:4d} | {lines[j].rstrip()}")
                                if '"""' in lines[j]:
                                    break
                            signatures.extend(doc_lines)

        elif ext in (".md", ".markdown"):
            # Markdown: headers
            for i, line in enumerate(lines):
                if line.startswith("#"):
                    signatures.append(f"{i + 1:4d} | {line.rstrip()}")

        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            # TypeScript/JavaScript: exports, functions, classes, interfaces
            for i, line in enumerate(lines):
                stripped = line.rstrip()
                if (
                    stripped.startswith("export ")
                    or stripped.startswith("function ")
                    or stripped.startswith("class ")
                    or stripped.startswith("interface ")
                    or stripped.startswith("type ")
                    or (
                        stripped.startswith("const ")
                        and "=" in stripped
                        and "=>" in stripped
                    )
                ):
                    signatures.append(f"{i + 1:4d} | {stripped}")

        elif ext in (".yaml", ".yml", ".toml", ".json"):
            # Config: top-level keys only (first 30 lines)
            for i, line in enumerate(lines[:30]):
                stripped = line.rstrip()
                if (
                    stripped
                    and not stripped.startswith(" ")
                    and not stripped.startswith("#")
                ):
                    signatures.append(f"{i + 1:4d} | {stripped}")

        else:
            # Generic: first 10 lines as preview
            for i, line in enumerate(lines[:10]):
                signatures.append(f"{i + 1:4d} | {line.rstrip()}")

        if not signatures:
            return f"  (empty or unrecognized format, {len(lines)} lines total)"

        return "\n".join(signatures)

    def build_folded_context(
        self,
        tracked_files: list[tuple[str, str]],
        budget: Optional[int] = None,
    ) -> str:
        """
        Build folded context from tracked files within character budget.

        Files are assumed to be pre-sorted by recency (most recent first).
        Allocates budget proportionally, giving more to recent files.
        Top 3 files get 60% of budget, remaining files share 40%.

        Returns formatted folded context string.
        """
        if not tracked_files:
            return ""

        max_chars = budget or self.fold_budget
        result_parts: list[str] = []
        chars_used = 0

        # Allocate budget: 60% to top 3 files, 40% to rest
        top_budget = int(max_chars * 0.6)
        rest_budget = max_chars - top_budget

        top_files = tracked_files[:3]
        rest_files = tracked_files[3:]

        # Process top files (most recent)
        per_top = top_budget // max(len(top_files), 1)
        for path, content in top_files:
            sig = self.extract_file_signatures(content, path)
            entry = f"### {path}\n{sig}\n"
            if len(entry) > per_top:
                entry = entry[: per_top - 20] + "\n  ... (truncated)\n"
            result_parts.append(entry)
            chars_used += len(entry)

        # Process remaining files (less budget each)
        if rest_files:
            per_rest = rest_budget // max(len(rest_files), 1)
            for path, content in rest_files:
                if chars_used >= max_chars:
                    result_parts.append(f"### {path}\n  (budget exhausted)\n")
                    break
                sig = self.extract_file_signatures(content, path)
                entry = f"### {path}\n{sig}\n"
                if len(entry) > per_rest:
                    entry = entry[: per_rest - 20] + "\n  ... (truncated)\n"
                result_parts.append(entry)
                chars_used += len(entry)

        return "\n".join(result_parts)

    def enhance_condensed_summary(
        self,
        summary: str,
        folded_context: str,
    ) -> str:
        """
        Combine condensed summary with folded file context.

        Produces a formatted output with clear sections:
        - CONDENSED SUMMARY: the LLM-generated summary of prior conversation
        - FILE STRUCTURE MAP: preserved signatures for continued reference
        """
        if not folded_context.strip():
            return summary

        return (
            f"{summary}\n\n"
            f"---\n"
            f"## FILE STRUCTURE MAP (Reference for continuing work)\n\n"
            f"The following file signatures were preserved from the conversation. "
            f"Use these to reference file structure without re-reading entire files:\n\n"
            f"{folded_context}\n"
            f"---"
        )

    def extract_files_from_messages(
        self,
        messages: list[dict],
    ) -> list[tuple[str, str]]:
        """
        Scan conversation messages to extract file contents that were read/written.

        Looks for patterns:
        - Tool results containing "File: path (Lines..."
        - Tool calls to write_file with content
        - Tool calls to edit_file (extract path, infer file was read)

        Returns [(path, content), ...] sorted by most recent occurrence last
        (so reverse gives most-recent-first).
        """
        files_found: dict[str, str] = {}  # path → latest content

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                # Pattern 1: read_file output
                # "File: path/to/file.py (Lines 1-50 of 200 total)"
                file_header_pattern = (
                    r"File: (.+?) \(Lines \d+-\d+ of \d+ total\)\n\n"
                    r"((?:.*\n)*?)(?:\[File continues|\Z)"
                )
                for match in re.finditer(file_header_pattern, content):
                    path = match.group(1).strip()
                    # Strip line numbers from content
                    raw_content = match.group(2)
                    clean_lines = []
                    for line in raw_content.split("\n"):
                        # Remove "  1 | " prefix
                        stripped = re.sub(r"^\s*\d+\s*\|\s?", "", line)
                        clean_lines.append(stripped)
                    files_found[path] = "\n".join(clean_lines)

            # Pattern 2: write_file tool calls
            # Look for tool_calls with write_file
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    if func.get("name") == "write_file":
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                            if "path" in args and "content" in args:
                                files_found[args["path"]] = args["content"]
                        except (json.JSONDecodeError, TypeError):
                            pass

        # Return as list of tuples, maintaining insertion order (last = most recent)
        return list(files_found.items())


if __name__ == "__main__":
    # Quick self-test
    sc = SmartCondensation(fold_budget=5000)

    test_py = """import os
import sys
from pathlib import Path

class FileManager:
    \"\"\"Manages file operations.\"\"\"

    def __init__(self, workspace: Path):
        self.workspace = workspace

    async def read_file(self, path: str) -> str:
        \"\"\"Read a file from workspace.\"\"\"
        full = self.workspace / path
        return full.read_text()

    def list_files(self) -> list:
        return list(self.workspace.iterdir())
"""

    sig = sc.extract_file_signatures(test_py, "manager.py")
    print("=== Python signatures ===")
    print(sig)

    folded = sc.build_folded_context(
        [("manager.py", test_py), ("README.md", "# Project\n## Install\n## Usage")]
    )
    print("\n=== Folded context ===")
    print(folded)

    enhanced = sc.enhance_condensed_summary(
        "Previous work: built a file manager.", folded
    )
    print("\n=== Enhanced summary ===")
    print(enhanced[:500])
