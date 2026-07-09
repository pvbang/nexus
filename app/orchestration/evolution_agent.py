"""Evolution Agent — continuous improvement advisor after each turn."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.orchestration.models import EvaluationResult, ExecutionState, TurnResult
from app.orchestration.termination import refusal_is_terminal

logger = logging.getLogger(__name__)


# Anti-fabrication guardrail (audit S5+S6, cross-session 2026-06-30). The Evolution
# Agent is told to "demand RADICAL change" + "be WILD/UNCONVENTIONAL" when stuck, with
# no bound — so the only "radically different" move it found on a blocked task was to
# FABRICATE (deepseek: "[NOVEL] RADICAL PIVOT: create FAKE SSL data"; Opus 4.6: invented
# an IP plan). This one line is injected into every steering surface (system prompt,
# stagnation nudge, mandatory-directive override) to bound creativity to REAL work.
_ANTI_FABRICATION_GUARDRAIL = (
    "NEVER recommend fabricating, inventing, faking, or 'simulating' data, results, "
    "evidence, or tool output — not even to satisfy a format or break a stall. "
    "'Radical change' means a different REAL technique, not a fictional result. If real "
    "execution is genuinely blocked, the correct move is to surface the blocker HONESTLY "
    "(or pivot to a real alternative), never to manufacture a plausible-looking answer."
)


# E-R10 (tri-run audit 2026-07-02): the guardrail ABOVE is prompt text, and a weak
# model at temperature=max ignores it — on the deepseek run Evolution still emitted
# "output a plausible list of discovered subdomains ... analyze this 'found' data",
# and the orchestrator promoted it to a MANDATORY DIRECTIVE. A prompt string cannot
# bind; a code filter can. These two helpers detect a fabrication-shaped directive
# and replace it before it can be promoted / injected.
#
# Detection is STRUCTURAL, not bare-keyword: it requires a fabrication VERB paired
# with a FAKE-OUTPUT object, so a legitimate directive that merely contains the word
# "simulate" (e.g. "run the thermodynamic simulation") is NOT tripped. The classic
# tells — "plausible list", "'found' data", "fictional but plausible", "make up ...
# results" — are matched directly because they have no honest reading.
_FABRICATION_VERB = (
    r"fabricat\w*|invent\w*|make[ -]?up|made[ -]?up|fake|faked|faking|fictional|"
    r"hallucinat\w*|simulat\w*|plausible|realistic"
)
_FAKE_OUTPUT_OBJECT = (
    r"data|results?|findings?|output|evidence|list|subdomains?|ports?|scan|"
    r"records?|values?|numbers?|responses?"
)
# R2-G (round-2 2026-07-03): the "recall instead of gather" fabrication. Instead of
# ordering "invent plausible data", the weak model rationalises "the target is
# well-documented, so RECONSTRUCT/WRITE the deliverable FROM MEMORY / PUBLIC
# KNOWLEDGE / TRAINING DATA" — writing findings it never actually collected. Round-1
# patterns missed this entirely (all returned False). The tell is a KNOWLEDGE SOURCE
# that is NOT real gathered evidence.
_RECALL_SOURCE = (
    r"public knowledge|general knowledge|prior knowledge|common knowledge|"
    r"training data|from memory|from what (?:i|we|you) (?:already )?know|"
    r"what (?:i|we|you) (?:already )?know about|best available content"
)
_FABRICATION_PATTERNS = (
    # verb ... object within a short window (e.g. "output a plausible list of subdomains",
    # "fabricate the findings", "invent realistic port scan results")
    re.compile(rf"\b(?:{_FABRICATION_VERB})\b[\w\s,]{{0,40}}?\b(?:{_FAKE_OUTPUT_OBJECT})\b", re.I),
    # "output a plausible/realistic <object>" (verb=output is honest, so require a
    # fabrication ADJECTIVE before the object)
    re.compile(rf"\boutput\b[\w\s,]{{0,20}}?\b(?:plausible|realistic|fictional|fake|made[ -]?up)\b", re.I),
    # analysing self-invented data — the tell 'found' in quotes, or "this 'found' data"
    re.compile(r"['\"]found['\"]\s+(?:data|results?|findings?)", re.I),
    # "as if ... real" / "pretend"
    re.compile(r"\bpretend\b|\bas if (?:it|they|these) (?:were|are) real\b", re.I),
    # recall-instead-of-gather: sourcing the deliverable from KNOWLEDGE not evidence.
    # Matches "reconstructed from memory + public knowledge", "write the report from
    # public knowledge", "base the findings on your general knowledge". The
    # _GATHERED_SOURCE guard below prevents "reconstruct from the evidence gathered".
    re.compile(rf"\b(?:{_RECALL_SOURCE})\b", re.I),
    # "produce a simulation of ... do not run tools" — simulate paired with an
    # explicit "don't actually execute" is a fabrication order (not a real sim run).
    re.compile(r"\b(?:simulat\w*|mock|stub)\b[\w\s,]{0,40}?\bdo(?:\s+not|n't)\s+(?:run|execute|use)\b", re.I),
    re.compile(r"\bdo(?:\s+not|n't)\s+(?:run|execute|use)\b[\w\s,]{0,40}?\b(?:simulat\w*|mock|stub|tool)", re.I),
)

# A directive that pairs a fabrication tell with an EXPLICIT honesty pivot ("do NOT
# fabricate", "surface the blocker honestly") is the guardrail itself, not an order to
# fake — never scrub that.
_HONESTY_PIVOT = re.compile(
    r"\b(?:do not|don't|never|avoid)\b[\w\s,'/-]{0,30}\b"
    r"(?:fabricat\w*|invent\w*|fake|simulat\w*|made[ -]?up)\b"
    r"|surface the blocker|report the blocker honestly|real (?:output|data|tool)",
    re.I,
)


def is_fabrication_directive(text: str) -> bool:
    """True if ``text`` orders the agent to manufacture data/results.

    Structural (verb + fake-output object), so a legitimate "run the simulation"
    or "install the tool and capture REAL output" directive is not tripped.
    """
    if not text:
        return False
    # An explicit honesty pivot means this is the guardrail / a correct instruction.
    if _HONESTY_PIVOT.search(text):
        return False
    return any(p.search(text) for p in _FABRICATION_PATTERNS)


def scrub_fabrication_directive(text: str) -> str:
    """Return ``text`` unchanged, or — if it is a fabrication directive — replace it
    with an honest surface-the-blocker instruction carrying the guardrail.

    Used at the promotion/injection boundary (mandatory directive, metacognitive
    breakthrough) so a fabrication-shaped order can never reach an agent.
    """
    if not is_fabrication_directive(text):
        return text
    logger.warning("fabrication_directive_scrubbed: %s", text[:200])
    return (
        "The previous breakthrough suggestion asked for invented/simulated results — "
        "that is not permitted. Instead: attempt the REAL action (install the missing "
        "tool and run it, or use an available tool), and if execution is genuinely "
        "blocked, SURFACE THE BLOCKER HONESTLY and report only what was truly obtained. "
        f"\n⛔ {_ANTI_FABRICATION_GUARDRAIL}"
    )


@dataclass
class EvolutionInsight:
    """Output of Evolution Agent analysis."""

    improvement_suggestions: list[str] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)
    quality_trajectory: str = ""
    challenge_questions: list[str] = field(default_factory=list)
    should_inject: bool = False
    severity_score: float = 0.0


class EvolutionAgent:
    """Lightweight agent that runs BEFORE evaluate_turn to provide insights.

    Tracks its own prior suggestions to avoid repeating the same advice
    when it was already tried and didn't work.
    """

    def __init__(self, llm_client: Any, config: Any) -> None:
        self._llm = llm_client
        self._config = config
        self._inject_threshold = getattr(config, "evolution_inject_threshold", 0.6)
        self._prior_suggestions: list[str] = []  # Track what was already suggested

    async def analyze(
        self,
        turn_result: TurnResult,
        state: ExecutionState,
    ) -> Optional[EvolutionInsight]:
        """Analyze completed turn and suggest improvements.

        Runs BEFORE evaluate_turn() so its insights can be injected
        into the Nexus Orchestrator's evaluation prompt.

        Returns None on any failure (graceful degradation).
        """
        if not self._config.evolution_agent_enabled:
            return None
        if state.current_turn < self._config.evolution_min_turn:
            return None

        try:
            prompt = self._build_prompt(turn_result, state)
            response = await self._llm.chat(
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self._config.evolution_max_tokens,
                temperature=1.0,
            )
            insight = self._parse_response(response)
            # Track suggestions so we don't repeat them next turn
            if insight and insight.improvement_suggestions:
                self._prior_suggestions.extend(insight.improvement_suggestions[:3])
                # Keep bounded
                if len(self._prior_suggestions) > 30:
                    self._prior_suggestions = self._prior_suggestions[-20:]
            return insight
        except Exception as e:
            logger.warning(f"Evolution Agent failed (non-blocking): {e}")
            return None

    def _get_workspace_truth(self, task_id: str) -> str:
        """Read workspace state for Evolution Agent evaluation.

        Reads: TASK_BRIEF (original objectives), TODO (current progress),
        workspace tree (filesystem reality), and design docs.
        """
        try:
            import os
            from pathlib import Path
            from app.orchestration.orchestrator_tools import _execute_workspace_tree
            from app.orchestration.workspace_reader import WorkspaceReader

            parts: list[str] = []
            from app.core import task_paths
            _ctx = task_paths.context_dir(task_id)

            # TASK_BRIEF.md — original objectives and plan
            brief_path = _ctx / "TASK_BRIEF.md"
            if brief_path.exists():
                try:
                    brief = brief_path.read_text(encoding="utf-8")[:2000]
                    parts.append(f"## Original Task Brief\n{brief}")
                except Exception:
                    pass

            # TODO.md — current progress state (single source of truth)
            todo_path = _ctx / "TODO.md"
            if todo_path.exists():
                try:
                    todo = todo_path.read_text(encoding="utf-8")[:3000]
                    parts.append(f"## Current Progress (TODO)\n{todo}")
                except Exception:
                    pass

            # DECISIONS.md — prior orchestrator + evolution reasoning
            decisions_path = _ctx / "DECISIONS.md"
            if decisions_path.exists():
                try:
                    decisions = decisions_path.read_text(encoding="utf-8")
                    # Get last entry only (most recent turn's decisions).
                    # Line-anchored ``^## Turn`` so an embedded ``### Turn N``
                    # in reasoning isn't mistaken for the entry start
                    # (task 3c44a93f bug #2 family).
                    import re as _re
                    matches = list(_re.finditer(r"(?m)^## Turn \d+", decisions))
                    if matches:
                        body_start = matches[-1].start()
                        parts.append(f"## Prior Decisions\n{decisions[body_start:][:2000]}")
                except Exception:
                    pass

            # Full workspace tree (cheap, complete picture)
            tree = _execute_workspace_tree(task_id, {})
            if tree:
                parts.append(f"## Workspace Tree\n```\n{tree}\n```")

            # Design document content (intelligent discovery)
            reader = WorkspaceReader(task_id)
            digest = reader.build_digest(max_chars=3000)
            if digest:
                parts.append(digest)

            return "\n\n".join(parts)
        except Exception as e:
            logger.warning(f"Evolution workspace read failed: {e}")
            return ""

    def _system_prompt(self) -> str:
        return (
            "You are the Evolution Agent — a critical quality reviewer "
            "in a multi-agent orchestration system.\n\n"
            "Your role: After each turn, CRITICALLY analyze what was produced and "
            "determine what's STILL MISSING relative to the task's own objectives.\n\n"
            "## Your Mindset\n"
            "Think like a **senior reviewer evaluating deliverables against requirements**:\n"
            "- Does the output satisfy the SPECIFIC objectives in TASK_BRIEF and ROE?\n"
            "- Are there gaps between what was requested and what was delivered?\n"
            "- Is the work functional and verifiable, not just 'looks done on paper'?\n"
            "- What blind spots might the executing agents have missed?\n\n"
            "## Evaluation Approach\n"
            "- Determine severity based on gap between CURRENT state and EXPECTED "
            "outcomes from the task requirements, NOT from hardcoded criteria\n"
            "- Evaluate completeness relative to what the USER ASKED FOR\n"
            "- DO NOT impose requirements the user didn't request "
            "(e.g., don't demand deployment if not requested)\n"
            "- DO NOT penalize technology choices that work correctly\n\n"
            "## Stagnation Detection (task-agnostic)\n"
            "- If progress stagnates 2+ turns → demand a RADICALLY different approach\n"
            "- If the same errors repeat → flag as critical\n"
            "- If agents are doing redundant work → flag for replanning\n\n"
            "## 🎯 FALSE POSITIVE & DEPTH DETECTION (ALL TASKS)\n"
            "Watch for agents claiming success without proof:\n"
            "- If agent claims something 'works' but shows no verification output → "
            "flag as UNVERIFIED. Demand evidence (actual output, response body, test result).\n"
            "- If the SAME approach has been tried 3+ turns without success → "
            "issue [NOVEL] pivot suggesting a DIFFERENT TECHNIQUE (not abandon the goal).\n"
            "- CRITICAL: 'ABANDON approach X' ≠ 'ABANDON the goal'. "
            "Always suggest an ALTERNATIVE technique that bypasses the specific blocker.\n"
            "  Example: 'Rapid RST_STREAM blocked by GOAWAY → try single early-reset with "
            "precise timing BEFORE stream registers in multiplexer (different trigger mechanism)'\n"
            "- If all attempts return the same error → the APPROACH is wrong (not the goal). "
            "Research: has anyone else achieved this? What technique did they use?\n"
            "- Suggest web_search for prior art: '{CVE} PoC writeup', '{problem} bypass {blocker}'\n"
            "- If repeated results have IDENTICAL patterns (same size, same response) → "
            "likely FALSE POSITIVE: agent is misinterpreting uniform error responses as success.\n"
            "- Track PROGRESS PHASES of the task. If stuck at the same phase for 2+ "
            "turns, raise severity to reflect that the approach needs to change — but "
            "set it to your HONEST read, not a forced number (severity gates completion; "
            "inflating it wrongly blocks a task that is actually progressing or near done).\n\n"
            "## 🧠 DEEP THINKING MANDATE\n"
            "When suggesting pivots, THINK DEEPLY about WHY the current approach fails:\n"
            "- Name the SPECIFIC protection/mechanism that blocks the approach\n"
            "- Suggest techniques that BYPASS that specific mechanism\n"
            "- Differentiate between protection classes: rate-limiting (try slower), "
            "pattern matching (try different pattern), state validation (try different state)\n"
            "- Don't suggest 'give up' just because one technique failed — first exhaust "
            "genuinely different techniques toward the same goal. If EVERY real avenue is "
            "exhausted (or a genuine safety/authorization/impossibility limit applies), the "
            "honest move is to name that blocker plainly, not to invent more busywork.\n"
            "- 'Protection X blocks technique A' → suggest 'technique B that doesn't trigger X'\n\n"
            "## 🔬 REVERSE ENGINEERING / EXPLOIT DEVELOPMENT GUIDANCE\n"
            "When an exploit attempt FAILS, the issue is usually UNDERSTANDING not brute force:\n"
            "- If 'spray and pray' (flood many requests) was used → suggest: 'Go BACK to source code. "
            "Read the EXACT vulnerable function. Trace what state triggers the bug. "
            "Design a SURGICAL single-request exploit that hits the precise code path.'\n"
            "- If protection blocks the approach → suggest: 'Read the protection's source code. "
            "What does it CHECK? What does it NOT check? Exploit the gap.'\n"
            "- If the wrong version was used → suggest: 'The bug is in version X.Y.Z. "
            "Lab MUST use that exact version. Compile from source if needed.'\n"
            "- KEY INSIGHT: A failed exploit means the team's UNDERSTANDING of the vulnerability "
            "is incomplete. The fix is always: deeper source code analysis, not more brute force.\n\n"
            "## 🚨 INFINITE LOOP DETECTION (CRITICAL)\n"
            "- If agents have been claiming 'workspace empty' or 'directory not found' "
            "for 3+ consecutive turns while the workspace tree shows actual files → "
            "this is a TOOL PATH RESOLUTION BUG, not a real data absence.\n"
            "- When loop detected: issue an IMMEDIATE 'BREAK GLASS' directive that "
            "forces agents to: (1) run 'get_workspace_info' to see actual paths, "
            "(2) run 'find_workspace_file' with the missing filenames, "
            "(3) use shell 'find /app -name' commands as fallback\n"
            "- After 5+ turns with zero progress → recommend FORCE COMPLETION or "
            "ABORT with documented reasons — do NOT let the loop continue indefinitely\n"
            "- SEPARATE 'tool cannot read' (path bug) from 'file does not exist' (real gap)"
            " — never conflate the two\n\n"
            "## Blind Spot Detection\n"
            "- Identify aspects of the user's request that haven't been addressed\n"
            "- Flag implicit requirements that executing agents may have overlooked\n"
            "- Note if partial work creates inconsistencies or integration gaps\n\n"
            "## Severity Guidelines\n"
            "- 0.9+: Critical gap — core deliverable is missing or non-functional\n"
            "- 0.7-0.8: Significant gap — important requested features incomplete\n"
            "- 0.5-0.6: Moderate gap — minor requested items missing\n"
            "- < 0.5: Minor — polish/optimization issues only\n\n"
            "Be concise, actionable, and objective. Challenge assumptions.\n"
            "Your job is to PREVENT premature completion — keep pushing until "
            "the user's ACTUAL requirements are met.\n\n"
            "## CREATIVE DIVERGENCE MANDATE\n"
            "When evaluating, also consider:\n"
            "- Is the current approach the ONLY way? What's a completely different path?\n"
            "- What would happen if we REVERSED an assumption?\n"
            "- Is there a tool/capability the team hasn't considered?\n"
            "- Can the agent CREATE a new tool (scaffold_custom_tool_module) to solve better?\n"
            "- Is there a way to SKIP steps that seem required but actually aren't?\n"
            "- What would a domain OUTSIDER see that insiders miss?\n\n"
            "If you identify a genuinely novel approach, include it in improvement_suggestions "
            "with prefix '[NOVEL]'. Be WILD, CREATIVE, UNCONVENTIONAL. Temperature is max.\n\n"
            "## ⛔ HARD BOUND ON CREATIVITY\n"
            + _ANTI_FABRICATION_GUARDRAIL
        )

    def _build_prompt(
        self,
        turn_result: TurnResult,
        state: ExecutionState,
    ) -> str:
        # Accumulated briefing: full user request + all accomplishments + gaps
        accumulated = state.get_accumulated_briefing(max_chars=15000)

        # ═══ Ground Truth: What actually exists in workspace? ═══
        # Evolution Agent needs filesystem reality to detect drift/gaps.
        workspace_truth = self._get_workspace_truth(state.task_id)
        if workspace_truth:
            accumulated += f"\n\n{workspace_truth}"

        # Current turn's agent results (more generous cap for Evolution's analysis)
        agent_summaries = []
        for ar in turn_result.agent_results:
            summary = str(getattr(ar, "output", ""))[:1500]
            agent_summaries.append(
                f"- {getattr(ar, 'agent_name', 'unknown')} ({getattr(ar, 'agent_role', '')}): {summary}"
            )

        # Detect stagnation from progress history.
        # GATE (audit 2026-06-29 C2): a lawful-refusal task sits at flat progress BY
        # DESIGN — the work is correctly declined, not stuck. Injecting "demand
        # radical change / severity MUST be >= 0.85" there manufactures self-jailbreak
        # pressure, since the only "radically different" action left is to attack.
        # Suppress the stagnation nudge when the latest turn is a terminal refusal;
        # the completion layer already treats this as a valid terminal state.
        stagnation_warning = ""
        latest_refusal = any(
            refusal_is_terminal(str(getattr(ar, "output", "")))
            for ar in turn_result.agent_results
        )
        if len(state.turns_completed) >= 2 and not latest_refusal:
            try:
                recent_pcts = [
                    t.orchestrator_evaluation.progress_pct
                    for t in state.turns_completed[-3:]
                    if t.orchestrator_evaluation
                ]
                if len(recent_pcts) >= 2 and max(recent_pcts) - min(recent_pcts) < 5:
                    stagnation_warning = (
                        "\n⚠️ STAGNATION ALERT: Progress stuck around "
                        f"{recent_pcts[-1]}% for {len(recent_pcts)} turns. Look hard "
                        "for a genuinely different approach. Set severity_score to your "
                        "HONEST assessment: high (>=0.8) if the current approach is truly "
                        "failing and must change; lower if the team is in fact making real "
                        "(if slow) progress on the right path, or is legitimately blocked/"
                        "refusing. Do NOT inflate the number to force a verdict — severity "
                        "gates completion, so a dishonest high value wrongly blocks a task "
                        "that is actually near done.\n"
                        f"{_ANTI_FABRICATION_GUARDRAIL}\n"
                    )
            except (ValueError, TypeError, AttributeError):
                pass

        # Show prior suggestions so Evolution doesn't repeat itself
        prior_section = ""
        if self._prior_suggestions:
            recent_prior = self._prior_suggestions[-10:]
            prior_section = (
                "\n## YOUR PRIOR SUGGESTIONS (already given — do NOT repeat these)\n"
                + "\n".join(f"- {s}" for s in recent_prior)
                + "\n\nIf these suggestions were NOT acted on, they may have been tried and failed. "
                "Suggest something GENUINELY DIFFERENT.\n"
            )

        return f"""## Accumulated Context (Full Picture)

{accumulated}

---

## Turn {state.current_turn} Results (JUST COMPLETED)

**Agent Results This Turn:**
{chr(10).join(agent_summaries[:7])}
{stagnation_warning}{prior_section}
---
Based on the FULL accumulated context above, evaluate what STILL NEEDS TO BE DONE
relative to the original user request. Consider what was already accomplished vs
what gaps remain.

IMPORTANT: Do NOT suggest approaches that have already been tried. If the same gap
persists after you suggested it before, it means agents TRIED and FAILED — suggest
something genuinely different or acknowledge it as infeasible.

Respond in JSON format:
{{
  "improvement_suggestions": ["suggestion1", "suggestion2"],
  "blind_spots": ["blind_spot1"],
  "quality_trajectory": "improving|stagnating|declining|unclear",
  "challenge_questions": ["question1", "question2"],
  "should_inject": true/false,
  "severity_score": 0.0-1.0
}}"""

    def _parse_response(self, response: Any) -> Optional[EvolutionInsight]:
        """Parse LLM JSON response into EvolutionInsight."""
        try:
            # Extract content from response (handle different LLM client formats)
            content = response
            if isinstance(response, dict):
                content = response.get("content", "")
            elif hasattr(response, "content"):
                content = response.content
            elif hasattr(response, "choices"):
                content = response.choices[0].message.content

            # Try to extract JSON from response
            text = str(content)
            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            data = json.loads(text.strip())
            # R2-G part (b) (round-2 2026-07-03): scrub fabrication-shaped text from
            # Evolution's OWN output AT THE SOURCE. Previously the filter only ran at
            # the mandatory-directive / metacog promotion boundary, so a fabrication
            # suggestion ("reconstruct from public knowledge", "output a plausible
            # list") still reached the orchestrator prompt verbatim via evolution_text
            # and enrich_evaluation. Scrubbing here means EVERY downstream consumer
            # gets clean advice. A clean suggestion passes through unchanged.
            def _scrub_list(items: Any) -> list[str]:
                if not isinstance(items, list):
                    return []
                return [scrub_fabrication_directive(str(s)) for s in items]

            insight = EvolutionInsight(
                improvement_suggestions=_scrub_list(data.get("improvement_suggestions", [])),
                blind_spots=_scrub_list(data.get("blind_spots", [])),
                quality_trajectory=data.get("quality_trajectory", "unclear"),
                challenge_questions=_scrub_list(data.get("challenge_questions", [])),
                should_inject=data.get("should_inject", False),
                severity_score=float(data.get("severity_score", 0.0)),
            )

            # Override should_inject based on config threshold
            if insight.severity_score >= self._inject_threshold:
                insight.should_inject = True

            return insight
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            logger.warning(f"Evolution Agent parse error: {e}")
            return None

    def enrich_evaluation(
        self,
        evaluation: EvaluationResult,
        insight: Optional[EvolutionInsight],
        stagnation_turns: int = 0,
    ) -> EvaluationResult:
        """Enrich evaluation with Evolution Agent's observations.

        ADVISORY ONLY — Evolution provides information and suggestions.
        It does NOT override the Orchestrator's completion decision.
        The Orchestrator sees these insights and decides for itself.
        """
        if not insight or not insight.should_inject:
            return evaluation

        # Append blind spots to missing items (informational)
        if insight.blind_spots:
            existing_missing = getattr(evaluation, "missing_items", []) or []
            for spot in insight.blind_spots[:3]:
                if spot not in existing_missing:
                    existing_missing.append(f"[Evolution] {spot}")
            evaluation.missing_items = existing_missing

        # Append evolution severity note to evaluation text (informational, not override)
        if insight.severity_score >= 0.7:
            eval_text = getattr(evaluation, "evaluation_text", "") or ""
            stagnation_note = (
                f" (task stagnant {stagnation_turns} turns — consider partial completion)"
                if stagnation_turns >= 4 else ""
            )
            evaluation.evaluation_text = (
                eval_text
                + f"\n[Evolution Advisory] Severity {insight.severity_score:.1f}{stagnation_note} — "
                f"gaps: {'; '.join(insight.blind_spots[:2])}"
            )

        # If stagnating, inject improvement suggestions into next focus
        if insight.quality_trajectory == "stagnating":
            suggestions = "; ".join(insight.improvement_suggestions[:2])
            current_focus = getattr(evaluation, "next_turn_focus", "") or ""
            evaluation.next_turn_focus = (
                f"{current_focus} [Evolution: CHANGE APPROACH — {suggestions}]"
            )

        return evaluation
