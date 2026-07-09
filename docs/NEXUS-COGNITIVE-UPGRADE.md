# Nexus Cognitive Upgrade — Architecture Plan

**Problem Statement:** Nexus hien tai la reactive execution engine — nhan lenh, chay, danh gia, lap. No thieu:
- Nhan thuc ve ban than (biet minh co gi, thieu gi, lam duoc gi)
- Tu duy phan tich sau (tai sao bi ket, can gi de thoat)
- Kha nang dot pha (tim cach moi, pha bo approach cu)
- Kha nang tu phat trien (tao tool moi, setup infrastructure)

**Solution:** 3 layers bo sung vao pipeline hien tai.

---

## Layer 1: Self-Awareness (Nhan Thuc Ban Than)

### Muc dich
Agent biet CHINH XAC:
- Minh dang o environment nao (cloud, local, sandbox)
- Co nhung tools/services nao THUC SU available (khong chi tren giay)
- Thieu credentials/config gi → biet cach tu setup
- Da lam gi roi, dang o buoc nao cua task

### Implementation

#### 1.1 Infrastructure Map (inject vao system prompt)

Tao file `app/core/infrastructure_map.py`:

```python
class InfrastructureMap:
    """Dynamic infrastructure awareness for agents."""

    async def build_map(self) -> str:
        """Build current infrastructure state for prompt injection.

        Returns markdown string describing:
        - Available cloud providers + credential status
        - Active services (DB, Redis, MCP servers)
        - Sandbox capabilities
        - Self-bootstrap options khi thieu credentials
        """
        sections = []

        # 1. Cloud providers
        sections.append(await self._check_cloud_providers())

        # 2. Tool availability (grouped by toolset, with health status)
        sections.append(await self._check_toolset_health())

        # 3. Sandbox/execution environment
        sections.append(self._check_execution_env())

        # 4. Self-bootstrap options
        sections.append(self._list_bootstrap_options())

        return "\n".join(sections)

    async def _check_cloud_providers(self) -> str:
        """Check which cloud providers are configured and working."""
        # Check actual connectivity, not just env var existence
        # Alibaba Cloud: check if AccessKey works
        # AWS Bedrock: check if credentials valid
        # Return: "Alibaba Cloud: CONFIGURED (AccessKey active)"
        #     or: "Alibaba Cloud: NOT CONFIGURED → use create_alibaba_account tool"
        ...

    def _list_bootstrap_options(self) -> str:
        """What agent CAN DO when credentials/services are missing."""
        return """
## Self-Bootstrap Options
When a required service/credential is missing, you can:
- `create_cloud_sandbox()` — provision a new sandbox environment
- `scaffold_custom_tool_module(...)` — create a tool wrapper for missing API
- `setup_credentials(provider, method="interactive"|"auto")` — configure auth
- `expose_port(...)` + `setup_public_inbox(...)` — create external access

DO NOT give up when credentials are missing. BOOTSTRAP them.
"""
```

#### 1.2 Tool Introspection (agent co the hoi "tool nay lam gi, can gi")

Them tool moi: `introspect_tools`

```python
@registry.register(toolset="meta", category="self-awareness")
async def introspect_tools(query: str = "", toolset: str = "") -> dict:
    """Agent asks about its own capabilities.

    Examples:
    - introspect_tools(query="what can I use for cloud deployment?")
    - introspect_tools(toolset="alibaba") → full status of Alibaba tools
    - introspect_tools(query="what's failing?") → recent tool failures
    """
    # Return: matching tools + their status + prerequisites + alternatives
```

#### 1.3 Inject Point

Trong `build_agent_execution_prompt()`, them section:

```
## YOUR CAPABILITIES (Live Status)
{infrastructure_map}

## SELF-BOOTSTRAP DOCTRINE
If a tool/service is unavailable:
1. Check WHY (introspect_tools)
2. Can you BOOTSTRAP it? (create account, install dependency, configure)
3. If yes → do it FIRST, then continue your task
4. If no → report the blocker clearly, suggest what human needs to do
NEVER silently fail. NEVER skip because "credentials missing".
```

---

## Layer 2: Metacognitive Loop (Tu Duy Ve Tu Duy)

### Muc dich
Agent NHAN RA khi no bi ket va HIEU TAI SAO, roi TU QUYET DINH hanh dong khac biet. Khong chi "change approach" ma phan tich:
- Tai sao approach hien tai khong work?
- Co assumption nao sai?
- Can thong tin gi ma chua co?
- Co nen hoi lai user khong?

### Implementation

#### 2.1 Metacognitive Agent (chay khi detect stagnation/loop)

Tao file `app/orchestration/metacognition.py`:

```python
class MetacognitiveAgent:
    """Analyzes WHY the system is stuck and produces actionable breakthrough.

    Triggered by: stagnation detection, repeated failures, loop detection.
    Unlike Evolution (which critiques quality), Metacognition analyzes PROCESS:
    - What assumption is wrong?
    - What information is missing?
    - What completely different frame could solve this?
    """

    async def analyze_blockage(
        self,
        state: ExecutionState,
        recent_failures: list[str],
        stuck_pattern: str,
    ) -> MetacognitiveInsight:
        """Deep analysis of why progress is stuck.

        Returns:
        - root_cause: str (the actual reason, not symptom)
        - wrong_assumptions: list[str] (beliefs that should be questioned)
        - missing_prerequisites: list[str] (what's needed before progress)
        - reframe: str (completely different way to view the problem)
        - concrete_next_action: str (specific, different action to try)
        - should_ask_user: bool (if blocker requires human input)
        - user_question: str (what to ask, if should_ask_user)
        """
```

System prompt cho Metacognitive Agent:
```
You are the Metacognitive Agent — you think about HOW the system is thinking.

When the system is stuck, you don't just say "try harder" or "change approach".
You perform ROOT CAUSE ANALYSIS:

## Your Analysis Framework
1. WHAT is happening? (describe the loop/failure pattern precisely)
2. WHY is it happening? (trace back to the root assumption/blocker)
3. What ASSUMPTION is wrong? (the belief that's causing the loop)
4. What's ACTUALLY NEEDED? (the real prerequisite before progress)
5. What COMPLETELY DIFFERENT frame could work? (lateral thinking)

## Common Root Causes You Detect:
- "Agent assumes X exists but it doesn't" → need to CREATE it first
- "Agent repeats search because it doesn't trust its own results" → need VERIFICATION not repetition
- "Agent uses wrong tool for the job" → need DIFFERENT TOOL not same tool with different args
- "Task is impossible with current capabilities" → need to BOOTSTRAP new capability or ASK USER
- "Agent doesn't understand the actual goal" → need to RE-READ original request carefully
- "Approach works but is too slow/expensive" → need OPTIMIZATION not repetition

## Output Requirements
- Be SPECIFIC. Not "try a different approach" but "use tool X with params Y because Z"
- If you identify a missing prerequisite, specify EXACTLY how to obtain it
- If you think the user's request needs clarification, formulate the EXACT question
- Temperature 1.0 — be creative, unconventional, break patterns
```

#### 2.2 Integration Point

Trong `_detect_stuck()` cua `AgentWorker` — khi detect stuck, TRUOC KHI inject warning:

```python
# Thay vi chi inject warning text:
if stuck_reason:
    # Goi Metacognitive Agent de phan tich tai sao
    insight = await self._metacognitive_analysis(stuck_reason, messages)
    # Inject insight THAY VI generic warning
    messages.append({
        "role": "user",
        "content": (
            f"[METACOGNITIVE ANALYSIS]\n"
            f"You are stuck: {stuck_reason}\n"
            f"Root cause: {insight.root_cause}\n"
            f"Wrong assumption: {insight.wrong_assumptions[0]}\n"
            f"What you actually need: {insight.missing_prerequisites}\n"
            f"Reframed approach: {insight.reframe}\n"
            f"CONCRETE NEXT ACTION: {insight.concrete_next_action}\n"
            f"DO THIS NOW. Do NOT repeat what you were doing."
        ),
    })
```

#### 2.3 Trigger cho Orchestrator level

Trong `plan_next_turn()` — khi progress stagnates 2+ turns:

```python
if progress_delta < 5 and state.turn_count >= 3:
    # Trigger metacognitive analysis at orchestrator level
    meta_insight = await self._metacognitive_agent.analyze_blockage(
        state=state,
        recent_failures=[...],
        stuck_pattern="progress stagnation",
    )

    # Override next assignments with metacognitive recommendation
    if meta_insight.should_ask_user:
        # Emit event asking user for clarification
        await self._emit("user_clarification_needed", {
            "question": meta_insight.user_question,
            "reason": meta_insight.root_cause,
        })
    else:
        # Replace assignments with metacognitive recommendation
        assignments = [AgentAssignment(
            agent_role=meta_insight.recommended_agent,
            task_description=meta_insight.concrete_next_action,
            success_criteria=meta_insight.verification_criteria,
        )]
```

---

## Layer 3: Creative Divergence (Tu Duy Dot Pha)

### Muc dich
Nexus hien tai chi co 1 strategy path. Can kha nang:
- Tao nhieu approach hoan toan khac nhau
- Pha bo assumption mac dinh
- Reverse-engineer problem
- "What if" reasoning
- Tu phat trien capability moi khi can

### Implementation

#### 3.1 Divergent Thinking Phase (truoc execution, cho complex tasks)

Them phase moi vao pipeline, chi trigger khi `complexity == "complex" | "expert"`:

```
PIPELINE (moi):
  Planning → [DIVERGENT THINKING] → Turn Loop → Report

DIVERGENT THINKING phase:
  1. Generate 3 COMPLETELY DIFFERENT approaches (parallel agents, temp 1.0)
  2. Score approaches by: feasibility, novelty, risk, coverage
  3. Select best approach OR hybrid
  4. Feed selected approach into Turn Loop as "strategic frame"
```

Tao file `app/orchestration/divergent_thinker.py`:

```python
class DivergentThinker:
    """Generates multiple radically different approaches to a problem.

    Runs BEFORE the turn loop for complex tasks.
    Each "thinker" gets the same problem but different CONSTRAINTS:
    - Thinker A: "Solve this with MINIMUM tools (elegant, simple)"
    - Thinker B: "Solve this UNCONVENTIONALLY (break assumptions)"
    - Thinker C: "Solve this by REVERSING the problem (work backwards from goal)"
    """

    FRAMES = [
        {
            "id": "minimalist",
            "instruction": (
                "Find the SIMPLEST possible solution. What is the minimum "
                "set of actions that achieves the goal? Challenge every step — "
                "is it truly necessary? Can 5 steps become 2?"
            ),
        },
        {
            "id": "unconventional",
            "instruction": (
                "BREAK every assumption. What if the obvious approach is wrong? "
                "What would a completely different expert do? "
                "What tools/methods are being OVERLOOKED? "
                "Think laterally — combine things that don't usually go together."
            ),
        },
        {
            "id": "reverse",
            "instruction": (
                "Work BACKWARDS from the perfect end state. "
                "If the task is perfectly completed, what does the output look like? "
                "What's the LAST step before completion? The step before that? "
                "Trace the chain all the way back to where we are now. "
                "What prerequisite is everyone else missing?"
            ),
        },
    ]

    async def generate_approaches(
        self,
        user_message: str,
        plan_data: dict,
        memory_context: str,
    ) -> list[DivergentApproach]:
        """Generate 3 radically different approaches in parallel."""
        # Temperature 1.0 for maximum creativity
        # Each approach: strategy, key_insight, risk, first_action
        ...

    async def select_best(
        self,
        approaches: list[DivergentApproach],
        user_message: str,
    ) -> SelectedStrategy:
        """Score and select (or hybridize) the best approach.

        Scoring criteria:
        - Feasibility (can we actually do this with available tools?)
        - Novelty (does this bring a genuinely new angle?)
        - Coverage (does this address ALL aspects of the user's request?)
        - Risk (what could go wrong?)

        May return a HYBRID that combines best elements from multiple approaches.
        """
        ...
```

#### 3.2 Continuous Novelty in Evolution Agent

Evolution Agent (temp 1.0) duoc bo sung them 1 diem:

```python
# Trong system prompt cua Evolution, them:
"""
## CREATIVE DIVERGENCE MANDATE
When evaluating, also consider:
- Is the current approach the ONLY way? What's a completely different path?
- What would happen if we REVERSED an assumption?
- Is there a tool/capability the team hasn't considered?
- Can the agent CREATE a new tool (scaffold_custom_tool_module) to solve this better?
- Is there a way to SKIP steps that seem required but actually aren't?

If you identify a genuinely novel approach that could break a stalemate,
include it in improvement_suggestions with prefix "[NOVEL]".
"""
```

#### 3.3 Self-Development Capability

Agents duoc day "khi can capability moi, TAO NO":

Trong execution prompt, them section:

```
## SELF-DEVELOPMENT (when you need capabilities you don't have)
You are not limited to existing tools. When you identify a gap:

1. ASSESS: "Do I need a tool that doesn't exist yet?"
2. CREATE: Use scaffold_custom_tool_module() to build it
3. LOAD: The tool becomes immediately available
4. USE: Continue your task with the new capability

Examples of self-development:
- Need to parse a specific file format? → Create a parser tool
- Need to interact with an API not covered? → Create an API wrapper tool
- Need a complex calculation? → Create a computation tool
- Need to manage state across turns? → Create a state management tool

You can also CREATE SKILLS (reusable strategies):
- scaffold_skill(skill_id="...", body="...") → Creates reusable workflow
- reload_skills() → Makes it available system-wide

NEVER say "I can't do X because no tool exists." CREATE THE TOOL.
```

---

## Integration: How the 3 Layers Work Together

```
USER REQUEST
    │
    ▼
┌─────────────────────────────────────────────┐
│ PLANNING (Orchestrator)                     │
│  + Infrastructure Map (Layer 1)             │
│  + Divergent Thinking (Layer 3, if complex) │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│ TURN LOOP                                   │
│                                             │
│  Agent Execution:                           │
│   • Self-Awareness: check tools, env,       │
│     bootstrap if missing (Layer 1)          │
│   • Workspace-Aware: check existing files   │
│   • If stuck → Metacognitive Analysis       │
│     (Layer 2) → concrete different action   │
│   • If needs new capability → Self-Dev      │
│     (Layer 3) → scaffold tool/skill         │
│                                             │
│  Evolution Agent (temp 1.0):                │
│   • Quality critique (existing)             │
│   • Creative alternatives (Layer 3)         │
│   • Metacognitive patterns (Layer 2)        │
│                                             │
│  Orchestrator Evaluation:                   │
│   • Full accumulated briefing               │
│   • Metacognitive override if stagnant      │
│   • Self-bootstrap recommendations          │
└─────────────────────────────────────────────┘
```

---

## Implementation Priority Order

### Phase 1: Self-Awareness (lam truoc vi fix van de truc tiep nhat)
1. `app/core/infrastructure_map.py` — build dynamic capability map
2. Tool `introspect_tools` — agent hoi ve capabilities
3. Inject infrastructure map + bootstrap doctrine vao execution prompt
4. Test: agent tu setup Alibaba Cloud khi thieu credentials

### Phase 2: Metacognitive Loop (fix vong lap)
1. `app/orchestration/metacognition.py` — MetacognitiveAgent class
2. Integrate vao AgentWorker._detect_stuck() — replace generic warning
3. Integrate vao NexusOrchestrator.plan_next_turn() — stagnation override
4. Test: task bi ket → metacognition phan tich → breakthrough action

### Phase 3: Creative Divergence (them dot pha)
1. `app/orchestration/divergent_thinker.py` — 3-frame approach generator
2. Integrate vao pipeline.run() — after planning, before turn loop
3. Bo sung Evolution prompt voi creative mandate
4. Bo sung self-development section vao execution prompt
5. Test: complex task → 3 approaches → best selected → novel solution

---

## Estimated Changes Per Phase

| Phase | New Files | Modified Files | LLM Calls Added |
|-------|-----------|----------------|-----------------|
| 1. Self-Awareness | 2 | 3 (prompts, pipeline, tool_registry) | 0 (rule-based) |
| 2. Metacognition | 1 | 3 (agent_worker, nexus_orchestrator, pipeline) | 1 per stuck event |
| 3. Divergence | 1 | 3 (pipeline, prompts, evolution_agent) | 4 per complex task (3 thinkers + 1 selector) |

---

## Key Design Principles

1. **Graceful degradation** — moi layer moi la optional, system van chay binh thuong neu tat
2. **Budget-aware** — metacognition chi trigger khi thuc su stuck, divergence chi cho complex tasks
3. **Observable** — moi layer emit events cho frontend (transparency)
4. **Self-reinforcing** — XSkill accumulate hoc tu metacognitive insights cho lan sau
5. **No infinite recursion** — metacognition chi chay 1 lan per stuck event, divergence chi 1 lan per task

---

*Architecture Plan — Ready for Implementation*
*Estimated total: 4 new files, ~800 lines code*
