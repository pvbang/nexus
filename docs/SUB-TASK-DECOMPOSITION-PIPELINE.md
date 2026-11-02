# Nexus — Sub-Task Decomposition Pipeline (Chi Tiet)

## Muc Luc

1. [Tong Quan Co Che](#1-tong-quan-co-che)
2. [Level 1: Turn-Based Decomposition (Chinh)](#2-level-1-turn-based-decomposition)
3. [Level 2: Within-Turn Agent Assignments](#3-level-2-within-turn-agent-assignments)
4. [Level 3: Subtask Delegation (Nested Orchestration)](#4-level-3-subtask-delegation)
5. [Luong Du Lieu Chi Tiet](#5-luong-du-lieu-chi-tiet)
6. [Cac Pattern Decomposition](#6-cac-pattern-decomposition)
7. [Dependency & Ordering System](#7-dependency--ordering-system)
8. [Dynamic Replanning (Adaptive)](#8-dynamic-replanning)
9. [Diem Yeu Hien Tai & Giai Phap](#9-diem-yeu-hien-tai--giai-phap)
10. [Vi Du Thuc Te](#10-vi-du-thuc-te)

---

## 1. Tong Quan Co Che

Nexus co **3 cap do** phan tach task:

```
┌─────────────────────────────────────────────────────────────────────┐
│ USER REQUEST                                                        │
│ "Reverse engineer app X, tìm RCE, bypass auth"                     │
└────────────────────────────────────┬────────────────────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │ LEVEL 1: Turn Decomposition      │
                    │ (Orchestrator planning)           │
                    │                                  │
                    │ Turn 1: Reconnaissance           │
                    │ Turn 2: Deep RE                  │
                    │ Turn 3: Exploitation             │
                    │ Turn 4: Report                   │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │ LEVEL 2: Agent Assignments       │
                    │ (Within each turn)               │
                    │                                  │
                    │ Turn 1:                          │
                    │   ├── recon_specialist: OSINT    │
                    │   ├── network_analyst: traffic   │
                    │   └── (parallel execution)       │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │ LEVEL 3: Subtask Delegation      │
                    │ (Agent spawns sub-orchestrator)   │
                    │                                  │
                    │ Agent calls delegate_task():     │
                    │   └── Sub-orchestrator runs      │
                    │       own turn loop              │
                    │       (max depth: 3)             │
                    └─────────────────────────────────┘
```

---

## 2. Level 1: Turn-Based Decomposition

### Ai quyet dinh? 
**Nexus Orchestrator** (LLM, temp 0.3)

### Khi nao?
1. **Luc dau**: `plan_task()` — tao toan bo execution_plan
2. **Sau moi turn**: `plan_next_turn()` — adaptive planning dua tren evaluation

### Input cho Orchestrator:

| Input | Noi dung | Kich thuoc |
|-------|----------|------------|
| User message | Yeu cau goc | Full |
| Agent Catalog | 50+ agent templates voi role, expertise | ~2KB |
| Memory Context | KG + RAG + XSkill + user profile | ~8KB |
| Complexity hint | simple/medium/complex/expert | tu LLM |

### Output (execution_plan):

```json
{
  "intent_analysis": "User wants comprehensive security audit of Android app",
  "task_type": "security",
  "complexity": "expert",
  "selected_agents": [
    {"role": "recon_specialist", "name": "Recon Scout", "primary_responsibility": "OSINT & metadata"},
    {"role": "malware_analyst", "name": "RE Engineer", "primary_responsibility": "APK decompile"},
    {"role": "pentester", "name": "App Pentester", "primary_responsibility": "Active exploitation"},
    {"role": "exploit_developer", "name": "Exploit Engineer", "primary_responsibility": "PoC development"}
  ],
  "execution_plan": [
    {
      "turn": 1,
      "objective": "Reconnaissance & APK Acquisition",
      "milestone": "APK file + metadata + endpoints + tech stack",
      "execution_mode": "parallel",
      "assignments": [
        {
          "agent_role": "recon_specialist",
          "task": "Thu thap metadata, domains, tech stack, download APK",
          "success_criteria": "Danh sach day du: domains, permissions, APK ready",
          "dependencies": [],
          "tools_hint": ["web_search", "docker_exec", "fetch_url"]
        },
        {
          "agent_role": "network_analyst",
          "task": "Capture traffic, identify API endpoints",
          "success_criteria": "Full API map, auth flow documented",
          "dependencies": [],
          "tools_hint": ["device_exec", "frida_run"]
        }
      ]
    },
    {
      "turn": 2,
      "objective": "Deep Reverse Engineering",
      "milestone": "Source decompiled, secrets extracted, attack surface mapped",
      "execution_mode": "parallel",
      "assignments": [
        {
          "agent_role": "malware_analyst",
          "task": "Decompile APK, analyze source, find secrets",
          "success_criteria": "Extracted API keys, auth logic, vuln classes identified",
          "dependencies": ["recon_specialist"],
          "tools_hint": ["docker_exec", "read_file"]
        }
      ]
    }
  ]
}
```

### Logic Phan Chia Turns:

```
Orchestrator su dung nhan thuc:
1. Turn dau LUON la "gather information" (recon, research, read)
2. Turn giua la "analyze + attempt" (RE, exploit, synthesize)
3. Turn cuoi la "verify + report" (test, consolidate, write)

Moi turn co:
- objective: muc tieu ro rang (1 cau)
- milestone: dau hieu "xong turn" (measurable)
- execution_mode: parallel (mac dinh) hoac sequential (co dependency)
- assignments: task cu the cho tung agent
```

### Quy Tac So Agent & Turns:

| Complexity | Agents | Turns | Logic |
|-----------|--------|-------|-------|
| simple | 1 | 1 | Question → 1 researcher → done |
| medium | 2-3 | 2-4 | Research → Analyze → Synthesize |
| complex | 3-7 | 3-10 | Multi-domain, iterative refinement |
| expert | 3-7+ | 3-10+ | + Divergent Thinking + Debate pattern |

---

## 3. Level 2: Within-Turn Agent Assignments

### Data Structure:

```python
@dataclass
class AgentAssignment:
    agent_role: str           # "pentester", "researcher", etc.
    agent_name: str           # "App Pentester"
    task_description: str     # Actionable task text
    success_criteria: str     # Measurable outcome
    dependencies: list[str]   # ["recon_specialist"] = phai doi
    tools_hint: list[str]     # Goi y tools nen dung
```

### Execution Modes:

#### Parallel Mode (default):
```
Turn 1 (parallel):
  ┌── Agent A starts ──────────────────────────────────┐
  ├── Agent B starts ──────────────────────────────────┤  (dong thoi)
  └── Agent C starts ──────────────────────────────────┘
  
  Semaphore: max 3 concurrent LLM calls (configurable)
  Context: Agents thay peers' thinking (tu discussion phase)
           KHONG thay peers' tool results (chua co)
```

#### Sequential Mode (with dependencies):
```
Turn 2 (sequential):
  Agent A (no deps) ─────→ Result A posted to ContextSharing
       │
       └──→ Agent B (depends_on: A) ─────→ Result B
                │
                └──→ Agent C (depends_on: A, B) ─────→ Result C
  
  Context: Agent B thay output cua Agent A (5KB capped)
           Agent C thay output cua A va B
```

### Topological Sort (dependency resolution):

```python
def _order_by_dependencies(assignments):
    """
    Input: [A(deps=[]), B(deps=["A"]), C(deps=["A","B"])]
    Output: [A, B, C]  — in dependency order
    
    Algorithm:
    1. Find assignments with all deps resolved → add to "ready"
    2. Mark resolved, remove from remaining
    3. Repeat until empty or circular (fallback: original order)
    """
```

---

## 4. Level 3: Subtask Delegation

### Khi nao agent delegate?
Agent TU QUYET DINH goi tool `delegate_task()` khi:
- Sub-problem qua phuc tap cho 1 agent
- Can tool set khac (vd: agent la researcher nhung can coding)
- Can execution isolation (khong anh huong main task)

### Mechanism:

```
Parent Agent (trong turn execution)
    │
    ▼ goi delegate_task(description="Decompile APK and find secrets")
    │
    ├── 1. Doc parent context (TASK_BRIEF, TODO, ACTIVITY[-4KB])
    ├── 2. Tao subtask_id moi
    ├── 3. Spawn SubagentDelegator (new orchestrator instance)
    │       └── Chay turn loop rieng (budget: max_iterations)
    │       └── Tools: subset tu parent (filtered)
    │       └── Timeout: configurable
    ├── 4. Subtask hoan thanh → return result
    ├── 5. Merge subtask ACTIVITY vao parent ACTIVITY.md
    └── 6. Cleanup subtask workspace
```

### Constraints:
- **Max depth: 3** (task → subtask → sub-subtask)
- **Context injection**: Subtask nhan parent's TASK_BRIEF + TODO + ACTIVITY tail
- **Result merge**: Max 8KB tu subtask activity
- **Workspace isolation**: Subtask co workspace rieng, xoa sau merge

### Tool Schema:

```python
delegate_task(
    description: str,      # Mo ta sub-task
    context: str = "",     # Them context (optional)
    max_iterations: int = 5,  # Budget cho sub-orchestrator
    tools: list[str] = [], # Gioi han tools (empty = all)
    timeout: int = 300,    # Timeout giay
)
```

---

## 5. Luong Du Lieu Chi Tiet

### Flow tu User Message → Sub-Tasks:

```
USER: "Reverse engineer app, find RCE, bypass auth"
    │
    ▼ ══════════════════════════════════════════════
    │ Phase 0: Smart Context Aggregation
    │ (Memory + KG + RAG + XSkill → 8KB context)
    ▼ ══════════════════════════════════════════════
    │ Phase 1: Orchestrator Planning
    │ 
    │ LLM Input:
    │   system: planning prompt (agent catalog, rules, patterns)
    │   user: message + memory_context
    │
    │ LLM Output (JSON):
    │   intent: "security audit + RE + exploitation"
    │   type: "security"
    │   complexity: "expert"
    │   agents: [recon, malware_analyst, pentester, exploit_dev]
    │   plan: [turn1: recon, turn2: RE, turn3: exploit, turn4: report]
    │
    ▼ ══════════════════════════════════════════════
    │ Phase 1.5: Divergent Thinking (expert tasks only)
    │
    │ 3 parallel thinkers (temp 1.0):
    │   - Minimalist: "Chi can decompile + grep secrets, 2 turns"
    │   - Unconventional: "Dung Frida hook truc tiep, skip decompile"
    │   - Reverse: "Bắt đầu từ exploit → trace ngược về vuln"
    │
    │ Selector (temp 0.3): chon hybrid strategy
    │ → Inject strategy vao first turn assignments
    │
    ▼ ══════════════════════════════════════════════
    │ Phase 2: Create Workers (1 per agent role)
    │ Phase 3: Turn Loop begins...
    │
    │ ┌──────────────────────────────────────────┐
    │ │ TURN 1                                   │
    │ │                                          │
    │ │ Thinking Phase (parallel):               │
    │ │   recon_specialist: "Se search Play Store│
    │ │     + download APK + enum domains"       │
    │ │   network_analyst: "Se setup proxy +     │
    │ │     capture login traffic"               │
    │ │                                          │
    │ │ Discussion Phase (parallel):             │
    │ │   recon → network: "Se share APK khi co" │
    │ │   network → recon: "Can package name"    │
    │ │                                          │
    │ │ Execution Phase (parallel, sem=3):       │
    │ │   recon: docker_exec("jadx...")          │
    │ │   network: frida_run(ssl_bypass, pkg)    │
    │ │                                          │
    │ │ → Turn Result: [AgentResult, ...]        │
    │ └──────────────────────────────────────────┘
    │
    ▼ ══════════════════════════════════════════════
    │ Evolution Agent (temp 1.0):
    │   "Blind spot: chua check native .so files"
    │   severity: 0.6
    │
    ▼ ══════════════════════════════════════════════
    │ Orchestrator Evaluation:
    │
    │ Input:
    │   - Accumulated briefing (user request + all accomplishments + gaps)
    │   - Turn 1 results (8KB/agent capped)
    │   - Evolution insights
    │
    │ Output (JSON):
    │   progress: 30%
    │   decision: "continue"
    │   missing: ["native lib analysis", "actual exploit code"]
    │   next_focus: "Deep RE on decompiled source + native libs"
    │   next_assignments: [
    │     {role: "malware_analyst", task: "Analyze jadx output..."},
    │     {role: "exploit_developer", task: "Write Frida bypass..."}
    │   ]
    │
    ▼ ══════════════════════════════════════════════
    │ plan_next_turn():
    │
    │ 1. Check stagnation (metacognitive override if stuck)
    │ 2. Build AgentAssignment from evaluation.next_assignments
    │ 3. Inject "prior work" context: "You previously accomplished: X"
    │ 4. Inject failure warnings if agent failed 2+ times
    │ 5. Return TurnPlan(turn_number=2, ...)
    │
    ▼ ══════════════════════════════════════════════
    │ TURN 2, 3, ... (repeat until is_complete=True)
    │
    ▼ ══════════════════════════════════════════════
    │ Final Report → Summarize → Answer to User
    └══════════════════════════════════════════════
```

---

## 6. Cac Pattern Decomposition

### Pattern 1: Standard (Default)

```
Turn 1: Research/Gather (parallel agents)
Turn 2: Analyze/Process (parallel or sequential)
Turn 3: Synthesize/Verify (sequential, single agent)
Turn 4: Report/Deliver (single agent)
```

**Dung khi:** Hau het tasks (research, coding, analysis)

### Pattern 2: Generation-Debate-Evolution

```
Turn 1: hypothesis_generator → propose ideas
Turn 2: scientific_critic + idea_ranker → evaluate/score
Turn 3: idea_evolver + proximity_mapper → refine + diversify
Turn 4: meta_reviewer → final synthesis
```

**Dung khi:** Ideation, brainstorming, hypothesis testing

### Pattern 3: Deep Research

```
Turn 1: researcher_A (topic 1) || researcher_B (topic 2) || researcher_C (topic 3)
Turn 2: analyst → cross-cutting synthesis from all researchers
Turn 3: writer → comprehensive report
```

**Dung khi:** Multi-source research, comprehensive analysis

### Pattern 4: Security Audit (domain-specific)

```
Turn 1: recon_specialist + network_analyst (parallel)
Turn 2: malware_analyst + vulnerability_researcher (parallel/sequential)
Turn 3: pentester + exploit_developer (sequential)
Turn 4: pentester → consolidated report
```

**Dung khi:** Security tasks (auto-detected)

### Pattern 5: Build-Test-Fix (coding tasks)

```
Turn 1: developer → implement
Turn 2: qa_engineer → test, security_reviewer → review
Turn 3: developer → fix issues from T2
Turn 4: developer → final delivery
```

**Dung khi:** Feature development, bug fixes

---

## 7. Dependency & Ordering System

### Cach Khai Bao Dependencies:

```json
{
  "assignments": [
    {"agent_role": "recon_specialist", "dependencies": []},
    {"agent_role": "malware_analyst", "dependencies": ["recon_specialist"]},
    {"agent_role": "exploit_developer", "dependencies": ["recon_specialist", "malware_analyst"]}
  ]
}
```

### Execution khi co dependencies:

```python
# execution_mode = "sequential":
ordered = _order_by_dependencies(assignments)
# [recon (no deps), malware (dep: recon), exploit (dep: recon+malware)]

for assignment in ordered:
    result = await worker.execute(assignment, shared_context)
    context_sharing.add_result(result)  # visible cho agents sau
```

### Context Visibility:

| Agent | Thay duoc | Khong thay |
|-------|-----------|------------|
| recon (chay truoc) | Khong ai | - |
| malware (sau recon) | recon output (5KB) | - |
| exploit (sau ca 2) | recon output + malware output | - |

### Parallel voi Dependency (hybrid):

```json
{
  "execution_mode": "parallel",
  "assignments": [
    {"role": "A", "dependencies": []},
    {"role": "B", "dependencies": []},
    {"role": "C", "dependencies": ["A"]}
  ]
}
```
→ A va B chay parallel. C doi A xong moi chay.
(Hien tai: neu mode = "parallel", TAT CA chay parallel bat ke deps.
Deps chi duoc respect khi mode = "sequential".)

---

## 8. Dynamic Replanning (Adaptive)

### Trigger: Sau moi turn evaluation

```python
# NexusOrchestrator.plan_next_turn(state, evaluation):

# Source 1: LLM evaluation noi "next_assignments = [...]"
# Source 2: Metacognitive override (khi stagnant)
# Source 3: Evolution insights (inject vao focus)
# Source 4: Failure tracker (inject warning)

# Logic:
if evaluation.is_complete:
    return None  # done

if stagnation_detected:
    → MetacognitiveAgent.analyze_blockage()
    → Override assignments with breakthrough action

assignments = evaluation.next_assignments
if not assignments:
    → Reassign ALL agents with focus on missing_items

for each assignment:
    inject "prior work" context
    inject failure warnings if applicable

return TurnPlan(...)
```

### Khi nao thay doi plan hoan toan?

1. **Stagnation** (progress < 5% delta for 3+ turns):
   → MetacognitiveAgent phan tich root cause
   → Override assignments completely
   → Emit event "metacognitive_insight" cho frontend

2. **Agent failure** (2+ consecutive):
   → Inject strategy change warning
   → May reassign to different agent role

3. **Evolution severity >= 0.8**:
   → Block completion
   → Force continue with Evolution's suggestions

4. **User cancellation**:
   → Break loop, collect partial output

---

## 9. Diem Yeu Hien Tai & Giai Phap

### Van de 1: Planning chi reactive, khong co look-ahead

**Hien tai:** Plan turn tiep chi dua tren evaluation turn vua xong.
Khong co "nhin xa" de optimize toan bo path.

**Hau qua:** Có thể chọn strategy turn 2 mà biết trước sẽ fail ở turn 4.

**Giai phap:** Thêm "strategic forecast" trong evaluation — LLM predict
2-3 turns ahead, adjust current plan if forecast shows dead-end.

---

### Van de 2: Dependencies chi trong 1 turn

**Hien tai:** Dependencies la list[agent_role] TRONG CUNG TURN.
Khong co cross-turn dependency (vd: "Turn 3 assignment X depends on Turn 2 result Y").

**Hau qua:** Cross-turn context chi qua `previous_turns` summary (6KB).
Agent turn 3 co the khong thay detail tu turn 1.

**Giai phap da implement:** `get_accumulated_briefing()` + "ALREADY COMPLETED" section.
Agent biet tat ca da lam duoc. Nhung khong co explicit data dependency graph.

---

### Van de 3: Khong co backtracking

**Hien tai:** Turn da evaluation xong → khong the "undo".
Neu turn 2 di sai huong, chi co the "fix o turn 3+", khong quay lai turn 2.

**Hau qua:** Wasted turns (nhu case 66096be8: 8/17 turns = 0 output).

**Giai phap:** Metacognitive Agent detect sai huong → override plan hoan toan.
Khong backtrack turn cu, nhung se "restart approach" tu turn moi.

---

### Van de 4: Parallel turns khong co

**Hien tai:** Turns chay SEQUENTIAL. Within-turn agents can parallel,
nhung GIUA cac turns la sequential (phai doi turn N xong moi chay N+1).

**Hau qua:** Cham. 5 turns x 3 min/turn = 15 min. Neu pipeline duoc thi < 10 min.

**Giai phap tuong lai:** Pipeline parallelism:
- Turn N agents thinking → Turn N-1 agents writing report (dong thoi)
- "Speculative execution": start turn N+1 early, discard if N changes plan

---

### Van de 5: Delegation khong tu dong

**Hien tai:** Agent phai TU QUYET DINH goi `delegate_task()`.
Orchestrator KHONG BAO GIO tu dong delegate.

**Hau qua:** Agents hiem khi delegate (khong nghi toi), dan den 1 agent
lam qua nhieu viec trong 1 turn.

**Giai phap:** Orchestrator co the tu dong delegate khi:
- Assignment qua phuc tap (nhieu sub-steps)
- Agent da fail 2+ times (delegate cho agent khac)
- Cross-domain work (delegate domain X cho specialist)

---

### Van de 6: Progress estimation la heuristic

**Hien tai:** LLM estimate `progress_pct` — khong co metric khach quan.
Co the: LLM noi 80% nhung thuc te chi 30%.

**Hau qua:** Premature completion hoac infinite loops.

**Giai phap hien tai:**
- Floor: < 80% → never complete
- Delta check: if climbing > 5% → continue
- Evolution override: severity >= 0.8 → block completion

---

## 10. Vi Du Thuc Te

### Case: Task `66096be8` (ReelShort Audit — 17 turns)

**Original Plan:**
```
Turn 1: Recon (recon + network) — parallel
Turn 2: RE (malware_analyst + vuln_researcher) — parallel  
Turn 3: Exploit (pentester + vuln_researcher) — parallel
Turn 4: PoC Dev (exploit_developer) — sequential
Turn 5: Report (pentester) — single
```

**Actual Execution:** 17 turns vi:
- Turn 2-3: Agents khong co tool de download/decompile (0 output)
- Turn 4-5: Lai 0 output (van co approaches cu)
- Turn 6: PIVOT to documentation-only (lam duoc!)
- Turn 7: Quay lai approach cu (0 output)
- Turn 8-17: Oscillate giua "work" va "0 output"

**Root cause:** Khong co self-awareness (agent khong biet minh khong co jadx/frida)
+ Khong co metacognition (khong hieu TAI SAO 0 output)
+ Khong co workspace-aware (khong biet `apk_analysis/` da co san)

**Voi fixes hien tai, expected flow:**
```
Turn 1: Infrastructure Map → "Docker: AVAILABLE, jadx READY"
         Agent: docker_exec("jadx -d /workspace/output app.apk")
         Agent: list_files → sees apk_analysis/ exists → reads it
         → REAL output from turn 1

Turn 2: Agent checks workspace: "jadx_output da co tu turn 1"
         Agent: read + analyze decompiled source
         → Findings, secrets, attack surface

Turn 3: (If stuck) Metacognition: "Root cause: khong co device cho Frida.
         Reframe: produce scripts for user to run. 
         Action: write complete toolkit scripts."
         → Deliverables as user-runnable scripts

Total: 3-5 turns thay vi 17.
```

---

## Phu Luc A: Data Models

```python
@dataclass
class TurnPlan:
    turn_number: int
    objective: str                    # "What this turn achieves"
    assignments: list[AgentAssignment]
    execution_mode: str = "parallel"  # "parallel" | "sequential"
    milestone: str = ""               # "How we know turn succeeded"

@dataclass
class AgentAssignment:
    agent_role: str                   # "pentester"
    agent_name: str                   # "App Pentester"
    task_description: str             # Actionable task
    success_criteria: str = ""        # Measurable outcome
    dependencies: list[str] = []      # ["recon_specialist"]
    tools_hint: list[str] = []        # ["docker_exec", "frida_run"]

@dataclass
class TurnResult:
    turn_number: int
    agent_results: list[AgentResult]
    orchestrator_evaluation: Optional[EvaluationResult]
    evolution_insight: Optional[EvolutionInsight]

@dataclass
class EvaluationResult:
    turn_number: int
    agent_evaluations: list[dict]     # per-agent: completed, quality, notes
    contradictions: list[str]
    missing_items: list[str]          # "what's still needed"
    progress_pct: int                 # 0-100
    is_complete: bool
    next_turn_focus: str              # strategic direction
    next_assignments: list[dict]      # new work to assign
```

---

## Phu Luc B: Prompt Instructions cho Decomposition

Orchestrator nhan prompt voi cac rules:

```
## PLANNING RULES
1. Start with minimum viable team — add agents only if truly needed
2. Simple: 1 agent, 1 turn
3. Research: 2-3 agents, 2-4 turns
4. Complex: 3-7 agents, 3-10 turns
5. Each assignment MUST have measurable success criteria
6. Mark dependencies explicitly (empty = independent)
7. Prefer "parallel" when tasks independent
8. Each turn MUST have clear milestone
9. First turn gathers info; later turns synthesize & verify

## EXECUTION PATTERNS:
- Standard: research → analysis → synthesis
- Generation-Debate-Evolution: ideation tasks
- Deep Research: comprehensive multi-source
- (Choose or combine based on task nature)

## THOROUGHNESS MANDATE:
- COMPREHENSIVE — no shortcuts
- Every aspect addressed (explicit AND implicit)
- Plan VERIFICATION steps after data gathering
```

---

## Phu Luc C: Comparison voi Alternatives

| Approach | Nexus (hien tai) | Tree-based (AutoGPT-style) | DAG-based (Langchain) |
|----------|------------------|---------------------------|----------------------|
| Structure | Sequential turns, parallel agents | Hierarchical sub-tasks | Directed Acyclic Graph |
| Planning | Adaptive (per-turn) | Upfront (full tree) | Upfront (full DAG) |
| Flexibility | Cao (replan moi turn) | Thap (fixed tree) | Trung binh |
| Context | Shared board per turn | Isolated per subtask | Passed via edges |
| Delegation | Optional (agent decides) | Built-in (parent→child) | Built-in (node→node) |
| Recovery | Metacognition + Evolution | Retry node | Retry edge |
| Weakness | No backtracking, reactive | Rigid, no adaptation | Complex setup |

---

*Generated: 2026-06-03*
*Source: app/orchestration/ (pipeline, nexus_orchestrator, turn_executor, models, prompts)*
