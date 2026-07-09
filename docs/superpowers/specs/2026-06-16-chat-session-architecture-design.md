# Hợp nhất kiến trúc phiên làm việc /chat

**Ngày:** 2026-06-16
**Branch:** `fix/chat-session-architecture`
**Trạng thái:** Design đã duyệt — đang triển khai

## Bối cảnh & chẩn đoán

Giao diện `/chat` có nhiều lỗi liên quan nhau: reload trang kẹt "đang xử lý" hoặc
mất state, mở nhiều tab xung đột, lịch sử trò chuyện/event hiển thị sai hoặc nhấp nháy.

Audit (4 vùng song song + verify code trực tiếp) kết luận: **đây KHÔNG phải nhiều bug
rời rạc mà là một vấn đề kiến trúc.** `ChatPanel.tsx` (2737 dòng) là "nghĩa địa bản vá"
(`AUDIT-BUG-8/9/17/19`, `FIX-POLL-LEAK`, `FLICKER-FIX`, `RT08-FIX`, `P0-FIX-2`,
`P1-FIX-4`, `DEBATE-VIS-FIX`...) — mỗi bản vá chữa 1 triệu chứng rồi đẻ race mới.

### 3 root cause gốc (đã verify bằng code)

1. **Ba kênh giao event chồng nhau, KHÔNG có dedup thật.** SSE (tàn dư) + Chat WS
   (`useResilientChat`) + DB polling (`useTaskPolling`) cùng đẩy event vào store với id
   khác hệ (`ws-evt-${Date.now()}` vs `poll-${seq}`). Comment `ChatPanel.tsx:218` hứa
   "SSE active thì WS skip" nhưng `getSseActiveSession()` (executionStore.ts:215) là
   **dead code** — chỉ tồn tại trong 1 comment, không nơi nào gọi để gate. Dedup phải
   dựa cửa sổ timestamp 2s → vỡ khi DB flush trễ. ~6 watchdog/timeout đua nhau bật/tắt.

2. **Backend không có single-source-of-truth cho `is_running`.** Suy ra từ 3 nguồn:
   `_running_tasks` (memory), `_completed_tasks` (LRU 200, memory), DB heartbeat (60s).
   Restart/evict → lệch nhau → frontend kẹt. Có đường "chết thầm lặng":
   `except Exception: pass` (task_runner.py) nuốt lỗi khi persist terminal event →
   DB kẹt `executing` → frontend poll vô tận.

3. **Zustand (memory per-tab) vs localStorage (chia sẻ mọi tab) — giả định sai.**
   `persistTaskMapping`/`persistStage` (persistence.ts:26,65) key theo `sessionId` →
   2 tab cùng session ghi đè task/stage nhau. `nexus_last_session` bị mọi tab ghi đè.
   Token không persist DB → F5 mất streaming. Dedup message dùng `content+timestamp` →
   nhân đôi khi local vs DB lệch timestamp.

## Quyết định thiết kế (đã chốt với người dùng)

- **Kênh event:** DB polling là nguồn DUY NHẤT, WS chỉ làm "chuông báo".
- **Multi-tab:** sửa gốc (key theo taskId, mỗi tab tự đọc backend), KHÔNG BroadcastChannel.
- **Streaming F5:** cân nhắc trong plan (mặc định: hiện indicator "đang xử lý", không
  cố khôi phục text dở — vì token tần suất cao, không persist là hợp lý).

## Thiết kế chi tiết

### Phần 1: Hợp nhất kênh giao event

**Nguyên tắc:** DB là nguồn sự thật. Mọi event/streaming đi qua MỘT đường:
`useTaskPolling` đọc `/tasks/{id}/events?after_seq=N` theo sequence monotonic.

- **`useTaskPolling`** → kênh DUY NHẤT đẩy event vào `executionStore`/`processStore`.
  Dedup theo `(task_id, sequence)` (server-side, ổn định), bỏ dedup theo timestamp.
- **`useResilientChat` (WS)** → giáng cấp thành chuông báo: chỉ nhận tín hiệu nhẹ
  (`task_started`, `has_update`/`event`, `stream_end`, `error`) → kích hoạt polling
  fetch ngay. KHÔNG tự đẩy event vào store. Loại bỏ phần route event của
  `handleWsChatEvent` (~150 dòng).
- **SSE tàn dư** → xoá: `setSseActiveSession`/`getSseActiveSession`/`abortRef`/
  `tabSwitchAbortRef` + watchdog liên quan (SSE stuck watchdog).
- **Gỡ nghĩa địa watchdog:** chỉ giữ 1 cơ chế quyết định "task còn chạy" = hỏi backend
  `/sessions/{id}/state`. Polling tự dừng khi đọc terminal event từ DB.

### Phần 2: Backend single-source-of-truth cho is_running

`/sessions/{id}/state` trả `is_running` theo ưu tiên, DB thắng khi mâu thuẫn:
1. DB có terminal event (`task_completed`/`failed`/`cancelled`) cho task mới nhất →
   `is_running=false` (dứt khoát, bất kể memory).
2. Chưa terminal nhưng `_running_tasks` có task active → `is_running=true`.
3. Memory trống + DB status `executing` → heartbeat freshness fallback, ngưỡng ~90-120s,
   log rõ khi nghi "chết thầm".

**Vá silent-death (CRITICAL):** tách persist terminal event thành thao tác
bắt-buộc-retry riêng (không gộp broadcast WS). Lỗi → log ERROR + retry, không nuốt im.
Mọi task khi kết thúc (kể cả crash/OOM/cancel) đều ghi 1 terminal event vào `task_events`.

**Watchdog backend bù lưới:** tác vụ nền quét task DB `executing` có heartbeat quá hạn →
đánh dấu `failed` + ghi terminal event `task_failed (resumable)`.

### Phần 3: Multi-tab state & persistence

**Nguyên tắc:** localStorage chỉ là HINT khởi tạo, KHÔNG phải nguồn sự thật. Trạng thái
task luôn lấy từ backend.

- **3.1 Key theo taskId:** `persistTaskMapping`/`persistStage` đổi sang
  `{ taskId → { sessionId, stage, updatedAt } }`. taskId per-tab lấy từ `sessionStorage`.
- **3.2 `nexus_last_session` chỉ là hint cho tab mới:** URL có sessionId → URL thắng;
  chỉ đọc `nexus_last_session` khi vào `/chat` trống.
- **3.3 Dedup message ổn định:** ưu tiên `id` (DB UUID); message local mang client
  `request_id` ổn định, match qua request_id (backend đã có `_processed_request_ids`).
  Bỏ phụ thuộc timestamp trong key dedup.
- **3.4 Routing:** mỗi tab set `currentSessionId` riêng; event polling chỉ áp đúng
  sessionId tab đang xem.
- **KHÔNG làm (YAGNI):** không BroadcastChannel, không lock localStorage.

### Phần 4: Render layer

- **4.1 Key React ổn định:** `ExecutionTracePanel` bỏ `${event.id}-${idx}` →
  chỉ `event.id`. `LiveDebateStream` dùng `round.round`+`agent_name` thay `idx`.
  Quét sạch key trộn `idx`/`Date.now()`/`Math.random()`.
- **4.2 Một nguồn render:** `executionStore` là nguồn render DUY NHẤT cho event.
  F5/lịch sử luôn nạp event từ DB `/tasks/{id}/events` vào store; `metadata.execution_events`
  chỉ là fallback khi DB lỗi.
- **4.3 Khôi phục debate/process:** giữ `populateProcessStore`/`mapSseToExecEvent`,
  đảm bảo luôn chạy từ nguồn DB sau khi message load xong.
- **4.4 Streaming vs message cuối:** clear `streamingContent` TRƯỚC khi `addMessage`
  hiển thị message DB, cùng 1 nhịp. Bỏ cửa sổ 50ms double-text.

## Tiêu chí nghiệm thu

1. F5 khi task đang chạy → hiện "đang xử lý" sạch, polling tiếp tục, tới terminal thì
   hiện message; KHÔNG kẹt vĩnh viễn.
2. F5 khi task đã xong → lịch sử message + event/debate hiển thị đầy đủ, không nhấp nháy.
3. 2 tab cùng 1 session → không ghi đè task/stage của nhau; mỗi tab phản ánh đúng backend.
4. 2 tab khác session → F5 không nhảy session.
5. Không còn message nhân đôi sau reload.
6. Backend restart giữa task → frontend không kẹt "đang chạy" quá ngưỡng watchdog.
7. Task crash/OOM → luôn có terminal event trong DB → polling dừng.

## Phạm vi file dự kiến

**Frontend:** `ChatPanel.tsx`, `useResilientChat.ts`, `useTaskPolling.ts`, `useWebSocket.ts`,
`executionStore.ts`, `sessionStore.ts`, `process/persistence.ts`, `ExecutionTracePanel.tsx`,
`LiveDebateStream.tsx`, `UnifiedProcessPanel.tsx`.

**Backend:** `app/api/v1/endpoints/session_state.py`, `app/core/task_runner.py`,
`app/api/ws.py`, `app/api/v1/endpoints/chat.py`.
