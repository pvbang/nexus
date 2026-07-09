# Cancel xuyên-process + Resume task — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancel thực sự dừng task ngay cả khi chạy ở worker khác, và task bị dừng (cancel/crash) luôn hiện nút "Tiếp tục nhiệm vụ" để resume từ checkpoint.

**Architecture:** Cancel phát tín hiệu xuyên process qua Redis pub/sub (`MessageBus` có sẵn) + ghi cờ DB `cancel_requested` làm backstop bền vững. Worker sở hữu task nhận tín hiệu → `asyncio_task.cancel()` (ngắt giữa LLM/tool call) → `_execute_task` bắt `CancelledError` → finalize `cancelled` sạch sẽ. Điều kiện resumable được thống nhất giữa list và resume; crash recovery chỉ đánh dấu `suspended/crashed` để user bấm resume thủ công (tắt auto-resume hiện có).

**Tech Stack:** Python 3, FastAPI, SQLAlchemy async, Redis (redis.asyncio), pytest (asyncio_mode=auto), React + TypeScript (frontend).

## Global Constraints

- Backend tests dùng `pytest`, `asyncio_mode = "auto"` (không cần `@pytest.mark.asyncio`), `testpaths = ["tests"]`. File test mới đặt tại `tests/test_*.py`.
- Pure-function-first: logic quyết định (resumable states, guard ghi đè, parse cancel message) tách thành hàm thuần để unit-test không cần DB/Redis — theo pattern `is_task_dead()` ở `app/core/task_watchdog.py:20` và `decide_is_running()`.
- Server chạy multi-worker (`UVICORN_WORKERS=2` trong `docker-compose.yml:75`). Mọi state in-memory KHÔNG được giả định chia sẻ giữa worker.
- Verify hành vi cancel thực tế (ngắt giữa LLM/tool) chỉ chắc chắn khi chạy trong Docker với ≥2 worker — unit test phủ logic, integration/manual phủ end-to-end.
- KHÔNG chuyển sang Celery/queue. KHÔNG bật auto-resume khi startup. KHÔNG refactor orchestrator ngoài phần cần thiết.
- Mọi commit message kết thúc bằng:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**Backend — mới:**
- `app/core/cancel_signal.py` — pure helpers + hằng số kênh Redis cancel; parse/build payload.
- `tests/test_cancel_signal.py` — test cho cancel_signal + resumable states + guard ghi đè.

**Backend — sửa:**
- `app/config.py` — tách flag `enable_task_auto_resume` (mặc định False) khỏi `enable_task_resume`.
- `app/api/v1/endpoints/tasks.py:348` — viết lại endpoint `/cancel` (ghi cờ DB + cancel cục bộ + publish Redis).
- `app/core/checkpoint_persistence.py:138` — `get_resumable_tasks()` dùng danh sách states thống nhất.
- `app/core/task_runner.py:1240` + `:569` — guard chống ghi đè status `cancelled`; hàm xử lý tín hiệu cancel cục bộ.
- `app/main.py:152` + `:236` — đăng ký subscriber `nexus:cancel`; gate auto-resume sau flag mới.
- `app/orchestration/pipeline.py:1742` — `cancel_check` đọc thêm cờ DB ở ranh giới turn.

**Frontend — sửa:**
- `frontend/src/hooks/useTaskPolling.ts:60` — không đổi terminal set; thêm comment xác nhận `task_crashed` được map.
- `frontend/src/components/tasks/TaskDetail.tsx:62` — thêm badge `crashed`, `suspended`, `cancelling`.
- `frontend/src/components/chat/ChatPanel.tsx:393` — resume detection cho cả `task_crashed`; cancel hiển thị "Đang hủy…".

---

## Task 1: Pure helpers cho cancel signal + resumable states + overwrite guard

Tách toàn bộ logic quyết định thành hàm thuần, test không cần DB/Redis. Đây là nền cho các task sau.

**Files:**
- Create: `app/core/cancel_signal.py`
- Create: `tests/test_cancel_signal.py`

**Interfaces:**
- Produces:
  - `CANCEL_CHANNEL: str = "nexus:cancel"`
  - `build_cancel_payload(task_id: str, session_id: str | None) -> dict` → `{"task_id": str, "session_id": str | None}`
  - `parse_cancel_payload(payload: dict) -> tuple[str | None, str | None]` → `(task_id, session_id)`; trả `(None, None)` nếu thiếu `task_id`.
  - `RESUMABLE_STATES: tuple[str, ...] = ("executing", "planning", "decomposing", "suspended", "cancelled", "failed", "error", "crashed")`
  - `should_keep_cancelled(current_status: str, cancel_requested: bool) -> bool` → True nếu không được ghi đè status thành công khác (tức task đã/đang bị hủy).

- [ ] **Step 1: Viết test thất bại**

```python
# tests/test_cancel_signal.py
"""Tests for cross-process cancel signal helpers + resumable-state policy.

These are pure functions (no Redis / no DB) so they pin the contract that
the cancel endpoint, the pub/sub subscriber, and the resume list all share.
"""

from __future__ import annotations

from app.core.cancel_signal import (
    CANCEL_CHANNEL,
    RESUMABLE_STATES,
    build_cancel_payload,
    parse_cancel_payload,
    should_keep_cancelled,
)


def test_build_and_parse_roundtrip():
    payload = build_cancel_payload("task-123", "sess-abc")
    assert payload == {"task_id": "task-123", "session_id": "sess-abc"}
    assert parse_cancel_payload(payload) == ("task-123", "sess-abc")


def test_parse_missing_task_id_returns_none_pair():
    assert parse_cancel_payload({"session_id": "x"}) == (None, None)
    assert parse_cancel_payload({}) == (None, None)


def test_channel_constant():
    assert CANCEL_CHANNEL == "nexus:cancel"


def test_resumable_states_cover_cancel_and_crash():
    # The resume LIST and the /resume ENDPOINT must agree on these states.
    for s in ("cancelled", "failed", "error", "crashed", "suspended"):
        assert s in RESUMABLE_STATES


def test_should_keep_cancelled_when_cancel_requested():
    # Task finished its loop normally but a cancel was requested mid-flight —
    # the terminal writer must NOT overwrite to "completed".
    assert should_keep_cancelled("completed", cancel_requested=True) is True


def test_should_keep_cancelled_when_already_cancelled():
    assert should_keep_cancelled("cancelled", cancel_requested=False) is True


def test_should_not_keep_when_clean_completion():
    assert should_keep_cancelled("completed", cancel_requested=False) is False
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/test_cancel_signal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.cancel_signal'`

- [ ] **Step 3: Viết implementation**

```python
# app/core/cancel_signal.py
"""Cross-process cancel signalling helpers (pure functions).

The cancel endpoint runs on whatever Uvicorn worker the HTTP request landed
on, but the task may be executing on a different worker (UVICORN_WORKERS=2).
In-memory cancellation tokens are per-process, so the endpoint publishes a
cancel message on a Redis pub/sub channel; every worker subscribes and the
one that OWNS the task performs the real cancellation. A durable DB flag
(`metadata.cancel_requested`) is the backstop in case the pub/sub message is
missed or the token was not yet registered.

This module holds only pure data/decision helpers so they can be unit-tested
without Redis or a DB — mirroring app/core/task_watchdog.is_task_dead().
"""

from __future__ import annotations

# Redis pub/sub channel that every worker subscribes to for cancel requests.
CANCEL_CHANNEL = "nexus:cancel"

# States from which a task may be resumed from its last checkpoint. MUST stay
# in sync with the /tasks/{id}/resume endpoint guard. "executing/planning/
# decomposing" are included because a crashed/restarted server leaves rows in
# those states (the startup reconciler flips them to suspended, but the list
# tolerates the race).
RESUMABLE_STATES: tuple[str, ...] = (
    "executing",
    "planning",
    "decomposing",
    "suspended",
    "cancelled",
    "failed",
    "error",
    "crashed",
)


def build_cancel_payload(task_id: str, session_id: str | None) -> dict:
    """Build the pub/sub envelope published on CANCEL_CHANNEL."""
    return {"task_id": task_id, "session_id": session_id}


def parse_cancel_payload(payload: dict) -> tuple[str | None, str | None]:
    """Extract (task_id, session_id) from a cancel envelope.

    Returns (None, None) when task_id is absent so subscribers can ignore
    malformed messages without raising.
    """
    task_id = payload.get("task_id")
    if not task_id:
        return None, None
    return task_id, payload.get("session_id")


def should_keep_cancelled(current_status: str, cancel_requested: bool) -> bool:
    """Decide whether a terminal-status write must preserve 'cancelled'.

    Called right before the task runner / pipeline writes a terminal status.
    A normal-looking 'completed' must NOT clobber a cancellation that was
    requested while the final turn was draining.
    """
    return current_status == "cancelled" or cancel_requested
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest tests/test_cancel_signal.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add app/core/cancel_signal.py tests/test_cancel_signal.py
git commit -m "feat(cancel): pure helpers for cross-process cancel signal + resumable states

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Thống nhất điều kiện resumable trong get_resumable_tasks()

`get_resumable_tasks()` đang lọc `["executing","suspended"]` (checkpoint_persistence.py:162) trong khi `/resume` cho phép `cancelled/failed/error/suspended/crashed`. Task vừa cancel không lọt list → nút resume không hiện. Dùng `RESUMABLE_STATES` từ Task 1.

**Files:**
- Modify: `app/core/checkpoint_persistence.py:162`
- Test: `tests/test_resumable_states.py` (Create)

**Interfaces:**
- Consumes: `RESUMABLE_STATES` từ `app.core.cancel_signal`.
- Produces: `get_resumable_tasks()` trả task ở mọi state trong `RESUMABLE_STATES` có checkpoint.

- [ ] **Step 1: Viết test thất bại**

```python
# tests/test_resumable_states.py
"""The resume LIST query must accept the same states the /resume endpoint does.

Regression: get_resumable_tasks() filtered ["executing","suspended"] only, so a
just-cancelled task (status="cancelled") never appeared and the UI's "Tiếp tục"
button never showed. We assert the query's state filter is sourced from the
shared RESUMABLE_STATES constant rather than a hard-coded subset.
"""

from __future__ import annotations

import inspect

from app.core import checkpoint_persistence
from app.core.cancel_signal import RESUMABLE_STATES


def test_query_uses_shared_resumable_states():
    # The source must reference the shared constant, not a hard-coded list that
    # can drift from the /resume endpoint. This guards the root cause directly.
    src = inspect.getsource(checkpoint_persistence.CheckpointPersistence.get_resumable_tasks)
    assert "RESUMABLE_STATES" in src
    assert '["executing", "suspended"]' not in src


def test_cancelled_and_crashed_are_resumable():
    assert "cancelled" in RESUMABLE_STATES
    assert "crashed" in RESUMABLE_STATES
    assert "failed" in RESUMABLE_STATES
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/test_resumable_states.py -v`
Expected: FAIL — `test_query_uses_shared_resumable_states` fails vì source vẫn chứa `["executing", "suspended"]`.

- [ ] **Step 3: Sửa implementation**

Trong `app/core/checkpoint_persistence.py`, sửa thân `get_resumable_tasks()` (dòng ~162). Đổi dòng:

```python
                    .where(TaskModel.status.in_(["executing", "suspended"]))
```

thành:

```python
                    .where(TaskModel.status.in_(list(RESUMABLE_STATES)))
```

Và thêm import ở đầu thân hàm (ngay sau các import `from app.models.database import ...` đã có trong hàm), để tránh import vòng ở module level:

```python
                from app.core.cancel_signal import RESUMABLE_STATES
```

Cập nhật docstring dòng 138-140 cho khớp:

```python
        """Find tasks in a resumable state (see RESUMABLE_STATES) that have checkpoints.

        Returns:
            List of dicts with: task_id, turn_number, state_json, reason, task_title.
        """
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest tests/test_resumable_states.py tests/test_cancel_signal.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/checkpoint_persistence.py tests/test_resumable_states.py
git commit -m "fix(resume): unify resumable-state filter with /resume endpoint (root cause #5)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Guard chống ghi đè status 'cancelled' trong TaskRunner

Endpoint cancel set `cancelled`, nhưng task nền khi kết thúc ghi đè `completed` (task_runner.py:1240) — không có guard. Dùng `should_keep_cancelled()` từ Task 1 để re-check DB ngay trước khi ghi terminal status.

**Files:**
- Modify: `app/core/task_runner.py` — `_update_task_db_status` (dòng 651) thêm param + guard.
- Test: `tests/test_cancel_overwrite_guard.py` (Create)

**Interfaces:**
- Consumes: `should_keep_cancelled` từ `app.core.cancel_signal`.
- Produces: `_update_task_db_status(..., respect_cancel: bool = False)` — khi `respect_cancel=True` và DB cho thấy task đã `cancelled` hoặc cờ `cancel_requested` bật, KHÔNG ghi đè sang `completed/failed`.

- [ ] **Step 1: Viết test thất bại (pure-logic level)**

```python
# tests/test_cancel_overwrite_guard.py
"""A draining 'completed' write must never clobber a user cancellation.

The runner finishes the final turn and tries to mark the task 'completed', but
the user pressed Cancel while that turn was draining. _update_task_db_status
re-reads the row and, via should_keep_cancelled, skips the overwrite.
"""

from __future__ import annotations

from app.core.cancel_signal import should_keep_cancelled


def test_completed_blocked_when_db_already_cancelled():
    assert should_keep_cancelled("cancelled", cancel_requested=False) is True


def test_completed_blocked_when_cancel_requested_flag_set():
    assert should_keep_cancelled("executing", cancel_requested=True) is True


def test_completed_allowed_on_clean_finish():
    assert should_keep_cancelled("executing", cancel_requested=False) is False
```

- [ ] **Step 2: Chạy test để xác nhận PASS ngay (logic đã có từ Task 1)**

Run: `python -m pytest tests/test_cancel_overwrite_guard.py -v`
Expected: PASS — đây là test khóa hợp đồng; phần wiring vào runner ở Step 3 không có unit test riêng (cần DB), được phủ bởi integration/manual ở Task 7.

- [ ] **Step 3: Wire guard vào `_update_task_db_status`**

Trong `app/core/task_runner.py`, sửa signature (dòng 651-655):

```python
    async def _update_task_db_status(
        self, task_id: str, status: str, *,
        error: Optional[str] = None,
        result_content: Optional[str] = None,
        respect_cancel: bool = False,
    ) -> None:
```

Bên trong block `async with _session_factory() as iso_db:` (sau dòng 679), TRƯỚC khi build `values`, thêm guard chỉ áp dụng cho status không phải cancelled:

```python
            async with _session_factory() as iso_db:
                # CANCEL-GUARD: a terminal 'completed'/'failed' write must not
                # clobber a cancellation that landed while the final turn drained.
                if respect_cancel and status not in ("cancelled",):
                    from app.core.cancel_signal import should_keep_cancelled
                    from sqlalchemy import select as _sel
                    row = await iso_db.execute(
                        _sel(TaskModel.status, TaskModel.metadata_).where(
                            TaskModel.id == uuid.UUID(task_id)
                        )
                    )
                    cur = row.first()
                    if cur is not None:
                        cur_status = cur[0] or ""
                        cur_meta = cur[1] or {}
                        if should_keep_cancelled(cur_status, bool(cur_meta.get("cancel_requested"))):
                            logger.info(
                                "task_status_overwrite_blocked",
                                task_id=task_id,
                                attempted=status,
                                kept=cur_status if cur_status == "cancelled" else "cancelled",
                            )
                            return
                values: dict[str, Any] = {"status": status}
```

Sau đó, tại điểm ghi `completed` (dòng 1240) đổi lời gọi thành:

```python
                await self._update_task_db_status(
                    running.task_id, "completed",
                    result_content=final_message[:50000],
                    respect_cancel=True,
                )
```

- [ ] **Step 4: Chạy lại test + smoke import**

Run: `python -m pytest tests/test_cancel_overwrite_guard.py -v && python -c "import app.core.task_runner"`
Expected: PASS + import không lỗi.

- [ ] **Step 5: Commit**

```bash
git add app/core/task_runner.py tests/test_cancel_overwrite_guard.py
git commit -m "fix(cancel): guard terminal status write from clobbering 'cancelled' (root cause #4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Endpoint /cancel viết lại — DB flag + cancel cục bộ + publish Redis

Hiện endpoint chỉ gọi `cancellation.cancel_task()` (set cờ in-memory ở 1 worker) — root cause #1 & #2. Viết lại để: (1) ghi cờ DB bền, (2) thử cancel cục bộ qua `TaskRunner.cancel_task`, (3) publish `nexus:cancel` cho các worker khác.

**Files:**
- Modify: `app/api/v1/endpoints/tasks.py:348-380`
- Test: `tests/test_cancel_endpoint.py` (Create)

**Interfaces:**
- Consumes: `build_cancel_payload`, `CANCEL_CHANNEL` từ `app.core.cancel_signal`; `get_message_bus`; `get_task_runner`.
- Produces: `POST /tasks/{id}/cancel` trả `{"status": "cancelling", "task_id": str, "local": bool}` khi task đang chạy; `{"status": "not_running", ...}` nếu task không ở state chạy.

- [ ] **Step 1: Viết test thất bại**

```python
# tests/test_cancel_endpoint.py
"""POST /tasks/{id}/cancel must signal across workers, not just locally.

The endpoint writes a durable cancel flag to the task row, attempts a local
cancel (works if the task runs on THIS worker), and publishes on the Redis
cancel channel so the OWNING worker (possibly a different process) stops the
task. We assert the source wires all three legs, since a unit test cannot spin
up two real Uvicorn workers.
"""

from __future__ import annotations

import inspect

from app.api.v1.endpoints import tasks as tasks_ep


def test_cancel_endpoint_wires_all_three_legs():
    src = inspect.getsource(tasks_ep.cancel_task)
    # 1. durable DB flag
    assert "cancel_requested" in src
    # 2. cross-process publish on the shared channel
    assert "CANCEL_CHANNEL" in src or "nexus:cancel" in src
    assert "get_message_bus" in src
    # 3. local fast-path through the runner (real asyncio cancel)
    assert "get_task_runner" in src
    # must NOT rely solely on the weak in-memory token function
    assert "return {\"status\": \"cancelling\"" in src or "\"cancelling\"" in src
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/test_cancel_endpoint.py -v`
Expected: FAIL — source hiện chưa có `cancel_requested`, `CANCEL_CHANNEL`, `get_message_bus`.

- [ ] **Step 3: Viết lại endpoint**

Thay thế toàn bộ thân `cancel_task` (tasks.py:348-380) bằng:

```python
@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Request cancellation of a running task (cross-process safe).

    Three legs, because the task may run on a different Uvicorn worker than the
    one serving this request:
      1. Write a durable cancel flag to the task row (backstop — the pipeline
         re-checks it at each turn boundary even if the pub/sub message is lost).
      2. Try a local cancel via TaskRunner (instant if the task is on THIS
         worker — performs the real asyncio_task.cancel()).
      3. Publish on the Redis cancel channel so the OWNING worker cancels too.

    The final 'cancelled' status is written by the owning worker when it
    finalises the task — this endpoint only requests it.
    """
    import datetime as _dt
    from app.core.cancel_signal import CANCEL_CHANNEL, build_cancel_payload

    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in ("executing", "planning", "decomposing"):
        return {
            "status": "not_running",
            "task_id": str(task_id),
            "message": f"Task is not running (status: {task.status})",
        }

    # 1. Durable cancel flag (backstop, survives worker restarts).
    meta = dict(task.metadata_ or {})
    meta["cancel_requested"] = True
    meta["cancel_requested_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    task.metadata_ = meta
    await db.commit()

    # 2. Local fast-path: cancel immediately if the task lives on this worker.
    local = False
    try:
        from app.core.task_runner import get_task_runner
        runner = get_task_runner()
        if task.session_id:
            local = await runner.cancel_task(task.session_id)
    except Exception as exc:
        logger.warning("cancel_local_failed", task_id=str(task_id), error=str(exc))

    # 3. Cross-process publish so the owning worker (if not this one) cancels.
    if not local:
        try:
            bus = await get_message_bus()
            await bus.publish(
                CANCEL_CHANNEL,
                build_cancel_payload(str(task_id), task.session_id),
            )
        except Exception as exc:
            logger.warning("cancel_publish_failed", task_id=str(task_id), error=str(exc))

    return {"status": "cancelling", "task_id": str(task_id), "local": local}
```

Thêm import `get_message_bus` ở đầu file (sau dòng 20, khu vực import top-level):

```python
from app.communication.message_bus import get_message_bus
```

Kiểm tra `logger` đã tồn tại trong file (dòng 11 `import structlog` — cần `logger = structlog.get_logger(__name__)`). Nếu chưa có biến `logger` module-level, thêm ngay sau dòng `logger = ...` hiện có; nếu file chưa khai báo, thêm:

```python
logger = structlog.get_logger(__name__)
```

- [ ] **Step 4: Chạy test + smoke import**

Run: `python -m pytest tests/test_cancel_endpoint.py -v && python -c "import app.api.v1.endpoints.tasks"`
Expected: PASS + import OK.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/tasks.py tests/test_cancel_endpoint.py
git commit -m "fix(cancel): cross-process cancel via DB flag + runner + Redis publish (root cause #1,#2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Subscriber nexus:cancel trong lifespan

Mỗi worker phải subscribe `nexus:cancel`. Handler nhận `{task_id, session_id}` → gọi `TaskRunner.cancel_task(session_id)` (worker không sở hữu task → trả False, vô hại) + set token cục bộ.

**Files:**
- Modify: `app/main.py:152-155` (sau khi `bus.start_listening()`).
- Create: `app/core/cancel_subscriber.py` — handler + hàm đăng ký (dễ test import).
- Test: `tests/test_cancel_subscriber.py` (Create)

**Interfaces:**
- Consumes: `parse_cancel_payload`, `CANCEL_CHANNEL`; `get_task_runner`; `cancellation.cancel_task`.
- Produces:
  - `async def handle_cancel_message(payload: dict) -> bool` — trả True nếu worker này đã cancel cục bộ.
  - `async def register_cancel_subscriber(bus) -> None` — subscribe `CANCEL_CHANNEL` với handler.

- [ ] **Step 1: Viết test thất bại**

```python
# tests/test_cancel_subscriber.py
"""The cancel pub/sub handler cancels only when THIS worker owns the task.

A worker that does not own the task must no-op gracefully (return False) so the
broadcast is harmless. The owning worker returns True after cancelling.
"""

from __future__ import annotations

import app.core.cancel_subscriber as cs


async def test_handler_ignores_malformed_payload():
    assert await cs.handle_cancel_message({}) is False
    assert await cs.handle_cancel_message({"session_id": "s"}) is False


async def test_handler_returns_false_when_not_owner(monkeypatch):
    class _Runner:
        async def cancel_task(self, session_id):
            return False  # this worker does not own the task

    monkeypatch.setattr(cs, "get_task_runner", lambda: _Runner())
    # token set is best-effort; cancel_task False ⇒ handler reports not-owned
    assert await cs.handle_cancel_message({"task_id": "t1", "session_id": "s1"}) is False


async def test_handler_returns_true_when_owner(monkeypatch):
    class _Runner:
        async def cancel_task(self, session_id):
            return True  # owner — really cancelled

    monkeypatch.setattr(cs, "get_task_runner", lambda: _Runner())
    assert await cs.handle_cancel_message({"task_id": "t2", "session_id": "s2"}) is True
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/test_cancel_subscriber.py -v`
Expected: FAIL — `No module named 'app.core.cancel_subscriber'`.

- [ ] **Step 3: Viết implementation**

```python
# app/core/cancel_subscriber.py
"""Per-worker subscriber for the cross-process cancel channel.

Each Uvicorn worker subscribes to CANCEL_CHANNEL. When a cancel is published,
every worker runs handle_cancel_message; the one that OWNS the task cancels it
(real asyncio cancel via TaskRunner). Non-owning workers no-op.

The MessageBus delivers the raw envelope's `payload` to handlers via the
'nexus:events'-style dispatch, but for this dedicated channel we subscribe
directly, so the handler receives the full envelope and reads its 'payload'.
"""

from __future__ import annotations

import structlog

from app.core.cancel_signal import CANCEL_CHANNEL, parse_cancel_payload
from app.core.task_runner import get_task_runner

logger = structlog.get_logger(__name__)


async def handle_cancel_message(message: dict) -> bool:
    """Handle one cancel message. Returns True if THIS worker cancelled the task.

    `message` may be either the raw payload ({"task_id","session_id"}) or a
    MessageBus envelope wrapping it under "payload"; we accept both.
    """
    payload = message.get("payload", message) if isinstance(message, dict) else {}
    task_id, session_id = parse_cancel_payload(payload or {})
    if not task_id:
        return False

    # Best-effort cooperative token (harmless if this worker lacks it).
    try:
        from app.core.cancellation import cancel_task as cancel_token_fn
        cancel_token_fn(task_id)
    except Exception:
        pass

    if not session_id:
        return False

    try:
        runner = get_task_runner()
        cancelled = await runner.cancel_task(session_id)
        if cancelled:
            logger.info("cancel_signal_applied", task_id=task_id, session_id=session_id)
        return bool(cancelled)
    except Exception as exc:
        logger.warning("cancel_signal_apply_failed", task_id=task_id, error=str(exc))
        return False


async def register_cancel_subscriber(bus) -> None:
    """Subscribe this worker to the cancel channel."""
    await bus.subscribe(CANCEL_CHANNEL, handle_cancel_message)
    logger.info("cancel_subscriber_registered", channel=CANCEL_CHANNEL)
```

- [ ] **Step 4: Wire vào lifespan**

Trong `app/main.py`, ngay sau dòng 155 (`logger.info("message_bus_listening_started")`), thêm:

```python
    # Subscribe this worker to the cross-process cancel channel so a cancel
    # request served by another worker still stops a task running here.
    try:
        from app.core.cancel_subscriber import register_cancel_subscriber
        await register_cancel_subscriber(bus)
    except Exception as _cancel_sub_err:
        logger.warning("cancel_subscriber_failed", error=str(_cancel_sub_err))
```

- [ ] **Step 5: Chạy test + smoke import**

Run: `python -m pytest tests/test_cancel_subscriber.py -v && python -c "import app.main"`
Expected: PASS + import OK.

- [ ] **Step 6: Commit**

```bash
git add app/core/cancel_subscriber.py app/main.py tests/test_cancel_subscriber.py
git commit -m "feat(cancel): per-worker Redis subscriber applies cross-process cancel

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Backstop cờ DB trong pipeline cancel_check + gate auto-resume

Hai việc nhỏ, cùng đụng config: (a) pipeline đọc thêm cờ DB `cancel_requested` ở ranh giới turn (phòng pub/sub miss / token chưa đăng ký); (b) tách flag để TẮT auto-resume khi startup (đúng ý: chỉ hiện nút thủ công), vẫn giữ checkpoint + đánh dấu suspended.

**Files:**
- Modify: `app/config.py:399` — thêm `enable_task_auto_resume`.
- Modify: `app/main.py:237` — đổi điều kiện auto-resume sang flag mới.
- Modify: `app/orchestration/pipeline.py:1742` — `_is_cancelled` đọc thêm cờ DB.
- Test: `tests/test_auto_resume_flag.py` (Create)

**Interfaces:**
- Consumes: `get_settings`.
- Produces: `Settings.enable_task_auto_resume: bool = False` — khi False, startup KHÔNG tự resume task; nút resume thủ công vẫn hoạt động.

- [ ] **Step 1: Viết test thất bại**

```python
# tests/test_auto_resume_flag.py
"""Crash recovery must be MANUAL by default: no auto-resume on startup.

We decouple auto-resume (startup re-runs tasks) from task_resume (checkpoint
persistence + the manual /resume button). The default is checkpoints ON,
auto-resume OFF — so a restarted server surfaces a "Tiếp tục" button instead of
silently re-spending tokens.
"""

from __future__ import annotations

import inspect

from app import main as app_main
from app.config import get_settings


def test_auto_resume_defaults_off_but_checkpoints_on():
    s = get_settings()
    assert s.enable_task_auto_resume is False
    assert s.enable_task_resume is True


def test_startup_gates_auto_resume_on_new_flag():
    src = inspect.getsource(app_main.lifespan)
    assert "enable_task_auto_resume" in src
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/test_auto_resume_flag.py -v`
Expected: FAIL — `enable_task_auto_resume` chưa tồn tại.

- [ ] **Step 3a: Thêm flag vào config**

Trong `app/config.py`, ngay sau khối `enable_task_resume` (dòng 399-402), thêm:

```python
    enable_task_auto_resume: bool = Field(
        default=False,
        description=(
            "Auto-resume interrupted tasks on server startup. Default False — "
            "crashed/suspended tasks instead surface a manual 'resume' button. "
            "Checkpoint persistence is controlled separately by enable_task_resume."
        ),
    )
```

- [ ] **Step 3b: Gate auto-resume trong main.py**

Trong `app/main.py` dòng 237, đổi:

```python
    if is_leader and settings.enable_task_resume:
```

thành:

```python
    if is_leader and settings.enable_task_auto_resume:
```

(Phần startup cleanup ở dòng 128 GIỮ NGUYÊN `enable_task_resume` — nó đánh dấu task dở thành `suspended` để nút resume thủ công hiện ra, đúng mong muốn.)

- [ ] **Step 3c: Backstop cờ DB trong pipeline**

Trong `app/orchestration/pipeline.py`, `_is_cancelled` (dòng 1742-1746) hiện chỉ check callback. Thêm đọc cờ DB, có cache thời gian để không query mỗi lần. Thay thế:

```python
    def _is_cancelled(self) -> bool:
        """Check cancellation state."""
        if self._cancel_check is None:
            return False
        return self._cancel_check()
```

bằng:

```python
    def _is_cancelled(self) -> bool:
        """Check cancellation state (in-memory token fast-path).

        The durable DB cancel flag is checked separately at turn boundaries via
        _is_cancelled_db() — this method stays synchronous and cheap for the
        many in-loop call sites.
        """
        if self._cancel_check is None:
            return False
        return self._cancel_check()

    async def _is_cancelled_db(self) -> bool:
        """Backstop: read the durable cancel_requested flag from the task row.

        Covers the race where the Redis cancel message was missed or the
        in-memory token was not yet registered. Called only at turn boundaries
        (already a checkpoint site), so the extra query is negligible.
        """
        try:
            from app.core.dependencies import _session_factory
            if _session_factory is None:
                return False
            from app.models.database import TaskModel
            from sqlalchemy import select as _sel
            import uuid as _uuid
            async with _session_factory() as _db:
                row = await _db.execute(
                    _sel(TaskModel.metadata_).where(
                        TaskModel.id == _uuid.UUID(self._task_id)
                    )
                )
                meta = row.scalar_one_or_none()
                return bool((meta or {}).get("cancel_requested"))
        except Exception:
            return False
```

Trong vòng lặp chính (dòng 351-356), bổ sung kiểm tra DB backstop. Thay:

```python
            while not state.is_complete:
                # Check cancellation
                if self._is_cancelled():
                    state.status = "failed"
                    state.error = "Cancelled by user"
                    break
```

bằng:

```python
            while not state.is_complete:
                # Check cancellation — in-memory token first, durable DB flag as
                # backstop (covers missed pub/sub message or unregistered token).
                if self._is_cancelled() or await self._is_cancelled_db():
                    state.status = "cancelled"
                    state.error = "Cancelled by user"
                    break
```

(Lưu ý: đổi `state.status` từ `"failed"` → `"cancelled"` để khớp ngữ nghĩa hủy.)

- [ ] **Step 4: Chạy test + smoke import**

Run: `python -m pytest tests/test_auto_resume_flag.py -v && python -c "import app.main, app.orchestration.pipeline"`
Expected: PASS + import OK.

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/main.py app/orchestration/pipeline.py tests/test_auto_resume_flag.py
git commit -m "feat(resume): manual-only crash recovery (auto-resume off) + DB cancel backstop in pipeline

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6.5: Fix bug logic — cooperative-break phải dừng ngay & emit task_cancelled

**Phát hiện trong rà soát logic (Opus 4.8) sau Task 6.** Đường "cooperative break" (backstop khi asyncio-cancel không tới được process — đúng đường Task 6 dựng lên) có 1 chuỗi bug:
- Sau khi loop `break` với `state.status="cancelled"` (pipeline.py:355), control flow KHÔNG thoát `run()` — nó chạy tiếp Phase 4 `FinalReportGenerator.generate()` (1 LLM call) + Phase 5 `_summarize_for_user()` (1 LLM call) → cancel KHÔNG dừng nhanh, tốn 2 LLM call.
- `run()` return bình thường → `orchestrator.process_user_request` (orchestrator.py:321) HARDCODE `"status": "completed"` → `task_runner` (task_runner.py:1270) emit **`task_completed`** thay vì `task_cancelled`. DB status vẫn được Task 3 guard giữ `"cancelled"`, nhưng EVENT sai → frontend mâu thuẫn.

**Đường cancel mạnh (asyncio `task.cancel()`) KHÔNG bị lỗi này** vì nó ném `asyncio.CancelledError` (là `BaseException`, không phải `Exception` — đã verify Python 3.13) → không bị `except Exception` ở pipeline.py:767 bắt → propagate thẳng tới handler `except asyncio.CancelledError` ở task_runner.py:1263 → finalize `task_cancelled` đúng & nhanh.

**Lời giải gốc rễ (DRY):** đường cooperative-break cũng phải `raise asyncio.CancelledError` thay vì `break`. Nó tự đi vào cùng handler đã kiểm chứng → dừng ngay (bỏ 2 LLM call) + emit đúng `task_cancelled`, KHÔNG phải sửa orchestrator/runner. Thêm điểm cancel-check ngay sau `execute_turn` để cancel giữa-turn cũng raise sớm.

**Files:**
- Modify: `app/orchestration/pipeline.py` — điểm cancel ở đầu loop (~line 351-357) + sau `execute_turn` (~line 384); refactor logic cancel-check thành helper async `_should_cancel()` để test được.
- Test: `tests/test_pipeline_cancel_raises.py` (Create)

**Interfaces:**
- Produces: `NexusExecutionPipeline._should_cancel() -> bool` (async) — gộp `_is_cancelled()` (token) OR `_is_cancelled_db()` (DB flag). Khi True ở turn boundary, pipeline raise `asyncio.CancelledError` (KHÔNG `break`).

- [ ] **Step 1: Viết test thất bại**

```python
# tests/test_pipeline_cancel_raises.py
"""A cooperative cancel must RAISE CancelledError, not fall through to report-gen.

Root cause (found in logic review): when the cancel token/DB-flag tripped, the
pipeline `break`-ed out of the turn loop but then ran FinalReportGenerator +
_summarize_for_user (2 LLM calls) and returned a normal dict, which the
orchestrator hard-codes to status "completed" → task_runner emits task_completed
instead of task_cancelled. Raising CancelledError routes into task_runner's
existing `except asyncio.CancelledError` handler, which finalises task_cancelled
fast. CancelledError is BaseException, so the pipeline's `except Exception` does
not swallow it.

We test the decision helper and the BaseException property the fix relies on.
"""

from __future__ import annotations

import asyncio

import pytest

from app.orchestration.pipeline import NexusExecutionPipeline


def _make_pipeline(cancel_flag: bool):
    # cancel_check is a plain sync callable per the orchestrator wiring.
    return NexusExecutionPipeline(
        llm=None,
        tool_executor=None,
        tool_schemas=[],
        event_callback=None,
        cancel_check=lambda: cancel_flag,
        task_id="00000000-0000-0000-0000-000000000000",
        db=None,
    )


async def test_should_cancel_true_when_token_set():
    p = _make_pipeline(True)
    assert await p._should_cancel() is True


async def test_should_cancel_false_when_not_cancelled():
    p = _make_pipeline(False)
    # _is_cancelled_db returns False on any error / no session factory in tests.
    assert await p._should_cancel() is False


def test_cancellederror_is_baseexception_not_exception():
    # The fix relies on CancelledError NOT being caught by `except Exception`.
    assert issubclass(asyncio.CancelledError, BaseException)
    assert not issubclass(asyncio.CancelledError, Exception)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/test_pipeline_cancel_raises.py -v`
Expected: FAIL — `_should_cancel` chưa tồn tại (AttributeError). (Test thứ 3 pass ngay, đó là contract-lock cho thuộc tính BaseException.)

- [ ] **Step 3: Thêm helper `_should_cancel` + đổi break thành raise**

Trong `app/orchestration/pipeline.py`, ngay sau `_is_cancelled_db` (kết thúc ~line 1777), thêm helper:

```python
    async def _should_cancel(self) -> bool:
        """Unified cancel decision for turn boundaries.

        Combines the in-memory token fast-path with the durable DB-flag
        backstop. When this returns True at a turn boundary the pipeline
        raises asyncio.CancelledError (NOT break) so cancellation routes into
        TaskRunner's existing CancelledError handler — which finalises a clean
        'task_cancelled' immediately, instead of falling through to final-report
        generation and a mislabelled 'task_completed'.
        """
        return self._is_cancelled() or await self._is_cancelled_db()
```

Sửa điểm cancel ở đầu loop (line 351-357). Thay:

```python
            while not state.is_complete:
                # Check cancellation — in-memory token first, durable DB flag as
                # backstop (covers missed pub/sub message or unregistered token).
                if self._is_cancelled() or await self._is_cancelled_db():
                    state.status = "cancelled"
                    state.error = "Cancelled by user"
                    break
```

bằng:

```python
            while not state.is_complete:
                # Check cancellation — in-memory token first, durable DB flag as
                # backstop (covers missed pub/sub message or unregistered token).
                # Raise (not break) so this routes into TaskRunner's existing
                # CancelledError handler → fast, correctly-labelled task_cancelled.
                # Falling through would run final-report + summarize (2 LLM calls)
                # and return a dict the orchestrator hard-codes to "completed".
                if await self._should_cancel():
                    state.status = "cancelled"
                    state.error = "Cancelled by user"
                    raise asyncio.CancelledError("Task cancelled by user")
```

Thêm điểm cancel-check ngay SAU `execute_turn` (sau line 384 `state.current_turn = current_plan.turn_number`), để cancel giữa-turn raise sớm trước khi tốn `evaluate_turn`:

```python
                # Execute turn
                turn_result = await turn_executor.execute_turn(current_plan, state)
                state.current_turn = current_plan.turn_number

                # Cancel may have landed WHILE the turn ran (long LLM/tool calls).
                # Raise now so we skip evaluate/plan and finalise task_cancelled fast.
                if await self._should_cancel():
                    state.status = "cancelled"
                    state.error = "Cancelled by user"
                    raise asyncio.CancelledError("Task cancelled by user")
```

(Lưu ý: `asyncio` đã được import ở pipeline.py:14. Checkpoint của turn vừa chạy xong đã được lưu ở cuối lần lặp trước / sẽ được lưu bởi turn_executor — resume vẫn hoạt động vì checkpoint lần cuối còn nguyên.)

- [ ] **Step 4: Chạy test + smoke import**

Run: `python -m pytest tests/test_pipeline_cancel_raises.py -v && python -c "import app.orchestration.pipeline"`
Expected: PASS (3/3) + import OK.

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/pipeline.py tests/test_pipeline_cancel_raises.py
git commit -m "fix(cancel): raise CancelledError on cooperative cancel so task_cancelled is emitted (root cause #6)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend — xử lý task_cancelled + badge mới + resume-on-crash + "Đang hủy…"

Backend giờ phát `cancelling`, đánh dấu `crashed/suspended`. Frontend cần: (a) **[ROOT CAUSE UI — phát hiện trong rà soát logic]** `handlePollingComplete` phải xử lý `task_cancelled` như nhánh riêng — hiện tại nó rơi vào `else` báo nhầm `addNotification('success', 'Task completed')` và KHÔNG hiện nút resume; (b) badge cho `crashed/suspended/cancelling`; (c) resume detection cũng kích hoạt khi nhận `task_crashed` VÀ `task_cancelled`; (d) sau khi bấm Cancel hiển thị "Đang hủy…" và chỉ tắt spinner khi nhận terminal event thật.

**Bằng chứng bug UI (ChatPanel.tsx:334-436):** `handlePollingComplete` chỉ có 2 nhánh: `if event_type === 'task_failed'` (hiện resume) `else` (báo "Task completed - Response ready"). Khi terminal event là `task_cancelled`, nó rơi vào `else` → báo thành công sai + không hiện nút "Tiếp tục". Đây là lý do gốc người dùng thấy "cancel xong tưởng hoàn thành, không có nút tiếp tục".

**Files:**
- Modify: `frontend/src/components/tasks/TaskDetail.tsx:62-74` (STATUS_CONFIG) + `:135-137` (isTaskActive).
- Modify: `frontend/src/components/chat/ChatPanel.tsx:2085` (cancelStream → "Đang hủy…").

**Interfaces:**
- Consumes: backend trả `{status:"cancelling"}`; sự kiện `task_crashed` / `task_failed` với `resumable:true`.
- Produces: UI hiển thị đúng trạng thái cuối; nút "Tiếp tục" hiện cho cancelled/failed/crashed/suspended.

- [ ] **Step 1: Thêm badge cho status mới (TaskDetail.tsx)**

Trong `STATUS_CONFIG` (dòng 67-73), thêm 3 dòng:

```typescript
  cancelled: { label: 'Cancelled', icon: '🚫', variant: 'error' },
  cancelling: { label: 'Cancelling…', icon: '⏹️', variant: 'warning' },
  suspended: { label: 'Suspended', icon: '⏸️', variant: 'warning' },
  crashed: { label: 'Crashed', icon: '💥', variant: 'error' },
```

- [ ] **Step 2: `isTaskActive` coi `cancelling` là còn hoạt động (spinner vẫn chạy tới khi terminal)**

Sửa dòng 135-137:

```typescript
function isTaskActive(status: string): boolean {
  return ['executing', 'running', 'planning', 'decomposing', 'cancelling'].includes(status)
}
```

- [ ] **Step 3: cancelStream hiển thị "Đang hủy…" thay vì kết thúc ngay (ChatPanel.tsx:2085)**

Thay thân `cancelStream` — bỏ việc set ngay `setIsProcessing(false)`/`endProcess`, thay bằng trạng thái chờ terminal:

```typescript
  const cancelStream = useCallback(() => {
    // Cancel the backend task; the real terminal state arrives via polling.
    const ps = useProcessStore.getState()
    const realTaskId = ps.taskId
    if (realTaskId && !realTaskId.startsWith('msg-')) {
      apiPost(`/tasks/${realTaskId}/cancel`, {}).catch(() => {
        // Best-effort: task may have already completed
      })
    }
    // Show a "cancelling" affordance but KEEP polling until the backend writes
    // a terminal task_cancelled event — otherwise the UI would claim the task
    // stopped while the owning worker is still draining.
    setStatusText('Đang hủy nhiệm vụ…')
  }, [])
```

(Việc tắt spinner + append "*(cancelled)*" do `handlePollingComplete` xử lý ở Step 4 khi nhận `task_cancelled`.)

- [ ] **Step 4: `handlePollingComplete` xử lý `task_cancelled` + `task_crashed` thành nhánh riêng (ChatPanel.tsx:390-436) — ROOT CAUSE UI**

Hiện tại `handlePollingComplete` chỉ phân biệt `task_failed` vs `else (success)`. Sửa để 3 trạng thái terminal không-thành-công (`task_failed`, `task_crashed`, `task_cancelled`) đều xử lý đúng: báo đúng thông điệp + hiện nút resume (nếu resumable). Thay block điều kiện (dòng 390-436):

Thay:

```typescript
    // Handle failure
    if (finalEvent.event_type === 'task_failed') {
      const errMsg = (eventData.error as string) || 'Task failed'
      const isResumable = eventData.resumable === true
      const failedTaskId = (eventData.task_id as string) || pollingTaskId

      addMessage(sessionId, {
        id: crypto.randomUUID(),
        role: 'system',
        content: isResumable
          ? `⚠️ Task bị gián đoạn: ${errMsg}. Bạn có thể tiếp tục nhiệm vụ.`
          : `❌ Error: ${errMsg}`,
        timestamp: new Date().toISOString(),
      })
      setCanRetry(!!lastSentMessageRef.current)
      addNotification('error', isResumable ? 'Task Interrupted' : 'Task Failed', errMsg)

      // Show resume button if task is resumable
      if (isResumable && failedTaskId) {
        setCanResume(true)
        setResumeInfo({ turn: 0, taskId: failedTaskId })
        // Fetch actual turn info from checkpoint
        fetch(`/api/v1/tasks/resumable?session_id=${sessionId}`)
          .then(r => r.json())
          .then(data => {
            const match = data.tasks?.find((t: any) => t.task_id === failedTaskId || t.session_id === sessionId)
            if (match) {
              setResumeInfo({ turn: match.turn_number, taskId: match.task_id })
            }
          })
          .catch(() => {})
      } else if (failedTaskId) {
        // Even without resumable flag, check if checkpoint exists
        fetch(`/api/v1/tasks/resumable?session_id=${sessionId}`)
          .then(r => r.json())
          .then(data => {
            const match = data.tasks?.find((t: any) => t.task_id === failedTaskId || t.session_id === sessionId)
            if (match) {
              setCanResume(true)
              setResumeInfo({ turn: match.turn_number, taskId: match.task_id })
            }
          })
          .catch(() => {})
      }
    } else {
      addNotification('success', 'Task completed', 'Response ready')
    }
```

bằng:

```typescript
    // Handle terminal states. A task that did NOT complete successfully
    // (failed / crashed / cancelled) gets an explanatory message and, if a
    // checkpoint exists, a resume affordance. Previously task_cancelled fell
    // into the `else` branch and was wrongly reported as "Task completed".
    const evType = finalEvent.event_type
    const isCancelled = evType === 'task_cancelled'
    const isFailedOrCrashed = evType === 'task_failed' || evType === 'task_crashed'

    if (isCancelled || isFailedOrCrashed) {
      const errMsg = (eventData.error as string) || (isCancelled ? 'Đã hủy nhiệm vụ' : 'Task failed')
      // Cancelled tasks keep a checkpoint, so they are resumable even without
      // an explicit flag; failed/crashed rely on the backend `resumable` flag.
      const isResumable = isCancelled || eventData.resumable === true
      const endedTaskId = (eventData.task_id as string) || pollingTaskId

      addMessage(sessionId, {
        id: crypto.randomUUID(),
        role: 'system',
        content: isCancelled
          ? `⏹️ Đã hủy nhiệm vụ. Bạn có thể tiếp tục từ checkpoint gần nhất.`
          : (isResumable
              ? `⚠️ Task bị gián đoạn: ${errMsg}. Bạn có thể tiếp tục nhiệm vụ.`
              : `❌ Error: ${errMsg}`),
        timestamp: new Date().toISOString(),
      })
      if (isFailedOrCrashed) setCanRetry(!!lastSentMessageRef.current)
      addNotification(
        isCancelled ? 'info' : 'error',
        isCancelled ? 'Task Cancelled' : (isResumable ? 'Task Interrupted' : 'Task Failed'),
        errMsg,
      )

      // Show resume button if a checkpoint exists for this session/task.
      if (endedTaskId) {
        // Optimistically enable for resumable terminal states; confirm + get the
        // real turn number from the resumable list.
        if (isResumable) {
          setCanResume(true)
          setResumeInfo({ turn: 0, taskId: endedTaskId })
        }
        fetch(`/api/v1/tasks/resumable?session_id=${sessionId}`)
          .then(r => r.json())
          .then(data => {
            const match = data.tasks?.find((t: any) => t.task_id === endedTaskId || t.session_id === sessionId)
            if (match) {
              setCanResume(true)
              setResumeInfo({ turn: match.turn_number, taskId: match.task_id })
            }
          })
          .catch(() => {})
      }
    } else {
      addNotification('success', 'Task completed', 'Response ready')
    }
```

LƯU Ý quan trọng: ở dòng 352, `useProcessStore.getState().endProcess(finalEvent.event_type === 'task_completed')` — giữ nguyên, nó đã đúng (chỉ `task_completed` mới là success). Việc thêm nhánh cancelled KHÔNG đụng dòng này.

LƯU Ý về `task_crashed`: event này CHỈ phát qua message-bus (`task_watchdog._notify_crashed_tasks`), KHÔNG ghi vào bảng `task_events`. Watchdog ghi vào DB một event type **`task_failed`** (kèm `resumable: true`, `reason: "stale_heartbeat"` — xem `build_stale_terminal_event`). Vì polling đọc `task_events`, nó luôn thấy `task_failed` cho ca crash, KHÔNG bao giờ thấy `task_crashed`. Do đó:
- Việc include `task_crashed` trong điều kiện `isFailedOrCrashed` là phòng thủ vô hại (không fire từ polling) — chấp nhận được, giữ cho rõ ý định.
- TUYỆT ĐỐI KHÔNG thêm `task_crashed` vào `TERMINAL_EVENT_TYPES` trong `useTaskPolling.ts` — nó không phải event được persist, thêm vào là thừa và gây hiểu nhầm. `TERMINAL_EVENT_TYPES` giữ nguyên `{task_completed, task_failed, task_cancelled}`.

`addNotification` ở ChatPanel là `useNotificationStore(s => s.add)` với signature `add(type: NotificationType, title, message?, duration?)`, và `NotificationType = 'success' | 'error' | 'info' | 'warning'` (`frontend/src/stores/notificationStore.ts:7`). Vì vậy `'info'` cho cancelled là HỢP LỆ — dùng trực tiếp như code ở trên.

- [ ] **Step 5: Verify frontend build (typecheck)**

Run: `cd frontend && npm run build` (hoặc `npx tsc --noEmit` nếu có script typecheck riêng)
Expected: Build/typecheck thành công, không lỗi type.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/tasks/TaskDetail.tsx frontend/src/components/chat/ChatPanel.tsx
git commit -m "feat(ui): cancelling/crashed/suspended badges + resume-on-crash + 'Đang hủy' state

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Integration verify (Docker, 2 worker) + regression suite

Xác nhận end-to-end theo tiêu chí thành công của spec. Hành vi cancel xuyên-process chỉ chắc chắn khi chạy thật với 2 worker.

**Files:** (không tạo code mới — chạy verify)

- [ ] **Step 1: Chạy toàn bộ test backend mới + liên quan**

Run: `python -m pytest tests/test_cancel_signal.py tests/test_resumable_states.py tests/test_cancel_overwrite_guard.py tests/test_cancel_endpoint.py tests/test_cancel_subscriber.py tests/test_auto_resume_flag.py -v`
Expected: Tất cả PASS.

- [ ] **Step 2: Smoke import toàn bộ module đã đụng**

Run: `python -c "import app.main, app.api.v1.endpoints.tasks, app.core.task_runner, app.core.checkpoint_persistence, app.orchestration.pipeline, app.core.cancel_subscriber, app.core.cancel_signal"`
Expected: Không lỗi import.

- [ ] **Step 3: Khởi động Docker (2 worker) và verify cancel xuyên-process**

Run: `docker compose up -d --build` rồi theo dõi log.
Kịch bản thủ công:
1. Gửi 1 task dài (nhiều turn / có tool call dài).
2. Khi đang ở giữa LLM/tool call, bấm Cancel ở UI.
- Expected: Task dừng trong **vài giây** (không phải chờ hết turn); status về `cancelled`; spinner đổi "Đang hủy…" rồi tắt khi nhận `task_cancelled`. Lặp lại vài lần để chắc chắn không phụ thuộc task rơi vào worker nào.

- [ ] **Step 4: Verify status không bị ghi đè**

Sau khi cancel ở Step 3, kiểm tra DB/endpoint `GET /tasks/{id}`.
- Expected: status `cancelled`, KHÔNG bị flip sang `completed`. Log có thể thấy `task_status_overwrite_blocked` nếu race xảy ra.

- [ ] **Step 5: Verify resume sau cancel**

Sau khi task `cancelled`, kiểm tra UI hiện nút "Tiếp tục nhiệm vụ"; bấm resume.
- Expected: `GET /tasks/resumable` trả task đó; task chạy tiếp từ turn checkpoint (log `task_resumed`, `resumed_from_turn > 0`), không làm lại từ đầu.

- [ ] **Step 6: Verify crash recovery**

Mô phỏng crash: trong lúc task chạy, `docker compose restart app` (hoặc kill worker).
- Expected: Sau restart, task được đánh dấu `suspended`/`crashed`; UI hiện nút "Tiếp tục" (KHÔNG tự động resume vì `enable_task_auto_resume=False`); bấm resume → chạy tiếp từ checkpoint.

- [ ] **Step 7: Commit (nếu có chỉnh sửa nhỏ phát sinh từ verify)**

```bash
git add -A
git commit -m "test(cancel-resume): integration verification notes + fixes from 2-worker run

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Root cause #1 (multi-worker) → Task 4 (publish) + Task 5 (subscriber). ✅
- Root cause #2 (endpoint gọi hàm yếu) → Task 4 (gọi `TaskRunner.cancel_task` + publish). ✅
- Root cause #3 (poll thưa) → giải bằng `asyncio_task.cancel()` (đã có ở `TaskRunner.cancel_task:596`, kích hoạt qua Task 4/5) + backstop DB ở turn boundary (Task 6). ✅
- Root cause #4 (ghi đè status) → Task 3. ✅
- Root cause #5 (resumable lệch) → Task 2. ✅
- Spec §5.B1 (bắt CancelledError) → đã tồn tại ở `task_runner.py:1263`; verify ở Task 8. ✅
- Spec §5.C2 (startup reconciliation → crashed/suspended) → đã có ở `main.py:128` (suspended) + watchdog `task_watchdog.py` (crashed); Task 6 chỉ tắt auto-resume. ✅
- Spec §5.D (frontend) → Task 7. ✅
- Crash recovery manual-only → Task 6 (flag mới mặc định off). ✅

**Placeholder scan:** Không có TBD/TODO; mọi step có code/command cụ thể. ✅

**Type consistency:** `RESUMABLE_STATES`, `CANCEL_CHANNEL`, `build_cancel_payload`, `parse_cancel_payload`, `should_keep_cancelled` dùng nhất quán giữa Task 1↔2↔3↔4↔5. `handle_cancel_message`/`register_cancel_subscriber` khớp Task 5↔lifespan. `respect_cancel` param khớp Task 3. ✅
