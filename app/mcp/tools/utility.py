"""
MekongAI Nexus - Utility Tools.

General-purpose tools: calculate, current_datetime, hash_encode, system_info.
Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import operator
import os
import platform
import sys
from datetime import datetime, timezone
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Safe Math Evaluator (no exec/eval)
# ------------------------------------------------------------------

_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "int": int,
    "float": float,
    "sum": sum,
    "len": len,
}

# Math module functions (safe subset)
try:
    import math
    _SAFE_FUNCTIONS.update({
        "sqrt": math.sqrt,
        "ceil": math.ceil,
        "floor": math.floor,
        "log": math.log,
        "log10": math.log10,
        "log2": math.log2,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "pi": math.pi,
        "e": math.e,
        "pow": math.pow,
        "factorial": math.factorial,
        "gcd": math.gcd,
    })
except ImportError:
    pass


def _safe_eval_node(node: ast.AST) -> Any:
    """Recursively evaluate an AST node safely (no exec)."""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported constant: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        # Prevent huge exponents
        if op_type is ast.Pow and isinstance(right, (int, float)) and abs(right) > 1000:
            raise ValueError("Exponent too large (max 1000)")
        return _SAFE_OPERATORS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        return _SAFE_OPERATORS[op_type](_safe_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCTIONS:
            args = [_safe_eval_node(a) for a in node.args]
            func = _SAFE_FUNCTIONS[node.func.id]
            if callable(func):
                return func(*args)
            return func  # constants like pi, e
        raise ValueError(f"Unsupported function: {ast.dump(node.func)}")
    if isinstance(node, ast.Name):
        if node.id in _SAFE_FUNCTIONS:
            val = _SAFE_FUNCTIONS[node.id]
            if not callable(val):
                return val  # pi, e
        raise ValueError(f"Unknown variable: {node.id}")
    if isinstance(node, ast.Tuple):
        return tuple(_safe_eval_node(e) for e in node.elts)
    if isinstance(node, ast.List):
        return [_safe_eval_node(e) for e in node.elts]
    raise ValueError(f"Unsupported expression: {type(node).__name__}")


def safe_calculate(expression: str) -> float | int:
    """Safely evaluate a math expression without exec/eval."""
    tree = ast.parse(expression.strip(), mode="eval")
    return _safe_eval_node(tree)


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


def _handle_calculate(args: dict[str, Any]) -> str:
    """Safely evaluate a mathematical expression."""
    expression = args.get("expression", "").strip()
    if not expression:
        return tool_error("Expression is required")

    if len(expression) > 500:
        return tool_error("Expression too long (max 500 chars)")

    try:
        result = safe_calculate(expression)
        return tool_result(
            success=True,
            output=f"🔢 {expression} = {result}",
            data={"expression": expression, "result": result},
        )
    except Exception as e:
        return tool_error(f"Calculation failed: {e}")


def _handle_current_datetime(args: dict[str, Any]) -> str:
    """Get current date/time in various formats."""
    tz_name = args.get("timezone", "UTC")
    fmt = args.get("format", "iso")

    now_utc = datetime.now(timezone.utc)

    if fmt == "timestamp":
        output = str(int(now_utc.timestamp()))
    elif fmt == "date":
        output = now_utc.strftime("%Y-%m-%d")
    elif fmt == "time":
        output = now_utc.strftime("%H:%M:%S")
    elif fmt == "human":
        output = now_utc.strftime("%B %d, %Y at %H:%M:%S UTC")
    else:
        output = now_utc.isoformat()

    return tool_result(
        success=True,
        output=f"🕐 Current time: {output}",
        data={
            "iso": now_utc.isoformat(),
            "timestamp": int(now_utc.timestamp()),
            "formatted": output,
            "timezone": tz_name,
        },
    )


def _handle_hash_encode(args: dict[str, Any]) -> str:
    """Hash or encode/decode text."""
    text = args.get("text", "")
    operation = args.get("operation", "sha256").lower()

    if not text:
        return tool_error("Text is required")

    try:
        if operation in ("md5", "sha1", "sha256", "sha512"):
            h = hashlib.new(operation)
            h.update(text.encode("utf-8"))
            result = h.hexdigest()
        elif operation == "base64_encode":
            result = base64.b64encode(text.encode("utf-8")).decode("ascii")
        elif operation == "base64_decode":
            result = base64.b64decode(text.encode("ascii")).decode("utf-8")
        elif operation == "url_encode":
            from urllib.parse import quote
            result = quote(text, safe="")
        elif operation == "url_decode":
            from urllib.parse import unquote
            result = unquote(text)
        elif operation == "hex_encode":
            result = text.encode("utf-8").hex()
        elif operation == "hex_decode":
            result = bytes.fromhex(text).decode("utf-8")
        else:
            return tool_error(
                f"Unknown operation: {operation}. "
                "Supported: md5, sha1, sha256, sha512, base64_encode, base64_decode, "
                "url_encode, url_decode, hex_encode, hex_decode"
            )

        return tool_result(
            success=True,
            output=f"🔐 {operation}:\n{result}",
            data={"operation": operation, "input_length": len(text), "result": result},
        )
    except Exception as e:
        return tool_error(f"Operation failed: {e}")


def _handle_system_info(args: dict[str, Any]) -> str:
    """Get system information (CPU, RAM, disk, Python version)."""
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "python_version": sys.version,
        "architecture": platform.machine(),
        "hostname": platform.node(),
        "cpu_count": os.cpu_count(),
    }

    # Memory info (cross-platform)
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        info["disk_total_gb"] = round(total / (1024**3), 1)
        info["disk_used_gb"] = round(used / (1024**3), 1)
        info["disk_free_gb"] = round(free / (1024**3), 1)
    except Exception:
        pass

    # Process memory
    try:
        import resource  # Unix only
        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        info["process_memory_mb"] = round(mem_mb, 1)
    except (ImportError, AttributeError):
        pass

    lines = ["💻 **System Information:**\n"]
    for k, v in info.items():
        lines.append(f"  - **{k}**: {v}")

    return tool_result(
        success=True,
        output="\n".join(lines),
        data=info,
    )


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="calculate",
    toolset="utility",
    schema={
        "description": (
            "Safely evaluate a mathematical expression. Supports: +, -, *, /, **, //, %, "
            "sqrt(), log(), sin(), cos(), abs(), round(), min(), max(), pi, e, factorial(). "
            "Use this instead of guessing numbers — it gives exact results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression (e.g., '2**10', 'sqrt(144)', '3.14 * 5**2')",
                },
            },
            "required": ["expression"],
        },
    },
    handler=_handle_calculate,
    emoji="🔢",
)

registry.register(
    name="current_datetime",
    toolset="utility",
    schema={
        "description": "Get current date and time. Use when you need to know the current time, date, or timestamp.",
        "parameters": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["iso", "timestamp", "date", "time", "human"],
                    "description": "Output format (default: iso)",
                },
            },
        },
    },
    handler=_handle_current_datetime,
    emoji="🕐",
)

registry.register(
    name="hash_encode",
    toolset="utility",
    schema={
        "description": (
            "Hash text or encode/decode between formats. "
            "Supports: md5, sha1, sha256, sha512, base64_encode, base64_decode, "
            "url_encode, url_decode, hex_encode, hex_decode."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to process"},
                "operation": {
                    "type": "string",
                    "enum": [
                        "md5", "sha1", "sha256", "sha512",
                        "base64_encode", "base64_decode",
                        "url_encode", "url_decode",
                        "hex_encode", "hex_decode",
                    ],
                    "description": "Hash/encode operation (default: sha256)",
                },
            },
            "required": ["text", "operation"],
        },
    },
    handler=_handle_hash_encode,
    emoji="🔐",
)

registry.register(
    name="system_info",
    toolset="utility",
    schema={
        "description": "Get system information: OS, CPU, RAM, disk, Python version.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=_handle_system_info,
    emoji="💻",
)
