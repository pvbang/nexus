import shutil
from pathlib import Path
import pytest
from app.documents import markdown_to_html, render_xlsx

GOLDEN = Path("tests/fixtures/golden.md").read_text(encoding="utf-8")
VN_SAMPLE = "ăâđêôơư"


def test_html_has_vietnamese_and_structure():
    html = markdown_to_html(GOLDEN, title="Golden")
    assert VN_SAMPLE in html
    assert "<table>" in html and "<h1>" in html
    assert "<del>" in html or "<s>" in html


def test_pdf_vietnamese_intact(tmp_path):
    pytest.importorskip("weasyprint")
    pdfplumber = pytest.importorskip("pdfplumber")
    from app.documents import render_pdf
    out = render_pdf(GOLDEN, tmp_path / "g.pdf", title="Golden")
    with pdfplumber.open(str(out)) as pdf:
        text = "".join(p.extract_text() or "" for p in pdf.pages)
    assert VN_SAMPLE in text


def test_docx_vietnamese_intact(tmp_path):
    from app.documents.docx import docx_available
    if not docx_available():
        pytest.skip("pandoc absent")
    from app.documents import render_docx
    from docx import Document
    out = render_docx(GOLDEN, tmp_path / "g.docx", title="Golden")
    full = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert VN_SAMPLE in full


def test_xlsx_vietnamese_intact(tmp_path):
    from openpyxl import load_workbook
    out = render_xlsx(tmp_path / "g.xlsx", headers=["Cột"], rows=[[VN_SAMPLE]])
    assert load_workbook(str(out)).active["A2"].value == VN_SAMPLE
