# HƯỚNG DẪN BÀN GIAO KỸ THUẬT SIÊU CHI TIẾT & CHUYÊN SÂU (NEXUS)

> **Tài liệu đặc tả kiến trúc cấp cao (Architectural Specification) và hướng dẫn vận hành hệ thống**  
> *Dành riêng cho đội ngũ kỹ sư Core, DevOps và Bảo mật.*  
> *Phiên bản: 1.1.0-Spec | Ngày phát hành: 06/07/2026*

---

## PHẦN 1: MÔ HÌNH THIẾT KẾ PHÂN TẦNG CAR & TRIẾT LÝ HARNESS ENGINEERING

### 1.1. Triết lý Harness Engineering trong Nexus

Trong các hệ thống AI tác nhân truyền thống (như AutoGPT hoặc các vòng lặp script đơn giản), tác nhân thường rơi vào trạng thái mất kiểm soát (entropy tăng cao), cạn kiệt cửa sổ ngữ cảnh (context exhaustion), hoặc lặp vô hạn do thiếu nhận thức về môi trường. Nexus giải quyết vấn đề này bằng phương pháp **Harness Engineering** — xây dựng một "bộ yên cương" kỹ thuật bao quanh tác nhân, kiểm soát chặt chẽ tài nguyên, ngữ cảnh, quyền hạn và khả năng tự phục hồi.

Hệ thống được thiết kế theo cấu trúc phân rã **Control-Agency-Runtime (CAR)**:

```
┌────────────────────────────────────────────────────────┐
│                   CONTROL LAYER                        │
│  - PromptBuilder (10 sections)                         │
│  - Archetype Registry (66 templates)                   │
│  - Production Excellence Directives                    │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│                    AGENCY LAYER                        │
│  - GoalEngine (Sub-goal tree)                          │
│  - Metacognitive Loop (Stagnation Detection)           │
│  - DivergentThinker (3 frames)                         │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│                    RUNTIME LAYER                       │
│  - NexusExecutionPipeline (8 Phases)                   │
│  - TurnExecutor (Think-Discuss-Execute)                │
│  - CheckpointPersistence (PostgreSQL restore)          │
└────────────────────────────────────────────────────────┘
```

### 1.2. Control Layer (Tầng kiểm soát)

*   **PromptBuilder (`app/core/prompt_builder.py`):** Lắp ráp Prompt hệ thống từ 10 phân đoạn (sections) theo một thứ tự tĩnh nghiêm ngặt. Việc này rất quan trọng để tối ưu hóa tỷ lệ trúng khóa lưu tạm (prompt cache hit rate) của Anthropic Bedrock hoặc OpenAI (chỉ thay đổi phần động ở cuối cùng):
    1.  *Identity*: Nhận diện Agent (ví dụ: Developer, Pentester) từ 66 templates tĩnh.
    2.  *System Info*: Thời gian hiện tại, hệ điều hành (Windows/Linux), thư mục làm việc.
    3.  *Tool Catalog*: Danh mục tools được sắp xếp theo thứ tự bảng chữ cái để giữ tính nhất quán.
    4.  *File Editing Rules*: Bộ quy tắc chỉnh sửa file nguyên tử (command line edits, atomic replacement chunks).
    5.  *Behavior Rules*: Quy tắc hành vi bắt buộc (cấm ngôn ngữ đệm "Great", "Certainly", bắt buộc dùng file path tương đối).
    6.  *Objective*: Khung thực thi 5 bước (Roo-Code Pattern).
    7.  *Capabilities*: Các tài liệu hướng dẫn và giới hạn quyền hạn.
    8.  *Response Format*: Yêu cầu định dạng đầu ra (strict JSON/Markdown).
    9.  *Skills Section*: Nạp danh sách skills tương thích dưới dạng lazy-loaded.
    10. *Dynamic Section*: Context của turn hiện tại, output của các Agent khác (được đẩy xuống cuối cùng).
*   **Production Excellence Directive:** Tự động tiêm (inject) vào tất cả các tác nhân các tiêu chuẩn kỹ thuật cấp cao: không được viết mã nguồn dạng giữ chỗ (placeholders), phải tích hợp Error Boundaries trong React, phải viết Docker multi-stage, phải sử dụng tham số hóa SQL để chống SQL Injection.

### 1.3. Agency Layer (Tầng tự chủ)

*   **GoalEngine (`app/core/goal_engine.py`):** Phân rã mục tiêu lớn của người dùng thành một cây mục tiêu con (sub-goal tree) dạng phân cấp. Mỗi mục tiêu con có trạng thái (`pending`, `active`, `completed`, `failed`) và danh sách các mục tiêu phụ thuộc (dependencies).
*   **Quality Checkpoints:** Ngưỡng chất lượng (quality threshold) được thiết lập động theo từng domain công việc:
    *   *Security/Pentest*: 0.80 (Yêu cầu bằng chứng PoC thực tế, không chấp nhận báo cáo lý thuyết).
    *   *Research/Analysis*: 0.70 (Yêu cầu nguồn dẫn chứng cụ thể, đường dẫn URL thực tế).
    *   *General/Simple*: 0.55.

### 1.4. Runtime Layer (Tầng thực thi)

*   **NexusExecutionPipeline (`app/orchestration/pipeline.py`):** Điểm vào duy nhất (Single Entry Point) cho toàn bộ yêu cầu. Đảm bảo tất cả các tác vụ đều đi qua các bộ lọc bảo mật, thu thập ngữ cảnh và lưu trữ checkpoint.
*   **CheckpointPersistence (`app/core/checkpoint_persistence.py`):** Lưu trạng thái thực thi vào PostgreSQL. Nếu tiến trình bị crash do cúp điện hoặc quá tải tài nguyên, khi hệ thống khởi động lại sẽ tự động quét bảng `task_checkpoints` và phục hồi đúng vị trí lượt (turn) và biến trạng thái của các Agent đang chạy dở dang (tối đa 3 checkpoints lưu trữ/task để tránh phình dữ liệu).

---

## PHẦN 2: CHI TIẾT PIPELINE ĐIỀU PHỐI ĐA TÁC NHÂN (ORCHESTRATION PIPELINE)

Quy trình hoạt động của `NexusExecutionPipeline` trải qua 8 giai đoạn tuần tự từ Phase 0 đến Phase 7:

```
[Phase 0: Context Aggregation] (Memory, KG, RAG, XSkill)
              │
              ▼
[Phase 1: Planning] (Select agents, build turn plans)
              │
              ▼
[Phase 2: Docs Generation] (Write TASK_BRIEF, ROE, TODO)
              │
              ▼
[Phase 3: Turn-Based Loop] (Think → Discuss → Execute)
              │
              ▼
[Phase 4: Evaluation] (Check quality, verify milestones)
              │
              ▼
[Phase 5: Evolution Analysis] (Analyze quality trajectory)
              │
              ▼
[Phase 6: Final Report] (Synthesize ACTIVITY.md)
              │
              ▼
[Phase 7: Summarization] (Deliver final answer to user)
```

### Phase 0: Smart Context Aggregation

*   **Chức năng:** Truy vấn đồng thời từ 7 nguồn dữ liệu nền tảng với cơ chế giới hạn thời gian phản hồi (timeout 3 giây/nguồn) để tránh nghẽn luồng.
*   **Thuật toán chấm điểm độ liên quan (Scoring):**
    $$\text{Score} = (\text{Keyword Overlap} \times 0.4) + (\text{Source Priority} \times 0.4) + (\text{Length Factor} \times 0.2)$$
    *   *Source Priority*: xskill (90), prev_task (85), active_recall (70), kg_context (60), memory_layers (50).
*   **Cơ chế lọc trùng (Dedup):** Đo lường tỷ lệ trùng lặp token (Token Overlap). Nếu vượt quá ngưỡng 0.6 giữa hai đoạn văn bản, đoạn có điểm thấp hơn sẽ bị loại bỏ.
*   **Quản lý ngân sách (Budget):** Cắt văn bản tại ranh giới câu gần nhất để đảm bảo không vượt quá giới hạn cấu hình (mặc định 8KB cho task đơn giản và 12KB cho task phức tạp).

### Phase 1: Orchestrator Planning

*   **Chức năng:** Sử dụng mô hình LLM mạnh nhất (Core LLM, ví dụ: Claude Opus) để lập kế hoạch tổng thể.
*   **Input Context:**
    *   Yêu cầu gốc của người dùng.
    *   Danh sách các Agent template dạng tóm tắt (2KB).
    *   Dữ liệu ngữ cảnh thu thập được từ Phase 0 (max 8KB).
*   **Output Plan JSON Schema:**
    ```json
    {
      "intent_analysis": "Phân tích ý định thực sự của người dùng",
      "task_type": "research|coding|analysis|creative|security|general",
      "complexity": "simple|medium|complex|expert",
      "selected_agents": [
        {
          "role": "vai_tro_agent",
          "name": "Tên định danh Agent",
          "primary_responsibility": "Nhiệm vụ chính"
        }
      ],
      "execution_plan": [
        {
          "turn": 1,
          "objective": "Mục tiêu lượt 1",
          "milestone": "Cột mốc đánh dấu hoàn thành lượt",
          "execution_mode": "parallel|sequential",
          "assignments": [
            {
              "agent_role": "vai_tro_agent",
              "task": "Mô tả chi tiết công việc",
              "success_criteria": "Tiêu chí nghiệm thu công việc",
              "dependencies": ["tên_role_phụ_thuộc"],
              "tools_hint": ["gợi_ý_tools"]
            }
          ]
        }
      ]
    }
    ```

### Phase 2: Task Documents Generation

Hệ thống ghi nhận 3 file Markdown trạng thái vào thư mục `_context/` của Task:
*   `TASK_BRIEF.md`: Bản mô tả nhiệm vụ, ràng buộc kỹ thuật và đội hình Agent.
*   `ROE.md` (Rules of Engagement): Các quy tắc ứng xử, tiêu chuẩn chất lượng. Đặc biệt với nhiệm vụ an ninh mạng (security tasks), hệ thống sẽ tự động ánh xạ mục tiêu theo MITRE ATT&CK techniques thông qua `RoeGenerator`.
*   `TODO.md`: Danh sách công việc của cả đội ngũ với các checkbox `[ ]` để cập nhật trạng thái liên tục.

### Phase 3: Turn-Based Loop

Mỗi lượt thực thi bao gồm 3 pha con:
1.  **Thinking Phase:** Các Agent nhận System Prompt chứa Identity của mình + Assignment của lượt + Context từ các lượt trước (`previous_context` tối đa 6KB). Các Agent thực hiện suy luận độc lập (temperature 0.4, không được gọi tools) và xuất ra cấu trúc JSON chứa phân tích chiến lược, rủi ro và các tools dự kiến sử dụng. Kết quả được đẩy lên bảng chia sẻ chung `ContextSharing`.
2.  **Discussion Phase:**
    *   *Round 1*: Các Agent đọc suy nghĩ của nhau từ `ContextSharing`, đưa ra các `challenges` (phản biện) và `supports` (đồng thuận) dưới dạng JSON.
    *   *Round 2*: Các Agent bị phản biện sẽ phản hồi lại (`rebuttals` hoặc `adjustments` để sửa đổi kế hoạch).
    *   *Synthesis*: Hệ thống tổng hợp cuộc thảo luận thành văn bản thống nhất (`agreed_approaches`, `unresolved_disagreements`, `key_insights`) làm kim chỉ nam cho pha thực thi.
3.  **Execution Phase (`AgentWorker`):**
    *   Các Agent chạy vòng lặp gọi tools không giới hạn.
    *   **Context Condensation**: Để tránh tràn ngữ cảnh (context overflow), nếu độ dài tin nhắn vượt quá 80KB, hệ thống kích hoạt nén: giữ lại prompt hệ thống + 10 tin nhắn gần nhất; toàn bộ các tin nhắn trung gian sẽ được tóm tắt lại thành 1 tin nhắn duy nhất ghi nhận các tools đã gọi thành công/thất bại và kết quả chính đạt được.
    *   **Output Capping**: Dữ liệu phản hồi từ tool trả về cho LLM bị cắt ở mức 20KB (kèm cảnh báo và gợi ý sử dụng `activity_search`). Toàn bộ dữ liệu đầy đủ (untruncated) được ghi trực tiếp vào `ACTIVITY.md`.

### Phase 4: Evaluation

`NexusOrchestrator` đánh giá kết quả của lượt dựa trên rubric tiêu chuẩn chất lượng.
*   **Guardrails nghiêm ngặt:**
    *   Nếu điểm tiến độ tự đánh giá `< 80%`, hệ thống tự động ghi đè quyết định thành `continue`.
    *   Nếu chênh lệch tiến độ lượt này so với lượt trước $> 5\%$ và tổng tiến độ $< 95\%$, hệ thống bắt buộc chạy tiếp lượt sau (momentum preservation).
    *   Nếu phát hiện lỗi thiếu thông tin cấu hình (Missing credentials), hệ thống chặn không cho hoàn thành và chuyển sang trạng thái chờ nhập liệu từ người dùng.

### Phase 5: Evolution Agent

*   `EvolutionAgent` (`app/orchestration/evolution_agent.py`) hoạt động ở nhiệt độ cao (temperature 1.0) để tạo sự sáng tạo.
*   Phân tích xu hướng chất lượng qua các lượt. Nếu phát hiện Agent bị lỗi lặp đi lặp lại hoặc chất lượng đi xuống, nó sẽ tính toán điểm nghiêm trọng (severity score từ 0.0 đến 1.0).
*   Nếu `severity_score >= 0.8`, hệ thống sẽ chặn không cho phép hoàn thành tác vụ, đồng thời tiêm các chỉ dẫn sửa đổi trực tiếp vào Assignment của lượt kế tiếp.

### Phase 6 & Phase 7: Final Report & User Summarization

*   **Final Report (`app/orchestration/final_report.py`):** Đọc toàn bộ file `ACTIVITY.md` theo từng khối 30KB. Trích xuất thông tin đa luồng, hợp nhất và tạo file `FINAL_REPORT.md` chứa toàn bộ tiến trình kỹ thuật, bằng chứng thu thập và khuyến nghị.
*   **User Summarization:** Tóm tắt báo cáo cuối cùng để trả lời người dùng. Nếu nội dung quá dài ($> 30\text{K}$ ký tự), hệ thống tự động cắt thành các đoạn 15KB để tóm tắt riêng lẻ rồi hợp nhất lại bằng một lượt xử lý cuối cùng (temp 0.3, max token 32K) nhằm đảm bảo không bị mất thông tin quan trọng.

---

## PHẦN 3: PHÂN RÃ TÁC VỤ 3 CẤP (SUB-TASK DECOMPOSITION PIPELINE)

Nexus thực hiện phân rã tác vụ thành 3 cấp độ độc lập để xử lý các bài toán từ đơn giản đến phức tạp:

```
┌────────────────────────────────────────────────────────┐
│ LEVEL 1: Turn-Based Plan (Objective & Milestones)      │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ LEVEL 2: Agent Assignments (Topological Dependency)    │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ LEVEL 3: Subtask Delegation (delegate_task() tool)     │
└────────────────────────────────────────────────────────┘
```

### 3.1. Level 1: Turn-Based Decomposition

Do `NexusOrchestrator` quyết định dựa trên độ phức tạp của bài toán:
*   *Simple Task*: Phân bổ 1 Agent chạy trong 1 Turn duy nhất (ví dụ: giải đáp câu hỏi lý thuyết).
*   *Medium Task*: 2-3 Agent chạy trong 2-4 Turns.
*   *Complex/Expert Task*: 3-7 Agent chạy trong 3-10+ Turns (tích hợp Divergent Thinking và giao thức tranh luận).
*   *Quy luật phân chia*: Lượt đầu luôn thu thập thông tin (recon/research), các lượt giữa phân tích và xử lý (analyse/execute), lượt cuối xác minh và báo cáo (verify/report).

### 3.2. Level 2: Within-Turn Agent Assignments

*   **Định nghĩa nhiệm vụ (`AgentAssignment`):**
    ```python
    @dataclass
    class AgentAssignment:
        agent_role: str           # "pentester", "developer"...
        agent_name: str           # Tên hiển thị trên giao diện
        task_description: str     # Mô tả công việc cụ thể
        success_criteria: str     # Tiêu chí nghiệm thu của lượt
        dependencies: list[str]   # Danh sách role phải chờ hoàn thành trước
        tools_hint: list[str]     # Gợi ý danh mục tools nên dùng
    ```
*   **Sắp xếp thứ tự thực thi (Topological Sort):**
    Nếu lượt chạy ở chế độ tuần tự (`sequential`), hệ thống sử dụng thuật toán sắp xếp topo để phân tích cây phụ thuộc:
    ```python
    def order_assignments(assignments):
        # 1. Tìm các assignment không có dependencies -> đẩy vào hàng đợi chạy trước
        # 2. Khi assignment hoàn thành, loại bỏ nó khỏi danh sách phụ thuộc của các assignment còn lại
        # 3. Lặp lại bước 1. Nếu phát hiện vòng lặp phụ thuộc (circular dependency), tự động quay về chế độ chạy song song (parallel)
    ```
    Các Agent chạy sau sẽ nhìn thấy kết quả chưng cất (max 5KB) của Agent chạy trước thông qua bộ nhớ chia sẻ.

### 3.3. Level 3: Subtask Delegation (Nested Orchestration)

Khi một tác nhân gặp một bài toán con quá phức tạp (ví dụ: cần dịch ngược một file nhị phân lớn trong khi Agent hiện tại là chuyên gia phân tích mạng), nó sẽ gọi tool `delegate_task()`:
*   **Cơ chế cô lập:** Hệ thống sẽ khởi tạo một instance `SubagentDelegator` mới. Instance này thừa hưởng tóm tắt ngữ cảnh của Agent cha và chạy một vòng lặp Turn Loop độc lập của riêng nó (giới hạn tối đa `max_iterations`, mặc định là 5).
*   **Quy tắc giới hạn độ sâu:** Giới hạn tối đa là 3 cấp lồng nhau (Parent Task → Subtask → Sub-subtask) để ngăn ngừa hiện tượng lặp vô hạn và cạn kiệt tài nguyên hệ thống.
*   **Hợp nhất dữ liệu:** Sau khi Subtask hoàn thành, thư mục làm việc của nó (`_work/`) sẽ được sao chép ngược về thư mục của Task cha với tiền tố ID của Subtask để tránh ghi đè file. Toàn bộ `ACTIVITY.md` của Subtask sẽ được định dạng blockquote và nối vào file hoạt động của Task cha.

---

## PHẦN 4: HỆ THỐNG BỘ NHỚ PHÂN TẦNG (MEMORY SYSTEM L0→L3) & DREAMING ENGINE

Kiến trúc bộ nhớ của Nexus được thiết kế để chuyển đổi thông tin từ dạng sự kiện ngắn hạn sang tri thức dài hạn thông qua 4 tầng chưng cất liên tục:

```
┌────────────────────────────────────────────────────────┐
│ L3: Persona Synthesis (Hồ sơ người dùng/tác nhân)       │
└──────────────────────────▲─────────────────────────────┘
                           │ (Synthesized every 50 atoms)
┌────────────────────────────────────────────────────────┐
│ L2: Scene Aggregation (Khối chủ đề mạch lạc)          │
└──────────────────────────▲─────────────────────────────┘
                           │ (Grouped every 90 seconds)
┌────────────────────────────────────────────────────────┐
│ L1: Atom Extraction (Hạt ký ức: Factual, Instruction)   │
└──────────────────────────▲─────────────────────────────┘
                           │ (Extracted every 5 turns)
┌────────────────────────────────────────────────────────┐
│ L0: Capture Tracker (Luồng sự kiện hội thoại thô)       │
└────────────────────────────────────────────────────────┘
```

### 4.1. Chi tiết 4 tầng bộ nhớ (L0 - L3)

1.  **L0 — Capture Tracker (`app/memory/layers/l0_capture.py`):** Ghi nhận toàn bộ luồng hội thoại thô (raw events) của session hiện tại vào database PostgreSQL.
2.  **L1 — Atom Extraction (`app/memory/layers/l1_extraction.py`):** Định kỳ mỗi 5 turns, một tiến trình chạy ngầm quét tối đa 50 tin nhắn gần nhất. LLM thực hiện phân tích và trích xuất các "hạt ký ức" (atoms) thuộc các nhóm: `PERSONA`, `EPISODIC`, `INSTRUCTION`, `FACTUAL`. Mỗi atom được gán điểm quan trọng (importance score từ 0.0 đến 1.0) và được lưu vào bảng `memory_atoms` kèm vector embeddings (Cosine distance).
3.  **L2 — Scene Aggregation (`app/memory/layers/l2_scenes.py`):** Gom nhóm các atoms có liên quan ngữ nghĩa thành các "phân cảnh" (scenes). Mỗi scene đại diện cho một chủ đề thảo luận lớn (ví dụ: "Cấu hình Docker Sandbox", "Quy trình khai thác CVE-2026-23918"). Quá trình này diễn ra trễ 90 giây sau khi L1 hoàn thành để đảm bảo các atoms đã ổn định.
4.  **L3 — Persona Synthesis (`app/memory/layers/l3_persona.py`):** Khi hệ thống tích lũy thêm 50 atoms mới, tiến trình ngầm sẽ thực hiện cập nhật lại hồ sơ Persona của người dùng và các Agent. Persona được định dạng Markdown có cấu trúc, lưu trữ lịch sử phiên bản (versioning) tối đa 3 phiên bản gần nhất để có thể phục hồi nếu dữ liệu bị ô nhiễm thông tin sai lệch.

### 4.2. Cơ chế Dreaming Engine (Pha ngủ ngầm)

Lấy cảm hứng từ cơ chế sinh học của con người, Dreaming Engine chạy ngoại tuyến (offline) định kỳ mỗi 6 giờ (`app/memory/dreaming.py`):
*   **Pha 1 (Light Sleep):** Quét toàn bộ tin nhắn trong vòng 30 ngày qua, chấm điểm ứng viên ký ức dựa trên công thức:
    $$\text{Candidate Score} = 0.3 \text{ (base)} + 0.2 \text{ (user preference)} + 0.15 \text{ (task completion)} + 0.1 \text{ (contains code block)}$$
*   **Pha 2 (Deep Sleep):** Thăng cấp (promote) các ứng viên có điểm $> 0.6$ lên tệp tin ký ức vĩnh viễn `MEMORY.md` của hệ thống.
*   **Pha 3 (REM Sleep):** LLM đọc các ký ức đã thăng cấp và viết thành một chuỗi nhật ký tường thuật (narrative dream diary) lưu trữ vào `DREAMS.md`. Để tránh trùng lặp ký ức qua các phiên chạy, hệ thống băm SHA256 (16 ký tự đầu) nội dung ký ức và đối chiếu trước khi ghi.

### 4.3. Thuật toán Active Recall (Truy xuất bộ nhớ)

Trước khi LLM tạo ra bất kỳ phản hồi nào, một tiến trình truy xuất song song (Parallel Search) được kích hoạt với timeout 3 giây:
*   *Vector Search*: Tìm kiếm tương đồng ngữ nghĩa trên bảng `memory_atoms` sử dụng `pgvector`.
*   *Full-Text Search (FTS)*: Tìm kiếm từ khóa chính xác sử dụng chỉ mục `tsvector` của PostgreSQL.
*   *File Search*: Đọc nhanh dòng khớp từ khóa trong `MEMORY.md` and `USER.md`.
*   *Graph Search*: Truy vấn các node liên quan trực tiếp trong Knowledge Graph.
Kết quả được hợp nhất, sắp xếp theo điểm liên quan và tiêm vào prompt dưới thẻ ẩn `<recalled_memory>` (tối đa 2000 ký tự).

---

## PHẦN 5: XSKILL CONTINUAL LEARNING

Khối học máy liên tục XSkill (`app/xskill/`) giúp Nexus tự động rút kinh nghiệm qua từng tác vụ:

```
[Pre-Execution]
  └─ Retrieve knowledge (proven workflows from skill DB)
  └─ Inject into Agent system prompt (priority score: 90)

[Task Execution (Turn Loop)]

[Post-Execution]
  └─ Trivial Task Gate (filter out greetings/short runs)
  └─ ExperienceManager (capture action-level tactical lessons)
  └─ SkillBuilder (extract reusable task-level workflows)
  └─ ExperienceCritique (LLM-as-judge 5-dimension scoring)
  └─ TrajectoryCompressor (compress logs to SFT training data)
```

### 5.1. Quy trình học tập (Learning Lifecycle)

1.  **Pre-Execution:** Khi nhận task mới, hệ thống tính toán vector tương đồng của mô tả tác vụ để tìm kiếm các Skill và Experience đã lưu trong database. Các tri thức này được tiêm vào prompt của Agent với điểm ưu tiên ngân sách cao nhất (Priority 90) để định hướng cách làm việc.
2.  **Post-Execution (Knowledge Accumulation):** Sau khi task hoàn thành:
    *   *Trivial Task Gate*: Lọc bỏ các tương tác ngắn (tin nhắn chào hỏi $< 30$ ký tự, câu trả lời $< 300$ ký tự không gọi tool) để tránh làm loãng cơ sở dữ liệu tri thức.
    *   *ExperienceManager*: Ghi lại các bài học kinh nghiệm ở mức hành động (ví dụ: "Gọi tool X với tham số Y sẽ bị lỗi Z, nên dùng tool W thay thế").
    *   *SkillBuilder*: Trích xuất kế hoạch thực thi thành công thành một Skill tái sử dụng dạng các bước thực thi (Phased Execution Plan).
    *   *ExperienceCritique*: Sử dụng LLM đóng vai trò giám khảo (LLM-as-judge) để chấm điểm chất lượng của trải nghiệm vừa lưu trên 5 chiều: Completeness, Correctness, Efficiency, Methodology, Output Quality.
    *   *TrajectoryCompressor*: Nén toàn bộ luồng suy nghĩ và hành động thành định dạng dữ liệu huấn luyện SFT (Supervised Fine-Tuning) để chuẩn bị cho việc tinh chỉnh mô hình local trong tương lai.

### 5.2. Công nghệ tìm kiếm Skill nâng cao (TreeSearch)

XSkill tích hợp thuật toán tìm kiếm cây (Beam Search) để phát hiện đường đi tối ưu khi thực hiện các kỹ năng phức tạp:
*   *Độ sâu tối đa (max_depth)*: 3 cấp.
*   *Hệ số nhánh (branching_factor)*: 3 nhánh con.
*   *Chiều rộng chùm (beam_width)*: 2 đường đi tốt nhất được giữ lại tại mỗi bước.
Cơ chế này giúp Agent tự động thử nghiệm các kịch bản workflow khác nhau trong môi trường mô phỏng trước khi đưa ra quyết định thực thi chính thức.

---

## PHẦN 6: TẦNG LLM PROVIDER & HẠ TẦNG PHỤC HỒI (RESILIENCE)

Tầng kết nối LLM (`app/core/provider_fallback.py`) được thiết kế để đảm bảo hệ thống có thể chạy liên tục $24/7$ mà không bị gián đoạn do lỗi API hoặc hết credit tài khoản:

### 6.1. Thiết kế Circuit Breaker (Cầu chì 3 trạng thái)

Mỗi LLM Provider (Bedrock, OpenAI, Gemini, OpenRouter, Kilo, Pollinations...) được giám sát bởi một bộ ngắt mạch độc lập:

```
       ┌────────── Fail 5 times in 30s ──────────┐
       │                                         │
       ▼                                         │
 ┌───────────┐                             ┌─────┴─────┐
 │   OPEN    │ <─── Probe request fails ── │ HALF-OPEN │
 └─────┬─────┘                             └─────▲─────┘
       │                                         │
       └───────── Cooldown 60s passes ───────────┘
```

*   **CLOSED (Bình thường):** Mọi request đi qua bình thường. Nếu phát hiện lỗi hạ tầng (Infrastructure Error: timeout, 5xx, 429), bộ đếm lỗi tăng lên. Nếu đạt 5 lỗi trong cửa sổ 30 giây, chuyển sang trạng thái `OPEN`.
*   **OPEN (Ngắt mạch):** Mọi yêu cầu đến provider này ngay lập tức bị từ chối (fail-fast) và chuyển sang provider dự phòng trong Fallback Chain mà không cần gọi API thật. Bắt đầu bộ đếm thời gian hồi phục (recovery timeout 60 giây).
*   **HALF-OPEN (Thử nghiệm):** Sau khi hết 60 giây, hệ thống cho phép duy nhất một yêu cầu thử nghiệm (probe request) đi qua:
    *   Nếu thành công: Reset bộ đếm lỗi, chuyển về `CLOSED`.
    *   Nếu thất bại: Chuyển ngược lại `OPEN`, reset bộ đếm thời gian hồi phục.
*   *Phân loại lỗi thông minh*: Lỗi logic do lỗi cú pháp prompt hoặc vi phạm chính sách nội dung (Content Policy) của người dùng sẽ **không** làm trip cầu chì để tránh ngắt mạch nhầm.

### 6.2. Cơ chế Credential Pool & Cooldown Ladder

*   **Credential Pool:** Hỗ trợ cấu hình nhiều API keys cho cùng một provider dưới dạng danh sách. Hệ thống thực hiện phân phối yêu cầu theo thuật toán xoay vòng (Round-Robin) bắt đầu từ một chỉ số ngẫu nhiên để tránh việc dồn tải vào khóa đầu tiên.
*   **Cooldown Ladder:** Khi một API Key bị lỗi Rate Limit (HTTP 429):
    *   Key đó sẽ bị tạm khóa (cooldown) theo các mức bậc thang: 30 giây $\rightarrow$ 60 giây $\rightarrow$ 300 giây.
    *   Yêu cầu được tự động định tuyến lại sang Key kế tiếp trong Pool.

### 6.3. Fallback Chain & Flattening Tool Messages

Khi tất cả API Keys của provider chính đều bị cooldown hoặc cầu chì bị ngắt, hệ thống sẽ kích hoạt chuỗi fallback mặc định:
$$\text{Core Provider (Bedrock/OpenAI)} \longrightarrow \text{Agent Provider (OpenRouter)} \longrightarrow \text{Kilo Gateway} \longrightarrow \text{Pollinations} \longrightarrow \text{g4f}$$
*   **Flattening Tool Messages:** Khi thực hiện fallback chuyển đổi giữa các họ mô hình khác nhau (ví dụ: từ Anthropic Claude sang OpenAI GPT hoặc Pollinations Llama), cấu trúc tin nhắn gọi tool (tool messages) sẽ được tự động làm phẳng (flatten) và chuẩn hóa lại cấu trúc tham số để tránh lỗi định dạng API đặc thù của từng hãng.

---

## PHẦN 7: MÔ HÌNH THỰC THI 4 CẤP (EXECUTION TIERS) & MCP TOOL SYSTEM

Để khắc phục hạn chế của các Agent chỉ hoạt động trong môi trường lý thuyết, Nexus phân cấp khả năng thực thi của các công cụ thành 4 cấp độ tăng dần:

```
┌────────────────────────────────────────────────────────┐
│ Tier 4: Self-Evolving Tool Factory                     │
│  - scaffold_custom_tool_module(), workflow DAGs        │
└──────────────────────────▲─────────────────────────────┘
                           │
┌────────────────────────────────────────────────────────┐
│ Tier 3: Cloud Infrastructure                           │
│  - cloud_exec(), cloud_provision() (AWS/Alibaba/GCP)    │
└──────────────────────────▲─────────────────────────────┘
                           │
┌────────────────────────────────────────────────────────┐
│ Tier 2: Remote Device Bridge                           │
│  - device_exec(), frida_run() (ADB/SSH rooted devices) │
└──────────────────────────▲─────────────────────────────┘
                           │
┌────────────────────────────────────────────────────────┐
│ Tier 1: Docker Security Toolkit                        │
│  - docker_exec() (nmap, sqlmap, jadx, nuclei)         │
└──────────────────────────▲─────────────────────────────┘
                           │
┌────────────────────────────────────────────────────────┐
│ Tier 0: In-Process Python                              │
│  - execute_code(), read_file(), write_file()           │
└────────────────────────────────────────────────────────┘
```

### 7.1. Chi tiết các cấp độ thực thi (Tiers)

*   **Tier 0 — In-Process Python:** Thực thi code Python trực tiếp trong process của ứng dụng backend. Phù hợp cho tính toán nhanh, đọc ghi file cơ bản. Quyền hạn bị giới hạn bởi quyền của user chạy backend.
*   **Tier 1 — Docker Security Toolkit:**
    *   *Môi trường:* Docker container cô lập (`nexus-security-toolkit`).
    *   *Công cụ tích hợp:* nmap, masscan, sqlmap, jadx (dịch ngược APK), apktool, nuclei, ffuf, tshark, pwntools, impacket.
    *   *Gắn kết thư mục:* Thư mục công việc `_work/` của Task được mount trực tiếp vào thư mục `/workspace` trong container. Mọi file Agent sinh ra trong Docker sẽ xuất hiện ngay lập tức ở workspace của Task.
*   **Tier 2 — Remote Device Bridge:**
    *   *Môi trường:* Kết nối thông qua ADB over network hoặc SSH tới thiết bị Android thật (đã được root bằng Magisk và cài sẵn frida-server) hoặc máy ảo.
    *   *Tính năng:* Gọi lệnh shell trên thiết bị (`device_exec`), đẩy/kéo file (`device_push`/`device_pull`), và chạy script injection thông qua Frida (`frida_run`) để bắt gói tin bypass SSL Pinning.
*   **Tier 3 — Cloud Infrastructure:**
    *   *Môi trường:* Container cô lập cài sẵn CLI của các nhà cung cấp đám mây (AWS, GCP, Alibaba Cloud).
    *   *Tính năng:* Thực thi lệnh quản trị hạ tầng (`cloud_exec`) và khởi tạo tài nguyên thử nghiệm ngắn hạn (`cloud_provision`) có thời gian sống (TTL) mặc định 1 giờ. Sau khi hết TTL, tài nguyên tự động bị tiêu hủy để tránh phát sinh chi phí.
*   **Tier 4 — Self-Evolving Tool Factory:** Cơ chế tự thiết kế công cụ tại runtime (đã mô tả tại Phần 4.2).

### 7.2. Quy trình thực thi Workflow DAG (Workflow Tools)

Hệ thống cho phép định nghĩa các workflow phức tạp dưới dạng đồ thị có hướng không chu trình (DAG) lưu trữ trong tệp tin `.workflows.json`:
*   *Thuật toán chạy:* Ứng dụng thuật toán Kahn để sắp xếp thứ tự các bước thực thi (Topological Sort).
*   *Thực thi song song:* Sử dụng hàng đợi bất đồng bộ (`asyncio.gather`) kết hợp thẻ giới hạn (Semaphore) để chạy tối đa 3 bước song song nếu chúng độc lập.
*   *Truyền biến động:* Thay thế các biến động dạng `{{var_name}}` hoặc kết quả đầu ra của bước trước dạng `{{step_name.output}}` vào tham số của bước sau.
*   *Xử lý lỗi:* Hỗ trợ 3 chế độ khi lỗi bước: `stop` (dừng toàn bộ), `continue` (bỏ qua bước lỗi, chạy tiếp), và `retry` (tự động thử lại tối đa 1 lần).

---

## PHẦN 8: KIẾN TRÚC BẢO MẬT & RAG PIPELINE CHUYÊN SÂU

### 8.1. Thiết kế an toàn SSRF Guard (`app/security/ssrf_guard.py`)

Bộ phòng thủ Server-Side Request Forgery ngăn chặn các tác nhân kết nối trái phép vào hạ tầng mạng nội bộ:
*   **DNS Pinning và DNS Rebinding Protection:** Khi tác nhân gọi công cụ truy cập web (như `fetch_url`), `SSRFGuard` không gọi URL trực tiếp. Nó thực hiện phân giải DNS thông qua hệ thống bất đồng bộ `asyncio` để lấy toàn bộ các IP ánh xạ thực tế:
    ```python
    addrs = await loop.run_in_executor(None, lambda: socket.getaddrinfo(hostname, port, ...))
    ```
    Hệ thống duyệt qua tất cả các IP phân giải được (ngăn chặn bypass qua DNS round-robin) và thực hiện đối chiếu với danh sách các dải mạng bị cấm.
*   **Mạng bị cấm (Blocked Networks):** Tích hợp cứng 21 dải mạng IPv4 và IPv6 (RFC1918, Carrier-grade NAT, Multicast, Link-local, Loopback):
    *   *IPv4:* `0.0.0.0/8`, `10.0.0.0/8`, `100.64.0.0/10`, `127.0.0.0/8`, `169.254.0.0/16` (Cloud metadata endpoint), `172.16.0.0/12`, `192.168.0.0/16`, `224.0.0.0/4`, `240.0.0.0/4`.
    *   *IPv6:* `::1/128`, `::/128`, `fc00::/7` (Unique Local), `fe80::/10` (Link-local), `ff00::/8` (Multicast).
*   **Host bị chặn cứng (`BLOCKED_HOSTS`):** Cấm truy cập các host quản trị hạ tầng nhạy cảm: `metadata.google.internal`, `metadata.goog`, `169.254.169.254` (AWS/Azure/DigitalOcean metadata), `kubernetes.default.svc`, `host.docker.internal`.
*   **Giao thức bị cấm (`BLOCKED_SCHEMES`):** Chặn các giao thức có thể rò rỉ dữ liệu hệ thống: `file`, `ftp`, `gopher`, `ldap`, `dict`, `ssh`, `telnet`, `tftp`, `jar`. Chỉ cho phép `http` và `https`.

### 8.2. Động cơ Chuỗi Tấn Công & Kích Hoạt Tò Mò (`app/security/exploitation_chain.py`)

Khi chạy các tác vụ đánh giá an toàn thông tin (security tasks), hệ thống không chỉ ghi nhận lỗ hổng riêng lẻ mà tự động mô hình hóa các chuỗi liên kết để leo thang đặc quyền:
*   **Exploitation Chain Engine:** Đăng ký các phát hiện (`SecurityFinding`) có phân loại và gán điểm CVSS v3.1. Động cơ sẽ tự động đối chiếu các phát hiện trong session với bộ luật chuỗi tấn công `CHAIN_RULES` để phát hiện khả năng leo thang:
    *   *Credential Stuffing Chain:* `user_enumeration` + `no_rate_limit` $\rightarrow$ Nâng mức độ nghiêm trọng lên **CRITICAL** (Chiến thuật: mass account takeover).
    *   *Session Hijacking Chain:* `session_fixation` + `xss` $\rightarrow$ Nâng mức độ nghiêm trọng lên **CRITICAL**.
    *   *OAuth Token Theft Chain:* `open_redirect` + `oauth_flow` $\rightarrow$ Nâng lên **CRITICAL**.
    *   *Cloud Pivot Chain:* `cloud_metadata_ssrf` + `iam_role_attached` $\rightarrow$ Nâng lên **CRITICAL** (Chiến thuật: đánh cắp IAM credentials qua SSRF để chiếm quyền AWS/Alibaba Cloud).
*   **Curiosity Driver (Tự sinh tác vụ nghiên cứu):** Khi phát hiện một lỗ hổng cụ thể, động cơ tò mò sẽ tự động kích hoạt (`CURIOSITY_TRIGGERS`) và tạo ra danh sách các task kiểm tra sâu hơn để tiêm trực tiếp vào context của Agent ở lượt sau:
    *   *Khi phát hiện SQLi:* Tự sinh các nhiệm vụ: Xác định hệ quản trị CSDL (Postgres, MySQL...) $\rightarrow$ Duyệt sơ đồ (schemas) bảng/cột $\rightarrow$ Trích xuất hashes tài khoản $\rightarrow$ Kiểm tra quyền đọc/ghi file nâng cao.
    *   *Khi phát hiện File Upload:* Tự sinh nhiệm vụ thử nghiệm các biến thể bypass extension (`.php.jpg`, `.pHp`, `.php%00.jpg`) và Content-Type.
    *   *Khi phát hiện SSRF:* Tự sinh nhiệm vụ quét cổng nội mạng (`10.x.x.x`) và thử nghiệm các giao thức smuggling (`gopher://`, `dict://`).
*   **Curiosity cho các tác vụ khác:** `CuriosityDriver` cũng tự động giám sát các dạng tác vụ Research/Development/Analysis để phát hiện dấu hiệu cẩu thả (ví dụ: Agent chỉ dùng 1 nguồn thông tin, Agent tự giả định tham số, Agent viết code thiếu Error Handling) để tiêm lệnh bắt buộc Agent phải đào sâu hoặc sửa đổi.

### 8.3. Bộ tạo Luật ứng xử và fail-closed ROE (`app/security/roe_generator.py`)

*   **Phân cấp trạng thái ủy quyền (`_authorization_posture`):**
    *   *`in_scope`:* Target nằm trong danh sách khai báo tại `pentest_scope.yaml`. Hệ thống sẽ nhúng trực tiếp thông tin tổ chức ủy quyền (`org`) và tài liệu thỏa thuận (`document`) vào đầu tệp tin `ROE.md` giúp Agent tin tưởng hoàn toàn và thực thi tấn công thật mà không từ chối (refuse).
    *   *`needs_proof`:* Target là IP/Domain public ngoài Internet nhưng không nằm trong `pentest_scope.yaml`. Hệ thống bắt buộc hiển thị yêu cầu nhập bằng chứng sở hữu hoặc quyền văn bản trước khi thực thi.
    *   *`self_attested`:* Môi trường lab cục bộ, do người dùng tự cam kết chịu trách nhiệm.
*   **Fail-Closed Downgrade (Chặn tự động leo thang bạo lực):**
    Nếu Orchestrator lập kế hoạch ở chế độ phi tấn công (như `BUILD`, `RESEARCH`, `ANALYZE`) nhưng bộ phân loại ROE lại xếp vào dạng tấn công (`pentest`), hệ thống sẽ **tự động hạ cấp** (downgrade) phân loại ROE về dạng `intel` (chỉ quan sát và ghi nhận, cấm sử dụng các công cụ khai thác/exploitation). Cơ chế fail-closed này đảm bảo tác nhân không bao giờ bị kích hoạt nhầm tính năng tấn công bạo lực.

---

## PHẦN 9: KIẾN TRÚC FRONTEND & GIAO TIẾP ĐA KÊNH

### 9.1. Giao diện Người Dùng (`/frontend`)

*   **Zustand Store:** Sử dụng Zustand 5 quản lý state thông qua các slice được chia nhỏ:
    *   `chat_slice`: Quản lý hội thoại, streaming tin nhắn.
    *   `task_slice`: Quản lý danh sách task, subtask tree, tiến trình thực thi của từng Agent.
    *   `system_slice`: Quản lý chỉ số CPU, RAM, trạng thái các provider và background metrics.
*   **Real-time Event Bus:**
    *   SSE (Server-Sent Events) truyền tải dòng sự kiện từ Backend: `planning_start`, `agent_thinking`, `discussion`, `tool_start`, `tool_done`, `evolution_insight`, `token` (stream text)...
    *   WebSocket dùng để đồng bộ trạng thái task lớn và cấu hình hệ thống.
*   **LRU File Cache:** Tối ưu hóa việc duyệt Artifacts bằng bộ nhớ đệm Least Recently Used (LRU) lưu trên RAM của trình duyệt, tự động làm mới (refresh) sau mỗi 30 giây hoặc khi có sự kiện thay đổi file từ WebSocket.

### 9.2. Giao tiếp đa kênh (Multi-Channel Message Router)

Bộ định tuyến tin nhắn (`app/channels/`) hoạt động như một HUB trung gian:
*   *Nhận tin:* Lắng nghe tin nhắn từ 9 kênh kết nối (Telegram Bot long-polling, Discord Gateway, Slack Socket Mode, IMAP Email reader...).
*   *Chuyển đổi ngữ cảnh:* Chuẩn hóa tin nhắn đầu vào thành cấu trúc `IncomingMessage` chuẩn.
*   *Xử lý & Phản hồi:* Chuyển tiếp tin nhắn tới `NexusExecutionPipeline`. Kết quả được tự động định dạng phù hợp với từng kênh (ví dụ: Slack Block Kit hoặc Telegram HTML) và trả về cho người dùng.

---

## PHẦN 10: HỌC THUYẾT PHÁT TRIỂN & PHÒNG THỦ KỸ THUẬT (ENGINEERING DOCTRINE)

Các nguyên lý vận hành kỹ thuật (Ported từ deer-flow harness review) ràng buộc quá trình phát triển hệ thống:

1.  **Teeth Test (Kiểm thử thực chiến):** Một bài kiểm tra bảo vệ tính bất biến của hệ thống (như `ledger_verifier`, `secret_scanner`, `command_guard`) chỉ thực sự có giá trị khi nó chứng minh được nó có thể thất bại: reintroduce the bug $\rightarrow$ test goes RED; restore the fix $\rightarrow$ test goes GREEN. Một test luôn pass ở happy path mà chưa từng fail chứng minh được gì.
2.  **Comment-Plane Safety Boundary (Ranh giới an toàn của tác nhân tự chủ):** Một Agent chạy tự động không có sự giám sát của con người chỉ được phép thực thi các hành động **có thể hoàn tác** (reversible surfaces). Việc sửa đổi code hay config phải chạy ở chế độ nháp/đề xuất (draft/comment/propose), tuyệt đối không được tự động merge code, release phiên bản hoặc chuyển tiền mà không có sự kiểm duyệt trực tiếp từ con người.
3.  **Confidence × Severity Gate (Bộ lọc Độ tin cậy & Độ nghiêm trọng):** Để tránh gây nhiễu và làm người vận hành bỏ qua các thông báo (warning fatigue), các Agent đánh giá (`evolution_agent`, `metacognition`, code-review) chỉ được phát tín hiệu cảnh báo ra ngoài khi và chỉ khi phát hiện đó đạt đồng thời cả hai tiêu chuẩn: **Độ tin cậy cao (high confidence) AND mức độ nghiêm trọng vượt ngưỡng (severity $\ge$ P2)**. Các phát hiện dưới ngưỡng sẽ được ghi thầm lặng vào log thay vì spam notification.
4.  **Reason about Batches (Tư duy theo lô thay vì riêng lẻ):** Khi điều phối song song các Agent độc lập, các Agent không thể nhìn thấy phát hiện của nhau trong khi thực thi. Các phát hiện riêng lẻ có thể đúng nhưng lại mâu thuẫn hoặc xung đột khi ghép lại (compose). Hệ thống bắt buộc phải kết thúc tiến trình fan-out bằng một **Synthesis Pass** (lượt tổng hợp) để rà soát xung đột, khử trùng lặp và liên kết logic tổng thể.

---

## PHẦN 11: CƠ SỞ DỮ LIỆU & SCHEMA CHI TIẾT (PostgreSQL models)

Đặc tả các lớp SQLAlchemy ORM models chính cấu thành database hệ thống (`app/models/database.py`):

```python
# 1. AgentModel — Tác nhân thông minh
class AgentModel(Base, TimestampMixin):
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    role = Column(String(255), nullable=False)                  # researcher, developer...
    status = Column(String(50), default="idle")                 # idle, active, busy, terminated
    personality = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    skills = Column(JSON, default=dict)
    tools = Column(JSON, default=list)
    config = Column(JSON, default=dict)
    llm_model = Column(String(100), default="auto")
    avatar = Column(String(50), nullable=True)                  # Emoji avatar
    expertise = Column(JSON, default=list)                      # Danh sách tag chuyên môn
    thinking_style = Column(Text, nullable=True)                # systematic, creative, analytical...
    background = Column(Text, nullable=True)                    # Kiến thức nền chuyên môn sâu
    parent_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    swarm_id = Column(UUID(as_uuid=True), nullable=True)

# 2. TaskModel — Tác vụ điều phối
class TaskModel(Base, TimestampMixin):
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), default="pending")              # pending, decomposing, planning, executing, completed, failed
    priority = Column(Integer, default=5)
    user_id = Column(String(255), nullable=True)
    session_id = Column(String(255), nullable=True)
    parent_task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)
    swarm_id = Column(UUID(as_uuid=True), nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    metadata_ = Column(JSON, default=dict)
    last_heartbeat = Column(DateTime(timezone=True), nullable=True) # Heartbeat để check treo task
    heartbeat_count = Column(Integer, default=0)

# 3. TaskEventModel — Sự kiện kỹ thuật phục vụ Replay
class TaskEventModel(Base):
    id = Column(UUID(as_uuid=True), primary_key=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(255), nullable=True)
    event_type = Column(String(100), nullable=False)            # step, tool_start, tool_done, agent_spawn
    data = Column(JSON, nullable=True)
    sequence = Column(Integer, nullable=True)                   # Đảm bảo thứ tự replay deterministic

# 4. ConversationMessageModel — Lịch sử chat FTS
class ConversationMessageModel(Base):
    id = Column(UUID(as_uuid=True), primary_key=True)
    session_id = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)                   # user, assistant, system, tool
    content = Column(Text, nullable=False)
    metadata_ = Column(JSON, nullable=True)
    is_compacted = Column(Boolean, default=False)               # Compaction nhưng không xóa để cho phép rollback/rewind
    compaction_group_id = Column(String(36), nullable=True)
    search_vector = Column(TSVECTOR, nullable=True)             # PostgreSQL tsvector phục vụ Full-Text Search
```

---

## PHẦN 12: VẬN HÀNH & XỬ LÝ SỰ CỐ (ERROR RECOVERY & O&M)

### 12.1. Ma trận xử lý lỗi tự động (Error Recovery Matrix)

| Kịch Bản Lỗi | Cơ Chế Tự Động Khắc Phục |
| :--- | :--- |
| **LLM Request Timeout** | Kích hoạt nén context gấp $\rightarrow$ Thử lại với `_MAX_TIMEOUT_RETRIES = 3` bậc thang $\rightarrow$ Nếu vẫn lỗi, chuyển sang provider kế tiếp trong Fallback Chain. |
| **Context Window Overflow** | Nén cưỡng bức (force compress) toàn bộ tin nhắn trung gian thành tóm tắt 1 dòng $\rightarrow$ Thử lại request duy nhất 1 lần. |
| **Orphan Tool Call (Mất tool result)** | Hệ thống tự động phát hiện cuộc gọi mồ côi $\rightarrow$ Tự chèn một `tool_result` ảo ghi lỗi hệ thống $\rightarrow$ Thử lại request mới để tránh kẹt trạng thái LLM. |
| **Agent Stuck (Hành động lặp)** | Stuck detector quét qua 5 patterns cảnh báo $\rightarrow$ Inject warning trực tiếp vào context của Agent $\rightarrow$ Trim lịch sử tin nhắn $\rightarrow$ Nếu lặp 100 lần liên tiếp, kích hoạt tắt nóng tác nhân (`force-stop`). |
| **Docker Sandbox Run Timeout** | Tiến trình Docker exec bị ngắt ở mức cứng 600 giây $\rightarrow$ Trả về lỗi timeout dạng văn bản $\rightarrow$ Agent tự động chuyển hướng tìm cách khác. |
| **Database Connection Fail** | Cơ chế tự động thử lại kết nối (SQLAlchemy pool ping test) $\rightarrow$ Ngắt mạch tạm thời nếu DB chết $\rightarrow$ Trả lỗi logic thân thiện về client. |

### 12.2. Triển khai & Kiểm tra sức khỏe hệ thống

*   **Kiểm tra logs hệ thống:**
    ```bash
    # Xem log dạng JSON có cấu trúc (structlog) của backend
    docker compose logs -f nexus | grep -E '"level":"error"|"level":"warning"'
    ```
*   **API Kiểm tra sức khỏe chi tiết (Doctor API):**
    ```bash
    curl http://localhost:8899/api/v1/doctor
    ```
*   **Truy vấn số liệu ngầm (Background metrics):**
    ```bash
    curl http://localhost:8899/api/v1/system/background-metrics
    ```
