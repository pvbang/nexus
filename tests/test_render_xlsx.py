from pathlib import Path
from openpyxl import load_workbook
from app.documents.xlsx import render_xlsx

VN = "Tiếng Việt ăâđêôơư"


def test_single_sheet_styled(tmp_path: Path):
    out = render_xlsx(tmp_path / "s.xlsx", headers=["Tên", "Số"],
                      rows=[[VN, 1], ["B", 2]], title="DL")
    wb = load_workbook(str(out))
    ws = wb.active
    assert ws["A1"].value == "Tên"
    assert ws["A2"].value == VN                       # dấu Việt nguyên vẹn
    assert ws["A1"].font.bold is True
    assert ws.freeze_panes == "A2"                    # freeze header


def test_multi_sheet(tmp_path: Path):
    out = render_xlsx(tmp_path / "m.xlsx",
                      sheets=[{"name": "S1", "headers": ["X"], "rows": [[1]]},
                              {"name": "S2", "headers": ["Y"], "rows": [[2]]}])
    wb = load_workbook(str(out))
    assert wb.sheetnames == ["S1", "S2"]
