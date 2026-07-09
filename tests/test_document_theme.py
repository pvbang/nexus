from pathlib import Path

FONTS = Path("assets/fonts")
THEME = Path("app/documents/theme")


def test_vietnamese_fonts_bundled():
    for name in (
        "NotoSans-Regular.ttf", "NotoSans-Bold.ttf",
        "NotoSans-Italic.ttf", "NotoSans-BoldItalic.ttf",
        "JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf",
    ):
        f = FONTS / name
        assert f.exists() and f.stat().st_size > 10_000, f"missing/empty font {name}"


def test_theme_css_declares_fonts():
    css = (THEME / "theme.css").read_text(encoding="utf-8")
    assert "@font-face" in css
    assert "Noto Sans" in css
    assert "JetBrains Mono" in css


def test_reference_docx_exists():
    assert (THEME / "reference.docx").stat().st_size > 1000
