# Token Governor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm module thuần `app/core/token_governor.py` (TokenEstimator + BudgetTracker + LoopMonitor) và cắm vào agent_worker (per-agent) + nexus_orchestrator (per-task) để chặn run lê thê/tốn kém theo trần token tích lũy, đếm token chính xác, và bắt loop trong react-loop từng agent.

**Architecture:** Module thuần fail-open, KHÔNG tự hành động — chỉ đo & trả phán quyết; call-site quyết định hành động. Per-task budget dùng registry keyed theo `task_id` (mirror `compression_chain.get_or_create_chain`) để worker feed usage mà không phải luồn tham số qua nhiều tầng. Terminate per-task CHỈ qua termination ladder hiện có (`evaluate_completion`), không tạo đường dừng song song.

**Tech Stack:** Python 3.11+, asyncio, structlog, tiktoken (optional), pytest. Tái dùng: `app.core.model_context.get_model_context_limit`, `app.orchestration.token_journal.TokenJournal`, cơ chế wrap-up `agent_worker.py:504-524`, `current_task_id` contextvar.

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-06-23-token-governor-design.md` — mọi quyết định bám theo spec này.
- **Fail-open tuyệt đối:** mọi lỗi đo lường được nuốt (try/except), KHÔNG BAO GIỜ raise vào agent/orchestrator loop. Giống `try/except` quanh `TokenJournal.record` (`agent_worker.py:686-693`).
- **Không trộn ngữ nghĩa:** "nén context" (ảnh-chụp-context-đầy, nén thì giảm) ≠ "budget tích lũy" (tổng token đã tiêu, đơn điệu tăng). BudgetTracker KHÔNG nằm trong class compression.
- **Terminate per-task chỉ qua `termination.evaluate_completion`** với `has_blocked_justification=True` + reason `token_budget_exhausted`. Không có đường dừng thứ hai.
- Mọi ngưỡng externalize ra `app/config.py` (pattern `Field(...)` / plain default đã dùng).
- `token_governor.py` chỉ import `app.core.model_context` + `tiktoken` (optional). KHÔNG import `app.orchestration.*` (tránh circular).
- **GOTCHA:** `tests/` bị gitignore ở repo này — viết & chạy test local nhưng KHÔNG commit file test (chỉ commit code `app/`). Cẩn thận các phiên Claude song song đổi branch.
- Branch làm việc: `feat/token-governor` (đã có, chứa spec commit c1a6ec62).
- Default budget chọn RỘNG (chặn runaway, không phải limit hành chính): `agent_token_budget=400_000`, `task_token_budget=2_000_000`.

---

### Task 1: TokenEstimator (B) — đếm token chính xác + calibrate

**Files:**
- Create: `app/core/token_governor.py`
- Test: `tests/test_token_governor.py`

**Interfaces:**
- Consumes: `app.core.model_context.get_model_context_limit` (chỉ dùng ở Task 2; ở đây import sẵn module).
- Produces:
  - `class TokenEstimator` với:
    - `__init__(self, use_tiktoken: bool = True) -> None`
    - `estimate(self, messages: list[dict], model: str = "") -> int`
    - `observe(self, model: str, est_tokens: int, real_prompt_tokens: int) -> None`
  - `_char_estimate(messages: list[dict]) -> int` (module-level helper, CJK-aware).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_token_governor.py
from app.core.token_governor import TokenEstimator, _char_estimate


def _msgs(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def test_char_estimate_latin_vs_cjk():
    # Latin ~4 chars/token, CJK ~2 chars/token → CJK estimate higher per char
    latin = _char_estimate(_msgs("hello world " * 50))
    cjk = _char_estimate(_msgs("正在处理任务" * 50))
    assert latin > 0 and cjk > 0
    # same char count, CJK should estimate more tokens
    assert cjk > latin


def test_estimate_returns_positive_and_handles_empty():
    est = TokenEstimator(use_tiktoken=False)  # force char fallback (deterministic)
    assert est.estimate(_msgs("some content here")) > 0
    assert est.estimate([]) == 0


def test_observe_calibrates_toward_real():
    est = TokenEstimator(use_tiktoken=False)
    model = "claude-opus-4-8"
    # Pretend our raw estimate is consistently HALF of real tokens.
    for _ in range(30):
        raw = est._raw_estimate(_msgs("abcd " * 100), model)  # uncalibrated baseline
        est.observe(model, est_tokens=raw, real_prompt_tokens=raw * 2)
    # After calibration, estimate() ≈ 2× the raw baseline (correction → ~2.0)
    raw_now = est._raw_estimate(_msgs("abcd " * 100), model)
    calibrated = est.estimate(_msgs("abcd " * 100), model)
    assert calibrated >= raw_now * 1.7  # converged most of the way to 2.0


def test_observe_clamps_correction():
    est = TokenEstimator(use_tiktoken=False)
    model = "weird"
    # Feed an absurd ratio → correction must clamp to [0.5, 2.0]
    for _ in range(50):
        est.observe(model, est_tokens=10, real_prompt_tokens=10_000)
    assert est._correction[model] <= 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_token_governor.py -v --no-header 2>&1 | head -20`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.token_governor'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/token_governor.py
"""Token Governor — pure measurement primitives for long-running tasks.

Three independent, fail-open components (NO side effects — they only measure
and return a verdict; the caller decides the action):

  - TokenEstimator (B): estimate tokens BEFORE an LLM call. tiktoken baseline
    (optional import) + per-model calibration learned from real usage.
  - BudgetTracker  (A): cumulative-EFFORT ceiling (monotonic total tokens spent),
    distinct from context compression (a point-in-time window check).
  - LoopMonitor    (C): detect a single agent repeating the same tool call
    inside its react-loop.

Inspired by deer-flow's TokenBudgetMiddleware + LoopDetectionMiddleware, adapted
to mekongai's plain-dict LLM result shape.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# ── B: token estimation ──────────────────────────────────────────────────────

_CHARS_PER_TOKEN_LATIN = 4.0
_CHARS_PER_TOKEN_CJK = 2.0
_CORRECTION_MIN = 0.5
_CORRECTION_MAX = 2.0
_CORRECTION_EMA_ALPHA = 0.2  # weight of each new observation


def _content_text(msg: dict) -> str:
    """Flatten a message's content to plain text (handles str or block list)."""
    content = msg.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    else:
        text = ""
    # tool_calls arguments also cost tokens
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            text += " " + str(tc.get("function", {}).get("arguments", ""))
    return text


def _char_estimate(messages: list[dict]) -> int:
    """CJK-aware char-based token estimate (fallback when tiktoken unavailable).

    Latin text ~4 chars/token; CJK/non-ASCII ~2 chars/token. We split each
    message's chars into ascii vs non-ascii and divide by the matching ratio.
    """
    total = 0.0
    for msg in messages:
        text = _content_text(msg)
        if not text:
            continue
        non_ascii = sum(1 for c in text if ord(c) > 127)
        ascii_n = len(text) - non_ascii
        total += ascii_n / _CHARS_PER_TOKEN_LATIN + non_ascii / _CHARS_PER_TOKEN_CJK
        total += 4  # per-message role/metadata overhead
    return int(total)


class TokenEstimator:
    """Estimate prompt tokens before a call; self-calibrate from real usage."""

    def __init__(self, use_tiktoken: bool = True) -> None:
        self._use_tiktoken = use_tiktoken
        self._correction: dict[str, float] = {}
        self._encoding = None
        self._tiktoken_ok = False
        if use_tiktoken:
            self._try_init_tiktoken()

    def _try_init_tiktoken(self) -> None:
        try:
            import tiktoken

            # cl100k_base is a reasonable cross-model baseline; per-model drift
            # is corrected by observe(). Warmup is cheap here (no network).
            self._encoding = tiktoken.get_encoding("cl100k_base")
            self._tiktoken_ok = True
        except Exception as e:  # missing lib, transient download failure, etc.
            logger.debug("tiktoken_unavailable", error=str(e))
            self._tiktoken_ok = False

    def _raw_estimate(self, messages: list[dict], model: str = "") -> int:
        """Uncalibrated baseline estimate (tiktoken if available, else char)."""
        if self._tiktoken_ok and self._encoding is not None:
            try:
                total = 0
                for msg in messages:
                    text = _content_text(msg)
                    if text:
                        total += len(self._encoding.encode(text)) + 4
                return total
            except Exception:
                pass  # fall through to char estimate
        return _char_estimate(messages)

    def estimate(self, messages: list[dict], model: str = "") -> int:
        """Calibrated token estimate for a message list."""
        if not messages:
            return 0
        raw = self._raw_estimate(messages, model)
        factor = self._correction.get(model, 1.0) if model else 1.0
        return int(raw * factor)

    def observe(self, model: str, est_tokens: int, real_prompt_tokens: int) -> None:
        """Update per-model correction from a real prompt_tokens observation."""
        if not model or est_tokens <= 0 or real_prompt_tokens <= 0:
            return
        ratio = real_prompt_tokens / est_tokens
        ratio = max(_CORRECTION_MIN, min(_CORRECTION_MAX, ratio))
        prev = self._correction.get(model, 1.0)
        self._correction[model] = (
            (1 - _CORRECTION_EMA_ALPHA) * prev + _CORRECTION_EMA_ALPHA * ratio
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_token_governor.py -v --no-header 2>&1 | head -25`
Expected: PASS (4 tests). Lưu ý: tiktoken có thể đã cài — test dùng `use_tiktoken=False` nên xác định, không phụ thuộc lib.

- [ ] **Step 5: Commit** (chỉ code, KHÔNG add file test vì `tests/` gitignored)

```bash
git add app/core/token_governor.py
git commit -m "feat(token-governor): TokenEstimator with tiktoken baseline + per-model calibration"
```

---

### Task 2: BudgetTracker (A) — trần công sức tích lũy + registry per-task

**Files:**
- Modify: `app/core/token_governor.py` (thêm BudgetTracker + registry)
- Test: `tests/test_token_governor.py` (thêm test)

**Interfaces:**
- Consumes: `app.core.model_context.get_model_context_limit` (chỉ để tham khảo, không bắt buộc).
- Produces:
  - `class BudgetStatus(NamedTuple)`: `level: str` (`"ok"|"warn"|"hard_stop"`), `used: int`, `limit: int`, `pct: float`.
  - `class BudgetTracker`:
    - `__init__(self, limit: int, warn_ratio: float = 0.80, hard_stop_ratio: float = 0.95) -> None`
    - `add(self, usage: Optional[dict]) -> None` (đọc `usage["total_tokens"]`, fail-open)
    - `status(self) -> BudgetStatus`
    - `used` (property) `-> int`
  - Registry per-task:
    - `get_task_budget(task_id: str, limit: int) -> BudgetTracker` (tạo nếu chưa có, thread-safe)
    - `clear_task_budget(task_id: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_token_governor.py
from app.core.token_governor import (
    BudgetTracker, BudgetStatus, get_task_budget, clear_task_budget,
)


def test_budget_levels():
    bt = BudgetTracker(limit=1000, warn_ratio=0.80, hard_stop_ratio=0.95)
    assert bt.status().level == "ok"
    bt.add({"total_tokens": 850})
    assert bt.status().level == "warn"
    bt.add({"total_tokens": 120})  # 970/1000
    assert bt.status().level == "hard_stop"
    assert bt.used == 970


def test_budget_monotonic_and_failopen():
    bt = BudgetTracker(limit=1000)
    bt.add(None)            # fail-open, no crash
    bt.add({})              # no total_tokens key
    bt.add({"total_tokens": 100})
    bt.add({"total_tokens": 0})
    assert bt.used == 100   # only real usage counts, never decreases


def test_task_budget_registry_shared():
    clear_task_budget("task-X")
    a = get_task_budget("task-X", limit=5000)
    b = get_task_budget("task-X", limit=5000)
    assert a is b           # same instance for same task_id
    a.add({"total_tokens": 50})
    assert b.used == 50
    clear_task_budget("task-X")
    c = get_task_budget("task-X", limit=5000)
    assert c.used == 0      # cleared → fresh
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_token_governor.py -k budget -v --no-header 2>&1 | head -20`
Expected: FAIL — `ImportError: cannot import name 'BudgetTracker'`

- [ ] **Step 3: Write minimal implementation** (append to `app/core/token_governor.py`)

```python
# ── A: cumulative-effort budget ───────────────────────────────────────────────

from typing import NamedTuple  # noqa: E402  (grouped with stdlib at top in final)


class BudgetStatus(NamedTuple):
    level: str   # "ok" | "warn" | "hard_stop"
    used: int
    limit: int
    pct: float


class BudgetTracker:
    """Monotonic cumulative token ceiling (effort/cost), fed by REAL usage.

    Distinct from compression: this never decreases. warn → ask the caller to
    wrap up; hard_stop → caller forces a conclusion.
    """

    def __init__(
        self,
        limit: int,
        warn_ratio: float = 0.80,
        hard_stop_ratio: float = 0.95,
    ) -> None:
        self._limit = max(1, int(limit))
        self._warn = warn_ratio
        self._hard = hard_stop_ratio
        self._used = 0

    def add(self, usage: Optional[dict]) -> None:
        if not usage:
            return
        try:
            tokens = int(usage.get("total_tokens", 0) or 0)
        except Exception:
            return
        if tokens > 0:
            self._used += tokens

    @property
    def used(self) -> int:
        return self._used

    def status(self) -> BudgetStatus:
        pct = self._used / self._limit
        if pct >= self._hard:
            level = "hard_stop"
        elif pct >= self._warn:
            level = "warn"
        else:
            level = "ok"
        return BudgetStatus(level=level, used=self._used, limit=self._limit, pct=pct)


# Per-task registry — mirrors compression_chain.get_or_create_chain so workers
# can feed the task-level budget via current_task_id without parameter threading.
_task_budgets: dict[str, BudgetTracker] = {}
_task_budgets_lock = threading.Lock()


def get_task_budget(task_id: str, limit: int) -> BudgetTracker:
    with _task_budgets_lock:
        bt = _task_budgets.get(task_id)
        if bt is None:
            bt = BudgetTracker(limit=limit)
            _task_budgets[task_id] = bt
        return bt


def clear_task_budget(task_id: str) -> None:
    with _task_budgets_lock:
        _task_budgets.pop(task_id, None)
```

> **Lưu ý cleanup:** chuyển `from typing import NamedTuple` lên cụm import đầu file (gộp với `Any, Optional`), bỏ dòng `# noqa` minh hoạ ở trên — để file gọn, không có import giữa file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_token_governor.py -k budget -v --no-header 2>&1 | head -20`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/core/token_governor.py
git commit -m "feat(token-governor): BudgetTracker (cumulative-effort ceiling) + per-task registry"
```

---

### Task 3: LoopMonitor (C) — bắt lặp tool-call trong react-loop

**Files:**
- Modify: `app/core/token_governor.py` (thêm LoopMonitor)
- Test: `tests/test_token_governor.py` (thêm test)

**Interfaces:**
- Produces:
  - `class LoopStatus(NamedTuple)`: `level: str` (`"ok"|"warn"|"hard_stop"`), `key: str`, `count: int`.
  - `class LoopMonitor`:
    - `__init__(self, warn_count: int = 3, hard_stop_count: int = 5) -> None`
    - `record(self, tool_name: str, args: Any) -> LoopStatus`
    - `reset(self) -> None`
  - `_stable_tool_key(tool_name: str, args: Any) -> str` (module-level helper).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_token_governor.py
from app.core.token_governor import LoopMonitor, _stable_tool_key


def test_stable_key_ignores_volatile_and_buckets_identifier():
    # Same identifying field (url) → same key even if other args differ
    k1 = _stable_tool_key("web_fetch", {"url": "http://x.com", "timeout": 5})
    k2 = _stable_tool_key("web_fetch", {"url": "http://x.com", "timeout": 99})
    assert k1 == k2
    k3 = _stable_tool_key("web_fetch", {"url": "http://y.com"})
    assert k1 != k3


def test_loop_monitor_warn_then_hardstop():
    lm = LoopMonitor(warn_count=3, hard_stop_count=5)
    args = {"path": "/a/b.py"}
    levels = [lm.record("read_file", args).level for _ in range(5)]
    assert levels == ["ok", "ok", "warn", "ok", "hard_stop"]


def test_loop_monitor_distinct_tools_dont_accumulate():
    lm = LoopMonitor(warn_count=3, hard_stop_count=5)
    assert lm.record("read_file", {"path": "/a"}).level == "ok"
    assert lm.record("read_file", {"path": "/b"}).level == "ok"
    assert lm.record("web_search", {"query": "x"}).level == "ok"
    # none repeated 3× → all ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_token_governor.py -k loop -v --no-header 2>&1 | head -20`
Expected: FAIL — `ImportError: cannot import name 'LoopMonitor'`

- [ ] **Step 3: Write minimal implementation** (append to `app/core/token_governor.py`)

```python
# ── C: in-agent loop detection ────────────────────────────────────────────────

import hashlib  # move to top-of-file import cluster in final cleanup

# Identifying arg fields, in priority order. First present field defines the
# call's identity; volatile fields (timeout, max_tokens, line ranges) ignored.
_IDENTITY_FIELDS = ("url", "target", "path", "file_path", "query", "key", "command")
_KEY_VALUE_MAX = 200


def _stable_tool_key(tool_name: str, args: Any) -> str:
    """Normalized identity for a tool call (deer-flow _stable_tool_key idea).

    Buckets by the first identifying field so repeats of the same logical action
    collide even when volatile params differ. Falls back to a hash of the whole
    arg blob when no identifying field is present.
    """
    if isinstance(args, dict):
        for field in _IDENTITY_FIELDS:
            val = args.get(field)
            if val:
                return f"{tool_name}:{field}={str(val)[:_KEY_VALUE_MAX]}"
        blob = repr(sorted((k, str(v)[:_KEY_VALUE_MAX]) for k, v in args.items()))
    else:
        blob = str(args)[:_KEY_VALUE_MAX]
    digest = hashlib.sha1(blob.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"{tool_name}:{digest}"


class LoopStatus(NamedTuple):
    level: str   # "ok" | "warn" | "hard_stop"
    key: str
    count: int


class LoopMonitor:
    """Counts repeated identical tool calls within ONE agent react-loop."""

    def __init__(self, warn_count: int = 3, hard_stop_count: int = 5) -> None:
        self._warn = warn_count
        self._hard = hard_stop_count
        self._counts: dict[str, int] = {}

    def record(self, tool_name: str, args: Any) -> LoopStatus:
        key = _stable_tool_key(tool_name, args)
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        if count >= self._hard:
            level = "hard_stop"
        elif count == self._warn:
            level = "warn"     # warn exactly once, at the warn threshold
        else:
            level = "ok"
        return LoopStatus(level=level, key=key, count=count)

    def reset(self) -> None:
        self._counts.clear()
```

> **Cleanup:** chuyển `import hashlib` lên cụm import đầu file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_token_governor.py -v --no-header 2>&1 | tail -25`
Expected: PASS — toàn bộ test file (Task 1+2+3) xanh.

- [ ] **Step 5: Commit**

```bash
git add app/core/token_governor.py
git commit -m "feat(token-governor): LoopMonitor for in-agent react-loop repeat detection"
```

---

### Task 4: Config tunables

**Files:**
- Modify: `app/config.py` (thêm 7 setting, cạnh `context_compress_threshold_chars:311`)

**Interfaces:**
- Produces (trên `Settings`): `agent_token_budget: int`, `task_token_budget: int`, `token_budget_warn_ratio: float`, `token_budget_hard_stop_ratio: float`, `loop_monitor_warn_count: int`, `loop_monitor_hard_stop_count: int`, `token_estimator_use_tiktoken: bool`.

- [ ] **Step 1: Add settings** — thêm ngay sau dòng `context_compress_threshold_chars` (`app/config.py:311`):

```python
    # ── Token Governor (A budget + B estimator + C loop monitor) ──
    # Budgets are RUNAWAY CEILINGS (cost guard), not administrative limits —
    # set wide so legitimate long tasks are never cut short.
    agent_token_budget: int = 400_000            # cumulative tokens / one agent react-loop
    task_token_budget: int = 2_000_000           # cumulative tokens / whole task
    token_budget_warn_ratio: float = 0.80        # warn → ask to wrap up
    token_budget_hard_stop_ratio: float = 0.95   # hard-stop → force conclusion
    loop_monitor_warn_count: int = 3             # same tool call repeated → warn
    loop_monitor_hard_stop_count: int = 5        # same tool call repeated → hard-stop
    token_estimator_use_tiktoken: bool = True    # disable if env lacks tiktoken
```

- [ ] **Step 2: Verify settings load**

Run: `python -c "from app.config import get_settings; s=get_settings(); print(s.agent_token_budget, s.task_token_budget, s.token_budget_warn_ratio, s.loop_monitor_hard_stop_count, s.token_estimator_use_tiktoken)"`
Expected: `400000 2000000 0.8 5 True`

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat(token-governor): add 7 config tunables for budget/estimator/loop-monitor"
```

---

### Task 5: Wire per-agent budget + estimator calibration + compression upgrade

**Files:**
- Modify: `app/orchestration/agent_worker.py` — `__init__` (~205-231), react-loop top (~494-560), token record point (~685-693), `_compress_context_if_needed` (~1479-1520)

**Interfaces:**
- Consumes: `TokenEstimator`, `BudgetTracker` (Task 1-2), settings (Task 4), cơ chế wrap-up `agent_worker.py:504-524`.
- Produces: agent tự warn (chèn message một lần) và hard-stop (tước tools → final answer) theo trần token tích lũy của chính nó; `_compress_context_if_needed` dùng estimator thay `chars//4`.

- [ ] **Step 1: Khởi tạo governor trong `__init__`** — thêm sau khối `self._token_journal = TokenJournal()` (`agent_worker.py:208`):

```python
        # Token Governor (A per-agent budget + B estimator + C loop monitor).
        from app.core.token_governor import (
            TokenEstimator, BudgetTracker, LoopMonitor,
        )
        try:
            from app.config import get_settings
            _gs = get_settings()
            self._token_estimator = TokenEstimator(
                use_tiktoken=bool(_gs.token_estimator_use_tiktoken)
            )
            self._budget = BudgetTracker(
                limit=int(_gs.agent_token_budget),
                warn_ratio=float(_gs.token_budget_warn_ratio),
                hard_stop_ratio=float(_gs.token_budget_hard_stop_ratio),
            )
            self._loop_monitor = LoopMonitor(
                warn_count=int(_gs.loop_monitor_warn_count),
                hard_stop_count=int(_gs.loop_monitor_hard_stop_count),
            )
        except Exception:
            self._token_estimator = TokenEstimator(use_tiktoken=False)
            self._budget = BudgetTracker(limit=400_000)
            self._loop_monitor = LoopMonitor()
        self._budget_warn_emitted = False  # warn message injected once per run
```

- [ ] **Step 2: Reset governor state cho mỗi assignment** — thêm vào khối reset (`agent_worker.py:464-472`, cạnh `self._recent_tool_calls = []`):

```python
        self._loop_monitor.reset()
        self._budget_warn_emitted = False
```

(BudgetTracker per-agent KHÔNG reset giữa các assignment trong cùng run — nó là trần tích lũy của cả vòng đời worker; nếu muốn per-assignment thì tạo mới, nhưng spec = trần công sức nên giữ tích lũy.)

- [ ] **Step 3: Feed budget + calibrate tại điểm record usage** — sửa khối `agent_worker.py:685-693`:

```python
            # Central token accounting (by model/caller) for this worker.
            try:
                _usage = result.get("usage")
                _model = getattr(self._llm, "model", "unknown")
                self._token_journal.record(
                    _usage, model=_model, caller=self.agent_name or "unknown",
                )
                # Token Governor: feed per-agent budget + per-task budget + calibrate.
                self._budget.add(_usage)
                if _usage:
                    from app.core.execution_context import current_task_id
                    from app.core.token_governor import get_task_budget
                    _tid = current_task_id.get("")
                    if _tid:
                        try:
                            from app.config import get_settings
                            _lim = int(get_settings().task_token_budget)
                        except Exception:
                            _lim = 2_000_000
                        get_task_budget(_tid, _lim).add(_usage)
                    # Calibrate estimator: compare last estimate vs real prompt_tokens.
                    _real = int(_usage.get("prompt_tokens", 0) or 0)
                    if _real > 0 and getattr(self, "_last_estimate", 0) > 0:
                        self._token_estimator.observe(_model, self._last_estimate, _real)
            except Exception:
                pass  # accounting must never break the agent loop
```

- [ ] **Step 4: Budget warn/hard-stop ở đầu vòng react-loop** — thêm ngay sau khối compression (`agent_worker.py:548-558`, sau `messages = self._compress_context_if_needed(messages)`):

```python
            # Token Governor — per-agent cumulative-effort budget.
            _bstatus = self._budget.status()
            if _bstatus.level == "hard_stop":
                logger.warning(
                    "agent_token_budget_exhausted",
                    agent=self.agent_name, used=_bstatus.used, limit=_bstatus.limit,
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM: You have consumed ~95% of your token budget "
                        f"({_bstatus.used}/{_bstatus.limit}). Wrap up immediately: "
                        "summarize what you've accomplished and provide your final "
                        "output NOW. No more tool calls.]\n\n"
                        f"REMINDER — Your assignment was: {assignment.task_description}\n"
                        f"Success criteria: {assignment.success_criteria}"
                    ),
                })
                try:
                    wrap_result = await self._llm.chat(
                        messages=messages, temperature=0.3, max_tokens=8192,
                    )
                    final_content = wrap_result.get("content", "") or final_content
                except Exception:
                    pass
                break
            elif _bstatus.level == "warn" and not self._budget_warn_emitted:
                self._budget_warn_emitted = True
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM: You have used ~80% of your token budget. Prioritize "
                        "concluding your work; avoid expensive or redundant tool calls. "
                        "Aim to produce your final output within the next 1-2 rounds.]"
                    ),
                })
```

- [ ] **Step 5: Estimator vào compression trigger** — trong `_compress_context_if_needed` (`agent_worker.py:~1505-1520`), TRƯỚC khi trả về sớm theo threshold, ghi lại estimate dùng để calibrate. Tìm đoạn tính `conversation_chars`/threshold và thêm:

```python
        # Token Governor B: record a calibrated token estimate of the OUTBOUND
        # context so Step 3's observe() can compare against real prompt_tokens.
        try:
            _model = getattr(self._llm, "model", "")
            self._last_estimate = self._token_estimator.estimate(messages, _model)
        except Exception:
            self._last_estimate = 0
```

(Giữ nguyên logic nén hiện có — KHÔNG đổi ngữ nghĩa nén. Chỉ thêm việc ghi `_last_estimate`. `self._last_estimate` được khởi tạo lười: thêm `self._last_estimate = 0` vào `__init__` Step 1 cũng được, hoặc dựa `getattr` như Step 3 đã làm.)

- [ ] **Step 6: Thêm `self._last_estimate = 0` vào `__init__`** (Task 5 Step 1) để không phụ thuộc getattr — thêm cạnh `self._budget_warn_emitted = False`:

```python
        self._last_estimate = 0
```

- [ ] **Step 7: Smoke test import + khởi tạo worker**

Run: `python -c "from app.orchestration.agent_worker import AgentWorker; w=AgentWorker('A','developer'); print(type(w._budget).__name__, type(w._token_estimator).__name__, type(w._loop_monitor).__name__)"`
Expected: `BudgetTracker TokenEstimator LoopMonitor`

- [ ] **Step 8: Commit**

```bash
git add app/orchestration/agent_worker.py
git commit -m "feat(token-governor): wire per-agent budget + estimator calibration into AgentWorker

- per-agent BudgetTracker fed by real usage; warn (~80%) injects wrap-up hint,
  hard-stop (~95%) forces a tools-off final answer (reuses max_rounds wrap-up).
- feeds the per-task budget registry via current_task_id (no param threading).
- calibrates TokenEstimator from real prompt_tokens; estimate() now backs the
  compression trigger instead of chars//4 (semantics of compression unchanged)."
```

---

### Task 6: Wire LoopMonitor vào dispatch tool

**Files:**
- Modify: `app/orchestration/agent_worker.py` — vòng `for tc in tool_calls:` (~798-825)

**Interfaces:**
- Consumes: `self._loop_monitor` (Task 5 Step 1), `tool_name`/`tool_args` (đã parse ở `agent_worker.py:799-807`), cờ để break vòng react-loop.
- Produces: agent nhận cảnh báo lặp (warn) và bị ép kết luận (hard-stop) khi gọi lặp cùng tool/args.

- [ ] **Step 1: Thêm cờ pending hard-stop trước vòng `for tc`** — ngay sau `messages.append({"role":"assistant", ...})` (`agent_worker.py:783-787`), thêm:

```python
            _loop_hard_stop = False  # set by LoopMonitor when a tool repeats too often
```

- [ ] **Step 2: Ghi nhận loop ngay trong vòng `for tc`** — thêm sau khối parse `tool_args` (`agent_worker.py:803-807`), TRƯỚC `tool_start`:

```python
                # Token Governor C: in-agent loop detection.
                _lstatus = self._loop_monitor.record(tool_name, tool_args)
                if _lstatus.level == "warn":
                    logger.info(
                        "agent_tool_loop_warn",
                        agent=self.agent_name, tool=tool_name, count=_lstatus.count,
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[SYSTEM: You have called `{tool_name}` with nearly identical "
                            f"arguments {_lstatus.count} times. This is going in circles. "
                            "Change your approach OR conclude with the data you already have.]"
                        ),
                    })
                elif _lstatus.level == "hard_stop":
                    logger.warning(
                        "agent_tool_loop_hard_stop",
                        agent=self.agent_name, tool=tool_name, count=_lstatus.count,
                    )
                    _loop_hard_stop = True
                    break  # stop dispatching further tool calls this round
```

- [ ] **Step 3: Sau vòng `for tc`, nếu hard-stop thì ép kết luận** — tìm điểm KẾT THÚC vòng `for tc in tool_calls:` (sau khi mọi tool trong round đã xử lý, trước khi quay lại đầu `while True`). Thêm:

```python
            # Token Governor C: a tool loop hit the hard ceiling → force conclusion.
            if _loop_hard_stop:
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM: Repeated tool calls detected — you are stuck in a loop. "
                        "Stop calling tools. Summarize what you've found and provide your "
                        "final output NOW.]\n\n"
                        f"REMINDER — Your assignment was: {assignment.task_description}"
                    ),
                })
                try:
                    wrap_result = await self._llm.chat(
                        messages=messages, temperature=0.3, max_tokens=8192,
                    )
                    final_content = wrap_result.get("content", "") or final_content
                except Exception:
                    pass
                break  # exit react-loop
```

> **Lưu ý vị trí:** Step 3 block phải nằm ở thân `while True` (cùng cấp với `for tc`), sau khi vòng `for` kết thúc. Nếu codebase có xử lý sau vòng `for` (vd append tool results), đặt block NÀY sau phần đó để messages đã có đủ tool results trước khi ép kết luận.

- [ ] **Step 4: Smoke test — loop monitor đếm qua worker**

Run:
```bash
python -c "
from app.orchestration.agent_worker import AgentWorker
w = AgentWorker('A','developer')
lv = [w._loop_monitor.record('read_file', {'path':'/a'}).level for _ in range(5)]
print(lv)
"
```
Expected: `['ok', 'ok', 'warn', 'ok', 'hard_stop']`

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/agent_worker.py
git commit -m "feat(token-governor): in-agent loop detection on tool dispatch

Same tool+args repeated 3× warns the agent to change approach; 5× forces a
tools-off conclusion. Complements the assignment-level loop detection already
in nexus_orchestrator."
```

---

### Task 7: Wire per-task budget + honest termination

**Files:**
- Modify: `app/orchestration/termination.py` (thêm reason constant + cho phép honest-halt)
- Modify: `app/orchestration/nexus_orchestrator.py` — eval/ completion path (~1190 `self._progress.record`, và `_determine_completion`)
- Modify: `app/orchestration/pipeline.py` — clear task budget khi task kết thúc (best-effort)
- Test: `tests/test_token_governor.py` (thêm test honest-halt qua evaluate_completion)

**Interfaces:**
- Consumes: `get_task_budget` (Task 2), `evaluate_completion` (`termination.py:226`), `current_task_id`.
- Produces: khi per-task budget = hard_stop, orchestrator cho 1 turn synthesis rồi terminate với reason `token_budget_exhausted` (honest, progress thật).

- [ ] **Step 1: Test honest-halt cho phép qua gate (FAIL trước)** — append vào `tests/test_token_governor.py`:

```python
def test_token_budget_exhausted_is_honest_halt():
    # A 'complete' at low progress is normally blocked (CC-7), but WITH a
    # blocked justification (token_budget_exhausted) it must be allowed.
    from app.orchestration.termination import evaluate_completion
    d = evaluate_completion(
        llm_decision="complete",
        progress_pct=40,
        turn_count=8,
        progress_history=[35, 38, 40],
        prev_progress=38,
        is_security_task=False,
        has_blocked_justification=True,   # token_budget_exhausted supplies this
    )
    assert d.complete is True
```

- [ ] **Step 2: Run test — phải PASS sẵn** (chứng minh dùng cơ chế hiện có, không cần đường mới):

Run: `python -m pytest tests/test_token_governor.py -k token_budget_exhausted -v --no-header 2>&1 | head -10`
Expected: PASS — `evaluate_completion` đã hỗ trợ `has_blocked_justification`. Đây là test bảo vệ (regression guard) cho hợp đồng tích hợp.

- [ ] **Step 3: Thêm reason constant vào `termination.py`** — sau `_EVAL_PARSE_FAIL_TERMINATE_AFTER = 5` (`termination.py:39`):

```python
# Token Governor: per-task cumulative-effort ceiling. When the task has spent
# ~95% of task_token_budget, the orchestrator allows ONE synthesis turn then
# terminates honestly (reports the REAL progress %, never fakes completion).
_TOKEN_BUDGET_TERMINATE_REASON = "token_budget_exhausted"
```

- [ ] **Step 4: Đọc per-task budget trong orchestrator completion path** — trong `nexus_orchestrator.py`, tại `_determine_completion` (chỗ build `CompletionDecision`, gần `self._progress.record` ~1190). Thêm kiểm tra TRƯỚC khi gọi `evaluate_completion`:

```python
        # Token Governor: per-task cumulative-effort ceiling.
        _budget_exhausted = False
        try:
            from app.core.token_governor import get_task_budget
            from app.config import get_settings as _gs2
            _tid = state.task_id
            if _tid:
                _bt = get_task_budget(_tid, int(_gs2().task_token_budget))
                _budget_exhausted = _bt.status().level == "hard_stop"
        except Exception:
            _budget_exhausted = False

        if _budget_exhausted:
            # Allow exactly one synthesis turn, then force an HONEST halt.
            # _budget_synthesis_used tracks whether we've already granted it.
            if not getattr(self, "_budget_synthesis_used", False):
                self._budget_synthesis_used = True
                logger.warning(
                    "task_token_budget_warn_synthesis",
                    task_id=_tid, note="granting one synthesis turn before halt",
                )
                # fall through to normal evaluation (one more turn happens)
            else:
                from app.orchestration.termination import _TOKEN_BUDGET_TERMINATE_REASON
                logger.warning(
                    "task_token_budget_exhausted_halt",
                    task_id=_tid, progress_pct=progress_pct,
                )
                return CompletionDecision(
                    True, _TOKEN_BUDGET_TERMINATE_REASON,
                    {"progress_pct": progress_pct},
                )
```

> **Lưu ý:** `progress_pct`, `state`, `CompletionDecision` đã có trong scope của `_determine_completion`. `_budget_synthesis_used` lười khởi tạo qua `getattr` — không cần thêm vào `__init__`. Phải đặt block NÀY trước `return evaluate_completion(...)` để honest-halt thắng, nhưng SAU khi `progress_pct` đã được tính.

- [ ] **Step 5: Bơm directive warn vào eval-context (best-effort)** — trong `nexus_orchestrator.py` tại chỗ build context cho evaluation (gần đầu eval, nơi `results_text`/eval prompt được lắp). Thêm:

```python
        # Token Governor: surface a wrap-up directive when task budget hits warn.
        try:
            from app.core.token_governor import get_task_budget
            from app.config import get_settings as _gs3
            if state.task_id:
                _bs = get_task_budget(state.task_id, int(_gs3().task_token_budget)).status()
                if _bs.level in ("warn", "hard_stop"):
                    results_text = (
                        f"[TOKEN BUDGET {_bs.pct:.0%} USED — prioritize concluding the "
                        f"task; do NOT open new lines of investigation.]\n\n{results_text}"
                    )
        except Exception:
            pass
```

> Đặt nơi `results_text` đã được khởi tạo và trước khi gửi vào LLM eval. Nếu tên biến eval-context khác `results_text`, dùng đúng tên biến tại chỗ đó (xác minh khi implement).

- [ ] **Step 6: Clear task budget khi task kết thúc** — trong `pipeline.py`, ở nhánh kết thúc task (sau khi loop hoàn tất / terminate, nơi cleanup per-task state). Thêm best-effort:

```python
        try:
            from app.core.token_governor import clear_task_budget
            clear_task_budget(self._task_id)
        except Exception:
            pass
```

- [ ] **Step 7: Chạy lại toàn bộ test governor + import smoke**

Run: `python -m pytest tests/test_token_governor.py -v --no-header 2>&1 | tail -30`
Expected: PASS toàn bộ.

Run: `python -c "from app.orchestration.nexus_orchestrator import NexusOrchestrator; from app.orchestration.pipeline import NexusExecutionPipeline; from app.orchestration.termination import _TOKEN_BUDGET_TERMINATE_REASON; print('imports OK', _TOKEN_BUDGET_TERMINATE_REASON)"`
Expected: `imports OK token_budget_exhausted`

- [ ] **Step 8: Commit**

```bash
git add app/orchestration/termination.py app/orchestration/nexus_orchestrator.py app/orchestration/pipeline.py
git commit -m "feat(token-governor): per-task cumulative-effort ceiling with honest termination

When a task spends ~95% of task_token_budget, the orchestrator grants exactly
one synthesis turn then terminates via the existing completion gate with reason
'token_budget_exhausted' (reports real progress %, no faked completion). A warn
directive is injected into evaluation context at ~80%. Per-task budget lives in
a task_id-keyed registry fed by workers; cleared on task end."
```

---

### Task 8: Integration verification

- [ ] **Run governor test suite**: `python -m pytest tests/test_token_governor.py -v --no-header 2>&1 | tail -30` → tất cả PASS.
- [ ] **Regression — termination/orchestrator tests** (nếu tồn tại): `python -m pytest tests/test_orchestrator_completion.py tests/test_orchestration_fixes.py -v --no-header 2>&1 | tail -20` → không hỏng.
- [ ] **Import sanity**: `python -c "from app.orchestration.agent_worker import AgentWorker; from app.orchestration.turn_executor import TurnExecutor; from app.core.token_governor import TokenEstimator, BudgetTracker, LoopMonitor, get_task_budget, clear_task_budget; print('all imports OK')"`
- [ ] **No-tiktoken path**: tạm `token_estimator_use_tiktoken=False` (env) → khởi tạo worker, estimate vẫn > 0 (char fallback).
- [ ] **Final commit nếu cần** sửa lỗi tích hợp phát hiện ở trên.

---

## Self-Review (đã chạy)

**Spec coverage:** A per-agent → Task 5; A per-task + honest-halt → Task 7; B estimator+calibrate → Task 1 + Task 5 Step 3/5; B thay chars//4 ở compression → Task 5 Step 5; C → Task 3 + Task 6; config → Task 4; test → mỗi task + Task 8. Không gap.

**Placeholder scan:** không có TBD/TODO; mọi step có code thật hoặc lệnh + output kỳ vọng.

**Type consistency:** `BudgetStatus.level/used/limit/pct`, `LoopStatus.level/key/count`, `BudgetTracker.add/status/used`, `LoopMonitor.record/reset`, `TokenEstimator.estimate/observe/_raw_estimate`, `get_task_budget(task_id, limit)`, `clear_task_budget(task_id)`, reason `token_budget_exhausted` — dùng nhất quán xuyên Task 1→8.
