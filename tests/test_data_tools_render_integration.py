import shutil
from pathlib import Path
import pytest
from app.mcp.tools import data as data_tools


@pytest.mark.asyncio
async def test_generate_excel_uses_new_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr(data_tools, "_get_output_dir", lambda: tmp_path)
    res = await data_tools.handle_generate_excel(
        {"filename": "r.xlsx", "headers": ["Tên"], "rows": [["ăâđêôơư"]]})
    assert res["success"] is True
    from openpyxl import load_workbook
    wb = load_workbook(str(tmp_path / "r.xlsx"))
    assert wb.active["A2"].value == "ăâđêôơư"
    assert wb.active.freeze_panes == "A2"   # style mới đã áp


@pytest.mark.asyncio
async def test_generate_word_uses_pandoc_or_gates(tmp_path, monkeypatch):
    from app.documents.docx import docx_available
    monkeypatch.setattr(data_tools, "_get_output_dir", lambda: tmp_path)
    res = await data_tools.handle_generate_word_document(
        {"title": "T", "content": "# H\n\nNội dung ăâđêôơư"})
    if docx_available():
        assert res["success"] is True
        docx_files = list(tmp_path.glob("*.docx"))
        assert len(docx_files) == 1 and docx_files[0].stat().st_size > 1000
    else:
        assert res["success"] is False and "pandoc" in res["output"].lower()
