"""MCP tool results must not silently drop non-text content."""

from app.mcp.content import extract_tool_result


def test_text_blocks_joined():
    r = {"content": [{"type": "text", "text": "hello"},
                     {"type": "text", "text": "world"}]}
    assert extract_tool_result(r) == "hello\nworld"


def test_image_block_becomes_placeholder():
    r = {"content": [{"type": "image", "mimeType": "image/png", "data": "QUJD"}]}
    out = extract_tool_result(r)
    assert "image" in out.lower()
    assert "image/png" in out
    # raw base64 must NOT be inlined verbatim
    assert "QUJD" not in out


def test_mixed_text_and_image():
    r = {"content": [{"type": "text", "text": "see screenshot:"},
                     {"type": "image", "mimeType": "image/jpeg", "data": "Zm9v"}]}
    out = extract_tool_result(r)
    assert "see screenshot:" in out
    assert "image/jpeg" in out


def test_embedded_resource_text():
    r = {"content": [{"type": "resource",
                      "resource": {"uri": "file:///a.txt", "text": "filedata"}}]}
    out = extract_tool_result(r)
    assert "filedata" in out


def test_resource_without_text_shows_uri():
    r = {"content": [{"type": "resource",
                      "resource": {"uri": "file:///b.bin", "blob": "AAAA"}}]}
    out = extract_tool_result(r)
    assert "file:///b.bin" in out
    assert "AAAA" not in out


def test_structured_content_appended():
    r = {"content": [{"type": "text", "text": "result"}],
         "structuredContent": {"score": 42}}
    out = extract_tool_result(r)
    assert "result" in out
    assert "42" in out


def test_no_content_key_falls_back_to_json():
    r = {"weird": "shape"}
    out = extract_tool_result(r)
    assert "weird" in out


def test_malformed_never_raises():
    assert isinstance(extract_tool_result(None), str)
    assert isinstance(extract_tool_result(12345), str)
