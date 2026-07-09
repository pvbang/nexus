# Thiết kế: Unified Document Rendering (md / docx / pdf / xlsx)

- **Ngày:** 2026-06-18
- **Trạng thái:** Đã duyệt thiết kế, chờ review spec → lập plan
- **Mục tiêu:** Dự án tạo ra file `.md`, `.docx`, `.pdf`, `.xlsx` với định dạng đẹp,
  chuyên nghiệp, nhất quán, **không lỗi font/ký tự** (đặc biệt tiếng Việt/tiếng Anh),
  ở cả khâu **convert (backend)** lẫn **hiển thị WYSIWYG (frontend)**.

---

## 1. Bối cảnh & vấn đề hiện tại

Hiện trạng (`app/mcp/tools/data.py`, 1244 dòng) sinh tài liệu rời rạc:

- `generate_pdf` dùng **reportlab** + parse Markdown bằng **regex thủ công**.
  Font core là Helvetica/Times → **vỡ dấu tiếng Việt** (không có glyph Việt, không embed font).
- `generate_word_document` cũng parse Markdown bằng **regex tay** (dễ sót cú pháp),
  style cứng rải rác trong code, không có hệ thống style nhất quán.
- `generate_excel` (openpyxl) UTF-8 OK nhưng style tối thiểu (chỉ header xanh).
- Frontend (React 19): có viewer cho md/pdf/docx/code nhưng **chưa có viewer XLSX**
  → không thể WYSIWYG đủ 4 định dạng.

Mỗi format style một kiểu → khó đồng bộ, khó maintain, và lỗi font tiếng Việt ở PDF.

## 2. Quyết định kiến trúc (đã chốt với user)

| Hạng mục | Quyết định |
|---|---|
| Engine tổng thể | **Pipeline thống nhất** — Markdown là nguồn duy nhất |
| Engine PDF | **WeasyPrint** (HTML + theme.css → PDF), control CSS hoàn toàn |
| Engine DOCX | **Pandoc + reference.docx** (bỏ hẳn parser regex tay) |
| Engine XLSX | **openpyxl** + bộ style theme (Excel khác bản chất, giữ riêng) |
| Style scope | **1 theme đẹp mặc định** áp cho mọi tài liệu |
| Frontend | **WYSIWYG** — preview khớp file thật, fix lỗi font viewer |

## 3. Kiến trúc tổng thể (Unified Render Core)

```
                         ┌──────────────────────────────┐
   Markdown (1 nguồn) →  │ render core (markdown-it-py)  │ → HTML (CommonMark + GFM)
                         └───────────────┬──────────────┘
                                         │
     ┌───────────────┬───────────────────┼──────────────────┬──────────────────┐
     ▼               ▼                   ▼                  ▼                  ▼
  .md (chuẩn      HTML + theme.css   HTML + theme.css    reference.docx     openpyxl
   hoá, giữ        → WeasyPrint       (frontend           (Pandoc)          + theme
   nguyên)         → .pdf             preview)            → .docx           → .xlsx
```

**Một "design language" sống ở 2 artifact** (Word không ăn CSS — đây là giới hạn thật):

1. **`theme.css`** — dùng chung cho **PDF (WeasyPrint)** *và* **frontend preview**.
   Là chỗ "fix font một lần": `@font-face` embed font Việt, typography, heading,
   bảng, code block, màu, lề trang.
2. **`reference.docx`** — template Word có sẵn named-style (Heading 1-4, Normal, Code,
   Table…) chỉnh khớp màu/typography với `theme.css`. DOCX **khớp** chứ không y hệt CSS.

**Font tiếng Việt** — bundle vào repo `assets/fonts/` (không phụ thuộc font hệ thống):

- Sans (body & heading): **Noto Sans** — phủ đủ dấu tiếng Việt.
- Mono (code block): **JetBrains Mono**.
- Embed trực tiếp vào PDF (WeasyPrint `@font-face`), nhúng vào `reference.docx`,
  serve cho frontend.
- XLSX: dùng font Excel an toàn dấu Việt (Calibri/Arial) — Excel không embed font
  nhưng dấu Việt vẫn hiển thị đúng.

## 4. Backend components

Tách phần *generation* khỏi `data.py` thành module gọn, mỗi unit một nhiệm vụ:

| Module | Nhiệm vụ | Phụ thuộc |
|---|---|---|
| `app/documents/theme/` | `theme.css`, `reference.docx`, font files — "design language" | — |
| `app/documents/render_core.py` | Markdown → HTML (markdown-it-py + GFM: table, strikethrough, task-list) | markdown-it-py |
| `app/documents/pdf.py` | HTML + theme.css → PDF | weasyprint |
| `app/documents/docx.py` | Markdown → DOCX qua Pandoc + reference.docx | pandoc (binary) |
| `app/documents/xlsx.py` | data → XLSX có style theme | openpyxl |

**Surgical scope:**
- Các tool `generate_pdf` / `generate_word_document` / `generate_excel` trong `data.py`
  trở thành **lớp mỏng** gọi vào module trên. **Giữ nguyên tên tool + schema** để không
  phá tích hợp agent.
- `generate_report` (.md) thêm bước chuẩn hoá nhẹ.
- `read_*` / `edit_*` (spreadsheet/document) và `convert_document` (markitdown):
  **giữ nguyên, không đụng.**

## 5. Frontend (WYSIWYG)

| Component | Thay đổi |
|---|---|
| `MarkdownViewer` / `MarkdownRenderer` | Nạp đúng font Việt + áp **cùng `theme.css`** với backend → preview md khớp PDF xuất ra |
| `PdfViewer` (react-pdf) | Đã render PDF thật → WYSIWYG; chỉ cần PDF backend nhúng font (lo ở §3) |
| `DocxViewer` (mammoth→HTML) | Áp style theme cho khớp |
| **`SpreadsheetViewer` (MỚI)** | Chưa có viewer xlsx → thêm mới: đọc xlsx (SheetJS) → bảng có style. Cần để đủ WYSIWYG 4 định dạng |

## 6. Error handling & độ bền

- Mỗi engine có `check_fn` kiểm tra binary/lib (tái dùng pattern sẵn trong `data.py`).
  Thiếu Pandoc/WeasyPrint → tool **tự gate, báo trung thực** (giống ffmpeg/tesseract),
  **không** fallback im lặng tạo file vỡ font.
- Render lỗi → trả lỗi rõ ràng kèm nguyên nhân, **không** tạo file rỗng.
- Font load từ `assets/fonts/` đóng gói trong repo + image → gốc rễ fix lỗi dấu Việt.

## 7. Docker

Thêm vào `Dockerfile` (stage backend, nối vào khối `apt-get` đã có ffmpeg/tesseract):

- `pandoc` (~150MB) — engine DOCX.
- Libs WeasyPrint: `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`,
  `libffi-dev`, `libcairo2`, `libgdk-pixbuf-2.0-0` (xác minh chính xác danh sách
  theo doc WeasyPrint khi implement).
- `pyproject.toml`: thêm `weasyprint`, `markdown-it-py` (+ plugins) vào extras phù hợp;
  Pandoc gọi qua subprocess (binary), hoặc `pypandoc` nếu tiện.

## 8. Testing — Success criteria

1. **Golden Markdown**: 1 file chứa heading H1-H4, `**bold**`/`*italic*`/`` `code` ``,
   bảng, list lồng, blockquote, code block, chuỗi tiếng Việt đầy đủ dấu
   (`Tiếng Việt: ăâđêôơư ạảấầ`), emoji, tiếng Anh → sinh ra cả `.pdf/.docx/.xlsx/.md`.
2. **Font Việt PDF**: trích text từ PDF xuất ra (pdfplumber/pypdf) → chuỗi tiếng Việt
   có dấu **nguyên vẹn** (test fail trước khi embed font, pass sau).
3. **DOCX**: đọc lại bằng python-docx → đúng heading-style, đúng số bảng/ô,
   text dấu Việt nguyên vẹn.
4. **XLSX**: đọc lại bằng openpyxl → đúng header style + dấu Việt.
5. **Smoke**: tool registry vẫn load, schema tool **không đổi** (không phá agent).

## 9. Ngoài phạm vi (YAGNI)

- Multi-theme / branding tùy biến (logo, màu thương hiệu) — chỉ 1 theme mặc định.
- PowerPoint generation — không nằm trong yêu cầu lần này.
- Header/footer trang PDF tùy biến — dùng mặc định của theme; mở rộng sau nếu cần.
