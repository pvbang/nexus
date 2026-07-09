"""SSE events must carry an `id:` field so clients can resume via Last-Event-ID."""

from app.core.sse import format_sse_event, parse_last_event_id


def test_event_without_id_has_no_id_line():
    out = format_sse_event({"type": "token", "content": "hi"})
    assert out.startswith("data: ")
    assert "id:" not in out
    assert out.endswith("\n\n")


def test_event_with_id_emits_id_line_first():
    out = format_sse_event({"type": "token", "content": "hi"}, event_id=42)
    lines = out.splitlines()
    assert lines[0] == "id: 42"
    assert lines[1].startswith("data: ")


def test_event_payload_is_json():
    import json
    out = format_sse_event({"type": "step", "step": 3}, event_id=7)
    data_line = [l for l in out.splitlines() if l.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: "):])
    assert payload == {"type": "step", "step": 3}


def test_parse_last_event_id_valid():
    assert parse_last_event_id("123") == 123


def test_parse_last_event_id_missing_or_bad():
    assert parse_last_event_id(None) == 0
    assert parse_last_event_id("") == 0
    assert parse_last_event_id("not-a-number") == 0
    assert parse_last_event_id("-5") == 0  # negative is meaningless → 0


def test_unicode_preserved():
    out = format_sse_event({"type": "token", "content": "Xin chào"}, event_id=1)
    assert "Xin chào" in out
