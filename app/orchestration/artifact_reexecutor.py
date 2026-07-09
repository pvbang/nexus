"""Re-execute runnable artifacts and compare claimed vs. observed numbers (CC-1).

Audit 2026-06-22 found the completion gate never re-ran a deliverable: it trusted
file-exists + byte-size + keyword substring. A report could claim "488 trades"
while the script that supposedly produced it outputs 58 (`350d9a50`).

This module re-runs the runnable artifacts an agent left in ``_work/`` and checks
whether the headline numbers the agent CLAIMED actually appear in the script's
real stdout.

Design contract (matches project memory `verification-theater` +
`capability-acquisition-doctrine`):
  - FAIL-CLOSED on a genuine mismatch: a claimed number with no match in the
    script's output is a real integrity failure → ``status="mismatch"`` (counted,
    blocks completion upstream).
  - FAIL-OPEN HONESTLY when re-execution is impossible (no sandbox/interpreter,
    timeout, crash before producing output): ``status="unverified"`` — surfaced as
    a skepticism note but NOT counted as a blocker and NEVER auto-passed.

Mirrors ``tautology_detector``'s scan/count/context helper shape so the pipeline
gate consumes it uniformly.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Only re-run small, recently-touched scripts. A re-exec re-runs code the agent
# already ran in this same workspace, so we add no new capability/risk — but we
# still bound cost and blast radius.
_MAX_FILES = 5
_MAX_FILE_BYTES = 200_000
_REEXEC_TIMEOUT_S = 15
_RUNNABLE_SUFFIXES = (".py", ".sh")
# Directories that hold third-party / VCS code we must never re-execute or walk.
_SKIP_DIRS = frozenset({
    "__pycache__", "venv", ".venv", "env", "site-packages", "node_modules",
    ".git", ".tox", "dist", "build", ".pytest_cache", ".mypy_cache",
})

# Numbers an agent "claims" — integers/decimals, optionally with thousands commas.
# Shared with numeric_integrity (single extractor, single source of truth).
_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")

# A "headline" claim is a number with a UNIT attached (28%, 488 trades, 14.3 kWe,
# $1200, 421 m, 13 dB, 9.5 GHz). Bare numbers (sharpe 1.5, index 3, a year) are too
# noisy to treat as a contradicted result, so re-exec compares only unit-bearing
# headline numbers.
#
# ORDERING MATTERS (audit f188a4c4 G1): regex alternation is first-match, so every
# multi-char unit MUST appear before the bare single-char fallbacks `m\b`/`s\b` —
# otherwise "150 mm" matches "150 m" and "20 m/s" matches "20 m". Ambiguous short
# units (m, s) are anchored with \b so they never match inside a word ("3 models"
# must NOT yield 3 m).
#
# CASE MATTERS (final-review Minor 1): the metric SI symbols are split into a
# CASE-SENSITIVE group `(?-i:...)`. SI symbols are case-defined (metre=m,
# second=s, watt=W), so making them case-sensitive stops `3M` (3 million),
# `2.0S` (a version), and `US`/`MM` shorthand from colliding with metre/second/
# micro-second. The distinctive units below (dB, Hz, kg, USD) stay
# case-insensitive (re.IGNORECASE) so `db`/`hz`/`KG` variants still match.
_HEADLINE_UNIT_CI = (
    # currency / counts / existing (case-insensitive: KG, USD, db, hz all fine)
    r"%|kWe|kWh|kW|MWe|MW|kg/h|kg|t/h|tonnes?|tons?|USD|usd|\$|trades?|"
    r"m3/h|m³/h|bar|°C|hours?|"
    r"GHz|MHz|kHz|Hz|"
    r"dBm|dBi|dB"
)
_HEADLINE_UNIT_CS = (
    # metric symbols — CASE-SENSITIVE, longest-first, \b-anchored.
    r"m/s²|m/s2|m/s|km\b|cm\b|mm\b|ms\b|µs\b|us\b|W\b|m\b|s\b|h\b"
)
_HEADLINE_UNIT = _HEADLINE_UNIT_CI + r"|(?-i:" + _HEADLINE_UNIT_CS + r")"
_HEADLINE_RE = re.compile(r"(-?\d[\d,]*\.?\d*)\s*(?:" + _HEADLINE_UNIT + r")", re.IGNORECASE)


def extract_numbers(text: str) -> set[float]:
    """Pull the distinct numeric values out of free text (commas stripped)."""
    out: set[float] = set()
    for m in _NUMBER_RE.findall(text or ""):
        token = m.replace(",", "")
        if token in ("", "-", ".", "-."):
            continue
        try:
            out.add(float(token))
        except ValueError:
            continue
    return out


def extract_headline_numbers(text: str) -> set[float]:
    """Distinct numeric values that carry a unit (the agent's claimed RESULTS)."""
    out: set[float] = set()
    for m in _HEADLINE_RE.finditer(text or ""):
        token = m.group(1).replace(",", "")
        if token in ("", "-", ".", "-."):
            continue
        try:
            out.add(float(token))
        except ValueError:
            continue
    return out


def _numbers_match(claimed: float, observed: set[float]) -> bool:
    """Relative-tolerance match (handles float formatting differences)."""
    for obs in observed:
        if abs(claimed - obs) <= max(1e-9, 1e-3 * abs(claimed)):
            return True
    return False


@dataclass
class ReExecResult:
    rel_path: str
    status: str  # "match" | "mismatch" | "error" | "unverified"
    claimed_numbers: set = field(default_factory=set)
    observed_numbers: set = field(default_factory=set)
    detail: str = ""


@dataclass
class ReExecScan:
    results: list[ReExecResult] = field(default_factory=list)

    @property
    def has_mismatch(self) -> bool:
        return any(r.status == "mismatch" for r in self.results)


def count_mismatches(scan: ReExecScan | None) -> int:
    """Number of artifacts whose claimed numbers contradict their real output."""
    if not scan:
        return 0
    return sum(1 for r in scan.results if r.status == "mismatch")


def mismatch_lines(scan: ReExecScan | None, limit: int = 5) -> list[str]:
    """Human-readable lines for the missing_items list (mismatches only)."""
    if not scan:
        return []
    lines = []
    for r in scan.results:
        if r.status == "mismatch":
            lines.append(
                f"artifact `{r.rel_path}` re-executed: claimed numbers "
                f"{sorted(r.claimed_numbers)} not found in real output "
                f"{sorted(r.observed_numbers)}"
            )
    return lines[:limit]


def build_reexec_context(scan: ReExecScan | None) -> str:
    """Skepticism context block for the verifier/orchestrator (all statuses)."""
    if not scan or not scan.results:
        return ""
    parts = ["## Artifact re-execution"]
    for r in scan.results:
        if r.status == "mismatch":
            parts.append(f"- ⚠️ MISMATCH `{r.rel_path}`: {r.detail}")
        elif r.status == "error":
            parts.append(f"- ⚠️ ERROR `{r.rel_path}`: {r.detail}")
        elif r.status == "unverified":
            parts.append(f"- ❔ UNVERIFIED `{r.rel_path}`: {r.detail}")
        else:
            parts.append(f"- ✅ match `{r.rel_path}`")
    return "\n".join(parts)


def _is_runnable_script(path: Path) -> bool:
    """A .py worth running has a top-level call/print or __main__ guard; a
    definition-only module (only def/class/import) has nothing to compare."""
    if path.suffix == ".sh":
        return True
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        # Can't parse → let it through; re-exec will surface a real error/unverified.
        return True
    for node in tree.body:
        if isinstance(node, (ast.Expr, ast.Call)):
            return True
        if isinstance(node, ast.If):
            test = node.test
            # if __name__ == "__main__":
            if isinstance(test, ast.Compare) and any(
                isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators
            ):
                return True
    return False


def _unverified(error: str) -> bool:
    """Does a sandbox error indicate the environment couldn't run the script
    (vs. the script itself failing)?"""
    low = (error or "").lower()
    markers = ("command not found", "no such file", "not recognized",
               "cannot find", "timed out", "docker", "no interpreter",
               "permission denied")
    return any(m in low for m in markers)


def _select_artifacts(work_dir: Path) -> list[Path]:
    if not work_dir or not work_dir.exists():
        return []
    candidates: list[Path] = []
    for p in work_dir.rglob("*"):
        if not p.is_file() or _SKIP_DIRS.intersection(p.parts):
            continue
        if p.suffix not in _RUNNABLE_SUFFIXES:
            continue
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        if not _is_runnable_script(p):
            continue
        candidates.append(p)
    # Newest first — re-run what this turn most likely produced.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:_MAX_FILES]


async def reexecute_runnable_artifacts(
    work_dir: Path,
    claimed_outputs: list[str],
    sandbox: Any,
    max_files: int = _MAX_FILES,
) -> ReExecScan:
    """Re-run runnable artifacts in ``work_dir`` and compare claimed vs observed.

    ``claimed_outputs`` are the agent outputs / ledger key_data for the turn — the
    numbers the agent ASSERTED. ``sandbox`` is anything exposing
    ``execute_in_project(project_path, command, timeout)`` (SandboxManager).
    """
    scan = ReExecScan()
    artifacts = _select_artifacts(Path(work_dir))[:max_files]
    if not artifacts:
        return scan

    # Compare only the agent's HEADLINE claims (numbers with a unit) — these are
    # the asserted results. Bare numbers (sharpe ratios, indices, years) are too
    # noisy. Also drop year-like integers that slipped in with a unit.
    claimed_numbers = set()
    for txt in (claimed_outputs or []):
        claimed_numbers |= extract_headline_numbers(txt)

    def _is_year(n: float) -> bool:
        return float(n).is_integer() and 1900 <= n <= 2100
    claimed_numbers = {n for n in claimed_numbers if abs(n) >= 1 and not _is_year(n)}

    for path in artifacts:
        rel = str(path.relative_to(work_dir)) if path.is_relative_to(work_dir) else path.name
        cmd = f'python "{path.name}"' if path.suffix == ".py" else f'sh "{path.name}"'
        try:
            result = await sandbox.execute_in_project(
                project_path=str(path.parent),
                command=cmd,
                timeout=_REEXEC_TIMEOUT_S,
            )
        except Exception as e:  # sandbox itself unavailable → unverified
            scan.results.append(ReExecResult(
                rel_path=rel, status="unverified",
                detail=f"could not re-execute (sandbox error: {str(e)[:120]})",
            ))
            continue

        output = getattr(result, "output", "") or ""
        error = getattr(result, "error", "") or ""
        success = getattr(result, "success", False)

        if not success:
            if _unverified(error):
                scan.results.append(ReExecResult(
                    rel_path=rel, status="unverified",
                    detail=f"could not re-execute (no sandbox/interpreter): {error[:120]}",
                ))
            else:
                scan.results.append(ReExecResult(
                    rel_path=rel, status="error",
                    detail=f"script failed on re-execution: {error[:160]}",
                ))
            continue

        observed = extract_numbers(output)
        # A claimed headline number is contradicted if it is ABSENT from the real
        # output. Flag a mismatch when ANY claimed number is missing — not only
        # when ALL are (the old rule let a lie like "488 trades" slip through just
        # because an unrelated coincidence, e.g. a win-rate 55, happened to match).
        # We only act when the script actually produced numbers to compare against
        # (empty output → nothing to contradict, handled as match/unverified).
        unmatched = [c for c in claimed_numbers if not _numbers_match(c, observed)]
        if claimed_numbers and observed and unmatched:
            scan.results.append(ReExecResult(
                rel_path=rel, status="mismatch",
                claimed_numbers=set(unmatched),
                observed_numbers=observed,
                detail=(f"claimed {sorted(unmatched)} not found in script output "
                        f"{sorted(observed)}"),
            ))
        else:
            scan.results.append(ReExecResult(
                rel_path=rel, status="match",
                claimed_numbers=set(claimed_numbers),
                observed_numbers=observed,
            ))

    return scan
