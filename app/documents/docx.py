"""Markdown → DOCX qua Pandoc, style theo reference.docx."""
import os
from shutil import which
from pathlib import Path

from app.documents.render_core import theme_dir

_REFERENCE = theme_dir() / "reference.docx"


def _pandoc_path() -> str | None:
    """Resolve pandoc: PATH first, else pypandoc's managed copy. None if absent."""
    found = which("pandoc")
    if found:
        return found
    try:
        import pypandoc
        return pypandoc.get_pandoc_path()
    except Exception:
        return None


def docx_available() -> bool:
    return _pandoc_path() is not None


def render_docx(md_text: str, out_path: Path, *, title: str | None = None) -> Path:
    pandoc = _pandoc_path()
    if pandoc is None:
        raise RuntimeError("pandoc chưa được cài (cần để sinh .docx chuyên nghiệp).")
    # pypandoc tìm pandoc qua env PYPANDOC_PANDOC khi không có trên PATH.
    os.environ.setdefault("PYPANDOC_PANDOC", pandoc)
    import pypandoc

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = f"# {title}\n\n{md_text}" if title else md_text
    extra = ["--from", "gfm", "--to", "docx"]
    if _REFERENCE.exists():
        extra += ["--reference-doc", str(_REFERENCE)]
    pypandoc.convert_text(body, "docx", format="gfm",
                          outputfile=str(out_path), extra_args=extra)
    return out_path
