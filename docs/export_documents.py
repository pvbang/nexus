"""
MekongAI Nexus — Document Export Script
Exports Technical and Functional documentation to DOCX, PDF, and PPTX.

Usage:
    pip install python-docx python-pptx markdown weasyprint
    python export_documents.py

Or for just DOCX (no weasyprint dependency):
    pip install python-docx python-pptx markdown
    python export_documents.py --docx-only
"""

import sys
import re
from pathlib import Path
from urllib.parse import unquote

try:
    from docx import Document
    from docx.shared import Inches, Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.style import WD_STYLE_TYPE
except ImportError:
    print("ERROR: python-docx not installed. Run: pip install python-docx")
    sys.exit(1)

try:
    from pptx import Presentation
    from pptx.util import Inches as PptxInches, Pt as PptxPt
    from pptx.dml.color import RGBColor as PptxRGBColor
    from pptx.enum.text import PP_ALIGN
except ImportError:
    print("ERROR: python-pptx not installed. Run: pip install python-pptx")
    sys.exit(1)


DOCS_DIR = Path(__file__).parent
OUTPUT_DIR = DOCS_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
TESTS_DIR = DOCS_DIR.parent / "tests"

BRAND_COLOR = RGBColor(0x14, 0x56, 0xF0)
DARK_COLOR = RGBColor(0x18, 0x1E, 0x25)
GRAY_COLOR = RGBColor(0x6B, 0x72, 0x80)


def parse_markdown_sections(md_text):
    """Parse markdown into structured sections."""
    lines = md_text.split("\n")
    sections = []
    current_section = {"level": 0, "title": "", "content": []}
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            current_section["content"].append(line)
            continue

        if in_code_block:
            current_section["content"].append(line)
            continue

        if line.startswith("# "):
            if current_section["title"] or current_section["content"]:
                sections.append(current_section)
            current_section = {"level": 1, "title": line[2:].strip(), "content": []}
        elif line.startswith("## "):
            if current_section["title"] or current_section["content"]:
                sections.append(current_section)
            current_section = {"level": 2, "title": line[3:].strip(), "content": []}
        elif line.startswith("### "):
            if current_section["title"] or current_section["content"]:
                sections.append(current_section)
            current_section = {"level": 3, "title": line[4:].strip(), "content": []}
        elif line.startswith("#### "):
            if current_section["title"] or current_section["content"]:
                sections.append(current_section)
            current_section = {"level": 4, "title": line[5:].strip(), "content": []}
        else:
            current_section["content"].append(line)

    if current_section["title"] or current_section["content"]:
        sections.append(current_section)

    return sections


def parse_table(lines):
    """Parse markdown table lines into rows."""
    rows = []
    for line in lines:
        if line.strip().startswith("|") and not re.match(r"^\|[\s\-:|]+\|$", line.strip()):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(cells)
    return rows


def setup_styles(doc):
    """Configure document styles."""
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Times New Roman"
    font.size = Pt(10.5)
    font.color.rgb = DARK_COLOR

    for i in range(1, 5):
        heading_style = doc.styles[f"Heading {i}"]
        heading_style.font.name = "Times New Roman"
        heading_style.font.color.rgb = BRAND_COLOR if i <= 2 else DARK_COLOR
        heading_style.font.bold = True
        if i == 1:
            heading_style.font.size = Pt(16)
        elif i == 2:
            heading_style.font.size = Pt(13)
        elif i == 3:
            heading_style.font.size = Pt(12)
        else:
            heading_style.font.size = Pt(11)


def add_table_to_doc(doc, rows):
    """Add a formatted table to document."""
    if not rows or len(rows) < 1:
        return

    num_cols = len(rows[0])
    table = doc.add_table(rows=len(rows), cols=num_cols)
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    for i, row_data in enumerate(rows):
        row = table.rows[i]
        for j, cell_text in enumerate(row_data):
            if j < num_cols:
                cell = row.cells[j]
                cell.text = cell_text.replace("**", "").strip()
                for paragraph in cell.paragraphs:
                    paragraph.style.font.size = Pt(9)
                    if i == 0:
                        for run in paragraph.runs:
                            run.bold = True


def add_code_block(doc, code_lines):
    """Add a styled code block to document with background shading."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    code_text = "\n".join(code_lines)
    para = doc.add_paragraph()
    para.style = doc.styles["Normal"]
    run = para.add_run(code_text)
    run.font.name = "Consolas"
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)
    para.paragraph_format.left_indent = Cm(0.8)
    para.paragraph_format.right_indent = Cm(0.8)
    para.paragraph_format.space_before = Pt(8)
    para.paragraph_format.space_after = Pt(8)

    # Add light gray background shading
    pPr = para._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F0F4F8")
    pPr.append(shd)


def md_to_docx(md_path, output_path, title, subtitle):
    """Convert markdown to professional DOCX."""
    md_text = md_path.read_text(encoding="utf-8")
    doc = Document()
    setup_styles(doc)

    # Title page
    for _ in range(4):
        doc.add_paragraph()

    title_para = doc.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title_para.add_run(title)
    run.font.size = Pt(14)
    run.font.color.rgb = BRAND_COLOR
    run.font.bold = True
    run.font.name = "Times New Roman"

    subtitle_para = doc.add_paragraph()
    subtitle_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle_para.add_run(subtitle)
    run.font.size = Pt(12)
    run.font.color.rgb = GRAY_COLOR
    run.font.name = "Times New Roman"

    doc.add_paragraph()

    info_para = doc.add_paragraph()
    info_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info_para.add_run("Version 1.1.0 | June 2026")
    run.font.size = Pt(11)
    run.font.color.rgb = GRAY_COLOR

    author_para = doc.add_paragraph()
    author_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = author_para.add_run("Author: MekongAI")
    run.font.size = Pt(11)
    run.font.color.rgb = GRAY_COLOR

    doc.add_page_break()

    # Table of Contents placeholder
    toc_heading = doc.add_heading("Table of Contents", level=1)
    doc.add_paragraph("(Update field to generate TOC in Word: Ctrl+A → F9)")
    doc.add_page_break()

    # Content
    sections = parse_markdown_sections(md_text)
    in_code_block = False
    code_lines = []
    in_table = False
    table_lines = []

    for section in sections:
        if section["level"] > 0 and section["title"]:
            clean_title = re.sub(r"\[.*?\]\(.*?\)", "", section["title"])
            clean_title = clean_title.replace("`", "").strip()
            doc.add_heading(clean_title, level=min(section["level"], 4))

        for line in section["content"]:
            # Code block handling
            if line.strip().startswith("```"):
                if in_code_block:
                    add_code_block(doc, code_lines)
                    code_lines = []
                    in_code_block = False
                else:
                    if in_table and table_lines:
                        rows = parse_table(table_lines)
                        if rows:
                            add_table_to_doc(doc, rows)
                        table_lines = []
                        in_table = False
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            # Table handling
            if line.strip().startswith("|"):
                if not in_table:
                    in_table = True
                    table_lines = []
                table_lines.append(line)
                continue
            elif in_table:
                rows = parse_table(table_lines)
                if rows:
                    add_table_to_doc(doc, rows)
                table_lines = []
                in_table = False

            # Image handling
            img_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line.strip())
            if img_match:
                alt_text = img_match.group(1)
                img_path = unquote(img_match.group(2))
                # Resolve relative path from docs dir
                resolved = (DOCS_DIR / img_path).resolve()
                if resolved.exists():
                    try:
                        doc.add_picture(str(resolved), width=Inches(5.5))
                        last_para = doc.paragraphs[-1]
                        last_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        if alt_text:
                            cap = doc.add_paragraph()
                            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            run = cap.add_run(alt_text)
                            run.font.size = Pt(9)
                            run.font.italic = True
                            run.font.color.rgb = GRAY_COLOR
                    except Exception:
                        doc.add_paragraph(f"[Image: {alt_text}]")
                else:
                    doc.add_paragraph(f"[Image not found: {alt_text}]")
                continue

            # Regular content
            if not line.strip():
                continue
            elif line.strip().startswith("- ") or line.strip().startswith("* "):
                text = line.strip().lstrip("- ").lstrip("* ")
                text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
                text = text.replace("`", "")
                para = doc.add_paragraph(text, style="List Bullet")
            elif line.strip().startswith("> "):
                text = line.strip().lstrip("> ")
                para = doc.add_paragraph()
                para.paragraph_format.left_indent = Cm(1)
                run = para.add_run(text)
                run.font.italic = True
                run.font.color.rgb = GRAY_COLOR
            elif line.strip().startswith("---"):
                doc.add_paragraph()
            else:
                text = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
                text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
                text = text.replace("`", "")
                if text.strip():
                    doc.add_paragraph(text.strip())

        # Flush remaining table
        if in_table and table_lines:
            rows = parse_table(table_lines)
            if rows:
                add_table_to_doc(doc, rows)
            table_lines = []
            in_table = False

    doc.save(str(output_path))
    print(f"  DOCX saved: {output_path}")


def create_pptx(output_path, doc_type="technical"):
    """Create professional PPTX presentation."""
    prs = Presentation()
    prs.slide_width = PptxInches(13.333)
    prs.slide_height = PptxInches(7.5)

    # Slide 1: Title
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    txBox = slide.shapes.add_textbox(PptxInches(1), PptxInches(2.5), PptxInches(11), PptxInches(2))
    tf = txBox.text_frame
    p = tf.paragraphs[0]
    p.text = "MekongAI Nexus"
    p.font.size = PptxPt(44)
    p.font.bold = True
    p.font.color.rgb = PptxRGBColor(0x14, 0x56, 0xF0)
    p.alignment = PP_ALIGN.CENTER

    p = tf.add_paragraph()
    if doc_type == "technical":
        p.text = "Technical Architecture Documentation"
    else:
        p.text = "Functional Capabilities & Demonstrations"
    p.font.size = PptxPt(24)
    p.font.color.rgb = PptxRGBColor(0x6B, 0x72, 0x80)
    p.alignment = PP_ALIGN.CENTER

    p = tf.add_paragraph()
    p.text = "Version 1.1.0 | June 2026 | MekongAI"
    p.font.size = PptxPt(16)
    p.font.color.rgb = PptxRGBColor(0x9C, 0xA3, 0xAF)
    p.alignment = PP_ALIGN.CENTER

    if doc_type == "technical":
        slides_data = [
            ("Platform Overview", [
                "Nền tảng điều phối đa tác nhân AI (Multi-Agent Orchestration)",
                "Turn-based Pipeline: Think → Discuss → Execute",
                "4-Layer Progressive Memory (L0→L3) + Dreaming Engine",
                "172+ MCP Tools trên 57 modules + Self-Evolution",
                "8+ LLM providers (bất kỳ OpenAI-compatible endpoint)",
                "Circuit breaker + fallback chain → zero-downtime",
            ]),
            ("System Architecture", [
                "Frontend: React 19 + TypeScript 5.7 + Vite 6 + Zustand 5 (160+ components)",
                "Backend: Python 3.12+ / FastAPI / SQLAlchemy 2.0 (80,000+ LOC)",
                "Database: PostgreSQL 16 + pgvector + Full-Text Search",
                "Cache: Redis 7+ (Pub/Sub + Response Cache + Message Bus)",
                "Sandbox: Docker + IronClaw (Rust) + OpenClaw (Node.js)",
                "Deploy: Docker Compose (5 services) — 1 lệnh duy nhất",
            ]),
            ("Orchestration Pipeline", [
                "NexusExecutionPipeline: Unified entry point",
                "SmartContextAggregator: 7 sources, parallel fetch, scoring",
                "EvolutionAgent: Continuous quality improvement",
                "NexusOrchestrator: Plan, evaluate, quality-based exit",
                "TurnExecutor: Think → Discuss → Execute phases",
                "AgentWorker: Unlimited tool rounds, activity-based timeout",
            ]),
            ("Structured Discussion Protocol", [
                "Round 1: Each agent → challenges + supports about peers",
                "Round 2: Challenged agents → rebuttals (defend or accept)",
                "Synthesis: agreed_approaches + unresolved + key_insights",
                "Published to ContextSharing as shared knowledge",
                "Adversarial collaboration improves output quality",
                "Full transparency — debate visible on frontend UI",
            ]),
            ("Memory System (L0→L3)", [
                "L0: Raw event capture + message counting",
                "L1: Typed atom extraction (4 types, scored, deduped)",
                "L2: Scene aggregation (cluster atoms → theme blocks)",
                "L3: Persona synthesis (comprehensive profile, versioned)",
                "Recall: Multi-keyword scoring + hybrid strategy",
                "Dreaming Engine: 6-hour consolidation cycles",
            ]),
            ("XSkill Continual Learning", [
                "SkillBuilder: Extract reusable task-level workflows",
                "ExperienceManager: Action-level tactical lessons",
                "ExperienceCritique: LLM-as-judge (5 dimensions)",
                "TrajectoryCompressor: Compress logs → SFT training data",
                "TreeSearch: Beam search for optimal skill paths",
                "Trivial Task Gate: Filter noise, learn from substance",
            ]),
            ("LLM Provider Layer", [
                "8+ providers: Bedrock, OpenAI, Gemini, OpenRouter, Kilo, Pollinations, g4f, Local",
                "Circuit Breaker: CLOSED → OPEN → HALF_OPEN (per provider)",
                "Smart Routing: Simple → cheap model, Complex → strong model",
                "Fallback Chain: Core → Agent → Kilo → Pollinations → g4f",
                "Per-Model Timeout: Fast (60s) vs Deep-thinking (900s)",
                "Prompt Cache: ~75% cost reduction on repeated prompts",
            ]),
            ("MCP Tools (57 Modules, 172+ Tools)", [
                "File & Code: filesystem, file_edit, code_navigation, git (22 tools)",
                "Web & Browser: AI-powered automation, Playwright (15 tools)",
                "Communication: email, telegram, discord, slack, 9 channels (8 tools)",
                "Security: scanning, monitoring, API testing, exploitation (17 tools)",
                "AI & Intelligence: delegate, self_evolve, workflow_tools (18 tools)",
                "Dynamic: Self-Evolution — tự tạo tools không giới hạn tại runtime",
            ]),
            ("Security Architecture (13 Layers)", [
                "CommandGuard: Dangerous command detection",
                "SSRF Guard: DNS pinning, private network blocking",
                "Tool Policy + Tool Guard: Per-role authorization",
                "Exfiltration Guard + Secret Redaction: Data leak prevention",
                "Rate Limiter + Session Mutex: Throttling + concurrency",
                "Approval Workflow: Human-in-the-loop for dangerous ops",
            ]),
            ("Cognitive Upgrade (3 Layers)", [
                "Layer 1: Self-Awareness — Infrastructure map + bootstrap",
                "Layer 2: Metacognitive Loop — Root cause analysis when stuck",
                "Layer 3: Creative Divergence — 3-frame parallel thinking",
                "Minimalist frame: Simplest possible solution",
                "Unconventional frame: Break every assumption",
                "Reverse frame: Work backwards from perfect end state",
            ]),
            ("Infrastructure & Deployment", [
                "Docker Compose: 5 services (nexus, gateway, postgres, redis, openclaw)",
                "PostgreSQL 16 + pgvector: 18 migrations, 25+ tables",
                "Auto-Scaling: CPU-aware workers, dynamic rate limits (60×cores RPM)",
                "Task Checkpoint & Resume: Crash recovery, auto-resume khi restart",
                "Không cần GPU | Không cần API key (free tier chain)",
                "Deploy 1 lệnh: docker compose up -d --build",
            ]),
            ("66 Agent Templates — 8 Lĩnh Vực", [
                "Core/Foundational (12): Researcher, Developer, Analyst, Writer...",
                "Security Operations (5): Pentester, Recon, Exploit Developer...",
                "Business & Finance (5): Quant Trader, Economist, Legal Advisor...",
                "Creative & Multimodal (5): Audio Producer, Storyteller...",
                "Intelligence (13): OSINT, Threat Hunter, Forensics, SIGINT, FININT...",
                "STEM, Humanities, Creative Ideation, Life & Everyday...",
            ]),
        ]
    else:
        slides_data = [
            ("MekongAI Nexus Là Gì?", [
                "Nền tảng AI đa tác nhân — nhiều AI agents chuyên biệt cộng tác",
                "Tự điều phối: Lập kế hoạch, tranh luận, thực thi, đánh giá tự chủ",
                "Tự học: Tích lũy skills từ mọi lần thực thi task",
                "Tự phục hồi: Circuit breaker + fallback = không bao giờ dừng",
                "Đa provider: 8+ LLM providers (OpenAI, Claude, Gemini, miễn phí)",
                "Production-ready: Docker Compose, 1 lệnh deploy, không cần GPU/API key",
            ]),
            ("Năng Lực Cốt Lõi", [
                "Multi-agent collaboration + structured debate (adversarial)",
                "4-layer progressive memory (L0→L3) + Dreaming Engine",
                "172+ tools (file, web, browser, code, security, data, AI) + self-evolve",
                "Knowledge Graph GraphRAG auto-populated + hybrid retrieval",
                "XSkill Continual Learning — tự cải thiện từ mọi task",
                "9 nền tảng giao tiếp: Web, Telegram, Discord, Slack, WhatsApp...",
            ]),
            ("Cách Hoạt Động", [
                "1. User gửi yêu cầu → Hệ thống phân tích độ phức tạp",
                "2. Orchestrator chọn agents chuyên biệt (tối đa 7+)",
                "3. Agents Think — suy luận song song độc lập",
                "4. Agents Discuss — challenge + support + rebuttal",
                "5. Agents Execute — vòng tool không giới hạn",
                "6. Quality evaluation → Tiếp tục hoặc hoàn thành (>80%)",
            ]),
            ("Structured Discussion Protocol", [
                "Không chỉ nhiều agents — chúng TRANH LUẬN lẫn nhau",
                "Round 1: Challenge điểm yếu + Support điểm mạnh",
                "Round 2: Bảo vệ quan điểm hoặc chấp nhận phản biện",
                "Synthesis: Đồng thuận + bất đồng + insight mới",
                "Kết quả: Chất lượng cao hơn đáng kể so với single agent",
                "Minh bạch hoàn toàn — xem debate real-time trên UI",
            ]),
            ("Web Interface (20 Routes)", [
                "Chat: Conversation full-featured với SSE streaming",
                "Dashboard: System overview, health metrics, provider status",
                "Agents: Registry, spawning, monitoring (66 templates)",
                "Tasks: Multi-task parallel monitoring với timeline",
                "Knowledge Graph: Visual network explorer, interactive",
                "Skills, Analytics, Terminal, XSkill, Admin panels...",
            ]),
            ("Case Study: Penetration Test (haufo.hanoi.gov.vn)", [
                "Mục tiêu: Website chính phủ, bắt đầu từ zero knowledge",
                "Team: 7 agents chuyên biệt tự phối hợp 18 turns",
                "Kết quả: 5 loại vulnerability, default creds admin:123qwe",
                "Output: 7 Python exploit scripts + 8 báo cáo chuyên nghiệp",
                "Evidence: 120+ config files, 59 users leaked, JWT captured",
                "Artifacts: 34 MB (150+ files tổng cộng)",
            ]),
            ("Case Study: CVE-2026-23918 Research", [
                "Mục tiêu: Apache mod_http2 double-free vulnerability",
                "Team: 7 agents TỰ ĐỘNG build lab + exploit + evidence",
                "Kết quả: CVSS 9.8 CRITICAL xác nhận, 100% crash rate",
                "Evidence: 32 crashes (SIGSEGV + SIGABRT), ASAN reports",
                "Output: 3 exploit vectors + detection tool h2ghost.py",
                "Lab: Docker ASAN-instrumented Apache tự động xây dựng",
            ]),
            ("Lĩnh Vực Ứng Dụng", [
                "Cybersecurity: Pentesting, vuln research, red team, audit",
                "Phát triển phần mềm: Code gen, review, bug fix, testing, deploy",
                "Nghiên cứu & Phân tích: Deep research, data analysis, due diligence",
                "Enterprise: IT automation, content creation, process automation",
                "Giáo dục: Tutoring, lab environments, assessments",
                "Bất kỳ bài toán phức tạp nào cần multi-perspective AI",
            ]),
            ("Lợi Thế Cạnh Tranh", [
                "Structured Debate → Chất lượng cao hơn single-agent",
                "4-Layer Memory → Chưng cất tri thức tiến triển",
                "Zero-Downtime → Free fallback chain, không bao giờ dừng",
                "Self-Evolution → Tự tạo tools mới khi cần",
                "Quality-Driven → Không giới hạn turns, chạy đến khi xuất sắc",
                "Provider-Agnostic → Bất kỳ LLM, không vendor lock-in",
            ]),
            ("Triển Khai & Truy Cập", [
                "1 lệnh duy nhất: docker compose up -d --build",
                "Không cần GPU | Không cần API key (free tier chain)",
                "Web UI: http://localhost:8899",
                "API: OpenAI-compatible /v1/chat/completions (drop-in)",
                "9 channels: Telegram, Discord, Slack, WhatsApp, Email...",
                "Self-hosted, private, secure — dữ liệu ở lại với bạn",
            ]),
            ("Lộ Trình Tương Lai", [
                "Voice interface + Mobile app (iOS/Android)",
                "Skill marketplace — community sharing với ratings",
                "Multi-tenant + Enterprise SSO/SAML/RBAC",
                "Autonomous research programs — long-running",
                "Cross-instance federated learning (privacy-preserving)",
                "Hardware integration (IoT, robotics, physical world)",
            ]),
        ]

    for title_text, bullets in slides_data:
        slide = prs.slides.add_slide(prs.slide_layouts[6])

        # Title
        txBox = slide.shapes.add_textbox(PptxInches(0.8), PptxInches(0.5), PptxInches(11.5), PptxInches(1.2))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = PptxPt(32)
        p.font.bold = True
        p.font.color.rgb = PptxRGBColor(0x18, 0x1E, 0x25)

        # Bullets
        txBox = slide.shapes.add_textbox(PptxInches(1.2), PptxInches(1.8), PptxInches(11), PptxInches(5))
        tf = txBox.text_frame
        tf.word_wrap = True

        for i, bullet in enumerate(bullets):
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
            p.text = bullet
            p.font.size = PptxPt(20)
            p.font.color.rgb = PptxRGBColor(0x37, 0x41, 0x51)
            p.space_before = PptxPt(12)
            p.level = 0

    # Screenshot slides (both types)
    if doc_type in ("functional", "technical"):
        screenshot_slides = [
            ("Chat Interface", "1. screenshot chat screen.png"),
            ("Multi-Agent Orchestration", "2. screenshot multi agent thinking and task excution.png"),
            ("Agent Execution Detail", "2. screenshot multi agent thinking and task excution 2.png"),
            ("Execution Results", "2. screenshot multi agent thinking and task excution 3.png"),
            ("Agent Registry", "3. screenshot multi agent info.png"),
            ("Skills Marketplace", "4. screenshot multi agent skills.png"),
            ("XSkill Continual Learning", "5. screenshot  agent XSkill — Continual Learning.png"),
            ("Knowledge Graph", "6. screenshot Agent KnowledGraph.png"),
            ("Task Artifacts", "screenshot agent artifacts TASK_BRIEF.md.png"),
            ("Workspace Structure", "screenshot agent artifacts workspace worktree Context and Work.png"),
        ]
        for slide_title, img_name in screenshot_slides:
            img_path = TESTS_DIR / img_name
            if img_path.exists():
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                # Title
                txBox = slide.shapes.add_textbox(PptxInches(0.5), PptxInches(0.2), PptxInches(12), PptxInches(0.8))
                tf = txBox.text_frame
                p = tf.paragraphs[0]
                p.text = slide_title
                p.font.size = PptxPt(24)
                p.font.bold = True
                p.font.color.rgb = PptxRGBColor(0x14, 0x56, 0xF0)
                p.alignment = PP_ALIGN.CENTER
                # Image
                try:
                    slide.shapes.add_picture(
                        str(img_path),
                        PptxInches(0.8), PptxInches(1.1),
                        width=PptxInches(11.7), height=PptxInches(6.0)
                    )
                except Exception:
                    pass

    # Final slide
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    txBox = slide.shapes.add_textbox(PptxInches(1), PptxInches(2.5), PptxInches(11), PptxInches(3))
    tf = txBox.text_frame
    p = tf.paragraphs[0]
    p.text = "MekongAI Nexus"
    p.font.size = PptxPt(40)
    p.font.bold = True
    p.font.color.rgb = PptxRGBColor(0x14, 0x56, 0xF0)
    p.alignment = PP_ALIGN.CENTER

    p = tf.add_paragraph()
    p.text = "Multi-Agent Orchestration Platform"
    p.font.size = PptxPt(22)
    p.font.color.rgb = PptxRGBColor(0x6B, 0x72, 0x80)
    p.alignment = PP_ALIGN.CENTER

    p = tf.add_paragraph()
    p.text = ""
    p = tf.add_paragraph()
    p.text = "github.com/pvbang | MekongAI"
    p.font.size = PptxPt(16)
    p.font.color.rgb = PptxRGBColor(0x9C, 0xA3, 0xAF)
    p.alignment = PP_ALIGN.CENTER

    prs.save(str(output_path))
    print(f"  PPTX saved: {output_path}")


def create_pdf_from_docx(docx_path, pdf_path):
    """Convert DOCX to PDF using python-docx2pdf or instructions."""
    try:
        from docx2pdf import convert
        convert(str(docx_path), str(pdf_path))
        print(f"  PDF saved: {pdf_path}")
    except ImportError:
        print(f"  PDF: Install docx2pdf for automatic conversion:")
        print(f"        pip install docx2pdf")
        print(f"        Or open {docx_path} in Word → Save as PDF → {pdf_path}")


def main():
    docx_only = "--docx-only" in sys.argv

    print("=" * 60)
    print("  MekongAI Nexus — Document Export")
    print("=" * 60)
    print()

    # Technical Documentation
    print("[1/6] Technical Documentation → DOCX")
    tech_md = DOCS_DIR / "TECHNICAL_DOCUMENTATION.md"
    tech_docx = OUTPUT_DIR / "MekongAI_Nexus_Technical_Documentation.docx"
    md_to_docx(
        tech_md, tech_docx,
        "MekongAI Nexus",
        "Technical Architecture Documentation"
    )

    # Functional Documentation
    print("[2/6] Functional Documentation → DOCX")
    func_md = DOCS_DIR / "FUNCTIONAL_DOCUMENTATION.md"
    func_docx = OUTPUT_DIR / "MekongAI_Nexus_Functional_Documentation.docx"
    md_to_docx(
        func_md, func_docx,
        "MekongAI Nexus",
        "Functional Capabilities & Demonstrations"
    )

    # Technical PPTX
    print("[3/6] Technical Presentation → PPTX")
    tech_pptx = OUTPUT_DIR / "MekongAI_Nexus_Technical_Presentation.pptx"
    create_pptx(tech_pptx, doc_type="technical")

    # Functional PPTX
    print("[4/6] Functional Presentation → PPTX")
    func_pptx = OUTPUT_DIR / "MekongAI_Nexus_Functional_Presentation.pptx"
    create_pptx(func_pptx, doc_type="functional")

    # PDFs
    if not docx_only:
        print("[5/6] Technical Documentation → PDF")
        tech_pdf = OUTPUT_DIR / "MekongAI_Nexus_Technical_Documentation.pdf"
        create_pdf_from_docx(tech_docx, tech_pdf)

        print("[6/6] Functional Documentation → PDF")
        func_pdf = OUTPUT_DIR / "MekongAI_Nexus_Functional_Documentation.pdf"
        create_pdf_from_docx(func_docx, func_pdf)
    else:
        print("[5/6] PDF skipped (--docx-only mode)")
        print("[6/6] PDF skipped (--docx-only mode)")

    print()
    print("=" * 60)
    print(f"  Output directory: {OUTPUT_DIR}")
    print("  Files generated:")
    for f in sorted(OUTPUT_DIR.glob("*")):
        print(f"    - {f.name} ({f.stat().st_size / 1024:.1f} KB)")
    print("=" * 60)


if __name__ == "__main__":
    main()
