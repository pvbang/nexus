"""
MekongAI Nexus - Safe Regex (OpenClaw-inspired).

ReDoS-safe regex validation to prevent catastrophic backtracking
from user-provided regex patterns.

Inspired by:
- OpenClaw src/security/safe-regex.ts — ReDoS protection
- OpenClaw src/security/config-regex.ts — Config regex validation

Features:
- Timeout-based regex compilation and match testing
- Pattern complexity analysis (nested quantifiers, backreferences)
- Maximum pattern length enforcement
- Safe compilation with configurable flags
"""

from __future__ import annotations

import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Optional, Pattern

import structlog

logger = structlog.get_logger(__name__)

# Thread pool for timeout-based regex testing
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="safe-regex")


@dataclass
class RegexValidation:
    """Result of safe regex validation."""

    safe: bool
    pattern: str = ""
    reason: str = ""
    compiled: Optional[Pattern] = None
    complexity_score: float = 0.0


# ─────────────────────────────────────────────────────────────────
# Dangerous Pattern Detection
# ─────────────────────────────────────────────────────────────────

# Patterns that indicate potential ReDoS vulnerability
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # Nested quantifiers: (a+)+ or (a*)*
    (r"\(.+[*+]\)\s*[*+]", "Nested quantifiers detected"),
    # Overlapping alternation with quantifiers: (a|a)+
    (r"\([^)]*\|[^)]*\)\s*[*+]", "Overlapping alternation with quantifier"),
    # Back-to-back quantifiers on same group
    (r"[*+?]\s*[*+?]", "Adjacent quantifiers"),
    # Exponential patterns: .* followed by .+
    (r"\.\*.*\.\+", "Greedy wildcards in sequence"),
    # Catastrophic backtracking pattern: (.*a){n}
    (r"\(\.\*[^)]+\)\s*\{", "Greedy wildcard in repeated group"),
]

# Maximum safe values
MAX_PATTERN_LENGTH = 500
MAX_QUANTIFIER_VALUE = 100
COMPILATION_TIMEOUT_S = 2.0
MATCH_TEST_TIMEOUT_S = 1.0


class SafeRegex:
    """
    ReDoS-safe regex validation.

    Validates user-provided regex patterns before use to prevent
    catastrophic backtracking (ReDoS) attacks.

    Usage:
        result = SafeRegex.validate(user_pattern)
        if result.safe:
            matches = result.compiled.findall(text)
        else:
            raise ValueError(f"Unsafe regex: {result.reason}")
    """

    @staticmethod
    def validate(
        pattern: str,
        max_length: int = MAX_PATTERN_LENGTH,
        timeout: float = COMPILATION_TIMEOUT_S,
        flags: int = 0,
    ) -> RegexValidation:
        """Validate a regex pattern for safety.

        Checks:
        1. Pattern length
        2. Known dangerous patterns (nested quantifiers, etc.)
        3. Quantifier bounds
        4. Timeout-guarded compilation
        5. Timeout-guarded test match against adversarial input

        Args:
            pattern: Regex pattern string to validate.
            max_length: Maximum allowed pattern length.
            timeout: Timeout in seconds for compilation/testing.
            flags: re flags to use for compilation.

        Returns:
            RegexValidation with safe=True if pattern is safe.
        """
        # Check length
        if len(pattern) > max_length:
            return RegexValidation(
                safe=False, pattern=pattern,
                reason=f"Pattern too long: {len(pattern)} > {max_length}",
            )

        # Check for empty pattern
        if not pattern.strip():
            return RegexValidation(
                safe=False, pattern=pattern,
                reason="Empty pattern",
            )

        # Check known dangerous patterns
        complexity_score = 0.0
        for dangerous_re, reason in _DANGEROUS_PATTERNS:
            try:
                if re.search(dangerous_re, pattern):
                    complexity_score += 1.0
                    if complexity_score >= 2.0:
                        return RegexValidation(
                            safe=False, pattern=pattern,
                            reason=f"High complexity: {reason}",
                            complexity_score=complexity_score,
                        )
            except re.error:
                pass

        # Check quantifier bounds
        quantifier_check = SafeRegex._check_quantifiers(pattern)
        if quantifier_check:
            return RegexValidation(
                safe=False, pattern=pattern,
                reason=quantifier_check,
            )

        # Timeout-guarded compilation
        try:
            compiled = SafeRegex._compile_with_timeout(pattern, flags, timeout)
        except FuturesTimeoutError:
            return RegexValidation(
                safe=False, pattern=pattern,
                reason=f"Compilation timed out after {timeout}s",
                complexity_score=complexity_score,
            )
        except re.error as e:
            return RegexValidation(
                safe=False, pattern=pattern,
                reason=f"Invalid regex: {e}",
            )

        # Timeout-guarded adversarial match test
        test_safe = SafeRegex._test_match_safety(compiled, timeout)
        if not test_safe:
            return RegexValidation(
                safe=False, pattern=pattern,
                reason="Pattern causes excessive backtracking on test input",
                complexity_score=complexity_score + 1.0,
            )

        return RegexValidation(
            safe=True, pattern=pattern,
            compiled=compiled,
            complexity_score=complexity_score,
        )

    @staticmethod
    def _check_quantifiers(pattern: str) -> str:
        """Check for unbounded or excessive quantifiers.

        Returns reason string if unsafe, empty string if ok.
        """
        # Find {n,m} quantifiers
        import re as _re
        for match in _re.finditer(r"\{(\d+)(?:,(\d*))?\}", pattern):
            min_val = int(match.group(1))
            max_group = match.group(2)
            if max_group == "":
                # {n,} — unbounded max
                if min_val > MAX_QUANTIFIER_VALUE:
                    return f"Unbounded quantifier with high min: {{{min_val},}}"
            elif max_group is not None:
                max_val = int(max_group)
                if max_val > MAX_QUANTIFIER_VALUE:
                    return f"Quantifier exceeds limit: {{{min_val},{max_val}}}"
            else:
                # {n} — exact count
                if min_val > MAX_QUANTIFIER_VALUE * 10:
                    return f"Exact quantifier too large: {{{min_val}}}"
        return ""

    @staticmethod
    def _compile_with_timeout(
        pattern: str, flags: int, timeout: float,
    ) -> Pattern:
        """Compile regex with timeout protection."""
        future = _executor.submit(re.compile, pattern, flags)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            raise

    @staticmethod
    def _test_match_safety(compiled: Pattern, timeout: float) -> bool:
        """Test regex against adversarial inputs to detect backtracking.

        Generates inputs known to trigger catastrophic backtracking
        in vulnerable patterns: long strings of repeated chars.
        """
        # Adversarial test inputs
        test_inputs = [
            "a" * 30 + "!",          # Repeated char + non-match
            "ab" * 20 + "!",         # Alternating + non-match
            " " * 30 + "x",          # Spaces + non-match
            "aaaaaaaaaa" * 3 + "b",  # Long repeated + different end
        ]

        def _run_tests() -> bool:
            for test_input in test_inputs:
                try:
                    compiled.search(test_input)
                except Exception:
                    pass
            return True

        future = _executor.submit(_run_tests)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            return False

    @staticmethod
    def compile_safe(
        pattern: str,
        flags: int = 0,
        max_length: int = MAX_PATTERN_LENGTH,
    ) -> Pattern:
        """Compile a regex pattern safely, raising ValueError if unsafe.

        Convenience method that either returns a compiled pattern
        or raises ValueError.

        Args:
            pattern: Regex pattern string.
            flags: re flags.
            max_length: Max pattern length.

        Returns:
            Compiled Pattern object.

        Raises:
            ValueError: If pattern is unsafe or invalid.
        """
        result = SafeRegex.validate(pattern, max_length=max_length, flags=flags)
        if not result.safe:
            raise ValueError(f"Unsafe regex pattern: {result.reason}")
        assert result.compiled is not None
        return result.compiled
