from app.orchestration.text_budget import (
    smart_truncate,
    keep_tail,
    keep_head_tail,
    parse_output_sections,
    classify_section,
    allocate_tiered,
)


def test_smart_truncate_word_boundary():
    assert smart_truncate("the quick brown fox jumps", 12) == "the quick…"


def test_smart_truncate_unchanged_when_fits():
    assert smart_truncate("short", 100) == "short"


def test_smart_truncate_hard_cut_when_no_space():
    # one long unbroken token → still bounded
    out = smart_truncate("a" * 50, 10)
    assert len(out) <= 11 and out.endswith("…")


def test_keep_tail_keeps_end():
    text = "START " + "x " * 200 + "THE-END"
    out = keep_tail(text, 40)
    assert "THE-END" in out
    assert "START" not in out
    assert out.startswith("…")


def test_keep_tail_unchanged_when_fits():
    assert keep_tail("short tail", 100) == "short tail"


def test_keep_head_tail_drops_middle_keeps_both_ends():
    text = "HEAD " + "m" * 500 + " TAIL"
    out = keep_head_tail(text, head=10, tail=10)
    assert "HEAD" in out and "TAIL" in out
    assert "[… " in out  # elision marker
    assert "mmmmmmmmmm" not in out  # middle gone (10+ m's)


# ── parse_output_sections + classify_section (T2) ──

_REAL_OUTPUT = """Some preamble line.

## ACCOMPLISHED
- Selected downdraft gasifier type.
- Calculated main dimensions.

## KEY FINDINGS
- Downdraft optimal for high-ash biomass.

## STILL MISSING
- Detailed mechanical drawing.
"""


def test_parse_sections_splits_named_blocks():
    secs = parse_output_sections(_REAL_OUTPUT)
    assert "downdraft gasifier" in secs["accomplished"]
    assert "high-ash biomass" in secs["key_findings"]
    assert "mechanical drawing" in secs["still_missing"]
    assert "preamble" in secs[""]


def test_parse_sections_vietnamese_headings():
    secs = parse_output_sections("## Kết quả\n- Đã tính xong.\n")
    assert "Đã tính xong" in secs["conclusion"]


def test_parse_sections_no_headings_is_preamble():
    secs = parse_output_sections("just prose, no headings here")
    assert secs[""].strip() == "just prose, no headings here"
    assert len(secs) == 1


def test_classify_accomplished_is_priority_zero():
    assert classify_section("ACCOMPLISHED")[1] == 0
    assert classify_section("Kết quả")[1] == 0
    assert classify_section("STILL MISSING")[1] == 1
    assert classify_section("KEY FINDINGS")[1] == 2
    assert classify_section("Random Heading") == ("other", 3)


# ── allocate_tiered (T3) ──

def test_allocate_keeps_conclusions_whole_even_over_budget():
    frags = [
        (0, "CONCLUSION: sizing closes within 5 percent for the reactor.", ""),
        (2, "detail " * 100, "activity_search(reactor, T3)"),
    ]
    out = allocate_tiered(frags, budget=80)
    assert "sizing closes within 5 percent" in out  # conclusion intact
    assert "activity_search(reactor, T3)" in out     # recovery pointer present


def test_allocate_shrinks_details_not_conclusions():
    frags = [
        (0, "DONE: built the model.", ""),
        (2, "x" * 400, "recall_x"),
    ]
    out = allocate_tiered(frags, budget=60)
    assert "built the model" in out
    assert "x" * 400 not in out  # detail shrunk


def test_allocate_all_fits_returns_everything():
    frags = [(0, "a done", ""), (2, "b detail", "")]
    out = allocate_tiered(frags, budget=1000)
    assert "a done" in out and "b detail" in out
