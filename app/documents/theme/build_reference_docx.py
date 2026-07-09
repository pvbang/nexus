"""Tạo reference.docx cho Pandoc — chỉnh font/màu các named-style khớp theme.css.

Chạy 1 lần (dev), commit kết quả. Bắt đầu từ reference mặc định của Pandoc rồi
ghi đè font + màu heading. Yêu cầu: pandoc + python-docx.

    python -m app.documents.theme.build_reference_docx
"""
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor

HERE = Path(__file__).parent
OUT = HERE / "reference.docx"
BODY_FONT = "Noto Sans"
MONO_FONT = "JetBrains Mono"
ACCENT = RGBColor(0x25, 0x63, 0xEB)

# Pandoc style → (font, size_pt, color, bold)
STYLE_MAP = {
    "Normal":      (BODY_FONT, 11, None, None),
    "Body Text":   (BODY_FONT, 11, None, None),
    "Heading 1":   (BODY_FONT, 22, ACCENT, True),
    "Heading 2":   (BODY_FONT, 16, None, True),
    "Heading 3":   (BODY_FONT, 13, None, True),
    "Heading 4":   (BODY_FONT, 11, None, True),
    "Title":       (BODY_FONT, 26, ACCENT, True),
    "Source Code": (MONO_FONT, 9, None, None),
    "Verbatim Char": (MONO_FONT, 9, None, None),
}


def _pandoc_bin() -> str:
    """Resolve the pandoc binary: PATH first, else pypandoc's managed copy."""
    from shutil import which

    found = which("pandoc")
    if found:
        return found
    try:
        import pypandoc
        return pypandoc.get_pandoc_path()
    except Exception:
        return "pandoc"  # last resort — subprocess will raise a clear error


def main() -> int:
    # 1) Lấy reference mặc định của pandoc (ghi ra stdout → file).
    with open(OUT, "wb") as fh:
        subprocess.run(
            [_pandoc_bin(), "--print-default-data-file", "reference.docx"],
            check=True, stdout=fh,
        )
    # 2) Chỉnh styles cho khớp theme.css.
    doc = Document(str(OUT))
    for style in doc.styles:
        spec = STYLE_MAP.get(style.name)
        if not spec or not hasattr(style, "font"):
            continue
        font, size, color, bold = spec
        style.font.name = font
        style.font.size = Pt(size)
        if color is not None:
            style.font.color.rgb = color
        if bold is not None:
            style.font.bold = bold
    doc.save(str(OUT))
    print(f"reference.docx written: {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
