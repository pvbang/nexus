from pathlib import Path
import pytest

from app.documents.docx import docx_available

if not docx_available():
    pytest.skip("pandoc không khả dụng (PATH lẫn pypandoc)", allow_module_level=True)

from app.documents.docx import render_docx  # noqa: E402
from docx import Document  # noqa: E402

VN = "Tiếng Việt: ăâđêôơư ạảấầ"


def test_docx_has_heading_table_and_vn(tmp_path: Path):
    md = f"# {VN}\n\nĐoạn văn.\n\n| Cột | Giá trị |\n|---|---|\n| A | 1 |"
    out = render_docx(md, tmp_path / "d.docx", title=VN)
    assert out.exists()
    doc = Document(str(out))
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "ăâđêôơư" in full                       # dấu Việt nguyên vẹn
    assert any(p.style.name.startswith("Heading") for p in doc.paragraphs)
    assert len(doc.tables) == 1 and len(doc.tables[0].rows) == 2
