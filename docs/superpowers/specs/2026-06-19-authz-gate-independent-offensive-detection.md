# Spec — B1: Authorization gate must not depend on the LLM it protects against

**Ngày:** 2026-06-19 · **Branch:** `fix/authz-gate-independent-offensive-b1`
**Nguồn:** code review của Gói 3 (C2), finding IMPORTANT #3.

## 1. Vấn đề
`pipeline.py::_check_authorization` tính `is_offensive = (task_type == "security")` rồi
`return None` ngay nếu không phải security. `task_type` do **LLM orchestrator phân loại** —
chính tầng đã thất bại trong task `4c05b7ff`. Nếu LLM gọi một task tấn công thật là
"general"/"intelligence", authorization gate **bị bỏ qua hoàn toàn**.

Đây vi phạm nguyên tắc: **một safety gate không được phụ thuộc vào thứ nó bảo vệ chống lại.**
(Phép loại suy: sandbox/permission của Claude Code chạy độc lập trên tool-call, không hỏi
LLM "lệnh này có nguy hiểm không" rồi mới chặn.)

## 2. Thiết kế (defense-in-depth, không thay LLM bằng keyword-soup)
LLM **vẫn là tầng phân loại chính** (semantic, đa ngôn ngữ — tin khi nó fire). Thêm một
**backstop độc lập, format/signal-based** để bắt khi LLM phân loại sai:

```python
# authorization_gate.py (pure)
def detect_offensive_intent(message: str) -> bool:
    """Backstop, KHÔNG phải primary classifier. True khi message chứa tín hiệu
    hành động tấn công TRỰC TIẾP, rõ ràng (đa ngôn ngữ, có biên giới từ)."""
```
Signal list **HẸP và rõ ràng** — chỉ hành động hands-on offensive (exploit, privilege
escalation, RCE, gain shell, pwn, leo thang đặc quyền, khai thác lỗ hổng, chiếm quyền…),
KHÔNG gồm từ mơ hồ ("test"/"attack" đơn lẻ) hay động từ thụ động (monitor/investigate/audit/
OSINT) để không chặn nhầm OSINT/defensive.

`_check_authorization`:
```python
is_offensive = (task_type == "security") or detect_offensive_intent(message)
```
Vì `requires_authorization` CHỈ block khi `target_class == REAL_PUBLIC_INFRA`, backstop
KHÔNG ảnh hưởng lab/CTF/private/no-target — chỉ bắt đúng case "tấn công thật + hạ tầng public
thật" mà LLM gọi sai loại.

## 3. Cân nhắc / tradeoff (trung thực)
- CLAUDE.md cảnh báo tránh keyword-matching làm giải pháp CHÍNH. Ở đây nó là **safety net thứ
  cấp**, không thay LLM. Bất đối xứng thiệt hại biện minh cho net bảo thủ: false-negative =
  tấn công trái phép (thảm hoạ); false-positive = user set `ALLOW_LIVE_OFFENSIVE=1` (rẻ).
- Signal list giữ HẸP để giảm false-positive; OSINT/defensive verbs bị loại tường minh.

## 4. Verify
- TDD: test pure `detect_offensive_intent` (fire trên offensive thật đa ngôn ngữ; KHÔNG fire
  trên OSINT/defensive/benign); test `_check_authorization` chặn live-attack-misclassified-as-general,
  vẫn cho qua OSINT-on-public + general-no-target.
- Full suite phải xanh (baseline 557). Không regression.

## 5. Ngoài phạm vi
- IPv6/integer-IP target (reviewer MINOR) — cân nhắc làm cùng nếu rủi ro thấp.
- A5/A6/A7/A8 — gói sau.
