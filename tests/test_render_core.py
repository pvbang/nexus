from app.documents.render_core import markdown_to_html, theme_css_path


def test_renders_heading_and_table():
    html = markdown_to_html("# Tiêu đề\n\n| A | B |\n|---|---|\n| 1 | 2 |", title="T")
    assert "<h1>" in html and "Tiêu đề" in html
    assert "<table>" in html and "<th>" in html


def test_vietnamese_preserved():
    md = "Tiếng Việt: ăâđêôơư ạảấầ — **đậm** *nghiêng* `mã`"
    html = markdown_to_html(md)
    assert "ăâđêôơư ạảấầ" in html
    assert "<strong>" in html and "<em>" in html and "<code>" in html


def test_embeds_theme_and_charset():
    html = markdown_to_html("hello", title="X")
    assert "charset" in html.lower()
    assert "@font-face" in html  # theme.css inlined
    assert theme_css_path().exists()


def test_strikethrough_and_tasklist():
    html = markdown_to_html("~~old~~\n\n- [x] done\n- [ ] todo")
    assert "<del>" in html or "<s>" in html
    assert "checkbox" in html.lower()
