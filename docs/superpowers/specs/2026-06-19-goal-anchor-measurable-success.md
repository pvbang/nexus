# Spec — A7: GOAL_ANCHOR must carry measurable success criteria, not echo the prompt

**Ngày:** 2026-06-19 · **Branch:** `fix/goal-anchor-measurable-a7`
**Nguồn:** `docs/AUDIT_TASKS_2026-06-19.md` (A7).

## 1. Vấn đề (đã neo vào code)
`pipeline.py:285` (non-resume) sinh GOAL_ANCHOR:
```python
f"SUCCESS WHEN: {str(plan_data.get('success_criteria', plan_data.get('intent_analysis', message[:100])))[:200]}"
```
`plan_data` KHÔNG có key `success_criteria` ở top-level (chỉ có per-assignment, prompts.py:419)
→ fallback rơi xuống `intent_analysis` → cuối cùng `message[:100]`. Kết quả: GOAL_ANCHOR của cả
2 task audit chỉ LẶP LẠI đề bài ("SUCCESS WHEN: <đề bài cắt cụt>"), KHÔNG phải tiêu chí đo được.
Đây là gốc của "completion theater": orchestrator không có mốc verify rõ → không biết "xong"
nghĩa là gì → A1/A2/A3/A6 chỉ chữa phần ngọn.

**Điểm mấu chốt:** prompt LLM ĐÃ sinh sẵn tiêu chí đo được — `deliverable_contract.definition_of_done`
("user is satisfied when ___") + `artifacts[].verify` ("how to PROVE it works — not just file
exists"). Nhưng GOAL_ANCHOR không bao giờ đọc chúng. Đây là bug WIRING, không phải thiếu cơ chế.

## 2. Triết lý fix (CLAUDE.md §5 — Goal-Driven Execution)
"Strong success criteria let you loop independently. Weak criteria require constant
clarification." GOAL_ANCHOR (mỏ neo orchestrator đọc mỗi turn + resume) phải mang
tiêu-chí-đo-được nếu LLM đã sinh, thay vì echo đề bài.

## 3. Thiết kế (surgical — 1 pure helper + nối dây 1 chỗ + resume)
### 3.1 `nexus_orchestrator.py` (hoặc module pure) — derive success criteria
```python
def derive_success_criteria(plan_data: dict) -> str:
    """Tiêu chí thành công đo được từ deliverable_contract, fallback an toàn.
    Ưu tiên: definition_of_done + (mỗi artifact: what — verify).
    Nếu không có contract: intent_analysis. Nếu cũng không: '' (KHÔNG echo message)."""
```
Trả chuỗi rỗng khi thật sự không có gì đo được — để caller xử lý honest (KHÔNG nhồi message làm
"tiêu chí" giả).

### 3.2 `pipeline.py:282-286` — dùng helper
```python
_success = derive_success_criteria(plan_data)
_goal_anchor = (
    "🎯 GOAL ANCHOR (NEVER deviate from this):\n"
    f"USER ASKED: {message[:200]}\n"
    + (f"SUCCESS WHEN: {_success[:400]}\n" if _success
       else "SUCCESS WHEN: (no measurable criteria derived — verify against USER ASKED above)\n")
)
```
Khi rỗng: nói THẲNG là chưa có tiêu chí đo (honest), không giả vờ message là tiêu chí.

### 3.3 Persist + resume
- Lưu `success_criteria` đã derive vào `ExecutionState` + checkpoint (như task_type ở C2) để resume
  giữ được, và để downstream (final report / completion) đọc một nguồn chuẩn.
- Resume path: GOAL_ANCHOR.txt đã có trên đĩa (pipeline đọc lại) — không regenerate, nhưng state
  hydrate `success_criteria` từ checkpoint.

## 4. Verify
- TDD: pure `derive_success_criteria` (có contract đầy đủ → definition_of_done + artifact verify;
  chỉ intent → intent; rỗng → ''; contract artifacts thiếu verify → vẫn lấy what). Integration:
  GOAL_ANCHOR.txt chứa definition_of_done khi có contract, chứa dòng honest "no measurable" khi không.
- Full suite xanh (baseline 604). Không regression. Đặc biệt không phá test đọc GOAL_ANCHOR/_scope_signals.

## 5. Ngoài phạm vi
- Không ép LLM sinh contract tốt hơn (đó là chất lượng prompt, không phải wiring). Chỉ đảm bảo NẾU
  LLM đã sinh tiêu chí đo được thì GOAL_ANCHOR phải dùng nó.
- A5/A8, B-IPv6 — gói sau.
