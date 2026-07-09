"""Unit tests for display-time tool-call markup stripping.

The reasoning text shown on the /chat timeline (the ``agent_thinking`` event)
is the model's prose `content`. Different models serialize a tool call with
different delimiters, and several of them leak the serialized call INTO that
prose (the call machinery ends up in `content` alongside the reasoning). The
display layer must hide that machinery for EVERY known delimiter family — not
just the Anthropic XML spelling the frontend already handles — so the timeline
shows clean reasoning regardless of which model is in rotation.

These pin ``strip_tool_call_markup`` against each delimiter family directly.
"""

from __future__ import annotations

from app.services.prompted_tools import strip_tool_call_markup


def test_plain_reasoning_untouched():
    text = "Bây giờ tôi sẽ kiểm tra workspace hiện tại để xem đã có dữ liệu nào chưa."
    assert strip_tool_call_markup(text) == text


def test_word_function_calls_in_prose_not_stripped():
    # A bare word that merely *mentions* tool calling must survive — only real
    # markup (pipe-wrapped special tokens / tagged blocks) is machinery.
    text = "I plan to make several function_calls to the API today."
    assert strip_tool_call_markup(text) == text


def test_ascii_pipe_token_truncated():
    text = "Let me check the files.\n<|tool_call|>\n{\"name\": \"list_files\"}"
    assert strip_tool_call_markup(text) == "Let me check the files."


def test_fullwidth_pipe_deepseek_token_truncated():
    # DeepSeek-style chat template: fullwidth bar U+FF5C + U+2581 word-joiner.
    text = (
        "Tôi sẽ liệt kê thư mục.\n"
        "<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>"
        "function<｜tool▁sep｜>list_files\n```json\n{}\n```"
    )
    assert strip_tool_call_markup(text) == "Tôi sẽ liệt kê thư mục."


def test_dsml_style_token_truncated():
    # The exact family the user observed. The bars render wide in a proportional
    # font (looking like `< | DSML | function_calls`), but the underlying chars are
    # the fullwidth bar U+FF5C glued to the marker. Format-agnostic truncation
    # hides it whole. (A space-separated ASCII form `< | … |` is deliberately NOT
    # matched: it collides with math/comparison prose like `x < |y|`.)
    text = "Bây giờ tôi sẽ kiểm tra workspace:\n<｜DSML｜function_calls{\"x\":1}"
    assert strip_tool_call_markup(text) == "Bây giờ tôi sẽ kiểm tra workspace:"


def test_fenced_tool_call_block_removed_prose_preserved():
    text = "Plan first.\n```tool_call\n{\"name\": \"read_file\"}\n```\nThen continue."
    out = strip_tool_call_markup(text)
    assert "Plan first." in out and "Then continue." in out
    assert "read_file" not in out and "```" not in out


def test_xml_invoke_block_removed_prose_preserved():
    text = (
        "I will write the file.\n"
        "<invoke name=\"write_file\"><parameter name=\"path\">r.md</parameter></invoke>\n"
        "Next step."
    )
    out = strip_tool_call_markup(text)
    assert "I will write the file." in out and "Next step." in out
    assert "invoke" not in out and "write_file" not in out


def test_fabricated_tool_response_block_removed():
    # E8 (run 3a75a0db): a model that leaks a `<tool_call>` ALSO fabricates a
    # `<tool_response>` block (the system never emits one into the model's own
    # content). Left in prose, "File written: ... (3.38 KB)" becomes a
    # self-authored lie that later turns read as ground truth → the phantom-write
    # spreads. The response machinery must be stripped like every other block.
    text = (
        "I'll write the design basis file now.\n"
        "<tool_call>\n{\"name\": \"write_file\", \"arguments\": {\"path\": \"x.json\"}}\n</tool_call>\n"
        "<tool_response>\nFile written: x.json (3.38 KB)\n</tool_response>\n"
        "The file is confirmed written and verified."
    )
    out = strip_tool_call_markup(text)
    assert "I'll write the design basis file now." in out
    assert "The file is confirmed written and verified." in out
    assert "tool_response" not in out
    assert "File written" not in out
    assert "3.38 KB" not in out


def test_fabricated_read_back_tool_response_removed():
    # The read-back variant: model fakes a read_file + its response with the full
    # file contents, "proving" the write. Both the call and the fake response go.
    text = (
        "Now let me read it back to verify:\n"
        "<tool_response>\n{\"feedstock\": \"rice husk\", \"capacity_kg_h\": 500}\n</tool_response>\n"
        "Verified."
    )
    out = strip_tool_call_markup(text)
    assert "Now let me read it back to verify:" in out
    assert "Verified." in out
    assert "feedstock" not in out and "tool_response" not in out


def test_empty_and_none_safe():
    assert strip_tool_call_markup("") == ""
    assert strip_tool_call_markup(None) is None
