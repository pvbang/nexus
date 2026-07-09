"""Runtime confirmation for tautology suspects — re-execute the flagged metric.

The static detector (``tautology_detector``) flags a closure/error metric whose
two compared sides are structurally identical. This prober *proves* it the way a
critic would: it isolates the metric's arithmetic dependency slice, evaluates it
under several DISTINCT input vectors, and checks whether the result moves.

  - metric constant across all input vectors → CONFIRMED tautology (verifies nothing)
  - metric responds to inputs                → REFUTED (the static flag was a false positive)
  - slice not purely arithmetic / unanalysable → left SUSPECTED (can't safely judge)

Safety: this never uses ``eval``/``exec`` and never imports the agent's module. It
walks the AST with a tiny interpreter that supports ONLY numeric literals, the
four arithmetic ops (+ − * / // % **), unary ±, and the pure builtins abs/min/max.
Any subscript, attribute, call to anything else, or name it can't resolve makes it
bail out to SUSPECTED — it judges only code it can fully and safely reproduce.

Inputs are deterministic distinct vectors (not RNG), so a confirmation is
reproducible and tests never flake.
"""

from __future__ import annotations

import ast

from app.orchestration.tautology_detector import (
    ScanResult,
    TautologySuspect,
    _closure_exprs,
)

_ALLOWED_FUNCS = {"abs": abs, "min": min, "max": max}


class _UnsupportedError(Exception):
    """Raised when an expression contains a node the safe interpreter won't touch."""


def _subscript_key(node: ast.AST) -> str | None:
    """Canonical key for a ``name[const]`` subscript (e.g. ``r['nt']``), else None.

    Such a subscript is an opaque input value — it can't be resolved from the
    arithmetic slice, so it's treated as a free variable to randomise. Same key →
    same value within a trial, which is exactly the desired semantics."""
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant)):
        return f"{node.value.id}[{node.slice.value!r}]"
    return None


def _eval(node: ast.AST, env: dict[str, float]) -> float:
    """Evaluate a pure-arithmetic AST node against an environment. Raises
    ``_UnsupportedError`` for anything outside the numeric allowlist."""
    sub = _subscript_key(node)
    if sub is not None:
        if sub in env:
            return env[sub]
        raise _UnsupportedError
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _UnsupportedError
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise _UnsupportedError
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, env), _eval(node.right, env)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
        if isinstance(op, ast.Pow):
            return left ** right
        raise _UnsupportedError
    if isinstance(node, ast.Call):
        if (isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS
                and not node.keywords):
            return _ALLOWED_FUNCS[node.func.id](*[_eval(a, env) for a in node.args])
        raise _UnsupportedError
    raise _UnsupportedError


def _referenced_names(node: ast.AST) -> set[str]:
    """Names this expression depends on, treating ``name[const]`` as a single opaque
    atom (its key) rather than the bare ``name`` — so ``r['nt']`` and ``r['V']`` are
    distinct free inputs, and ``r`` alone is never a dependency."""
    names: set[str] = set()
    subscript_bases: set[ast.AST] = set()
    for n in ast.walk(node):
        key = _subscript_key(n)
        if key is not None:
            names.add(key)
            subscript_bases.add(n.value)  # the Name inside the subscript
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n not in subscript_bases:
            names.add(n.id)
    return names


def _simple_assignments(tree: ast.AST) -> dict[str, ast.AST]:
    """name -> RHS expr, last write wins, for ``name = <expr>`` only."""
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            out[node.targets[0].id] = node.value
    return out


def _build_slice(
    metric_expr: ast.AST,
    assignments: dict[str, ast.AST],
) -> tuple[list[str], set[str]] | None:
    """Resolve the transitive arithmetic dependency slice of ``metric_expr``.

    Returns (ordered_assignment_vars, free_vars) where ordered vars are
    topologically sorted (dependencies first), or None if the slice can't be
    safely reproduced (cycle, or a dependency that isn't a simple assignment we
    keep — free vars are fine, those become randomised inputs).
    """
    order: list[str] = []
    visiting: set[str] = set()
    done: set[str] = set()
    free: set[str] = set()

    def visit(name: str) -> bool:
        if name in done:
            return True
        if name in visiting:
            return False  # cycle
        if name not in assignments:
            free.add(name)
            return True
        visiting.add(name)
        for dep in _referenced_names(assignments[name]):
            if not visit(dep):
                return False
        visiting.discard(name)
        done.add(name)
        order.append(name)
        return True

    for n in _referenced_names(metric_expr):
        if not visit(n):
            return None
    return order, free


def _input_vectors(free_vars: list[str], trials: int = 6) -> list[dict[str, float]]:
    """Deterministic, distinct input vectors. Each var gets a different value in
    each trial (co-prime-ish steps), magnitudes vary across trials — so a metric
    only stays constant if it's algebraically forced, not by coincidence. Values
    are >= 1 to avoid division-by-zero on denominators."""
    vectors: list[dict[str, float]] = []
    for k in range(trials):
        vec: dict[str, float] = {}
        for i, var in enumerate(free_vars):
            vec[var] = 1.0 + (i + 1) * (k + 1) * 1.7 + (k * k)
        vectors.append(vec)
    return vectors


def probe_suspect(source: str, suspect: TautologySuspect) -> str:
    """Re-execute a single suspect's metric. Returns "confirmed" | "refuted" |
    "suspected" (unchanged if it can't be safely reproduced)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "suspected"

    assignments = _simple_assignments(tree)
    metric_expr = next(
        (expr for name, line, expr in _closure_exprs(tree)
         if name == suspect.name and line == suspect.line),
        None,
    )
    if metric_expr is None:
        return "suspected"

    built = _build_slice(metric_expr, assignments)
    if built is None:
        return "suspected"
    order, free = built
    if not free:
        return "suspected"  # nothing to vary → can't demonstrate

    results: list[float] = []
    for vec in _input_vectors(sorted(free)):
        env = dict(vec)
        try:
            for var in order:
                env[var] = _eval(assignments[var], env)
            results.append(_eval(metric_expr, env))
        except (_UnsupportedError, ZeroDivisionError, OverflowError, ValueError):
            return "suspected"  # not safely reproducible → don't over-claim

    spread = max(results) - min(results)
    scale = 1.0 + sum(abs(r) for r in results) / len(results)
    return "confirmed" if spread < 1e-9 * scale else "refuted"


def probe_scan(source: str, scan: ScanResult) -> ScanResult:
    """Annotate each suspect in a scan with its runtime confirmation status."""
    for s in scan.suspects:
        s.confirmed = probe_suspect(source, s)
    return scan
