"""Reflection-based dependency injection: resolve ``module:attr`` paths.

Lets configuration name a provider/tool/class by an import path string
(``"app.mcp.tool_search:search_tools"``, ``"langfuse:Langfuse"``) instead of
hard-wiring it. When a referenced module is missing because an optional
dependency isn't installed, the error carries an actionable install hint
(``uv add <pkg>``) rather than a raw ImportError.

Mirrors deer-flow's ``reflection/resolvers.py`` (resolve_variable / resolve_class
+ MODULE_TO_PACKAGE_HINTS).
"""

from __future__ import annotations

import importlib
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class ReflectionError(Exception):
    """Raised when a ``module:attr`` path cannot be resolved."""


# Top-level module name → pip/uv package to suggest when the import fails.
MODULE_TO_PACKAGE_HINTS: dict[str, str] = {
    "langfuse": "langfuse",
    "opentelemetry": "opentelemetry-sdk",
    "langsmith": "langsmith",
    "anthropic": "anthropic",
}


def _install_hint(module_path: str) -> str:
    top = module_path.split(".")[0]
    pkg = MODULE_TO_PACKAGE_HINTS.get(top, top)
    return f"Install it with `uv add {pkg}` (or `pip install {pkg}`)."


def resolve_variable(path: str, expected_type: Optional[type] = None) -> Any:
    """Resolve a ``"module.sub:attr.nested"`` path to the referenced object.

    The part before ``:`` is imported as a module; the part after is walked as
    a (possibly dotted) attribute chain. Raises ReflectionError with an install
    hint when the module is missing.
    """
    if not isinstance(path, str) or ":" not in path:
        raise ReflectionError(
            f"Invalid reference {path!r}: expected 'module.path:attribute'."
        )

    module_path, _, attr_path = path.partition(":")
    module_path = module_path.strip()
    attr_path = attr_path.strip()
    if not module_path or not attr_path:
        raise ReflectionError(f"Invalid reference {path!r}: empty module or attribute.")

    try:
        obj: Any = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        raise ReflectionError(
            f"Cannot resolve {path!r}: module {module_path!r} not found. {_install_hint(module_path)}"
        ) from exc
    except ImportError as exc:
        raise ReflectionError(f"Cannot import {module_path!r} for {path!r}: {exc}") from exc

    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise ReflectionError(
                f"Cannot resolve {path!r}: {module_path!r} has no attribute {part!r}."
            ) from exc

    if expected_type is not None and not isinstance(obj, expected_type):
        raise ReflectionError(
            f"{path!r} resolved to {type(obj).__name__}, expected {expected_type.__name__}."
        )
    return obj


def resolve_class(path: str, base_class: Optional[type] = None) -> type:
    """Resolve a ``module:ClassName`` path to a class, optionally type-checked."""
    obj = resolve_variable(path)
    if not isinstance(obj, type):
        raise ReflectionError(f"{path!r} resolved to a {type(obj).__name__}, not a class.")
    if base_class is not None and not issubclass(obj, base_class):
        raise ReflectionError(
            f"{path!r} ({obj.__name__}) is not a subclass of {base_class.__name__}."
        )
    return obj
