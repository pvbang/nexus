# Context Management Gaps Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 7 architectural weaknesses in the orchestration context management system that cause memory degradation, goal drift, and information loss during long-running tasks.

**Architecture:** Surgical changes to existing files — no new modules. Each gap gets a targeted fix in the file where the deficiency lives. Fixes are independent and can be applied in any order.

**Tech Stack:** Python 3.11+, asyncio, structlog, existing ContextLedger/AgentWorker/TurnExecutor/NexusOrchestrator classes.

---

## Gap Summary & Fix Strategy

| # | Gap | Root Cause | Fix Location |
|---|-----|-----------|--------------|
| 1 | No priority-based context eviction | Fixed-order truncation ignores relevance | `agent_worker.py` compression method |
| 2 | No on-demand ledger query for agents | Agents get static inject, can't search | `turn_executor.py` + tool registration |
| 3 | Orchestrator can't re-read ACTIVITY.md | Only sees ledger + results, misses nuance | `nexus_orchestrator.py` eval method |
| 4 | No cross-agent deconfliction in-turn | Parallel agents may duplicate work | `turn_executor.py` execution phase |
| 5 | Token counting imprecise for CJK | ~4 chars/token assumption wrong for CJK | `agent_worker.py` threshold calc |
| 6 | Memory Layers pipeline latency | Facts not available until background completes | `turn_executor.py` + `pipeline.py` |
| 7 | No goal reminder after 20+ turns | ROE not re-injected, original intent drifts | `turn_executor.py` + `nexus_orchestrator.py` |

---

## File Map

| File | Changes |
|------|---------|
| `app/orchestration/agent_worker.py` | Task 1 (relevance scoring in compression), Task 5 (adaptive token estimation) |
| `app/orchestration/turn_executor.py` | Task 2 (ledger query tool), Task 4 (deconfliction), Task 6 (eager memory), Task 7 (goal anchor) |
| `app/orchestration/nexus_orchestrator.py` | Task 3 (ACTIVITY.md selective read), Task 7 (goal anchor in eval) |
| `app/orchestration/context_ledger.py` | Task 2 (query_for_tool method) |
| `app/orchestration/pipeline.py` | Task 6 (memory pre-warm), Task 7 (goal anchor propagation) |

---

### Task 1: Priority-Based Context Eviction in Agent Compression

**Files:**
- Modify: `app/orchestration/agent_worker.py:1514-1736` (the `_compress_context_if_needed` method)

**Problem:** When agent conversation exceeds 60KB, compression uses fixed ordering: protect system + recent 20, summarize everything else. Messages containing critical discoveries (URLs found, credentials confirmed, API responses with data) are treated identically to verbose debug output.

**Fix:** Add relevance scoring before compression. Messages containing key findings (structured data, URLs, confirmation of success criteria) get promoted to "protected" alongside recent messages. Budget: protect up to 5 additional high-value messages beyond the recent-20 window.

- [ ] **Step 1: Add relevance scoring function**

In `agent_worker.py`, add after the `_CONTEXT_KEEP_RECENT_MESSAGES` constant (line 79):

```python
_MAX_PRIORITY_PROTECTED = 5  # Max additional messages to protect based on relevance

def _score_message_relevance(msg: dict[str, Any], assignment_keywords: list[str]) -> float:
    """Score a message's importance for context retention.
    
    Higher score = more likely to contain critical findings worth preserving.
    Scale: 0.0 (pure noise) to 1.0 (critical finding).
    """
    content = msg.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    if not content or len(content) < 50:
        return 0.0

    score = 0.0
    content_lower = content.lower()

    # Structural signals (data-bearing messages)
    if any(marker in content for marker in ("http://", "https://", "```")):
        score += 0.3
    if any(marker in content_lower for marker in ("found", "confirmed", "discovered", "result:")):
        score += 0.2
    if any(marker in content for marker in ("{", "[")):
        # Likely contains structured data (JSON, lists)
        score += 0.15

    # Assignment keyword overlap
    if assignment_keywords:
        hits = sum(1 for kw in assignment_keywords if kw in content_lower)
        score += min(0.3, hits * 0.1)

    # Penalize verbose/repetitive patterns
    if content_lower.startswith(("i'll ", "let me ", "now i'll ", "okay,")):
        score -= 0.1
    if "error" in content_lower and "traceback" in content_lower:
        score -= 0.15  # Full tracebacks less valuable than findings

    return max(0.0, min(1.0, score))
```

- [ ] **Step 2: Integrate scoring into compression method**

In `_compress_context_if_needed`, after the recent messages extraction (around line 1558), add priority protection:

Find the section that builds `middle_msgs` (the messages to be compressed) and inject priority scoring:

```python
# Priority-based eviction: protect high-value messages from middle section
if middle_msgs and len(middle_msgs) > _MAX_PRIORITY_PROTECTED:
    assignment_text = ""
    if len(messages) > 1:
        first_user = messages[1].get("content", "")
        if isinstance(first_user, str):
            assignment_text = first_user
    keywords = [w.lower() for w in assignment_text.split() if len(w) > 4][:20]
    
    scored = [
        (i, _score_message_relevance(m, keywords))
        for i, m in enumerate(middle_msgs)
        if m.get("role") != "tool"  # Tool results scored via their preceding assistant msg
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    
    # Protect top-N high-value messages (move to recent section)
    protect_indices = set()
    for idx, sc in scored[:_MAX_PRIORITY_PROTECTED]:
        if sc >= 0.4:  # Only protect genuinely high-value
            protect_indices.add(idx)
            # Also protect adjacent tool result if next message is tool role
            if idx + 1 < len(middle_msgs) and middle_msgs[idx + 1].get("role") == "tool":
                protect_indices.add(idx + 1)
    
    if protect_indices:
        priority_msgs = [middle_msgs[i] for i in sorted(protect_indices)]
        middle_msgs = [m for i, m in enumerate(middle_msgs) if i not in protect_indices]
        # Insert priority messages before recent messages (preserve chronological order)
        recent_msgs = priority_msgs + recent_msgs
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -k "compress" -v --no-header 2>&1 | head -30`
Expected: Existing tests pass (this is additive behavior).

- [ ] **Step 4: Commit**

```bash
git add app/orchestration/agent_worker.py
git commit -m "fix(context): add priority-based eviction to agent compression

Messages containing key findings (URLs, structured data, confirmed results)
are now protected during context compression instead of being lost with
other middle-turn messages. Up to 5 high-value messages promoted to recent."
```

---

### Task 2: On-Demand Ledger Query Tool for Agents

**Files:**
- Modify: `app/orchestration/context_ledger.py` (add `query_for_tool` method)
- Modify: `app/orchestration/turn_executor.py:822-860` (inject tool availability)
- Modify: `app/orchestration/agent_worker.py` (handle the ledger_query tool in execute loop)

**Problem:** Agents get a static ledger inject at turn start. If they need information from a specific prior turn (e.g., "what did the researcher find in turn 3?"), they can't query it — they only see the pre-built summary.

**Fix:** Add a lightweight `context_recall` pseudo-tool that agents can call during execution to query the ledger for specific information. This is handled locally (no LLM call) — instant response.

- [ ] **Step 1: Add query_for_tool method to ContextLedger**

In `context_ledger.py`, after the `build_context_for_orchestrator` method, add:

```python
def query_for_tool(self, query: str, max_chars: int = 4000) -> str:
    """Handle agent tool call to recall context from ledger.
    
    Supports queries like:
    - "turn 3" → entries from turn 3
    - "researcher" → entries from researcher role
    - "key findings" → all key_data points
    - Free text → keyword match against did_what + key_data
    """
    query_lower = query.lower().strip()
    results: list[str] = []
    
    # Parse turn number from query
    import re
    turn_match = re.search(r'turn\s*(\d+)', query_lower)
    role_match = re.search(r'(researcher|developer|analyst|writer|pentester|coordinator|critic|judge)', query_lower)
    
    if turn_match:
        turn_num = int(turn_match.group(1))
        entries = self.query(since_turn=turn_num, until_turn=turn_num, limit=20)
        if role_match:
            entries = [e for e in entries if role_match.group(1) in e.agent_role.lower()]
        for e in entries:
            line = f"T{e.turn} [{e.agent_role}]: {e.did_what}"
            if e.key_data:
                line += f"\n  Data: {'; '.join(e.key_data)}"
            if e.blockers:
                line += f"\n  Blocker: {e.blockers}"
            results.append(line)
    elif "key findings" in query_lower or "key data" in query_lower:
        kd = self.query_key_data(limit=30)
        results = kd
    elif role_match:
        entries = self.query(role=role_match.group(1), limit=15)
        for e in entries:
            line = f"T{e.turn}: {e.did_what}"
            if e.key_data:
                line += f" → {'; '.join(e.key_data[:3])}"
            results.append(line)
    else:
        # Free text search across all entries
        all_entries = self._load_cache()
        for entry in reversed(all_entries):
            match_text = f"{entry.did_what} {' '.join(entry.key_data)}".lower()
            if any(word in match_text for word in query_lower.split() if len(word) > 3):
                line = f"T{entry.turn} [{entry.agent_role}]: {entry.did_what}"
                if entry.key_data:
                    line += f"\n  → {'; '.join(entry.key_data[:3])}"
                results.append(line)
                if len(results) >= 15:
                    break
    
    if not results:
        return f"No ledger entries found for query: {query}"
    
    output = "\n".join(results)
    if len(output) > max_chars:
        output = output[:max_chars] + "\n[...truncated]"
    return output
```

- [ ] **Step 2: Register context_recall as a tool schema in TurnExecutor**

In `turn_executor.py`, add a constant after `_TURN_SAFETY_TIMEOUT_SECONDS` (line 41):

```python
_CONTEXT_RECALL_TOOL_SCHEMA = {
    "name": "context_recall",
    "description": (
        "Query the task's structured memory for information from prior turns. "
        "Use this when you need specific data from earlier work instead of guessing. "
        "Supports: 'turn N' for specific turn, role names (researcher/developer/etc), "
        "'key findings' for accumulated data, or free-text keyword search."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to recall. Examples: 'turn 3 researcher', 'key findings', 'API endpoints found'",
            }
        },
        "required": ["query"],
    },
}
```

- [ ] **Step 3: Inject context_recall tool into agent workers**

In `_execution_phase` method (line 782+), before the `worker.execute()` call, inject the tool schema:

```python
# Inject context_recall tool for on-demand ledger queries
if self._ledger and _CONTEXT_RECALL_TOOL_SCHEMA["name"] not in [
    t.get("name") for t in (worker._tool_schemas or [])
]:
    worker._tool_schemas = list(worker._tool_schemas or []) + [_CONTEXT_RECALL_TOOL_SCHEMA]
```

- [ ] **Step 4: Handle context_recall in AgentWorker execute loop**

In `agent_worker.py`, in the tool execution section of the `execute` method (around line 630-759 where tool calls are processed), add handling before the normal tool_executor dispatch:

```python
# Handle context_recall locally (no external tool call needed)
if tool_name == "context_recall":
    from app.orchestration.context_ledger import ContextLedger
    # Ledger instance passed via worker._context_ledger (set by TurnExecutor)
    ledger = getattr(self, '_context_ledger', None)
    if ledger:
        query_text = tool_input.get("query", "")
        tool_output = ledger.query_for_tool(query_text)
    else:
        tool_output = "(context_recall unavailable — no ledger attached)"
    # Skip normal tool_executor, build result directly
    ...continue with existing message-building logic using tool_output...
```

- [ ] **Step 5: Wire ledger reference to workers in TurnExecutor**

In `_execution_phase`, before `worker.execute()`:

```python
# Attach ledger reference so worker can handle context_recall locally
worker._context_ledger = self._ledger
```

- [ ] **Step 6: Commit**

```bash
git add app/orchestration/context_ledger.py app/orchestration/turn_executor.py app/orchestration/agent_worker.py
git commit -m "feat(context): add on-demand context_recall tool for agents

Agents can now query the structured ledger mid-execution instead of
relying solely on static context injection. Handles turn-specific,
role-specific, and keyword-based recall with zero LLM latency."
```

---

### Task 3: Orchestrator Selective ACTIVITY.md Read

**Files:**
- Modify: `app/orchestration/nexus_orchestrator.py` (~line 476-492, context building in evaluate_turn)

**Problem:** Orchestrator only sees ledger entries (terse 1-2 sentence summaries) + current turn results. For nuanced decisions (e.g., "did the agent actually verify this or just claim it?"), it lacks the full execution trace that lives in ACTIVITY.md.

**Fix:** When evaluation has contradictions or low-confidence signals, auto-read relevant ACTIVITY.md sections. Budget: max 8KB additional context, only triggered when needed.

- [ ] **Step 1: Add selective activity read method**

In `nexus_orchestrator.py`, add after `__init__`:

```python
def _read_activity_section(self, task_id: str, turn: int, agent_role: str = "", max_chars: int = 8000) -> str:
    """Read relevant ACTIVITY.md section for deeper evaluation context.
    
    Only called when standard ledger context is insufficient for decisions.
    Searches for specific turn/agent section in the activity log.
    """
    activity_path = _WORKSPACE_BASE / "tasks" / task_id[:8] / "_context" / "ACTIVITY.md"
    if not activity_path.exists():
        return ""
    
    try:
        content = activity_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    
    # Find the section for this turn
    section_marker = f"## Turn {turn}"
    start_idx = content.find(section_marker)
    if start_idx == -1:
        return ""
    
    # Find the end (next turn section or EOF)
    next_turn = content.find(f"## Turn {turn + 1}", start_idx + 1)
    section = content[start_idx:next_turn] if next_turn != -1 else content[start_idx:]
    
    # If filtering by agent role, narrow further
    if agent_role:
        agent_marker = f"### {agent_role}"
        agent_start = section.find(agent_marker)
        if agent_start != -1:
            next_agent = section.find("### ", agent_start + 1)
            section = section[agent_start:next_agent] if next_agent != -1 else section[agent_start:]
    
    if len(section) > max_chars:
        # Keep head + tail (conclusions at end)
        head = section[:max_chars // 2]
        tail = section[-(max_chars // 2):]
        section = f"{head}\n[...{len(section) - max_chars} chars omitted...]\n{tail}"
    
    return section
```

- [ ] **Step 2: Integrate into evaluate_turn when confidence is low**

In the `evaluate_turn` method, after the orchestrator's LLM evaluation call returns (around line 694), add a confidence check:

```python
# If orchestrator evaluation has contradictions or ambiguous progress,
# do a targeted ACTIVITY.md read for grounding
_needs_deeper_read = (
    hasattr(evaluation, 'contradictions') and evaluation.contradictions
) or (
    evaluation.progress_pct > 0
    and evaluation.progress_pct < 50
    and state.turn_count >= 5
)

if _needs_deeper_read and state.task_id:
    # Read the most recent turn's full activity for verification
    activity_detail = self._read_activity_section(
        state.task_id, state.current_turn,
        max_chars=6000,
    )
    if activity_detail:
        # Inject into evaluation context for next decision
        evaluation._activity_verification = activity_detail[:6000]
```

- [ ] **Step 3: Commit**

```bash
git add app/orchestration/nexus_orchestrator.py
git commit -m "fix(context): orchestrator reads ACTIVITY.md when evaluation ambiguous

When contradictions detected or progress stalled with ambiguous signals,
orchestrator now reads the relevant ACTIVITY.md section for verification
instead of relying solely on terse ledger summaries."
```

---

### Task 4: Cross-Agent Deconfliction in Parallel Execution

**Files:**
- Modify: `app/orchestration/turn_executor.py:782-860` (`_execution_phase` method)
- Modify: `app/orchestration/context_sharing.py` (add deconfliction method)

**Problem:** When execution_mode is "parallel", multiple agents start simultaneously with no visibility into each other's in-progress work. Two agents may independently search the same endpoint, scan the same port, or fetch the same file.

**Fix:** Add a lightweight deconfliction board that agents check before expensive tool calls. When an agent starts a tool call, it registers the action. Other agents see "(in progress by X)" for duplicate actions.

- [ ] **Step 1: Add deconfliction tracking to ContextSharing**

In `context_sharing.py`, add to the `ContextSharing` class:

```python
def __init__(self) -> None:
    self._thinkings: dict[str, AgentThinking] = {}
    self._results: dict[str, AgentResult] = {}
    self._turn_number: int = 0
    self._active_actions: dict[str, str] = {}  # action_key → agent_role

def reset_for_turn(self, turn_number: int) -> None:
    """Clear board for a new turn."""
    self._thinkings.clear()
    self._results.clear()
    self._active_actions.clear()
    self._turn_number = turn_number

def register_action(self, agent_role: str, action_key: str) -> str | None:
    """Register an action being taken by an agent.
    
    Returns None if action is free, or the role of the agent already doing it.
    action_key should be a normalized string like "web_search:target.com"
    """
    normalized = action_key.lower().strip()
    if normalized in self._active_actions:
        owner = self._active_actions[normalized]
        if owner != agent_role:
            return owner
    self._active_actions[normalized] = agent_role
    return None

def get_active_actions_summary(self, exclude_role: str = "") -> str:
    """Get summary of what other agents are currently working on."""
    actions = [
        f"- {role}: {action}"
        for action, role in self._active_actions.items()
        if role != exclude_role
    ]
    if not actions:
        return ""
    return "## In-Progress By Other Agents (avoid duplicating):\n" + "\n".join(actions[:10])
```

- [ ] **Step 2: Inject deconfliction context into parallel execution**

In `_execution_phase`, when `plan.execution_mode == "parallel"`, add deconfliction context to each agent's shared context:

```python
shared = self._context.get_shared_context_with_budget(
    assignment.agent_role, max_chars=6000
)
# Deconfliction: show what other parallel agents are doing
if plan.execution_mode == "parallel":
    deconfliction = self._context.get_active_actions_summary(
        exclude_role=assignment.agent_role
    )
    if deconfliction:
        shared = f"{deconfliction}\n\n{shared}"
```

- [ ] **Step 3: Register actions from tool calls**

In `agent_worker.py`, in the execute loop where tool calls are dispatched (around line 630), add action registration:

```python
# Register action for deconfliction (parallel mode)
_context_sharing = getattr(self, '_context_sharing', None)
if _context_sharing and tool_name:
    action_key = f"{tool_name}:{str(tool_input.get('url', tool_input.get('target', tool_input.get('path', ''))))[:100]}"
    conflict = _context_sharing.register_action(self.agent_role, action_key)
    if conflict:
        logger.debug("deconfliction_skip", agent=self.agent_name, action=action_key, owner=conflict)
```

- [ ] **Step 4: Wire context_sharing to workers**

In `_execution_phase`, before `worker.execute()`:

```python
worker._context_sharing = self._context
```

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/context_sharing.py app/orchestration/turn_executor.py app/orchestration/agent_worker.py
git commit -m "fix(context): add cross-agent deconfliction for parallel execution

Parallel agents now register their tool actions on a shared board.
Other agents see what's in-progress and avoid duplicate work (same
URL fetched, same port scanned, same file read by multiple agents)."
```

---

### Task 5: Adaptive Token Estimation for CJK/Emoji Content

**Files:**
- Modify: `app/orchestration/agent_worker.py:78` (threshold constant and compression trigger)

**Problem:** Token counting uses `~4 chars/token` assumption. CJK characters are typically 1-2 tokens per character, and emoji can be 2-3 tokens. A 60KB conversation in Chinese is actually ~30K-45K tokens (vs expected 15K), risking context overflow.

**Fix:** Detect content character distribution and adjust the threshold dynamically. Simple heuristic: sample first 1000 chars, if >30% are non-ASCII, use 2.5 chars/token instead of 4.

- [ ] **Step 1: Add adaptive threshold calculation**

In `agent_worker.py`, after `_CONTEXT_COMPRESS_THRESHOLD_CHARS` (line 78):

```python
def _estimate_chars_per_token(content_sample: str) -> float:
    """Estimate chars-per-token ratio based on content character distribution.
    
    Standard English: ~4 chars/token
    CJK-heavy content: ~2.0-2.5 chars/token
    Mixed content: ~3.0 chars/token
    """
    if not content_sample:
        return 4.0
    
    sample = content_sample[:2000]
    non_ascii = sum(1 for c in sample if ord(c) > 127)
    ratio = non_ascii / len(sample) if sample else 0
    
    if ratio > 0.5:
        return 2.0  # Heavy CJK/non-Latin
    elif ratio > 0.3:
        return 2.5  # Mixed
    elif ratio > 0.15:
        return 3.0  # Some non-ASCII
    return 4.0  # Standard English/Latin
```

- [ ] **Step 2: Use adaptive threshold in compression trigger**

In `_compress_context_if_needed`, replace the static threshold check:

```python
# Adaptive threshold based on content character distribution
_sample = "".join(
    m.get("content", "")[:200] if isinstance(m.get("content"), str) else ""
    for m in messages[1:6]
)
_chars_per_token = _estimate_chars_per_token(_sample)
_adaptive_threshold = int(_CONTEXT_COMPRESS_THRESHOLD_CHARS * (_chars_per_token / 4.0))

conversation_chars = sum(
    len(m.get("content", "")) if isinstance(m.get("content"), str)
    else sum(len(b.get("text", "")) for b in m.get("content", []) if isinstance(b, dict))
    for m in messages[1:]
)

if not force and conversation_chars <= _adaptive_threshold:
    return messages
```

- [ ] **Step 3: Commit**

```bash
git add app/orchestration/agent_worker.py
git commit -m "fix(context): adaptive token estimation for CJK/emoji content

Compression threshold now adjusts based on content character distribution.
CJK-heavy conversations trigger compression earlier (at ~30KB instead of
60KB) to prevent context window overflow."
```

---

### Task 6: Eager Memory Layer Facts Injection

**Files:**
- Modify: `app/orchestration/turn_executor.py:154-228` (context building section)
- Modify: `app/orchestration/pipeline.py:303-323` (memory refresh interval)

**Problem:** Memory Layers (L1 extraction) only run every 5 turns with 90s delay for L2. Facts discovered in turns 1-4 aren't in memory recall until turn 5+. Agent in turn 3 can't benefit from facts extracted from turn 1 conversation.

**Fix:** Two changes: (1) Reduce memory refresh interval from 5 to 3 turns. (2) In TurnExecutor, inject raw recent conversation facts directly from collected_data_cache as "inline memory" — bypassing the layer pipeline for immediate availability.

- [ ] **Step 1: Reduce memory refresh interval**

In `pipeline.py`, change line 303:

```python
_MEMORY_REFRESH_INTERVAL = 3  # Re-fetch memory every 3 turns (was 5)
```

- [ ] **Step 2: Add inline fact extraction from recent turn results**

In `turn_executor.py`, in the context-building section (after collected data injection, around line 228), add:

```python
# GAP-6 FIX: Eager inline facts from recent turns.
# Memory Layers pipeline has latency (L1 every 5 turns + 90s L2 delay).
# Extract key facts directly from recent turn results for immediate use.
if state.turns_completed and _ctx_remaining > 500:
    _recent_facts: list[str] = []
    for tr in state.turns_completed[-3:]:  # Last 3 turns only
        for ar in tr.agent_results:
            if ar.success and ar.output:
                # Extract factual statements (lines starting with bullet or containing ":")
                for line in ar.output.split("\n"):
                    line = line.strip()
                    if (
                        len(line) > 30
                        and (line.startswith(("-", "•", "*")) or ": " in line)
                        and not line.startswith(("I ", "Let ", "Now ", "I'll"))
                    ):
                        _recent_facts.append(line[:200])
                        if len(_recent_facts) >= 10:
                            break
                if len(_recent_facts) >= 10:
                    break
        if len(_recent_facts) >= 10:
            break
    
    if _recent_facts:
        _facts_cap = min(2000, _ctx_remaining)
        facts_section = "## Recent Facts (from prior turns, use directly):\n"
        for fact in _recent_facts[:8]:
            entry = f"  {fact}\n"
            if len(facts_section) + len(entry) > _facts_cap:
                break
            facts_section += entry
        previous_context += facts_section
        _ctx_remaining -= len(facts_section)
```

- [ ] **Step 3: Commit**

```bash
git add app/orchestration/turn_executor.py app/orchestration/pipeline.py
git commit -m "fix(context): eager memory injection bypasses layer pipeline latency

Key facts from recent turn results now injected directly into agent context
without waiting for Memory Layers L1 extraction (which has 5-turn + 90s delay).
Also reduces memory refresh interval from 5 to 3 turns."
```

---

### Task 7: Goal Anchor Mechanism for Long-Running Tasks

**Files:**
- Modify: `app/orchestration/turn_executor.py:154-228` (context building)
- Modify: `app/orchestration/nexus_orchestrator.py` (evaluation prompt building)
- Modify: `app/orchestration/pipeline.py` (pass original message to turn_executor)

**Problem:** After 20+ turns, the original user intent can drift. ROE.md is injected (8KB cap) but may be stale relative to evolved understanding. Agents see TODO.md (progress tracker) but lose sight of the WHY behind the task.

**Fix:** Add a persistent "Goal Anchor" — a compact 3-line distillation of the original user intent + success criteria. Injected at EVERY turn (including in orchestrator evaluation). Never compressed, never truncated. Max 500 chars.

- [ ] **Step 1: Generate goal anchor during planning**

In `pipeline.py`, after `orchestrator.write_task_brief()` (around line 229), add goal anchor generation:

```python
# Generate Goal Anchor — persistent compact reminder of user intent
# This survives all compression and is injected every single turn.
_goal_anchor = (
    f"🎯 GOAL ANCHOR (NEVER deviate from this):\n"
    f"USER ASKED: {message[:200]}\n"
    f"SUCCESS WHEN: {plan_data.get('success_criteria', plan_data.get('intent_analysis', message[:100]))[:200]}\n"
)
# Persist to file for resume scenarios
_anchor_path = (
    _WORKSPACE_BASE / "tasks" / self._task_id[:8] / "_context" / "GOAL_ANCHOR.txt"
)
_anchor_path.parent.mkdir(parents=True, exist_ok=True)
_anchor_path.write_text(_goal_anchor, encoding="utf-8")
```

- [ ] **Step 2: Pass goal anchor to TurnExecutor**

In `pipeline.py`, when creating `TurnExecutor` (line 272), add `goal_anchor` parameter:

```python
turn_executor = TurnExecutor(
    workers=workers,
    context_sharing=context_sharing,
    activity_logger=activity_logger,
    context_ledger=context_ledger,
    event_callback=self._event_callback,
    cancel_check=self._cancel_check,
    total_turns_estimate=total_turns_estimate,
    memory_context=memory_context,
    task_id=self._task_id,
    goal_anchor=_goal_anchor,  # NEW
)
```

- [ ] **Step 3: Accept and inject goal_anchor in TurnExecutor**

In `turn_executor.py`, add `goal_anchor` parameter to `__init__`:

```python
def __init__(
    self,
    workers: dict[str, AgentWorker],
    context_sharing: ContextSharing,
    activity_logger: ActivityLogger,
    context_ledger: Optional[ContextLedger] = None,
    event_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
    total_turns_estimate: int = 5,
    memory_context: str = "",
    task_id: str = "",
    goal_anchor: str = "",  # NEW
) -> None:
    ...
    self._goal_anchor = goal_anchor
```

In `execute_turn`, inject goal_anchor at the TOP of previous_context (before TODO.md):

```python
# Goal Anchor: ALWAYS first, NEVER compressed, NEVER truncated
if self._goal_anchor:
    previous_context = f"{self._goal_anchor}\n\n{previous_context}"
    # Don't deduct from _ctx_remaining — goal anchor is sacred
```

- [ ] **Step 4: Inject goal anchor into orchestrator evaluation**

In `nexus_orchestrator.py`, in `evaluate_turn` (around line 476 where context is built), load and inject the goal anchor:

```python
# Load Goal Anchor for evaluation grounding
_anchor_path = _WORKSPACE_BASE / "tasks" / state.task_id[:8] / "_context" / "GOAL_ANCHOR.txt"
_goal_anchor_text = ""
if _anchor_path.exists():
    try:
        _goal_anchor_text = _anchor_path.read_text(encoding="utf-8")[:500]
    except Exception:
        pass

# Prepend to evaluation context so orchestrator always evaluates against original intent
if _goal_anchor_text:
    results_text = f"{_goal_anchor_text}\n\n{results_text}"
```

- [ ] **Step 5: Load goal anchor on resume**

In `pipeline.py`, in the resume path (around line 284), load existing anchor:

```python
if _is_resume:
    _anchor_path = _WORKSPACE_BASE / "tasks" / self._task_id[:8] / "_context" / "GOAL_ANCHOR.txt"
    if _anchor_path.exists():
        try:
            _goal_anchor = _anchor_path.read_text(encoding="utf-8")
        except Exception:
            _goal_anchor = f"🎯 GOAL ANCHOR: {message[:300]}\n"
    else:
        _goal_anchor = f"🎯 GOAL ANCHOR: {message[:300]}\n"
```

- [ ] **Step 6: Commit**

```bash
git add app/orchestration/pipeline.py app/orchestration/turn_executor.py app/orchestration/nexus_orchestrator.py
git commit -m "feat(context): add Goal Anchor for long-running task coherence

Persistent 3-line distillation of user intent + success criteria injected
at every turn and every orchestrator evaluation. Never compressed, never
truncated. Prevents goal drift after 20+ turns of execution."
```

---

## Integration Verification

After all 7 tasks are complete:

- [ ] **Run full test suite**: `python -m pytest tests/ -v --tb=short 2>&1 | tail -20`
- [ ] **Check import errors**: `python -c "from app.orchestration.pipeline import NexusExecutionPipeline; print('OK')"`
- [ ] **Verify no circular imports**: `python -c "from app.orchestration.turn_executor import TurnExecutor; from app.orchestration.agent_worker import AgentWorker; print('All imports OK')"`
- [ ] **Final commit (if needed)**: Fix any integration issues found above.

---

## Risk Assessment

| Task | Risk | Mitigation |
|------|------|-----------|
| 1 (Priority eviction) | Scoring too aggressive → important msgs evicted | Threshold 0.4 + max 5 protected = conservative |
| 2 (Ledger query tool) | Agents overuse tool → waste rounds | Only returns 4KB max, free text is fast |
| 3 (ACTIVITY read) | Large file read slows evaluation | Only triggered on contradictions, capped 8KB |
| 4 (Deconfliction) | False positives block legitimate parallel work | Only logs, doesn't block — agents see info, decide themselves |
| 5 (Token estimation) | Heuristic wrong for edge cases | Conservative: worst case compresses slightly early |
| 6 (Eager facts) | Noisy facts injected | Filters: >30 chars, structural markers, max 8 items |
| 7 (Goal anchor) | Extra 500 chars per turn | Negligible vs 60KB budget, provides critical grounding |
