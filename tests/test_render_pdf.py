from pathlib import Path
import pytest

weasyprint = pytest.importorskip("weasyprint")  # skip nếu WeasyPrint chưa cài (Windows local)
from app.documents.pdf import render_pdf  # noqa: E402

VN = "Tiếng Việt: ăâđêôơư ạảấầ Nội dung"


def test_pdf_created_and_nonempty(tmp_path: Path):
    out = render_pdf(f"# Báo cáo\n\n{VN}\n\n| Cột | Giá trị |\n|---|---|\n| A | 1 |",
                     tmp_path / "r.pdf", title="Báo cáo")
    assert out.exists() and out.stat().st_size > 2000


def test_vietnamese_text_extractable(tmp_path: Path):
    pdfplumber = pytest.importorskip("pdfplumber")
    out = render_pdf(VN, tmp_path / "vn.pdf")
    with pdfplumber.open(str(out)) as pdf:
        text = "".join(p.extract_text() or "" for p in pdf.pages)
    assert "ăâđêôơư" in text and "ạảấầ" in text  # font có glyph Việt, không vỡ
