"""Static detector for self-cancelling ("tautology") verification code in
agent-authored Python scripts.

Audit of task d8b78042 ("verification theater") found a closure check that
*ran*, *looked* rigorous, and reported "PASS" at every operating point — yet
verified nothing, because it was algebraically forced to zero:

    E_chem  = LHV_dry - Q_evap - Q_char - Q_loss - Q_sens   # whole − parts
    E_out   = E_chem  + Q_evap + Q_char + Q_loss + Q_sens   # == LHV_dry by construction
    closure = abs(LHV_dry - E_out) / LHV_dry * 100          # == 0 for ALL inputs

Re-running the script proved it: closure was 0.00% at ER 0.25→0.35 alike.

This detector walks the AST and traces variable definitions to flag exactly that
structure — a closure/error metric comparing a "whole" against a value that was
reconstructed from it. It is deliberately scoped and high-precision: it reports a
*suspect* for a critic to confirm by re-running with perturbed inputs; it never
hard-fails a turn on its own.

Scope note: the complementary failure mode — *back-solve / circular derivation*
(a result reverse-engineered from the target, then used to "prove" the target) —
requires semantic judgement ("is this value later used to validate the same
target?") and is left to the LLM critic, which is explicitly briefed for it. A
noisy static heuristic there would repeat the very mistake the audit flagged
(BUG-08: a gate that looks rigorous but checks the wrong thing).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Names that signal a closure / error / residual verification metric.
_CLOSURE_HINTS = ("closure", "residual", "balance_err", "imbalance", "mismatch")


@dataclass
class TautologySuspect:
    """One flagged construct in a script."""

    kind: str  # currently always "tautology"
    name: str  # the metric variable / key involved
    line: int
    detail: str
    # Runtime confirmation status, set by tautology_prober:
    #   "suspected"  — static structure only (default)
    #   "confirmed"  — re-ran with random inputs, metric did not move
    #   "refuted"    — re-ran, metric responded to inputs (false positive)
    confirmed: str = "suspected"


@dataclass
class ScanResult:
    suspects: list[TautologySuspect] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.suspects


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _subtracted_terms(node: ast.AST) -> tuple[str | None, set[str]]:
    """For ``A - b - c - ...`` return (A_name, {b, c, ...}); else (None, set())."""
    subtracted: set[str] = set()

    def peel(n: ast.AST) -> ast.AST:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub):
            subtracted.update(_names_in(n.right))
            return peel(n.left)
        return n

    base = peel(node)
    if not subtracted:
        return None, set()
    return (base.id if isinstance(base, ast.Name) else None), subtracted


def _added_terms(node: ast.AST) -> set[str]:
    """For ``a + b + c + ...`` return {a, b, c, ...}; else empty set."""
    terms: set[str] = set()

    def peel(n: ast.AST) -> None:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            peel(n.left)
            peel(n.right)
        elif isinstance(n, ast.Name):
            terms.add(n.id)

    peel(node)
    return terms if len(terms) >= 2 else set()


def _normalize_source_expr(node: ast.AST) -> str | None:
    """A canonical, order-insensitive signature of a PURE Name/BinOp expression.

    ``a + b`` and ``b + a`` yield the same signature; anything involving a call,
    subscript, attribute, or literal returns None (we only compare simple algebraic
    definitions). Used to detect two variables defined from the SAME source
    expression — the second self-cancelling shape (R2-TAUTOLOGY, round-2): the
    gasifier compared ``P_gross_needed`` (= P_net + P_par) against ``P_gross_got``
    (= the same P_net + P_par computed elsewhere), forcing the metric to ~0.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
        left = _normalize_source_expr(node.left)
        right = _normalize_source_expr(node.right)
        if left is None or right is None:
            return None
        op = "+" if isinstance(node.op, ast.Add) else "*"
        # commutative → sort operands so a+b == b+a
        return "(" + op.join(sorted([left, right])) + ")"
    return None


def _subtracted_pair(node: ast.AST) -> tuple[str, str] | None:
    """For ``A - B`` (or ``abs(A - B)``) return (A_name, B_name) when both sides are
    single Names; else None. Peels a wrapping ``abs(...)`` / division normalisation
    so ``abs(got - needed) / needed * 100`` still yields (got, needed)."""
    n = node
    # unwrap abs(...) / func(...) with one arg that is the subtraction
    if isinstance(n, ast.Call) and len(n.args) == 1:
        n = n.args[0]
    # unwrap  (A - B) / X * Y  → find the Sub BinOp inside
    def find_sub(x: ast.AST) -> ast.AST | None:
        if isinstance(x, ast.BinOp):
            if isinstance(x.op, ast.Sub):
                return x
            found = find_sub(x.left)
            if found:
                return found
            return find_sub(x.right)
        if isinstance(x, ast.Call) and len(x.args) == 1:
            return find_sub(x.args[0])
        return None

    sub = find_sub(n)
    if sub is None or not isinstance(sub, ast.BinOp):
        return None
    if isinstance(sub.left, ast.Name) and isinstance(sub.right, ast.Name):
        return (sub.left.id, sub.right.id)
    return None


def _closure_exprs(tree: ast.AST) -> list[tuple[str, int, ast.AST]]:
    """Collect (name, lineno, value_expr) for every closure-hinted metric, whether
    written as an assignment, a dict literal entry, or a ``dict(closure=...)`` kwarg.
    """
    found: list[tuple[str, int, ast.AST]] = []
    for node in ast.walk(tree):
        # closure = <expr>
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and any(h in tgt.id.lower() for h in _CLOSURE_HINTS):
                found.append((tgt.id, node.lineno, node.value))
        # {"closure": <expr>}
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=False):
                if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                        and any(h in k.value.lower() for h in _CLOSURE_HINTS)):
                    found.append((k.value, getattr(v, "lineno", node.lineno), v))
        # dict(closure=<expr>)
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and any(h in kw.arg.lower() for h in _CLOSURE_HINTS):
                    found.append((kw.arg, getattr(kw.value, "lineno", node.lineno), kw.value))
    return found


def scan_source(source: str) -> ScanResult:
    """Scan one Python source string for a self-cancelling closure metric.

    Pure function, no I/O. Never raises on bad syntax (returns clean — we only judge
    code we can parse).
    """
    result = ScanResult()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return result

    # whole − parts definitions: var -> (whole, {parts})
    minus_defs: dict[str, tuple[str, set[str]]] = {}
    # additive definitions: var -> {terms}
    plus_defs: dict[str, set[str]] = {}
    # canonical source signature of each simple algebraic definition (R2-TAUTOLOGY).
    # A var may be defined in more than one scope with the same signature; we keep
    # the signature so two vars sharing it can be recognised as the SAME source.
    src_sig: dict[str, str] = {}
    # vars assigned from a subscript of a dict a like-named var was stored into
    # (``P_gross_got = chain["P_gross"]``) alias back to that stored var's signature.
    alias_by_subscript_key: dict[str, str] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            var = node.targets[0].id
            base, parts = _subtracted_terms(node.value)
            if base and parts:
                minus_defs[var] = (base, parts)
            added = _added_terms(node.value)
            if added:
                plus_defs[var] = added
            sig = _normalize_source_expr(node.value)
            if sig is not None:
                src_sig[var] = sig
            # ``x = <dict>["key"]`` → remember key so a value stored under that key
            # elsewhere (``{"key": <expr>}`` / ``dict(key=<expr>)``) aliases here.
            if isinstance(node.value, ast.Subscript):
                sub = node.value.slice
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    alias_by_subscript_key[var] = sub.value
        # collect signatures stored under dict keys so a subscript-alias can resolve
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=False):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    vsig = _normalize_source_expr(v)
                    if vsig is not None:
                        src_sig.setdefault(f"__key__{k.value}", vsig)

    # resolve subscript aliases: P_gross_got = chain["P_gross"] takes the signature
    # stored under key "P_gross".
    for var, key in alias_by_subscript_key.items():
        if var not in src_sig and f"__key__{key}" in src_sig:
            src_sig[var] = src_sig[f"__key__{key}"]

    # Transitive resolution: a signature that is itself a single bare Name pointing
    # at another var with a richer signature is replaced by that richer signature.
    # e.g. dict stored {"P_gross": P_gross} → key sig "P_gross"; P_gross itself is
    # (P_net_target+P_parasitic). So P_gross_got (= chain["P_gross"]) resolves to
    # (P_net_target+P_parasitic), matching P_gross_needed. Bounded iterations avoid
    # any cycle looping forever.
    for _ in range(5):
        changed = False
        for var, sig in list(src_sig.items()):
            if sig in src_sig and src_sig[sig] != sig and sig != var:
                src_sig[var] = src_sig[sig]
                changed = True
        if not changed:
            break

    for cl_name, lineno, expr in _closure_exprs(tree):
        # R2-TAUTOLOGY shape: closure = A − B where A and B were BOTH defined from
        # the same source expression (e.g. both = P_net_target + P_parasitic) →
        # algebraically ~0 for every input.
        pair = _subtracted_pair(expr)
        if pair:
            a, b = pair
            sig_a, sig_b = src_sig.get(a), src_sig.get(b)
            if sig_a is not None and sig_a == sig_b and a != b:
                result.suspects.append(TautologySuspect(
                    kind="tautology",
                    name=cl_name,
                    line=lineno,
                    detail=(
                        f"'{cl_name}' compares '{a}' against '{b}', but BOTH are defined "
                        f"from the same source expression ({sig_a}) — so the metric is "
                        f"algebraically ~0 for every input and verifies nothing. Re-run "
                        f"with perturbed inputs: a real closure must move when inputs change."
                    ),
                ))
                continue
        refs = _names_in(expr)
        for recon, (whole, parts) in minus_defs.items():
            # A reconstructor is `recon` itself (== whole − parts) or any additive
            # var that sums `recon` back together with those same parts (== whole).
            reconstructors = {recon} | {
                y for y, terms in plus_defs.items() if recon in terms and parts & terms
            }
            if whole in refs and (refs & reconstructors):
                result.suspects.append(TautologySuspect(
                    kind="tautology",
                    name=cl_name,
                    line=lineno,
                    detail=(
                        f"'{cl_name}' compares '{whole}' against a value reconstructed from it "
                        f"('{recon}' = {whole} − parts, then summed back) — algebraically forced "
                        f"to ~0 for every input, so the check verifies nothing. Re-run with "
                        f"perturbed inputs: a real closure metric must move when inputs change."
                    ),
                ))
                break
    return result


def scan_workspace_scripts(work_dir, max_files: int = 20) -> dict[str, ScanResult]:
    """Scan ``*.py`` under a work dir. Returns {relative_path: ScanResult} for files
    with at least one suspect. Best-effort; skips unreadable files.
    """
    from pathlib import Path

    work = Path(work_dir)
    out: dict[str, ScanResult] = {}
    if not work.exists():
        return out
    for p in sorted(work.rglob("*.py"))[:max_files]:
        try:
            res = scan_source(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not res.is_clean:
            out[str(p.relative_to(work))] = res
    return out


def count_confirmed_tautologies(scans: dict[str, ScanResult]) -> int:
    """Number of suspects the prober runtime-CONFIRMED as self-cancelling.

    Only ``confirmed == "confirmed"`` counts — a metric re-run with perturbed
    inputs that did NOT move, i.e. proven to verify nothing. "suspected" (static
    only) and "refuted" (responds to inputs → false positive) do NOT count, so
    the completion gate never blocks on an unproven heuristic.
    """
    return sum(
        1
        for res in scans.values()
        for s in res.suspects
        if s.confirmed == "confirmed"
    )


def confirmed_tautology_lines(scans: dict[str, ScanResult], limit: int = 5) -> list[str]:
    """``["path:line — name", ...]`` for runtime-CONFIRMED tautologies.

    Used to spell out, in the orchestrator's ``missing_items``, exactly which
    self-cancelling checks void a "PASS"/"complete" claim.
    """
    out: list[str] = []
    for path, res in scans.items():
        for s in res.suspects:
            if s.confirmed == "confirmed":
                out.append(f"{path}:{s.line} — {s.name}")
                if len(out) >= limit:
                    return out
    return out


def build_suspect_context(scans: dict[str, ScanResult], limit: int = 5) -> str:
    """Render a skepticism block for the orchestrator from workspace scan results.

    Suspects that were runtime-CONFIRMED by the prober (metric never moved under
    varied inputs) are reported as proven facts and listed first; runtime-REFUTED
    ones are dropped (false positives); the rest are reported as suspicions.
    """
    if not scans:
        return ""

    confirmed: list[str] = []
    suspected: list[str] = []
    for path, res in scans.items():
        for s in res.suspects:
            if s.confirmed == "refuted":
                continue  # prover showed the metric responds → not a tautology
            line = f"- {path}:{s.line} — {s.detail}"
            (confirmed if s.confirmed == "confirmed" else suspected).append(line)

    if not confirmed and not suspected:
        return ""

    lines = ["## ⚠️ VERIFICATION AUDIT — self-cancelling check(s) detected"]
    if confirmed:
        lines.append(
            "PROVEN by re-execution: the following metric(s) stayed CONSTANT across "
            "randomised inputs — they verify NOTHING. Any 'PASS'/'closure'/'đã xác minh' "
            "verdict resting on them is void; the underlying claim is UNVERIFIED:"
        )
        lines.extend(confirmed[:limit])
    if suspected:
        lines.append(
            "STRUCTURALLY SUSPECT (re-run with perturbed inputs to confirm the metric "
            "responds before trusting any PASS):"
        )
        lines.extend(suspected[: max(0, limit - len(confirmed))])
    return "\n".join(lines)
