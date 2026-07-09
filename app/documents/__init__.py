"""Unified document rendering — Markdown là nguồn duy nhất.

Public API:
    markdown_to_html, render_pdf, render_docx, render_xlsx,
    theme_css_path, theme_dir, fonts_dir
"""
from app.documents.render_core import (
    markdown_to_html, theme_css_path, theme_dir, fonts_dir,
)

__all__ = [
    "markdown_to_html", "theme_css_path", "theme_dir", "fonts_dir",
    "render_pdf", "render_docx", "render_xlsx",
]


def __getattr__(name):  # lazy import engines (tránh import nặng khi chỉ cần HTML)
    if name == "render_pdf":
        from app.documents.pdf import render_pdf
        return render_pdf
    if name == "render_docx":
        from app.documents.docx import render_docx
        return render_docx
    if name == "render_xlsx":
        from app.documents.xlsx import render_xlsx
        return render_xlsx
    raise AttributeError(name)
