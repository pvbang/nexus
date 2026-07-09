from app.api.v1.endpoints.artifacts import _get_preview_type


def test_xlsx_is_spreadsheet():
    assert _get_preview_type("data.xlsx") == "spreadsheet"
    assert _get_preview_type("old.xls") == "spreadsheet"


def test_docx_still_document():
    assert _get_preview_type("a.docx") == "document"
    assert _get_preview_type("a.pptx") == "document"
