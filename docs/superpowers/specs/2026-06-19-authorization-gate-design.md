# Spec — Gói 3: Authorization Gate for Live Offensive Ops (C2)

**Ngày:** 2026-06-19 · **Branch:** `fix/authorization-gate-c2`
**Nguồn:** `docs/AUDIT_TASKS_2026-06-19.md` (C2)

## 1. Vấn đề (đã neo vào code)
- `roe_generator.py` **ghi** ROE.md (authorization statement, hard constraints) nhưng
  **không có code nào đọc lại để enforce**. ROE chỉ là tài liệu.
- `app/services/orchestrator.py:262` gọi `pipeline.run()` **vô điều kiện**; không có
  bước nào kiểm tra "target này có được phép tấn công không" trước khi spawn agent.
- Hậu quả thực (task `4c05b7ff`): hệ nhận IP thật `117.5.141.7` + credentials thật +
  "leo thang đặc quyền + kiểm soát toàn bộ hệ thống" và **đã chạy lệnh thật** (PTY
  round-trip, enum `id`) trước khi content_filter chặn. Không phải lab/CTF.

## 2. Triết lý fix (chính sách harness Claude Code)
- KHÔNG refuse pentest/CTF/security research/defensive hợp pháp.
- Dual-use offensive ops chống **hạ tầng public thật** đòi **authorization context rõ
  ràng** (như `--dangerously-skip-permissions` của agy phải là cờ tường minh).
- Mặc định AN TOÀN: live offensive chống public infra bị chặn cho tới khi chủ sở hữu
  **opt-in tường minh**. Lab/CTF/private/internal target KHÔNG bị ảnh hưởng.

## 3. Thiết kế (surgical — 1 module pure + 1 chèn gate + 1 env)

### 3.1 `app/security/authorization_gate.py` (pure, no I/O)
Phân loại dựa trên **định dạng** (đúng việc cho `ipaddress`/regex — KHÔNG keyword semantic):
```python
class TargetClass(Enum):
    NONE              # không có target hạ tầng (vd thiết kế sản phẩm)
    SAFE              # private/loopback/reserved IP, localhost, *.lab/.local/.internal,
                      # example.com, CTF ranges (htb/thm) → cho phép tự do
    REAL_PUBLIC_INFRA # public-routable IP / domain thật

def classify_target(message: str) -> tuple[TargetClass, str]:
    """Trích IP/domain (tái dùng regex của roe_generator), phân loại qua ipaddress:
       is_private/is_loopback/is_reserved/is_link_local → SAFE; còn lại public → REAL."""

def requires_authorization(
    target_class: TargetClass, is_offensive: bool, authorized: bool
) -> bool:
    """True (block) ⟺ is_offensive AND target_class == REAL_PUBLIC_INFRA AND not authorized."""

@dataclass
class AuthorizationDecision:
    blocked: bool
    target: str
    target_class: TargetClass
    reason: str
```
`is_offensive` = task_type == "security" (pentest chủ động). "intelligence"/OSINT
(passive) KHÔNG bị chặn. `authorized` = env `ALLOW_LIVE_OFFENSIVE` truthy
(`1/true/yes`). Default off.

### 3.2 `pipeline.py` Phase 1.5 — enforce sau planning, trước execution loop
- Sau khi có `task_type`/`is_security_task`/`message` (dòng ~197-203), gọi gate.
- Nếu `decision.blocked`: KHÔNG spawn agent. Emit event `authorization_required`,
  ghi một honest message giải thích (target là public infra thật; cần xác nhận ủy quyền
  + set `ALLOW_LIVE_OFFENSIVE=1`), `return` kết quả sớm với content đó.
- Áp dụng cho cả non-resume path (resume path đã qua gate ở lần chạy đầu — và env vẫn
  enforce nếu muốn; nhưng để đơn giản & an toàn, resume cũng check).

### 3.3 env
`ALLOW_LIVE_OFFENSIVE` (default unset/false). Đọc qua `os.environ` trong gate (pure
nhận `authorized: bool` từ caller → caller đọc env; gate vẫn pure & testable).

## 4. An toàn & verify
- Module pure tách riêng → unit test trực tiếp (classify từng loại IP/domain, ma trận
  requires_authorization). Integration nhẹ: pipeline block path trả message + không
  spawn (test bằng cách gọi gate + giả lập, không chạy LLM thật).
- Mặc định off KHÔNG phá test hiện có (không test nào tấn công public IP). Full suite
  phải xanh (baseline gói 2: 520 passed).
- Rủi ro false-block: chỉ chặn khi đồng thời (offensive) + (public infra thật) + (chưa
  opt-in). Thiết kế sản phẩm / OSINT / lab → không chạm.
- KHÔNG làm lộ: message block không in lại credentials.

## 5. Ngoài phạm vi
- Không thêm per-action runtime sandbox (đã có Governance khác). Gate là pre-flight
  authorization, đúng gốc rễ C2 ("ROE ghi nhưng không enforce").
