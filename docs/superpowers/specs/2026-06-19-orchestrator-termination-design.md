# Spec — Gói 1: Orchestrator Honest Termination (A1 + A2)

**Ngày:** 2026-06-19 · **Branch:** `fix/orchestrator-termination-a1-a2`
**Nguồn:** `docs/AUDIT_TASKS_2026-06-19.md` (gốc rễ A1, A2)

## 1. Vấn đề (đã neo vào code)

### A1 — Re-schedule vô hạn, bỏ qua STOP signal
- `nexus_orchestrator.py:1453-1469`: khi cùng một assignment-hash lặp ≥3 lần, code **chỉ prepend warning `[LOOP DETECTED]`** rồi vẫn `return TurnPlan(...)` → vòng lặp tiếp tục mãi.
- `pipeline.py:1718-1728`: tín hiệu `FORCE-STOPPED` của agent (agent đã bị kill vì stuck) chỉ chảy vào `situational_awareness` text — **không tới logic quyết định dừng**.
- Hậu quả thực tế: task `4c05b7ff` re-schedule live-exploit ≥7 lần sau STOP → tái tạo đúng infinite-loop mà đề bài cấm.

### A2 — Kẹt "50% mù" vô hạn khi evaluator parser hỏng
- `nexus_orchestrator.py:902-934`: parse fail → `EvaluationResult(progress_pct=50, is_complete=False)`. Escalate ở `_EVAL_PARSE_FAIL_ESCALATE_AFTER = 3` chỉ **thêm text** vào `missing_items`/`next_turn_focus`, **không bao giờ cho phép dừng**.
- Stagnation guard (`termination.py`) cần 15 turn flat mới cứu → task `4c05b7ff` lãng phí 7 turn (12→18) ở "50% mù" (xác nhận bằng snapshots `total_bytes` đứng yên).

## 2. Triết lý fix (mượn từ kiến trúc Claude Code)
- **Subagent "đến lúc nghỉ" thì DỪNG** và final message *là* kết quả → orchestrator phải biết dừng, không re-schedule vô hạn.
- **Báo cáo trung thực**: khi không steer được nữa thì *honest handoff* (verified vs open), không giả vờ còn 50% để tiếp tục mù. Đây chính là việc agent đã phải làm thủ công qua `HANDOFF.md` ở task 2.
- Nguyên tắc: **escalation ladder** — cảnh báo MỘT lần (cho cơ hội đổi cách), rồi *terminate trung thực*.

## 3. Thiết kế (surgical — 2 file code + 1 file test)

### 3.1 `termination.py` — thêm pure policy (no I/O, có test)
```python
_EVAL_PARSE_FAIL_TERMINATE_AFTER = 5   # parse hỏng 5 turn liên tiếp → honest terminate
_LOOP_REPEAT_TERMINATE_AT = 5          # cùng assignment lặp 5 lần → terminate

def should_terminate_for_loop(occurrences: int, has_force_stopped: bool) -> bool:
    """True khi pipeline lặp vô vọng:
      - occurrences >= 5 (đã warn ở 3, vẫn lặp thêm), HOẶC
      - occurrences >= 3 VÀ agent đã FORCE-STOPPED (cách tiếp cận đã cạn → dừng sớm).
    """
    if occurrences >= _LOOP_REPEAT_TERMINATE_AT:
        return True
    if occurrences >= 3 and has_force_stopped:
        return True
    return False
```
Giữ `_EVAL_PARSE_FAIL_ESCALATE_AFTER = 3` (warn) như cũ; thêm ngưỡng terminate ở 5.

### 3.2 `nexus_orchestrator.py`
**A2** — trong khối parse-fail (sau khi tăng `streak`):
- streak 3..4 → giữ nguyên hành vi hiện tại (warn qua `missing_items`/`next_turn_focus`).
- `streak >= _EVAL_PARSE_FAIL_TERMINATE_AFTER (5)` → `EvaluationResult(is_complete=True, progress_pct=<giữ prev>, ...)` kèm `missing_items` honest-handoff: *"Evaluator unparseable 5 turn — consolidate verified vs open, không tiếp tục mù."* + `next_turn_focus` chỉ đạo consolidate.

**A1** — tính `has_force_stopped` trong `evaluate_turn` (mirror `pipeline.py:1718`, đọc `turn_result.agent_results`), lưu `self._force_stopped_by_task[task_id]`. Trong `plan_next_turn`, tại chỗ loop-detect: vẫn prepend warning ở 3x (giữ nguyên), nhưng nếu `should_terminate_for_loop(occurrences, has_force_stopped)` → `return None` (kết thúc task thay vì re-schedule).

### 3.3 Vì sao KHÔNG sửa `pipeline.py`
`evaluate_turn` đã nhận `turn_result` (chứa `agent_results`), nên tính cờ FORCE-STOPPED ngay trong orchestrator → không cần truyền thêm tham số qua pipeline. Ít bề mặt thay đổi hơn.

## 4. An toàn & verify
- Chỉ đụng 2 file production + 1 file test. Mỗi dòng truy về A1/A2. Không refactor cái không hỏng.
- `is_complete=True` khi parse-fail là **honest termination** (kèm missing_items trung thực), KHÔNG phải giả hoàn thành — tốt hơn kẹt 50% vô hạn.
- **TDD:** viết test trước cho `should_terminate_for_loop` (pure) + nhánh parse-fail-terminate; chạy `tests/test_orchestrator_completion.py` + `test_orchestration_fixes.py` phải xanh trước/sau (baseline: 34 passed).
- `tests/` KHÔNG bị gitignore (đã verify; memory cũ lỗi thời).

## 5. Ngoài phạm vi (gói sau)
- A3 (verification integrity) → Gói 2. C2 (authorization guard) → Gói 3.
- Bug nội dung task gasifier (tautology số liệu, gỗ↔trấu) KHÔNG phải code dự án → không fix ở đây.
