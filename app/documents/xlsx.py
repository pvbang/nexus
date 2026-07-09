"""data → XLSX styled (header accent, zebra, freeze, auto-width)."""
from pathlib import Path
from typing import Any

ACCENT = "2563EB"
BODY_FONT = "Calibri"  # font Excel an toàn dấu Việt (Excel không embed font)


def xlsx_available() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


def _fill_sheet(ws, headers: list, rows: list) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    header_font = Font(name=BODY_FONT, bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color=ACCENT, end_color=ACCENT, fill_type="solid")
    body_font = Font(name=BODY_FONT, size=11)
    zebra = PatternFill(start_color="FAFBFC", end_color="FAFBFC", fill_type="solid")

    if headers:
        for col, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=col, value=str(h))
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws.freeze_panes = "A2"

    start = 2 if headers else 1
    for r_i, row in enumerate(rows or [], start):
        values = (
            [row.get(str(k), "") for k in (headers or row.keys())]
            if isinstance(row, dict) else list(row)
        )
        for c_i, val in enumerate(values, 1):
            c = ws.cell(row=r_i, column=c_i, value=val)
            c.font = body_font
            if (r_i - start) % 2 == 1:
                c.fill = zebra

    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(width + 4, 60)


def render_xlsx(
    out_path: Any,
    *,
    sheets: list | None = None,
    headers: list | None = None,
    rows: list | None = None,
    title: str | None = None,
) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    if sheets and isinstance(sheets, list):
        for i, sd in enumerate(sheets):
            ws = wb.active if i == 0 else wb.create_sheet()
            ws.title = sd.get("name", f"Sheet{i+1}")
            _fill_sheet(ws, sd.get("headers", []), sd.get("rows", []))
    else:
        ws = wb.active
        ws.title = (title or "Data")[:31]
        _fill_sheet(ws, headers or [], rows or [])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))
    return out_path
