# Token Governor — Design Spec

> **Trạng thái:** Đã chốt qua brainstorming (2026-06-23). Bước tiếp theo: writing-plans.
> **Nguồn cảm hứng:** deer-flow `TokenBudgetMiddleware` + `LoopDetectionMiddleware` + tiktoken counting.
> **Phạm vi:** A (token-budget warn/hard-stop) + B (đếm token chính xác) + C (loop-detection trong react-loop).

---

## 1. Bối cảnh & vấn đề

Khảo sát so sánh `src/open-source/deer-flow` với dự án hiện tại cho thấy dự án ĐÃ có context-handling rất mạnh cho task dài hơi (proactive compression ở agent react-loop `agent_worker._compress_context_if_needed`, ContextCompressor hermes-style `context_manager.py`, LEDGER/EPOCHS, Goal Anchor, termination ladder, checkpoint+resume, RAG over history). Tuy nhiên có **3 khoảng trống thật** so với deer-flow:

| # | deer-flow có | Dự án hiện tại | Gap |
|---|---|---|---|
| **A** | `TokenBudgetMiddleware`: theo dõi token TÍCH LŨY (gồm subagent), 2 ngưỡng — warn (~80%: nhắc wrap-up) + hard-stop (~95%: tước tool_calls, ép final answer) | Chỉ NÉN để chạy tiếp; không báo agent "hãy kết luận", không ép kết luận khi sắp cháy trần công sức. Chỉ dừng ở orchestrator-level theo turn/stagnation | Không có trần "công sức tích lũy" có feedback |
| **B** | tiktoken counting (fallback char-based CJK-aware) | `chars//4` ở mọi nơi (`estimate_tokens`, `_estimate_tokens`, ngưỡng nén). Có biên an toàn 15% nhưng vẫn phỏng đoán | Ước lượng token kém chính xác |
| **C** | Loop detection ở mức react-loop: hash tool-call + frequency, chèn cảnh báo cho chính agent | Loop-detect ở mức ASSIGNMENT (orchestrator, `nexus_orchestrator.py:1633`); bên trong 1 agent react-loop chỉ giới hạn số vòng (`max_rounds`) — agent có thể gọi lặp cùng tool/args mà không tự biết | Thiếu loop-detect bên-trong-1-agent |

### Phân biệt ngữ nghĩa cốt lõi (tránh xây trùng)

- **Nén context (ĐÃ CÓ):** "cửa sổ context hiện tại đầy chưa?" → ảnh chụp tại 1 thời điểm; nén xong thì GIẢM xuống dưới ngưỡng. Lo việc nhồi-vừa-context-window.
- **Token budget (A — MỚI):** "agent/task này đã tiêu TỔNG CỘNG bao nhiêu token rồi?" → đo độ dài/chi phí của cả quá trình; tăng ĐƠN ĐIỆU, nén KHÔNG làm giảm. Lo việc chặn run lê thê & tốn kém.

Hai trục khác nhau → A **không** được nhét vào class compression (Approach 3 đã loại). Đây là quyết định nền tảng của spec.

---

## 2. Quyết định đã chốt (qua brainstorming)

| Câu hỏi | Quyết định |
|---|---|
| A áp ở tầng nào | **Cả hai tầng**: per-agent (trong react-loop) + per-task (orchestrator) |
| Ngữ nghĩa A | **Trần công sức tích lũy** (tổng token đã tiêu, nuôi bằng usage thật) |
| Chiến lược B | **tiktoken + calibrate**: tiktoken baseline, nhân hệ số hiệu chỉnh per-model học từ `prompt_tokens` thật |
| Hành động C | **Warn (3x) rồi hard-stop (5x)** |
| Per-task hard-stop kết task thế nào | **1 turn synthesis rồi terminate trung thực** qua termination ladder (reason `token_budget_exhausted`) |
| Tổ chức kiến trúc | **Approach 1**: một module thuần `app/core/token_governor.py` |

---

## 3. Kiến trúc

Module thuần mới: `app/core/token_governor.py`. **Không tự hành động** (không chèn message, không tước tool, không terminate) — chỉ *đo* và *trả phán quyết*. Call-site (agent_worker / nexus_orchestrator) thực hiện hành động vì hành động khác nhau ở mỗi tầng.

```
app/core/token_governor.py
├── TokenEstimator      (B) — ước lượng token TRƯỚC khi gọi LLM
│     • tiktoken baseline (import OPTIONAL, warmup off-event-loop, cooldown 600s, fallback CJK-aware)
│     • bảng correction[model] (EMA) học từ usage thật
│     • estimate(messages, model) -> int
│     • observe(model, est_tokens, real_prompt_tokens) -> None   # cập nhật hệ số
│
├── BudgetTracker       (A) — trần CÔNG SỨC TÍCH LŨY (đơn điệu tăng)
│     • cộng dồn total_tokens (nuôi bằng usage THẬT, không phải ước lượng)
│     • ngưỡng warn=0.80, hard_stop=0.95 trên `limit` cấu hình được
│     • add(usage: dict) -> None
│     • status() -> BudgetStatus{level: ok|warn|hard_stop, used, limit, pct}
│     • dùng được ở CẢ per-agent (instance riêng) và per-task (instance tổng hợp)
│
└── LoopMonitor         (C) — phát hiện lặp tool-call trong 1 react-loop
      • hash(tool_name + args chuẩn hoá) + đếm tần suất
      • warn=3 (cùng hash), hard_stop=5
      • record(tool_name, args) -> LoopStatus{level: ok|warn|hard_stop, key, count}
```

### Ranh giới phụ thuộc
- `token_governor.py` import: chỉ `app.core.model_context` (lấy context limit) + `tiktoken` (optional). **Không** import `app.orchestration.*` → tránh circular.
- Là module thuần, fail-open: mọi lỗi đo lường được nuốt và trả trạng thái an toàn (ok / ước lượng fallback) — đo lường KHÔNG BAO GIỜ làm hỏng task.

---

## 4. Component B — TokenEstimator

**Mục tiêu:** cung cấp ước-lượng-trước token chính xác (tiktoken + calibrate per-model, đúng cho Claude/Gemini qua Bedrock chứ không chỉ OpenAI), dùng để (1) tự hiệu chỉnh hệ số per-model từ usage thật và (2) nuôi giá trị `_last_estimate` cho calibration. **Lưu ý (đã chốt khi triển khai):** trigger nén `_compress_context_if_needed` GIỮ NGUYÊN heuristic char hiện có — estimator KHÔNG thay `chars//4` ở quyết định nén (giữ hành vi nén production bất biến, an toàn hơn); estimator chỉ ghi `_last_estimate` để calibrate. Việc thay `chars//4` ở trigger nén là quyết định thiết kế riêng, cần brainstorm trước nếu muốn.

- **Baseline tiktoken:** `import tiktoken` trong try/except. Thiếu lib → `_available=False`, dùng fallback char-based CJK-aware (non-ASCII ratio → 2.0/2.5/3.0/4.0 chars-per-token, tái dùng heuristic `_estimate_chars_per_token` đã có trong `agent_worker.py:93`). Warmup encoding chạy off-event-loop lúc khởi tạo (giống `model_context`), lỗi transient → cooldown 600s rồi thử lại.
- **Calibrate per-model:** giữ `_correction: dict[str, float]` (mặc định 1.0). Sau mỗi call: `observe(model, est, real_prompt_tokens)` cập nhật `_correction[model]` bằng EMA của tỉ lệ `real/est` (clamp [0.5, 2.0] chống nhiễu). `estimate()` trả `baseline * _correction[model]`.
- **Nguồn usage thật:** `result["usage"]["prompt_tokens"]` — đã có sẵn, `agent_worker.py:687` đang record vào TokenJournal. Chỉ thêm 1 lời gọi `observe()` cạnh đó.

## 5. Component A — BudgetTracker (2 tầng)

**Ngữ nghĩa:** trần CÔNG SỨC TÍCH LŨY. `add(usage)` cộng dồn `total_tokens` thật; `status()` trả level theo `pct = used/limit`.

### 5.1 Per-agent (trong `agent_worker.py`)
- Mỗi worker giữ 1 `BudgetTracker(limit = settings.agent_token_budget)`. Nuôi bằng usage thật ngay tại `agent_worker.py:687` (cạnh `TokenJournal.record`).
- Đầu mỗi vòng react-loop, đọc `status()`:
  - `warn` → chèn 1 message (một lần duy nhất) nhắc: *"Bạn đã dùng ~80% token budget. Ưu tiên kết luận, hạn chế tool call tốn kém."*
  - `hard_stop` → tái dùng ĐÚNG cơ chế wrap-up đã có (`agent_worker.py:504-524`): chèn message ép kết luận + 1 LLM call cuối **không** tools → final answer. Reason log: `agent_token_budget_exhausted`.
- Bổ sung cho `max_rounds` (dừng theo SỐ VÒNG): A dừng theo TOKEN THẬT — bắt được ca ít vòng nhưng mỗi vòng đọc file khổng lồ.

### 5.2 Per-task (trong `nexus_orchestrator.py`)
- Orchestrator giữ 1 `BudgetTracker(limit = settings.task_token_budget)` tổng-task. **Lưu ý tích hợp:** hiện `TokenJournal` được tạo PER-AGENT (`agent_worker.py:208`), KHÔNG có sink tổng-task. → mỗi turn, gom `total_tokens` từ usage các agent vào budget tổng-task (qua TurnResult/agent usage).
  - `warn` → bơm directive vào eval-context: *"Task đã dùng ~80% token budget — ưu tiên kết luận, không mở thêm hướng mới."*
  - `hard_stop` → cho **đúng 1 turn synthesis** (tổng hợp kết quả đã có thành deliverable), rồi terminate qua `termination.evaluate_completion(...)` với `has_blocked_justification=True` và reason mới `token_budget_exhausted`. Báo **% hoàn thành THẬT**, KHÔNG giả completion. KHÔNG tạo đường dừng song song — đi qua đúng completion gate hiện có.

## 6. Component C — LoopMonitor

**Mục tiêu:** lấp khoảng trống loop-detect BÊN TRONG 1 agent (orchestrator đã có loop-detect ở mức assignment).

- Mỗi worker giữ 1 `LoopMonitor`. Trước khi dispatch mỗi tool call: `record(tool_name, args)`.
- Chuẩn hoá args để hash ổn định (theo tinh thần deer-flow `_stable_tool_key`): vd `read_file` bucket theo path (bỏ qua line-range), tool có `url`/`target`/`path`/`query` → lấy field định danh. Args quá lớn → hash phần đầu.
- `warn` (3x cùng hash) → chèn message: *"Bạn vừa gọi `<tool>` với tham số gần như y hệt 3 lần. Đổi cách tiếp cận hoặc kết luận với dữ liệu đã có."*
- `hard_stop` (5x) → tước tool, ép kết luận (cùng cơ chế wrap-up §5.1). Reason log: `agent_loop_detected`.
- Cảnh báo chèn dạng message SAU khi tool round xong (deferred — không phá cặp tool_call/result).

## 7. Cấu hình (externalize — theo pattern M3)

Thêm vào `app/config.py` (Settings), theo pattern `Field(...)` đã dùng:

| Setting | Default | Ý nghĩa |
|---|---|---|
| `agent_token_budget` | 400_000 | Trần token tích lũy / 1 agent react-loop |
| `task_token_budget` | 2_000_000 | Trần token tích lũy / cả task |
| `token_budget_warn_ratio` | 0.80 | Ngưỡng warn |
| `token_budget_hard_stop_ratio` | 0.95 | Ngưỡng hard-stop |
| `loop_monitor_warn_count` | 3 | Lặp tới đây → warn |
| `loop_monitor_hard_stop_count` | 5 | Lặp tới đây → hard-stop |
| `token_estimator_use_tiktoken` | true | Tắt được nếu môi trường không có tiktoken |

Default budget chọn rộng (không phải limit hành chính) — chỉ chặn runaway/cháy chi phí, không cản task dài hợp lệ. Y như tinh thần `_MAX_TURNS=500`.

## 8. Test & an toàn

**Unit (thuần, không LLM)** — `tests/test_token_governor.py`:
- TokenEstimator: calibrate hội tụ về tỉ lệ thật sau N lần observe; fallback đúng khi `_available=False`; clamp hệ số.
- BudgetTracker: ok→warn→hard_stop đúng ngưỡng; cộng dồn đơn điệu; nén KHÔNG làm tụt budget.
- LoopMonitor: đếm đúng theo hash chuẩn hoá; tool khác nhau không cộng nhầm; warn trước hard_stop.

**Tích hợp:**
- Agent hard-stop → ra final answer không tools.
- Per-task hard-stop → đi qua `evaluate_completion` thành honest-halt reason `token_budget_exhausted`, progress THẬT.
- Thiếu tiktoken → estimator fallback, ngưỡng nén cũ vẫn chạy.

**Nguyên tắc bất biến:**
- Governor **fail-open**: lỗi đo lường nuốt sạch, không bao giờ raise vào agent/orchestrator loop (giống `try/except` quanh `TokenJournal.record`).
- Không trộn ngữ nghĩa "nén" và "budget tích lũy".
- Per-task terminate CHỈ qua termination ladder hiện có — không có đường dừng thứ hai.
- trigger nén `_compress_context_if_needed` GIỮ NGUYÊN heuristic char — estimator chỉ ghi `_last_estimate` để calibrate, KHÔNG thay `chars//4` ở quyết định nén (hành vi nén production bất biến).

## 9. File map (dự kiến — plan sẽ chốt dòng cụ thể)

| File | Thay đổi |
|---|---|
| `app/core/token_governor.py` | **MỚI** — TokenEstimator + BudgetTracker + LoopMonitor |
| `app/orchestration/agent_worker.py` | Giữ BudgetTracker + LoopMonitor per-agent; gọi `observe()` cạnh dòng 687; check status đầu vòng; LoopMonitor trước dispatch tool; nâng `_compress_context_if_needed` dùng estimator |
| `app/orchestration/nexus_orchestrator.py` | BudgetTracker tổng-task; gom usage mỗi turn; bơm directive warn vào eval; hard-stop → 1 turn synthesis → terminate |
| `app/orchestration/termination.py` | Thêm reason `token_budget_exhausted` như honest-halt hợp lệ |
| `app/config.py` | 7 setting §7 |
| `tests/test_token_governor.py` | **MỚI** — unit test 3 class |

> **Gotcha (từ memory dự án):** `tests/` bị gitignore ở repo này — vẫn viết test & chạy local, chỉ là không commit. Cẩn thận các phiên Claude song song đổi branch.
