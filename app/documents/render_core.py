"""Markdown → HTML (CommonMark + GFM) với theme.css nhúng inline."""
from functools import lru_cache
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

_HERE = Path(__file__).parent


def theme_dir() -> Path:
    return _HERE / "theme"


def theme_css_path() -> Path:
    return theme_dir() / "theme.css"


def fonts_dir() -> Path:
    # repo_root/assets/fonts  (app/documents/ → repo root là parent.parent)
    return _HERE.parent.parent / "assets" / "fonts"


@lru_cache(maxsize=1)
def _theme_css() -> str:
    return theme_css_path().read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _md() -> MarkdownIt:
    # "commonmark" preset + bật table/strikethrough thủ công (GFM-like) + task-list.
    md = (
        MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
        .enable("table")
        .enable("strikethrough")
    )
    md.use(tasklists_plugin, enabled=True)
    return md


def render_body(md_text: str) -> str:
    """Chỉ render phần body HTML (không kèm <html>/theme) — dùng cho preview."""
    return _md().render(md_text)


def markdown_to_html(md_text: str, *, title: str | None = None) -> str:
    """Trả về full HTML doc, theme.css nhúng inline, charset utf-8."""
    body = render_body(md_text)
    head_title = f"<title>{title}</title>" if title else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="vi"><head><meta charset="utf-8">'
        f"{head_title}<style>{_theme_css()}</style></head>"
        f"<body>{body}</body></html>"
    )
