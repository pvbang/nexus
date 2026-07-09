# MekongAI Nexus — Agent Context Pipeline (Chi Tiet Day Du)

Tai lieu nay mo ta TOAN BO pipeline phan bo context cho cac agent trong qua trinh xu ly, bao gom prompt, context, va du lieu moi loai agent nhan duoc tai tung giai doan.

---

## Muc Luc

1. [Tong Quan Pipeline](#1-tong-quan-pipeline)
2. [Phase 0: Smart Context Aggregation](#2-phase-0-smart-context-aggregation)
3. [Phase 1: Orchestrator Planning](#3-phase-1-orchestrator-planning)
4. [Phase 2: Task Documents Generation](#4-phase-2-task-documents-generation)
5. [Phase 3: Turn-Based Execution Loop](#5-phase-3-turn-based-execution-loop)
6. [Phase 3A: Thinking Phase](#6-phase-3a-thinking-phase)
7. [Phase 3B: Discussion Phase](#7-phase-3b-discussion-phase)
8. [Phase 3C: Execution Phase](#8-phase-3c-execution-phase)
9. [Phase 4: Evaluation](#9-phase-4-evaluation)
10. [Phase 5: Evolution Agent](#10-phase-5-evolution-agent)
11. [Phase 6: Final Report Generation](#11-phase-6-final-report-generation)
12. [Phase 7: User Answer Summarization](#12-phase-7-user-answer-summarization)
13. [Context Sharing & Budget Management](#13-context-sharing--budget-management)
14. [Context Compression Strategy](#14-context-compression-strategy)
15. [Post-Processing: XSkill & Graph RAG](#15-post-processing-xskill--graph-rag)

---

## 1. Tong Quan Pipeline

```
User Message
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ Phase 0: Smart Context Aggregation                  │
│ (Memory Layers + KG + RAG + XSkill → parallel)      │
└──────────────────────┬──────────────────────────────┘
                       │ enriched memory_context
                       ▼
┌─────────────────────────────────────────────────────┐
│ Phase 1: Nexus Orchestrator PLANNING                │
│ (Select agents, create execution plan)              │
└──────────────────────┬──────────────────────────────┘
                       │ TurnPlan + plan_data
                       ▼
┌─────────────────────────────────────────────────────┐
│ Phase 2: Task Documents (TASK_BRIEF, ROE, TODO)     │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ Phase 3: TURN LOOP (repeat until complete)          │
│                                                     │
│  ┌─ 3A: Thinking (parallel) ──────────────────────┐ │
│  │  All agents analyze assignment → JSON output   │ │
│  └────────────────────────────────────────────────┘ │
│           │ ContextSharing Board                    │
│  ┌─ 3B: Discussion (parallel) ────────────────────┐ │
│  │  Challenge/Support/Rebuttal → Synthesis        │ │
│  └────────────────────────────────────────────────┘ │
│           │ ContextSharing Board                    │
│  ┌─ 3C: Execution (parallel/sequential) ──────────┐ │
│  │  Tool-calling loop (unlimited rounds)          │ │
│  └────────────────────────────────────────────────┘ │
│           │                                         │
│  ┌─ Phase 4: Evaluation ──────────────────────────┐ │
│  │  Orchestrator evaluates → continue/complete    │ │
│  └────────────────────────────────────────────────┘ │
│           │                                         │
│  ┌─ Phase 5: Evolution Agent ─────────────────────┐ │
│  │  Quality analysis → blind spots, suggestions   │ │
│  └────────────────────────────────────────────────┘ │
└──────────────────────┬──────────────────────────────┘
                       │ state.is_complete = True
                       ▼
┌─────────────────────────────────────────────────────┐
│ Phase 6: Final Report Generation                    │
│ (TASK_BRIEF + ROE + ACTIVITY → FINAL_REPORT.md)    │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ Phase 7: Summarize for User                         │
│ (FINAL_REPORT → user-facing answer)                 │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ Post-Processing: XSkill Accumulate + Graph RAG      │
│ (Fire-and-forget knowledge persistence)             │
└─────────────────────────────────────────────────────┘
```

---

## 2. Phase 0: Smart Context Aggregation

**File:** `app/core/smart_context.py`, `app/orchestration/pipeline.py:_aggregate_context()`

**Muc dich:** Thu thap context tu nhieu nguon song song, scoring, dedup, budget enforcement.

### Cac nguon context (parallel fetch):

| Source | Priority | Timeout | Noi dung |
|--------|----------|---------|----------|
| `memory_layers` | 0.55 | 3s | Recall tu L0→L3 memory system (persona, episodic, instructions) |
| `knowledge_graph` | 0.6 | 3s | Entity search tu PostgreSQL KG (max 5 nodes, 1-hop relations) |
| `rag` | 0.7 | 3s | Hybrid retrieval: vector + FTS + graph fusion (top 5, max 4000 chars) |
| `xskill` | 0.4 | 3s | Prior knowledge tu continual learning engine (max 3000 chars) |

### Scoring Algorithm:
```
score = (keyword_overlap * 0.4) + (source_priority * 0.4) + (length_factor * 0.2)
```

### Budget:
- Default: `context_budget_complex` tu settings (thong thuong ~8000 chars)
- Dedup threshold: 60% token overlap → remove lower-scored piece
- Greedy top-K selection until budget exhausted

### Output Format:
```
[memory_layers] <content from memory>

[knowledge_graph] <content from KG>

[rag] <content from RAG retrieval>
```

---

## 3. Phase 1: Orchestrator Planning

**File:** `app/orchestration/nexus_orchestrator.py:plan_task()`

### Nexus Orchestrator nhan:

| Input | Noi dung | Kich thuoc |
|-------|----------|------------|
| System Prompt | `build_orchestrator_planning_prompt()` | ~3000-5000 chars |
| User Message | Original user message | Khong gioi han |
| Agent Catalog | Danh sach 50+ agent templates formatted | ~2000 chars |
| Memory Context | Output tu Phase 0 (enriched) | ~8000 chars max |

### System Prompt cua Orchestrator bao gom:

1. **Role definition**: "You are Nexus Orchestrator — the master coordinator"
2. **Capabilities**: UNDERSTAND, PLAN, SELECT, ASSIGN, COORDINATE
3. **Agent Catalog**: Toan bo 50+ agent templates voi role, avatar, description, expertise
4. **Planning Rules**: 9 rules ve team size, turn count, dependencies, execution mode
5. **Thoroughness Mandate**: "Solved COMPREHENSIVELY — no shortcuts"
6. **Execution Patterns**: Standard, Generation-Debate-Evolution, Deep Research
7. **Quality Standards** (ORCHESTRATOR_PRODUCTION_MANDATE):
   - Understand actual intent
   - Match solution scope to request
   - Verify deliverables functional
   - DO NOT impose min turns / tech choices / unwanted features
8. **Output Format**: Strict JSON schema required

### LLM Parameters:
- Temperature: 0.3
- Max tokens: 8192

### Output (plan_data JSON):
```json
{
  "intent_analysis": "...",
  "task_type": "research|coding|analysis|creative|security|general",
  "complexity": "simple|medium|complex|expert",
  "selected_agents": [...],
  "execution_plan": [...],
  "constraints": [...],
  "language": "vi|en|auto"
}
```

---

## 4. Phase 2: Task Documents Generation

**File:** `app/orchestration/nexus_orchestrator.py:write_task_brief()`, `write_roe()`, `write_todo()`

Ba file duoc tao va luu vao `workspace_data/tasks/{task_id[:8]}/_context/`:

### TASK_BRIEF.md
- User Request (original message)
- Intent Analysis
- Constraints
- Team Composition (table)
- Execution Plan (numbered list)

### ROE.md (Rules of Engagement)
- Task Type
- Response Language
- Quality Standards (evidence-backed, no fabrication, comprehensive, excellence)
- Boundaries (constraints)
- Tool Usage Rules
- Collaboration Rules
- **Neu la security/pentest task:** Su dung `RoeGenerator` voi ATT&CK mappings, audit trail requirements
- **Neu la intelligence task:** Audit trail requirements, NATO confidence grading

### TODO.md
- Team listing
- Execution Plan voi checkboxes per assignment
- Updated after each turn voi progress %

---

## 5. Phase 3: Turn-Based Execution Loop

**File:** `app/orchestration/turn_executor.py:execute_turn()`

### Moi turn thuc hien 3 phase:

```
Reset ContextSharing Board
    │
    ├── Phase A: THINKING (parallel, 5min timeout)
    │       └── Tat ca agent phan tich assignment
    │
    ├── Phase B: DISCUSSION (parallel, 5min timeout)  
    │       └── Challenge → Rebuttal → Synthesis
    │
    └── Phase C: EXECUTION (parallel/sequential, 45min timeout)
            └── Tool-calling loop per agent
```

### Context truyen vao TurnExecutor:

| Field | Noi dung | Nguon |
|-------|----------|-------|
| `workers` | Dict[role, AgentWorker] — cac agent instance | Pipeline._create_workers() |
| `context_sharing` | Board chia se context giua agents | ContextSharing() |
| `activity_logger` | Log toan bo hoat dong | ActivityLogger(task_id) |
| `memory_context` | Enriched context tu Phase 0 | Pipeline |
| `task_id` | Task identifier | Pipeline |
| `roe_context` | Noi dung ROE.md (max 8000 chars) | _load_roe() |
| `workspace_path` | Path toi `_work/` directory | _resolve_workspace() |

### Previous Context Construction (per turn):
```python
previous_context = state.get_previous_context_summary(max_chars=6000)
# Recent turns (last 2): detailed agent outputs (1KB each)
# Older turns: brief summaries (objective + evaluation only)
# Total budget: ~6KB

if memory_context:
    previous_context = f"## Memory Context\n{memory_context[:4000]}\n\n{previous_context}"

if state.collected_data_cache:  # Data tu crashed agents
    previous_context += "\n## DATA DA THU THAP (khong can fetch lai):\n..."
```

---

## 6. Phase 3A: Thinking Phase

**File:** `app/orchestration/agent_worker.py:think()`

### Moi Agent Worker nhan:

#### System Prompt (`build_agent_thinking_prompt()`):

```
You are **{agent_name}**, a specialist in **{agent_role}**.

## YOUR IDENTITY
- Personality: {personality}
- Expertise: {expertise_str}
- Thinking Style: {thinking_style}

## YOUR ASSIGNMENT THIS TURN
{assignment.task_description}

## SUCCESS CRITERIA
{assignment.success_criteria}

## CONTEXT FROM OTHER AGENTS (this turn)
{shared_context}  ← tu ContextSharing board (hoac "(No other agents...)")

## PREVIOUS TURNS SUMMARY
{previous_context}  ← 6KB max, compressed history

## RULES OF ENGAGEMENT (Summary)
{roe_context[:3000]}

## INSTRUCTIONS
1. Analyze what needs to be done
2. Identify potential challenges or risks
3. If you disagree with another agent's approach, state WHY
4. If you support another agent's idea, explain HOW
5. Outline your execution strategy

## THOROUGHNESS MINDSET
- Think deeply, consider edge cases, alternative approaches
- Plan to verify findings, not just gather them
```

#### User Message:
```
Produce your thinking about this assignment: {assignment.task_description}
```

### LLM Parameters:
- Temperature: 0.4
- Max tokens: 2048
- No tools available

### Output (JSON):
```json
{
  "thinking": "Detailed reasoning (2-5 sentences)",
  "strategy": "Execution plan (brief)",
  "challenges": ["challenge 1", "challenge 2"],
  "supports": ["support 1", "support 2"],
  "tools_planned": ["web_search", "execute_code"]
}
```

### Sau khi thinking xong:
- Ket qua duoc dang len `ContextSharing.add_thinking(role, thinking)`
- Toan bo thinking cua cac agents visible cho nhau (full content)
- Log vao `ActivityLogger.log_agent_thinking()`

---

## 7. Phase 3B: Discussion Phase

**File:** `app/orchestration/turn_executor.py:_discussion_phase()`, `agent_worker.py:discuss()`

### Chi chay khi co >= 2 agents trong turn

### Round 1: Challenge + Support

#### Moi Agent nhan:

**System Prompt:**
```
You are {agent_name} ({agent_role}).
Your expertise: {expertise_list}
Your assignment: {own_assignment.task_description}

Other agents have shared their thinking.
If you have challenges or supports, state them briefly.
Respond with JSON: {"challenges": [...], "supports": [...]}
Only include items where you have genuine expert insight.
Empty arrays are fine if you have nothing to add.
```

**User Message:**
```
Other agents' thinking:
- **Agent A** (role_a): {thinking_a}
- **Agent B** (role_b): {thinking_b}
...
```

### LLM Parameters:
- Temperature: 0.3
- Max tokens: 1024
- No tools

### Round 2: Rebuttal (chi cho agents bi challenged)

**System Prompt:**
```
You are {agent_name} ({agent_role}).
Your expertise: {expertise_list}
Your original plan: {own_thinking[:1500]}

Other agents have CHALLENGED aspects of your plan.
You have ONE rebuttal. Either:
1. DEFEND your approach with reasoning, OR
2. ADJUST your plan based on valid criticism.

Respond with JSON: {"rebuttals": [...], "adjustments": [...]}
```

**User Message:**
```
Challenges to your plan:
- [from Agent X] challenge_text_1
- [from Agent Y] challenge_text_2
...
```

### LLM Parameters:
- Temperature: 0.3
- Max tokens: 1024

### Synthesis:
- All challenges/supports/rebuttals duoc merge vao ContextSharing
- `_synthesize_discussion()` tao structured summary:
  - Agreed Approaches
  - Unresolved Disagreements
  - Key Insights from Debate
- Summary duoc publish nhu `_discussion_synthesis` entry tren board

---

## 8. Phase 3C: Execution Phase

**File:** `app/orchestration/agent_worker.py:execute()`

### Day la phase CHINH — agents goi tools, lam viec thuc su.

### Moi Agent Worker nhan:

#### System Prompt (`build_agent_execution_prompt()`):

```
You are **{agent_name}**, a specialist in **{agent_role}**.

## YOUR IDENTITY
- Personality: {personality}
- Expertise: {expertise_str}
- Thinking Style: {thinking_style}

## YOUR ASSIGNMENT
{assignment.task_description}

## SUCCESS CRITERIA
{assignment.success_criteria}

## RULES OF ENGAGEMENT
{roe_context}

## YOUR THINKING (from earlier this turn)
{own_thinking}  ← thinking output tu Phase 3A

## CONTEXT FROM TEAM
{shared_context}  ← thinking + results cua cac agent khac (capped 5KB/agent)

## PREVIOUS TURNS
{previous_turns}  ← 6KB compressed history + memory context

## WORKSPACE
- Working directory: {workspace_path}
- Structure: _work/ for files, ../_context/ for docs
- Use activity_search to retrieve truncated outputs

## EXECUTION RULES
1. Focus ONLY on assigned task
2. Use tools actively
3. Be thorough — over-deliver
4. Save artifacts to _work/
5. Clear summary when done
6. If tool fails, try alternative
7. Do NOT fabricate data

## THOROUGHNESS MANDATE
- THOROUGH. Use as many tools as needed. Do NOT stop early.
- After initial results: "What else can I do?"
- Verify from multiple sources when possible
- NEVER settle for "good enough"

## CONTEXT RECOVERY
- activity_search(turn=3)
- activity_search(keyword="...", turn=3)
- task_doc_read(doc="ACTIVITY")
- task_doc_search(query="...")

## SELF-EVOLVE (TOOLS/SKILLS)
- scaffold_custom_tool_module(...)
- load_tool_module(module="...")
- scaffold_skill(...) + reload_skills()

## EMAIL (IMAP/SMTP)
- fetch_emails(...), send_email(...), email_health_check()

## PUBLIC LINKS
- expose_port(port=8000)
- setup_public_inbox(...)

## AVAILABLE TOOLS
{tool_catalog}  ← 50 tools max, format: "- `tool_name`: description"

## Execution Standards (AGENT_EXECUTION_PRODUCTION_MANDATE)
- Production-quality, no placeholders, no TODOs
- Code executable and tested
- Intelligence Doctrine: TRUST NOTHING — VERIFY EVERYTHING
- Confidence grading: Confirmed > Probable > Possible > Unconfirmed > Unknown
```

#### User Message:
```
Execute your assignment: {assignment.task_description}

Success criteria: {assignment.success_criteria}

Use tools as needed. When finished, provide your final output.
```

### LLM Parameters:
- Temperature: 0.4
- Max tokens: 16384
- Tools: full tool_schemas array (30-50 tools)

### Tool-Calling Loop:
```
while True:
    ├── Check: max_rounds reached? → inject wrap-up message → last call without tools
    ├── Check: safety ceiling (100K rounds)? → break
    ├── Check: cancelled? → break
    ├── Smart context compression (if > 80KB)
    ├── LLM call with tools
    │   ├── TimeoutError → compress & retry (max 3 retries)
    │   ├── Context overflow → compress & retry once
    │   ├── Orphan tool_use → fix & retry
    │   └── Generic error → call without tools → break
    ├── No tool_calls? → final_content = content → break
    ├── For each tool_call:
    │   ├── Parse arguments
    │   ├── Execute tool (600s timeout per tool)
    │   ├── Record full output in ToolCall record
    │   ├── Cap output for LLM messages (20KB max)
    │   │   └── If truncated: append "[OUTPUT TRUNCATED]" + activity_search hint
    │   └── Append tool result to messages
    ├── Stuck detection (5 patterns):
    │   ├── Same tool+args 15x → warning
    │   ├── Same tool 100x in a row → warning
    │   ├── Only 1 tool for 100 calls → warning
    │   ├── Alternating A↔B → warning
    │   └── Last 20 all failed → warning
    │   └── After MAX_STUCK_ESCALATIONS (100): force-stop
    └── round_num++
```

### Tool Output Management:

| Destination | Kich thuoc | Muc dich |
|-------------|-----------|----------|
| ToolCall.output | FULL (khong gioi han) | Luu vao ACTIVITY.md |
| LLM messages | Max 20KB/tool result | Giu context window nho |
| ContextSharing (cho agent khac) | Max 5KB/agent | Budget control |
| Orchestrator evaluation | Max 8KB/agent + tool summaries | Evaluation call |
| Frontend event | Max 500 chars | Real-time display |

### Execution Modes:

- **Parallel** (default): Tat ca agents chay dong thoi, throttled boi `max_parallel_llm_agents` semaphore (default 3)
- **Sequential**: Chay theo dependency order (topological sort), ket qua agent truoc visible cho agent sau qua ContextSharing

---

## 9. Phase 4: Evaluation

**File:** `app/orchestration/nexus_orchestrator.py:evaluate_turn()`

### Orchestrator nhan:

#### System Prompt (`build_orchestrator_evaluation_prompt()`):
```
You are **Nexus Orchestrator** evaluating the results of a completed turn.

## YOUR TASK
Assess agents completed assignments, identify gaps, decide continue/complete.

## EVALUATION CRITERIA
1. Did each agent complete their specific assignment?
2. Is information gathered accurate and sufficient?
3. Are there contradictions between agents' findings?
4. What is still MISSING?
5. Has the turn's milestone been achieved?

## BE STRICT — DEMAND EXCELLENCE
- Do NOT mark "complete" unless EXCELLENT
- Partial answers / surface-level = NEVER complete
- Look for: missing details, unverified claims, unexplored angles

## DECISION RULES
- Complete ONLY when ALL requirements FULLY satisfied
- Progress >= 95% and gaps trivial → complete
- Agent failed → reassign next turn
- Agents contradict → verify next turn
- NEVER mark complete below 80% progress

## PRODUCTION_EVALUATION_CRITERIA
- Evaluate against SPECIFIC objectives in TASK_BRIEF and ROE
- DO NOT penalize for not deploying unless requested
- DO NOT penalize for tech choices that work
```

#### User Message:
```
## Original User Request
{user_message}

## Turn {N} Plan (what was assigned)
**Objective**: {turn_plan.objective}
**Milestone**: {turn_plan.milestone}
- **Agent A** (role_a)
  - Task: {task_description}
  - Success criteria: {success_criteria}
...

## Turn {N} Results
### Agent A (role_a)
**Tools used** (X):
  - ✅ `web_search` result_hint
  - ❌ `execute_code` error_hint
**Output**: {output[:8000]}  ← capped at 8KB per agent

## Progress So Far
Turns completed: X
Total agents active: Y
Previous focus: ...

## Evolution Agent Analysis [severity_label]
**Quality trajectory:** ...
**Blind spots:** ...
**Challenge questions:** ...
**Required improvements:** ...
```

### LLM Parameters:
- Temperature: 0.2
- Max tokens: 3072

### Output (JSON):
```json
{
  "agent_evaluations": [{"agent": "...", "role": "...", "completed": true, "quality": "high", "notes": "..."}],
  "contradictions": ["..."],
  "missing_items": ["..."],
  "progress_pct": 75,
  "milestone_achieved": true,
  "decision": "continue|complete",
  "reasoning": "...",
  "next_turn_focus": "...",
  "next_assignments": [{"agent_role": "...", "task": "...", "success_criteria": "..."}]
}
```

### Post-Processing Logic:
1. **Progress floor**: progress_pct >= prev - 10 (khong giam qua 10%)
2. **Completion guardrails**:
   - progress < 80% → override to `continue` (bat ke LLM noi gi)
   - progress_delta > 5% AND progress < 95% → continue (van tang)
   - _MAX_TURNS (999999) → force complete
3. **Credential check**: Neu agents co "Missing credentials" error → force continue
4. **Partial data recovery**: Extract successful tool outputs tu crashed agents → inject next turn
5. **Failure tracking**: 2+ consecutive failures → inject warning vao assignment

---

## 10. Phase 5: Evolution Agent

**File:** `app/orchestration/evolution_agent.py`

### Chay SAU moi turn, TRUOC evaluation.

### Input:
- `turn_result`: Ket qua cua turn vua hoan thanh
- `state`: Trang thai toan bo task (turns completed, total agents, etc.)

### Output (EvolutionInsight):
```python
@dataclass
class EvolutionInsight:
    improvement_suggestions: list[str]  # max 3
    blind_spots: list[str]              # max 3
    quality_trajectory: str             # "improving|stable|degrading"
    challenge_questions: list[str]      # max 2
    severity_score: float               # 0.0 - 1.0
    should_inject: bool                 # True nếu severity >= threshold
```

### Tac dong:
- Neu `should_inject = True` va `severity_score >= 0.8`: Orchestrator BI CAM mark complete
- Evolution insights duoc inject vao Orchestrator evaluation message
- Enrich evaluation result voi additional context

---

## 11. Phase 6: Final Report Generation

**File:** `app/orchestration/final_report.py`

### Input files (doc toan bo, KHONG gioi han):
- `TASK_BRIEF.md` — full
- `ROE.md` — full
- `TODO.md` — full
- `ACTIVITY.md` — full (co the rat lon)

### Process:
1. **Header synthesis**: TASK_BRIEF + ROE + TODO → Executive Summary
2. **Chunked processing**: ACTIVITY.md split thanh chunks (30KB/chunk)
3. **Per-chunk extraction**: LLM extract key findings tu moi chunk
4. **Multi-pass verification**: Doc lai report, so sanh voi sources, bo sung thieu
5. **Output**: `FINAL_REPORT.md` luu vao `_context/`

### LLM Calls:
- Moi chunk: 1 LLM call de extract
- Final merge: 1 LLM call de verify completeness
- Total: O(n_chunks + 2) LLM calls

---

## 12. Phase 7: User Answer Summarization

**File:** `app/orchestration/pipeline.py:_summarize_for_user()`

### System Prompt (`build_final_summary_prompt()`):
```
You are summarizing a comprehensive task report into a clear answer for the user.

## RULES
1. Extract ALL KEY findings — do not omit important details
2. Format with markdown: headers, bold, lists, tables
3. Keep comprehensive — ALL important details
4. Respond in SAME LANGUAGE as user's original question
5. Mention artifact locations if created
6. Do NOT add disclaimers unless report flags uncertainty
7. Be direct and confident
8. Include specific data: numbers, URLs, code snippets
9. Cover ALL sections — do not cherry-pick
10. Longer and detailed is BETTER than shorter and vague
```

### User Message:
```
## Original Question
{user_message}

## Full Report
{final_report}  ← full content (hoac chunked neu > 30K chars)

Provide the final answer to the user.
```

### LLM Parameters:
- Temperature: 0.3
- Max tokens: 32768

### Chunked Summarization (neu report > 30K chars):
1. Split report thanh 15KB chunks
2. Moi chunk → summarize (temp 0.2, max 8192 tokens)
3. Merge all chunk summaries
4. Final synthesis: merge → user answer (temp 0.3, max 32768)

### Truncation Warning:
Neu answer khong ket thuc bang `.!?。\n` → append:
```
(Luu y: Phan tom tat co the bi cat ngan. Xem FINAL_REPORT.md trong task artifacts de xem day du.)
```

---

## 13. Context Sharing & Budget Management

**File:** `app/orchestration/context_sharing.py`

### 3 Views cua cung mot data:

| Consumer | Thinking | Results | Tool Outputs | Cap |
|----------|----------|---------|--------------|-----|
| **Other Agents** | Full | Capped 5KB/agent | Khong thay | `_AGENT_RESULT_CAP_CHARS = 5000` |
| **Orchestrator** | Full | Capped 8KB/agent | Summary only (name+status+1-line) | `_ORCHESTRATOR_AGENT_CAP_CHARS = 8000` |
| **ActivityLogger** | Full | Full | Full | Khong gioi han |
| **Frontend Events** | 2KB max | 500 chars | 500 chars | Per-event cap |

### Budget-Aware Sharing (`get_shared_context_with_budget()`):
- Total budget: 15000 chars default
- Priority 1: Other agents' thinking (full)
- Priority 2: Other agents' results (remaining budget / N agents)
- Neu over budget: truncate + "[OUTPUT TRUNCATED]" + activity_search hint

### Turn Reset:
- Moi turn moi: `context_sharing.reset_for_turn(turn_number)`
- Clear toan bo thinkings va results
- Agents khong thay direct data tu turns truoc (chi qua `previous_context`)

---

## 14. Context Compression Strategy

**File:** `app/orchestration/agent_worker.py:_compress_context_if_needed()`

### Khi nao compress:
- Total message chars > `_CONTEXT_COMPRESS_THRESHOLD_CHARS` (80KB)
- Hoac `force=True` (error recovery)

### Strategy:
```
messages = [system_prompt] + [middle_messages...] + [last_10_messages]
                                    │
                                    ▼ Compress
[CONTEXT COMPRESSED — X tool calls (Y succeeded), Z messages summarized]
Key actions: Called: web_search, execute_code; Thought: ...
[Full history preserved in ACTIVITY.md — use activity_search to retrieve]
```

### Chi tiet:
- **System prompt**: KHONG BAO GIO compress
- **Recent messages** (last 10): Giu nguyen
- **Middle messages**: Compress thanh 1 assistant message summary
  - Tool messages → count only (name + success/fail)
  - Assistant messages → tools used + thinking snippet (150 chars)

### Khi LLM tra ve context overflow error:
1. Force compress context
2. Retry LLM call 1 lan
3. Neu van fail → try call without tools → get final content

### Khi LLM timeout:
1. Force compress context
2. Retry max 3 lan (`_MAX_TIMEOUT_RETRIES = 3`)
3. Neu van fail → break loop, force synthesis

---

## 15. Post-Processing: XSkill & Graph RAG

### XSkill Accumulate
**File:** `app/orchestration/pipeline.py:_accumulate_xskill()`

Chi chay khi task complete thanh cong. Fire-and-forget.

**Input:**
- task_description (user message)
- task_result: {content, final_answer, tools_used, tool_calls_total, turns, complexity}
- agents_used: [{role, name}]

**Tac dong:** Luu experience/skill vao ContinualLearningEngine de cai thien tasks tuong lai.

### Graph RAG Auto-Index
**File:** `app/orchestration/pipeline.py:_auto_index_entities()`

Chi chay khi task complete thanh cong. Fire-and-forget.

**Input:**
```
## User Query
{message}

## Task Results
{final_report[:8000]}
```

**Tac dong:** Extract entities va relationships → store vao Knowledge Graph (PostgreSQL). Enriches RAG cho future queries.

---

## Phu Luc A: Agent Template Structure

**File:** `app/services/agent_spawner.py`

Moi agent template dinh nghia:
```python
{
    "default_name": "Research Specialist",
    "avatar": "🔍",
    "description": "Deep research and fact-checking specialist",
    "personality": "Methodical, curious, detail-oriented.",
    "expertise": ["web_research", "fact_checking", "data_analysis"],
    "skills": {"research": True, "analysis": True},
    "tools": ["web_search", "fetch_url", "execute_code"],
    "thinking_style": "empirical",
    "background": "Expert in multi-source research and verification"
}
```

### Tool Assignment:
- **Base tools** (toan bo agent): ~31 tools (execute_code, read_file, web_search, ...)
- **Template tools**: Bo sung theo role (vd: pentester co port_scan, dns_lookup)
- **Auto-enrichment**: web_search → web_search_news, web_search_academic

---

## Phu Luc B: Token Budget Allocation

| Component | Budget | Notes |
|-----------|--------|-------|
| System Prompt (agent execution) | ~4000-8000 chars | Fixed per call |
| Tool Catalog in prompt | ~2000 chars (50 tools) | Static |
| Shared Context (other agents) | 5KB/agent, 15KB total | Budget-aware |
| Previous Turns | 6KB max | Compressed progressively |
| Memory Context | 4KB max | From Phase 0 |
| ROE Context | 3KB max (in thinking), 8KB (in execution) | Static per task |
| Tool Output per call (LLM) | 20KB max | Full saved to ACTIVITY.md |
| Context Compression threshold | 80KB total messages | Auto-compress middle |
| Keep recent messages | Last 10 | Never compressed |

---

## Phu Luc C: Event Flow (Frontend)

Moi phase emit events cho frontend real-time display:

| Phase | Event Types | Data |
|-------|-------------|------|
| Planning | `planning_start`, `planning_done` | complexity, agents, intent, assignments |
| Turn Start | `step` | turn number, agents count, mode |
| Thinking | `micro_step`, `agent_thinking` | phase, agent, content (2KB) |
| Discussion | `micro_step`, `agent_thinking` (action=discussion/rebuttal) | challenges, supports |
| Execution | `micro_step`, `tool_start`, `tool_done` | tool name, params, success, output (500 chars) |
| Evaluation | `evaluation_done` | progress_pct, decision, missing_items, agent_evaluations |
| Evolution | `evolution_insight` | suggestions, blind_spots, quality_trajectory |
| Next Turn | `next_turn_plan` | objective, milestone, assignments |
| Documents | `document_created` | TASK_BRIEF, ROE, TODO, TODO_UPDATE |
| Answer | `token` | content chunks (40 chars each) |
| Completion | `xskill_accumulate`, `graph_rag_indexed` | metadata |

---

## Phu Luc D: Data Flow Diagram (Chi Tiet)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         USER MESSAGE                                      │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                 ┌───────────────┼───────────────┐
                 │               │               │
           ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
           │ Memory     │  │ Knowledge │  │ RAG       │  ┌──────────┐
           │ Layers     │  │ Graph     │  │ Hybrid    │  │ XSkill   │
           │ Recall     │  │ (PgVec)   │  │ Retriever │  │ Engine   │
           └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └────┬─────┘
                 │               │               │              │
                 └───────────────┼───────────────┘              │
                                 │ parallel fetch               │
                    ┌────────────▼────────────┐                 │
                    │  SmartContextAggregator  │◄────────────────┘
                    │  score → dedup → budget  │
                    └────────────┬─────────────┘
                                 │ enriched memory_context (~8KB)
                                 ▼
                    ┌─────────────────────────────────┐
                    │    NEXUS ORCHESTRATOR            │
                    │                                 │
                    │  Input:                         │
                    │   • System: planning prompt     │
                    │   • User: original message      │
                    │   • Agent catalog (50+ roles)   │
                    │   • Memory context (enriched)   │
                    │                                 │
                    │  Output: plan_data JSON         │
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │  TASK DOCUMENTS WRITTEN          │
                    │   • TASK_BRIEF.md               │
                    │   • ROE.md                      │
                    │   • TODO.md                     │
                    └────────────┬────────────────────┘
                                 │
              ┌──────────────────▼──────────────────────┐
              │           TURN LOOP                      │
              │                                         │
              │  ┌────────────────────────────────────┐ │
              │  │ THINKING PHASE (parallel)          │ │
              │  │                                    │ │
              │  │ Per Agent receives:                │ │
              │  │  • Identity (name, role, style)    │ │
              │  │  • Assignment + success criteria   │ │
              │  │  • Shared context (peers' thinking)│ │
              │  │  • Previous turns (6KB compressed) │ │
              │  │  • ROE context (3KB)              │ │
              │  │                                    │ │
              │  │ Per Agent outputs:                 │ │
              │  │  • Thinking (reasoning)            │ │
              │  │  • Challenges / Supports           │ │
              │  │  • Tools planned                   │ │
              │  │                                    │ │
              │  │ → Posted to ContextSharing Board   │ │
              │  └───────────────┬────────────────────┘ │
              │                  │                       │
              │  ┌───────────────▼────────────────────┐ │
              │  │ DISCUSSION PHASE (parallel)        │ │
              │  │                                    │ │
              │  │ Round 1: Challenge + Support       │ │
              │  │  Input: all others' thinking       │ │
              │  │  Output: challenges[], supports[]  │ │
              │  │                                    │ │
              │  │ Round 2: Rebuttal (only challenged)│ │
              │  │  Input: challenges received        │ │
              │  │  Output: rebuttals[], adjustments[]│ │
              │  │                                    │ │
              │  │ Synthesis: structured summary      │ │
              │  │  → Agreed / Unresolved / Insights  │ │
              │  │  → Posted as _discussion_synthesis │ │
              │  └───────────────┬────────────────────┘ │
              │                  │                       │
              │  ┌───────────────▼────────────────────┐ │
              │  │ EXECUTION PHASE                    │ │
              │  │ (parallel OR sequential)           │ │
              │  │                                    │ │
              │  │ Per Agent receives:                │ │
              │  │  • Full identity section           │ │
              │  │  • Assignment + success criteria   │ │
              │  │  • ROE (full)                     │ │
              │  │  • Own thinking (from Phase A)     │ │
              │  │  • Team context (5KB/peer)        │ │
              │  │  • Previous turns (6KB)           │ │
              │  │  • Workspace path                  │ │
              │  │  • Tool catalog (50 tools)        │ │
              │  │  • Production mandate             │ │
              │  │  • Context recovery instructions   │ │
              │  │  • Self-evolve instructions        │ │
              │  │                                    │ │
              │  │ Tool Loop (unlimited rounds):      │ │
              │  │  • LLM call → tool_calls          │ │
              │  │  • Execute each tool (600s timeout)│ │
              │  │  • Full output → ACTIVITY.md      │ │
              │  │  • Capped output → LLM (20KB)    │ │
              │  │  • Stuck detection (5 patterns)   │ │
              │  │  • Context compression (>80KB)    │ │
              │  │  • Repeat until no tool_calls     │ │
              │  │                                    │ │
              │  │ Output: AgentResult               │ │
              │  │  • tool_calls[] (full output)     │ │
              │  │  • final output text              │ │
              │  │  • success/error                   │ │
              │  └───────────────┬────────────────────┘ │
              │                  │                       │
              │  ┌───────────────▼────────────────────┐ │
              │  │ EVOLUTION AGENT                    │ │
              │  │  Input: turn_result + state        │ │
              │  │  Output: EvolutionInsight          │ │
              │  │   • improvement_suggestions       │ │
              │  │   • blind_spots                    │ │
              │  │   • quality_trajectory             │ │
              │  │   • severity_score                 │ │
              │  └───────────────┬────────────────────┘ │
              │                  │                       │
              │  ┌───────────────▼────────────────────┐ │
              │  │ ORCHESTRATOR EVALUATION            │ │
              │  │                                    │ │
              │  │ Input:                             │ │
              │  │  • Original user request           │ │
              │  │  • Turn plan (assignments)        │ │
              │  │  • Turn results (8KB/agent cap)    │ │
              │  │  • Tool summaries (name+status)    │ │
              │  │  • Progress history                │ │
              │  │  • Evolution insights              │ │
              │  │                                    │ │
              │  │ Output: EvaluationResult           │ │
              │  │  • progress_pct                    │ │
              │  │  • is_complete                     │ │
              │  │  • next_turn_focus                 │ │
              │  │  • next_assignments               │ │
              │  │                                    │ │
              │  │ Guards:                            │ │
              │  │  • < 80% → never complete         │ │
              │  │  • delta > 5% → keep going        │ │
              │  │  • severity >= 0.8 → must continue│ │
              │  │  • credentials missing → continue │ │
              │  └───────────────┬────────────────────┘ │
              │                  │                       │
              │         is_complete? ──No──→ plan_next   │
              │                  │           _turn()     │
              │                 Yes                      │
              └──────────────────┬──────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │  FINAL REPORT GENERATOR          │
                    │                                 │
                    │  Reads:                         │
                    │   • TASK_BRIEF.md (full)        │
                    │   • ROE.md (full)              │
                    │   • TODO.md (full)             │
                    │   • ACTIVITY.md (full, chunked) │
                    │                                 │
                    │  Process:                       │
                    │   • Header synthesis (1 call)   │
                    │   • Per-chunk extraction        │
                    │   • Multi-pass verification     │
                    │                                 │
                    │  Output: FINAL_REPORT.md        │
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │  SUMMARIZE FOR USER              │
                    │                                 │
                    │  Input:                         │
                    │   • System: summary prompt      │
                    │   • User message + full report  │
                    │                                 │
                    │  If report > 30K:               │
                    │   • Chunk (15KB each)           │
                    │   • Summarize each chunk        │
                    │   • Final merge synthesis       │
                    │                                 │
                    │  Output: user-facing answer     │
                    │  (streamed as 40-char tokens)   │
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │  POST-PROCESSING                 │
                    │   • XSkill accumulate           │
                    │   • Graph RAG auto-index        │
                    │  (fire-and-forget, never block) │
                    └─────────────────────────────────┘
```

---

## Phu Luc E: Error Recovery Mechanisms

| Scenario | Recovery Strategy |
|----------|-------------------|
| LLM timeout | Force compress context → retry (max 3) |
| Context overflow | Force compress → retry once |
| Orphan tool_use (no tool_result) | Insert dummy tool_result → retry |
| Malformed response (no content + no tools) | Retry once |
| Agent stuck (repeated actions) | Inject warning → trim history → if 100x → force-stop |
| Agent crash (error but tools succeeded) | Extract partial data → inject next turn |
| Tool timeout (600s) | Return error string → agent chooses alternative |
| Planning parse fail | Fallback plan (1 researcher, 1 turn) |
| Evaluation parse fail | progress=50%, is_complete=False |
| Report too long (>30K) | Chunked summarization (15KB chunks) |

---

*Generated: 2026-06-03*
*Source: app/orchestration/, app/core/, app/memory/*
