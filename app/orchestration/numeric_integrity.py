"""Numeric integrity: ground report numbers (CC-2) + reconcile conflicts (CC-3).

Audit 2026-06-22 found two cooperating defects with NO code mechanism:
  - CC-2 fabrication: headline numbers in the final report that no script
    produced (488/243 trades in `350d9a50`; hand-typed 26.24 kg/h in `367a9793`).
  - CC-3 no reconciliation: the same quantity carrying ≥2 different values across
    artifacts/report (rice 26.24 vs 30.82 kg/h; operating hours 4800 vs 7200) —
    two files both self-labeled "canonical/LOCKED" yet disagreeing.

Both questions share ONE data source — the structured artifacts the scripts
produced (`_work/*.json|*.csv`). This module reads them once and answers both:

  - ``run_numeric_integrity`` → reconciliation report (CC-3): structured artifact
    values are the SOURCE OF TRUTH; a prose value that disagrees with a structured
    value for the same labelled quantity is a conflict that blocks completion.
  - ``ground_report_numbers`` (CC-2): a report figure carrying a unit that no
    structured artifact produced is annotated ``[UNSOURCED]`` (never removed —
    respects the report's "only ADD" rule; fail-open if no structured source).

Design: structured beats prose, conflict requires BOTH a label match AND a value
divergence (so the same value reused in many places is never a false positive),
and a pure-prose task with no structured artifact never blocks.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import structlog

from app.orchestration.artifact_reexecutor import extract_numbers  # shared extractor

logger = structlog.get_logger(__name__)

_STRUCTURED_SUFFIXES = (".json", ".csv")
_MAX_ARTIFACT_BYTES = 2_000_000
# Relative tolerance for "same quantity, different value" (1%).
_CONFLICT_REL_TOL = 0.01
# Label fuzzy-match threshold (normalized).
_LABEL_SIM = 0.85

# A number in prose, optionally followed by a unit. Units anchor "headline"
# figures (CC-2) and pair a value with a label.
#
# ORDERING MATTERS (audit f188a4c4 G1): alternation is first-match, so multi-char
# units precede the bare single-char fallbacks `m\b`/`s\b`, and ambiguous short
# units are \b-anchored so they never match inside a word ("3 models" → no unit).
_UNIT = (
    r"(?:%|kWe?|kWh|kW|MWe?|MW|kg/h|kg|t/h|tonnes?|tons?|USD|usd|\$|trades?|"
    r"m³/h|°C|bar|"
    r"GHz|MHz|kHz|Hz|"
    r"dBm|dBi|dB|W\b|"
    r"m/s²|m/s2|m/s|"
    r"km\b|cm\b|mm\b|ms\b|µs\b|us\b|"
    r"m\b|s\b)"
)
_PROSE_NUM_UNIT_RE = re.compile(
    r"(?P<num>-?\d[\d,]*\.?\d*)\s*(?P<unit>" + _UNIT + r")?",
)
# label = a few word-ish tokens just before the number (e.g. "power_kwe = ", "throughput reached ")
_LABEL_BEFORE_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*(?:[ _][A-Za-z][A-Za-z0-9_]*){0,3})\s*[:=]?\s*$")


@dataclass
class NumericFact:
    label: str
    value: float
    unit: str = ""
    source: str = ""  # "calc.json", "report:prose", "results.csv:power_kwe"


@dataclass
class NumericIntegrityReport:
    facts: list[NumericFact] = field(default_factory=list)
    conflicts: list[tuple[NumericFact, NumericFact]] = field(default_factory=list)
    has_structured: bool = False

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


# --- normalization helpers --------------------------------------------------

def _norm_label(label: str) -> str:
    return re.sub(r"[\s_\-.]+", "", (label or "").strip().lower())


def _label_tokens(label: str) -> set[str]:
    """Alphanumeric tokens of a label (split on space/underscore/dot/dash)."""
    return {t for t in re.split(r"[\s_\-.]+", (label or "").strip().lower()) if t}


def _labels_match(a: str, b: str) -> bool:
    """``a`` = artifact label, ``b`` = prose label.

    True when they name the same quantity: exact normalized equality, OR the
    artifact label's tokens are all present in the prose label (prose adds noise
    words like "delivers power_kwe"), OR a close fuzzy match. Token-subset is the
    primary rule — fuzzy is only a fallback for spelling variants."""
    na, nb = _norm_label(a), _norm_label(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = _label_tokens(a), _label_tokens(b)
    if ta and ta <= tb:  # every artifact token appears in the prose label
        return True
    return SequenceMatcher(None, na, nb).ratio() >= _LABEL_SIM


def _values_conflict(a: float, b: float) -> bool:
    return abs(a - b) > max(1e-9, _CONFLICT_REL_TOL * max(abs(a), abs(b)))


def _norm_unit(unit: str) -> str:
    """Normalize a unit for compatibility comparison (lowercase, strip 'e'/spaces)."""
    return re.sub(r"[\s]+", "", (unit or "").strip().lower())


def _units_compatible(u1: str, u2: str) -> bool:
    """Two facts are comparable only if their units match — or at least one is
    unitless (we can't tell, so don't block on it). Different explicit units (% vs
    kg) name different quantities → never a numeric conflict."""
    n1, n2 = _norm_unit(u1), _norm_unit(u2)
    if not n1 or not n2:
        return True
    return n1 == n2


# --- structured artifact extraction (source of truth) ----------------------

def _flatten_json(obj, prefix: str, out: list[NumericFact], source: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            label = f"{prefix}.{k}" if prefix else str(k)
            _flatten_json(v, label, out, source)
    elif isinstance(obj, list):
        # Lists of records: descend; lists of scalars: ignore (no label per item).
        for item in obj:
            if isinstance(item, (dict, list)):
                _flatten_json(item, prefix, out, source)
    elif isinstance(obj, bool):
        return  # bools are not measurements
    elif isinstance(obj, (int, float)):
        unit = "%" if prefix.lower().endswith(("pct", "percent")) else ""
        out.append(NumericFact(label=prefix, value=float(obj), unit=unit, source=source))


def _facts_from_json(path: Path) -> list[NumericFact]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, ValueError):
        return []
    out: list[NumericFact] = []
    _flatten_json(data, "", out, source=path.name)
    return out


def _facts_from_csv(path: Path) -> list[NumericFact]:
    out: list[NumericFact] = []
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return []
            for row in reader:
                for col, cell in row.items():
                    if col is None or cell is None:
                        continue
                    nums = extract_numbers(cell)
                    if len(nums) == 1:
                        out.append(NumericFact(
                            label=str(col), value=next(iter(nums)),
                            source=f"{path.name}:{col}",
                        ))
    except (OSError, csv.Error):
        return []
    return out


def build_numeric_facts(work_dir) -> list[NumericFact]:
    """Read every structured artifact (.json/.csv) under ``work_dir`` ONCE and
    return (label, value, unit, source) facts. These are the source of truth."""
    wd = Path(work_dir)
    if not wd.exists():
        return []
    facts: list[NumericFact] = []
    for p in sorted(wd.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix not in _STRUCTURED_SUFFIXES:
            continue
        try:
            if p.stat().st_size > _MAX_ARTIFACT_BYTES:
                continue
        except OSError:
            continue
        if p.suffix == ".json":
            facts.extend(_facts_from_json(p))
        else:
            facts.extend(_facts_from_csv(p))
    return facts


# --- prose extraction (weak source) ----------------------------------------

def extract_prose_facts(text: str, source: str = "report:prose") -> list[NumericFact]:
    """Extract (label, value, unit) triples from prose. Weak source: only used to
    compare against structured facts (CC-3) and to find unsourced figures (CC-2)."""
    out: list[NumericFact] = []
    if not text:
        return out
    for m in _PROSE_NUM_UNIT_RE.finditer(text):
        token = (m.group("num") or "").replace(",", "")
        if token in ("", "-", ".", "-."):
            continue
        try:
            value = float(token)
        except ValueError:
            continue
        unit = m.group("unit") or ""
        before = text[max(0, m.start() - 48):m.start()]
        lm = _LABEL_BEFORE_RE.search(before)
        label = lm.group(1).strip() if lm else ""
        out.append(NumericFact(label=label, value=value, unit=unit, source=source))
    return out


# --- CC-3 reconciliation ----------------------------------------------------

def check_reconciliation(
    artifact_facts: list[NumericFact], prose_facts: list[NumericFact]
) -> list[tuple[NumericFact, NumericFact]]:
    """Conflict ⟺ a prose value does NOT match ANY artifact value for the same
    labelled quantity.

    A structured artifact may hold MANY values under one label (a CSV column /
    JSON list is a time-series), all legitimate. So a prose figure conflicts only
    when it matches none of the artifact values sharing that label — not merely
    because it differs from one of them (that produced false positives on any
    time-series CSV). Structured artifacts are the source of truth.
    """
    conflicts: list[tuple[NumericFact, NumericFact]] = []
    for pf in prose_facts:
        if not pf.label:
            continue
        # All artifact facts whose label names the same quantity AND whose unit is
        # compatible (different explicit units = different quantities, not a conflict).
        matching = [
            af for af in artifact_facts
            if af.label and _labels_match(af.label, pf.label)
            and _units_compatible(af.unit, pf.unit)
        ]
        if not matching:
            continue
        # No conflict if the prose value equals ANY of the artifact values.
        if any(not _values_conflict(pf.value, af.value) for af in matching):
            continue
        # The prose value matches none of them → genuine conflict. Report it
        # against the artifact value closest to the prose value (most informative).
        closest = min(matching, key=lambda af: abs(af.value - pf.value))
        conflicts.append((closest, pf))
    return conflicts


def _source_file(source: str) -> str:
    """The artifact FILE a fact came from — the part before any ``:col`` suffix.

    ``calc.json`` → ``calc.json``; ``revenue.csv:revenue`` → ``revenue.csv``. Two
    facts from the SAME file (a JSON list, a CSV column time-series) share a file
    and are never a cross-artifact conflict; two DIFFERENT files disagreeing on
    the same quantity are.
    """
    return (source or "").split(":", 1)[0]


def check_artifact_conflicts(
    artifact_facts: list[NumericFact],
) -> list[tuple[NumericFact, NumericFact]]:
    """CC-3b: the same labelled quantity carrying ≥2 divergent values across
    DIFFERENT source files (audit tri-model 2026-07-01).

    Two scripts/artifacts both self-labeled authoritative yet disagreeing (LHV
    7.29 vs 5.30; Ø496 vs Ø550) is an internally-contradictory design basis even
    when the report happens to match one of them. This compares structured facts
    against each other — the piece `check_reconciliation` (prose-vs-artifact) never
    covered.

    Guards against false positives:
      - values from the SAME file are a legitimate series (never conflict);
      - units must be compatible (28% vs 28 kg name different quantities);
      - the same value repeated across files is agreement, not conflict.
    """
    # Bucket facts by (normalized-label, normalized-unit) so only same-quantity,
    # same-unit facts are ever compared.
    buckets: dict[tuple[str, str], list[NumericFact]] = {}
    for f in artifact_facts:
        if not f.label:
            continue
        buckets.setdefault((_norm_label(f.label), _norm_unit(f.unit)), []).append(f)

    conflicts: list[tuple[NumericFact, NumericFact]] = []
    for group in buckets.values():
        # Distinct source FILES represented in this quantity's group.
        files = {_source_file(f.source) for f in group}
        if len(files) < 2:
            continue  # single file → a series, not a cross-artifact disagreement
        # Report the widest disagreement in the group (min vs max value) once,
        # but only if those two extremes come from DIFFERENT files and diverge.
        lo = min(group, key=lambda f: f.value)
        hi = max(group, key=lambda f: f.value)
        if (
            _source_file(lo.source) != _source_file(hi.source)
            and _values_conflict(lo.value, hi.value)
        ):
            conflicts.append((lo, hi))
    return conflicts


def run_numeric_integrity(work_dir, report_text: str = "") -> NumericIntegrityReport:
    """Build the structured facts and reconcile them against prose (report + any
    .md notes in work_dir) AND against each other. Fail-open: no structured
    artifact → no conflicts."""
    artifact_facts = build_numeric_facts(work_dir)
    rep = NumericIntegrityReport(facts=artifact_facts, has_structured=bool(artifact_facts))
    if not artifact_facts:
        return rep  # nothing structured to arbitrate against → never block

    # CC-3b: structured-vs-structured disagreement (independent of the report).
    rep.conflicts.extend(check_artifact_conflicts(artifact_facts))

    prose = report_text or ""
    # Also pull prose from any markdown notes in the workspace.
    try:
        wd = Path(work_dir)
        for p in sorted(wd.rglob("*.md")):
            if p.is_file() and "__pycache__" not in p.parts:
                if p.stat().st_size <= _MAX_ARTIFACT_BYTES:
                    prose += "\n" + p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    prose_facts = extract_prose_facts(prose)
    # Append prose-vs-artifact conflicts to the cross-artifact ones already found.
    rep.conflicts.extend(check_reconciliation(artifact_facts, prose_facts))
    return rep


# --- CC-2 grounding ---------------------------------------------------------

def ground_report_numbers(report_text: str, artifact_facts: list[NumericFact]) -> str:
    """Annotate headline figures (numbers WITH a unit) that no structured artifact
    produced with ``[UNSOURCED]``. Fail-open: no structured facts → unchanged."""
    if not report_text or not artifact_facts:
        return report_text

    artifact_values = [f.value for f in artifact_facts]

    def _sourced(value: float) -> bool:
        return any(
            abs(value - av) <= max(1e-9, _CONFLICT_REL_TOL * max(abs(value), abs(av)))
            for av in artifact_values
        )

    def _annotate(m: re.Match) -> str:
        whole = m.group(0)
        unit = m.group("unit") or ""
        if not unit:  # only headline figures (with a unit) are grounded
            return whole
        token = (m.group("num") or "").replace(",", "")
        try:
            value = float(token)
        except ValueError:
            return whole
        if _sourced(value):
            return whole
        return f"{whole} [UNSOURCED]"

    return _PROSE_NUM_UNIT_RE.sub(_annotate, report_text)


# --- gate helpers (mirror tautology_detector shape) -------------------------

def count_conflicts(report: NumericIntegrityReport | None) -> int:
    if not report:
        return 0
    return len(report.conflicts)


def conflict_lines(report: NumericIntegrityReport | None, limit: int = 5) -> list[str]:
    if not report or not report.conflicts:
        return []
    lines = []
    for af, pf in report.conflicts[:limit]:
        lines.append(
            f"Unreconciled quantity '{pf.label or af.label}': {af.value}{af.unit} "
            f"(artifact {af.source}) vs {pf.value}{pf.unit} ({pf.source}) — "
            f"use the script-produced value or explain the difference"
        )
    return lines


def build_conflict_context(report: NumericIntegrityReport | None, limit: int = 5) -> str:
    lines = conflict_lines(report, limit)
    if not lines:
        return ""
    return "## Numeric conflicts (reconcile before completing)\n" + "\n".join(
        f"- {l}" for l in lines
    )
