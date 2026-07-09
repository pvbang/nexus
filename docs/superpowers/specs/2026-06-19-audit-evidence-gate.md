# Spec — A6: audit_log must not bless an evidence-less finding

**Ngày:** 2026-06-19 · **Branch:** `fix/audit-evidence-gate-a6`
**Nguồn:** `docs/AUDIT_TASKS_2026-06-19.md` (A6).

## 1. Vấn đề (đã neo vào code — và đính chính báo động giả)
`app/mcp/tools/audit_log.py::_audit_log` **luôn** `return tool_result(success=True, ...)`
bất kể `evidence` có hay không (`evidence` là optional, schema không bắt buộc). Một agent
có thể ghi một FINDING (severity=critical) mà KHÔNG kèm bằng chứng, và:
- tool báo "Audit transaction logged" / success=True,
- `log_audit_transaction` ghi vào ACTIVITY.md NHƯNG bỏ qua block `**Evidence**:` — không
  có dấu hiệu nào cho biết "claim này chưa có bằng chứng".

→ audit trail trông đầy đủ giao dịch nhưng RỖNG bằng chứng. Đây là self-attestation ở tầng
audit (đúng họ hàng A3), và ROE còn dạy "every action MUST be logged via audit_log".

**ĐÍNH CHÍNH:** agent điều tra vòng trước claim A6 là "async race — success trả về trước
khi activity log xong". SAI: `_audit_log` `await al.log_audit_transaction(...)` ĐẦY ĐỦ rồi mới
return. Không có race. Gốc rễ thật là evidence-less success ở trên.

## 2. Triết lý fix (Claude Code: evidence before assertions)
Một audit transaction tuyên một FINDING đáng kể (severity ∈ {critical,high,medium} hoặc
category=exploit) PHẢI kèm evidence. Hành động recon trung tính (no severity / info / low,
vd dns_lookup, port_scan) thì KHÔNG bắt buộc — chính hành động là sự kiện.

Khi finding thiếu evidence: KHÔNG chặn cứng (audit_log là "mandatory every action" — chặn
cứng đẩy agent vào loop, đúng cái A1 chống). Thay vào đó SURFACE minh bạch (giống A3 surface
self-attested, không suppress):
- Ghi vào ACTIVITY.md với badge "⚠ UNSUBSTANTIATED — no evidence provided".
- tool_result vẫn success=True (giữ dấu vết) NHƯNG output cảnh báo rõ: finding này chưa có
  bằng chứng, re-log với `evidence=` để substantiate.

## 3. Thiết kế (surgical — 1 pure helper + 2 nối dây + 1 test)
### 3.1 `audit_log.py` — pure helper
```python
_EVIDENCE_REQUIRED_SEVERITIES = {"critical", "high", "medium"}
_EVIDENCE_REQUIRED_CATEGORIES = {"exploit"}

def audit_requires_evidence(severity: str, category: str) -> bool:
    """A finding with material severity (or an exploit) must carry evidence."""
```
### 3.2 `audit_log.py::_audit_log`
- `evidence = args.get("evidence","").strip()`
- `unsubstantiated = audit_requires_evidence(sev, cat) and not evidence`
- truyền `unsubstantiated` xuống `log_audit_transaction`
- nếu `unsubstantiated`: return success=True nhưng output cảnh báo (UNSUBSTANTIATED + cách sửa).

### 3.3 `activity_logger.py::log_audit_transaction`
- thêm param `unsubstantiated: bool = False`; khi True, thêm badge "⚠ UNSUBSTANTIATED
  (no evidence provided)" vào block transaction.

## 4. Verify
- TDD: pure `audit_requires_evidence` (ma trận severity/category); `_audit_log` async với
  current_task_id set (dùng tmp workspace) — finding-no-evidence → output cảnh báo + badge
  trong ACTIVITY.md; finding-with-evidence & recon-no-severity → success thường.
- Full suite xanh (baseline 587). Không regression.

## 5. Ngoài phạm vi
- "PASS ghi vào manifest trước khi có log" (audit gốc) là vấn đề nội dung/orchestration rộng,
  không phải audit_log tool — không fix ở đây.
- A5/A7/A8, B-IPv6 — gói sau.
