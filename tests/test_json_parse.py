"""Tests for parse_json_response — lenient LLM JSON extraction (audit C1/C3).

Extracted from agent_worker (was reached cross-module by turn_executor) with no
tests. Pins the three strategies — direct, fenced, embedded — and the None
fallback that callers depend on.
"""

from __future__ import annotations

from app.orchestration.json_parse import parse_json_response


def test_direct_json():
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_json_in_markdown_fence():
    text = 'Here you go:\n```json\n{"status": "ok", "n": 2}\n```\nthanks'
    assert parse_json_response(text) == {"status": "ok", "n": 2}


def test_json_in_plain_fence():
    text = '```\n{"x": true}\n```'
    assert parse_json_response(text) == {"x": True}


def test_json_embedded_in_prose():
    text = 'The result is {"score": 9} according to analysis.'
    assert parse_json_response(text) == {"score": 9}


def test_no_json_returns_none():
    assert parse_json_response("just some prose, no json here") is None


def test_empty_returns_none():
    assert parse_json_response("") is None


def test_malformed_json_returns_none():
    # Looks like JSON but isn't valid → None (caller decides fallback).
    assert parse_json_response("{not: valid, json}") is None


def test_direct_parse_preferred_over_fence():
    # A bare valid JSON should parse directly even if it contains backticks-ish text.
    assert parse_json_response('{"note": "use ```json``` fences"}') == {
        "note": "use ```json``` fences"
    }


def test_nested_object_via_fence():
    text = '```json\n{"outer": {"inner": [1, 2, 3]}}\n```'
    assert parse_json_response(text) == {"outer": {"inner": [1, 2, 3]}}
