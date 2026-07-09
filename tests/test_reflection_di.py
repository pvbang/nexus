"""Reflection DI: resolve 'module:attr' paths with actionable install hints."""

import pytest

from app.core.reflection import (
    resolve_variable,
    resolve_class,
    ReflectionError,
)


def test_resolve_variable_stdlib():
    # 'math:pi' → the pi constant
    import math
    assert resolve_variable("math:pi") == math.pi


def test_resolve_variable_dotted_attr():
    # nested attribute access after the colon
    val = resolve_variable("os:path.sep")
    import os
    assert val == os.path.sep


def test_resolve_class_returns_type():
    cls = resolve_class("collections:OrderedDict")
    from collections import OrderedDict
    assert cls is OrderedDict


def test_resolve_class_type_check_passes():
    cls = resolve_class("collections:OrderedDict", base_class=dict)
    assert issubclass(cls, dict)


def test_resolve_class_type_check_fails():
    with pytest.raises(ReflectionError):
        resolve_class("collections:OrderedDict", base_class=list)


def test_bad_format_raises():
    with pytest.raises(ReflectionError):
        resolve_variable("no_colon_here")


def test_missing_module_gives_install_hint():
    with pytest.raises(ReflectionError) as ei:
        resolve_variable("langfuse:Langfuse")
    msg = str(ei.value)
    # actionable hint mentions how to install the missing package
    assert "langfuse" in msg.lower()
    assert "install" in msg.lower() or "uv add" in msg.lower() or "pip install" in msg.lower()


def test_missing_attribute_raises():
    with pytest.raises(ReflectionError):
        resolve_variable("math:not_a_real_attr")
