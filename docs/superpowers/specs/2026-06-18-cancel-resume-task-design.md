# Thiết kế: Cancel xuyên-process + Resume task bền vững

- **Ngày:** 2026-06-18
- **Trạng thái:** Đã duyệt thiết kế, chờ lập plan triển khai
- **Phạm vi:** `app/` (backend) + `frontend/` (UI cancel/resume)

## 1. Vấn đề

Người dùng báo hai triệu chứng:

1. Bấm **Cancel** không thực sự dừng được task đang chạy.
2. Không thấy **nút tiếp tục (resume)** task sau khi bị dừng (do server lỗi hoặc do user tự cancel).

Điều tra cho thấy **hạ tầng đã có sẵn gần đủ** (CancellationToken, CheckpointPersistence, endpoint `/cancel`, `/resume`, `/resumable`, watchdog, nút Cancel/Resume ở frontend), nhưng có các khoảng cách khiến chúng không hoạt động đúng trong thực tế.

## 2. Gốc rễ (đã xác nhận trong code)

| # | Gốc rễ | Bằng chứng |
|---|--------|-----------|
| 1 | Server chạy **2 worker process**; registry cờ cancel là dict **in-memory không chia sẻ** giữa process. Cancel rơi vào worker A nhưng task chạy ở worker B → `cancel_task()` trả `False`, task chạy tiếp. | `docker-compose.yml:75` (`UVICORN_WORKERS=2`), `app/core/cancellation.py:74` (`_active_tokens`) |
| 2 | Endpoint `/cancel` gọi **hàm yếu** `cancellation.cancel_task()` (chỉ set cờ Event), **không** gọi `TaskRunner.cancel_task()` — hàm xịn đã có sẵn, thực sự `asyncio_task.cancel()` + finalize terminal event. | `app/api/v1/endpoints/tasks.py:368` vs `app/core/task_runner.py:569` |
| 3 | Poll cờ cancel **quá thưa** (chỉ ở đầu turn/round). LLM call / tool call có thể chạy 30–120s mà không ai poll. | `app/orchestration/turn_executor.py:205,368`, `app/orchestration/agent_worker.py:533` |
| 4 | Status `cancelled` có thể bị **ghi đè** thành `completed` khi task nền kết thúc (không có guard). | `app/core/task_runner.py:1240` |
| 5 | Điều kiện **resumable lệch nhau**: `get_resumable_tasks()` lọc `["executing","suspended"]`, trong khi `/resume` cho phép `cancelled/failed/error/suspended/crashed`. Task vừa cancel mang status `cancelled` → không bao giờ lọt danh sách resumable → nút resume không hiện. | `app/core/checkpoint_persistence.py:162` vs `app/api/v1/endpoints/tasks.py:402` |

## 3. Quyết định thiết kế (đã chốt với user)

- **Tín hiệu cancel xuyên process:** Redis pub/sub (tận dụng `MessageBus` có sẵn) **kết hợp** cờ DB bền vững làm backstop.
- **Độ ngắt nhanh:** Cứng + hợp tác — gọi `asyncio_task.cancel()` để ném `CancelledError` vào điểm await hiện tại (kể cả giữa LLM/tool call), pipeline bắt để finalize sạch sẽ.
- **Crash recovery:** Chỉ **hiện nút resume thủ công**, KHÔNG auto-resume khi startup.
- **Phạm vi sửa:** Sửa **cả backend và frontend** kỹ lưỡng.

## 4. Kiến trúc

```
User bấm Cancel
   │
   ▼
POST /tasks/{id}/cancel  (rơi vào worker bất kỳ A)
   │ 1. Ghi cờ DB bền: metadata.cancel_requested=true + cancel_requested_at (commit)
   │ 2. Thử cancel cục bộ: runner.cancel_task(session_id)
   │ 3. Publish Redis "nexus:cancel" {task_id, session_id}
   │ 4. Trả 200 {status:"cancelling"} ngay
   ▼
Mọi worker subscribe "nexus:cancel"
   ├─ Worker SỞ HỮU task → cancellation.cancel_task(set token)
   │                      + runner.cancel_task() → asyncio_task.cancel()
   │                      → CancelledError ném vào điểm await (giữa LLM/tool)
   │                      → pipeline/runner bắt → finalize status="cancelled" + task_cancelled event
   └─ Worker khác → no-op (token set vô hại)

Backstop: pipeline đọc cờ DB cancel_requested ở mỗi ranh giới turn
          (phòng khi pub/sub miss hoặc token chưa kịp đăng ký)
```

**Nguồn sự thật của trạng thái cuối cùng `cancelled`** là worker sở hữu task (qua `_finalize_terminal`), KHÔNG phải endpoint. Endpoint chỉ phát lệnh `cancelling`.

## 5. Thay đổi cụ thể

### A. Backend — Cancel xuyên process

- **A1.** Đăng ký subscriber kênh `nexus:cancel` trong lifespan của `app/main.py`. Handler nhận `{task_id, session_id}` → set token (`cancellation.cancel_task`) + gọi `runner.cancel_task(session_id)`. Worker không sở hữu task → cancel trả `False` (vô hại).
- **A2.** Viết lại endpoint `POST /tasks/{id}/cancel`:
  - Ghi cờ DB `metadata_["cancel_requested"]=True` + `cancel_requested_at` (bền, không phụ thuộc worker).
  - Gọi `runner.cancel_task(session_id)` cục bộ (bắt nhanh nếu task ở chính worker này).
  - Publish `nexus:cancel`.
  - Trả `{status: "cancelling", task_id}`.
- **A3.** Backstop cờ DB: mở rộng `cancel_check` của pipeline — ngoài `token.is_cancelled`, kiểm tra cờ DB `cancel_requested` ở **ranh giới turn** (nơi đã có checkpoint, không tăng tải DB đáng kể).

### B. Backend — Dừng nhanh + không ghi đè status

- **B1.** Đảm bảo `_execute_task` trong `TaskRunner` **bắt `asyncio.CancelledError`** và đi vào nhánh cancelled (lưu checkpoint, finalize `task_cancelled`), KHÔNG rơi vào nhánh `completed`. Verify code hiện tại; bổ sung nếu thiếu.
- **B2.** **Guard chống ghi đè**: trước khi ghi terminal status `completed/failed`, re-check — nếu task đã `cancelled` hoặc cờ `cancel_requested` đã bật → giữ nguyên `cancelled`. Sửa tại `task_runner.py` (quanh dòng 1240) và pipeline.

### C. Backend — Resume thống nhất + crash recovery

- **C1.** Sửa `get_resumable_tasks()` để bao gồm `["executing","suspended","cancelled","failed","error","crashed"]` có checkpoint — **khớp** với điều kiện `/resume` (`tasks.py:402`).
- **C2.** Startup reconciliation (lifespan): quét task ở `executing/planning/decomposing` có `last_heartbeat` cũ (stale) và **không** nằm trong memory worker nào → đánh dấu `crashed`. Nhờ đó task crash hiện ra trong danh sách resumable. Resume vẫn **thủ công** (user bấm nút).

### D. Frontend — Rà soát kỹ luồng cancel/resume

- **D1.** Sau khi bấm Cancel: xử lý trạng thái `cancelling` (spinner đổi "Đang hủy…"), polling tiếp tục **cho đến khi** nhận `task_cancelled` terminal event mới dừng hẳn và hiện nút Resume. Tránh UI báo cancel xong nhưng backend còn chạy.
- **D2.** Đảm bảo nút "Tiếp tục nhiệm vụ" hiện đúng cho mọi task `cancelled/failed/crashed/suspended` có checkpoint (đồng bộ C1). Kiểm tra `/tasks/resumable` và đường detect khi F5 reload.
- **D3.** Badge trạng thái: thêm `cancelling`, `crashed` nếu thiếu.

## 6. Phạm vi KHÔNG đụng tới (YAGNI)

- Không chuyển task sang Celery/queue — giữ asyncio in-process.
- Không auto-resume khi startup.
- Không refactor god-object orchestrator ngoài phần cần thiết.

## 7. Tiêu chí thành công (verify)

1. Bấm Cancel khi task đang ở giữa LLM/tool call dài → task dừng trong **vài giây**, status về `cancelled`, spinner tắt. (Test với 2 worker.)
2. Task bị cancel/crash xuất hiện nút "Tiếp tục" → bấm → chạy tiếp từ turn checkpoint, không làm lại từ đầu.
3. Status `cancelled` KHÔNG bị ghi đè thành `completed`.
4. Server restart → task đang chạy dở thành `crashed` và resume được.

## 8. Rủi ro & lưu ý

- **Race:** Cancel ngay khi task mới khởi động (token chưa đăng ký) — backstop cờ DB ở ranh giới turn xử lý ca này.
- **CancelledError lan ngoài ý muốn:** Phải bắt đúng tầng (pipeline/runner) để lưu checkpoint trước khi finalize, tránh mất tiến độ.
- **Pub/sub miss khi Redis outage:** `MessageBus` đã có buffer replay; backstop cờ DB là lớp bảo hiểm thứ hai.
