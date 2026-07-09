"""HTML + theme.css → PDF qua WeasyPrint, embed font tiếng Việt."""
from pathlib import Path

from app.documents.render_core import markdown_to_html, theme_dir


def pdf_available() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


def render_pdf(md_text: str, out_path: Path, *, title: str | None = None) -> Path:
    try:
        from weasyprint import HTML
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "WeasyPrint chưa sẵn sàng (thiếu lib hệ thống pango/cairo). "
            f"Chi tiết: {e}"
        ) from e

    html = markdown_to_html(md_text, title=title)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # base_url = theme_dir để các url("../../../assets/fonts/..") trong theme.css resolve đúng.
    HTML(string=html, base_url=str(theme_dir())).write_pdf(str(out_path))
    return out_path
