# Spec — A5: phantom write_file should not report success silently

**Ngày:** 2026-06-19 · **Branch:** `fix/phantom-write-honest-a5`
**Nguồn:** `docs/AUDIT_TASKS_2026-06-19.md` (A5) + memory `workspace-path-inconsistency`.

## 1. Vấn đề (đã reproduce)
Khi `current_task_id`/`current_parent_task_id` contextvar rỗng (context propagation
fail trong Docker/async), `get_task_workspace()` âm thầm fallback về `WORKSPACE_DIR/_unscoped`
và `handle_write_file` vẫn trả `success: True`. Agent (và LLM) tin file đã vào task
workspace, thực tế nó lạc vào `_unscoped` → "phantom write". Log có WARNING/ERROR nhưng
KHÔNG vào tool output, nên không ai thấy.

Reproduce: gọi `handle_write_file` với contextvar rỗng → `success=True`, ws kết thúc
`/_unscoped`. Với contextvar set → ws đúng `tasks/<id>/_work`.

## 2. Triết lý fix (Claude Code: report outcomes faithfully)
Một tool ghi file mà KHÔNG chắc đang ở đúng task workspace phải báo điều đó RÕ RÀNG
trong output, không "success" im lặng. KHÔNG đổi thành `success:False` cứng (file vẫn
ghi được; đổi sẽ phá caller + đẩy agent vào retry-loop) — surface honest, đồng nhất A6.

## 3. Thiết kế (surgical, testable không cần Docker)
### 3.1 `filesystem.py` — resolve trả kèm cờ fallback
Tách logic của `get_task_workspace()` thành `resolve_task_workspace() -> (Path, bool)`
trong đó bool = `unscoped_fallback` (True khi rơi vào `_unscoped` vì thiếu cả context
lẫn discovery). `get_task_workspace()` giữ nguyên signature (trả Path) để 19 caller cũ
không đổi — chỉ gọi `resolve_task_workspace()[0]`.

### 3.2 `handle_write_file` — surface cảnh báo khi unscoped fallback
Dùng `resolve_task_workspace()`; nếu `unscoped_fallback`:
- vẫn ghi (file không mất), vẫn `success: True`,
- thêm vào output: "⚠ WORKSPACE FALLBACK: no task context — wrote to `_unscoped/`, NOT the
  task workspace. This usually means context propagation failed; the file may not appear
  where the task expects it."
- thêm `data["unscoped_fallback"] = True` để caller/telemetry đọc được.

## 4. Verify
- TDD: pure-ish test gọi `handle_write_file` với contextvar rỗng (assert output chứa
  cảnh báo + data.unscoped_fallback True) vs set (assert KHÔNG cảnh báo). Test
  `resolve_task_workspace` trả cờ đúng cho 3 nhánh (parent/task set → False; rỗng → True).
- Full suite xanh (baseline 644). Không regression (19 caller của get_task_workspace
  giữ nguyên signature).
- TRUNG THỰC: đây sửa TRIỆU CHỨNG-báo-cáo (phantom write giờ to tiếng). NGUYÊN NHÂN gốc
  (vì sao contextvar rỗng trong Docker) là vấn đề propagation riêng cần verify trong
  container — KHÔNG đổi được bằng unit test; ghi nhận, không tự nhận đã fix.

## 5. Ngoài phạm vi
- Sửa root cause context propagation trong Docker (cần môi trường container).
- B-IPv6, GoalComplianceChecker — gói khác.
