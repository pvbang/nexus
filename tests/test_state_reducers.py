"""State reducers: safe merge semantics for concurrent same-key writes."""

from app.orchestration.state_reducers import (
    merge_keyed,
    dedupe_append,
    DuplicateKeyResolution,
)


def test_merge_keyed_new_key():
    store = {}
    res = merge_keyed(store, "developer", "result-1")
    assert store["developer"] == "result-1"
    assert res == DuplicateKeyResolution.INSERTED


def test_merge_keyed_duplicate_key_keeps_first_by_default():
    store = {"developer": "first"}
    res = merge_keyed(store, "developer", "second")
    # Fail-closed default: do not silently clobber — keep first, flag duplicate.
    assert store["developer"] == "first"
    assert res == DuplicateKeyResolution.KEPT_EXISTING


def test_merge_keyed_overwrite_when_allowed():
    store = {"developer": "first"}
    res = merge_keyed(store, "developer", "second", on_conflict="overwrite")
    assert store["developer"] == "second"
    assert res == DuplicateKeyResolution.OVERWROTE


def test_merge_keyed_suffix_strategy_keeps_both():
    store = {"developer": "first"}
    res = merge_keyed(store, "developer", "second", on_conflict="suffix")
    assert store["developer"] == "first"
    # second stored under a uniquified key, nothing lost
    assert any(k != "developer" and v == "second" for k, v in store.items())
    assert res == DuplicateKeyResolution.SUFFIXED


def test_dedupe_append_skips_duplicate():
    seen = set()
    out = []
    assert dedupe_append(out, seen, "a", key="a") is True
    assert dedupe_append(out, seen, "a", key="a") is False  # duplicate
    assert dedupe_append(out, seen, "b", key="b") is True
    assert out == ["a", "b"]


def test_dedupe_append_uses_value_as_key_when_none():
    seen = set()
    out = []
    dedupe_append(out, seen, "x")
    dedupe_append(out, seen, "x")
    assert out == ["x"]
