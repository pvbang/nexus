# Unified Document Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sinh `.md/.docx/.pdf/.xlsx` đẹp, nhất quán, không lỗi font tiếng Việt, từ một nguồn Markdown duy nhất; frontend preview khớp file thật (WYSIWYG).

**Architecture:** Markdown → HTML (markdown-it-py) → một bộ "design language" (theme.css + reference.docx + bundled fonts) render ra PDF (WeasyPrint), DOCX (Pandoc + reference.docx), XLSX (openpyxl + style). Các tool hiện có (`generate_pdf/word_document/excel`) trở thành lớp mỏng gọi vào `app/documents/`, **giữ nguyên tên + schema** để không phá tích hợp agent.

**Tech Stack:** Python (markdown-it-py, weasyprint, pypandoc/pandoc, python-docx, openpyxl), React 19 + TypeScript + Tailwind (SheetJS cho xlsx viewer), Docker (pandoc + libpango/cairo).

## Global Constraints

- Python floor: `python-docx>=1.1.0`, `openpyxl>=3.1.0` (đã có). Thêm: `weasyprint>=62`, `markdown-it-py>=3.0`, `mdit-py-plugins>=0.4`, `pypandoc>=1.13`.
- **Không đổi tên tool và schema** của `generate_pdf`, `generate_word_document`, `generate_excel`, `generate_report`. Chỉ thay phần thân handler.
- **Không đụng** `read_spreadsheet`, `edit_spreadsheet`, `read_document`, `edit_document`, `convert_document`.
- Engine thiếu binary/lib → tool tự gate qua `check_fn` và báo trung thực; **không** fallback im lặng tạo file vỡ font; **không** tạo file rỗng khi lỗi.
- Output dir lấy qua `_get_output_dir()` (đã có trong `data.py`); trả về dict `{success, output, data, artifacts}` đúng pattern hiện tại; `download_url = f"/api/v1/artifacts/download/{filename}"`.
- Font bundle trong repo `assets/fonts/` (Noto Sans + JetBrains Mono, OFL). Không phụ thuộc font hệ thống.
- Tests đặt trong `tests/` (không bị gitignore). Test phụ thuộc binary tùy chọn dùng `pytest.importorskip(...)` / `shutil.which(...)` để skip sạch khi môi trường local thiếu (verify thật chạy trong Docker).
- Git: commit cuối mỗi task. Co-author trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**Tạo mới (backend):**
- `app/documents/__init__.py` — public API: `markdown_to_html`, `render_pdf`, `render_docx`, `render_xlsx`, `theme_css_path`, font helpers.
- `app/documents/render_core.py` — Markdown → HTML.
- `app/documents/pdf.py` — HTML + theme.css → PDF (WeasyPrint).
- `app/documents/docx.py` — Markdown → DOCX (Pandoc + reference.docx).
- `app/documents/xlsx.py` — data → XLSX styled.
- `app/documents/theme/theme.css` — design language dùng chung PDF + frontend.
- `app/documents/theme/build_reference_docx.py` — dev script tạo `reference.docx`.
- `app/documents/theme/reference.docx` — template Word (artifact build, commit).
- `assets/fonts/` — `NotoSans-Regular/Bold/Italic/BoldItalic.ttf`, `JetBrainsMono-Regular/Bold.ttf`.

**Sửa (backend):**
- `app/mcp/tools/data.py` — thay thân `handle_generate_pdf`, `handle_generate_word_document`, `handle_generate_excel`; thêm `check_fn` cho pdf/docx.
- `app/api/v1/endpoints/artifacts.py:62-82` — tách `xlsx/xls` ra `preview_type="spreadsheet"`.
- `pyproject.toml` — thêm deps.
- `Dockerfile` — thêm `pandoc` + libs WeasyPrint.

**Tạo mới / sửa (frontend):**
- `frontend/src/components/artifacts/viewers/SpreadsheetViewer.tsx` — MỚI.
- `frontend/src/components/artifacts/viewers/index.ts` — export thêm.
- `frontend/src/components/artifacts/FileViewer.tsx` — route `spreadsheet`.
- `frontend/src/types/artifacts.ts` — thêm `'spreadsheet'` vào `PreviewType`.
- `frontend/public/fonts/` + `frontend/src/styles/document-theme.css` — font Việt + theme khớp backend.
- `frontend/package.json` — thêm `xlsx` (SheetJS).

---

## Task 1: Dependencies + Docker system packages

**Files:**
- Modify: `pyproject.toml`
- Modify: `Dockerfile:69-81`
- Test: `tests/test_document_deps.py` (create)

**Interfaces:**
- Produces: môi trường có `markdown-it-py`, `weasyprint`, `pypandoc`; Docker có `pandoc` + libs WeasyPrint.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_document_deps.py
"""Verify document-rendering dependencies declared & importable (skips if absent)."""
import tomllib
from pathlib import Path


def test_pyproject_declares_render_deps():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    deps = " ".join(data["project"]["dependencies"])
    for pkg in ("markdown-it-py", "weasyprint", "mdit-py-plugins", "pypandoc"):
        assert pkg in deps, f"{pkg} missing from pyproject dependencies"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_document_deps.py -v`
Expected: FAIL — `markdown-it-py missing from pyproject dependencies`

- [ ] **Step 3: Add Python deps to pyproject.toml**

Trong `[project] dependencies` (cạnh `reportlab>=4.0.0`), thêm:

```toml
    "markdown-it-py>=3.0",
    "mdit-py-plugins>=0.4",
    "weasyprint>=62",
    "pypandoc>=1.13",
```

- [ ] **Step 4: Add system packages to Dockerfile**

Trong khối `apt-get install` (`Dockerfile:69-81`), thêm các gói (giữ thứ tự alphabet, trước `tesseract-ocr`):

```dockerfile
    libcairo2 \
    libffi-dev \
    libgdk-pixbuf-2.0-0 \
    libharfbuzz0b \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    pandoc \
```

- [ ] **Step 5: Install deps locally & run test**

Run: `python -m pip install "markdown-it-py>=3.0" "mdit-py-plugins>=0.4" "pypandoc>=1.13" && python -m pytest tests/test_document_deps.py -v`
Expected: PASS. (WeasyPrint khó cài trên Windows — bỏ qua cài local, test chỉ đọc pyproject nên vẫn PASS.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml Dockerfile tests/test_document_deps.py
git commit -m "build(docs): add weasyprint/markdown-it-py/pandoc deps + WeasyPrint libs in Docker"
```

---

## Task 2: Bundle fonts + theme.css + reference.docx

**Files:**
- Create: `assets/fonts/` (6 file .ttf)
- Create: `app/documents/theme/theme.css`
- Create: `app/documents/theme/build_reference_docx.py`
- Create: `app/documents/theme/reference.docx` (artifact build)
- Test: `tests/test_document_theme.py` (create)

**Interfaces:**
- Produces:
  - `assets/fonts/NotoSans-Regular.ttf`, `NotoSans-Bold.ttf`, `NotoSans-Italic.ttf`, `NotoSans-BoldItalic.ttf`, `JetBrainsMono-Regular.ttf`, `JetBrainsMono-Bold.ttf`
  - `app/documents/theme/theme.css` (chứa `@font-face` Noto Sans + JetBrains Mono trỏ tới `assets/fonts/`)
  - `app/documents/theme/reference.docx`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_document_theme.py
from pathlib import Path

FONTS = Path("assets/fonts")
THEME = Path("app/documents/theme")


def test_vietnamese_fonts_bundled():
    for name in (
        "NotoSans-Regular.ttf", "NotoSans-Bold.ttf",
        "NotoSans-Italic.ttf", "NotoSans-BoldItalic.ttf",
        "JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf",
    ):
        f = FONTS / name
        assert f.exists() and f.stat().st_size > 10_000, f"missing/empty font {name}"


def test_theme_css_declares_fonts():
    css = (THEME / "theme.css").read_text(encoding="utf-8")
    assert "@font-face" in css
    assert "Noto Sans" in css
    assert "JetBrains Mono" in css


def test_reference_docx_exists():
    assert (THEME / "reference.docx").stat().st_size > 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_document_theme.py -v`
Expected: FAIL — font/theme/reference chưa tồn tại.

- [ ] **Step 3: Tải fonts vào assets/fonts/**

```bash
mkdir -p assets/fonts
cd assets/fonts
# Noto Sans (OFL) — Google Fonts raw
curl -fsSL -o NotoSans-Regular.ttf    "https://github.com/google/fonts/raw/main/ofl/notosans/NotoSans%5Bwdth,wght%5D.ttf"
# JetBrains Mono (OFL)
curl -fsSL -o JetBrainsMono-Regular.ttf "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/ttf/JetBrainsMono-Regular.ttf"
curl -fsSL -o JetBrainsMono-Bold.ttf    "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/ttf/JetBrainsMono-Bold.ttf"
cd ../..
```

> Nếu link variable-font không tách được Bold/Italic tĩnh, tải bộ static Noto Sans từ
> `https://fonts.google.com/noto/specimen/Noto+Sans` (nút "Get font" → giải nén lấy
> `NotoSans-Regular/Bold/Italic/BoldItalic.ttf`). Mục tiêu: đủ 4 file Noto Sans tĩnh
> phủ dấu tiếng Việt + 2 file JetBrains Mono. Verify mỗi file > 10KB.

- [ ] **Step 4: Tạo theme.css**

```css
/* app/documents/theme/theme.css
   Design language dùng chung cho WeasyPrint (PDF) và frontend preview.
   Đường dẫn font tương đối được resolve qua base_url khi render PDF. */

@font-face {
  font-family: "Noto Sans";
  src: url("../../../assets/fonts/NotoSans-Regular.ttf") format("truetype");
  font-weight: 400; font-style: normal;
}
@font-face {
  font-family: "Noto Sans";
  src: url("../../../assets/fonts/NotoSans-Bold.ttf") format("truetype");
  font-weight: 700; font-style: normal;
}
@font-face {
  font-family: "Noto Sans";
  src: url("../../../assets/fonts/NotoSans-Italic.ttf") format("truetype");
  font-weight: 400; font-style: italic;
}
@font-face {
  font-family: "Noto Sans";
  src: url("../../../assets/fonts/NotoSans-BoldItalic.ttf") format("truetype");
  font-weight: 700; font-style: italic;
}
@font-face {
  font-family: "JetBrains Mono";
  src: url("../../../assets/fonts/JetBrainsMono-Regular.ttf") format("truetype");
  font-weight: 400;
}
@font-face {
  font-family: "JetBrains Mono";
  src: url("../../../assets/fonts/JetBrainsMono-Bold.ttf") format("truetype");
  font-weight: 700;
}

:root {
  --ink: #1a1a1a;
  --muted: #6b7280;
  --accent: #2563eb;
  --border: #e5e7eb;
  --code-bg: #f6f8fa;
}

@page {
  size: A4;
  margin: 2.2cm 2cm;
  @bottom-center { content: counter(page) " / " counter(pages); color: var(--muted); font-size: 9pt; }
}

body {
  font-family: "Noto Sans", sans-serif;
  color: var(--ink);
  font-size: 11pt;
  line-height: 1.6;
}
h1, h2, h3, h4 { font-family: "Noto Sans", sans-serif; font-weight: 700; line-height: 1.25; margin: 1.2em 0 0.5em; }
h1 { font-size: 22pt; color: var(--ink); border-bottom: 2px solid var(--accent); padding-bottom: 0.2em; }
h2 { font-size: 16pt; }
h3 { font-size: 13pt; }
h4 { font-size: 11.5pt; color: var(--muted); }
p { margin: 0.5em 0; }
a { color: var(--accent); text-decoration: none; }
code, pre { font-family: "JetBrains Mono", monospace; }
code { background: var(--code-bg); padding: 0.1em 0.3em; border-radius: 4px; font-size: 0.9em; }
pre {
  background: var(--code-bg); padding: 12px 14px; border-radius: 8px;
  border: 1px solid var(--border); overflow-x: auto; font-size: 9.5pt; line-height: 1.45;
}
pre code { background: none; padding: 0; }
blockquote { margin: 0.8em 0; padding: 0.2em 1em; border-left: 4px solid var(--accent); color: var(--muted); }
table { border-collapse: collapse; width: 100%; margin: 0.8em 0; font-size: 10pt; }
th, td { border: 1px solid var(--border); padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: var(--accent); color: #fff; font-weight: 700; }
tr:nth-child(even) td { background: #fafbfc; }
hr { border: none; border-top: 1px solid var(--border); margin: 1.5em 0; }
ul, ol { padding-left: 1.4em; }
li { margin: 0.2em 0; }
img { max-width: 100%; }
```

- [ ] **Step 5: Tạo build_reference_docx.py**

```python
# app/documents/theme/build_reference_docx.py
"""Tạo reference.docx cho Pandoc — chỉnh font/màu các named-style khớp theme.css.

Chạy 1 lần (dev), commit kết quả. Bắt đầu từ reference mặc định của Pandoc rồi
ghi đè font + màu heading. Yêu cầu: pandoc + python-docx.

    python -m app.documents.theme.build_reference_docx
"""
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor

HERE = Path(__file__).parent
OUT = HERE / "reference.docx"
BODY_FONT = "Noto Sans"
MONO_FONT = "JetBrains Mono"
ACCENT = RGBColor(0x25, 0x63, 0xEB)

# Pandoc style → (font, size_pt, color, bold)
STYLE_MAP = {
    "Normal":      (BODY_FONT, 11, None, None),
    "Body Text":   (BODY_FONT, 11, None, None),
    "Heading 1":   (BODY_FONT, 22, ACCENT, True),
    "Heading 2":   (BODY_FONT, 16, None, True),
    "Heading 3":   (BODY_FONT, 13, None, True),
    "Heading 4":   (BODY_FONT, 11, None, True),
    "Title":       (BODY_FONT, 26, ACCENT, True),
    "Source Code": (MONO_FONT, 9, None, None),
    "Verbatim Char": (MONO_FONT, 9, None, None),
}


def main() -> int:
    # 1) Lấy reference mặc định của pandoc
    subprocess.run(
        ["pandoc", "-o", str(OUT), "--print-default-data-file", "reference.docx"],
        check=True, stdout=open(OUT, "wb"),
    )
    # 2) Chỉnh styles
    doc = Document(str(OUT))
    for style in doc.styles:
        spec = STYLE_MAP.get(style.name)
        if not spec or not hasattr(style, "font"):
            continue
        font, size, color, bold = spec
        style.font.name = font
        style.font.size = Pt(size)
        if color is not None:
            style.font.color.rgb = color
        if bold is not None:
            style.font.bold = bold
    doc.save(str(OUT))
    print(f"reference.docx written: {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Build reference.docx**

Run: `python -m app.documents.theme.build_reference_docx`
Expected: in ra `reference.docx written: ... (NNNN bytes)`.
(Nếu local thiếu pandoc: chạy step này trong môi trường có pandoc, hoặc tạm tạo bằng `pandoc -o app/documents/theme/reference.docx --print-default-data-file reference.docx` rồi rerun script để chỉnh font.)

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_document_theme.py -v`
Expected: PASS (3 test).

- [ ] **Step 8: Commit**

```bash
git add assets/fonts/ app/documents/theme/ tests/test_document_theme.py
git commit -m "feat(docs): bundle Vietnamese fonts + shared theme.css + Pandoc reference.docx"
```

---

## Task 3: render_core — Markdown → HTML

**Files:**
- Create: `app/documents/__init__.py`
- Create: `app/documents/render_core.py`
- Test: `tests/test_render_core.py` (create)

**Interfaces:**
- Produces:
  - `markdown_to_html(md: str, *, title: str | None = None) -> str` — full HTML doc, `<head>` nhúng `theme.css` inline, `<meta charset=utf-8>`, body chứa markdown đã render (GFM: table, strikethrough, task-list).
  - `theme_css_path() -> Path`, `theme_dir() -> Path`, `fonts_dir() -> Path`.
- Consumes: `app/documents/theme/theme.css` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_core.py
from app.documents.render_core import markdown_to_html, theme_css_path


def test_renders_heading_and_table():
    html = markdown_to_html("# Tiêu đề\n\n| A | B |\n|---|---|\n| 1 | 2 |", title="T")
    assert "<h1>" in html and "Tiêu đề" in html
    assert "<table>" in html and "<th>" in html


def test_vietnamese_preserved():
    md = "Tiếng Việt: ăâđêôơư ạảấầ — **đậm** *nghiêng* `mã`"
    html = markdown_to_html(md)
    assert "ăâđêôơư ạảấầ" in html
    assert "<strong>" in html and "<em>" in html and "<code>" in html


def test_embeds_theme_and_charset():
    html = markdown_to_html("hello", title="X")
    assert "charset" in html.lower()
    assert "@font-face" in html  # theme.css inlined
    assert theme_css_path().exists()


def test_strikethrough_and_tasklist():
    html = markdown_to_html("~~old~~\n\n- [x] done\n- [ ] todo")
    assert "<del>" in html or "<s>" in html
    assert "checkbox" in html.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_core.py -v`
Expected: FAIL — `ModuleNotFoundError: app.documents.render_core`.

- [ ] **Step 3: Tạo app/documents/__init__.py**

```python
# app/documents/__init__.py
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
```

- [ ] **Step 4: Tạo render_core.py**

```python
# app/documents/render_core.py
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
    # "gfm-like": strikethrough + table bật sẵn trong preset "commonmark"? -> bật thủ công.
    md = (
        MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
        .enable("table")
        .enable("strikethrough")
    )
    md.use(tasklists_plugin, enabled=True)
    return md


def render_body(md_text: str) -> str:
    """Chỉ render phần body HTML (không kèm <html>/theme) — dùng cho DOCX/preview."""
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_render_core.py -v`
Expected: PASS (4 test). Nếu `test_strikethrough` fail vì plugin name, dùng `from mdit_py_plugins.front_matter import ...`? Không — strikethrough là core rule `enable("strikethrough")`; task-list từ `mdit_py_plugins.tasklists`.

- [ ] **Step 6: Commit**

```bash
git add app/documents/__init__.py app/documents/render_core.py tests/test_render_core.py
git commit -m "feat(docs): markdown-it-py render core (GFM) with inlined theme"
```

---

## Task 4: pdf — HTML + theme → PDF (WeasyPrint)

**Files:**
- Create: `app/documents/pdf.py`
- Test: `tests/test_render_pdf.py` (create)

**Interfaces:**
- Consumes: `markdown_to_html` (Task 3), `fonts_dir`/`theme_dir`.
- Produces:
  - `pdf_available() -> bool`
  - `render_pdf(md_text: str, out_path: Path, *, title: str | None = None) -> Path` — ghi PDF có embed font Việt. Raise `RuntimeError` nếu WeasyPrint thiếu.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_pdf.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_pdf.py -v`
Expected: FAIL (`ModuleNotFoundError: app.documents.pdf`) — hoặc SKIP nếu máy không có weasyprint. Trong Docker/CI có weasyprint → phải FAIL trước khi implement.

- [ ] **Step 3: Tạo pdf.py**

```python
# app/documents/pdf.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_pdf.py -v`
Expected: PASS (hoặc SKIP nếu local thiếu weasyprint; bắt buộc PASS trong Docker).

- [ ] **Step 5: Commit**

```bash
git add app/documents/pdf.py tests/test_render_pdf.py
git commit -m "feat(docs): WeasyPrint PDF renderer with embedded Vietnamese fonts"
```

---

## Task 5: docx — Markdown → DOCX (Pandoc + reference.docx)

**Files:**
- Create: `app/documents/docx.py`
- Test: `tests/test_render_docx.py` (create)

**Interfaces:**
- Consumes: `app/documents/theme/reference.docx` (Task 2).
- Produces:
  - `docx_available() -> bool` (pandoc trên PATH)
  - `render_docx(md_text: str, out_path: Path, *, title: str | None = None) -> Path` — DOCX dùng reference.docx. Raise `RuntimeError` nếu pandoc thiếu.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_docx.py
import shutil
from pathlib import Path
import pytest

if shutil.which("pandoc") is None:
    pytest.skip("pandoc không có trên PATH", allow_module_level=True)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_docx.py -v`
Expected: FAIL (`ModuleNotFoundError`) — hoặc SKIP nếu không có pandoc.

- [ ] **Step 3: Tạo docx.py**

```python
# app/documents/docx.py
"""Markdown → DOCX qua Pandoc, style theo reference.docx."""
import shutil
from pathlib import Path

from app.documents.render_core import theme_dir

_REFERENCE = theme_dir() / "reference.docx"


def docx_available() -> bool:
    return shutil.which("pandoc") is not None


def render_docx(md_text: str, out_path: Path, *, title: str | None = None) -> Path:
    if not docx_available():
        raise RuntimeError("pandoc chưa được cài (cần để sinh .docx chuyên nghiệp).")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_docx.py -v`
Expected: PASS (hoặc SKIP nếu local thiếu pandoc; bắt buộc PASS trong Docker).

- [ ] **Step 5: Commit**

```bash
git add app/documents/docx.py tests/test_render_docx.py
git commit -m "feat(docs): Pandoc DOCX renderer using styled reference.docx"
```

---

## Task 6: xlsx — data → styled XLSX

**Files:**
- Create: `app/documents/xlsx.py`
- Test: `tests/test_render_xlsx.py` (create)

**Interfaces:**
- Produces:
  - `xlsx_available() -> bool`
  - `render_xlsx(out_path, *, sheets=None, headers=None, rows=None, title=None) -> Path` — chữ ký dữ liệu giống `handle_generate_excel` hiện tại (single-sheet: `headers`+`rows`+`title`; multi-sheet: `sheets=[{name,headers,rows}]`). Header style accent (#2563EB, chữ trắng đậm), zebra rows, freeze header, auto-width, font an toàn dấu Việt (Calibri).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_xlsx.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_xlsx.py -v`
Expected: FAIL — `ModuleNotFoundError: app.documents.xlsx`.

- [ ] **Step 3: Tạo xlsx.py**

```python
# app/documents/xlsx.py
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


def render_xlsx(out_path, *, sheets=None, headers=None, rows=None, title=None) -> Path:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_xlsx.py -v`
Expected: PASS (2 test).

- [ ] **Step 5: Commit**

```bash
git add app/documents/xlsx.py tests/test_render_xlsx.py
git commit -m "feat(docs): styled XLSX renderer (accent header, zebra, freeze, autowidth)"
```

---

## Task 7: Wire vào data.py tools (giữ schema, thêm check_fn)

**Files:**
- Modify: `app/mcp/tools/data.py` (thân `handle_generate_pdf:509-591`, `handle_generate_word_document:156-335`, `handle_generate_excel:631-686`; `register(generate_pdf)` thêm/đổi `check_fn`; `register(generate_word_document)` thêm `check_fn`)
- Test: `tests/test_data_tools_render_integration.py` (create)

**Interfaces:**
- Consumes: `app.documents.render_pdf/render_docx/render_xlsx`, `pdf.pdf_available`, `docx.docx_available`, `xlsx.xlsx_available`.
- Produces: handlers giữ nguyên signature `(params, **kwargs) -> dict` và keys `{success, output, data, artifacts}`; tên tool + schema không đổi.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data_tools_render_integration.py
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
    monkeypatch.setattr(data_tools, "_get_output_dir", lambda: tmp_path)
    res = await data_tools.handle_generate_word_document(
        {"title": "T", "content": "# H\n\nNội dung ăâđêôơư"})
    if shutil.which("pandoc"):
        assert res["success"] is True and (tmp_path / list(
            f.name for f in tmp_path.glob("*.docx"))[0]).exists()
    else:
        assert res["success"] is False and "pandoc" in res["output"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_data_tools_render_integration.py -v`
Expected: FAIL — `freeze_panes` chưa được set bởi handler cũ (handler cũ chưa gọi renderer mới).

- [ ] **Step 3: Thay thân handle_generate_excel**

Thay toàn bộ thân `handle_generate_excel` (giữ phần đọc params + chuẩn hoá filename) bằng:

```python
async def handle_generate_excel(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Generate styled Excel (.xlsx) — delegates to app.documents.xlsx."""
    filename = params.get("filename", "output.xlsx")
    if not filename.endswith(".xlsx"):
        filename += ".xlsx"
    try:
        from app.documents.xlsx import render_xlsx
        out = render_xlsx(
            _get_output_dir() / filename,
            sheets=params.get("sheets"),
            headers=params.get("headers"),
            rows=params.get("rows"),
            title=params.get("title", ""),
        )
        download_url = f"/api/v1/artifacts/download/{filename}"
        return {
            "success": True,
            "output": f"📊 Excel created: {filename} ({out.stat().st_size:,}b) | Download: {download_url}",
            "data": {"path": filename, "download_url": download_url},
            "artifacts": [{"type": "file", "path": filename, "format": "xlsx"}],
        }
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": f"❌ Excel generation failed: {e}", "data": {"error": str(e)}, "artifacts": []}
```

Xoá hàm `_fill_sheet` cũ (lines 689-725) — đã chuyển sang `app/documents/xlsx.py` (orphan do thay đổi này tạo ra).

- [ ] **Step 4: Thay thân handle_generate_pdf**

Thay phần thân (sau khi đọc `title/content/filename`) bằng:

```python
    try:
        from app.documents.pdf import render_pdf
        out = render_pdf(content, _get_output_dir() / filename, title=title)
    except Exception as e:  # noqa: BLE001
        logger.error("pdf_generation_failed", error=str(e))
        return {"success": False, "output": f"❌ PDF generation failed: {e}", "data": {"error": str(e)}, "artifacts": []}
    file_size = out.stat().st_size
    download_url = f"/api/v1/artifacts/download/{filename}"
    return {
        "success": True,
        "output": f"📄 PDF generated: {filename} ({file_size:,}b)\nDownload: {download_url}",
        "data": {"path": filename, "size": file_size, "download_url": download_url},
        "artifacts": [{"type": "document", "path": filename, "format": "pdf"}],
    }
```

Đổi `check_fn=_check_pdf_available` của `register(generate_pdf)` thành:

```python
def _check_pdf_available() -> bool:
    from app.documents.pdf import pdf_available
    return pdf_available()
```

- [ ] **Step 5: Thay thân handle_generate_word_document**

Thay phần thân parse-markdown-bằng-tay (toàn bộ khối `try: doc = Document()` … `doc.save`) bằng:

```python
    try:
        from app.documents.docx import render_docx, docx_available
        if not docx_available():
            return {"success": False,
                    "output": "❌ pandoc chưa được cài (cần để sinh .docx chuyên nghiệp). Cài: apt-get install pandoc",
                    "data": {"error": "pandoc not installed"}, "artifacts": []}
        out = render_docx(content, _get_output_dir() / filename, title=title)
    except Exception as e:  # noqa: BLE001
        logger.error("docx_generation_failed", error=str(e), title=title[:100])
        return {"success": False, "output": f"❌ DOCX generation failed: {e}", "data": {"error": str(e)}, "artifacts": []}
    file_size = out.stat().st_size
    download_url = f"/api/v1/artifacts/download/{filename}"
    return {
        "success": True,
        "output": f"Word doc created: {filename} ({file_size:,}b) | Download: {download_url}",
        "data": {"path": filename, "size": file_size, "download_url": download_url},
        "artifacts": [{"type": "document", "path": filename, "format": "docx"}],
    }
```

Thêm `check_fn` vào `register(generate_word_document)`:

```python
def _check_docx_render_available() -> bool:
    from app.documents.docx import docx_available
    return docx_available()
```

và truyền `check_fn=_check_docx_render_available` trong lời gọi `registry.register(name="generate_word_document", ...)`.

Xoá các helper chỉ phục vụ parser tay nếu không còn dùng: `_parse_markdown_table`, `_add_formatted_text` (orphan do thay đổi này tạo ra — kiểm tra không còn caller bằng grep trước khi xoá).

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_data_tools_render_integration.py -v`
Expected: PASS (excel PASS; word PASS nếu có pandoc, ngược lại assert nhánh gate).

- [ ] **Step 7: Verify orphan cleanup + registry vẫn load**

Run: `python -c "import app.mcp.tools.data; from app.mcp.tool_registry import registry; print('generate_pdf' in registry._tools, 'generate_excel' in registry._tools, 'generate_word_document' in registry._tools)"`
Expected: `True True True`
Run: `grep -n "_parse_markdown_table\|_add_formatted_text" app/mcp/tools/data.py` → không còn caller (chỉ định nghĩa hoặc rỗng).

- [ ] **Step 8: Commit**

```bash
git add app/mcp/tools/data.py tests/test_data_tools_render_integration.py
git commit -m "refactor(data): generate_pdf/word/excel delegate to app.documents (keep schema)"
```

---

## Task 8: Golden Markdown end-to-end test

**Files:**
- Create: `tests/fixtures/golden.md`
- Create: `tests/test_documents_golden_e2e.py`

**Interfaces:**
- Consumes: toàn bộ `app.documents` (Tasks 3-6).

- [ ] **Step 1: Tạo fixture golden.md**

```bash
mkdir -p tests/fixtures
```

```markdown
<!-- tests/fixtures/golden.md -->
# Báo cáo Tổng hợp — Summary Report

## Tiếng Việt đầy đủ dấu

Nội dung kiểm thử: ăâđêôơư ạảấầ ẩẫậ ọỏốồổỗộ ùúủũụ ưừửữự đ Đ.
English text with **bold**, *italic*, ~~strikethrough~~ and `inline code`.

### Danh sách
- Mục một
- Mục hai
  - Mục con
- [x] Đã xong
- [ ] Chưa xong

### Bảng dữ liệu
| Hạng mục | Giá trị | Ghi chú |
|----------|--------:|---------|
| Doanh thu | 1.000 | tăng |
| Chi phí   |   400 | giảm |

> Trích dẫn: chất lượng là ưu tiên.

```python
def xin_chao(ten: str) -> str:
    return f"Xin chào, {ten}!"
```

---
Kết thúc.
```

- [ ] **Step 2: Write the test**

```python
# tests/test_documents_golden_e2e.py
import shutil
from pathlib import Path
import pytest
from app.documents import markdown_to_html, render_xlsx

GOLDEN = Path("tests/fixtures/golden.md").read_text(encoding="utf-8")
VN_SAMPLE = "ăâđêôơư"


def test_html_has_vietnamese_and_structure():
    html = markdown_to_html(GOLDEN, title="Golden")
    assert VN_SAMPLE in html
    assert "<table>" in html and "<h1>" in html and "<del>" in html or "<s>" in html


def test_pdf_vietnamese_intact(tmp_path):
    pytest.importorskip("weasyprint")
    pdfplumber = pytest.importorskip("pdfplumber")
    from app.documents import render_pdf
    out = render_pdf(GOLDEN, tmp_path / "g.pdf", title="Golden")
    with pdfplumber.open(str(out)) as pdf:
        text = "".join(p.extract_text() or "" for p in pdf.pages)
    assert VN_SAMPLE in text


def test_docx_vietnamese_intact(tmp_path):
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc absent")
    from app.documents import render_docx
    from docx import Document
    out = render_docx(GOLDEN, tmp_path / "g.docx", title="Golden")
    full = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert VN_SAMPLE in full


def test_xlsx_vietnamese_intact(tmp_path):
    from openpyxl import load_workbook
    out = render_xlsx(tmp_path / "g.xlsx", headers=["Cột"], rows=[[VN_SAMPLE]])
    assert load_workbook(str(out)).active["A2"].value == VN_SAMPLE
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_documents_golden_e2e.py -v`
Expected: html + xlsx PASS; pdf/docx PASS hoặc SKIP tùy môi trường.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/golden.md tests/test_documents_golden_e2e.py
git commit -m "test(docs): golden markdown end-to-end (Vietnamese fidelity across formats)"
```

---

## Task 9: Backend preview_type "spreadsheet" cho xlsx/xls

**Files:**
- Modify: `app/api/v1/endpoints/artifacts.py:71` (`_PREVIEW_EXTENSIONS`)
- Test: `tests/test_preview_type_spreadsheet.py` (create)

**Interfaces:**
- Produces: `_get_preview_type("x.xlsx") == "spreadsheet"`; `.docx/.doc/.pptx/.ppt` vẫn `"document"`.
- **Lý do:** hiện `xlsx` map `"document"` → frontend route vào DocxViewer (mammoth) → hỏng. Tách ra để route đúng SpreadsheetViewer (Task 10).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preview_type_spreadsheet.py
from app.api.v1.endpoints.artifacts import _get_preview_type


def test_xlsx_is_spreadsheet():
    assert _get_preview_type("data.xlsx") == "spreadsheet"
    assert _get_preview_type("old.xls") == "spreadsheet"


def test_docx_still_document():
    assert _get_preview_type("a.docx") == "document"
    assert _get_preview_type("a.pptx") == "document"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_preview_type_spreadsheet.py -v`
Expected: FAIL — xlsx hiện trả `"document"`.

- [ ] **Step 3: Sửa _PREVIEW_EXTENSIONS**

Tại `app/api/v1/endpoints/artifacts.py`, đổi dòng `"document"` và thêm `"spreadsheet"`:

```python
    "document": {"docx", "doc", "pptx", "ppt"},
    "spreadsheet": {"xlsx", "xls"},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_preview_type_spreadsheet.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/artifacts.py tests/test_preview_type_spreadsheet.py
git commit -m "fix(artifacts): xlsx/xls -> 'spreadsheet' preview_type (was wrongly 'document')"
```

---

## Task 10: Frontend SpreadsheetViewer (SheetJS)

**Files:**
- Modify: `frontend/package.json` (thêm `"xlsx"`)
- Create: `frontend/src/components/artifacts/viewers/SpreadsheetViewer.tsx`
- Modify: `frontend/src/components/artifacts/viewers/index.ts`
- Modify: `frontend/src/types/artifacts.ts` (thêm `'spreadsheet'` vào `PreviewType`)
- Modify: `frontend/src/components/artifacts/FileViewer.tsx` (lazy import + case `'spreadsheet'`)

**Interfaces:**
- Consumes: `getDownloadUrl(filePath, taskId)` từ `../../../api/artifacts` (đã dùng trong DocxViewer).
- Produces: component `SpreadsheetViewer({ filePath, taskId })` render bảng có style + tab chọn sheet.

- [ ] **Step 1: Thêm dep SheetJS**

Run: `cd frontend && npm install xlsx@^0.18.5 && cd ..`
Expected: `package.json` có `"xlsx"`.

- [ ] **Step 2: Thêm 'spreadsheet' vào PreviewType**

Tại `frontend/src/types/artifacts.ts`, tìm union `PreviewType` và thêm `'spreadsheet'`:

```ts
export type PreviewType =
  | 'image' | 'video' | 'audio' | 'pdf' | 'markdown' | 'html'
  | 'document' | 'spreadsheet' | 'code' | 'text' | 'unknown';
```

(Giữ các giá trị hiện có; chỉ chèn `'spreadsheet'`. Nếu union đã liệt kê khác, chèn `| 'spreadsheet'` vào.)

- [ ] **Step 3: Tạo SpreadsheetViewer.tsx**

```tsx
// frontend/src/components/artifacts/viewers/SpreadsheetViewer.tsx
// ============================================================
// MekongAI Nexus - SpreadsheetViewer
// Reads .xlsx/.xls via SheetJS and renders styled tables with sheet tabs.
// ============================================================
import { useState, useEffect, useCallback } from 'react';
import * as XLSX from 'xlsx';
import { AlertCircle, Loader2 } from 'lucide-react';
import { getDownloadUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

interface SpreadsheetViewerProps {
  filePath: string;
  taskId?: string;
}

interface SheetData {
  name: string;
  rows: (string | number)[][];
}

export function SpreadsheetViewer({ filePath, taskId }: SpreadsheetViewerProps) {
  const [sheets, setSheets] = useState<SheetData[] | null>(null);
  const [active, setActive] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(getDownloadUrl(filePath, taskId));
      if (!res.ok) throw new Error(`Failed to fetch: ${res.statusText}`);
      const buf = await res.arrayBuffer();
      const wb = XLSX.read(buf, { type: 'array' });
      const parsed: SheetData[] = wb.SheetNames.map((name) => ({
        name,
        rows: XLSX.utils.sheet_to_json<(string | number)[]>(wb.Sheets[name], {
          header: 1,
          defval: '',
        }),
      }));
      setSheets(parsed);
      setActive(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to read spreadsheet');
    } finally {
      setIsLoading(false);
    }
  }, [filePath, taskId]);

  useEffect(() => { load(); }, [load]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-red-600">
        <AlertCircle className="w-8 h-8" />
        <p className="text-sm">{error}</p>
      </div>
    );
  }
  if (isLoading || !sheets) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
        <p className="text-sm text-gray-500">Reading spreadsheet...</p>
      </div>
    );
  }

  const current = sheets[active];
  const [head, ...body] = current.rows.length ? current.rows : [[]];

  return (
    <div className="flex flex-col h-full w-full">
      {sheets.length > 1 && (
        <div className="flex gap-1 px-3 py-2 border-b border-gray-200 bg-gray-50 overflow-x-auto shrink-0">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              type="button"
              onClick={() => setActive(i)}
              className={cn(
                'px-3 py-1 rounded-md text-xs font-medium whitespace-nowrap',
                i === active ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-200',
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-auto p-4">
        <table className="border-collapse text-sm">
          <thead>
            <tr>
              {head.map((cell, i) => (
                <th key={i} className="border border-gray-300 bg-blue-600 text-white font-semibold px-3 py-1.5 text-left whitespace-nowrap">
                  {String(cell)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, ri) => (
              <tr key={ri} className={ri % 2 ? 'bg-gray-50' : ''}>
                {head.map((_, ci) => (
                  <td key={ci} className="border border-gray-300 px-3 py-1.5 whitespace-nowrap">
                    {String(row[ci] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Export trong index.ts**

Thêm vào `frontend/src/components/artifacts/viewers/index.ts`:

```ts
export { SpreadsheetViewer } from './SpreadsheetViewer';
```

- [ ] **Step 5: Route trong FileViewer.tsx**

Thêm lazy import cạnh `DocxViewerLazy` (sau dòng 34):

```tsx
const SpreadsheetViewerLazy = lazy(() =>
  import('./viewers/SpreadsheetViewer').then((m) => ({ default: m.SpreadsheetViewer })),
);
```

Thêm `case` trong `renderViewer()` (cạnh `case 'document'`):

```tsx
      case 'spreadsheet':
        return (
          <Suspense fallback={suspenseFallback}>
            <SpreadsheetViewerLazy filePath={filePath} taskId={taskId} />
          </Suspense>
        );
```

- [ ] **Step 6: Verify build**

Run: `cd frontend && npm run build && cd ..`
Expected: build thành công, không lỗi type về `PreviewType` / import.

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/artifacts/viewers/SpreadsheetViewer.tsx frontend/src/components/artifacts/viewers/index.ts frontend/src/types/artifacts.ts frontend/src/components/artifacts/FileViewer.tsx
git commit -m "feat(frontend): SpreadsheetViewer (SheetJS) for xlsx/xls WYSIWYG preview"
```

---

## Task 11: Frontend theme khớp backend (font Việt cho md/docx preview)

**Files:**
- Create: `frontend/public/fonts/` (NotoSans + JetBrainsMono woff2/ttf)
- Create: `frontend/src/styles/document-theme.css`
- Modify: `frontend/src/main.tsx` (hoặc entry CSS) — import `document-theme.css`
- Modify: `frontend/src/components/ui/MarkdownRenderer.tsx` — bọc class `.doc-theme`
- Modify: `frontend/src/components/artifacts/viewers/DocxViewer.tsx` — bọc class `.doc-theme`

**Interfaces:**
- Produces: class CSS `.doc-theme` áp font Noto Sans (body) + JetBrains Mono (code) khớp `theme.css` backend → md/docx preview hiển thị dấu Việt đúng & giống file xuất ra.

- [ ] **Step 1: Copy fonts cho frontend**

```bash
mkdir -p frontend/public/fonts
cp assets/fonts/NotoSans-Regular.ttf assets/fonts/NotoSans-Bold.ttf assets/fonts/NotoSans-Italic.ttf assets/fonts/NotoSans-BoldItalic.ttf frontend/public/fonts/
cp assets/fonts/JetBrainsMono-Regular.ttf assets/fonts/JetBrainsMono-Bold.ttf frontend/public/fonts/
```

- [ ] **Step 2: Tạo document-theme.css**

```css
/* frontend/src/styles/document-theme.css
   Font tiếng Việt cho document preview, khớp app/documents/theme/theme.css. */
@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSans-Regular.ttf") format("truetype"); font-weight: 400; font-style: normal; font-display: swap; }
@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSans-Bold.ttf") format("truetype"); font-weight: 700; font-style: normal; font-display: swap; }
@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSans-Italic.ttf") format("truetype"); font-weight: 400; font-style: italic; font-display: swap; }
@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSans-BoldItalic.ttf") format("truetype"); font-weight: 700; font-style: italic; font-display: swap; }
@font-face { font-family: "JetBrains Mono"; src: url("/fonts/JetBrainsMono-Regular.ttf") format("truetype"); font-weight: 400; font-display: swap; }
@font-face { font-family: "JetBrains Mono"; src: url("/fonts/JetBrainsMono-Bold.ttf") format("truetype"); font-weight: 700; font-display: swap; }

.doc-theme { font-family: "Noto Sans", system-ui, sans-serif; }
.doc-theme code, .doc-theme pre { font-family: "JetBrains Mono", monospace; }
```

- [ ] **Step 3: Import CSS ở entry**

Trong `frontend/src/main.tsx`, thêm cạnh các import css hiện có:

```ts
import './styles/document-theme.css';
```

(Nếu entry là file khác — `index.tsx`/`App.tsx` với css import — thêm vào nơi đó. Tìm bằng `grep -rn "index.css\|\.css'" frontend/src/main.tsx frontend/src/App.tsx`.)

- [ ] **Step 4: Áp .doc-theme cho MarkdownRenderer**

Tại `frontend/src/components/ui/MarkdownRenderer.tsx`, thêm `doc-theme` vào class container ngoài cùng (nơi đang ghép `className`). Ví dụ wrapper `<div className={cn('prose ...', className)}>` → `cn('doc-theme prose ...', className)`.

- [ ] **Step 5: Áp .doc-theme cho DocxViewer**

Tại `frontend/src/components/artifacts/viewers/DocxViewer.tsx:74`, thêm `'doc-theme'` vào danh sách class của div `prose`:

```tsx
          'doc-theme max-w-[800px] mx-auto prose prose-sm',
```

- [ ] **Step 6: Verify build + manual check**

Run: `cd frontend && npm run build && cd ..`
Expected: build OK.
Manual: mở 1 file `.md` có tiếng Việt trong UI → chữ hiển thị font Noto Sans, dấu đúng, code block JetBrains Mono.

- [ ] **Step 7: Commit**

```bash
git add frontend/public/fonts/ frontend/src/styles/document-theme.css frontend/src/main.tsx frontend/src/components/ui/MarkdownRenderer.tsx frontend/src/components/artifacts/viewers/DocxViewer.tsx
git commit -m "feat(frontend): Vietnamese document theme fonts for md/docx preview (WYSIWYG)"
```

---

## Verification (toàn cục, sau Task 11)

- [ ] Backend tests: `python -m pytest tests/test_render_core.py tests/test_render_xlsx.py tests/test_data_tools_render_integration.py tests/test_preview_type_spreadsheet.py tests/test_documents_golden_e2e.py tests/test_document_theme.py tests/test_document_deps.py -v` → PASS (pdf/docx SKIP nếu local thiếu binary).
- [ ] Docker build: `docker build -t nexus-test .` → thành công; trong container chạy `pytest tests/test_render_pdf.py tests/test_render_docx.py -v` → PASS (font Việt nguyên vẹn — đây là bằng chứng fix lỗi font).
- [ ] Frontend: `cd frontend && npm run build` → OK; preview md/pdf/docx/xlsx hiển thị đúng dấu Việt.
- [ ] Registry không đổi schema: tool `generate_pdf/word_document/excel` vẫn load, tham số như cũ.

## Ngoài phạm vi (YAGNI)
- Multi-theme / branding (logo, màu thương hiệu).
- PowerPoint generation.
- Header/footer PDF tùy biến ngoài số trang.
