"""
MekongAI Nexus - Tool Argument Type Coercion (Hermes-agent inspired).

Auto-fix common LLM type errors in tool call arguments.
LLMs frequently return numbers as strings ("42" instead of 42),
booleans as strings ("true" instead of true), etc.

This module coerces tool arguments to match the JSON Schema
defined by each tool, preventing unnecessary tool failures.

Inspired by: hermes-agent/model_tools.py coerce_tool_args()
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


def coerce_tool_args(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Coerce tool arguments to match the expected JSON Schema types.

    Handles common LLM mistakes:
    - ``"42"`` → ``42`` (string to integer)
    - ``"3.14"`` → ``3.14`` (string to number)
    - ``"true"`` / ``"false"`` → ``True`` / ``False`` (string to boolean)
    - ``"[1,2,3]"`` → ``[1,2,3]`` (JSON-string to array)
    - ``"{\\"a\\": 1}"`` → ``{"a": 1}`` (JSON-string to object)
    - ``42`` → ``"42"`` (number to string when string expected)
    - ``null`` → default value from schema (when available)

    Parameters
    ----------
    args:
        Raw arguments dict from LLM tool_call.
    schema:
        Tool's JSON Schema (the ``parameters`` block from the function def).

    Returns
    -------
    dict:
        Coerced arguments. Original dict is NOT mutated.
    """
    if not args or not schema:
        return args

    properties = schema.get("properties", {})
    if not properties:
        return args

    coerced = dict(args)
    coercion_count = 0

    for key, value in list(coerced.items()):
        if key not in properties:
            continue

        prop_schema = properties[key]
        expected_type = prop_schema.get("type")
        if not expected_type:
            continue

        new_value = _coerce_value(value, expected_type, prop_schema)
        if new_value is not value:  # identity check — changed
            coerced[key] = new_value
            coercion_count += 1

    # Fill missing required fields with defaults (if schema provides them)
    required = set(schema.get("required", []))
    for key in required:
        if key not in coerced or coerced[key] is None:
            prop_schema = properties.get(key, {})
            default = prop_schema.get("default")
            if default is not None:
                coerced[key] = default
                coercion_count += 1

    if coercion_count > 0:
        logger.debug("tool_args_coerced", count=coercion_count, keys=list(args.keys()))

    return coerced


def _coerce_value(
    value: Any,
    expected_type: str,
    prop_schema: dict[str, Any],
) -> Any:
    """Coerce a single value to the expected type.

    Returns the original value unchanged if coercion is not possible.
    """
    if value is None:
        default = prop_schema.get("default")
        if default is not None:
            return default
        return value

    # Handle oneOf / anyOf by trying each
    if "oneOf" in prop_schema or "anyOf" in prop_schema:
        return value  # too complex to auto-coerce safely

    # ── String → Integer ─────────────────────────────────────────
    if expected_type == "integer" and isinstance(value, str):
        return _str_to_int(value)

    # ── String → Number (float) ──────────────────────────────────
    if expected_type == "number" and isinstance(value, str):
        return _str_to_number(value)

    # ── String → Boolean ─────────────────────────────────────────
    if expected_type == "boolean" and isinstance(value, str):
        return _str_to_bool(value)

    # ── String → Array ───────────────────────────────────────────
    if expected_type == "array" and isinstance(value, str):
        return _str_to_array(value)

    # ── String → Object ──────────────────────────────────────────
    if expected_type == "object" and isinstance(value, str):
        return _str_to_object(value)

    # ── Number → String ──────────────────────────────────────────
    if expected_type == "string" and isinstance(value, (int, float)):
        return str(value)

    # ── Bool → String ────────────────────────────────────────────
    if expected_type == "string" and isinstance(value, bool):
        return str(value).lower()

    # ── Integer (float with .0) → Integer ────────────────────────
    if expected_type == "integer" and isinstance(value, float):
        if value == int(value):
            return int(value)

    # ── Bool → Integer (0/1) ─────────────────────────────────────
    if expected_type == "integer" and isinstance(value, bool):
        return int(value)

    return value


# ── Conversion helpers ───────────────────────────────────────────────


def _str_to_int(value: str) -> Any:
    """Try converting string to int."""
    stripped = value.strip()
    if not stripped:
        return value
    try:
        return int(stripped)
    except ValueError:
        # Try float then truncate: "3.0" → 3
        try:
            f = float(stripped)
            if f == int(f):
                return int(f)
        except ValueError:
            pass
    return value


def _str_to_number(value: str) -> Any:
    """Try converting string to float/int."""
    stripped = value.strip()
    if not stripped:
        return value
    try:
        f = float(stripped)
        # Return int if it's a whole number
        if f == int(f) and "." not in stripped and "e" not in stripped.lower():
            return int(f)
        return f
    except ValueError:
        return value


def _str_to_bool(value: str) -> Any:
    """Try converting string to boolean."""
    lower = value.strip().lower()
    if lower in ("true", "1", "yes", "on"):
        return True
    if lower in ("false", "0", "no", "off"):
        return False
    return value


def _str_to_array(value: str) -> Any:
    """Try parsing string as JSON array."""
    stripped = value.strip()
    if not stripped.startswith("["):
        return value
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return value


def _str_to_object(value: str) -> Any:
    """Try parsing string as JSON object."""
    stripped = value.strip()
    if not stripped.startswith("{"):
        return value
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return value
