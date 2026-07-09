"""Convert MekongAI Nexus Proposal HTML to PPTX - Cool Light Theme"""
import os
from pptx import Presentation
from pptx.util import Inches, Pt, Emu, Cm
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.oxml import parse_xml

# Paths
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BASE))
IMAGES = os.path.join(ROOT, "frontend", "atsets", "images")
TESTS = os.path.join(ROOT, "tests")
OUTPUT = os.path.join(BASE, "MekongAI_Nexus_Proposal.pptx")

# Color palette - Cool Light Theme (Tailwind)
BRAND = RGBColor(0x25, 0x63, 0xEB)
TEAL = RGBColor(0x08, 0x91, 0xB2)
DARK = RGBColor(0x1E, 0x29, 0x3B)
MUTED = RGBColor(0x64, 0x74, 0x8B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BG = RGBColor(0xFA, 0xFB, 0xFC)
CARD_BG = RGBColor(0xF1, 0xF5, 0xF9)
BORDER = RGBColor(0xE2, 0xE8, 0xF0)
RED = RGBColor(0xEF, 0x44, 0x44)
GREEN = RGBColor(0x05, 0x96, 0x69)
PURPLE = RGBColor(0x8B, 0x5C, 0xF6)
PINK = RGBColor(0xEC, 0x48, 0x99)
ORANGE = RGBColor(0xF5, 0x9E, 0x0B)
INDIGO = RGBColor(0x63, 0x66, 0xF1)
CYAN = RGBColor(0x06, 0xB6, 0xD4)

# Presentation setup - widescreen 16:9
prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

# Use blank layout
BLANK_LAYOUT = prs.slide_layouts[6]


def set_slide_bg(slide, color=BG):
    """Set slide background color"""
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_text_box(slide, left, top, width, height, text="", font_size=18,
                 bold=False, color=DARK, align=PP_ALIGN.LEFT, font_name='Inter'):
    """Add a text box and return the text frame"""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.name = font_name
    p.alignment = align
    return tf


def add_paragraph(tf, text="", font_size=18, bold=False, color=DARK,
                  align=PP_ALIGN.LEFT, space_before=Pt(6), font_name='Inter'):
    """Add a paragraph to existing text frame"""
    p = tf.add_paragraph()
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.name = font_name
    p.alignment = align
    if space_before:
        p.space_before = space_before
    return p


def add_shape_with_text(slide, left, top, width, height, text="",
                        fill_color=WHITE, border_color=BORDER,
                        font_size=14, font_color=DARK, bold=False,
                        align=PP_ALIGN.LEFT, border_width=Pt(1)):
    """Add a rounded rectangle with text"""
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color
    shape.line.color.rgb = border_color
    shape.line.width = border_width
    shape.shadow.inherit = False
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = Pt(12)
    tf.margin_right = Pt(12)
    tf.margin_top = Pt(8)
    tf.margin_bottom = Pt(8)
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = font_color
    p.font.name = 'Inter'
    p.alignment = align
    return shape, tf


def slide_title(slide, title, subtitle=None, title_color=DARK):
    """Add standard slide title + optional subtitle"""
    tf = add_text_box(slide, Inches(0.7), Inches(0.4), Inches(11.5), Inches(0.8),
                      title, font_size=36, bold=False, color=title_color)
    if subtitle:
        add_paragraph(tf, subtitle, font_size=18, color=MUTED, space_before=Pt(4))
    return tf


def add_image_safe(slide, path, left, top, width=None, height=None):
    """Add image if exists, otherwise add placeholder"""
    if os.path.exists(path):
        kwargs = {"left": left, "top": top}
        if width:
            kwargs["width"] = width
        if height:
            kwargs["height"] = height
        return slide.shapes.add_picture(path, **kwargs)
    else:
        shape, _ = add_shape_with_text(slide, left, top,
                                        width or Inches(4), height or Inches(3),
                                        f"[Image: {os.path.basename(path)}]",
                                        fill_color=CARD_BG, font_color=MUTED)
        return shape


print("Setup complete. Building slides...")

# ============================================================
# SLIDE 1: Title
# ============================================================
s1 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s1, BG)

# Logo
logo_path = os.path.join(IMAGES, "mekongai-full-logo-and-text.png")
add_image_safe(s1, logo_path, Inches(5.2), Inches(0.6), height=Inches(0.7))

# "PROJECT PROPOSAL" label
add_text_box(s1, Inches(0), Inches(1.6), Inches(13.333), Inches(0.4),
             "PROJECT PROPOSAL", font_size=12, color=BRAND, align=PP_ALIGN.CENTER)

# Main title
add_text_box(s1, Inches(1.5), Inches(2.1), Inches(10.3), Inches(1.2),
             "MekongAI Nexus", font_size=54, bold=False, color=BRAND, align=PP_ALIGN.CENTER)

# Subtitle
add_text_box(s1, Inches(1.5), Inches(3.2), Inches(10.3), Inches(0.6),
             "Nền Tảng Điều Phối Đa Tác Nhân AI - Tự Lập Kế Hoạch, Tự Tranh Luận, Tự Học Hỏi",
             font_size=20, color=DARK, align=PP_ALIGN.CENTER)

# Stats line
add_text_box(s1, Inches(1.5), Inches(3.9), Inches(10.3), Inches(0.5),
             "80,000+ LOC | 172+ Tools | 66 Agents | 13 Security Layers | 9 Channels | Zero-Config Deploy",
             font_size=14, color=MUTED, align=PP_ALIGN.CENTER, font_name='JetBrains Mono')

# Badges
badges = ["Self-Orchestrating", "Self-Learning", "Self-Healing", "Self-Evolving"]
badge_x = 3.8
for i, badge in enumerate(badges):
    clr = BRAND if i % 2 == 0 else TEAL
    add_shape_with_text(s1, Inches(badge_x), Inches(4.7), Inches(1.8), Inches(0.4),
                        badge, fill_color=BG, border_color=clr,
                        font_size=11, font_color=clr, align=PP_ALIGN.CENTER)
    badge_x += 1.95

# Footer
add_text_box(s1, Inches(0), Inches(6.8), Inches(13.333), Inches(0.4),
             "Version 1.1.0  |  June 2026  |  MekongAI",
             font_size=12, color=MUTED, align=PP_ALIGN.CENTER)

print("  Slide 1: Title - done")

# ============================================================
# SLIDE 2: Problem & Solution
# ============================================================
s2 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s2, BG)
slide_title(s2, "Vấn Đề & Giải Pháp")

# Problem card (left)
shape_p, tf_p = add_shape_with_text(
    s2, Inches(0.5), Inches(1.3), Inches(6.0), Inches(4.5), "",
    fill_color=WHITE, border_color=RED)
tf_p.paragraphs[0].text = "Giới Hạn Của AI Agents Hiện Tại"
tf_p.paragraphs[0].font.size = Pt(16)
tf_p.paragraphs[0].font.bold = True
tf_p.paragraphs[0].font.color.rgb = RED

problems = [
    ("Single-agent:", "Claude Code, Cursor, Gemini CLI - chỉ 1 AI suy luận"),
    ("Không memory dài hạn:", "Mất toàn bộ ngữ cảnh khi hết session"),
    ("Không tự học:", "Cùng task lặp lại vẫn bắt đầu từ 0"),
    ("Vendor lock-in:", "Hết credit = dừng hoàn toàn, không fallback"),
    ("Crash = mất tiến độ:", "Không checkpoint, không resume"),
    ("Tools cố định:", "Không tự mở rộng capability runtime"),
]
for label, desc in problems:
    p = tf_p.add_paragraph()
    p.space_before = Pt(8)
    run = p.add_run()
    run.text = f"→ {label} "
    run.font.size = Pt(12)
    run.font.bold = True
    run.font.color.rgb = DARK
    run2 = p.add_run()
    run2.text = desc
    run2.font.size = Pt(12)
    run2.font.color.rgb = MUTED

# Solution card (right)
shape_s, tf_s = add_shape_with_text(
    s2, Inches(6.8), Inches(1.3), Inches(6.0), Inches(4.5), "",
    fill_color=WHITE, border_color=BRAND)
tf_s.paragraphs[0].text = "Giải Pháp MekongAI Nexus"
tf_s.paragraphs[0].font.size = Pt(16)
tf_s.paragraphs[0].font.bold = True
tf_s.paragraphs[0].font.color.rgb = BRAND

solutions = [
    ("7+ agents tranh luận:", "Structured debate 2 rounds → output chất lượng cao"),
    ("Memory 4 tầng (L0→L3):", "Tự chưng cất tri thức + Dreaming Engine mỗi 6h"),
    ("XSkill Learning:", "Tự trích xuất workflow từ task thành công → tái sử dụng"),
    ("8+ providers + FREE fallback:", "Hết credit? Tự chuyển sang LLM miễn phí"),
    ("Checkpoint PostgreSQL:", "Crash → tự resume chính xác tại turn đang chạy"),
    ("Self-Evolution:", "Tự sinh Python tools mới từ mô tả ngôn ngữ tự nhiên"),
]
for label, desc in solutions:
    p = tf_s.add_paragraph()
    p.space_before = Pt(8)
    run = p.add_run()
    run.text = f"→ {label} "
    run.font.size = Pt(12)
    run.font.bold = True
    run.font.color.rgb = DARK
    run2 = p.add_run()
    run2.text = desc
    run2.font.size = Pt(12)
    run2.font.color.rgb = MUTED

# Bottom commitment box
shape_c, tf_c = add_shape_with_text(
    s2, Inches(0.5), Inches(6.0), Inches(12.3), Inches(1.0), "",
    fill_color=WHITE, border_color=BRAND)
p0 = tf_c.paragraphs[0]
run_b = p0.add_run()
run_b.text = "Cam kết Zero-Downtime: "
run_b.font.size = Pt(11)
run_b.font.bold = True
run_b.font.color.rgb = BRAND
run_t = p0.add_run()
run_t.text = "Nếu không cấu hình API key, hoặc hết tiền giữa chừng → hệ thống tự chuyển sang chuỗi LLM miễn phí. Task KHÔNG BAO GIỜ bị dừng giữa chừng."
run_t.font.size = Pt(11)
run_t.font.color.rgb = DARK

print("  Slide 2: Problem & Solution - done")

# ============================================================
# SLIDE 3: Architecture Overview - 5 Layers
# ============================================================
s3 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s3, BG)
slide_title(s3, "Kiến Trúc Hệ Thống", "5 Tầng")

layers = [
    (TEAL, "Layer 1 - Presentation:", "React 19 + TypeScript 5.7 + Vite 6 | 20 routes | SSE + WebSocket real-time"),
    (BRAND, "Layer 2 - Orchestration:", "NexusExecutionPipeline | Think → Discuss → Execute | Evolution Agent"),
    (PURPLE, "Layer 3 - Intelligence:", "Memory L0→L3 | XSkill Learning | 172+ MCP Tools | RAG + Knowledge Graph"),
    (ORANGE, "Layer 4 - LLM Infrastructure:", "8+ Providers | Circuit Breaker | Fallback Chain | Smart Routing"),
    (GREEN, "Layer 5 - Data & Execution:", "PostgreSQL 16 + pgvector | Redis 7+ | OpenClaw Sandbox | Public Gateway"),
]
for i, (color, title, desc) in enumerate(layers):
    y = Inches(1.4 + i * 0.95)
    shape = s3.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.7), y, Inches(11.9), Inches(0.8))
    shape.fill.solid()
    shape.fill.fore_color.rgb = WHITE
    shape.line.color.rgb = color
    shape.line.width = Pt(2)
    tf = shape.text_frame
    tf.margin_left = Pt(16)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    run1 = p.add_run()
    run1.text = title + " "
    run1.font.size = Pt(14)
    run1.font.bold = True
    run1.font.color.rgb = color
    run2 = p.add_run()
    run2.text = desc
    run2.font.size = Pt(12)
    run2.font.color.rgb = MUTED

# Bottom badges
badge_data = [("13 Security Layers", BRAND), ("Crash Recovery", TEAL),
              ("Auto-Scaling", BRAND), ("Zero-Config Deploy", TEAL)]
bx = 3.0
for text, clr in badge_data:
    add_shape_with_text(s3, Inches(bx), Inches(6.4), Inches(1.8), Inches(0.35),
                        text, fill_color=BG, border_color=clr,
                        font_size=10, font_color=clr, align=PP_ALIGN.CENTER)
    bx += 2.0

print("  Slide 3: Architecture - done")

# ============================================================
# SLIDE 4: Harness Engineering
# ============================================================
s4 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s4, BG)
slide_title(s4, "Harness Engineering", "Kiến Trúc Điều Khiển AI - Framework CAR")

car_cards = [
    (BRAND, "Control Layer (Kiểm soát)",
     "System prompt modular - 10 sections, tối đa hóa prompt cache hit rate (~75% giảm chi phí).\n\nProduction Excellence - Mỗi agent tuân theo tiêu chuẩn: Web apps dùng framework thực, Security audit 10 tiêu chí CVE-specific."),
    (TEAL, "Agency Layer (Tự chủ)",
     "GoalEngine - Phân rã mục tiêu thành sub-goals.\n\nSelfReflection - Checkpoint chất lượng mỗi 3 tool rounds.\n\nMetacognitiveAgent - 6 câu hỏi: WHAT → WHY → ASSUMPTION → NEEDED → DIFFERENT FRAME → ASK USER?"),
    (RED, "Runtime Layer (Thực thi)",
     "Pipeline thống nhất - MỌI request đi qua cùng 1 engine.\n\nCheckpoint - PostgreSQL mỗi turn, max 3/task. Crash → resume.\n\nExponential backoff - 30s → 60s → 120s (cap 5 phút)."),
]
for i, (color, title, body) in enumerate(car_cards):
    x = Inches(0.5 + i * 4.2)
    shape, tf = add_shape_with_text(s4, x, Inches(1.6), Inches(3.9), Inches(4.8), "",
                                     fill_color=WHITE, border_color=color)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(14)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    for line in body.split('\n'):
        if line.strip():
            add_paragraph(tf, line.strip(), font_size=10, color=DARK if line.startswith(' ') or '—' in line else MUTED, space_before=Pt(6))

# Reference box
shape_r, tf_r = add_shape_with_text(
    s4, Inches(0.5), Inches(6.6), Inches(12.3), Inches(0.5), "",
    fill_color=CARD_BG, border_color=BORDER)
p_r = tf_r.paragraphs[0]
run_r = p_r.add_run()
run_r.text = "Tham khảo: "
run_r.font.size = Pt(10)
run_r.font.bold = True
run_r.font.color.rgb = BRAND
run_r2 = p_r.add_run()
run_r2.text = "Anthropic (effective harnesses), OpenAI (Codex), LangChain, Manus.im (KV-cache), HumanLayer (12 Factor Agents)"
run_r2.font.size = Pt(10)
run_r2.font.color.rgb = MUTED

print("  Slide 4: Harness Engineering - done")

# ============================================================
# SLIDE 5: Technology Stack
# ============================================================
s5 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s5, BG)
slide_title(s5, "Technology Stack")

tech_items = [
    ("Backend", "Python 3.12+ / FastAPI / SQLAlchemy 2.0", "80,000+ LOC | 300+ modules"),
    ("Frontend", "React 19 / TypeScript 5.7 / Vite 6 / Zustand 5", "160+ components | 22 hooks"),
    ("Database", "PostgreSQL 16 + pgvector + FTS", "25+ tables | 18 migrations"),
    ("Cache & Messaging", "Redis 7+ (Pub/Sub + Response Cache)", "Message Bus | Leader Election"),
    ("LLM", "LiteLLM + AsyncOpenAI SDK", "8+ providers | Any OpenAI-compatible"),
    ("Deployment", "Docker Compose (5 services)", "1 lệnh deploy | No GPU needed"),
]
for i, (title, desc, stat) in enumerate(tech_items):
    col = i % 3
    row = i // 3
    x = Inches(0.5 + col * 4.2)
    y = Inches(1.3 + row * 1.8)
    shape, tf = add_shape_with_text(s5, x, y, Inches(3.9), Inches(1.5), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(14)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = BRAND
    add_paragraph(tf, desc, font_size=11, color=DARK, space_before=Pt(6))
    add_paragraph(tf, stat, font_size=10, color=BRAND, space_before=Pt(4), font_name='JetBrains Mono')

# Stats row
stats = [("172+", "MCP Tools"), ("66", "Agent Templates"), ("39", "Built-in Skills"), ("9", "Channels")]
for i, (num, label) in enumerate(stats):
    x = Inches(1.5 + i * 2.8)
    add_text_box(s5, x, Inches(5.2), Inches(2.2), Inches(0.5),
                 num, font_size=32, color=BRAND, align=PP_ALIGN.CENTER)
    add_text_box(s5, x, Inches(5.8), Inches(2.2), Inches(0.4),
                 label, font_size=12, color=MUTED, align=PP_ALIGN.CENTER)

print("  Slide 5: Tech Stack - done")

# ============================================================
# SLIDE 6: Demo Chat Interface
# ============================================================
s6 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s6, BG)
slide_title(s6, "Demo: Chat Interface")

# Left text
tf6 = add_text_box(s6, Inches(0.5), Inches(1.3), Inches(5.5), Inches(3.5), "",
                   font_size=14, color=DARK)
tf6.paragraphs[0].text = "Giao diện chat 3-panel chuyên nghiệp:"
tf6.paragraphs[0].font.size = Pt(14)
tf6.paragraphs[0].font.bold = True
items = [
    "Navigation bar với tabs + model indicator",
    "Sidebar quản lý sessions + search full-text",
    "Panel chat: SSE streaming, Markdown, code highlighting",
    "Panel Process: Agent reasoning + tool execution real-time",
]
for item in items:
    add_paragraph(tf6, f"→ {item}", font_size=12, color=MUTED, space_before=Pt(8))

# Right screenshot
img_path = os.path.join(TESTS, "1. screenshot chat screen.png")
add_image_safe(s6, img_path, Inches(6.2), Inches(1.0), width=Inches(6.8))

print("  Slide 6: Demo Chat - done")

# ============================================================
# SLIDE 7: Demo Agent Management
# ============================================================
s7 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s7, BG)
slide_title(s7, "Demo: Agent Management")

# Left screenshot
img_path = os.path.join(TESTS, "3. screenshot multi agent info.png")
add_image_safe(s7, img_path, Inches(0.3), Inches(1.0), width=Inches(6.8))

# Right text
tf7 = add_text_box(s7, Inches(7.3), Inches(1.3), Inches(5.5), Inches(4.0), "",
                   font_size=14, color=DARK)
tf7.paragraphs[0].text = "Trang Agents - master-detail layout:"
tf7.paragraphs[0].font.size = Pt(14)
tf7.paragraphs[0].font.bold = True
items7 = [
    "66 agent templates sẵn có (8 lĩnh vực)",
    "Chi tiết: Description, Personality, Thinking Style",
    "Skills badges + Tools list (role-based access)",
    "Chat trực tiếp với agent + Spawn mới",
    "Logs + History cho audit trail",
]
for item in items7:
    add_paragraph(tf7, f"→ {item}", font_size=12, color=MUTED, space_before=Pt(8))

print("  Slide 7: Demo Agents - done")

# ============================================================
# SLIDE 8: Demo Multi-Agent Execution
# ============================================================
s8 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s8, BG)
slide_title(s8, "Demo: Multi-Agent Execution")

# Left text
tf8 = add_text_box(s8, Inches(0.5), Inches(1.3), Inches(5.5), Inches(4.0), "",
                   font_size=14, color=DARK)
tf8.paragraphs[0].text = "Process panel - theo dõi execution real-time:"
tf8.paragraphs[0].font.size = Pt(14)
tf8.paragraphs[0].font.bold = True
items8 = [
    "7 agents hoạt động đồng thời với status badges",
    "Discussion cards: Challenge → Rebuttal → Synthesis",
    "Self-Reflection cards (color-coded quality)",
    "Tool execution logs với Docker/nmap/Python output",
    "Evolution Agent suggestions inline",
]
for item in items8:
    add_paragraph(tf8, f"→ {item}", font_size=12, color=MUTED, space_before=Pt(8))

# Code block text
add_paragraph(tf8, "", font_size=8, space_before=Pt(12))
add_paragraph(tf8, "Think (temp 0.4) → Discuss (2 rounds) → Execute → Self-Reflect → Complete",
              font_size=10, color=BRAND, space_before=Pt(4), font_name='JetBrains Mono')

# Right screenshot
img_path = os.path.join(TESTS, "2. screenshot multi agent thinking and task excution.png")
add_image_safe(s8, img_path, Inches(6.2), Inches(1.0), width=Inches(6.8))

print("  Slide 8: Demo Multi-Agent - done")

# ============================================================
# SLIDE 9: Demo Knowledge Graph & XSkill
# ============================================================
s9 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s9, BG)
slide_title(s9, "Demo: Knowledge Graph & Continual Learning")

# Left image - Knowledge Graph
img_kg = os.path.join(TESTS, "6. screenshot Agent KnowledGraph.png")
add_image_safe(s9, img_kg, Inches(0.3), Inches(1.3), width=Inches(6.3))

# Right image - XSkill
img_xs = os.path.join(TESTS, "5. screenshot agent XSkill - Continual Learning.png")
add_image_safe(s9, img_xs, Inches(6.8), Inches(1.3), width=Inches(6.3))

# Bottom stats
add_text_box(s9, Inches(0.5), Inches(6.2), Inches(6.0), Inches(0.4),
             "37 nodes | 40 edges | 12 entity types - Auto-populated",
             font_size=11, color=TEAL, align=PP_ALIGN.CENTER)
add_text_box(s9, Inches(6.8), Inches(6.2), Inches(6.0), Inches(0.4),
             "39 skills | 60 experiences - Tự học từ mỗi task thành công",
             font_size=11, color=BRAND, align=PP_ALIGN.CENTER)

print("  Slide 9: Demo KG & XSkill - done")

# ============================================================
# SLIDE 10: Orchestration Pipeline - 8 Phases
# ============================================================
s10 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s10, BG)
slide_title(s10, "Orchestration Pipeline", "8 Phases - Điểm vào DUY NHẤT cho TẤT CẢ requests")

# Flow arrows
phases = [
    ("0. Context", TEAL), ("1. Plan", BRAND), ("2. Think", PURPLE),
    ("3. Discuss", ORANGE), ("4. Execute", GREEN), ("5. Evaluate", TEAL),
    ("6. Report", BRAND), ("7. Learn", MUTED),
]
for i, (name, color) in enumerate(phases):
    x = Inches(0.5 + i * 1.6)
    shape, tf = add_shape_with_text(s10, x, Inches(1.5), Inches(1.35), Inches(0.5),
                                     name, fill_color=WHITE, border_color=color,
                                     font_size=10, font_color=color, align=PP_ALIGN.CENTER)
    if i < len(phases) - 1:
        add_text_box(s10, Inches(0.5 + (i + 1) * 1.6 - 0.25), Inches(1.55), Inches(0.3), Inches(0.4),
                     "→", font_size=16, color=BRAND, align=PP_ALIGN.CENTER)

# Phase details grid (2x3)
phase_details = [
    ("Phase 0 - Smart Context", TEAL, "Fetch SONG SONG từ 7 nguồn, timeout 3s/nguồn. Budget 8-12KB."),
    ("Phase 1 - Planning", BRAND, "Phân tích complexity → chọn agents. Simple: 1 agent. Complex: 3-7 agents."),
    ("Phase 2-3 - Think + Discuss", PURPLE, "Agents suy luận (temp 0.4). Debate 2 rounds: Challenge → Rebuttal → Synthesis."),
    ("Phase 4 - Execute", GREEN, "Tool rounds KHÔNG GIỚI HẠN. Output capped 20KB. Full lưu ACTIVITY.md."),
    ("Phase 5 - Evaluate", TEAL, "Progress < 80% = NEVER complete. Delta > 5% = ALWAYS continue."),
    ("Phase 6-7 - Report + Learn", BRAND, "FINAL_REPORT.md multi-pass. Background: Memory + XSkill + GraphRAG."),
]
for i, (title, color, desc) in enumerate(phase_details):
    col = i % 2
    row = i // 2
    x = Inches(0.5 + col * 6.5)
    y = Inches(2.3 + row * 1.5)
    shape, tf = add_shape_with_text(s10, x, y, Inches(6.2), Inches(1.3), "",
                                     fill_color=WHITE, border_color=BORDER)
    p0 = tf.paragraphs[0]
    run1 = p0.add_run()
    run1.text = title + ": "
    run1.font.size = Pt(11)
    run1.font.bold = True
    run1.font.color.rgb = color
    run2 = p0.add_run()
    run2.text = desc
    run2.font.size = Pt(10)
    run2.font.color.rgb = MUTED

print("  Slide 10: Orchestration Pipeline - done")

# ============================================================
# SLIDE 11: Context Engineering
# ============================================================
s11 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s11, BG)
slide_title(s11, "Context Engineering", "Quản lý 'bộ nhớ làm việc' của AI")

# Left column - Modular Prompt
tf_left = add_text_box(s11, Inches(0.5), Inches(1.3), Inches(6.0), Inches(5.5), "",
                       font_size=12, color=DARK)
tf_left.paragraphs[0].text = "Modular Prompt Assembly"
tf_left.paragraphs[0].font.size = Pt(14)
tf_left.paragraphs[0].font.bold = True
tf_left.paragraphs[0].font.color.rgb = BRAND

prompt_sections = [
    ("1-4 (Static):", "Identity → System Info → Tool Catalog → File Rules"),
    ("5-7 (Semi-static):", "Behavior Rules → 5-Step Objective → Capabilities"),
    ("8-10 (Dynamic):", "Response Format → Skills (lazy-load) → Context"),
]
for label, desc in prompt_sections:
    p = tf_left.add_paragraph()
    p.space_before = Pt(8)
    r1 = p.add_run()
    r1.text = label + " "
    r1.font.size = Pt(10)
    r1.font.bold = True
    r1.font.color.rgb = BRAND
    r2 = p.add_run()
    r2.text = desc
    r2.font.size = Pt(10)
    r2.font.color.rgb = MUTED

add_paragraph(tf_left, "", font_size=8, space_before=Pt(12))
p_ep = tf_left.add_paragraph()
p_ep.space_before = Pt(8)
r_ep = p_ep.add_run()
r_ep.text = "Epoch Compression: "
r_ep.font.size = Pt(12)
r_ep.font.bold = True
r_ep.font.color.rgb = BRAND
r_ep2 = p_ep.add_run()
r_ep2.text = "Mỗi 10 turns, nén lịch sử thành structured summary. Task chạy 100+ turns mà KHÔNG mất context."
r_ep2.font.size = Pt(10)
r_ep2.font.color.rgb = MUTED

# Right column - Budget Allocation
tf_right = add_text_box(s11, Inches(6.8), Inches(1.3), Inches(6.0), Inches(5.5), "",
                        font_size=12, color=DARK)
tf_right.paragraphs[0].text = "Budget Allocation Algorithm"
tf_right.paragraphs[0].font.size = Pt(14)
tf_right.paragraphs[0].font.bold = True
tf_right.paragraphs[0].font.color.rgb = TEAL

budget_items = [
    "XSkill workflows: 90 (proven, tái sử dụng)",
    "Previous task context: 85 (session continuity)",
    "Active recall (user prefs): 70",
    "Knowledge Graph: 60",
    "Memory layers (L1/L2/L3): 50",
    "Agent memory files: 40",
    "",
    "Score = priority×0.6 + relevance×0.4",
    "Smart truncation tại ranh giới câu.",
]
for item in budget_items:
    add_paragraph(tf_right, item, font_size=10,
                  color=BRAND if "Score" in item else MUTED,
                  bold="Score" in item, space_before=Pt(6))

add_paragraph(tf_right, "", font_size=8, space_before=Pt(10))
p_sb = tf_right.add_paragraph()
p_sb.space_before = Pt(8)
r_sb = p_sb.add_run()
r_sb.text = "Self-Bootstrap: "
r_sb.font.size = Pt(12)
r_sb.font.bold = True
r_sb.font.color.rgb = TEAL
r_sb2 = p_sb.add_run()
r_sb2.text = "Agent KHÔNG BAO GIỜ nói 'không thể' - thiếu tool → scaffold_custom_tool_module()"
r_sb2.font.size = Pt(10)
r_sb2.font.color.rgb = MUTED

print("  Slide 11: Context Engineering - done")

# ============================================================
# SLIDE 12: Structured Discussion
# ============================================================
s12 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s12, BG)
slide_title(s12, "Structured Discussion", "Tại Sao Multi-Agent > Single-Agent")

add_text_box(s12, Inches(0.5), Inches(1.1), Inches(12.3), Inches(0.4),
             "7+ AI chuyên biệt tranh luận lẫn nhau → phát hiện blind spots, loại bỏ giả định sai.",
             font_size=13, color=MUTED)

# Three cards
disc_cards = [
    (ORANGE, "Round 1: Challenge",
     "Mỗi agent đọc thinking của đồng nghiệp → xác định điểm yếu (giả định sai) và điểm mạnh (cần bảo toàn)"),
    (BRAND, "Round 2: Rebuttal",
     "Agent bị challenge phải bảo vệ với evidence HOẶC chấp nhận sai và điều chỉnh. Không 'agree to disagree'"),
    (TEAL, "Synthesis",
     "Auto-generated: agreed_approaches + unresolved_disagreements + key_insights (phát hiện MỚI nhờ debate)"),
]
for i, (color, title, desc) in enumerate(disc_cards):
    x = Inches(0.5 + i * 4.2)
    shape, tf = add_shape_with_text(s12, x, Inches(1.7), Inches(3.9), Inches(3.5), "",
                                     fill_color=WHITE, border_color=color)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(16)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    add_paragraph(tf, desc, font_size=11, color=DARK, space_before=Pt(12))

# Bottom case study box
shape_cs, tf_cs = add_shape_with_text(
    s12, Inches(0.5), Inches(5.5), Inches(12.3), Inches(1.2), "",
    fill_color=WHITE, border_color=BORDER)
p_cs = tf_cs.paragraphs[0]
r_cs1 = p_cs.add_run()
r_cs1.text = "Kết quả thực tế: "
r_cs1.font.size = Pt(11)
r_cs1.font.bold = True
r_cs1.font.color.rgb = BRAND
r_cs2 = p_cs.add_run()
r_cs2.text = "CVE-2026-23918 - ban đầu CVSS 8.8, sau debate Forensics Analyst challenge rằng crash rate 100% + deterministic → team đồng thuận nâng lên 9.8 CRITICAL."
r_cs2.font.size = Pt(11)
r_cs2.font.color.rgb = DARK

print("  Slide 12: Structured Discussion - done")

# ============================================================
# SLIDE 13: 66 Agent Templates
# ============================================================
s13 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s13, BG)
slide_title(s13, "66 Agent Templates - 8 Lĩnh Vực")

agent_groups = [
    (BRAND, "Core/Foundational (12)", "Researcher, Developer, Analyst, Writer, Coordinator, Judge, Critic, Architect, Debugger, Orchestrator"),
    (ORANGE, "Security Operations (5)", "Pentester, Recon Specialist, Exploit Developer, DevOps, Vulnerability Researcher"),
    (TEAL, "Specialized Technical (7)", "Frontend/Backend Dev, DB Engineer, Data Scientist, UX Designer, PM, QA Engineer"),
    (PURPLE, "Business & Finance (5)", "Quant Trader, Macro Economist, Legal Advisor, Business Strategist, Accountant"),
    (PINK, "Intelligence (13)", "OSINT, Threat Hunter, Forensics, Counter-Intel, SIGINT, FININT, Cyber Warfare, Malware..."),
    (ORANGE, "Creative & Others (24)", "Multimodal Creator, Linguist, Historian, Physicist, Medical Researcher, Educator..."),
]
for i, (color, title, desc) in enumerate(agent_groups):
    col = i % 3
    row = i // 3
    x = Inches(0.5 + col * 4.2)
    y = Inches(1.3 + row * 2.3)
    shape, tf = add_shape_with_text(s13, x, y, Inches(3.9), Inches(2.0), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(12)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    add_paragraph(tf, desc, font_size=10, color=MUTED, space_before=Pt(8))

# Bottom info
add_text_box(s13, Inches(0.5), Inches(6.1), Inches(12.3), Inches(0.5),
             "Mỗi template: personality + thinking_style + domain knowledge (300-1000 từ) + skills dict + specific tools + methodology",
             font_size=12, color=DARK, align=PP_ALIGN.CENTER)

print("  Slide 13: Agent Templates - done")

# ============================================================
# SLIDE 14: Memory System L0→L3
# ============================================================
s14 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s14, BG)
slide_title(s14, "Memory System (L0→L3)", "Vượt xa 'session memory' - Tự CHƯNG CẤT knowledge qua 4 tầng")

# Memory layers
mem_layers = [
    (GREEN, "L3 - Persona Synthesis", "Hồ sơ toàn diện user/agent, versioned. Kích hoạt mỗi 50 atoms mới."),
    (BRAND, "L2 - Scene Aggregation", "Gom atoms → theme blocks (max 15 scenes). Delay 90s sau L1."),
    (PURPLE, "L1 - Atom Extraction", "4 types: PERSONA, EPISODIC, INSTRUCTION, FACTUAL. Dedup 0.85."),
    (MUTED, "L0 - Capture Tracker", "Đếm messages, warmup thresholds (1→2→4→N). Kích hoạt L1 mỗi 5 turns."),
]
for i, (color, title, desc) in enumerate(mem_layers):
    y = Inches(1.5 + i * 1.1)
    shape = s14.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.5), y, Inches(8.0), Inches(0.9))
    shape.fill.solid()
    shape.fill.fore_color.rgb = WHITE
    shape.line.color.rgb = color
    shape.line.width = Pt(2)
    tf = shape.text_frame
    tf.margin_left = Pt(16)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    r1 = p.add_run()
    r1.text = title + "  "
    r1.font.size = Pt(13)
    r1.font.bold = True
    r1.font.color.rgb = color
    r2 = p.add_run()
    r2.text = desc
    r2.font.size = Pt(11)
    r2.font.color.rgb = MUTED

# Right side - Dreaming Engine card
shape_d, tf_d = add_shape_with_text(
    s14, Inches(8.8), Inches(1.5), Inches(4.0), Inches(4.4), "",
    fill_color=WHITE, border_color=BRAND)
tf_d.paragraphs[0].text = "Dreaming Engine"
tf_d.paragraphs[0].font.size = Pt(14)
tf_d.paragraphs[0].font.bold = True
tf_d.paragraphs[0].font.color.rgb = BRAND

add_paragraph(tf_d, "Chu kỳ 6 giờ:", font_size=11, color=DARK, space_before=Pt(10))
add_paragraph(tf_d, "Light Sleep → Deep Sleep → REM Sleep", font_size=10, color=MUTED, space_before=Pt(4))
add_paragraph(tf_d, "", font_size=8, space_before=Pt(10))
add_paragraph(tf_d, "Active Recall", font_size=12, bold=True, color=TEAL, space_before=Pt(6))
add_paragraph(tf_d, "Pre-reply search, multi-source parallel", font_size=10, color=MUTED, space_before=Pt(4))
add_paragraph(tf_d, "", font_size=8, space_before=Pt(10))
add_paragraph(tf_d, "Recall Strategy", font_size=12, bold=True, color=TEAL, space_before=Pt(6))
add_paragraph(tf_d, "keyword_overlap × 0.4 + source_priority × 0.4 + length × 0.2", font_size=10, color=MUTED, space_before=Pt(4))

print("  Slide 14: Memory System - done")

# ============================================================
# SLIDE 15: XSkill Continual Learning
# ============================================================
s15 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s15, BG)
slide_title(s15, "XSkill Continual Learning", "Hệ thống tự cải thiện - học từ mỗi task execution thành công")

# Flow
xskill_flow = ["Task Execution", "Trivial Gate", "Experience Capture", "LLM Critique (5-dim)", "Skill Extraction", "Reuse (priority 90)"]
for i, step in enumerate(xskill_flow):
    x = Inches(0.3 + i * 2.15)
    clr = BRAND if i < 5 else WHITE
    bg = WHITE if i < 5 else BRAND
    shape, tf = add_shape_with_text(s15, x, Inches(1.5), Inches(1.9), Inches(0.5),
                                     step, fill_color=bg, border_color=BRAND,
                                     font_size=9, font_color=clr, align=PP_ALIGN.CENTER)
    if i < len(xskill_flow) - 1:
        add_text_box(s15, Inches(0.3 + (i + 1) * 2.15 - 0.2), Inches(1.55), Inches(0.2), Inches(0.4),
                     "→", font_size=14, color=BRAND, align=PP_ALIGN.CENTER)

# Three cards
xskill_cards = [
    (TEAL, "SkillBuilder", "Trích xuất workflows tái sử dụng cấp task - phased execution plans"),
    (TEAL, "ExperienceManager", "Bài học chiến thuật cấp hành động - what worked, what didn't"),
    (TEAL, "TrajectoryCompressor", "Nén execution logs → SFT training data - lossless compression"),
]
for i, (color, title, desc) in enumerate(xskill_cards):
    x = Inches(0.5 + i * 4.2)
    shape, tf = add_shape_with_text(s15, x, Inches(2.4), Inches(3.9), Inches(1.8), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(14)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    add_paragraph(tf, desc, font_size=11, color=MUTED, space_before=Pt(8))

# Bottom highlight
shape_h, tf_h = add_shape_with_text(
    s15, Inches(2.0), Inches(4.5), Inches(9.3), Inches(0.6), "",
    fill_color=WHITE, border_color=BRAND)
tf_h.paragraphs[0].text = "Priority Score 90 - cao nhất trong context budget. Skills được inject trước mọi nguồn khác."
tf_h.paragraphs[0].font.size = Pt(12)
tf_h.paragraphs[0].font.color.rgb = BRAND
tf_h.paragraphs[0].alignment = PP_ALIGN.CENTER

print("  Slide 15: XSkill - done")

# ============================================================
# SLIDE 16: 172+ MCP Tools
# ============================================================
s16 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s16, BG)
slide_title(s16, "172+ MCP Tools", "57 Modules")

tool_cats = [
    (BRAND, "File & Code", "22 tools"),
    (TEAL, "Terminal & Dev", "24 tools"),
    (PURPLE, "AI & Intelligence", "18 tools"),
    (RED, "Security", "17 tools"),
    (PINK, "Web & Browser", "15 tools"),
    (ORANGE, "Data & Output", "14 tools"),
    (GREEN, "Infrastructure", "19 tools"),
    (CYAN, "Utilities", "12 tools"),
]
for i, (color, title, count) in enumerate(tool_cats):
    col = i % 4
    row = i // 4
    x = Inches(0.5 + col * 3.2)
    y = Inches(1.3 + row * 1.3)
    shape, tf = add_shape_with_text(s16, x, y, Inches(2.9), Inches(1.0), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(13)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    add_paragraph(tf, count, font_size=11, color=MUTED, space_before=Pt(4))

# Self-Evolution card
shape_se, tf_se = add_shape_with_text(
    s16, Inches(0.5), Inches(3.9), Inches(12.3), Inches(1.8), "",
    fill_color=WHITE, border_color=TEAL)
tf_se.paragraphs[0].text = "Self-Evolution - Tự Tạo Tools Không Giới Hạn"
tf_se.paragraphs[0].font.size = Pt(16)
tf_se.paragraphs[0].font.bold = True
tf_se.paragraphs[0].font.color.rgb = TEAL
add_paragraph(tf_se, "scaffold_custom_tool_module() - Sinh Python implementation từ mô tả ngôn ngữ tự nhiên → validate → register → sử dụng ngay",
              font_size=11, color=DARK, space_before=Pt(10))
add_paragraph(tf_se, "create_workflow_tool() - Multi-step workflow thành single tool (chains existing tools + Docker commands)",
              font_size=11, color=DARK, space_before=Pt(6))

# Execution tiers
add_text_box(s16, Inches(0.5), Inches(6.0), Inches(12.3), Inches(0.4),
             "Tier 0: In-Process  |  Tier 1: Docker Security Toolkit  |  Tier 2: Remote Device Bridge  |  Tier 3: Cloud Infrastructure",
             font_size=11, color=BRAND, align=PP_ALIGN.CENTER)

print("  Slide 16: MCP Tools - done")

# ============================================================
# SLIDE 17: LLM Provider Layer
# ============================================================
s17 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s17, BG)
slide_title(s17, "LLM Provider Layer", "Không Bao Giờ Chết - Tự chuyển sang LLM miễn phí khi cần")

providers = [
    (BRAND, "AWS Bedrock", "Claude Opus/Sonnet\nChất lượng cao nhất"),
    (GREEN, "OpenAI", "GPT-4o, GPT-4.1\n60-120s timeout"),
    (ORANGE, "Google Gemini", "Gemini 2.5 Flash\nFree tier available"),
    (PURPLE, "OpenRouter", "100+ models\nNhiều model miễn phí"),
    (TEAL, "Kilo (FREE)", "200 req/hr/IP\nKhông cần auth"),
    (TEAL, "Pollinations (FREE)", "Unlimited requests\nKhông cần auth"),
    (TEAL, "g4f (FREE)", "Anonymous multi-provider\nBackup cuối cùng"),
    (MUTED, "Local/Ollama", "Qwen, Llama\nHoàn toàn offline"),
]
for i, (color, title, desc) in enumerate(providers):
    col = i % 4
    row = i // 4
    x = Inches(0.5 + col * 3.2)
    y = Inches(1.3 + row * 1.8)
    bg_color = CARD_BG if "FREE" in title else WHITE
    shape, tf = add_shape_with_text(s17, x, y, Inches(2.9), Inches(1.5), "",
                                     fill_color=bg_color, border_color=color)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(12)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    for line in desc.split('\n'):
        add_paragraph(tf, line, font_size=10, color=MUTED, align=PP_ALIGN.CENTER, space_before=Pt(4))

# Bottom row - Circuit Breaker, Fallback, Smart Routing
infra_cards = [
    (BRAND, "Circuit Breaker", "5 fails/30s → cách ly → thử phục hồi sau 60s"),
    (BRAND, "Fallback Chain (15 attempts)", "Core → Agent → Kilo → Pollinations → g4f"),
    (BRAND, "Smart Routing", "Tin nhắn ngắn → model rẻ. Khác → model mạnh"),
]
for i, (color, title, desc) in enumerate(infra_cards):
    x = Inches(0.5 + i * 4.2)
    shape, tf = add_shape_with_text(s17, x, Inches(5.0), Inches(3.9), Inches(1.2), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(11)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    add_paragraph(tf, desc, font_size=10, color=MUTED, space_before=Pt(6))

print("  Slide 17: LLM Providers - done")

# ============================================================
# SLIDE 18: Security Architecture - 13 Layers
# ============================================================
s18 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s18, BG)
slide_title(s18, "Security Architecture", "13 Layers - Defense-in-Depth")

sec_layers = [
    (RED, "1. CommandGuard", "Dangerous command detection"),
    (ORANGE, "2. SSRF Guard", "DNS pinning, private network blocking"),
    (ORANGE, "3. Tool Policy", "Per-role authorization"),
    (ORANGE, "4. Exfiltration Guard", "Block secret leaks"),
    (GREEN, "5. Secret Redaction", "20+ patterns masking"),
    (GREEN, "6. Rate Limiter", "Redis sliding-window"),
    (GREEN, "7. Session Mutex", "Concurrency control"),
    (TEAL, "8. Approval Workflow", "Human-in-the-loop"),
    (TEAL, "9. Audit Logging", "Complete event trail"),
    (BRAND, "10. Atomic Writes", "Transaction-safe ops"),
    (BRAND, "11. Circuit Breaker", "Prevent cascade failures"),
    (INDIGO, "12. Resource Monitor", "System limits"),
    (PURPLE, "13. JWT Authentication", "Token-based auth"),
]
for i, (color, title, desc) in enumerate(sec_layers):
    col = i % 2
    row = i // 2
    x = Inches(0.5 + col * 6.5)
    y = Inches(1.3 + row * 0.82)
    shape = s18.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, Inches(6.2), Inches(0.7))
    shape.fill.solid()
    shape.fill.fore_color.rgb = WHITE
    shape.line.color.rgb = color
    shape.line.width = Pt(1.5)
    tf = shape.text_frame
    tf.margin_left = Pt(12)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    r1 = p.add_run()
    r1.text = title + "  "
    r1.font.size = Pt(12)
    r1.font.bold = True
    r1.font.color.rgb = color
    r2 = p.add_run()
    r2.text = "- " + desc
    r2.font.size = Pt(11)
    r2.font.color.rgb = MUTED

print("  Slide 18: Security - done")

# ============================================================
# SLIDE 19: Cognitive Upgrade - 3 Layers
# ============================================================
s19 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s19, BG)
slide_title(s19, "Cognitive Upgrade", "3 Tầng Trí Tuệ")

cog_cards = [
    (BRAND, "Layer 1: Self-Awareness",
     "InfrastructureMap - Agents biết CHÍNH XÁC capabilities tại mọi thời điểm.\n\nLLM status, Cloud providers, Toolset health, Self-Bootstrap options.\n\nKHÔNG BAO GIỜ nói 'không thể' - bootstrap trước!"),
    (PURPLE, "Layer 2: Metacognition",
     "MetacognitiveAgent - Phân tích TẠI SAO stuck.\n\n6-Point Framework:\nWHAT → WHY → ASSUMPTION → NEEDED → DIFFERENT FRAME → ASK USER?\n\nKhông lặp lại sai lầm."),
    (TEAL, "Layer 3: Divergence",
     "DivergentThinker - 3 cách tiếp cận song song (temp 1.0).\n\nFrames:\n• Production Excellence\n• Unconventional\n• Reverse Engineering\n\nChọn best, không đi shortcut."),
]
for i, (color, title, body) in enumerate(cog_cards):
    x = Inches(0.5 + i * 4.2)
    shape, tf = add_shape_with_text(s19, x, Inches(1.5), Inches(3.9), Inches(5.2), "",
                                     fill_color=WHITE, border_color=color)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(15)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    for line in body.split('\n'):
        if line.strip():
            add_paragraph(tf, line.strip(), font_size=10, color=DARK, space_before=Pt(5))

print("  Slide 19: Cognitive - done")

# ============================================================
# SLIDE 20: Multi-Channel - 9 Platforms
# ============================================================
s20 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s20, BG)
slide_title(s20, "Multi-Channel - 9 Nền Tảng", "Một hệ thống - mọi kênh giao tiếp")

channels = [
    ("Web UI", "Full-featured, 20 routes, SSE streaming"),
    ("Telegram", "Rich messages, inline keyboards"),
    ("Discord", "Slash commands, embeds"),
    ("Slack", "Block Kit, Socket Mode"),
    ("WhatsApp", "Baileys / Cloud API"),
    ("Email", "IMAP + SMTP, threads"),
    ("SMS", "Twilio API"),
    ("Signal", "E2E encrypted"),
    ("Matrix", "Decentralized, E2EE"),
]
for i, (title, desc) in enumerate(channels):
    col = i % 3
    row = i // 3
    x = Inches(1.5 + col * 3.6)
    y = Inches(1.6 + row * 1.7)
    shape, tf = add_shape_with_text(s20, x, y, Inches(3.2), Inches(1.4), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(14)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = DARK
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    add_paragraph(tf, desc, font_size=11, color=MUTED, align=PP_ALIGN.CENTER, space_before=Pt(6))

# Bottom
add_text_box(s20, Inches(0), Inches(6.8), Inches(13.333), Inches(0.4),
             "+ Webhook Generic  |  Message Router thông minh  |  Stateful cross-channel",
             font_size=12, color=TEAL, align=PP_ALIGN.CENTER)

print("  Slide 20: Multi-Channel - done")

# ============================================================
# SLIDE 21: Case Study - Penetration Testing
# ============================================================
s21 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s21, BG)
slide_title(s21, "Case Study: Penetration Testing")

# Left info
tf21 = add_text_box(s21, Inches(0.5), Inches(1.2), Inches(6.5), Inches(5.5), "", font_size=12, color=DARK)
info_items = [
    ("Target:", "haufo.hanoi.gov.vn (Government website)"),
    ("Team:", "7 specialized agents, 18 turns tự động"),
    ("Bắt đầu:", "Zero knowledge - không thông tin trước"),
]
for label, val in info_items:
    p = tf21.add_paragraph()
    p.space_before = Pt(6)
    r1 = p.add_run()
    r1.text = label + " "
    r1.font.size = Pt(12)
    r1.font.bold = True
    r1.font.color.rgb = BRAND
    r2 = p.add_run()
    r2.text = val
    r2.font.size = Pt(12)
    r2.font.color.rgb = DARK

add_paragraph(tf21, "", font_size=8, space_before=Pt(10))
add_paragraph(tf21, "Kết Quả:", font_size=14, bold=True, color=DARK, space_before=Pt(6))
results = [
    "5 loại vulnerability discovered",
    "Default credentials: admin:123qwe → Full Admin JWT",
    "59 user accounts leaked (unauthenticated API)",
    "120+ configuration files extracted (MinIO)",
    "7 Python exploit scripts ready-to-run",
    "8 comprehensive reports (OWASP methodology)",
]
for r in results:
    add_paragraph(tf21, f"→ {r}", font_size=11, color=MUTED, space_before=Pt(5))

# Right stats
stats_cs1 = [("34 MB", "Artifacts"), ("150+", "Files Generated"), ("66%", "Progress (WAF blocked)"), ("18", "Execution Turns")]
for i, (num, label) in enumerate(stats_cs1):
    col = i % 2
    row = i // 2
    x = Inches(7.5 + col * 2.8)
    y = Inches(1.3 + row * 2.0)
    shape, tf = add_shape_with_text(s21, x, y, Inches(2.5), Inches(1.6), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = num
    tf.paragraphs[0].font.size = Pt(28)
    tf.paragraphs[0].font.bold = False
    tf.paragraphs[0].font.color.rgb = BRAND
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    add_paragraph(tf, label, font_size=11, color=MUTED, align=PP_ALIGN.CENTER, space_before=Pt(4))

# Adaptive note
shape_a, tf_a = add_shape_with_text(
    s21, Inches(7.2), Inches(5.5), Inches(5.8), Inches(1.0), "",
    fill_color=WHITE, border_color=ORANGE)
tf_a.paragraphs[0].text = "Adaptive: Khi bị block IP → tự pivot sang document exploitation và viết workbook cho clean IP execution"
tf_a.paragraphs[0].font.size = Pt(10)
tf_a.paragraphs[0].font.color.rgb = MUTED

print("  Slide 21: Case Study Pentest - done")

# ============================================================
# SLIDE 22: Case Study - CVE Research
# ============================================================
s22 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s22, BG)
slide_title(s22, "Case Study: CVE-2026-23918 Research")

# Left info
tf22 = add_text_box(s22, Inches(0.5), Inches(1.2), Inches(6.5), Inches(5.5), "", font_size=12, color=DARK)
info_cve = [
    ("Target:", "Apache HTTP Server 2.4.66 mod_http2 double-free"),
    ("Team:", "7 agents - researcher, lab builder, exploit engineer, forensics..."),
    ("Yêu cầu:", "Research → Build Lab → Prove → Evidence → Report"),
]
for label, val in info_cve:
    p = tf22.add_paragraph()
    p.space_before = Pt(6)
    r1 = p.add_run()
    r1.text = label + " "
    r1.font.size = Pt(12)
    r1.font.bold = True
    r1.font.color.rgb = TEAL
    r2 = p.add_run()
    r2.text = val
    r2.font.size = Pt(12)
    r2.font.color.rgb = DARK

add_paragraph(tf22, "", font_size=8, space_before=Pt(10))
add_paragraph(tf22, "Kết Quả:", font_size=14, bold=True, color=DARK, space_before=Pt(6))
results_cve = [
    "CVSS 9.8 CRITICAL - confirmed",
    "100% crash rate (32/32 attempts)",
    "Root cause: duplicate entry m→spurge array",
    "3 exploit vectors developed",
    "Detection tool h2ghost.py - standalone",
    "Docker ASAN lab TỰ ĐỘNG xây dựng",
]
for r in results_cve:
    add_paragraph(tf22, f"→ {r}", font_size=11, color=MUTED, space_before=Pt(5))

# Right stats
stats_cve = [("9.8", "CVSS Score"), ("100%", "Crash Rate"), ("32", "Crashes Proven"), ("2.9 MB", "Artifacts")]
for i, (num, label) in enumerate(stats_cve):
    col = i % 2
    row = i // 2
    x = Inches(7.5 + col * 2.8)
    y = Inches(1.3 + row * 2.0)
    shape, tf = add_shape_with_text(s22, x, y, Inches(2.5), Inches(1.6), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = num
    tf.paragraphs[0].font.size = Pt(28)
    tf.paragraphs[0].font.bold = False
    tf.paragraphs[0].font.color.rgb = TEAL
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    add_paragraph(tf, label, font_size=11, color=MUTED, align=PP_ALIGN.CENTER, space_before=Pt(4))

# Bottom note
shape_det, tf_det = add_shape_with_text(
    s22, Inches(7.2), Inches(5.5), Inches(5.8), Inches(1.0), "",
    fill_color=WHITE, border_color=TEAL)
tf_det.paragraphs[0].text = "Deterministic - NOT race condition. Từ zero → confirmed vulnerability trong 1 session tự động."
tf_det.paragraphs[0].font.size = Pt(10)
tf_det.paragraphs[0].font.color.rgb = TEAL

print("  Slide 22: Case Study CVE - done")

# ============================================================
# SLIDE 23: Competitive Comparison
# ============================================================
s23 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s23, BG)
slide_title(s23, "So Sánh Với Top-Tier AI Agents")

# Table header
headers = ["Feature", "Nexus", "Claude Code", "Cursor", "Gemini CLI", "AutoGPT", "CrewAI"]
col_widths = [Inches(1.6), Inches(1.8), Inches(1.6), Inches(1.6), Inches(1.6), Inches(1.6), Inches(1.6)]

# Table data
table_data = [
    ["Multi-Agent", "7+ debate", "Single", "Single", "Single", "Sequential", "Sequential"],
    ["Memory", "4-layer L0→L3", "CLAUDE.md", "Context files", "GEMINI.md", "File-based", "None"],
    ["Continual Learn", "XSkill auto", "No", "No", "No", "No", "No"],
    ["Tools", "172+ self-create", "MCP + Bash", "Built-in", "MCP + Shell", "Limited", "Custom"],
    ["Provider Lock", "None (8+)", "Anthropic", "OpenAI/Anth", "Google", "OpenAI", "Any"],
    ["Free Fallback", "3 free chains", "No", "No", "No", "No", "No"],
    ["Self-Healing", "Circuit Breaker", "No", "No", "No", "No", "No"],
    ["Discussion", "Adversarial 2R", "No", "No", "No", "No", "No"],
    ["Self-Hosted", "Docker 1 lệnh", "Cloud only", "Cloud only", "Cloud only", "Yes", "Yes"],
    ["Crash Resume", "Checkpoint", "No", "No", "No", "No", "No"],
    ["Channels", "9 platforms", "CLI only", "IDE only", "CLI only", "CLI", "API"],
]

# Header row
for j, h in enumerate(headers):
    x = Inches(0.3 + j * 1.85)
    color = BRAND if j == 1 else DARK
    add_text_box(s23, x, Inches(1.2), Inches(1.7), Inches(0.4),
                 h, font_size=10, bold=True, color=color, align=PP_ALIGN.CENTER)

# Data rows
for i, row in enumerate(table_data):
    y = Inches(1.6 + i * 0.48)
    # Alternate row bg
    if i % 2 == 0:
        shape = s23.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.2), y, Inches(12.9), Inches(0.45))
        shape.fill.solid()
        shape.fill.fore_color.rgb = CARD_BG
        shape.line.fill.background()
    for j, cell in enumerate(row):
        x = Inches(0.3 + j * 1.85)
        if j == 1:
            color = GREEN
            bold = True
        elif cell in ["No", "None", "Single"]:
            color = RGBColor(0x94, 0xA3, 0xB8)
            bold = False
        else:
            color = DARK
            bold = False
        add_text_box(s23, x, y, Inches(1.7), Inches(0.42),
                     cell, font_size=9, bold=bold, color=color, align=PP_ALIGN.CENTER)

print("  Slide 23: Competitive - done")

# ============================================================
# SLIDE 24: 10 Unique Differentiators
# ============================================================
s24 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s24, BG)
slide_title(s24, "10 Điểm Khác Biệt Độc Nhất")

differentiators = [
    "Structured Debate - Agents TRANH LUẬN qua 2 rounds (Challenge→Rebuttal→Synthesis)",
    "4-Layer Memory - Tự CHƯNG CẤT qua L0→L3 + Dreaming Engine mỗi 6 giờ",
    "Zero-Downtime Fallback - Free chain (Kilo→Pollinations→g4f) + Circuit Breaker",
    "Self-Evolution - Tự TẠO tools, cài dependencies, scaffold Docker containers",
    "Quality-Driven Iteration - Không giới hạn turns, chạy đến 80%+ quality",
    "Complete Transparency - 14 SSE event types real-time. ACTIVITY.md unlimited",
    "Metacognition - 6-point framework phân tích ROOT CAUSE khi stuck",
    "Harness Engineering - Context engineering + architectural constraints",
    "Provider Agnostic - 8+ providers, credential pool, smart routing",
    "Enterprise Security - 13 lớp defense-in-depth",
]
for i, item in enumerate(differentiators):
    col = i % 2
    row = i // 2
    x = Inches(0.5 + col * 6.5)
    y = Inches(1.2 + row * 1.15)
    shape, tf = add_shape_with_text(s24, x, y, Inches(6.2), Inches(1.0), "",
                                     fill_color=WHITE, border_color=BORDER)
    p0 = tf.paragraphs[0]
    r1 = p0.add_run()
    r1.text = f"{i+1}. "
    r1.font.size = Pt(11)
    r1.font.bold = True
    r1.font.color.rgb = BRAND
    parts = item.split(" - ", 1)
    r2 = p0.add_run()
    r2.text = parts[0]
    r2.font.size = Pt(11)
    r2.font.bold = True
    r2.font.color.rgb = DARK
    if len(parts) > 1:
        r3 = p0.add_run()
        r3.text = " - " + parts[1]
        r3.font.size = Pt(10)
        r3.font.color.rgb = MUTED

print("  Slide 24: Differentiators - done")

# ============================================================
# SLIDE 25: Applications
# ============================================================
s25 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s25, BG)
slide_title(s25, "Ứng Dụng Đa Lĩnh Vực")

apps = [
    (RED, "Cybersecurity", "Penetration Testing, Vulnerability Research, Red Team, Security Audit, Bug Bounty"),
    (BRAND, "Software Development", "Code Generation, Code Review, Bug Fixing, Refactoring, Testing, Documentation"),
    (TEAL, "Research & Analysis", "Deep Research, Data Analysis, Competitive Analysis, Due Diligence, Patent Research"),
    (ORANGE, "Enterprise Ops", "IT Automation, Content Creation, Customer Support, Process Automation"),
    (PURPLE, "Education", "Personalized Tutoring, Lab Environments, Assessment, Course Material"),
    (PINK, "Any Complex Task", "Multi-perspective AI, collaborative problem-solving, iterative quality"),
]
for i, (color, title, desc) in enumerate(apps):
    col = i % 3
    row = i // 3
    x = Inches(0.5 + col * 4.2)
    y = Inches(1.3 + row * 2.8)
    shape, tf = add_shape_with_text(s25, x, y, Inches(3.9), Inches(2.4), "",
                                     fill_color=WHITE, border_color=color)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(16)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    add_paragraph(tf, desc, font_size=11, color=MUTED, space_before=Pt(10))

print("  Slide 25: Applications - done")

# ============================================================
# SLIDE 26: Deployment
# ============================================================
s26 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s26, BG)
slide_title(s26, "Deployment - 1 Lệnh Duy Nhất")

# Code block
shape_code, tf_code = add_shape_with_text(
    s26, Inches(1.5), Inches(1.3), Inches(10.3), Inches(2.8), "",
    fill_color=RGBColor(0x1E, 0x29, 0x3B), border_color=RGBColor(0x33, 0x41, 0x55))
code_lines = [
    ("# 1. Clone repository", True),
    ("git clone <repo-url> mekongai-nexus && cd mekongai-nexus", False),
    ("", False),
    ("# 2. Configure (hoặc để trống cho free tier)", True),
    ("cp .env.example .env", False),
    ("", False),
    ("# 3. Build & Run", True),
    ("docker compose up -d --build", False),
    ("", False),
    ("# 4. Access: http://localhost:8899", True),
]
for i, (line, is_comment) in enumerate(code_lines):
    if i == 0:
        tf_code.paragraphs[0].text = line
        tf_code.paragraphs[0].font.size = Pt(12)
        tf_code.paragraphs[0].font.name = 'JetBrains Mono'
        tf_code.paragraphs[0].font.color.rgb = RGBColor(0x94, 0xA3, 0xB8) if is_comment else RGBColor(0x38, 0xBD, 0xF8)
    else:
        p = tf_code.add_paragraph()
        p.text = line
        p.font.size = Pt(12)
        p.font.name = 'JetBrains Mono'
        p.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8) if is_comment else RGBColor(0x38, 0xBD, 0xF8)
        p.space_before = Pt(2)

# Requirements
req_stats = [("4 GB", "RAM tối thiểu"), ("2 cores", "CPU tối thiểu"), ("0", "GPU required")]
for i, (num, label) in enumerate(req_stats):
    x = Inches(2.0 + i * 3.5)
    add_text_box(s26, x, Inches(4.4), Inches(2.5), Inches(0.5),
                 num, font_size=24, color=BRAND, align=PP_ALIGN.CENTER)
    add_text_box(s26, x, Inches(4.9), Inches(2.5), Inches(0.3),
                 label, font_size=11, color=MUTED, align=PP_ALIGN.CENTER)

# Services
services = [("Nexus", ":8899"), ("Gateway", ":18800"), ("PostgreSQL", ":5433"), ("Redis", ":6380"), ("OpenClaw", ":18789")]
for i, (name, port) in enumerate(services):
    x = Inches(1.0 + i * 2.4)
    shape, tf = add_shape_with_text(s26, x, Inches(5.5), Inches(2.1), Inches(0.8), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = name
    tf.paragraphs[0].font.size = Pt(12)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = BRAND
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    add_paragraph(tf, port, font_size=10, color=MUTED, align=PP_ALIGN.CENTER, space_before=Pt(2))

print("  Slide 26: Deployment - done")

# ============================================================
# SLIDE 27: Roadmap
# ============================================================
s27 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s27, BG)
slide_title(s27, "Lộ Trình Phát Triển")

roadmap = [
    (TEAL, "3-6 Tháng", [
        "Voice Interface real-time", "Visual QA Automation",
        "Skill Marketplace (community)", "Multi-Tenant isolation",
        "Speculative execution (2-3× throughput)"]),
    (BRAND, "6-12 Tháng", [
        "Autonomous Research Programs", "Cross-Instance Federated Learning",
        "Real-Time Collaboration", "Mobile App (iOS/Android)",
        "Plugin Ecosystem marketplace"]),
    (PURPLE, "Tầm Nhìn Dài Hạn", [
        "Enterprise AI Operating System", "Distributed Multi-Instance",
        "Hardware Integration (IoT)", "Self-Improving Architecture",
        "SSO/SAML/RBAC/SOC2/GDPR"]),
]
for i, (color, title, items) in enumerate(roadmap):
    x = Inches(0.5 + i * 4.2)
    shape, tf = add_shape_with_text(s27, x, Inches(1.3), Inches(3.9), Inches(5.2), "",
                                     fill_color=WHITE, border_color=color)
    tf.paragraphs[0].text = title
    tf.paragraphs[0].font.size = Pt(16)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = color
    for item in items:
        add_paragraph(tf, f"→ {item}", font_size=11, color=MUTED, space_before=Pt(10))

print("  Slide 27: Roadmap - done")

# ============================================================
# SLIDE 28: Technical Stats
# ============================================================
s28 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s28, BG)
slide_title(s28, "Thống Kê Kỹ Thuật")

all_stats = [
    ("80K+", "Lines of Code"), ("172+", "MCP Tools"), ("66", "Agent Templates"),
    ("13", "Security Layers"), ("9", "Channels"), ("8+", "LLM Providers"),
    ("4", "Memory Layers"), ("39", "Built-in Skills"), ("20", "Frontend Routes"),
    ("25+", "DB Tables"),
]
for i, (num, label) in enumerate(all_stats):
    col = i % 5
    row = i // 5
    x = Inches(0.5 + col * 2.55)
    y = Inches(1.5 + row * 2.5)
    shape, tf = add_shape_with_text(s28, x, y, Inches(2.3), Inches(2.0), "",
                                     fill_color=WHITE, border_color=BORDER)
    tf.paragraphs[0].text = num
    tf.paragraphs[0].font.size = Pt(32)
    tf.paragraphs[0].font.color.rgb = BRAND
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    add_paragraph(tf, label, font_size=12, color=MUTED, align=PP_ALIGN.CENTER, space_before=Pt(8))

print("  Slide 28: Stats - done")

# ============================================================
# SLIDE 29: Thank You
# ============================================================
s29 = prs.slides.add_slide(BLANK_LAYOUT)
set_slide_bg(s29, BG)

# Logo
logo_path2 = os.path.join(IMAGES, "mekongai-logo.png")
add_image_safe(s29, logo_path2, Inches(5.9), Inches(1.0), height=Inches(1.0))

# Title
add_text_box(s29, Inches(1.5), Inches(2.3), Inches(10.3), Inches(1.0),
             "MekongAI Nexus", font_size=48, bold=False, color=BRAND, align=PP_ALIGN.CENTER)

# Subtitle
add_text_box(s29, Inches(1.5), Inches(3.3), Inches(10.3), Inches(0.5),
             "Multi-Agent Orchestration Platform", font_size=20, color=DARK, align=PP_ALIGN.CENTER)
add_text_box(s29, Inches(1.5), Inches(3.8), Inches(10.3), Inches(0.5),
             "with Layered Memory, Structured Debate & Continual Learning",
             font_size=16, color=MUTED, align=PP_ALIGN.CENTER)

# Badges
badges_end = ["Self-Orchestrating", "Self-Learning", "Self-Healing", "Self-Evolving"]
bx2 = 3.8
for i, badge in enumerate(badges_end):
    clr = BRAND if i % 2 == 0 else TEAL
    add_shape_with_text(s29, Inches(bx2), Inches(4.8), Inches(1.8), Inches(0.4),
                        badge, fill_color=BG, border_color=clr,
                        font_size=11, font_color=clr, align=PP_ALIGN.CENTER)
    bx2 += 1.95

# Footer
add_text_box(s29, Inches(0), Inches(6.3), Inches(13.333), Inches(0.4),
             "MekongAI  |  Version 1.1.0  |  June 2026",
             font_size=14, color=MUTED, align=PP_ALIGN.CENTER)

print("  Slide 29: Thank You - done")

# ============================================================
# SAVE
# ============================================================
prs.save(OUTPUT)
print(f"\nSaved to: {OUTPUT}")
print(f"  Total slides: {len(prs.slides)}")
