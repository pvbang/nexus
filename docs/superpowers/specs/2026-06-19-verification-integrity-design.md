# Spec — Gói 2: Verification Integrity (A3)

**Ngày:** 2026-06-19 · **Branch:** `fix/verification-integrity-a3`
**Nguồn:** `docs/AUDIT_TASKS_2026-06-19.md` (A3, B13/B14, R2-3/6/7/8/10)

## 1. Vấn đề (đã neo vào code)

Hệ thống ĐÃ CÓ đủ cơ chế phát hiện verification-theater, nhưng chúng chỉ **inject
context cho LLM / discount trust**, KHÔNG **chặn completion**:

- `tautology_prober.probe_scan` runtime-CONFIRM một check là self-cancelling (metric
  không đổi qua input ngẫu nhiên) → `pipeline.py:561` chỉ ghép `build_suspect_context`
  vào `ledger_context` cho LLM đọc. LLM evaluator vẫn có thể quyết "complete".
- `_postprocess_evaluation` (`pipeline.py:1505-1549`) là cổng completion deterministic
  DUY NHẤT, nhưng chỉ veto khi `verification_blocks_completion(claims_failed)` —
  tức **chỉ khi một claim "done" bị workspace mâu thuẫn**. Nó KHÔNG chặn khi:
  (a) tautology đã runtime-CONFIRMED tồn tại trong workspace, hoặc
  (b) completion chỉ dựa trên self-attested verdict mà chưa agent độc lập nào re-run.

Hậu quả thực: task `33ddfc0c` "complete / 32-32 PASS" trong khi closure 100% là
tautology, SF=655.6 back-solve, validation_suite do chính agent viết+chấm — đúng
pattern memory `[[verification-theater-task-d8b78042]]`.

## 2. Triết lý fix (kiến trúc Claude Code)
- **Verifier là subagent context cô lập, KHÁC người làm.** Self-attested "PASS" không
  phải bằng chứng; chỉ một independent re-run mới là.
- **Evidence before assertions.** Một completion dựa trên verdict chưa ai re-execute
  phải bị chặn cho tới khi có critic độc lập — không "discount rồi cho qua".
- Tautology runtime-CONFIRMED = bằng chứng CỨNG rằng "PASS" là void → block ngang
  hàng với `claims_failed`.

## 3. Thiết kế (surgical — nâng "advisory" thành "blocking", nối dây sẵn có)

### 3.1 `tautology_detector.py` — pure helper đếm confirmed (no I/O)
```python
def count_confirmed_tautologies(scans: dict[str, ScanResult]) -> int:
    """Số suspect đã được prober runtime-CONFIRMED (confirmed == 'confirmed')."""
def confirmed_tautology_lines(scans, limit=5) -> list[str]:
    """['path:line — name', ...] cho các confirmed — để ghi vào missing_items."""
```

### 3.2 `ledger_verifier.py` — pure gate cho self-attested-không-có-critic
```python
def completion_needs_independent_review(
    is_complete: bool,
    self_attested_recent: int,
    independent_review_recent: bool,
) -> bool:
    """True khi LLM muốn complete, turn gần đây CÓ self-attested verdict, NHƯNG
    chưa có critic/independent re-run nào → cần 1 turn critic độc lập trước khi done."""
```
Lý do tách pure: testable không cần pipeline, đúng convention module này.

### 3.3 `pipeline.py::_postprocess_evaluation` — nối 2 blocker mới vào cổng có sẵn
- Truyền thêm `tautology_scans` (đã tính ở dòng ~552) và tín hiệu "đã có independent
  review gần đây" vào `_postprocess_evaluation`.
- Thêm 2 nhánh veto (cùng style với nhánh `claims_failed` hiện có):
  1. `count_confirmed_tautologies(scans) > 0` và `is_complete` → set
     `is_complete=False`, append missing_items: *"Self-cancelling check(s) PROVEN by
     re-execution (verify nothing): {lines}. Fix the check + re-derive independently
     before completing."* + steer `next_turn_focus` tới critic.
  2. `completion_needs_independent_review(...)` → block 1 turn, steer next_turn_focus:
     *"[INDEPENDENT REVIEW REQUIRED] A DIFFERENT agent must re-execute/re-derive the
     self-attested numbers and try to REFUTE them before this task may complete."*
- Khi block vì (2), đảm bảo next turn có critic: set `evaluation.next_assignments`
  có một entry `agent_role="critic"` nếu chưa có (role rotation + critic prompt sẵn
  có ở nexus_orchestrator.py:1289-1339 sẽ lo phần re-execute/refute).

### 3.4 Theo dõi "independent review gần đây"
- Một turn coi là "có independent review" nếu có `agent_results` với `agent_role` ∈
  {"critic"} (hoặc role review/QA) đã execute. Đọc từ `state.turns_completed[-N:]`.
- Pure helper trong ledger_verifier nhận sẵn boolean này (tính ở pipeline) → giữ
  pure/testable.

### 3.5 KHÔNG đụng consensus-skip (A4) ở gói này
`turn_executor.py:559` consensus-skip là tối ưu chi phí hợp lệ KHI các agent làm việc
phân tách. Việc ép critic độc lập đã được giải quyết qua completion-gate (3.3) — đó
là đòn bẩy đúng, ít rủi ro hơn là can thiệp discussion phase. Ghi nhận, không sửa.

## 4. An toàn & verify
- Chỉ đụng 3 file production (2 thêm pure helper, 1 nối dây) + 1 file test. Mỗi thay
  đổi truy về A3. Không refactor cái không hỏng.
- TDD: test pure (`count_confirmed_tautologies`, `completion_needs_independent_review`)
  trước; rồi test `_postprocess_evaluation` 2 nhánh veto mới (dựng `ScanResult` với
  suspect `confirmed="confirmed"` + `EvaluationResult(is_complete=True)`).
- Chạy full suite trước/sau (baseline gói 1: 508 passed). Không regression.
- Rủi ro: block sai (false-positive tautology) → đã giảm vì chỉ block trên
  `confirmed == "confirmed"` (prober đã re-run xác nhận), KHÔNG block trên "suspected".

## 5. Ngoài phạm vi
- C2 (authorization guard) → Gói 3.
- A4 consensus-skip: ghi nhận, giải quyết gián tiếp qua completion-gate.
