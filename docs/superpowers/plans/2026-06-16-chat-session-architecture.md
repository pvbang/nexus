# Hợp nhất kiến trúc phiên /chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Loại bỏ sự chồng chéo 3 kênh giao event và 3 nguồn `is_running`, biến DB thành nguồn sự thật duy nhất, để giao diện /chat ổn định khi reload, đa tab, và hiển thị lịch sử/event.

**Architecture:** DB polling (`useTaskPolling`) là kênh DUY NHẤT đẩy event vào store. WS (`useResilientChat`) giáng cấp thành "chuông báo" (chỉ kích hoạt fetch, không đẩy event). Backend `/sessions/{id}/state` lấy DB terminal event làm trọng tài. Persistence key theo `taskId`. `executionStore` là nguồn render duy nhất.

**Tech Stack:** React 18 + Zustand + Vitest (frontend); FastAPI + SQLAlchemy async + pytest-asyncio (backend).

**Lệnh kiểm chứng:**
- Frontend test: `cd frontend && npm run test`
- Frontend typecheck/build: `cd frontend && npm run build`
- Frontend lint: `cd frontend && npm run lint`
- Backend test: `pytest tests/ -v`

**Quy ước message gửi (đã verify):** message gửi qua `POST /chat/send` (`sendChatMessage`, ChatPanel.tsx:2428), KHÔNG qua WS. Không được phá đường này.

---

## GIAI ĐOẠN A — BACKEND: single source of truth + vá silent-death

### Task A1: `/sessions/{id}/state` — DB terminal event là trọng tài

**Files:**
- Modify: `app/api/v1/endpoints/session_state.py` (hàm xử lý GET state, ~dòng 85-218)
- Test: `tests/api/test_session_state.py`

- [ ] **Step 1: Viết test thất bại** — task có terminal event trong DB nhưng memory vẫn báo running → `is_running` phải = false.

```python
# tests/api/test_session_state.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_db_terminal_event_overrides_memory(client: AsyncClient, seed_session_with_terminal_event):
    """DB có task_completed → is_running=false dù _running_tasks còn entry."""
    session_id = seed_session_with_terminal_event  # fixture: ghi task_completed vào task_events + để 1 RunningTask 'running' trong runner
    res = await client.get(f"/api/v1/sessions/{session_id}/state")
    assert res.status_code == 200
    body = res.json()
    assert body["is_running"] is False
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest tests/api/test_session_state.py::test_db_terminal_event_overrides_memory -v`
Expected: FAIL (hiện memory thắng → is_running=true)

- [ ] **Step 3: Sửa logic state** — thêm bước đầu tiên: query terminal event mới nhất cho task mới nhất của session; nếu có → trả `is_running=false` ngay, trước khi xét memory.

```python
# Trong handler, TRƯỚC khi đọc runner.get_active_task:
# Tìm task mới nhất của session
latest = await db.execute(
    select(TaskModel).where(TaskModel.session_id == session_id)
    .order_by(TaskModel.created_at.desc()).limit(1)
)
latest_task = latest.scalar_one_or_none()
if latest_task:
    term = await db.execute(
        select(TaskEventModel.event_type).where(
            TaskEventModel.task_id == latest_task.id,
            TaskEventModel.event_type.in_(("task_completed", "task_failed", "task_cancelled")),
        ).limit(1)
    )
    if term.scalar_one_or_none() is not None:
        return SessionStateResponse(
            session_id=session_id, is_running=False,
            task_id=latest_task.id, task_status=latest_task.status,
            # ... các field còn lại lấy từ latest_task / giá trị mặc định như code hiện có
        )
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `pytest tests/api/test_session_state.py::test_db_terminal_event_overrides_memory -v`
Expected: PASS

- [ ] **Step 5: Nâng ngưỡng stale heartbeat** — đổi `_STALE_HEARTBEAT_SECONDS = 60` → `90` (session_state.py), thêm `logger.warning` khi rơi vào nhánh heartbeat-fallback (nghi chết thầm).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/endpoints/session_state.py tests/api/test_session_state.py
git commit -m "fix(backend): DB terminal event is sole arbiter for is_running"
```

---

### Task A2: Đảm bảo terminal event LUÔN persist (vá silent-death)

**Files:**
- Modify: `app/core/task_runner.py` (khối terminal-emit ~dòng 1289-1309, `except Exception: pass`)
- Test: `tests/core/test_task_runner_terminal.py`

- [ ] **Step 1: Viết test thất bại** — khi broadcast WS lỗi, persist terminal event vào DB vẫn phải xảy ra.

```python
# tests/core/test_task_runner_terminal.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_terminal_persists_even_when_broadcast_fails(task_runner, running_task):
    with patch.object(task_runner, "_emit_event", side_effect=RuntimeError("ws down")):
        persisted = []
        async def fake_persist(session_id, event):
            persisted.append(event["event_type"])
        with patch.object(task_runner, "_persist_event", side_effect=fake_persist):
            await task_runner._finalize_terminal(running_task, "task_completed", {})
    assert "task_completed" in persisted  # persist KHÔNG bị broadcast lỗi chặn
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest tests/core/test_task_runner_terminal.py -v`
Expected: FAIL (`_finalize_terminal` chưa tồn tại / broadcast lỗi chặn persist)

- [ ] **Step 3: Tách thành `_finalize_terminal`** — persist TRƯỚC, broadcast SAU, mỗi cái try/catch riêng; persist lỗi → log ERROR + 1 lần retry, KHÔNG nuốt im.

```python
async def _finalize_terminal(self, running, event_type: str, data: dict) -> None:
    if running._terminal_emitted:
        return
    running._terminal_emitted = True
    event = {"event_type": event_type, "data": data, "task_id": running.task_id}
    # 1) PERSIST trước (chân lý cho frontend polling) — bắt buộc, có retry
    for attempt in range(2):
        try:
            await self._persist_event(running.session_id, event)
            break
        except Exception:
            self.logger.error("CRITICAL: terminal persist failed (attempt %d) task=%s",
                               attempt + 1, running.task_id, exc_info=True)
    # 2) BROADCAST sau (best-effort, lỗi không ảnh hưởng chân lý DB)
    try:
        await self._emit_event(running.session_id, event)
    except Exception:
        self.logger.warning("terminal broadcast failed task=%s", running.task_id, exc_info=True)
```

- [ ] **Step 4: Thay mọi chỗ emit terminal** trong task_runner (success/failed/cancelled/crashed paths) gọi `_finalize_terminal(...)` thay vì khối cũ.

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `pytest tests/core/test_task_runner_terminal.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/core/task_runner.py tests/core/test_task_runner_terminal.py
git commit -m "fix(backend): always persist terminal event before broadcast (no silent death)"
```

---

### Task A3: Watchdog backend quét task treo

**Files:**
- Modify: `app/core/task_runner.py` (thêm coroutine nền + đăng ký ở startup)
- Test: `tests/core/test_task_runner_watchdog.py`

- [ ] **Step 1: Viết test thất bại** — task DB `executing` với heartbeat quá hạn → watchdog đánh dấu failed + ghi terminal event.

```python
@pytest.mark.asyncio
async def test_watchdog_finalizes_stale_executing_task(task_runner, stale_executing_task_in_db):
    await task_runner._sweep_stale_tasks(max_idle_seconds=90)
    events = await fetch_task_events(stale_executing_task_in_db)
    assert any(e["event_type"] == "task_failed" for e in events)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest tests/core/test_task_runner_watchdog.py -v`
Expected: FAIL (`_sweep_stale_tasks` chưa tồn tại)

- [ ] **Step 3: Viết `_sweep_stale_tasks`** — query TaskModel status=executing, last_heartbeat older than max_idle; với mỗi cái ghi terminal `task_failed` `{resumable: true, reason: "stale_heartbeat"}` qua đường persist (dùng lại logic A2), set status=failed.

- [ ] **Step 4: Đăng ký coroutine nền** chạy mỗi 60s ở app startup (theo pattern lifespan hiện có — tìm `@asynccontextmanager` / `lifespan` trong app/main.py).

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `pytest tests/core/test_task_runner_watchdog.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/core/task_runner.py app/main.py tests/core/test_task_runner_watchdog.py
git commit -m "feat(backend): background watchdog finalizes stale executing tasks"
```

---

## GIAI ĐOẠN B — FRONTEND: hợp nhất kênh event (DB polling = nguồn duy nhất)

### Task B1: WS giáng cấp thành "chuông báo"

**Files:**
- Modify: `frontend/src/components/chat/ChatPanel.tsx` (`handleWsChatEvent` ~dòng 223-369)
- Test: `frontend/src/__tests__/chat/wsDoorbell.test.ts` (mới)

- [ ] **Step 1: Viết test thất bại** — handler WS không được gọi `addExecutionEvent`; chỉ được kích hoạt fetch (qua callback `onWake`).

```ts
// frontend/src/__tests__/chat/wsDoorbell.test.ts
import { describe, it, expect, vi } from 'vitest'
import { makeWsDoorbellHandler } from '../../components/chat/wsDoorbell'

describe('WS doorbell', () => {
  it('does not push events to store, only wakes polling', () => {
    const onWake = vi.fn()
    const addExecutionEvent = vi.fn()
    const handler = makeWsDoorbellHandler({ onWake, sessionId: 's1' })
    handler({ type: 'event', event_type: 'tool_start', data: {} })
    expect(addExecutionEvent).not.toHaveBeenCalled()
    expect(onWake).toHaveBeenCalledTimes(1)
  })
  it('wakes on task_started and signals terminal on stream_end', () => {
    const onWake = vi.fn(); const onTerminalHint = vi.fn()
    const handler = makeWsDoorbellHandler({ onWake, onTerminalHint, sessionId: 's1' })
    handler({ type: 'task_started', task_id: 't1' })
    handler({ type: 'stream_end', full_content: 'x' })
    expect(onWake).toHaveBeenCalled()
    expect(onTerminalHint).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- wsDoorbell`
Expected: FAIL (`wsDoorbell.ts` chưa tồn tại)

- [ ] **Step 3: Tạo `frontend/src/components/chat/wsDoorbell.ts`** — pure function biến WS message thành tín hiệu (wake / terminalHint / taskStarted), KHÔNG đụng store.

```ts
export interface WsDoorbellDeps {
  sessionId: string
  onWake: () => void                  // kích hoạt useTaskPolling.pollNow()
  onTaskStarted?: (taskId: string) => void
  onTerminalHint?: () => void         // gợi ý task xong → pollNow xác nhận từ DB
}
export function makeWsDoorbellHandler(deps: WsDoorbellDeps) {
  return (msg: { type: string; [k: string]: unknown }) => {
    const t = msg.type
    if (t === 'heartbeat' || t === 'pong') return
    if (t === 'task_started' || (t === 'session_event' && msg.event === 'task_started')) {
      const taskId = (msg.task_id as string) || ''
      if (taskId) deps.onTaskStarted?.(taskId)
      deps.onWake()
      return
    }
    if (t === 'stream_end' || t === 'complete' || t === 'error') {
      deps.onTerminalHint?.()
      deps.onWake()
      return
    }
    // mọi event khác (token/event/...): chỉ đánh thức polling fetch ngay
    deps.onWake()
  }
}
```

- [ ] **Step 4: Thay `handleWsChatEvent` trong ChatPanel** bằng `makeWsDoorbellHandler`. `onWake` gọi `pollNow()` (lấy từ useTaskPolling return). `onTaskStarted` set `pollingTaskId`. Xoá toàn bộ block route event (debate/token/exec) trong handler cũ.

- [ ] **Step 5: Chạy test + build**

Run: `cd frontend && npm run test -- wsDoorbell && npm run build`
Expected: test PASS, build thành công

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat/wsDoorbell.ts frontend/src/components/chat/ChatPanel.tsx frontend/src/__tests__/chat/wsDoorbell.test.ts
git commit -m "refactor(chat): WS becomes doorbell-only, polling is sole event source"
```

---

### Task B2: useTaskPolling dedup theo (task_id, sequence) + expose pollNow

**Files:**
- Modify: `frontend/src/hooks/useTaskPolling.ts`
- Test: `frontend/src/__tests__/hooks/useTaskPolling.test.ts`

- [ ] **Step 1: Viết test thất bại** — dedup phải dựa `(task_id, sequence)`, không để event task cũ (cùng seq) ghi đè task mới.

```ts
// kiểm tra hàm thuần dedupBySeqAndTask(prev, incoming)
import { dedupBySeqAndTask } from '../../hooks/useTaskPolling'
it('dedups by task_id+sequence, keeps different-task same-seq', () => {
  const prev = [{ task_id: 'A', sequence: 1 }]
  const out = dedupBySeqAndTask(prev as any, [{ task_id: 'A', sequence: 1 }, { task_id: 'B', sequence: 1 }] as any)
  expect(out.length).toBe(2) // A:1 trùng → bỏ; B:1 khác task → giữ
})
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- useTaskPolling`
Expected: FAIL (`dedupBySeqAndTask` chưa export)

- [ ] **Step 3: Tách & export `dedupBySeqAndTask`**, dùng trong `setEvents` (thay set dedup theo `sequence` đơn ở dòng 197-202). Key = `${task_id}:${sequence}`.

- [ ] **Step 4: `pollNow` đã có sẵn (dòng 307)** — đảm bảo nó được return & ChatPanel dùng làm `onWake`. Không cần đổi.

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `cd frontend && npm run test -- useTaskPolling`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useTaskPolling.ts frontend/src/__tests__/hooks/useTaskPolling.test.ts
git commit -m "fix(chat): poll dedup by (task_id, sequence) not timestamp"
```

---

### Task B3: Xoá SSE tàn dư + gỡ watchdog đua nhau

**Files:**
- Modify: `frontend/src/components/chat/ChatPanel.tsx` (các useEffect watchdog dòng ~877-985, các ref SSE)
- Modify: `frontend/src/stores/executionStore.ts` (xoá `setSseActiveSession`/`getSseActiveSession` dòng 213-215)
- Modify: `frontend/src/hooks/useResilientChat.ts` (bỏ `setWsConnectingSession` nếu chỉ phục vụ dedup SSE)

- [ ] **Step 1:** Xoá `setSseActiveSession`/`getSseActiveSession` (executionStore.ts:213-215) và mọi import/lời gọi trong ChatPanel.tsx. (Đã verify: dead code, không gate gì.)

- [ ] **Step 2:** Xoá useEffect "SSE active flag stuck watchdog" (ChatPanel.tsx:921-948) và "auto-reset stuck isProcessing" nếu trùng vai trò với terminal-from-DB. GIỮ LẠI 1 watchdog duy nhất: nếu `pollingTaskId` set mà polling im > ngưỡng → `pollNow()` (đã có trong useTaskPolling STALE_SILENCE_MS=30s). Xoá watchdog 15s backend-running và WS-drop-verify (B1 đã thay bằng doorbell wake).

- [ ] **Step 3:** Xoá các ref không còn dùng do thay đổi của mình: `tabSwitchAbortRef`, `abortRef` (nếu chỉ phục vụ SSE), `pollKilledRef` (nếu watchdog gây ra nó đã bị xoá). Chạy `npm run lint` để bắt biến mồ côi.

- [ ] **Step 4: Build + lint**

Run: `cd frontend && npm run lint && npm run build`
Expected: không lỗi unused-var, build thành công

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/ChatPanel.tsx frontend/src/stores/executionStore.ts frontend/src/hooks/useResilientChat.ts
git commit -m "refactor(chat): remove dead SSE flags and racing watchdogs"
```

---

## GIAI ĐOẠN C — MULTI-TAB STATE & PERSISTENCE

### Task C1: persistence key theo taskId

**Files:**
- Modify: `frontend/src/stores/process/persistence.ts`
- Modify: nơi gọi `persistTaskMapping`/`persistStage`/`getPersistedTaskId`/`getPersistedStage` (grep để tìm)
- Test: `frontend/src/__tests__/stores/persistence.test.ts`

- [ ] **Step 1: Viết test thất bại** — 2 task khác nhau cùng session không ghi đè stage của nhau.

```ts
import { persistTaskStage, getStageByTask } from '../../stores/process/persistence'
it('keeps per-task stage, no cross-overwrite', () => {
  persistTaskStage('taskA', 'sess1', 'executing')
  persistTaskStage('taskB', 'sess1', 'planning')
  expect(getStageByTask('taskA')).toBe('executing')
  expect(getStageByTask('taskB')).toBe('planning')
})
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- persistence`
Expected: FAIL (API mới chưa có)

- [ ] **Step 3: Đổi map sang key theo taskId** — `{ taskId: { sessionId, stage, updatedAt } }`. Thêm `persistTaskStage(taskId, sessionId, stage)`, `getStageByTask(taskId)`, `getTaskBySession(sessionId)` (trả taskId mới nhất theo updatedAt). Giữ tên cũ làm wrapper nếu cần để không vỡ chỗ gọi, nhưng ưu tiên cập nhật chỗ gọi.

- [ ] **Step 4: Cập nhật chỗ gọi** trong processStore actions để dùng taskId. taskId per-tab đọc từ `sessionStorage` (`nexus_lastTaskId_{sessionId}`), không từ localStorage chia sẻ.

- [ ] **Step 5: Chạy test + build, xác nhận PASS**

Run: `cd frontend && npm run test -- persistence && npm run build`
Expected: PASS + build OK

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/process/persistence.ts frontend/src/stores/processStore.ts frontend/src/__tests__/stores/persistence.test.ts
git commit -m "fix(chat): persist process stage by taskId to fix multi-tab overwrite"
```

---

### Task C2: nexus_last_session chỉ là hint; URL thắng

**Files:**
- Modify: `frontend/src/layouts/ChatLayout.tsx` (effect routing dòng 46-103)
- Modify: `frontend/src/stores/sessionStore.ts` (`setActiveSession` dòng 333-365)

- [ ] **Step 1:** Trong ChatLayout, khi `sessionId` (từ URL params) tồn tại → tuyệt đối dùng nó, KHÔNG đọc `nexus_last_session`. Chỉ đọc `nexus_last_session` trong nhánh "no-session" (dòng 86-103).

- [ ] **Step 2:** `setActiveSession` vẫn ghi `nexus_last_session` (cho tab mới), nhưng thêm comment rõ "hint-only, không phải nguồn sự thật". Không đổi hành vi ghi.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add frontend/src/layouts/ChatLayout.tsx frontend/src/stores/sessionStore.ts
git commit -m "fix(chat): URL sessionId wins over shared nexus_last_session hint"
```

---

### Task C3: Dedup message ổn định theo id / request_id

**Files:**
- Modify: `frontend/src/stores/sessionStore.ts` (`loadSessionMessages` dòng 401-427, `addMessage` dòng 448-478)
- Test: `frontend/src/__tests__/stores/messageDedup.test.ts`

- [ ] **Step 1: Viết test thất bại** — message local (`request_id=r1`, timestamp T1) và message DB (uuid, cùng request_id trong metadata, timestamp T2) → KHÔNG nhân đôi.

```ts
import { mergeMessages } from '../../stores/sessionStore'
it('dedups local vs DB by request_id even when timestamps differ', () => {
  const local = [{ id: 'msg-x', role: 'user', content: 'hi', timestamp: 'T1', metadata: { request_id: 'r1' } }]
  const db = [{ id: 'uuid-1', role: 'user', content: 'hi', timestamp: 'T2', metadata: { request_id: 'r1' } }]
  const merged = mergeMessages(db as any, local as any)
  expect(merged.length).toBe(1)
  expect(merged[0].id).toBe('uuid-1') // DB thắng
})
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- messageDedup`
Expected: FAIL (`mergeMessages` chưa export, dedup hiện dựa timestamp)

- [ ] **Step 3: Tách & export `mergeMessages(dbMsgs, localMsgs)`** — dedup ưu tiên: (1) id khớp, (2) `metadata.request_id` khớp, (3) fallback `role+content` (BỎ timestamp khỏi key). Dùng trong `loadSessionMessages`.

- [ ] **Step 4: Đảm bảo message local mang request_id** — ở chỗ gửi (ChatPanel sendMessage), khi `addMessage` user message local, gắn `metadata.request_id` cùng giá trị gửi lên `/chat/send`. Backend đã echo request_id vào metadata (verify ở chat.py `_processed_request_ids`); nếu chưa, thêm vào response message metadata.

- [ ] **Step 5: Chạy test + build, xác nhận PASS**

Run: `cd frontend && npm run test -- messageDedup && npm run build`
Expected: PASS + OK

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/sessionStore.ts frontend/src/components/chat/ChatPanel.tsx frontend/src/__tests__/stores/messageDedup.test.ts
git commit -m "fix(chat): stable message dedup by id/request_id, drop timestamp key"
```

---

## GIAI ĐOẠN D — RENDER LAYER

### Task D1: Key React ổn định

**Files:**
- Modify: `frontend/src/components/chat/ExecutionTracePanel.tsx` (key dòng ~787, ~531)
- Modify: `frontend/src/components/chat/LiveDebateStream.tsx` (key dòng ~337, ~448)

- [ ] **Step 1:** ExecutionTracePanel: đổi `key={`${event.id}-${idx}`}` → `key={event.id}`. Đảm bảo `event.id` luôn unique (B2 đã đảm bảo qua `poll-${task}-${seq}`).

- [ ] **Step 2:** LiveDebateStream: đổi `key={`${p.agent_name}-${idx}`}` → `key={`${round.round}-${p.agent_name}-${p.role ?? ''}`}`; round card dùng `key={`round-${round.round}`}`.

- [ ] **Step 3:** Grep toàn chat tìm key trộn `idx`/`Date.now()`/`Math.random()`: `cd frontend && npx eslint src/components/chat --rule '{"react/no-array-index-key":"warn"}'` (hoặc grep thủ công), sửa các chỗ render list event.

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/ExecutionTracePanel.tsx frontend/src/components/chat/LiveDebateStream.tsx
git commit -m "fix(chat): stable React keys for event/debate rendering"
```

---

### Task D2: executionStore là nguồn render duy nhất + clear streaming trước addMessage

**Files:**
- Modify: `frontend/src/components/chat/ChatMessageList.tsx` (dòng 51-62: bỏ render từ `message.metadata.execution_events`)
- Modify: `frontend/src/components/chat/ChatPanel.tsx` (`handlePollingComplete` dòng 487-499: clear streaming TRƯỚC addMessage)

- [ ] **Step 1:** ChatMessageList: render event chỉ qua component đọc `executionStore` (UnifiedProcessPanel/MessageEventExplorer dùng store). Để `metadata.execution_events` chỉ là fallback khi store rỗng (giữ điều kiện `store empty && metadata exists`).

- [ ] **Step 2:** handlePollingComplete: đặt `setStreamingContent('')` + `streamingContentRef.current=''` NGAY TRƯỚC `addMessage(...)` để không có cửa sổ double-text. (Hiện thứ tự ngược ở dòng 487-499.)

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/chat/ChatMessageList.tsx frontend/src/components/chat/ChatPanel.tsx
git commit -m "fix(chat): single render source (executionStore), no double-text on complete"
```

---

## GIAI ĐOẠN E — NGHIỆM THU TỔNG

### Task E1: Chạy toàn bộ test + build + lint

- [ ] **Step 1:** `cd frontend && npm run test` → tất cả PASS
- [ ] **Step 2:** `cd frontend && npm run build` → typecheck + build OK
- [ ] **Step 3:** `cd frontend && npm run lint` → không lỗi
- [ ] **Step 4:** `pytest tests/ -v` → backend test PASS
- [ ] **Step 5:** Kiểm thử thủ công 7 tiêu chí nghiệm thu trong design doc (F5 đang chạy / F5 đã xong / 2 tab cùng session / 2 tab khác session / không nhân đôi message / backend restart / task crash). Dùng skill `verify` hoặc Playwright nếu cần.

### Task E2: Cập nhật memory về kiến trúc mới

- [ ] **Step 1:** Cập nhật `[[deliverable-contract-architecture]]` không liên quan; tạo memory mới ghi quyết định kiến trúc: "DB polling = nguồn event duy nhất, WS = doorbell, DB terminal event = trọng tài is_running, persistence theo taskId". Cập nhật MEMORY.md.

---

## Self-review checklist (đã chạy)

- **Spec coverage:** Phần 1→Task B1-B3; Phần 2→Task A1-A3; Phần 3→Task C1-C3; Phần 4→Task D1-D2. ✓
- **Placeholder scan:** không có TBD/TODO; mọi code step có code thật. ✓
- **Type consistency:** `dedupBySeqAndTask`, `mergeMessages`, `makeWsDoorbellHandler`, `persistTaskStage`/`getStageByTask` dùng nhất quán giữa task định nghĩa và task tiêu thụ. ✓
- **Rủi ro cần kiểm khi thực thi:** tên field response của `/sessions/{id}/state` (SessionStateResponse) phải khớp code thật — verify khi làm A1; `_emit_event`/`_persist_event` signature thật — verify khi làm A2.
