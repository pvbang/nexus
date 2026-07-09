"""Composable middleware chain with declarative positioning (@Next/@Prev)."""

import pytest

from app.orchestration.middleware_chain import (
    MiddlewareChain,
    After,
    Before,
)


def test_append_order_preserved():
    chain = MiddlewareChain()
    chain.add("a")
    chain.add("b")
    chain.add("c")
    assert chain.resolved() == ["a", "b", "c"]


def test_insert_after_anchor():
    chain = MiddlewareChain()
    chain.add("a")
    chain.add("c")
    chain.add("b", position=After("a"))
    assert chain.resolved() == ["a", "b", "c"]


def test_insert_before_anchor():
    chain = MiddlewareChain()
    chain.add("a")
    chain.add("c")
    chain.add("b", position=Before("c"))
    assert chain.resolved() == ["a", "b", "c"]


def test_unknown_anchor_raises():
    chain = MiddlewareChain()
    chain.add("a")
    with pytest.raises(ValueError):
        chain.add("b", position=After("does_not_exist"))


def test_disabled_middleware_excluded():
    chain = MiddlewareChain()
    chain.add("a")
    chain.add("b", enabled=False)
    chain.add("c")
    assert chain.resolved() == ["a", "c"]


def test_flag_true_keeps_default_flag_false_drops():
    chain = MiddlewareChain()
    chain.add("loop_detect", enabled=True)
    chain.add("summarize", enabled=False)
    names = chain.resolved()
    assert "loop_detect" in names
    assert "summarize" not in names


def test_duplicate_name_rejected():
    chain = MiddlewareChain()
    chain.add("a")
    with pytest.raises(ValueError):
        chain.add("a")
