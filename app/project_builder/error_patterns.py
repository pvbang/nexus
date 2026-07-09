"""
MekongAI Nexus - Error Pattern Recognition.

Nhận dạng error patterns phổ biến và auto-suggest fixes.
Sử dụng regex patterns + LLM fallback cho unknown errors.
Faster than LLM cho common errors (import, syntax, type errors).

Categories:
- import: Module/package import issues
- syntax: Syntax errors
- type: Type errors
- runtime: Runtime errors
- build: Build/compile errors
- config: Configuration issues
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ErrorPattern:
    """Definition of a recognizable error pattern.

    language: "js" | "ts" | "python" | "config" | "any"
    category: "import" | "syntax" | "type" | "runtime" | "build" | "config"
    """

    name: str
    regex: str  # regex pattern
    language: str  # js | ts | python | config | any
    category: str  # import | syntax | type | runtime | build | config
    description: str
    fix_suggestion: str
    auto_fixable: bool = False
    fix_template: str = ""  # e.g. "npm install {module}"


@dataclass
class ErrorMatch:
    """Result of matching an error against known patterns."""

    pattern_name: str
    category: str
    description: str
    fix_suggestion: str
    auto_fixable: bool = False
    fix_template: str = ""
    matched_text: str = ""
    confidence: float = 1.0  # 1.0 for regex, <1.0 for LLM
    extracted_info: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ErrorPatternMatcher
# ---------------------------------------------------------------------------


class ErrorPatternMatcher:
    """Nhận dạng error patterns phổ biến và suggest fixes.

    Sử dụng regex patterns + LLM fallback cho unknown errors.
    Faster than LLM cho common errors (import, syntax, type errors).
    """

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_core_llm()
        self._patterns: list[ErrorPattern] = []
        self._compiled: list[tuple[ErrorPattern, re.Pattern[str]]] = []
        self._register_default_patterns()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(
        self,
        error_text: str,
        language: str = "auto",
    ) -> ErrorMatch | None:
        """Match error text against known patterns.

        Returns ErrorMatch with fix suggestion, or None if no match.
        """
        if not error_text.strip():
            return None

        for pattern, compiled_re in self._compiled:
            # Filter by language if specified
            if language != "auto" and pattern.language not in (language, "any"):
                continue

            m = compiled_re.search(error_text)
            if m is None:
                continue

            # Extract named groups or positional groups
            extracted = m.groupdict() if m.groupdict() else {}
            if not extracted and m.groups():
                extracted = {f"group_{i}": g for i, g in enumerate(m.groups(), 1) if g}

            # Render fix_template with extracted info
            fix_template = pattern.fix_template
            fix_suggestion = pattern.fix_suggestion
            if extracted and fix_template:
                try:
                    fix_template = fix_template.format(**extracted)
                except (KeyError, IndexError):
                    pass
            if extracted and fix_suggestion:
                try:
                    fix_suggestion = fix_suggestion.format(**extracted)
                except (KeyError, IndexError):
                    pass

            logger.debug(
                "error_pattern_matched",
                pattern=pattern.name,
                extracted=extracted,
            )

            return ErrorMatch(
                pattern_name=pattern.name,
                category=pattern.category,
                description=pattern.description,
                fix_suggestion=fix_suggestion,
                auto_fixable=pattern.auto_fixable,
                fix_template=fix_template,
                matched_text=m.group(0),
                confidence=1.0,
                extracted_info=extracted,
            )

        return None

    async def analyze_unknown(
        self,
        error_text: str,
        file_path: str = "",
        code_context: str = "",
    ) -> ErrorMatch:
        """LLM phân tích unknown error → suggest fix.

        Used when no regex pattern matches.
        """
        context_parts = [f"Error:\n{error_text[:3000]}"]
        if file_path:
            context_parts.append(f"File: {file_path}")
        if code_context:
            context_parts.append(f"Code context:\n{code_context[:2000]}")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert developer. Analyze the error and suggest a fix.\n\n"
                    "Return ONLY valid JSON:\n"
                    "{\n"
                    '  "pattern_name": "short name for this error type",\n'
                    '  "category": "import|syntax|type|runtime|build|config",\n'
                    '  "description": "what went wrong",\n'
                    '  "fix_suggestion": "how to fix it",\n'
                    '  "auto_fixable": false,\n'
                    '  "confidence": 0.0 to 1.0\n'
                    "}"
                ),
            },
            {
                "role": "user",
                "content": "\n\n".join(context_parts),
            },
        ]

        try:
            parsed = await self.llm.chat_json(messages, temperature=0.3)

            category = parsed.get("category", "runtime")
            if category not in ("import", "syntax", "type", "runtime", "build", "config"):
                category = "runtime"

            confidence = float(parsed.get("confidence", 0.7))
            confidence = max(0.0, min(1.0, confidence))

            return ErrorMatch(
                pattern_name=parsed.get("pattern_name", "unknown_error"),
                category=category,
                description=parsed.get("description", "Unknown error"),
                fix_suggestion=parsed.get("fix_suggestion", "Review the error manually"),
                auto_fixable=bool(parsed.get("auto_fixable", False)),
                matched_text=error_text[:500],
                confidence=confidence,
            )

        except Exception as exc:
            logger.error("error_pattern_llm_analysis_failed", error=str(exc))
            return ErrorMatch(
                pattern_name="analysis_failed",
                category="runtime",
                description=f"Could not analyze error: {exc}",
                fix_suggestion="Review the error manually",
                matched_text=error_text[:500],
                confidence=0.0,
            )

    async def suggest_fix(
        self,
        error_text: str,
        file_path: str = "",
        code_context: str = "",
    ) -> ErrorMatch:
        """Convenience: try match() first, fallback to analyze_unknown()."""
        # Auto-detect language from file_path
        language = _detect_language(file_path)

        result = self.match(error_text, language=language)
        if result is not None:
            logger.info(
                "error_fix_suggested_regex",
                pattern=result.pattern_name,
            )
            return result

        logger.info("error_fix_no_regex_match_using_llm")
        return await self.analyze_unknown(error_text, file_path, code_context)

    # ------------------------------------------------------------------
    # Pattern registration
    # ------------------------------------------------------------------

    def register_pattern(self, pattern: ErrorPattern) -> None:
        """Register a custom error pattern."""
        self._patterns.append(pattern)
        try:
            compiled = re.compile(pattern.regex, re.IGNORECASE | re.MULTILINE)
            self._compiled.append((pattern, compiled))
        except re.error as exc:
            logger.error(
                "error_pattern_invalid_regex",
                pattern=pattern.name,
                error=str(exc),
            )

    def _register_default_patterns(self) -> None:
        """Register tất cả built-in error patterns."""
        for pattern in _DEFAULT_PATTERNS:
            self.register_pattern(pattern)


# ---------------------------------------------------------------------------
# Default error patterns (20+)
# ---------------------------------------------------------------------------

_DEFAULT_PATTERNS: list[ErrorPattern] = [
    # ===== JavaScript / TypeScript =====

    # 1. Cannot find module
    ErrorPattern(
        name="js_module_not_found",
        regex=r"Cannot find module ['\"](?P<module>[^'\"]+)['\"]",
        language="js",
        category="import",
        description="Module not found — missing dependency or wrong import path.",
        fix_suggestion="Run: npm install {module} — or check the import path is correct.",
        auto_fixable=True,
        fix_template="npm install {module}",
    ),

    # 2. SyntaxError: Unexpected token
    ErrorPattern(
        name="js_syntax_unexpected_token",
        regex=r"SyntaxError:\s*Unexpected token\s*(?P<token>\S+)?",
        language="js",
        category="syntax",
        description="Unexpected token — likely missing bracket, semicolon, or invalid syntax.",
        fix_suggestion="Check for missing brackets, semicolons, or malformed expressions near the error.",
        auto_fixable=False,
    ),

    # 3. TypeError: X is not a function
    ErrorPattern(
        name="js_not_a_function",
        regex=r"TypeError:\s*(?P<name>\S+)\s+is not a function",
        language="js",
        category="type",
        description="'{name}' is not a function — wrong import or incorrect method name.",
        fix_suggestion="Check the import of '{name}' — it may be a default vs named export mismatch.",
        auto_fixable=False,
    ),

    # 4. ReferenceError: X is not defined
    ErrorPattern(
        name="js_not_defined",
        regex=r"ReferenceError:\s*(?P<name>\S+)\s+is not defined",
        language="js",
        category="runtime",
        description="'{name}' is not defined — missing import or variable declaration.",
        fix_suggestion="Import or declare '{name}' before using it.",
        auto_fixable=False,
    ),

    # 5. JSX element type has no construct/call signatures
    ErrorPattern(
        name="ts_jsx_no_signatures",
        regex=r"JSX element type .+ does not have any construct or call signatures",
        language="ts",
        category="type",
        description="JSX element type error — component is not a valid React component.",
        fix_suggestion="Check the component import — ensure it's exported as a React component (not a type).",
        auto_fixable=False,
    ),

    # 6. Property 'X' does not exist on type
    ErrorPattern(
        name="ts_property_not_on_type",
        regex=r"Property '(?P<prop>[^']+)' does not exist on type '(?P<type>[^']+)'",
        language="ts",
        category="type",
        description="Property '{prop}' doesn't exist on type '{type}'.",
        fix_suggestion="Add '{prop}' to the type definition of '{type}', or use type assertion.",
        auto_fixable=False,
    ),

    # 7. Cannot use import statement outside a module
    ErrorPattern(
        name="js_import_outside_module",
        regex=r"Cannot use import statement outside a module",
        language="js",
        category="config",
        description="ES module import used in CommonJS context.",
        fix_suggestion='Add "type": "module" to package.json, or rename file to .mjs.',
        auto_fixable=True,
        fix_template='Add "type": "module" to package.json',
    ),

    # ===== Python =====

    # 8. ModuleNotFoundError
    ErrorPattern(
        name="py_module_not_found",
        regex=r"ModuleNotFoundError:\s*No module named ['\"]?(?P<module>[^'\"\\s]+)",
        language="python",
        category="import",
        description="Python module '{module}' not found.",
        fix_suggestion="Run: pip install {module}",
        auto_fixable=True,
        fix_template="pip install {module}",
    ),

    # 9. ImportError: cannot import name
    ErrorPattern(
        name="py_import_error",
        regex=r"ImportError:\s*cannot import name ['\"]?(?P<name>[^'\"]+)['\"]?\s*from\s*['\"]?(?P<module>[^'\"]+)",
        language="python",
        category="import",
        description="Cannot import '{name}' from '{module}'.",
        fix_suggestion="Check that '{name}' is exported from '{module}'. It may have been renamed or removed.",
        auto_fixable=False,
    ),

    # 10. SyntaxError: invalid syntax (Python)
    ErrorPattern(
        name="py_syntax_error",
        regex=r"SyntaxError:\s*invalid syntax",
        language="python",
        category="syntax",
        description="Python syntax error — invalid syntax.",
        fix_suggestion="Check the line indicated for missing colons, brackets, or incorrect keywords.",
        auto_fixable=False,
    ),

    # 11. IndentationError
    ErrorPattern(
        name="py_indentation_error",
        regex=r"IndentationError:\s*(?P<detail>.+)",
        language="python",
        category="syntax",
        description="Python indentation error: {detail}.",
        fix_suggestion="Fix indentation — use consistent spaces (4 spaces recommended). Don't mix tabs and spaces.",
        auto_fixable=False,
    ),

    # 12. NameError: name 'X' is not defined
    ErrorPattern(
        name="py_name_error",
        regex=r"NameError:\s*name '(?P<name>[^']+)' is not defined",
        language="python",
        category="runtime",
        description="Python name '{name}' is not defined.",
        fix_suggestion="Import or define '{name}' before use.",
        auto_fixable=False,
    ),

    # ===== Build / Config =====

    # 13. ENOENT: no such file or directory
    ErrorPattern(
        name="enoent_file_not_found",
        regex=r"ENOENT:\s*no such file or directory,?\s*\w*\s*'?(?P<path>[^']+)'?",
        language="any",
        category="build",
        description="File or directory not found: {path}.",
        fix_suggestion="Create the missing file/directory at '{path}', or fix the path reference.",
        auto_fixable=False,
    ),

    # 14. Port already in use
    ErrorPattern(
        name="port_in_use",
        regex=r"(?:EADDRINUSE|address already in use).*?(?::|\s)(?P<port>\d{4,5})",
        language="any",
        category="config",
        description="Port {port} is already in use.",
        fix_suggestion="Kill the process using port {port}, or change the port in config.",
        auto_fixable=True,
        fix_template="npx kill-port {port}",
    ),

    # 15. EACCES: permission denied
    ErrorPattern(
        name="eacces_permission",
        regex=r"EACCES:\s*permission denied,?\s*\w*\s*'?(?P<path>[^']+)'?",
        language="any",
        category="config",
        description="Permission denied for '{path}'.",
        fix_suggestion="Check file permissions for '{path}'. May need to run with elevated privileges.",
        auto_fixable=False,
    ),

    # 16. vite: command not found
    ErrorPattern(
        name="vite_not_found",
        regex=r"(?:vite|next|react-scripts|tsc|eslint):\s*command not found",
        language="any",
        category="build",
        description="Build tool command not found — dependencies not installed.",
        fix_suggestion="Run: npm install — to install project dependencies.",
        auto_fixable=True,
        fix_template="npm install",
    ),

    # 17. PostCSS plugin tailwindcss requires PostCSS 8
    ErrorPattern(
        name="postcss_version_mismatch",
        regex=r"PostCSS plugin (?P<plugin>\S+) requires PostCSS (?P<version>\d+)",
        language="any",
        category="config",
        description="PostCSS version mismatch — plugin '{plugin}' requires PostCSS {version}.",
        fix_suggestion="Run: npm install postcss@latest — to update PostCSS to version {version}+.",
        auto_fixable=True,
        fix_template="npm install postcss@latest",
    ),

    # ===== React specific =====

    # 18. Invalid hook call
    ErrorPattern(
        name="react_invalid_hook",
        regex=r"Invalid hook call\.\s*Hooks can only be called inside",
        language="js",
        category="runtime",
        description="Invalid React hook call — hooks must be called inside function components.",
        fix_suggestion=(
            "Ensure hooks are called at the top level of a function component. "
            "Check for duplicate React versions: npm ls react."
        ),
        auto_fixable=False,
    ),

    # 19. Each child in a list should have a unique "key" prop
    ErrorPattern(
        name="react_missing_key",
        regex=r"Each child in a (?:list|array) should have a unique [\"']key[\"'] prop",
        language="js",
        category="runtime",
        description="Missing key prop on list children.",
        fix_suggestion="Add a unique 'key' prop to each element rendered in a list/map.",
        auto_fixable=False,
    ),

    # 20. Maximum update depth exceeded
    ErrorPattern(
        name="react_max_update_depth",
        regex=r"Maximum update depth exceeded",
        language="js",
        category="runtime",
        description="Infinite re-render loop detected.",
        fix_suggestion=(
            "Check useEffect dependencies — likely a state setter is called "
            "unconditionally inside an effect, or a dependency changes every render."
        ),
        auto_fixable=False,
    ),

    # ===== Additional patterns =====

    # 21. TypeScript strict null checks
    ErrorPattern(
        name="ts_possibly_null",
        regex=r"Object is possibly '(?P<type>null|undefined)'",
        language="ts",
        category="type",
        description="Object might be {type} — strict null check failure.",
        fix_suggestion="Add a null/undefined check or use optional chaining (?.) before accessing the property.",
        auto_fixable=False,
    ),

    # 22. ESLint parsing error
    ErrorPattern(
        name="eslint_parse_error",
        regex=r"Parsing error:\s*(?P<detail>.+)",
        language="any",
        category="syntax",
        description="ESLint parsing error: {detail}.",
        fix_suggestion="Check ESLint config (.eslintrc) and parser settings. May need @typescript-eslint/parser.",
        auto_fixable=False,
    ),

    # 23. npm ERR! peer dep
    ErrorPattern(
        name="npm_peer_dep",
        regex=r"npm (?:ERR!|error)\s*.*peer dep.*?(?P<package>\S+@\S+)",
        language="any",
        category="config",
        description="npm peer dependency conflict with {package}.",
        fix_suggestion="Run: npm install --legacy-peer-deps — or resolve version conflicts manually.",
        auto_fixable=True,
        fix_template="npm install --legacy-peer-deps",
    ),

    # 24. Out of memory / heap
    ErrorPattern(
        name="js_heap_out_of_memory",
        regex=r"(?:JavaScript heap out of memory|FATAL ERROR:.*heap)",
        language="js",
        category="runtime",
        description="JavaScript heap out of memory.",
        fix_suggestion="Increase Node.js memory: NODE_OPTIONS='--max-old-space-size=4096'. Check for memory leaks.",
        auto_fixable=True,
        fix_template="NODE_OPTIONS='--max-old-space-size=4096'",
    ),

    # 25. Python TypeError
    ErrorPattern(
        name="py_type_error",
        regex=r"TypeError:\s*(?P<detail>.+)",
        language="python",
        category="type",
        description="Python TypeError: {detail}.",
        fix_suggestion="Check argument types and ensure compatible types are used.",
        auto_fixable=False,
    ),

    # 26. Tailwind class not found / purged
    ErrorPattern(
        name="tailwind_class_issue",
        regex=r"(?:The `(?P<class>[^`]+)` class does not exist|warn - .+tailwind)",
        language="any",
        category="config",
        description="Tailwind CSS class issue.",
        fix_suggestion="Check tailwind.config.js content paths. Ensure the class is a valid Tailwind utility.",
        auto_fixable=False,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "ts",
    ".tsx": "ts",
    ".js": "js",
    ".jsx": "js",
    ".json": "config",
    ".yml": "config",
    ".yaml": "config",
    ".toml": "config",
}


def _detect_language(file_path: str) -> str:
    """Detect language from file extension for pattern filtering."""
    if not file_path:
        return "auto"
    for ext, lang in _EXTENSION_LANGUAGE_MAP.items():
        if file_path.endswith(ext):
            return lang
    return "auto"
