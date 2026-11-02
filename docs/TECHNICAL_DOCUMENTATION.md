# Nexus — Technical Documentation

**Nền Tảng Điều Phối Đa Tác Nhân AI với Bộ Nhớ Phân Tầng, Tranh Luận Có Cấu Trúc & Học Liên Tục**

Version 1.1.0 | Architecture Reference | June 2026

---

## Executive Summary

Nexus là nền tảng AI đa tác nhân (multi-agent) cấp production, tự điều phối, được thiết kế cho các tác vụ phức tạp và dài hạn hoàn toàn tự chủ. Hệ thống tích hợp pipeline thực thi theo lượt (turn-based), giao thức thảo luận có cấu trúc (structured debate protocol), hệ thống bộ nhớ tiến triển 4 tầng (L0→L3), học liên tục (XSkill Continual Learning), và 192 MCP tools phân bố trên 68 modules (63 module tự nạp khi khởi động + 5 module lazy-load) — tất cả được hỗ trợ bởi tầng LLM đa nhà cung cấp với cơ chế circuit breaker đảm bảo khả năng phục hồi.

Khác với các hệ thống đơn tác nhân truyền thống, Nexus phối hợp nhiều AI agent chuyên biệt thông qua pipeline có cấu trúc Think → Discuss → Execute, cho phép giải quyết vấn đề theo cách cộng tác với vòng lặp chất lượng (quality-driven iteration). Nền tảng hoàn toàn không phụ thuộc nhà cung cấp (provider-agnostic), hỗ trợ bất kỳ LLM endpoint nào tương thích OpenAI, và bao gồm chuỗi fallback tự động đảm bảo hoạt động liên tục không gián đoạn (zero-downtime).

**Điểm nổi bật kỹ thuật:**
- **80,000+ dòng code backend** Python trên 300+ modules
- **192 registered tools** (68 tool modules) + khả năng tự tạo tools không giới hạn tại runtime
- **79 agent templates** chuyên biệt trải rộng nhiều lĩnh vực (IT, khoa học, kinh doanh, sáng tạo...)
- **13 lớp bảo mật** defense-in-depth
- **9 kênh giao tiếp** đa nền tảng (Telegram, Discord, Slack, WhatsApp, Email, SMS, Signal, Matrix, Webhook)
- **Zero-config deployment** — hoạt động hoàn toàn miễn phí không cần API key

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Harness Engineering & Design Philosophy](#2-harness-engineering--design-philosophy)
3. [Orchestration Pipeline](#3-orchestration-pipeline)
4. [Prompt Engineering & Context Pipeline](#4-prompt-engineering--context-pipeline)
5. [Memory System (L0→L3)](#5-memory-system-l0l3)
6. [XSkill Continual Learning](#6-xskill-continual-learning)
7. [LLM Provider Layer & Resilience](#7-llm-provider-layer--resilience)
8. [MCP Tool System](#8-mcp-tool-system)
9. [RAG Pipeline & Knowledge Graph](#9-rag-pipeline--knowledge-graph)
10. [Security Architecture (13 Layers)](#10-security-architecture-13-layers)
11. [Cognitive Upgrade (Self-Awareness, Metacognition, Divergence)](#11-cognitive-upgrade)
12. [Frontend Architecture](#12-frontend-architecture)
13. [Infrastructure & Deployment](#13-infrastructure--deployment)
14. [API Reference & Multi-Channel](#14-api-reference--multi-channel)
15. [Performance, Scalability & Observability](#15-performance-scalability--observability)
16. [Future Development Roadmap](#16-future-development-roadmap)

---

## 1. System Architecture

### 1.1. High-Level Architecture

Nexus được tổ chức thành 5 tầng kiến trúc chính, giao tiếp qua SSE, WebSocket, và REST API:

**Layer 1 — Presentation (Frontend)**
React 19 + TypeScript 5.7 + Vite 6 + Zustand 5 + Tailwind CSS. 20 routes bao gồm Chat, Dashboard, Agents, Tasks, Knowledge Graph, XSkill, Artifacts, Skills, Process Transparency, Analytics, Terminal, Admin panels. Giao tiếp real-time qua SSE streaming + WebSocket.

**Layer 2 — Orchestration (Core Intelligence)**
NexusExecutionPipeline điều phối toàn bộ quá trình thực thi. SmartContextAggregator (7 nguồn song song), EvolutionAgent (cải tiến liên tục), NexusOrchestrator (lập kế hoạch + đánh giá), TurnExecutor (các pha Think → Discuss → Execute), AgentWorker (vòng tool không giới hạn với activity-based timeout), ContextSharing (khả năng nhìn thấy kết quả ngang hàng), GoalEngine (phân rã mục tiêu tự chủ), SelfReflection (checkpoints chất lượng).

**Layer 3 — Intelligence Services**
Memory System (4 tầng: L0 Capture → L1 Atoms → L2 Scenes → L3 Persona), XSkill Continual Learning (skills + experiences + critique + trajectory compression + tree search), 68 MCP Tool Modules (192 tools bao gồm self_evolve để tự tạo tool runtime), RAG Pipeline (hybrid vector + FTS + GraphRAG với auto-ontology).

**Layer 4 — LLM Infrastructure**
Tầng đa nhà cung cấp tổng hợp (8+ providers: Bedrock, OpenAI, Gemini, OpenRouter, Kilo, Pollinations, g4f, Local). Circuit Breaker theo từng provider, Fallback Chain (Core → Agent → Kilo → Pollinations → g4f), Per-Model Timeout, Smart Routing (đơn giản→model rẻ, phức tạp→model mạnh), Credential Pool (xoay vòng round-robin), Prompt Cache (giảm ~75% chi phí).

**Layer 5 — Data & Execution**
PostgreSQL 16 + pgvector (19 migration versions, 22 tables), Redis 7+ (Pub/Sub + response cache), Public Gateway (port 18800, 300s proxy timeout), OpenClaw Execution Layer (thực thi code sandbox + giao tiếp đa kênh).

**Core Infrastructure Services:** TaskQueue (bounded FIFO), RateLimiter (Redis sliding-window), BackgroundMetrics (fire-and-forget observability), SessionManager, TaskWatchdog (heartbeat monitoring), CheckpointPersistence (phục hồi sau crash), LongMessageProcessor (xử lý chunked 500K chars).

**Security:** Mô hình phòng thủ 13 lớp bao gồm CommandGuard, SSRF Guard, Tool Policy, Exfiltration Guard, Secret Redaction, Rate Limiter, Session Mutex, Approval Workflow, Audit Logging, Atomic Writes, Circuit Breaker, Resource Monitor, và JWT Authentication.

### 1.2. Technology Stack

| Tầng | Công nghệ | Phiên bản |
|------|-----------|-----------|
| **Backend** | Python / FastAPI / Uvicorn / SQLAlchemy 2.0 | 3.12+ |
| **Database** | PostgreSQL + pgvector extension | 16 |
| **Cache** | Redis (Pub/Sub + Response Cache) | 7+ |
| **Frontend** | React + TypeScript + Vite + Zustand + Tailwind CSS | 19 / 5.7 / 6 / 5 |
| **LLM** | LiteLLM + AsyncOpenAI SDK | Latest |
| **Embeddings** | Sentence-Transformers / OpenAI / Bedrock Titan / Qwen3-Embedding | — |
| **RAG** | LangChain (chunking) + pgvector + FTS + GraphRAG | — |
| **Protocol** | MCP (stdio + HTTP + OAuth 2.1 PKCE) | — |
| **Sandbox** | Docker + IronClaw (Rust binary) | — |
| **Scheduling** | APScheduler + croniter | — |
| **Logging** | structlog (structured JSON) | — |
| **Deploy** | Docker Compose (5 services) | — |
| **Browser Automation** | Playwright + browser-use (AI-powered) | — |
| **Code Parsing** | Tree-sitter (AST extraction) | — |
| **Document Processing** | MarkItDown (50+ formats, OCR) | — |

### 1.3. Service Architecture

| Service | Port | Vai trò |
|---------|------|---------|
| **Nexus** (All-in-One) | 8899 | Backend API + Frontend SPA (ứng dụng chính) |
| **Public Gateway** | 18800 | API bề mặt công khai tối thiểu (proxy timeout 300s) |
| **PostgreSQL** | 5433 | Persistent storage + pgvector + Full-Text Search |
| **Redis** | 6380 | Response cache + Pub/Sub messaging |
| **OpenClaw** | 18789 | Sandbox execution layer (tùy chọn) |

---

## 2. Harness Engineering & Triết Lý Thiết Kế

Nexus được xây dựng trên nguyên lý **Harness Engineering** — thực hành định hình môi trường xung quanh AI agents để chúng hoạt động đáng tin cậy. Harness nằm tại giao điểm của context engineering, evaluation, observability, orchestration, safe autonomy, và software architecture.

### 2.1. Harness Architecture (CAR Decomposition)

Tuân theo framework **Control-Agency-Runtime (CAR)** từ nghiên cứu học thuật:

**Control Layer (Tầng kiểm soát):**
- Lắp ráp system prompt module hóa (PromptBuilder với 10 sections xác định để ổn định cache key)
- Quy tắc hành vi theo từng agent (14 archetype với tiêu chuẩn xuất sắc theo role)
- Production Excellence Directive (bắt buộc trên TẤT CẢ agents)
- Strict Response Rules (cấm ngôn ngữ đệm, bắt buộc tham chiếu file path, không hỏi xin phép)
- Intelligence Verification Doctrine (cho security/intelligence agents)

**Agency Layer (Tầng tự chủ):**
- GoalEngine: Phân rã sub-goal tự chủ với theo dõi phụ thuộc
- SelfReflection: Ngưỡng chất lượng theo domain (security: 0.80, research: 0.70, general: 0.55)
- MetacognitiveAgent: Phân tích root cause khi bị stuck (6-point framework)
- DivergentThinker: 3 frame sáng tạo đa chiều cho task phức tạp
- EvolutionAgent: Cố vấn cải tiến liên tục (temperature 1.0, chặn completion khi severity >= 0.8)

**Runtime Layer (Tầng thực thi):**
- NexusExecutionPipeline: Điểm vào duy nhất cho TẤT CẢ requests
- TurnExecutor: Vòng lặp xác định Think → Discuss → Execute → Evaluate
- CheckpointPersistence: Phục hồi sau crash qua PostgreSQL (tối đa 3 checkpoints/task)
- TaskWatchdog: Giám sát heartbeat, dọn dẹp task cũ
- Infrastructure Resilience: Exponential backoff khi tất cả fail (30s → 60s → 120s, giới hạn 5 phút)

### 2.2. Nguyên Lý Context Engineering

Harness quản lý context như **ngân sách bộ nhớ làm việc**, không phải bãi chứa:

| Nguyên lý | Triển khai |
|-----------|------------|
| **Budget-aware allocation** | ContextBudgetAllocator chấm điểm sections theo priority × relevance, phân bổ tỷ lệ |
| **Smart truncation** | Cắt tại ranh giới câu, không cắt tùy tiện theo giới hạn ký tự |
| **KV-cache locality** | Thứ tự sections xác định trong system prompts để ổn định Anthropic cache key |
| **Filesystem memory** | Files workspace (_context/, _work/) tồn tại xuyên suốt turns như nguồn sự thật duy nhất |
| **Context ledger** | Entries JSONL có cấu trúc per agent per turn — có thể truy vấn, thêm, không bao giờ mất |
| **Epoch compression** | Mỗi 10 turns, nén thành tóm tắt — cho phép task 100+ turns không mất ngữ cảnh |
| **Useful failures in-context** | Các lần thất bại được giữ lại (kèm lý do) để agents không lặp lại sai lầm |

### 2.3. Agent Identity & Behavioral Templates

Hệ thống duy trì **66 identity templates** thủ công chuyên sâu, phân bổ trên 8 lĩnh vực:

**Core/Foundational (12):** Core, Researcher, Developer, Analyst, Writer, Coordinator, Judge, Critic, Architect, Debugger, Orchestrator, Assistant

**Security Operations (5):** Pentester, Recon Specialist, Exploit Developer, DevOps, Vulnerability Researcher

**Specialized Technical (7):** Frontend Developer, Backend Developer, Database Engineer, Data Scientist, UX Designer, Project Manager, QA Engineer

**Business & Finance (5):** Quantitative Trader, Macro Economist, Legal Advisor, Business Strategist, Accountant

**Creative & Multimodal (5):** Multimodal Creator, Audio Producer, Linguist, Storyteller, Curriculum Designer

**Humanities & Social (5):** Geopolitician, Historian, Psychologist, Philosopher, Sociologist

**STEM Non-Software (5):** Medical Researcher, Earth Scientist, Physicist, Engineer, Astronomer

**Intelligence & Security Operations (13):** OSINT Collector, Threat Hunter, Forensics Analyst, Counter Intelligence, Network Analyst, SIGINT, FININT, Cyber Warfare, Social Engineering, Dark Web Analyst, Malware Analyst, Crypto Analyst, Security Reviewer

**Creative Ideation (6):** Hypothesis Generator, Scientific Critic, Idea Ranker, Idea Evolver, Proximity Mapper, Meta Reviewer

**Life & Everyday (3):** Educator, Chef, Travel Planner, Crisis Manager

Mỗi identity template bao gồm:
- Trạng thái ủy quyền và khung đạo đức
- Triết lý hoạt động và học thuyết cốt lõi
- Phương pháp luận (OWASP, ATT&CK, NATO source grading, FATF, CVSS, v.v.)
- Hệ thống phân cấp xác minh
- Các hành động bị cấm
- Ngữ cảnh pháp luật Việt Nam khi có liên quan (Nghị định 19/2023, Luật Phòng chống Rửa tiền 2022)
- Domain knowledge chuyên sâu (300-1000 từ kiến thức nền)
- Thinking style riêng biệt (empirical/pragmatic/analytical/creative/critical/systematic/strategic/adversarial/pedagogical/empathetic)

### 2.4. Production Excellence Standards

Mỗi agent nhận một **Production Excellence Directive** bắt buộc với tiêu chuẩn theo từng loại:

| Category | Standards |
|----------|-----------|
| **Web Applications** | Proper frameworks (React/Next.js/FastAPI), responsive design, error boundaries, environment config, auth, DB schema |
| **Security Audits** | 10 must-have criteria (CVE-specific PoCs, version fingerprinting, attack chains, remediation with exact versions) |
| **APIs** | OpenAPI documentation, proper HTTP semantics, rate limiting, pagination, versioning |
| **DevOps** | Docker multi-stage builds, health checks, graceful shutdown, log aggregation |
| **Data Analysis** | Statistical rigor, visualization, confidence intervals, reproducible methodology |

### 2.5. Self-Solving Strategy (Roo-Code Pattern)

Tất cả agents tuân theo framework thực thi lặp 5 bước:
1. Phân tích yêu cầu hoàn chỉnh trước khi hành động
2. Thực thi từng bước một
3. Xác minh mỗi bước thành công qua output của tool
4. Thích ứng khi thất bại — tối thiểu 3 lần thử trước khi tuyên bố không thể
5. Hoàn thành với kết quả dứt khoát — không bao giờ kết thúc bằng câu hỏi cho user

### 2.6. Evaluation & Quality Enforcement

**Rào chắn xác định (không thể bị LLM ghi đè):**
- Progress < 80% = NEVER mark complete
- Progress delta > 5% = ALWAYS continue (momentum preserved)
- Evolution severity >= 0.8 = MUST continue
- Missing LLM credentials detected = block completion
- Empty turn counter > 2 = force retry with different approach
- Stagnation detected (< 3% variance across 3+ turns) = trigger MetacognitiveAgent

**Self-Reflection Quality Checkpoints:**
- Kích hoạt mỗi 3 vòng hoặc mỗi 6 tool calls
- Chấm điểm theo domain với rubric 10 tiêu chí cho security tasks
- Phát hiện vòng lặp thất bại (cùng endpoint fail 3+ lần, cùng tool gọi 5+ lần cùng lỗi)
- Phát hiện trì trệ qua lịch sử điểm chất lượng

### 2.7. Nền Tảng Nghiên Cứu

Thiết kế harness được tham khảo từ nghiên cứu và best practices hàng đầu trong ngành:
- Anthropic's "Effective harnesses for long-running agents" — initializer agents, feature lists, self-verification, handoff artifacts
- Anthropic's "Harness design for long-running application development" — task state and evaluator design
- OpenAI's "Harness engineering: leveraging Codex in an agent-first world" — architectural constraints, browser validation
- LangChain's "Agent Frameworks, Runtimes, and Harnesses" — separation of concerns
- HumanLayer's "12 Factor Agents" — explicit prompts, state ownership, clean pause-resume
- Thoughtworks' "Harness Engineering" — context engineering + architectural constraints + entropy management
- The CAR (Control-Agency-Runtime) decomposition framework from "Harness Engineering for Language Agents"
- Manus.im's context engineering lessons — KV-cache locality, tool masking, filesystem memory

---

## 3. Orchestration Pipeline

### 3.1. NexusExecutionPipeline

Engine thực thi cốt lõi — điểm vào THỐNG NHẤT DUY NHẤT cho TẤT CẢ requests. Mỗi task tuân theo luồng chính xác sau:

**Phase 0 — Smart Context Aggregation:** Fetch song song từ 7 nguồn (memory_layers, knowledge_graph, RAG, XSkill, history, user_memory, active_recall) với timeout 3s/nguồn. Thuật toán chấm điểm: `(keyword_overlap * 0.4) + (source_priority * 0.4) + (length_factor * 0.2)`. Loại bỏ trùng lặp ở ngưỡng 0.6. Giới hạn token budget (8KB task đơn giản / 12KB task phức tạp).

**Phase 0.5 — Evolution Agent Analysis:** Khởi tạo lười (lazy), điều khiển bởi config. Phân tích quỹ đạo chất lượng qua các turns trước. Đưa gợi ý cải thiện. Đọc workspace truth (TASK_BRIEF, TODO, DECISIONS, workspace tree). Theo dõi gợi ý đã đưa (tối đa 30) để tránh lặp lại.

**Phase 1 — NexusOrchestrator Planning:** Chọn agents phù hợp từ 66 templates. Tạo turn plan với tiêu chí đánh giá. Theo dõi tiến độ per-task (thread-safe, không race conditions). Lập kế hoạch có nhận thức hạ tầng qua InfrastructureMap.

**Phase 1.5 — Security Plan Enforcement:** Ghi đè kế hoạch cho security tasks để đảm bảo thành phần team đúng (recon, vulnerability hunter, pentester, exploit engineer, forensics).

**Phase 1.6 — Divergent Thinking (chỉ task complex/expert):** Tạo 3 cách tiếp cận hoàn toàn khác nhau song song (temperature 1.0). Frames: production_excellence, unconventional, reverse. Chấm điểm theo Production Quality, Coverage, Modernity, Feasibility. Chiến lược được chọn bổ sung vào tất cả agent assignments.

**Phase 2 — Worker Creation:** Khởi tạo AgentWorker instances từ templates. Tạo worker động cho roles được giao ở turns sau (đảm bảo "No worker available" không bao giờ xảy ra).

**Phase 3 — Turn-Based Loop (lặp không giới hạn, thoát dựa trên chất lượng):**
- Think Phase: Suy luận song song (temperature 0.4, max 2048 tokens, không có tools)
- Discuss Phase: Tranh luận có cấu trúc (tối đa 2 rounds) — Challenge + Support → Rebuttal → Synthesis
- Execute Phase: AgentWorker với vòng tool không giới hạn, activity-based timeout, giới hạn output (20KB cho LLM, toàn bộ trong ACTIVITY.md)
- Context Sharing: Agents thấy thinking ngang hàng (đầy đủ) + kết quả (giới hạn 5KB) + synthesis thảo luận
- Ledger entries được ghi (kết luận có cấu trúc per agent)
- TODO.md được cập nhật, DECISIONS.md được ghi
- Checkpoints được lưu nếu bật

**Phase 4 — Evaluation:** Đánh giá chất lượng per-task. Tín hiệu tình huống được inject (lịch sử tiến độ, sức khỏe hạ tầng, blockers lặp lại, agents bị force-stop). Phát hiện trì trệ (< 3% variance kích hoạt MetacognitiveAgent override).

**Phase 5 — Final Report Generation:** LUÔN chạy, không bao giờ bỏ qua. Đọc toàn bộ TASK_BRIEF + ROE + TODO + ACTIVITY.md (chunked tại 30KB). Xác minh multi-pass. Output: FINAL_REPORT.md.

**Phase 5.5 — Background Processing (fire-and-forget, tracked bằng metrics):**
- Memory Layers pipeline: L0 → L1 (atoms) → L2 (scenes) → L3 (persona)
- XSkill Accumulation: stores task results + tools + agents for future learning
- Graph RAG Auto-Index: LLM extracts entities from results → Knowledge Graph
- Ontology Generation: document → LLM → entity/relation types → KG

**Phase 6 — Summarize for User:** LLM tóm tắt final report. Với >30K chars: chia thành sections 15KB, tóm tắt từng phần, rồi tổng hợp cuối. Stream tokens sang frontend. Thêm cảnh báo cắt nếu câu trả lời không kết thúc tự nhiên.

**Phase 7 — Checkpoint Persistence:** Lưu trữ PostgreSQL, tối đa 3 per task. Sống sót qua crash, tự resume khi restart. Khôi phục vị trí turn loop + trạng thái agents.

**Infrastructure Resilience:** Sau 3 turns liên tiếp tất cả fail, exponential backoff (30s → 60s → 120s, giới hạn 5 phút). Phát cảnh báo hạ tầng đến frontend.

### 3.2. Structured Discussion Protocol

Cơ chế tranh luận đa tác nhân đảm bảo phân tích chặt chẽ thông qua hợp tác đối kháng (adversarial collaboration):

**Round 1 — Challenge + Support:**
Mỗi agent xem xét thinking của đồng nghiệp và đưa ra:
- Challenges: Điểm yếu được xác định, giả định sai, các cân nhắc bị thiếu
- Supports: Điểm mạnh cần được bảo toàn

**Round 2 — Rebuttal:**
Agent bị challenge hoặc:
- Bảo vệ quan điểm với lý luận bổ sung
- Chấp nhận challenge và điều chỉnh cách tiếp cận

**Synthesis:**
Tổng hợp tự động tạo ra:
- `agreed_approaches`: Chiến lược đồng thuận
- `unresolved_disagreements`: Điểm cần nghiên cứu thêm
- `key_insights`: Phát hiện mới từ cuộc tranh luận

### 3.3. Agent Worker Execution

Mỗi agent hoạt động với:
- **Vòng tool không giới hạn**: Không có giới hạn iteration nhân tạo
- **Activity-based timeout**: Agents chạy cho đến khi thực sự xong hoặc trì trệ
- **Output capping**: LLM thấy tối đa 20KB per tool result; output đầy đủ được giữ trong ACTIVITY.md
- **Stuck detection**: Self-reflection kích hoạt sau N rounds không tiến triển
- **Context visibility**: Agents thấy thinking ngang hàng (đầy đủ) + kết quả (giới hạn 5KB) + discussion synthesis

### 3.4. Evolution Agent

Cố vấn chất lượng chuyên dụng chạy ở temperature 1.0:
- Phân tích quỹ đạo chất lượng xuyên suốt các turns
- Xác định blind spots (tối đa 3 per turn)
- Đề xuất gợi ý cải thiện cụ thể (tối đa 3)
- Đánh dấu câu hỏi thách thức (tối đa 2)
- Có thể chặn completion khi severity >= 0.8
- Theo dõi gợi ý đã đưa để tránh lặp lại

### 3.5. Task Decomposition Architecture

**Phân rã phân cấp 3 mức:**

| Mức | Phạm vi | Cơ chế |
|-----|---------|--------|
| **L1** | Turn-Based | Orchestrator quyết định số turns và trọng tâm mỗi turn |
| **L2** | Within-Turn | Agent assignments với dependencies và thực thi song song/tuần tự |
| **L3** | Sub-Delegation | Agents tự chủ spawn sub-agents cho sub-problems phức tạp cô lập |

**Định tuyến theo độ phức tạp:**
- Simple: 1 agent, 1 turn
- Medium: 2-3 agents, 2-4 turns
- Complex: 3-7 agents, 3-10 turns
- Expert: 3-7+ agents, turns không giới hạn (với divergent thinking)

### 3.6. Context Ledger & Epochs

Context Ledger là bản ghi có cấu trúc chỉ-thêm (append-only) ngăn chặn hiện tượng agent mất trí nhớ:

**LedgerEntry fields (per agent per turn):** turn, agent_role, agent_name, did_what (tối đa 800 chars), key_data (phát hiện quan trọng: URLs, CVEs), tools_used, success (boolean), output_chars, blockers, truncated_topics (gợi ý tìm kiếm cho agents tương lai).

**EpochSummary (auto-generated mỗi 10 turns):** epoch number, turns_range, summary (có cấu trúc theo role), accumulated key_data, roles_active, progress_trajectory. Cho phép task 100+ turns mà không tràn context.

**Xây dựng context cho agents:**
- Công việc trước đó của chính mình: Full entries (ngăn làm lại)
- Thỏa thuận team: TẤT CẢ discussion syntheses (không bao giờ hết hạn)
- Công việc đồng đội: Tập trung key_data (nhận thức cross-role)
- Epoch summaries: Cửa sổ động mở rộng theo độ dài task

### 3.7. Context Sharing & Budget Management

Khả năng nhìn thấy context giữa các agents với kiểm soát ngân sách nghiêm ngặt:

| Audience | Thinking | Results | Tool Outputs |
|----------|----------|---------|-------------|
| **Other Agents** | Full (usually < 2KB each) | Capped at 5KB (conclusion extraction) | Not shared |
| **Orchestrator** | Full | Capped at 8KB | Summary only |
| **ACTIVITY.md** | Full | Full (unlimited) | Full |
| **Frontend Events** | Capped at 2KB | Capped at 500 chars | Tool name + status |

Thuật toán trích xuất kết luận ưu tiên phần cuối output agent (nơi chứa findings/summaries) hơn phần đầu thô.

### 3.8. Subagent Delegation

Agents có thể tự chủ ủy thác subtasks với bàn giao context đầy đủ:
- Parent context injection: TASK_BRIEF.md + TODO.md + ACTIVITY.md (tail 12KB) + ledger summary
- Kiểm tra trùng lặp: Token overlap > 60% với ledger entries có sẵn = ngăn chặn công việc trùng
- Kết quả merge: Full subtask ACTIVITY.md append vào parent (blockquote formatting)
- Work file merge: Files _work/ được copy với prefix subtask ID (tránh xung đột)
- Khôi phục thất bại: Cứu vãn công việc một phần trước khi dọn dẹp
- Độ sâu lồng tối đa: 3 cấp

---

## 4. Prompt Engineering & Context Pipeline

### 4.1. Modular Prompt Assembly (PromptBuilder)

System prompts được lắp ráp từ 10 sections độc lập theo **thứ tự xác định** (quan trọng cho ổn định Anthropic cache key):

| # | Section | Source | Cacheable |
|---|---------|--------|-----------|
| 1 | **Identity** | 29 handcrafted templates per agent type | Static (per type) |
| 2 | **System Info** | Current date/time (UTC+7), OS, working directory, external hostname | Semi-static |
| 3 | **Tool Catalog** | Auto-generated from MCP registry (deterministic sort) | Semi-static |
| 4 | **File Editing Rules** | Read/edit/write rules, atomic operations | Static |
| 5 | **Behavior Rules** | Per-type behaviors + deployment rules + production excellence | Static (per type) |
| 6 | **Objective** | 5-step iterative execution framework (Roo-Code pattern) | Static |
| 7 | **Capabilities** | Task docs, agent autonomy, tool availability | Semi-static |
| 8 | **Response Format** | Strict rules (forbidden openers, markdown, real data) | Static |
| 9 | **Skills Section** | Lazy-loaded skill catalog with mandatory applicability check | Dynamic |
| 10 | **Dynamic Sections** | skill_context, profile, reasoning modifier | Dynamic (appended LAST) |

Thiết kế: Mỗi section có thể test độc lập. Nội dung dynamic đặt CUỐI CÙNG để tối đa hóa cache hit rate trên prefix tĩnh.

### 4.2. Agent Context Distribution (7-Phase Pipeline)

**Phase 0 (Smart Context Aggregation):**
Sources with priority weights:
- xskill: 90 (proven workflows — highest value)
- prev_task: 85 (session continuity)
- active_recall: 70 (user preferences/known facts)
- kg_context: 60 (knowledge graph entities)
- memory_layers: 50 (generic L1/L2/L3 memory)
- active_memory: 40 (agent MEMORY.md files)

Budget allocation: Effective score = priority * 0.6 + relevance * 0.4. Proportional budget distribution. Minimum 200 chars per section. Maximum 8000 chars per section. Smart truncation at sentence boundaries.

**Phase 1 (Orchestrator Planning):**
System prompt (~3000-5000 chars) + user message + agent catalog (50+ templates, ~2KB) + memory context (~8KB max). Outputs: intent analysis, task type, complexity, selected agents, execution plan, constraints, language preference.

**Phase 2 (Task Documents Generation):**
TASK_BRIEF.md (mission statement), ROE.md (Rules of Engagement with ATT&CK mappings for security), TODO.md (living progress tracker). Stored in workspace_data/tasks/{task_id}/_context/.

**Phase 3A (Thinking Phase context):**
Agent identity + assignment + success criteria + shared context from peers + previous turns (6KB compressed) + ROE summary (3KB). Temperature 0.4, max 2048 tokens, no tools.

**Phase 3B (Discussion Phase context):**
Peer thinking results + challenge/support prompts + rebuttal injection for challenged agents.

**Phase 3C (Execution Phase context):**
Full tool-calling loop with intelligent output capping:
- LLM messages: max 20KB per tool result
- ContextSharing to peers: 5KB cap
- Orchestrator evaluation: 8KB cap
- Frontend events: 500 chars
- ACTIVITY.md: FULL (unlimited fidelity)

**Phase 4 (Evaluation context):**
Turn results + agent evaluations + contradictions + missing items + progress % + milestone achievement. Temperature 0.2, max 3072 tokens. Guards: progress < 80% = never complete; delta > 5% = continue; evolution >= 0.8 = must continue.

**Phase 5 (Evolution Agent context):**
Quality trajectory + blind spots (max 3) + improvements (max 3) + challenge questions (max 2). Severity score 0.0-1.0.

**Phase 6 (Final Report context):**
TASK_BRIEF + ROE + TODO + ACTIVITY.md (chunked at 30KB). Multi-pass extraction per chunk. Verification pass.

**Phase 7 (User Answer):**
Final report (chunked if >30K). Temperature 0.3, max 32768 tokens. Truncation warning appended if incomplete.

### 4.3. Strict Response Rules (Inject vào TẤT CẢ agents)

**Cụm từ mở đầu bị cấm:** "Great", "Certainly", "Sure", "Of course", "Absolutely" — tất cả bị loại khỏi từ vựng agent.

**Hành vi bắt buộc:**
- Tham chiếu file PHẢI bao gồm đường dẫn tương đối (không chỉ tên file)
- Hiển thị 3-5 dòng xung quanh khi trình bày thay đổi code
- Không hỏi xin phép cho bước tiếp theo hiển nhiên
- Giải thích lỗi và đề xuất cách tiếp cận thay thế
- Không ngôn ngữ đệm, không xin lỗi, không disclaimer
- Trả lời bằng ngôn ngữ của user (tự động phát hiện)
- Sử dụng dữ liệu thực với URLs thực, chi tiết cụ thể

### 4.4. Reasoning Effort Control

6 cấp độ suy luận, có thể điều chỉnh động per session:

| Level | Behavior | Use Case |
|-------|----------|----------|
| OFF | Answer directly, no explanation | Quick lookups |
| MINIMAL | 1-sentence rationale | Simple Q&A |
| LOW | 2-3 sentence reasoning | Standard queries |
| MEDIUM | Clear moderate-detail reasoning | Default |
| HIGH | Detailed step-by-step breakdown | Complex problems |
| XHIGH | Exhaustive analysis, all considerations, alternatives, rejections | Security audits, research |

Cấu hình qua lệnh `/think [level]`. Mặc định: XHIGH cho tất cả agent operations.

### 4.5. Environment & Infrastructure Awareness

Được inject vào tất cả agent prompts:

**Environment Details:** Workspace files summary, recently edited files, stale file warnings, file editing rules (read before edit, use edit_file not write_file for existing files).

**Runtime Detection (parallel async, 5s timeout each):** Python version, Node.js, npm, Docker, Git, public network access, available ports (3000, 5000, 8000, 8080), tunnel tools (ngrok, cloudflared).

**Infrastructure Map (live status):** LLM provider availability (env var checks), cloud provider status (Alibaba, AWS, GCP), toolset health, execution environment (Docker, network, sandbox mode), execution tier capabilities (Tier 0-4).

**Self-Bootstrap Doctrine (BẮT BUỘC trong tất cả prompts):**
- Thiếu Node.js? → Cài đặt
- Thiếu Docker? → Cài đặt
- Thiếu Python library? → install_packages()
- Thiếu custom tool? → scaffold_custom_tool_module()
- Thiếu cloud? → cloud_provision() hoặc hỏi user
- KHÔNG BAO GIỜ nói "Tôi không thể làm X vì thiếu tool/service" — cài đặt/bootstrap trước
- Chọn cách tiếp cận HIỆN ĐẠI TỐT NHẤT, không bao giờ hạ cấp xuống demo nhanh

### 4.6. Skill Loading (Lazy, On-Demand)

Skills được tham chiếu bằng tên + mô tả trong system prompt nhưng nội dung chỉ load KHI agent quyết định skill phù hợp. Tiết kiệm tokens — 30+ skills sẽ tiêu tốn ~50KB nếu load eager.

**Điều kiện tiên quyết bắt buộc khi check Skill:**
1. Đánh giá xem skill nào trong danh sách phù hợp với task hiện tại
2. Nếu có → load nội dung skill (single fetch)
3. Nếu không → tiếp tục bình thường
4. Không bao giờ reload skills đã load
5. Files liên kết trong skills: không auto-load, chỉ đọc khi được tham chiếu rõ ràng

---

## 5. Memory System (L0→L3)

### 5.1. Tổng Quan Kiến Trúc

Hệ thống chưng cất tri thức tiến triển 4 tầng, lấy cảm hứng từ quá trình củng cố trí nhớ con người:

**L3 — Persona Synthesis (đỉnh):** Hồ sơ toàn diện user/agent, có versioning với tối đa 3 bản sao lưu. Kích hoạt mỗi 50 atoms mới. Tạo persona Markdown có cấu trúc.

**L2 — Scene Aggregation:** Gom nhóm L1 atoms thành các khối chủ đề mạch lạc (tối đa 15 scenes hoạt động). Chấm điểm recall đa từ khóa. Trễ 90s sau L1 (để atoms ổn định).

**L1 — Atom Extraction:** Atoms có kiểu (PERSONA, EPISODIC, INSTRUCTION, FACTUAL). Chấm điểm importance 0.0-1.0. Embedding vector cho semantic similarity. Loại bỏ trùng tại ngưỡng 0.85. Kích hoạt mỗi 5 turns. Tối đa 50 messages per batch.

**L0 — Capture Tracker (nền tảng):** Đếm messages per session. Ngưỡng warmup (1→2→4→...→N). Raw event stream đến các tầng trên.

### 5.2. Recall Mechanism (Multi-Source, Parallel)

**Multi-keyword scoring algorithm:**
1. Extract keywords from current query
2. OR query against memory database (PostgreSQL)
3. Compute keyword overlap ratio per result
4. Rank by: `(keyword_overlap * 0.4) + (source_priority * 0.4) + (length_factor * 0.2)`
5. Return top-K within token budget

**Recall Modes:** MESSAGE (current only), RECENT (current + last 3), FULL (all session context with optional LLM refinement)

**Multi-Source Search (async parallel):**
- Vector: pgvector semantic similarity
- FTS: Full-text search via tsvector (keyword matching)
- File: MEMORY.md + USER.md keyword line matching
- Graph: Knowledge graph entity lookup

**Ranking & Dedup:** Sort by relevance_score, dedupe by content hash, filter by min_relevance (0.3), limit to max_results (5). Inject as hidden `<recalled_memory>` markers (max 2000 chars).

### 5.3. Dreaming Engine (Offline Consolidation)

Lấy cảm hứng từ chu kỳ giấc ngủ con người và kiến trúc OpenClaw. Thăng cấp bộ nhớ nền từ sessions ngắn hạn sang lưu trữ dài hạn bền vững:

**Chu kỳ ngủ 3 pha:**

| Phase | Function | Algorithm |
|-------|----------|-----------|
| **Light Sleep** | Scan recent messages (30 days), extract candidates | Score: base 0.3 + preference signals (+0.2) + task completion (+0.15) + length (+0.1) + code blocks (+0.1) |
| **Deep Sleep** | Score staged candidates, promote top N to MEMORY.md | Promotion threshold: 0.6. Reasons: HIGH_IMPORTANCE, REPEATED_REFERENCE, USER_PREFERENCE, TASK_OUTCOME |
| **REM Sleep** | LLM generates narrative dream diary from staged memories | Appended to DREAMS.md with date header |

**Memory Types:** episodic, semantic, preference, task_outcome, user_profile

**Duplicate Prevention:** SHA256 hash (first 16 chars) tracked across all runs

**Cycle Frequency:** Every 6 hours (configurable)

### 5.4. Active Recall (Tìm Kiếm Bộ Nhớ Trước Mỗi Trả Lời)

Tự động kéo context liên quan trước mỗi reply của LLM:
- Query refinement: LLM extracts 3-5 key search terms from current conversation
- Parallel search across vector, FTS, graph, and file sources
- Ranking, deduplication, relevance filtering (min 0.3)
- Prompt injection format: Hidden `<recalled_memory>` markers (max 2000 chars)
- Graceful fallback: 3s timeout, empty result on failure

### 5.5. Additional Memory Components

| Component | Function |
|-----------|----------|
| **GraphRAG** | Knowledge graph with entity extraction, relationship mapping, multi-hop traversal |
| **Ontology Generator** | Auto-generates domain ontologies from indexed documents (fire-and-forget on index) |
| **User Memory** | Persistent per-user facts injected into MEMORY.md at session start |
| **Entity Extractor** | Named entity extraction from task results → Knowledge Graph nodes |
| **Graph Updater** | Fire-and-forget KG relationship updates |
| **Surgical Editor** | Precision memory editing (update/delete specific atoms) |
| **Task Working Memory** | Per-task temporary memory (cleared on task completion) |

---

## 6. XSkill Continual Learning

### 6.1. Kiến Trúc Engine

**ContinualLearningEngine** hoạt động theo hai pha:

**Pre-Execution (retrieve_knowledge):**
- Tìm skills theo similarity của task (dựa trên embedding)
- Tìm experiences theo pattern matching
- Inject vào context agent prompts (priority score 90 — cao nhất trong phân bổ ngân sách)

**Post-Execution (accumulate_knowledge):**
- Trivial Task Gate: Lọc nhiễu (lời chào < 30 chars, responses < 300 chars không có tool calls)
- ExperienceManager: Ghi nhận bài học chiến thuật cấp hành động
- SkillBuilder: Trích xuất workflows tái sử dụng cấp task
- ExperienceCritique: LLM-as-judge chấm điểm chất lượng 5 chiều
- TrajectoryCompressor: Nén execution logs thành SFT training data

### 6.2. Components

| Component | Function |
|-----------|----------|
| **SkillBuilder** | Extracts reusable task-level workflows from successful executions |
| **ExperienceManager** | Captures action-level tactical lessons (what worked, what didn't) |
| **ExperienceCritique** | LLM-as-judge scoring across 5 quality dimensions |
| **TrajectoryCompressor** | Compresses task execution logs into SFT training data |
| **TreeSearch** | Beam search for optimal skill discovery paths (max_depth=3, branching_factor=3, beam_width=2) |
| **Embedding** | Embedding integration for semantic similarity search |

### 6.3. Trivial Task Gate

Ngăn ô nhiễm tri thức bằng cách lọc:
- Greetings (< 30 chars matching trivial patterns)
- Short responses (< 300 chars without tool calls)
- Minimal execution (no meaningful tool usage)

### 6.4. Skills Library (40 Built-in Skills)

Danh sách đầy đủ tất cả skills có sẵn cho agents:

| # | Skill | Description |
|---|-------|-------------|
| 1 | **API Builder** | FastAPI/REST API design, implementation, and OpenAPI documentation |
| 2 | **architecture-diagram** | Dark-themed SVG architecture/cloud/infra diagrams as HTML |
| 3 | **biomass-gasifier-sizing** | Quantitative sizing of downdraft biomass gasifiers (throat/hearth, ER, syngas LHV, CGE, power chain) |
| 4 | **browser-use** | Automates browser interactions for web testing, form filling, screenshots, data extraction |
| 5 | **Canvas** | Display HTML content on connected OpenClaw nodes (Mac, iOS, Android) |
| 6 | **code-review** | Comprehensive code review with best practices analysis |
| 7 | **coding-agent** | Delegate coding tasks to Codex, Claude Code, or Pi agents via background process |
| 8 | **Content Writer** | Blog posts, articles, marketing copy, and SEO-optimized content |
| 9 | **Data Analyst** | Data analysis, visualization, and statistical insights using Python |
| 10 | **debugging-hermes-tui-commands** | Debug Hermes TUI slash commands: Python, gateway, Ink UI |
| 11 | **domain-intel** | Passive domain reconnaissance using Python stdlib (subdomains, SSL, WHOIS, DNS) |
| 12 | **github** | GitHub operations via gh CLI: issues, PRs, CI runs, code review, API queries |
| 13 | **hermes-agent-skill-authoring** | Author in-repo SKILL.md: frontmatter, validator, structure |
| 14 | **himalaya** | Himalaya CLI: IMAP/SMTP email from terminal |
| 15 | **jupyter-live-kernel** | Iterative Python via live Jupyter kernel (hamelnb) |
| 16 | **kanban-orchestrator** | Decomposition playbook + specialist-roster for orchestrator routing via Kanban |
| 17 | **kanban-worker** | Pitfalls, examples, edge cases for Hermes Kanban workers |
| 18 | **llm-guidelines** | Behavioral guidelines to reduce LLM coding mistakes (overcomplication, surgical changes) |
| 19 | **model-usage** | CodexBar CLI local cost usage to summarize per-model usage/cost data |
| 20 | **native-mcp** | MCP client: connect servers, register tools (stdio/HTTP) |
| 21 | **node-inspect-debugger** | Debug Node.js via --inspect + Chrome DevTools Protocol CLI |
| 22 | **open-source** | Documentation reference for browser-use open-source library |
| 23 | **plan** | Plan mode: write markdown plan to .hermes/plans/, no execution |
| 24 | **project-deploy** | Deploy projects built by agents to local dev server with real URLs |
| 25 | **python-debugpy** | Debug Python: pdb REPL + debugpy remote (DAP) |
| 26 | **Python Developer** | Expert Python development, debugging, optimization, best practices |
| 27 | **remote-browser** | Controls local browser from sandboxed remote machine |
| 28 | **Report Generator** | Generate professional reports in Markdown and DOCX formats |
| 29 | **requesting-code-review** | Pre-commit review: security scan, quality gates, auto-fix |
| 30 | **research** | Deep web research with source synthesis and report generation |
| 31 | **review-checklist** | Pre-merge review checklist based on recurring AI reviewer feedback patterns |
| 32 | **skill-creator** | Create, edit, improve, or audit AgentSkills (SKILL.md files) |
| 33 | **SQL Expert** | SQL query writing, optimization, schema design, database administration |
| 34 | **subagent-driven-development** | Execute plans via delegate_task subagents (2-stage review) |
| 35 | **System Admin** | System administration, Docker, CI/CD pipelines, deployment automation |
| 36 | **systematic-debugging** | 4-phase root cause debugging: understand bugs before fixing |
| 37 | **test-driven-development** | TDD: enforce RED-GREEN-REFACTOR, tests before code |
| 38 | **webhook-subscriptions** | Webhook subscriptions: event-driven agent runs |
| 39 | **Web Research** | Deep web research with comprehensive summarization and source verification |
| 40 | **writing-plans** | Write implementation plans: bite-sized tasks, paths, code |

Skills tùy chỉnh có thể được tạo tại runtime qua `scaffold_skill()` và load ngay lập tức qua `reload_skills()`.

---

## 7. LLM Provider Layer & Resilience

### 7.1. Hỗ Trợ Đa Nhà Cung Cấp

Hệ thống sử dụng **LiteLLM + AsyncOpenAI SDK**, hỗ trợ bất kỳ provider nào có endpoint `/v1/chat/completions` tương thích OpenAI:

| Provider | Models | Timeout | Free? |
|----------|--------|---------|-------|
| **AWS Bedrock** | Claude Opus/Sonnet, Llama, Titan | 900s | No |
| **OpenAI** | GPT-4o, GPT-4o-mini, GPT-4.1 | 60-120s | No |
| **Google Gemini** | gemini-2.0-flash, gemini-2.5-flash | 90s | Free tier |
| **OpenRouter** | 100+ models (many free) | Variable | Partial |
| **Kilo Gateway** | kilo-auto/free, nemotron-120b, poolside-laguna | 900s | Yes (200 req/hr/IP) |
| **Pollinations** | openai, mistral, llama, qwen | 900s | Yes (unlimited) |
| **g4f (GPT4Free)** | gpt-4o, gpt-4, claude, llama-3 | 900s | Yes |
| **Local (Ollama/vLLM)** | Qwen, Llama, Mistral (self-hosted) | 900s | Yes |

Bất kỳ provider nào có endpoint `/v1/chat/completions` đều hoạt động ngay (Together AI, Groq, Mistral, Deepseek, Fireworks, Cerebras, xAI).

### 7.2. LLM Client Architecture

**Multi-Provider Client (llm_client.py):**
- Maps provider names to AsyncOpenAI clients with provider-specific config
- API key resolution: explicit → provider-native env var → CORE_LLM_API_KEY (60s cache)
- Retry loop: MAX_RETRIES=3, exponential backoff (1s → 30s max) with 0.25 jitter
- Global concurrency semaphore (default 5 parallel calls)
- Deterministic tool definition sorting for Anthropic cache stability
- JSON response parsing with auto-repair (fixes truncated JSON, literal newlines)
- Tool message sanitization (ensures tool_use/tool_result pairing)

**Context Window Detection (model_context.py):**
Resolution order: User override (env var) → litellm registry (700+ models) → Family fallback patterns. Defaults to 128K if unknown. Thread-safe caching.

### 7.3. Circuit Breaker (Per-Provider)

Ba trạng thái: CLOSED → OPEN → HALF_OPEN → CLOSED

- **CLOSED → OPEN:** 5 lỗi hạ tầng liên tiếp trong cửa sổ 30s
- **OPEN → HALF_OPEN:** Sau 60s recovery timeout, cho phép single probe request
- **HALF_OPEN → CLOSED:** Probe thành công → reset failure count
- **HALF_OPEN → OPEN:** Probe thất bại → restart recovery timer

**Phân loại lỗi thông minh:**
- Infrastructure errors (CÓ trip): timeout, HTTP 500, 502, 503, 429, auth failures
- Logic errors (KHÔNG trip): HTTP 400, parse errors, content policy violations

**Health metrics theo dõi per provider:** total_calls, successes, failures, avg_latency_ms, current_state, last_state_change

### 7.4. Provider Fallback Chain (FallbackLLMClient)

**Fallback Algorithm (max 15 provider attempts per request):**
1. Get next available provider (respects cooldown + circuit breaker)
2. If circuit breaker open → skip to next
3. Flatten tool messages if switching provider family (bedrock↔openai↔free)
4. Resolve API key from credential pool
5. Attempt call
6. On success: report_success, return result
7. On failure: classify error →
   - should_compress → trim messages, retry same provider
   - should_rotate_credential → rotate key, retry same provider
   - should_fallback → try next provider

**Cooldown Ladder (stepped, not exponential):** 30s → 60s → 300s (3 steps max)

**Tool Message Flattening:** When switching between provider families that handle tools differently:
- assistant message with tool_calls → plain text summary
- tool role messages → user message with tool result text

### 7.5. Credential Pool (API Key Rotation)

Per-provider pool with per-key state tracking:
- Round-robin rotation across multiple keys
- Random starting index for load distribution
- Rate limit auto-response: mark current key as limited, apply 120s cooldown, rotate to next
- If all keys cooling: return key with shortest remaining cooldown (best-effort)
- Key masking in logs (only last 4 chars shown)
- Thread-safe mutations

### 7.6. Smart Model Routing

Chỉ sử dụng heuristics cấu trúc (không semantic matching, không gọi LLM):
- Độ dài ≤ 160 chars VÀ ≤ 28 từ VÀ một dòng VÀ không code/URLs VÀ < 10% ký tự đặc biệt → model rẻ
- Mọi thứ khác → primary (model mạnh)
- Bảo thủ: nếu không chắc, giữ primary model

### 7.7. Cost Optimization

| Feature | Mechanism | Impact |
|---------|-----------|--------|
| **Prompt Cache** | Anthropic cache_control injection, deterministic section ordering | ~75% cost reduction on repeated prompts |
| **API Key Cache** | 60s TTL in-memory (avoid env var reads per call) | Reduced I/O overhead |
| **Credential Pool** | Round-robin rotation on rate-limit, multiple keys per provider | Maximize throughput |
| **Emergency Context Trim** | Keep system_prompt + first_user_msg + last 40% of messages | Auto-retry on context overflow |
| **Smart Condensation** | Tree-sitter AST-aware compression (preserves file structure) | Intelligent context reduction |
| **Model-Specific Timeout** | GPT-4o-mini=60s, Sonnet=180s, Opus=900s | Fail-fast on known-fast providers |
| **Free Tier Fallback** | Kilo → Pollinations → g4f chain | Zero-cost operation when paid credits exhausted |

### 7.8. Cấu Hình API Miễn Phí

Hoạt động không cần cấu hình với providers miễn phí:

| Provider | Base URL | Auth Required | Rate Limit |
|----------|----------|---------------|-----------|
| **Kilo Gateway** | https://api.kilo.ai/api/gateway/v1 | No (IP-based) | 200 req/hr/IP |
| **Pollinations AI** | https://text.pollinations.ai/openai | No | Unlimited (throttled under load) |
| **GPT4Free (g4f)** | Local Python library | No | Provider-dependent |

Embedding miễn phí fallback: Qwen3-Embedding-0.6B (tự tải, cache cục bộ, hỗ trợ tiếng Việt)

Tìm kiếm miễn phí: DuckDuckGo (không giới hạn, không cần key, tích hợp sẵn)

---

## 8. MCP Tool System

### 8.1. Tổng Quan

68 tool modules cung cấp **192 registered tools** qua Model Context Protocol (MCP). Trong đó 63 module tự đăng ký khi khởi động (import-triggered registration), 5 module còn lại lazy-load qua `load_tool_module` (cloud_exec, deployment, device_bridge, docker_exec, interactive). Ngoài ra, agents có thể tạo custom tools không giới hạn tại runtime qua self_evolve.

**Kiến trúc đăng ký:** Mỗi tool file tự gọi `registry.register()` ở module level (pattern self-registration, import-tolerant — module thiếu optional dependency không chặn các module khác). 3 module `search_engines`, `search_orchestrator`, `search_scoring` là thư viện engine hỗ trợ (15 search engine adapters: Google, DuckDuckGo, Brave, Tavily, Serper, Arxiv, PubMed, Semantic Scholar, Google Scholar, GitHub, NVD, ExploitDB, Wikidata...) được các web/search tools sử dụng bên trong, không đăng ký tool riêng. `complete_task` là tool deprecated (no-op, pipeline tự quản lý completion) — cố ý KHÔNG đăng ký.

**Complete Tool Registry (192 tools):**

**File & Code (22):** read_file, write_file, list_files, delete_file, create_directory, move_file, copy_file, glob_files, find_workspace_file, get_workspace_info, get_file_info (filesystem) · edit_file, apply_edits (file_edit) · search_files, patch_file, diff_files (search_patch) · list_code_definitions, codebase_search, find_definitions, find_references, find_imports (code_navigation) · git (multi-command: status, diff, log, commit, push, pull, branch, checkout, merge, rebase, stash, tag)

**Search & Web (13):** web_search, web_search_news, web_search_academic, web_search_knowledge, web_search_medical, fetch_url (web) · document_search, rag_search, rag_index (rag) · activity_search (activity_search) · session_search, session_list_recent (session_search) · http_request

**Execution & Build (13):** execute_command, execute_code, install_packages (terminal) · run_tests, run_build, check_project_health (test_tools) · project_scaffold, install_dependencies, build_project (project_tools) · start_dev_server, stop_dev_server, list_dev_servers, get_server_logs (dev_server_tools)

**Browser Automation (8):** browser_navigate, browser_screenshot, browser_click, browser_type, browser_extract (browser) · browser_ai_action, browser_ai_extract, browser_ai_navigate_flow (browser_ai)

**Docker & Containers (10):** docker_compose_up, docker_compose_down, docker_build, docker_run, docker_exec, docker_logs, docker_ps, docker_network (docker_tools) · deploy_docker_build, docker_deploy (deployment) · [docker_exec cũng có bản lazy trong module docker_exec riêng]

**Network & Infrastructure (13):** port_scan, tcp_connect, ssh_exec, dns_lookup, network_probe (network) · check_health, monitor_process, check_disk_usage, check_port_available, measure_latency, system_resources (monitoring_tools) · setup_public_inbox, open_firewall_port (public_link_setup)

**Cloud & Device (6, lazy):** cloud_exec, cloud_provision (cloud_exec) · device_exec, frida_run, device_push, device_pull (device_bridge)

**Data & Output (15):** parse_json, generate_report, generate_word_document, generate_csv, generate_pdf, generate_excel, read_spreadsheet, edit_spreadsheet, read_document, edit_document (data) · database_query (database) · generate_chart (chart) · convert_document (document_converter) · compress, extract (archive)

**Communication (9):** send_email (email) · fetch_emails (email_inbox) · email_health_check (email_diagnostics) · send_telegram_message (telegram) · send_message (communication) · send_external_message, telegram_get_updates, world_connectivity_status, create_public_inbox (external_channels_tools)

**Notification & Interactive (2):** notify_user (notify) · ask_user (interactive, lazy)

**Documentation Generation (3):** generate_api_docs, generate_readme, generate_changelog (documentation_tools)

**CI/CD (3):** generate_github_actions, generate_dockerfile, generate_docker_compose (cicd_tools)

**GitHub & Bounty (12):** github_fork, github_clone, github_create_pr, github_search_issues, github_browse_bounties, github_api (github_tools) · search_bounties, verify_bounty_target, monitor_pr_status, respond_pr_review, earnings_ledger, prepare_bounty_submission (bounty_tools)

**Security & Intelligence (15):** scan_dependencies, scan_secrets, scan_headers (security_scan_tools) · cve_lookup, network_topology_map, ioc_validate, osint_domain_profile, threat_intel_enrich, evidence_acquire (intelligence_tools) · malware_sandbox_analyze, mobile_forensics_extract (malware_tools) · api_test_endpoint, api_test_sequence, api_mock_server (api_testing_tools) · audit_log (audit_log)

**Blockchain (3):** blockchain_trace, wallet_cluster, transaction_graph (blockchain_tools)

**Task & Workflow (12):** task_doc_read, task_doc_write, task_doc_edit, task_doc_search, task_doc_list, task_doc_delete (task_docs) · create_workflow, execute_workflow, list_workflows, get_workflow_status (workflow_tools) · schedule_task, list_scheduled_tasks (schedule)

**Todo Management (4):** todo_add, todo_list, todo_complete, todo_clear (todo)

**Agent & Skills (7):** delegate_task (delegate) · load_skill (skill_loader) · load_tool_module (tool_module_loader) · scaffold_custom_tool_module, scaffold_skill, reload_skills, discover_mcp_capability (self_evolve; kèm tool sinh runtime không giới hạn)

**Visual & Media (11):** analyze_image, describe_image, extract_text_from_image (vision) · visual_check, visual_check_responsive (visual_tools) · image_generate (image_gen) · text_to_speech, speech_to_text (voice) · video_info, extract_frames, extract_audio (video)

**Memory (3):** memory_read, memory_write, memory_search (memory_tool)

**Tunneling (3):** expose_port, stop_tunnel, list_tunnels (tunnel)

**Utilities (5):** calculate, current_datetime, hash_encode, system_info (utility) · translate_text (translate)

| Category | Modules | Tools |
|----------|---------|-------|
| **File & Code** | filesystem, file_edit, search_patch, code_navigation, git | 22 |
| **Search & Web** | web, rag, activity_search, session_search, http_request | 13 |
| **Execution & Build** | terminal, test_tools, project_tools, dev_server_tools | 13 |
| **Browser** | browser, browser_ai | 8 |
| **Docker & Containers** | docker_tools, deployment, docker_exec (lazy) | 10 |
| **Network & Monitoring** | network, monitoring_tools, public_link_setup | 13 |
| **Cloud & Device (lazy)** | cloud_exec, device_bridge | 6 |
| **Data & Output** | data, database, chart, document_converter, archive | 15 |
| **Communication** | email, email_inbox, email_diagnostics, telegram, communication, external_channels_tools, notify, interactive (lazy) | 11 |
| **Docs & CI/CD** | documentation_tools, cicd_tools | 6 |
| **GitHub & Bounty** | github_tools, bounty_tools | 12 |
| **Security & Intelligence** | security_scan_tools, intelligence_tools, malware_tools, api_testing_tools, audit_log | 15 |
| **Blockchain** | blockchain_tools | 3 |
| **Task & Workflow** | task_docs, workflow_tools, schedule | 12 |
| **Todo** | todo | 4 |
| **Agent & Skills** | delegate, skill_loader, tool_module_loader, self_evolve | 7 + unlimited (runtime) |
| **Visual & Media** | vision, visual_tools, image_gen, voice, video | 11 |
| **Memory** | memory_tool | 3 |
| **Tunneling** | tunnel, public_link_setup | 3 |
| **Utilities** | utility, translate | 5 |
| **Dynamic** | tool_module_loader, self_evolve | Unlimited (runtime generation) |

### 8.2. Self-Evolution (Tự Tạo Tool Tại Runtime)

Agents có thể tạo tools mới tại runtime qua `scaffold_custom_tool_module()`:
- Sinh Python implementation hoạt động từ mô tả ngôn ngữ tự nhiên
- Validate tên module/tool là Python identifiers hợp lệ
- Validate theo OpenAI tool parameter schemas
- Đăng ký ngay lập tức qua dynamic import (có sẵn trong cùng session)
- Lưu trữ tại `app/mcp/tools/custom/` để tồn tại qua các lần restart
- Ngăn ghi đè modules có sẵn (an toàn)

Ngoài ra, agents có thể tạo execution capabilities:
- `create_execution_capability()`: Bọc Docker command patterns thành tools
- `create_workflow_tool()`: Workflow nhiều bước thành single tool (chuỗi existing tools + Docker commands)

### 8.3. Workflow Tools (Persistent DAG Execution)

Multi-step workflows defined as directed acyclic graphs:

**Workflow Definition Schema:**
- StepDef: name, tool, params, depends_on (list), condition (optional), on_failure (stop/continue/retry)
- WorkflowDef: id, name, description, steps list, created_at

**Execution Engine:**
- Persistent storage: `.workflows.json` (survives restarts)
- Variable substitution: `{{var_name}}` and `{{step.output}}` placeholders
- DAG execution via Kahn's algorithm (topological sort)
- Parallel batch execution (asyncio.gather with semaphore, max 3 concurrent)
- Per-step condition evaluation (skip if false)
- Failure handling: stop (break), continue (mark failed, proceed), retry (1 auto-retry)
- Execution tracking: status, results, durations per step

**Available operations:** create_workflow, execute_workflow (by ID or inline), list_workflows, get_workflow_status

### 8.4. Execution Tiers

Mô hình thực thi 4 cấp cho leo thang khả năng:

| Tier | Environment | Capabilities |
|------|-------------|-------------|
| **0** | In-Process Python | File I/O, HTTP, search, basic computation |
| **1** | Docker Security Toolkit | nmap, jadx, frida-tools, nuclei, sqlmap, mitmproxy |
| **2** | Remote Device Bridge | ADB/SSH to rooted devices, Frida injection |
| **3** | Cloud Infrastructure | AWS/GCP/Alibaba CLI, resource provisioning |
| **4** | Self-Evolving Factory | Runtime tool creation, workflow composition |

### 8.5. Agent Spawner (Tạo Agent Động)

Agents được tạo động với identity templates phong phú.

**Base Tools (42 tools mỗi agent đều có):**
execute_code, execute_command, install_packages, read_file, write_file, list_files, create_directory, search_files, delete_file, move_file, copy_file, find_workspace_file, get_workspace_info, edit_file, apply_diff, patch_file, web_search, fetch_url, http_request, run_tests, run_build, check_project_health, project_scaffold, install_dependencies, generate_report, generate_chart, task_doc_write, task_doc_read, task_doc_list, task_doc_search, task_doc_edit, list_code_definitions, codebase_search, find_definitions, find_references, find_imports, calculate, current_datetime, git, database_query, compress, extract

**Tool Enrichment Map:** Tự động thêm tools bổ sung khi base tool có mặt:
- web_search → web_search_news, web_search_academic, web_search_knowledge, web_search_medical
- browser_navigate → browser_extract, browser_click, browser_type, browser_screenshot
- docker_run → docker_build, docker_exec, docker_logs, docker_ps, docker_compose_up, docker_compose_down, docker_network

**Agent Templates (66 complete templates across 8 categories):**

**Core/Foundational (12):** researcher (Research Specialist), developer (Code Engineer), analyst (Data Analyst), writer (Content Writer), coordinator (Team Coordinator), judge (Quality Judge), critic (Critical Analyst), architect (System Architect), debugger (Debug Specialist), assistant (Knowledge Assistant), orchestrator (Task Orchestrator), security_reviewer (Security Reviewer)

**Security Operations (5):** pentester (Penetration Tester), recon_specialist (Recon Specialist), exploit_developer (Exploit Engineer), devops (DevOps Engineer), vulnerability_researcher (Vulnerability Researcher)

**Specialized Technical (7):** frontend_developer (Frontend Developer), backend_developer (Backend Developer), database_engineer (Database Engineer), data_scientist (Data Scientist), ux_designer (UX Designer), project_manager (Project Manager), qa_engineer (QA Engineer)

**Business & Finance (5):** quant_trader (Quantitative Trader), macro_economist (Macro Economist), legal_advisor (Legal Advisor), business_strategist (Business Strategist), accountant (Accounting Specialist)

**Creative & Multimodal (5):** multimodal_creator (Multimodal Creator), audio_producer (Audio Producer), linguist (Linguist & Localizer), storyteller (Storyteller), curriculum_designer (Curriculum Designer)

**Humanities & Social (5):** geopolitician (Geopolitical Analyst), historian (Historian), psychologist (Behavioral Psychologist), philosopher (Philosopher & Ethicist), sociologist (Sociologist)

**STEM Non-Software (5):** medical_researcher (Medical Researcher), earth_scientist (Earth Scientist), physicist (Physicist & Chemist), engineer (Mechanical & Civil Engineer), astronomer (Astrophysicist)

**Life & Everyday (3):** educator (Educator & Tutor), chef (Chef & Nutritionist), travel_planner (Travel Planner), crisis_manager (Crisis Manager)

**Creative Ideation (6):** hypothesis_generator (Hypothesis Generator), scientific_critic (Scientific Critic), idea_ranker (Idea Ranker), idea_evolver (Idea Evolver), proximity_mapper (Proximity Mapper), meta_reviewer (Meta Reviewer)

**Intelligence & Security Operations (13):** osint_collector (OSINT Collector), threat_hunter (Threat Hunter), forensics_analyst (Digital Forensics Analyst), counter_intelligence (Counter-Intelligence Specialist), network_analyst (Network Intelligence Analyst), signal_intelligence (SIGINT Analyst), financial_intelligence (FININT Analyst), cyber_warfare (Cyber Warfare Specialist), social_engineering (Social Engineering Analyst), dark_web_analyst (Dark Web Analyst), malware_analyst (Malware Analyst), crypto_analyst (Cryptocurrency Analyst)

Mỗi template bao gồm: default_name, avatar emoji, description, personality, thinking_style (empirical/pragmatic/analytical/creative/critical/systematic/strategic/adversarial/pedagogical/empathetic), background (300-1000 từ domain knowledge), expertise tags, skills dict, và danh sách tools cụ thể.

**Knowledge Base Injection:** Mỗi template có domain knowledge toàn diện bao gồm architecture patterns, methodology frameworks (OWASP, ATT&CK, NATO source grading, FATF, CVSS), hướng dẫn tích hợp Context7, ngữ cảnh pháp luật Việt Nam khi có liên quan.

---

## 9. RAG Pipeline & Knowledge Graph

### 9.1. Kiến Trúc Pipeline

Documents đi qua pipeline 4 giai đoạn: **Chunking** (LangChain text splitters) → **Embedding** (Sentence-Transformers / OpenAI / Bedrock Titan / Qwen3-Embedding) → **Multi-Index Storage** (pgvector cho vectors, PostgreSQL tsvector cho FTS, knowledge_nodes/edges cho graph) → **Auto-Ontology Generation** (fire-and-forget cho documents > 500 chars).

### 9.2. Hybrid Retrieval với RRF Fusion

Ba chiến lược retrieval thực thi song song, kết quả hợp nhất qua Reciprocal Rank Fusion (RRF):

| Strategy | Engine | Strength |
|----------|--------|----------|
| **Vector similarity** | pgvector (cosine distance) | Semantic meaning matching |
| **Full-text search** | PostgreSQL tsvector + ts_rank | Exact keyword matching |
| **Graph traversal** | Knowledge Graph (BFS/DFS) | Relationship-aware, multi-hop reasoning |

RRF formula: `score = sum(1 / (k + rank_i))` across all strategies (k=60 default)

### 9.3. Knowledge Graph (GraphRAG)

**Nodes:** knowledge_nodes table (entity_type, name, properties, embedding vector)
**Edges:** knowledge_edges table (source_id, target_id, relationship_type, weight)
**Auto-population:** Entity extraction from task results + document indexing (fire-and-forget)
**Ontology Generation:** LLM extracts entity types and relationship types → creates typed schema for domain
**Retrieval:** Multi-hop traversal starting from query-matched nodes, expanding through weighted edges

### 9.4. Light RAG

QA đơn tài liệu trong bộ nhớ cho các câu hỏi nhanh về nội dung vừa index. Không lưu trữ vĩnh viễn — tạm thời, phạm vi session.

---

## 10. Security Architecture (13 Layers)

### 10.1. Mô Hình Phòng Thủ Theo Chiều Sâu (Defense-in-Depth)

| Layer | Module | Function |
|-------|--------|----------|
| 1 | **CommandGuard** | Dangerous command detection + risk classification |
| 2 | **SSRF Guard** | DNS pinning, private network blocking |
| 3 | **Tool Policy** | Per-role tool authorization |
| 4 | **Tool Guard** | Execution safety validation |
| 5 | **Exfiltration Guard** | Block secret leaks in LLM responses |
| 6 | **Secret Redaction** | 20+ patterns for sensitive data masking |
| 7 | **Rate Limiter** | Redis sliding-window per IP |
| 8 | **Session Mutex** | Concurrency control (prevent race conditions) |
| 9 | **Approval Workflow** | Human-in-the-loop for dangerous operations |
| 10 | **Audit Logging** | Complete event trail |
| 11 | **Atomic Writes** | Transaction-safe file operations |
| 12 | **Circuit Breaker** | Provider resilience (prevent cascade failures) |
| 13 | **Resource Monitor** | System resource limits enforcement |

### 10.2. Authentication

- JWT-based authentication (optional, enabled when admin_password configured)
- API key management for external access
- OAuth 2.1 PKCE support for MCP protocol

### 10.3. Security Research Capabilities

Công cụ đánh giá bảo mật tích hợp:
- Framework penetration testing với sinh ROE (Rules of Engagement) tự động
- Mapping ATT&CK framework
- Bộ công cụ quét và khai thác lỗ hổng
- Phân tích forensics và thu thập bằng chứng
- Kiểm tra tuân thủ pháp luật

---

## 11. Cognitive Upgrade (Self-Awareness, Metacognition, Divergence)

Ba tầng trí tuệ bổ sung được thêm vào core pipeline:

### 11.1. Layer 1: Self-Awareness (InfrastructureMap)

Agents biết CHÍNH XÁC những gì chúng có thể và không thể làm tại mọi thời điểm:

**InfrastructureMap (injected into system prompt):**
- LLM Provider status: Which providers configured, credential health
- Cloud Providers: Alibaba/AWS/GCP availability (actual connectivity check, not just env var existence)
- Toolset Health: Which tool categories operational (queried from registry)
- Execution Environment: Docker availability, network access, sandbox mode
- Execution Tier capabilities: Tier 0-4 availability summary
- Self-Bootstrap Options: Concrete instructions for missing capabilities

**Self-Bootstrap Doctrine (BẮT BUỘC):**
Agents KHÔNG BAO GIỜ bỏ cuộc khi thiếu capabilities. Chúng tự bootstrap: cài đặt packages, tạo Docker containers, scaffold custom tools, cấu hình credentials tương tác, hoặc nêu rõ những gì con người cần cung cấp.

### 11.2. Layer 2: Metacognitive Loop (MetacognitiveAgent)

Phân tích TẠI SAO hệ thống bị stuck và tạo ra breakthroughs có thể hành động. Kích hoạt bởi phát hiện trì trệ, thất bại lặp lại, hoặc loop patterns.

**6-Point Analysis Framework:**
1. WHAT is happening? (describe the failure pattern precisely)
2. WHY is it happening? (trace back to root assumption/blocker)
3. What ASSUMPTION is wrong? (the belief causing the loop)
4. What's ACTUALLY NEEDED? (the real prerequisite before progress)
5. What COMPLETELY DIFFERENT frame could work? (lateral thinking)
6. Should we ASK THE USER? (if blocker requires human input)

**Common Root Causes Detected:**
- "Agent assumes X exists but it doesn't" → need to CREATE it first
- "Agent repeats search because doesn't trust results" → need VERIFICATION not repetition
- "Agent uses wrong tool for the job" → need DIFFERENT TOOL
- "Task impossible with current capabilities" → need SCAFFOLD new tool or ASK USER
- "Agent downgraded to simpler approach" → REVERT to original plan, install required tools

**Output (MetacognitiveInsight):** root_cause, wrong_assumptions, missing_prerequisites, reframe, concrete_next_action, should_ask_user, user_question, recommended_agent, verification_criteria

### 11.3. Layer 3: Creative Divergence (DivergentThinker)

Sinh 3 cách tiếp cận hoàn toàn khác nhau TRƯỚC KHI bắt đầu thực thi. Chỉ kích hoạt cho task complex/expert.

**Ba Thinking Frames (temperature 1.0):**

| Frame | Instruction |
|-------|-------------|
| **Production Excellence** | "What would a senior engineer at a top tech company build? Proper frameworks, modern stack, never single-file." |
| **Unconventional** | "BREAK every assumption. What if the obvious approach is wrong? Think laterally, combine things that don't usually go together." |
| **Reverse** | "Work BACKWARDS from perfect end state. What's the LAST step? Trace the chain all the way back." |

**Chấm điểm chọn lọc:** Production Quality, Coverage, Modernity, Feasibility. Quy tắc quan trọng: Framework approach + installation > single-file shortcut. "Dễ deploy" KHÔNG PHẢI ưu điểm — đó là đi tắt.

**Kết quả:** Chiến lược được chọn (hoặc kết hợp nhiều approaches) bổ sung vào tất cả agent assignments trong turn loop.

---

## 12. Kiến Trúc Frontend

### 12.1. Technology Stack

| Technology | Version | Role |
|-----------|---------|------|
| React | 19 | UI Framework |
| TypeScript | 5.7 | Type Safety |
| Vite | 6 | Build Tool |
| Zustand | 5 | State Management |
| React Router | v7 | Routing |
| Tailwind CSS | Latest | Styling |

### 12.2. Route Structure (20 Routes)

Bảng dưới liệt kê các màn hình chính (20 routes chính). Ngoài ra còn có `/` (welcome), `/login`, các route tham số hóa (`/chat/:sessionId`, `/tasks/:taskId`, `/artifacts/:taskId`), dynamic plugin routes đăng ký runtime, và catch-all 404.

| Route | Function |
|-------|----------|
| `/chat` | Main conversation interface |
| `/chat/:sessionId` | Resume conversation |
| `/dashboard` | System overview & health |
| `/agents` | Agent registry & management |
| `/tasks` | Task list & monitoring |
| `/tasks/:taskId` | Task detail + timeline |
| `/graph` | Knowledge Graph visualization |
| `/xskill` | Continual learning dashboard |
| `/artifacts` | File management (9 format viewers) |
| `/skills` | Skills marketplace |
| `/process` | Multi-task monitoring |
| `/analytics` | Token usage, costs, patterns |
| `/terminal` | Terminal interface (ANSI colored) |
| `/admin/cron` | Cron job management |
| `/admin/plugins` | Plugin management |
| `/admin/batch` | Batch execution |
| `/settings` | User preferences |

### 12.3. Giao Tiếp Real-Time

**SSE Event Types:**
- `step` — Pipeline progress
- `token` — Response streaming
- `tool_start` / `tool_done` — Tool lifecycle
- `agent_spawn` — New agent creation
- `agent_thinking` — Reasoning display
- `discussion` — Debate data (challenges, rebuttals, synthesis)
- `deep_analysis` — Analysis results
- `self_reflection` — Quality checkpoints
- `evolution` — Improvement suggestions
- `heartbeat` — Keep-alive
- `usage` — Token summary
- `done` / `error` — Stream lifecycle

### 12.4. Kiến Trúc Component (~160 TSX files)

- **Chat components** (29): Messages, input, discussion stream, thinking display
- **Process components** (20): Event cards (DeepAnalysis, SelfReflection, Discussion, Tool)
- **Artifact viewers** (12): PDF, DOCX, Audio, Video, Image, Code, HTML, Markdown
- **Dashboard** (4): Stats, health, metrics, activity
- **Project builder** (5): Preview, progress, diff, file browser
- **Admin** (4): Skills, Cron, Plugins, Batch

**Tối ưu hiệu năng Frontend:**
- Code Splitting: Lazy-loaded routes (trang không phải chat)
- Virtual Scrolling: `@tanstack/react-virtual` cho danh sách lớn
- Memoization: React.memo trên navigation items
- LRU file content cache (30s refresh, visibility-aware)
- Skeleton Loading: Per-page skeletons khi đang load
- Pagination: Message history lazy-loading

---

## 13. Hạ Tầng & Triển Khai

### 13.1. Kiến Trúc Docker Compose

5 services chạy mặc định qua Docker Compose (thêm security-toolkit build-only profile, bật với `--profile tools`):

| Service | Image | Port Mapping | Role | Health Check |
|---------|-------|-------------|------|--------------|
| **nexus** | pvbang/mekongai-nexus:latest | 8899→8000 | Backend + Frontend (All-in-One) | curl /health |
| **nexus-gateway** | (same, different entrypoint) | 18800→18800 | Public API Gateway (300s timeout) | curl /healthz |
| **postgres** | pgvector/pgvector:pg16 | 5433→5432 | Database + pgvector | pg_isready |
| **redis** | redis:7-alpine | 6380→6379 | Cache + Pub/Sub | redis-cli ping |
| **openclaw** | (Node.js execution layer) | 18789→18789 | Sandbox + Channels (optional) | curl /healthz |

Volumes: postgres_data, redis_data, workspace_data (shared between nexus and openclaw)

### 13.2. Database Schema (19 Migrations · 22 Tables)

| Category | Tables |
|----------|--------|
| **Core** | agents, tasks, messages, tools, swarms (legacy, migration-managed) |
| **Memory** | agent_memories, memory_atoms, memory_scenes, memory_personas, memory_pipeline_state |
| **Knowledge** | knowledge_nodes, knowledge_edges, document_chunks, indexed_documents |
| **Learning** | skills, experiences |
| **Chat** | conversation_messages (with FTS tsvector) |
| **Execution** | task_events, task_checkpoints |
| **Security** | approval_requests, tool_policies, security_audit_results |

### 13.3. Auto-Scaling

- Số workers nhận thức CPU (tự phát hiện cores)
- Rate limits động (60 × cores RPM)
- Queue sizing dựa trên tài nguyên khả dụng
- Override cấu hình cho tinh chỉnh thủ công

### 13.4. Yêu Cầu Triển Khai

| Yêu cầu | Tối thiểu | Khuyến nghị |
|----------|-----------|-------------|
| Docker + Docker Compose | Latest | Latest |
| RAM | 4 GB | 8+ GB |
| CPU | 2 cores | 4+ cores |
| Storage | 10 GB | 50+ GB |
| GPU | Không cần | Không cần |
| LLM API Key | Không cần (có free tier) | Khuyến nghị |

> **Production hardening:** Khi deploy production, cần thay đổi các giá trị default trong `.env` (mật khẩu database, APP_SECRET, JWT secret, terminal API key — server fail-fast nếu phát hiện secret default), giới hạn CORS, và cân nhắc mount `docker.sock` read-only hoặc tách OpenClaw ra hạ tầng riêng.

---

## 14. API Reference & Giao Tiếp Đa Kênh

### 14.1. Core Endpoints

**Chat & Streaming:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/chat/stream` | SSE streaming response |
| POST | `/api/v1/chat/` | Synchronous chat |
| GET | `/api/v1/chat/profiles` | Available conversation profiles |
| GET | `/api/v1/chat/models` | Available LLM models |

**Sessions:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/sessions` | List sessions |
| GET | `/api/v1/sessions/search?q=` | Full-text search |
| GET | `/api/v1/sessions/{id}/messages` | Load messages |
| DELETE | `/api/v1/sessions/{id}` | Delete session |

**Tasks:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/tasks/` | List all tasks |
| GET | `/api/v1/tasks/{id}` | Task detail |
| GET | `/api/v1/tasks/{id}/events` | Execution events |
| POST | `/api/v1/tasks/{id}/cancel` | Cancel task |

**System:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/system/info` | System information |
| GET | `/api/v1/system/background-metrics` | Background task observability |
| GET | `/api/v1/doctor` | Health check |
| GET | `/api/v1/analytics` | Analytics data |

**Nâng cao (Advanced):**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/decisions/{id}/answer` | Trả lời interactive decision (agent hỏi user) |
| POST | `/api/v1/batch/run` | Chạy batch nhiều model/song song |
| GET | `/api/v1/session-state/{id}/state` | Session state snapshot |
| GET | `/api/v1/session-state/{id}/events` | Event replay |
| GET | `/api/v1/history` | Session history (full-text search) |
| POST | `/api/v1/project-builder/*` | Project Builder pipeline |
| POST | `/api/v1/terminal` | Terminal (terminal-key protected) |
| POST | `/api/v1/hermes/chat`, `/code-edit`, `/terminal`, `/search` | Hermes agent bridge |
| GET/POST | `/api/v1/cron/jobs`, `/api/v1/skill-runner/*` | Cron jobs + skill-runner |
| GET/POST | `/api/v1/plugins/*`, `/api/v1/approvals/*` | Plugin management + approval workflow |
| POST | `/api/v1/public-inbox` | Tokenized public inbox |
| GET | `/api/v1/xskill`, `/api/v1/skills` | XSkill + Skills management |

**OpenAI-Compatible:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/chat/completions` | Drop-in OpenAI replacement |
| GET | `/v1/models` | Model listing |

### 14.2. Giao Tiếp Đa Kênh (9 Nền Tảng)

Mỗi kênh được bật độc lập qua biến môi trường riêng (ví dụ bot token, API key, URL). Channel manager kết nối các kênh đã đăng ký song song với timeout 10s/kênh (15s tổng). Không cấu hình = không kích hoạt — hệ thống vẫn chạy đầy đủ qua Web UI.

| Channel | Transport | Features |
|---------|-----------|----------|
| **Telegram** | Bot API long-polling | Rich messages, inline keyboards |
| **Discord** | Slash commands, embeds | Guild management, thread support |
| **Slack** | Block Kit, Socket Mode | Interactive components |
| **WhatsApp** | Baileys / Cloud API | Media messages |
| **Email** | IMAP + SMTP | Thread tracking, attachments |
| **SMS** | Twilio API | Short message delivery |
| **Signal** | signal-cli-rest-api | E2E encrypted |
| **Matrix** | matrix-nio, E2EE | Decentralized, encrypted |
| **Webhook** | Generic HTTP | Custom integrations |

---

## 15. Hiệu Năng, Khả Năng Mở Rộng & Khả Năng Quan Sát

### 15.1. Quản Lý Context

| Feature | Limit | Strategy |
|---------|-------|----------|
| Tool output to LLM | 20 KB | Cap + full preserved in ACTIVITY.md |
| Context sharing between agents | 5 KB | Conclusion extraction (tail-priority) |
| Orchestrator evaluation | 8 KB | Summary extraction |
| Smart Context budget | 8-12 KB | Priority scoring + dedup + greedy top-K |
| Long message processing | 500K chars | map_reduce / sequential chunking |
| Context compression trigger | 80 KB | Middle messages summarized (keep first 10 + last 10) |
| Epoch compression | Every 10 turns | Structured summaries for 100+ turn tasks |

### 15.2. Tính Năng Phục Hồi

| Feature | Mechanism |
|---------|-----------|
| **Task Checkpoint & Resume** | PostgreSQL persistence, max 3 per task, atomic writes, auto-resume on restart |
| **Circuit Breaker** | Per-provider, 3-state (CLOSED/OPEN/HALF_OPEN), infrastructure-only trips |
| **Credential Pool** | Round-robin rotation, per-key cooldown (120s), random start index |
| **Emergency Context Trim** | Keep system_prompt + first_user_msg + last 40% of messages |
| **Task Watchdog** | Heartbeat monitoring, automatic stale task cleanup |
| **Graceful Cancellation** | Cooperative cancellation tokens (not forced kill) |
| **Infrastructure Backoff** | 3 consecutive all-fail turns → exponential wait (30s/60s/120s, cap 5min) |
| **Fallback Chain** | 15 provider attempts max, automatic tool message flattening on switch |
| **Cooldown Ladder** | Stepped (30s → 60s → 300s), not exponential |

### 15.3. Khả Năng Quan Sát (Observability)

| System | Function |
|--------|----------|
| **Background Metrics** | Fire-and-forget task tracking (XSkill, KG indexing, ontology) — API endpoint `/system/background-metrics` |
| **Provider Health** | Success rate, avg latency, failure count per provider (real-time) |
| **Activity Logger** | JSONL audit trail per task (every agent action recorded) |
| **Analytics Dashboard** | Token usage charts, cost tracking, model breakdown, hourly patterns (pure SVG) |
| **SSE Event Stream** | 14 event types for frontend real-time monitoring |
| **WebSocket Monitor** | System-wide health monitoring channel |
| **Structured Logging** | structlog (JSON format) for all backend operations |
| **Task Events** | PostgreSQL-persisted execution events (resumable, replayable) |

### 15.4. Concurrency & Mở Rộng

- Uvicorn workers: Auto-detect CPU cores (or manual override)
- Rate limiting: Redis sliding-window, 60 * cores RPM default
- Task queue: Bounded FIFO, configurable max concurrent
- LLM concurrency: Semaphore (default 5 parallel calls)
- Agent parallelism: MAX_PARALLEL_LLM_AGENTS=5 per turn
- Turn safety timeout: 1 hour (bug prevention only, not flow control)

---

## 16. Lộ Trình Phát Triển Tương Lai

### 16.1. Cải Tiến Ngắn Hạn

| Feature | Status | Impact |
|---------|--------|--------|
| Speculative execution (parallel turns) | Planned | 2-3× throughput |
| Automatic delegation on agent failure | Planned | Improved reliability |
| Strategic forecast in evaluation | Planned | Better planning |
| Cross-turn dependency resolution | Planned | Complex workflow support |

### 16.2. Mở Rộng Nền Tảng

| Direction | Description |
|-----------|-------------|
| **Multi-Tenant** | Organization-level isolation, team collaboration |
| **Skill Marketplace** | Community-shared skills with quality ratings |
| **Plugin Ecosystem** | Third-party integrations via manifest-driven plugins |
| **Federated Learning** | Cross-instance knowledge sharing (privacy-preserving) |
| **Real-Time Collaboration** | Multiple users observing/guiding same task |

### 16.3. Khuếch Đại Trí Tuệ

| Direction | Description |
|-----------|-------------|
| **Hierarchical Planning** | Multi-level goal decomposition with backtracking |
| **Transfer Learning** | Apply skills across domains |
| **Multi-Modal Reasoning** | Image/audio/video understanding in reasoning pipeline |
| **Autonomous Research** | Long-running research programs with periodic reporting |
| **Self-Optimization** | System auto-tunes its own parameters based on performance data |

### 16.4. Tính Năng Enterprise

| Feature | Description |
|---------|-------------|
| **SSO/SAML** | Enterprise identity provider integration |
| **RBAC** | Role-based access control for agents and tools |
| **Compliance** | SOC 2, GDPR, HIPAA compliance modules |
| **SLA Monitoring** | Response time guarantees with alerting |
| **Cost Control** | Per-team/project budget enforcement |

---

## Phụ Lục: Chi Tiết Kỹ Thuật Các Hệ Thống Con

### A.1. Agent Worker — Execution Engine Detail

Each AgentWorker is the lowest-level execution unit. Key parameters:

| Constant | Value | Purpose |
|----------|-------|---------|
| _TOOL_ROUND_SAFETY_CEILING | 100,000 | Safety cap (NOT flow control — agents stop when LLM stops calling tools) |
| _TOOL_TIMEOUT_SECONDS | 6,000 (100 min) | Per-tool execution timeout |
| _MAX_TIMEOUT_RETRIES | 3 | Retries after tool timeout |
| _TOOL_OUTPUT_CAP_CHARS | 20,000 (20KB) | LLM message cap (full output always in ACTIVITY.md) |
| _CONTEXT_COMPRESS_THRESHOLD_CHARS | 80,000 | Trigger smart compression (~60% of 128K context) |
| _CONTEXT_KEEP_RECENT_MESSAGES | 20 | Keep last 20 messages uncompressed (~5-6 tool rounds) |
| MAX_STUCK_ESCALATIONS | 5 | Force-stop after 5 consecutive stuck detections |
| MAX_STUCK_ESCALATIONS_SECURITY | 8 | Higher limit for security agents (exploitation needs more retries) |
| _DEFAULT_ROLE_MAX_ROUNDS | 500 | Default for unknown roles |

**Stuck Detection:** Tracks _recent_tool_calls, _stuck_count, _no_progress_since (wall-clock), _consecutive_error_calls. When stuck count reaches MAX_STUCK_ESCALATIONS, agent is force-stopped.

**Self-Reflection Integration:** SelfReflectionEngine injected with reflection_interval=3, min_tool_calls_before_reflect=3. Triggers quality checkpoint every 3 rounds.

### A.2. Smart Condensation — Tree-Sitter AST Compression

Preserves file structure during context compression:

| File Type | Extraction Strategy |
|-----------|-------------------|
| **Python (.py)** | Class defs, function defs, imports, decorators, docstrings (up to 5 lines) |
| **TypeScript (.ts/.tsx)** | Exports, functions, classes, interfaces via tree-sitter |
| **Markdown (.md)** | All headers (# lines) |
| **JSON/YAML** | Top-level keys |
| **Other** | First few lines as preview |

Default fold budget: 50,000 chars. Phase 1: tree-sitter AST (accurate). Phase 2: regex fallback (broad support). Output format: `{line_number:4d} | {content}`.

### A.3. Final Report Generator

Generates FINAL_REPORT.md from task workspace:

- Reads: TASK_BRIEF.md, ROE.md, TODO.md, ACTIVITY.md (entirety)
- Chunk size: 30,000 chars (larger = better synthesis)
- NO cap on number of chunks — processes ALL activity
- Multi-pass verification (tracks covered topics, prevents duplication)
- Minimal report fallback when no activity exists
- Event emission for frontend progress tracking

### A.4. Plugin System

Manifest-driven plugin architecture:

- Discovery: Scans `plugins/` and `workspace_data/plugins/` for plugin.json
- Loading: importlib.util for isolated SafeLoading
- Lifecycle: discover → validate manifest → load → register hooks → activate
- Capabilities model: Each plugin declares capabilities in manifest
- Hook integration: Lifecycle events (on_start, on_stop, on_message, on_tool_call)
- Plugin fields: id, name, version, capabilities, active, description

### A.5. Pentest Phases & ATT&CK Integration

Security tasks follow PTES (Penetration Testing Execution Standard) methodology:

| Phase | Min Rounds | Transitions To |
|-------|-----------|----------------|
| RECON | 2 | ENUMERATION |
| ENUMERATION | 2 | VULNERABILITY_SCAN, RECON |
| VULNERABILITY_SCAN | 3 | EXPLOITATION, ENUMERATION |
| EXPLOITATION | 3 | POST_EXPLOITATION, VULNERABILITY_SCAN |
| POST_EXPLOITATION | 2 | REPORTING, EXPLOITATION |
| REPORTING | 1 | RECON (re-test cycle) |

**MITRE ATT&CK Kill Chain (always-active context):**
1. Reconnaissance (T1595, T1592, T1589)
2. Weaponization (T1587, T1588)
3. Delivery (T1566, T1195)
4. Exploitation (T1203, T1190)
5. Installation (T1059, T1547)
6. Command & Control (T1071, T1573)

### A.6. ROE Generator (Rules of Engagement)

Auto-generates Rules of Engagement documents for security assessments:

- Target extraction via regex: Domain → Android package → CIDR → IP → Wallet → CVE
- Classification via LLM (no keyword matching — understands intent/language/context)
- Regex patterns: _DOMAIN_RE, _ANDROID_PACKAGE_RE, _IP_RE, _CIDR_RE, _WALLET_RE, _CVE_RE
- AppStore platform whitelist: play.google.com, apps.apple.com, appstore.com, itunes.apple.com

### A.7. Visual QA Engine

Automated UI quality assurance for Project Builder:

| Viewport | Resolution |
|----------|-----------|
| desktop | 1280 x 800 |
| tablet | 768 x 1024 |
| mobile | 375 x 812 |

**Flow:** Navigate URL (Playwright) → Set viewport → Take screenshot → Send to LLM Vision → Parse quality scores

**Fallback chain:** Playwright + Vision available → full screenshot analysis. Playwright unavailable → fetch HTML + text analysis. LLM no vision → text-based evaluation.

Screenshot naming: `{page_name}_{viewport}_{uuid}.png` saved to task workspace .screenshots/

### A.8. Approval Workflow (Human-in-the-Loop)

For dangerous tool executions requiring human confirmation:

**Approval Scopes:**
- ONCE: This request only
- SESSION: Remember for this session
- TOOL: Remember for this tool (any session)
- PERMANENT: Always allow/deny

**Statuses:** PENDING → APPROVED / DENIED / TIMEOUT / CANCELLED

**Features:** Channel-agnostic notification (Telegram, Discord, Web, CLI), configurable timeout (default 30s) with auto-deny, persistent approval history for audit, bulk approve/deny for batch operations.

### A.9. Cron Scheduler

Recurring task execution using APScheduler:

**CronJob fields:** job_id, name, cron_expression, task_message, user_id, enabled, last_run_at, last_result, run_count, delivery_channel (websocket/telegram/email), delivery_target

**Persistence:** Redis jobstore (APScheduler) or in-memory with 60s polling loop

**Delivery routing:** After job completion, route result to configured channel automatically

### A.10. OpenClaw Bridge

HTTP client for OpenClaw Gateway (execution layer):

**Capabilities:** Chat completions (send via OpenClaw channels), tool invocation (delegate to sandbox), message delivery (proactive outbound), health monitoring (periodic checks)

**Statistics tracked:** requests_sent, requests_failed, tools_invoked, messages_delivered

**Resilience:** Automatic retries with exponential backoff, health check background task

### A.11. Public Gateway

Minimal public API surface (port 18800):

- Separate from main backend for security isolation
- Header filtering: removes hop-by-hop headers (RFC 7230) before forwarding
- Proxy timeout: 300s (5 minutes) for long-running tasks
- Use case: Tokenized inbox without exposing admin endpoints
- Endpoints: GET /health, GET /ready, proxy to backend with filtered headers

### A.12. Parallel Tool Execution & Iteration Budget

**IterationBudget (Hermes-agent pattern):** Tracks and enforces max tool calls per session/task. Methods: consume(n=1) → bool (returns False when budget exhausted). Prevents infinite loops while still allowing unlimited rounds by default.

**Tool Argument Type Coercion (tool_coercion.py):** Auto-fixes LLM type errors in tool arguments before execution:
- String "42" → integer 42
- String "3.14" → float 3.14
- String "true"/"false" → boolean True/False
- JSON string → parsed object/array
- Number → string (when schema expects string)
- null → default value from schema

### A.13. Prompt Cache Optimization

Ensures deterministic system prompt assembly for Anthropic cache key stability:

**PromptComponent:** Named section with key, content, cacheable flag.
**CacheObservation:** Tracks cache_read, cache_write, total_input, broke (cache miss), changes list.
**PromptCacheOptimizer:** Maintains stable prefix hash. Rules: deterministic ordering from maps/sets, stable prefixes, mutations only at tail. Saves ~75% cost on repeated prompts.

### A.14. Docker Sandbox Manager

Isolated code execution with fine-grained resource control:

| Parameter | Default | Description |
|-----------|---------|-------------|
| image | python:3.12-slim | Base Docker image |
| memory_limit | 1g | Memory cap |
| cpu_quota | 200000 (2 cores) | CPU allocation |
| network_disabled | false | Agents need network access |
| read_only_rootfs | false | Agents need write access |
| timeout_seconds | 120 | Per-execution timeout |
| tmpfs_size | 512m | Temporary filesystem |
| pids_limit | 200 | Process limit |
| max_timeout | 600 (10 min) | Maximum allowed timeout |

Security: no-new-privileges, cap-drop ALL, user=nobody. Health checks and image lifecycle management.

### A.15. Message Bus (Redis Pub/Sub)

Inter-agent and system-wide communication:

| Channel Pattern | Purpose |
|----------------|---------|
| nexus:direct:{agent_id} | Direct messages to specific agent |
| nexus:swarm:{swarm_id} | Swarm/team broadcasts |
| nexus:broadcast | System-wide announcements |
| nexus:events | System events (task complete, error, etc.) |

Features: Async message handlers, publish buffer (max 500) during Redis outage, automatic buffer replay on reconnect, automatic re-subscribe on reconnect.

### A.16. Webhook System

Event-driven agent activation from external sources:

**WebhookEvent:** event_id, source (github/gmail/custom), event_type, payload, headers, received_at, session_id
**WebhookRegistration:** webhook_id, name, secret (HMAC validation), target_session, event_filter (regex), active

Pattern: External events (GitHub push, Gmail new email, custom HTTP) → WebhookManager → route to appropriate agent session → trigger execution.

### A.17. Hermes Agent MCP Bridge

MCP protocol bridge to Hermes Agent ecosystem:

**Connection modes:** stdio (JSON-RPC over stdin/stdout) and HTTP
**Capabilities:** Discover Hermes tools, execute tools on Hermes (terminal, code edit, search), send chat messages through Hermes agent loop, manage Hermes sessions.
**Thread-safe:** asyncio.Lock for all requests, auto-incrementing request_id.

### A.18. Session Manager

Per-session state management with TTL eviction:

**SessionState fields:** session_key ({user_id}:{conversation_id}), user_id, conversation_id, created_at, last_activity, message_count, is_active, interrupt_requested, interrupt_message, steer_messages, budget (IterationBudget), compression_chain, ref_count (active requests).

Features: 30-minute idle timeout with eviction, per-session caching of IterationBudget and CompressionChain, thread-safe (asyncio.Lock), interrupt/steer support (Hermes-agent pattern for mid-execution user input).

### A.19. Workspace Manager & File Locking

**FileLock — Advisory file locking for multi-agent coordination:**
- acquire_lock(filepath, agent_name) → bool
- LOCK_TIMEOUT = 600s (10 minutes)
- Prevents accidental overwrites during parallel agent writes
- Advisory only (warns, doesn't block)
- Thread-safe via threading.Lock
- Auto-releases expired locks

**Workspace root:** Configurable via WORKSPACE_DIR env var (default: workspace_data/)

### A.20. Document Processor

Multi-format document ingestion pipeline:

**Supported formats (50+ extensions):**
- Documents: .pdf, .docx, .doc, .pptx, .ppt, .xlsx, .xls
- Text: .txt, .md, .csv, .json, .xml, .yaml, .yml, .html, .htm, .rst, .log
- Images: .png, .jpg, .jpeg, .gif, .bmp, .tiff, .tif, .webp
- Audio: .wav, .mp3
- Archives: .zip
- Code: .py, .js, .ts, .jsx, .tsx, .java, .c, .cpp, .go, .rs, .rb, .php, .sql, .sh, .bat

**Processing:** MarkItDown library converts all formats to Markdown. OCR for images. Transcription for audio. Max file size: 50 MB. Upload directory: workspace/uploads/

### A.21. Error Classifier (Failover Taxonomy)

Structured error taxonomy determining recovery strategy:

| FailoverReason | Recovery Action |
|---------------|-----------------|
| auth | Rotate credential |
| auth_permanent | Skip provider permanently |
| billing | Skip provider |
| rate_limit | Rotate credential or cooldown |
| overloaded | Retry with backoff |
| server_error | Retry, then failover |
| timeout | Retry, then failover |
| context_overflow | Emergency trim, retry |
| payload_too_large | Trim payload, retry |
| model_not_found | Try alternative model |
| format_error | Flatten tool messages, retry |
| thinking_signature | Retry without thinking |
| long_context_tier | Trim to shorter context |
| unknown | Generic failover |

Classification pipeline: provider-specific patterns → HTTP status code → error code → message regex → transport errors → fallback.

### A.22. Docker Multi-Stage Build (Dockerfile)

4-stage production build creating an all-in-one container:

| Stage | Base Image | Builds | Fallback |
|-------|-----------|--------|----------|
| 1. frontend-build | node:20-alpine | React frontend (Vite) | — |
| 2. openclaw-build | node:22-bookworm-slim | OpenClaw gateway (pnpm, Node >= 22.14.0) | Placeholder dist/index.js |
| 3. ironclaw-build | rust:1.82-slim | IronClaw sandbox binary (Rust) | Placeholder binary |
| 4. Final | python:3.12-slim | Combines all + Python backend (uv for fast deps) | — |

System dependencies in final image: bash, build-essential, ca-certificates, curl, git, iproute2, procps. Non-fatal build failures with fallbacks for OpenClaw and IronClaw.

### A.23. Coding Engine (Runtime Code & Tool Generation)

Auto-generates code and tools when the system detects missing capabilities:
- Detects capability gaps during execution
- Generates Python code for new tools
- Tests generated code in sandbox (validate before register)
- Registers new tools into MCP registry immediately
- Inspired by Roo Code's on-the-fly tool generation pattern

### A.24. Session Audit Logger (Crash-Safe Logging)

Append-only, crash-safe audit logging per session:

**Tracked events:** phases (start/end), LLM calls (with tokens/costs), tool calls (with durations), agent execution, errors, user interactions

**Design:** Human-readable log format designed for `tail -f` viewing. Per-session metrics and completion summaries. Crash-safe (append-only, no corruption on kill).

### A.25. Structured Action Logger (JSONL Timeline)

JSONL-based structured action logging (MiroFish pattern):
- Per-session timeline reconstruction
- Analytics (tool usage frequency, error rates, cost tracking)
- Separate from session audit logger (structured vs human-readable)

### A.26. Tree-Sitter Code Parser

AST-based code structure extraction (more accurate than regex):
- Extracts function signatures, class declarations, imports, decorators
- Used by Smart Condensation for intelligent context compression
- Supports Python, TypeScript/JavaScript, and more via tree-sitter grammars
- Graceful fallback to regex when tree-sitter library unavailable
- Inspired by Roo Code's tree-sitter implementation

### A.27. AWS Credential Refresher

Automatic AWS session token management:
- Refreshes via capitol-ai-grafana IMDS bridge
- Handles Bedrock credential lifecycle
- Graceful fallback to existing credentials file when refresh fails
- Background refresh before expiration

### A.28. Background Task Metrics

Thread-safe singleton for fire-and-forget task observability:
- Tracks success/failure counts per task category (XSkill, KG indexing, ontology, memory layers)
- Stores last error messages and timestamps
- API endpoint: GET /api/v1/system/background-metrics
- No external dependencies (in-process tracking)

### A.29. Checkpoint Persistence Service

PostgreSQL-backed crash recovery:
- Persists ExecutionState to database after each turn
- Enables auto-resume of tasks after server restart
- Pruning logic: keeps max 3 checkpoints per task (oldest pruned)
- Atomic writes (transaction-safe)
- Restores: turn position, agent states, progress history, ledger entries

### A.30. Model Switcher (Mid-Session Model Change)

Per-session LLM model selection:

| Alias | Model |
|-------|-------|
| claude-opus | us.anthropic.claude-opus-4-6-v1 |
| claude-sonnet | anthropic.claude-sonnet-4-6-v1 |
| gpt-4o | gpt-4o |
| gpt-4.1 | gpt-4.1 |
| gemini-flash | gemini-2.5-flash |
| free | kilo-auto/free |
| laguna | poolside/laguna-m.1:free |
| nemotron | nvidia/nemotron-3-super-120b-a12b:free |
| deepseek | deepseek-v4-pro |
| local | ollama/qwen2.5:32b |
| auto | smart routing (default) |

Switchable mid-session via `/model [alias]` command. Thread-safe via asyncio.Lock.

### A.31. Task Queue (Bounded Concurrency)

Redis-based async task queue for heavy orchestrator requests:
- Default concurrency: 3 concurrent tasks
- Bounded queue prevents server overload
- Returns HTTP 503 with wait estimate when queue full
- FIFO ordering (first-in, first-out)
- Configurable via TASK_QUEUE_MAX_CONCURRENT env var

### A.32. Tool Policy Engine (Fine-Grained Access Control)

OpenClaw-inspired per-tool authorization system:

**Actions:** allow, deny, ask (human approval), sandbox (isolated execution), allow_once

**Tool categories:** safe, moderate, dangerous, critical

**Conditional rules:**
- workspace_only: Tool can only access task workspace
- network_restricted: Tool cannot make outbound requests
- read_only: Tool cannot write/modify
- sandbox_required: Tool must run in Docker sandbox

Priority-based rule evaluation. Per-role policies. Audit logging of all decisions.

### A.33. System Metrics Emitter

Periodic real-time system health emission:
- Publishes CPU usage, memory usage, active task count, active agent count
- Emission to Message Bus → frontend WebSocket dashboard
- Collection interval: configurable (default periodic)
- Powers the System Dashboard health indicators

### A.34. Execution Context (Context Variables)

Per-request context propagation via Python contextvars:
- session_id, task_id, parent_task_id, agent_name
- Event callback streaming (SSE) attachment
- FileContextTracker attachment for tool handlers
- Per-task workspace isolation via subtask parent tracking
- Enables tools to know which task/session they're executing in without explicit parameter passing

### A.35. Conversation Profiles (Switchable Personas)

6 switchable conversation personas for different use cases:

| Profile | Behavior |
|---------|----------|
| **coder** | Concise, code-focused, minimal explanation |
| **analyst** | Data-driven, quantitative, evidence-based |
| **creative** | Imaginative, exploratory, brainstorming |
| **tutor** | Patient, step-by-step, pedagogical |
| **researcher** | Thorough, multi-source, citation-heavy |
| **default** | Balanced general-purpose |

Switchable per session. Affects system prompt tone and response format.

### A.36. Project Builder Pipeline

End-to-end project generation (7 phases):

| Phase | Function |
|-------|----------|
| 1. Blueprint | LLM generates ProjectBlueprint (name, tech_stack, components, pages, data_models, API endpoints, file_structure, dependencies, implementation_order) |
| 2. Scaffold | Create project dir, write scaffold files, install dependencies |
| 3. Implement | Generate code per file via LLM (respecting implementation_order, injecting dependency context) |
| 4. Build & Test | Run build command, parse errors via ErrorPatternMatcher, run test suite |
| 5. Visual QA | Screenshot each route via VisualQAEngine, score UI quality |
| 6. Iterate | Loop while quality < 0.8: evaluate → plan fixes → apply → rebuild → retest |
| 7. Deliver | Start dev server, return preview URL |

**Production standards enforced:** Modern frameworks (React/Next + FastAPI/Express/NestJS), proper structure, auth, DB schema, environment config, responsive design, error boundaries, deploy-ready.

---

## Appendix B: Configuration Reference

### Environment Variables. (Key Sections)

| Category | Variable | Default | Description |
|----------|----------|---------|-------------|
| **LLM** | CORE_LLM_PROVIDER | bedrock | Primary LLM provider |
| | CORE_LLM_MODEL | us.anthropic.claude-opus-4-6-v1 | Primary model |
| | AGENT_LLM_PROVIDER | openrouter | Agent LLM provider |
| | AGENT_LLM_MODEL | nvidia/nemotron-3-super-120b-a12b:free | Agent model |
| **Execution** | AGENT_MAX_ROUNDS | 0 (unlimited) | Max tool rounds per agent |
| | AGENT_REASONING_EFFORT | xhigh | off/minimal/low/medium/high/xhigh |
| | PIPELINE_INACTIVITY_TIMEOUT | 0 (unlimited) | Activity-based timeout |
| | EVOLUTION_AGENT_ENABLED | true | Continuous improvement advisor |
| | ENABLE_TASK_RESUME | true | Checkpoint & auto-resume |
| | MAX_PARALLEL_LLM_AGENTS | 5 | Max agents per turn |
| **Memory** | MEMORY_LAYERS_ENABLED | true | 4-layer memory system |
| | MEMORY_L1_EVERY_N_TURNS | 5 | Atom extraction frequency |
| | MEMORY_L3_TRIGGER_EVERY_N_ATOMS | 50 | Persona synthesis trigger |
| | MEMORY_RECALL_STRATEGY | hybrid | hybrid/keyword/embedding |
| | MEMORY_RECALL_TIMEOUT_MS | 3000 | Recall timeout (graceful fallback) |
| **Resilience** | CIRCUIT_BREAKER_FAILURE_THRESHOLD | 5 | Failures to trip |
| | CIRCUIT_BREAKER_FAILURE_WINDOW | 30.0 | Window (seconds) |
| | CIRCUIT_BREAKER_RECOVERY_TIMEOUT | 60.0 | OPEN → HALF_OPEN delay |
| **Scaling** | AUTO_SCALE | true | CPU-aware auto-configuration |
| | UVICORN_WORKERS | 0 (auto) | 0 = CPU cores |
| | RATE_LIMIT_RPM | 0 (auto) | 0 = 60 * cores |

Full configuration template: `.env.example` (478 lines covering all parameters across LLM providers, security, execution, memory, channels, and infrastructure).

---

## Appendix C: Database Entity Relationship

**Core Entities:**
- agents → tasks (one-to-many), tasks → task_events (execution trail), tasks → task_checkpoints (crash recovery), tasks → messages (inter-agent)
- agents → agent_memories (pgvector embeddings)
- agents → skills → experiences (continual learning)
- agents → conversation_messages (tsvector FTS indexed)

**Knowledge Entities:**
- knowledge_nodes ↔ knowledge_edges (GraphRAG bidirectional)
- document_chunks → indexed_documents (RAG pipeline)

**Memory Layer Entities:**
- memory_atoms (L1) → memory_scenes (L2) → memory_personas (L3)
- memory_pipeline_state (processing cursors per layer)

---

## Appendix D: Execution Tiers Detail

**Tier 0 — In-Process Python:** execute_code, read_file, write_file, web_search, http_request. Limits: no binary execution, limited network, no root access. Always available.

**Tier 1 — Docker Security Toolkit (nexus-security-toolkit image):** Pre-installed tools:
- openjdk-17, python3, wget, curl, git, unzip
- nmap, masscan (network scanning)
- sqlmap (SQL injection)
- jadx v1.5.0 (Android decompiler)
- apktool v2.9.3 (APK disassembly)
- frida-tools, objection, mitmproxy (dynamic analysis)
- nuclei (vulnerability scanning)
- ffuf (fuzzing)
- tcpdump, tshark (traffic capture)
- netcat, socat (networking)
- dnsutils (DNS tools)
- pwntools, impacket (exploitation)

Workspace mounting: Agent's _work/ → /workspace in container.

**Tier 2 — Remote Device Bridge:** ADB/SSH to rooted Android devices. Tools: device_exec, device_push, device_pull, frida_run. Requires: Pixel 6a/emulator with Magisk + frida-server. Configuration: NEXUS_DEVICES env var (JSON array of device configs).

**Tier 3 — Cloud Infrastructure:** AWS/GCP/Alibaba CLIs configured. Tools: cloud_exec, cloud_provision (temporary resources with 1h TTL: sandbox VM, network endpoint, storage). Alibaba-specific: alibaba_get_metadata() for STS tokens, RAM policy, privilege escalation paths.

**Tier 4 — Self-Evolving Tool Factory:** Agent CREATES tools at runtime. Tools: scaffold_custom_tool_module (creates new Python tool module with auto-deps), create_execution_capability (wraps Docker commands as tools), create_workflow_tool (chains existing tools + Docker as single tool).

---

---

## Phụ Lục E: Thống Kê Mã Nguồn

| Thông số | Giá trị |
|----------|---------|
| **Backend Python files** | 300+ |
| **Backend Lines of Code** | 80,000+ |
| **Core Module LOC** | 23,437 |
| **Orchestration LOC** | 10,951 |
| **MCP Tool LOC** | 27,681 |
| **MCP Tool Modules** | 68 (63 auto + 5 lazy) |
| **Registered Tools** | 192+ |
| **API Endpoint Modules** | 25 |
| **Frontend TSX files** | 160+ |
| **Frontend Components** | 150+ |
| **Custom Hooks** | 22+ |
| **Routes** | 20+ (thêm login, parameterized, dynamic plugin routes) |
| **Database Migrations** | 19 |
| **Docker Containers** | 5 (+ security-toolkit profile) |
| **LLM Providers hỗ trợ** | 8+ |
| **Communication Channels** | 9 |
| **Security Layers** | 13 |
| **Skills Directory** | 40 |
| **Agent Templates** | 79 |
| **Open-Source Projects tích hợp** | 14 |
| **Database Tables** | 22 |
| **Memory Layers** | 4 (L0-L3) |

---

*Tài liệu tạo: Tháng 6, 2026*
*Nexus v1.1.0*
