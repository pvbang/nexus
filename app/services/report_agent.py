"""
MekongAI Nexus - ReACT Report Agent.

Autonomous report generation with tool use.
Pattern from MiroFish: Outline Planning → Per-section ReACT loops.

ReACT Loop per section:
  Thought → Action → Observation → repeat → Final Answer

Safety: max iterations per section, overall timeout, fallback synthesis.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import AsyncGenerator
from typing import Any, Optional

import structlog

from app.mcp.tool_executor import ToolExecutor
from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)

# ReACT loop safety limits
DEFAULT_MAX_REACT_ITERATIONS = 5
DEFAULT_MIN_TOOL_CALLS = 2
DEFAULT_SECTION_TIMEOUT_S = 120

# Model output token limits — used by _get_adaptive_max_tokens()
# Maps model name patterns to their maximum output token capacity.
MODEL_OUTPUT_LIMITS: dict[str, int] = {
    "gpt-4o": 16384,
    "gpt-4-turbo": 4096,
    "gpt-3.5": 4096,
    "claude-3": 65536,
    "claude-4": 65536,
    "gemini": 32768,
    "bedrock/anthropic": 65536,
    "bedrock/amazon": 8192,
    # Bedrock cross-region inference models (us.anthropic.claude-*)
    "us.anthropic": 65536,
    # litellm bedrock/ prefix
    "bedrock/us.anthropic": 65536,
    # OpenRouter models
    "anthropic/claude": 65536,
    "openai/gpt-4o": 16384,
    "google/gemini": 32768,
    # Nemotron and other large output models
    "nemotron": 16384,
    "qwen": 32768,
    "llama": 16384,
    "deepseek": 32768,
}
_MODEL_OUTPUT_DEFAULT = 32768

# Chat mode constants
CHAT_MAX_REPORT_CONTEXT_CHARS = 15000

CHAT_SYSTEM_PROMPT = """You are a helpful report assistant. You have access to the following generated report:

<report>
{report_context}
</report>

Rules:
1. Answer questions based on the report content first
2. Only use tools when report content is insufficient
3. Be concise and direct
4. Use > quote format for citing report content

Available tools (use only if needed, max {max_tool_calls} calls):
{tools_description}

Tool call format:
<tool_call>
{{"name": "tool_name", "parameters": {{"param": "value"}}}}
</tool_call>
"""


# ============================================================
# Data classes
# ============================================================


@dataclass
class ReactStep:
    """Single step in the ReACT loop."""

    step_type: str  # "thought", "action", "observation", "final_answer"
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict[str, Any]] = None
    tool_result: Optional[str] = None


@dataclass
class SectionPlan:
    """Plan for a single report section."""

    title: str
    description: str
    research_queries: list[str] = field(default_factory=list)


@dataclass
class SectionResult:
    """Result of a single report section after ReACT loop."""

    title: str
    content: str
    sources: list[str] = field(default_factory=list)
    tool_calls_count: int = 0
    react_steps: list[ReactStep] = field(default_factory=list)


@dataclass
class ReportResult:
    """Final report output."""

    topic: str
    executive_summary: str
    sections: list[SectionResult]
    full_markdown: str
    metadata: dict[str, Any] = field(default_factory=dict)
    trajectory: list[dict[str, Any]] = field(default_factory=list)


# ============================================================
# Per-report Structured Logger
# ============================================================


class ReportLogger:
    """Per-report structured logging. Writes JSONL entries for each ReACT step.

    Each action is a single JSON line appended to ``agent_log.jsonl`` inside the
    report's output directory.  The logger is intentionally *fire-and-forget*:
    every public method is wrapped in ``try/except`` so logging failures never
    block report generation.
    """

    def __init__(self, report_id: str, output_dir: str = "workspace/reports") -> None:
        self._report_id = report_id
        self._start_time = time.perf_counter()
        self._log_dir = Path(output_dir) / report_id
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / "agent_log.jsonl"

    # ------------------------------------------------------------------
    # Core writer
    # ------------------------------------------------------------------

    def log(
        self,
        action: str,
        stage: str,
        details: dict[str, Any] | None = None,
        section_title: str | None = None,
        section_index: int | None = None,
    ) -> None:
        """Append one JSON line to ``agent_log.jsonl``.

        Thread-safe via file append mode.  Never raises – errors are
        silently swallowed so report generation is not interrupted.
        """
        try:
            entry: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(time.perf_counter() - self._start_time, 3),
                "report_id": self._report_id,
                "action": action,
                "stage": stage,
            }
            if section_title is not None:
                entry["section_title"] = section_title
            if section_index is not None:
                entry["section_index"] = section_index
            if details:
                entry["details"] = details

            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 – logging must never crash
            pass

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def log_start(self, task_description: str) -> None:
        """Log ``report_start`` action."""
        self.log("report_start", "initializing", {"task": task_description})

    def log_planning(self, outline: dict[str, Any]) -> None:
        """Log ``planning_complete`` action with the generated outline."""
        self.log("planning_complete", "planning", {"outline": outline})

    def log_section_start(self, title: str, index: int) -> None:
        """Log ``section_start`` action."""
        self.log(
            "section_start", "generating",
            section_title=title, section_index=index,
        )

    def log_tool_call(
        self,
        title: str,
        index: int,
        tool_name: str,
        tool_args: dict[str, Any],
        iteration: int,
    ) -> None:
        """Log ``tool_call`` action."""
        self.log(
            "tool_call", "generating",
            details={"tool": tool_name, "args": tool_args, "iteration": iteration},
            section_title=title, section_index=index,
        )

    def log_tool_result(
        self,
        title: str,
        index: int,
        tool_name: str,
        result_preview: str,
        iteration: int,
    ) -> None:
        """Log ``tool_result`` action (truncated preview)."""
        self.log(
            "tool_result", "generating",
            details={
                "tool": tool_name,
                "result_preview": result_preview[:500],
                "iteration": iteration,
            },
            section_title=title, section_index=index,
        )

    def log_section_complete(
        self,
        title: str,
        index: int,
        content_length: int,
        tool_calls_count: int,
    ) -> None:
        """Log ``section_complete`` action."""
        self.log(
            "section_complete", "generating",
            details={
                "content_length": content_length,
                "tool_calls_count": tool_calls_count,
            },
            section_title=title, section_index=index,
        )

    def log_complete(self, total_sections: int, elapsed_seconds: float) -> None:
        """Log ``report_complete`` action."""
        self.log(
            "report_complete", "completed",
            details={
                "total_sections": total_sections,
                "elapsed_seconds": round(elapsed_seconds, 3),
            },
        )

    def log_error(self, error: str, stage: str) -> None:
        """Log ``error`` action."""
        self.log("error", stage, details={"error": error})


# ============================================================
# ReACT Report Agent
# ============================================================


class ReportAgent:
    """
    Autonomous report generator using ReACT pattern.

    Pipeline:
    1. Plan outline (LLM generates sections structure)
    2. For each section: ReACT loop (Thought → Action → Observation)
    3. Combine all sections
    4. Generate executive summary
    5. Return ReportResult with full trajectory for XSkill
    """

    def __init__(
        self,
        llm: LLMClient,
        tool_executor: ToolExecutor,
        memory_manager: Optional[Any] = None,
    ):
        self.llm = llm
        self.tool_executor = tool_executor
        self.memory_manager = memory_manager
        self.report_logger: ReportLogger | None = None

    def _get_adaptive_max_tokens(self, desired: int) -> int:
        """Return max output tokens capped by the model's known limit.

        Looks up ``self.llm.model`` against ``MODEL_OUTPUT_LIMITS`` patterns
        and returns ``min(desired, model_limit)``.  Falls back to a safe
        default (8192) for unknown models.
        """
        model_name = getattr(self.llm, "model", "") or ""
        model_lower = model_name.lower()

        for pattern, limit in MODEL_OUTPUT_LIMITS.items():
            if pattern in model_lower:
                return min(desired, limit)

        return min(desired, _MODEL_OUTPUT_DEFAULT)

    async def generate_report(
        self,
        topic: str,
        requirements: str = "",
        max_sections: int = 5,
    ) -> ReportResult:
        """
        Full report generation pipeline.

        Args:
            topic: Report topic / research question.
            requirements: Additional constraints or focus areas.
            max_sections: Maximum number of sections to generate.

        Returns:
            ReportResult with all sections, summary, and trajectory.
        """
        start_time = time.perf_counter()
        trajectory: list[dict[str, Any]] = []
        total_tool_calls = 0

        # Generate a deterministic report_id from timestamp
        report_id = f"rpt_{int(time.time() * 1000)}"

        # Initialise per-report structured logger
        self.report_logger = ReportLogger(report_id)
        self.report_logger.log_start(
            f"topic={topic[:120]}, max_sections={max_sections}",
        )

        logger.info(
            "report_generation_started",
            topic=topic[:100],
            report_id=report_id,
            max_sections=max_sections,
        )

        # Step 1: Plan outline
        trajectory.append({
            "phase": "outline_planning",
            "status": "started",
            "timestamp_ms": _elapsed_ms(start_time),
        })

        section_plans = await self._plan_outline(topic, requirements, max_sections)

        self.report_logger.log_planning({
            "sections": [
                {"title": sp.title, "description": sp.description}
                for sp in section_plans
            ],
        })

        trajectory.append({
            "phase": "outline_planning",
            "status": "completed",
            "sections_count": len(section_plans),
            "sections": [sp.title for sp in section_plans],
            "timestamp_ms": _elapsed_ms(start_time),
        })

        logger.info(
            "report_outline_planned",
            sections=[sp.title for sp in section_plans],
        )

        # Step 2: ReACT loop for each section
        section_results: list[SectionResult] = []
        accumulated_context = f"Report topic: {topic}\nRequirements: {requirements}\n"

        for i, plan in enumerate(section_plans):
            self.report_logger.log_section_start(plan.title, i)

            trajectory.append({
                "phase": "section_react",
                "section_index": i,
                "section_title": plan.title,
                "status": "started",
                "timestamp_ms": _elapsed_ms(start_time),
            })

            # Build context from previous sections
            prev_summaries = "\n".join(
                f"- {sr.title}: {sr.content[:200]}..."
                for sr in section_results
            )
            section_context = (
                f"{accumulated_context}\n"
                f"Previous sections completed:\n{prev_summaries}\n"
                if prev_summaries
                else accumulated_context
            )

            result = await self._react_section(plan, section_context, i)
            section_results.append(result)
            total_tool_calls += result.tool_calls_count

            self.report_logger.log_section_complete(
                plan.title, i, len(result.content), result.tool_calls_count,
            )

            trajectory.append({
                "phase": "section_react",
                "section_index": i,
                "section_title": plan.title,
                "status": "completed",
                "tool_calls": result.tool_calls_count,
                "react_steps_count": len(result.react_steps),
                "sources": result.sources,
                "timestamp_ms": _elapsed_ms(start_time),
            })

            logger.info(
                "report_section_completed",
                section=plan.title,
                tool_calls=result.tool_calls_count,
                index=i,
            )

        # Step 3: Generate executive summary first (used by final synthesis)
        trajectory.append({
            "phase": "synthesis",
            "status": "started",
            "timestamp_ms": _elapsed_ms(start_time),
        })

        executive_summary = await self._generate_executive_summary(
            section_results, topic,
        )

        full_markdown = await self._generate_final_comprehensive_report(
            section_results, topic, requirements, executive_summary,
        )

        # Fallback: if comprehensive report is empty, use basic synthesis
        if not full_markdown or not full_markdown.strip():
            full_markdown = await self._synthesize_report(
                section_results, topic, requirements,
            )

        elapsed_ms = _elapsed_ms(start_time)
        elapsed_s = elapsed_ms / 1000.0
        trajectory.append({
            "phase": "synthesis",
            "status": "completed",
            "timestamp_ms": elapsed_ms,
        })

        report = ReportResult(
            topic=topic,
            executive_summary=executive_summary,
            sections=section_results,
            full_markdown=full_markdown,
            metadata={
                "report_id": report_id,
                "total_tool_calls": total_tool_calls,
                "generation_time_ms": round(elapsed_ms),
                "sections_count": len(section_results),
                "requirements": requirements,
            },
            trajectory=trajectory,
        )

        self.report_logger.log_complete(len(section_results), elapsed_s)

        logger.info(
            "report_generation_completed",
            topic=topic[:100],
            report_id=report_id,
            sections=len(section_results),
            total_tool_calls=total_tool_calls,
            time_ms=round(elapsed_ms),
        )

        return report

    # ================================================================
    # Section-by-Section Streaming
    # ================================================================

    async def generate_report_streaming(
        self,
        task_description: str,
        context: str = "",
        tools: list[str] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Generate report yielding each section as it completes.

        Reuses ``_plan_outline()`` and ``_react_section()`` internally but
        yields incremental events suitable for SSE / frontend polling.

        Yields dicts:
          {"type": "outline", "data": {"title": "...", "sections": [...]}}
          {"type": "section", "data": {"index": 0, "title": "...", "content": "...", "tool_calls": 3}}
          {"type": "complete", "data": {"report_id": "...", "total_sections": N, "elapsed_s": ...}}
          {"type": "error", "data": {"message": "...", "stage": "..."}}

        Args:
            task_description: Report topic / research question.
            context: Additional context or requirements.
            tools: Optional list of allowed tool names (unused currently,
                   reserved for future tool filtering).

        Yields:
            Event dicts with ``type`` and ``data`` keys.
        """
        start_time = time.perf_counter()
        report_id = f"rpt_{int(time.time() * 1000)}"

        # Initialise per-report structured logger
        self.report_logger = ReportLogger(report_id)
        self.report_logger.log_start(task_description[:200])

        logger.info(
            "report_streaming_started",
            topic=task_description[:100],
            report_id=report_id,
        )

        # ------------------------------------------------------------------
        # Step 1: Plan outline
        # ------------------------------------------------------------------
        try:
            section_plans = await self._plan_outline(
                task_description, context, max_sections=10,
            )
        except Exception as exc:
            error_msg = f"Outline planning failed: {exc}"
            logger.error("streaming_outline_error", error=str(exc))
            if self.report_logger:
                self.report_logger.log_error(error_msg, "planning")
            yield {"type": "error", "data": {"message": error_msg, "stage": "planning"}}
            return

        outline_data = {
            "title": task_description,
            "sections": [
                {"title": sp.title, "description": sp.description}
                for sp in section_plans
            ],
        }

        if self.report_logger:
            self.report_logger.log_planning(outline_data)

        yield {"type": "outline", "data": outline_data}

        # ------------------------------------------------------------------
        # Step 2: ReACT loop per section, yield as each completes
        # ------------------------------------------------------------------
        section_results: list[SectionResult] = []
        accumulated_context = (
            f"Report topic: {task_description}\nRequirements: {context}\n"
        )

        for idx, plan in enumerate(section_plans):
            if self.report_logger:
                self.report_logger.log_section_start(plan.title, idx)

            # Build context from already-completed sections
            prev_summaries = "\n".join(
                f"- {sr.title}: {sr.content[:200]}..."
                for sr in section_results
            )
            section_context = (
                f"{accumulated_context}\n"
                f"Previous sections completed:\n{prev_summaries}\n"
                if prev_summaries
                else accumulated_context
            )

            try:
                result = await self._react_section(plan, section_context, idx)
            except Exception as exc:
                error_msg = f"Section '{plan.title}' failed: {exc}"
                logger.error(
                    "streaming_section_error",
                    section=plan.title,
                    error=str(exc),
                )
                if self.report_logger:
                    self.report_logger.log_error(error_msg, "generating")
                yield {
                    "type": "error",
                    "data": {"message": error_msg, "stage": "generating"},
                }
                # Continue to next section instead of aborting
                continue

            section_results.append(result)

            # Persist section file to disk
            try:
                self._save_section_file(
                    report_id, idx, result.title, result.content,
                )
            except Exception as exc:
                logger.warning(
                    "section_file_save_failed",
                    report_id=report_id,
                    index=idx,
                    error=str(exc),
                )

            if self.report_logger:
                self.report_logger.log_section_complete(
                    plan.title, idx, len(result.content), result.tool_calls_count,
                )

            logger.info(
                "streaming_section_completed",
                section=plan.title,
                index=idx,
                tool_calls=result.tool_calls_count,
            )

            yield {
                "type": "section",
                "data": {
                    "index": idx,
                    "title": result.title,
                    "content": result.content,
                    "tool_calls": result.tool_calls_count,
                },
            }

        # ------------------------------------------------------------------
        # Step 3: Assemble full report & yield complete
        # ------------------------------------------------------------------
        try:
            full_markdown = self._assemble_full_report(
                report_id, outline_data, section_results,
            )
            # Persist assembled report
            report_dir = Path("workspace/reports") / report_id
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "full_report.md").write_text(
                full_markdown, encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(
                "full_report_assembly_failed",
                report_id=report_id,
                error=str(exc),
            )
            full_markdown = ""

        elapsed_s = round((time.perf_counter() - start_time), 3)

        if self.report_logger:
            self.report_logger.log_complete(len(section_results), elapsed_s)

        logger.info(
            "report_streaming_completed",
            report_id=report_id,
            total_sections=len(section_results),
            elapsed_s=elapsed_s,
        )

        yield {
            "type": "complete",
            "data": {
                "report_id": report_id,
                "total_sections": len(section_results),
                "elapsed_s": elapsed_s,
            },
        }

    # ================================================================
    # Section File Helpers
    # ================================================================

    def _save_section_file(
        self,
        report_id: str,
        index: int,
        title: str,
        content: str,
    ) -> Path:
        """Save individual section markdown file.

        Writes ``section_XX.md`` into ``workspace/reports/{report_id}/``.

        Args:
            report_id: Unique report identifier.
            index: Zero-based section index.
            title: Section title (used as H2 header in the file).
            content: Markdown content body.

        Returns:
            Path to the written file.
        """
        report_dir = Path("workspace/reports") / report_id
        report_dir.mkdir(parents=True, exist_ok=True)

        filename = f"section_{index:02d}.md"
        file_path = report_dir / filename

        section_md = f"## {title}\n\n{content}\n"
        file_path.write_text(section_md, encoding="utf-8")

        logger.debug(
            "section_file_saved",
            report_id=report_id,
            index=index,
            path=str(file_path),
        )

        return file_path

    def _assemble_full_report(
        self,
        report_id: str,
        outline: dict[str, Any],
        sections: list[SectionResult],
    ) -> str:
        """Assemble all sections into a single markdown report.

        Args:
            report_id: Unique report identifier.
            outline: Outline dict with ``title`` and ``sections`` keys.
            sections: List of completed ``SectionResult`` objects.

        Returns:
            Full markdown string with title, sections, and sources.
        """
        title = outline.get("title", "Untitled Report")

        # Build section bodies
        sections_md = "\n\n---\n\n".join(
            f"## {s.title}\n\n{s.content}" for s in sections
        )

        # Collect and deduplicate sources
        all_sources: list[str] = []
        for s in sections:
            all_sources.extend(s.sources)
        unique_sources = list(dict.fromkeys(all_sources))

        sources_md = ""
        if unique_sources:
            sources_md = "\n\n---\n\n## Sources\n\n" + "\n".join(
                f"- {src}" for src in unique_sources
            )

        full_report = (
            f"# {title}\n\n"
            f"*Generated by MekongAI Nexus ReACT Report Agent*\n"
            f"*Report ID: {report_id}*\n\n"
            f"---\n\n"
            f"{sections_md}"
            f"{sources_md}\n"
        )

        return full_report

    # ================================================================
    # Outline Planning
    # ================================================================

    async def _plan_outline(
        self,
        topic: str,
        requirements: str,
        max_sections: int,
    ) -> list[SectionPlan]:
        """LLM generates structured outline for the report."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a research report planner. Given a topic and requirements, "
                    "create a structured outline with sections.\n\n"
                    "Return ONLY valid JSON with this structure:\n"
                    '{"sections": [\n'
                    '  {"title": "Section Title", "description": "What to cover", '
                    '"research_queries": ["search query 1", "search query 2"]}\n'
                    "]}\n\n"
                    "Guidelines:\n"
                    f"- Maximum {max_sections} sections\n"
                    "- Each section should have 1-3 research queries for web search\n"
                    "- Sections should flow logically\n"
                    "- Cover different dimensions of the topic\n"
                    "- Include introduction and conclusion sections"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Topic: {topic}\n"
                    f"Requirements: {requirements or 'None specified'}\n\n"
                    f"Create a report outline with up to {max_sections} sections."
                ),
            },
        ]

        try:
            result = await self.llm.chat_json(messages, temperature=0.3)
            sections_data = result.get("sections", [])

            plans: list[SectionPlan] = []
            for s in sections_data[:max_sections]:
                plans.append(SectionPlan(
                    title=s.get("title", "Untitled Section"),
                    description=s.get("description", ""),
                    research_queries=s.get("research_queries", []),
                ))

            if not plans:
                # Fallback: single section covering everything
                plans = [SectionPlan(
                    title=topic,
                    description=f"Comprehensive analysis of {topic}",
                    research_queries=[topic],
                )]

            return plans

        except Exception as e:
            logger.warning("outline_planning_failed", error=str(e))
            return [SectionPlan(
                title=topic,
                description=f"Research report on {topic}",
                research_queries=[topic],
            )]

    # ================================================================
    # ReACT Loop for Section
    # ================================================================

    async def _react_section(
        self,
        section: SectionPlan,
        context: str,
        section_index: int = 0,
    ) -> SectionResult:
        """
        ReACT loop for one section.

        Iterates: Thought → Action → Observation until Final Answer
        or max iterations reached.

        Safety: min 2, max 5 tool calls. Timeout per section.
        """
        react_steps: list[ReactStep] = []
        observations: list[str] = []
        sources: list[str] = []
        tool_calls_count = 0
        max_iterations = DEFAULT_MAX_REACT_ITERATIONS
        min_tool_calls = DEFAULT_MIN_TOOL_CALLS

        # Build initial prompt
        system_prompt = (
            "You are a research agent writing a section of a report.\n"
            "Use the ReACT pattern: Think about what information you need, "
            "then use tools to gather it, then synthesize.\n\n"
            "Available tools:\n"
            "- web_search: {\"query\": \"search terms\"}\n"
            "- fetch_url: {\"url\": \"https://...\"}\n"
            "- read_file: {\"path\": \"file/path\"}\n"
            "- execute_code: {\"language\": \"python\", \"code\": \"...\"}\n\n"
            "Response format — use EXACTLY one of these:\n\n"
            "1. To think and plan:\n"
            "Thought: <your reasoning>\n"
            "Action: {\"tool\": \"tool_name\", \"args\": {\"param\": \"value\"}}\n\n"
            "2. When you have enough information:\n"
            "Thought: <final reasoning>\n"
            "Final Answer: <your complete section content in markdown>\n\n"
            "IMPORTANT:\n"
            f"- You MUST use at least {min_tool_calls} tool calls before Final Answer\n"
            "- Use web_search to find relevant data and facts\n"
            "- Use fetch_url to get details from promising search results\n"
            "- Cite sources in your final answer\n"
            "- Write detailed, substantive content (not just summaries)"
        )

        conversation: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Context:\n{context}\n\n"
                    f"Section: {section.title}\n"
                    f"Description: {section.description}\n"
                    f"Suggested research queries: {', '.join(section.research_queries)}\n\n"
                    "Begin your research. Use Thought/Action/Final Answer format."
                ),
            },
        ]

        try:
            for iteration in range(max_iterations):
                # Get LLM response with section timeout
                response = await asyncio.wait_for(
                    self.llm.chat(
                        conversation,
                        temperature=0.4,
                        max_tokens=self._get_adaptive_max_tokens(32768),
                    ),
                    timeout=DEFAULT_SECTION_TIMEOUT_S,
                )

                response_text = response.get("content", "")
                if not response_text:
                    break

                # Parse the ReACT response
                step = self._parse_react_response(response_text)

                # Handle Thought
                if step.step_type == "thought":
                    react_steps.append(step)

                    # Check for Action in the same response
                    if step.tool_name and step.tool_args is not None:
                        # Execute tool
                        react_steps.append(ReactStep(
                            step_type="action",
                            content=f"Calling {step.tool_name}",
                            tool_name=step.tool_name,
                            tool_args=step.tool_args,
                        ))

                        # Log tool call via per-report logger
                        if self.report_logger:
                            self.report_logger.log_tool_call(
                                section.title, section_index,
                                step.tool_name, step.tool_args or {},
                                iteration,
                            )

                        tool_result = await self._execute_tool_safe(
                            step.tool_name, step.tool_args,
                        )
                        tool_calls_count += 1

                        # Log tool result via per-report logger
                        if self.report_logger:
                            self.report_logger.log_tool_result(
                                section.title, section_index,
                                step.tool_name, tool_result,
                                iteration,
                            )

                        # Track sources from web search
                        if step.tool_name == "web_search":
                            query = step.tool_args.get("query", "")
                            sources.append(f"web_search: {query}")
                        elif step.tool_name == "fetch_url":
                            url = step.tool_args.get("url", "")
                            sources.append(url)

                        observation_text = tool_result[:3000]
                        observations.append(observation_text)

                        react_steps.append(ReactStep(
                            step_type="observation",
                            content=observation_text,
                            tool_name=step.tool_name,
                            tool_result=observation_text,
                        ))

                        # Add to conversation for next iteration
                        conversation.append({
                            "role": "assistant",
                            "content": response_text,
                        })
                        conversation.append({
                            "role": "user",
                            "content": f"Observation: {observation_text}\n\nContinue.",
                        })
                        continue

                # Handle Final Answer
                if step.step_type == "final_answer":
                    # Enforce minimum tool calls
                    if tool_calls_count < min_tool_calls:
                        # Force more research
                        conversation.append({
                            "role": "assistant",
                            "content": response_text,
                        })
                        conversation.append({
                            "role": "user",
                            "content": (
                                f"You've only made {tool_calls_count} tool calls. "
                                f"Please use at least {min_tool_calls} tools to gather "
                                "more data before providing your final answer. "
                                "Try a web_search or fetch_url for more information."
                            ),
                        })
                        continue

                    react_steps.append(step)
                    return SectionResult(
                        title=section.title,
                        content=step.content,
                        sources=sources,
                        tool_calls_count=tool_calls_count,
                        react_steps=react_steps,
                    )

                # Default: add response and continue
                conversation.append({
                    "role": "assistant",
                    "content": response_text,
                })
                conversation.append({
                    "role": "user",
                    "content": "Continue with your research. Use Thought/Action or Final Answer.",
                })

        except asyncio.TimeoutError:
            logger.warning(
                "react_section_timeout",
                section=section.title,
                tool_calls=tool_calls_count,
            )
            if self.report_logger:
                self.report_logger.log_error(
                    f"Timeout in section '{section.title}'", "generating",
                )
        except Exception as e:
            logger.error(
                "react_section_error",
                section=section.title,
                error=str(e),
            )
            if self.report_logger:
                self.report_logger.log_error(str(e), "generating")

        # Fallback: synthesize from observations if loop exhausts
        fallback_content = await self._fallback_synthesize(
            section, observations, context,
        )
        react_steps.append(ReactStep(
            step_type="final_answer",
            content=fallback_content,
        ))

        return SectionResult(
            title=section.title,
            content=fallback_content,
            sources=sources,
            tool_calls_count=tool_calls_count,
            react_steps=react_steps,
        )

    # ================================================================
    # Response Parsing
    # ================================================================

    def _parse_react_response(self, response: str) -> ReactStep:
        """
        Parse LLM output for ReACT components.

        Supports:
        - Text-style: Thought: ... / Action: {...} / Final Answer: ...
        - XML-style: <tool>...</tool>
        - JSON-style action blocks
        """
        text = response.strip()

        # Check for Final Answer
        final_match = re.search(
            r"Final\s*Answer\s*:\s*(.*)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if final_match:
            content = final_match.group(1).strip()
            # Check if there was also a Thought before the Final Answer
            return ReactStep(step_type="final_answer", content=content)

        # Check for Thought + Action pattern
        thought_match = re.search(
            r"Thought\s*:\s*(.*?)(?=Action\s*:|Final\s*Answer\s*:|$)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        thought_text = thought_match.group(1).strip() if thought_match else text

        # Check for Action (JSON-style)
        action_match = re.search(
            r'Action\s*:\s*(\{.*?\})\s*$',
            text,
            re.DOTALL | re.IGNORECASE,
        )
        tool_name = None
        tool_args = None

        if action_match:
            try:
                action_data = json.loads(action_match.group(1))
                tool_name = action_data.get("tool", "")
                tool_args = action_data.get("args", {})
            except (json.JSONDecodeError, TypeError):
                pass

        # Check for XML-style tool call: <tool name="...">...</tool>
        if not tool_name:
            xml_match = re.search(
                r'<tool\s+name="(\w+)">(.*?)</tool>',
                text,
                re.DOTALL,
            )
            if xml_match:
                tool_name = xml_match.group(1)
                try:
                    tool_args = json.loads(xml_match.group(2).strip())
                except (json.JSONDecodeError, TypeError):
                    tool_args = {"query": xml_match.group(2).strip()}

        # Check inline JSON action block after "Action:"
        if not tool_name:
            inline_json_match = re.search(
                r'Action\s*:\s*```(?:json)?\s*(\{.*?\})\s*```',
                text,
                re.DOTALL | re.IGNORECASE,
            )
            if inline_json_match:
                try:
                    action_data = json.loads(inline_json_match.group(1))
                    tool_name = action_data.get("tool", "")
                    tool_args = action_data.get("args", {})
                except (json.JSONDecodeError, TypeError):
                    pass

        return ReactStep(
            step_type="thought",
            content=thought_text,
            tool_name=tool_name,
            tool_args=tool_args,
        )

    # ================================================================
    # Tool Execution
    # ================================================================

    async def _execute_tool_safe(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> str:
        """Execute a tool with error handling. Returns output string."""
        try:
            result = await asyncio.wait_for(
                self.tool_executor.execute(tool_name, tool_args),
                timeout=30,
            )
            output = result.get("output", "")
            if result.get("success"):
                return output or "Tool executed successfully (no output)."
            return f"Tool failed: {output}"
        except asyncio.TimeoutError:
            return f"Tool '{tool_name}' timed out after 30s."
        except Exception as e:
            return f"Tool error: {e}"

    # ================================================================
    # Synthesis
    # ================================================================

    async def _fallback_synthesize(
        self,
        section: SectionPlan,
        observations: list[str],
        context: str,
    ) -> str:
        """Fallback synthesis when ReACT loop exhausts iterations."""
        if not observations:
            return (
                f"## {section.title}\n\n"
                f"{section.description}\n\n"
                "*Note: Unable to gather detailed information for this section.*"
            )

        obs_text = "\n\n---\n\n".join(observations[:5])
        messages = [
            {
                "role": "system",
                "content": (
                    "Synthesize the research observations below into a well-written "
                    "report section. Use markdown formatting. Be detailed and cite facts."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Section: {section.title}\n"
                    f"Description: {section.description}\n\n"
                    f"Research observations:\n{obs_text}\n\n"
                    "Write a comprehensive section based on this research."
                ),
            },
        ]

        try:
            result = await self.llm.chat(messages, temperature=0.5, max_tokens=self._get_adaptive_max_tokens(32768))
            return result.get("content", section.description)
        except Exception:
            return f"## {section.title}\n\n{section.description}"

    async def _synthesize_report(
        self,
        sections: list[SectionResult],
        topic: str,
        requirements: str,
    ) -> str:
        """Combine sections into full markdown report with transitions."""
        sections_md = "\n\n---\n\n".join(
            f"## {s.title}\n\n{s.content}" for s in sections
        )

        # Collect all sources
        all_sources: list[str] = []
        for s in sections:
            all_sources.extend(s.sources)
        unique_sources = list(dict.fromkeys(all_sources))  # dedupe preserving order

        sources_md = ""
        if unique_sources:
            sources_md = "\n\n---\n\n## Sources\n\n" + "\n".join(
                f"- {src}" for src in unique_sources
            )

        full_report = (
            f"# {topic}\n\n"
            f"*Generated by MekongAI Nexus ReACT Report Agent*\n\n"
            f"---\n\n"
            f"{sections_md}"
            f"{sources_md}"
        )

        return full_report

    async def _generate_executive_summary(
        self,
        sections: list[SectionResult],
        topic: str,
    ) -> str:
        """Generate executive summary from all section contents.

        BUG-001 fix: Increased max_tokens to 65536 and expanded
        prompt to request extremely detailed, comprehensive output.
        """
        sections_overview = "\n\n".join(
            f"### {s.title}\n{s.content[:4000]}"
            + ("..." if len(s.content) > 4000 else "")
            for s in sections
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert report writer producing a FINAL COMPREHENSIVE EXECUTIVE SUMMARY.\n\n"
                    "## OUTPUT REQUIREMENTS\n"
                    "- Minimum 2000 words, aim for 3000-5000 words\n"
                    "- 15-25 paragraphs covering EVERY aspect of the research\n"
                    "- Use markdown formatting: headers (##, ###), bold, bullet lists, tables where appropriate\n"
                    "- Write in the SAME LANGUAGE as the report content\n\n"
                    "## CONTENT REQUIREMENTS\n"
                    "You MUST cover ALL of the following for EACH section:\n"
                    "1. **Key findings and discoveries** — specific facts, data points, quotes\n"
                    "2. **Statistical evidence** — numbers, percentages, metrics, comparisons\n"
                    "3. **Trends and patterns** — what the data reveals about direction/momentum\n"
                    "4. **Analysis and interpretation** — what it means, why it matters\n"
                    "5. **Strategic implications** — how it affects decisions, planning, action\n"
                    "6. **Risks and challenges** — threats, vulnerabilities, concerns\n"
                    "7. **Actionable recommendations** — specific, concrete next steps\n"
                    "8. **Cross-section connections** — how findings from different sections relate\n"
                    "9. **Conclusion** — overall assessment with confidence level\n\n"
                    "## CRITICAL RULES\n"
                    "- Do NOT abbreviate or truncate. Write EVERYTHING in full detail.\n"
                    "- Do NOT say 'as mentioned above' or skip details. Each section stands alone.\n"
                    "- Include specific examples, case studies, and data from the research.\n"
                    "- This executive summary should be a STANDALONE document that captures\n"
                    "  ALL essential insights without needing to read the full report.\n"
                    "- Tạo báo cáo đầy đủ, chi tiết, bao gồm tất cả findings quan trọng.\n"
                    "  Không tóm tắt quá mức. Mỗi section cần có phân tích sâu và ví dụ cụ thể."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Report topic: {topic}\n\n"
                    f"Sections:\n{sections_overview}\n\n"
                    "Write a COMPREHENSIVE, EXTREMELY DETAILED executive summary "
                    "covering ALL findings from EVERY section. Minimum 2000 words."
                ),
            },
        ]

        try:
            result = await self.llm.chat(messages, temperature=0.4, max_tokens=self._get_adaptive_max_tokens(65536))
            return result.get("content", "Executive summary unavailable.")
        except Exception:
            return "Executive summary could not be generated."

    async def _generate_final_comprehensive_report(
        self,
        sections: list[SectionResult],
        topic: str,
        requirements: str,
        executive_summary: str = "",
    ) -> str:
        """Generate a final comprehensive report that synthesizes all sections
        into a cohesive, extremely detailed document.

        BUG-001: New method with max_tokens=65536 for maximum detail.

        Args:
            sections: All completed section results.
            topic: Report topic.
            requirements: Original user requirements.
            executive_summary: Pre-generated executive summary.

        Returns:
            Full markdown report with comprehensive synthesis.
        """
        sections_full = "\n\n---\n\n".join(
            f"## {s.title}\n\n{s.content}" for s in sections
        )

        # Collect all sources
        all_sources: list[str] = []
        for s in sections:
            all_sources.extend(s.sources)
        unique_sources = list(dict.fromkeys(all_sources))

        sources_md = ""
        if unique_sources:
            sources_md = "\n\n---\n\n## References & Sources\n\n" + "\n".join(
                f"- {src}" for src in unique_sources
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert report writer producing the FINAL COMPREHENSIVE REPORT.\n\n"
                    "## OUTPUT REQUIREMENTS\n"
                    "- Minimum 3000 words, aim for 5000-10000 words\n"
                    "- This is the DEFINITIVE document — include ALL details\n"
                    "- Write in the SAME LANGUAGE as the research sections\n\n"
                    "## STRUCTURE REQUIREMENTS\n"
                    "1. Title and metadata\n"
                    "2. Executive Summary (from provided summary)\n"
                    "3. Each research section — EXPANDED with:\n"
                    "   - Smooth transitions between sections\n"
                    "   - Cross-references between related findings\n"
                    "   - Deeper analysis where sections are thin\n"
                    "   - Specific data points, statistics, examples\n"
                    "4. Comprehensive Conclusion with:\n"
                    "   - Overall assessment\n"
                    "   - Strategic recommendations (numbered, actionable)\n"
                    "   - Risk factors and mitigation strategies\n"
                    "   - Future outlook and next steps\n"
                    "5. References & Sources\n\n"
                    "## FORMATTING\n"
                    "- Use markdown: # ## ### headers, **bold**, bullet lists, tables\n"
                    "- Ensure consistent terminology and style throughout\n"
                    "- Include inline citations [Source Name]\n\n"
                    "## CRITICAL RULES\n"
                    "- Do NOT truncate or abbreviate ANY section\n"
                    "- Do NOT skip details — write EVERYTHING in full\n"
                    "- EXPAND thin sections with deeper analysis\n"
                    "- Tạo báo cáo đầy đủ, chi tiết, bao gồm tất cả findings quan trọng.\n"
                    "  Không tóm tắt quá mức. Mỗi section cần có phân tích sâu và ví dụ cụ thể.\n"
                    "  Write as much detail as possible — this is the FINAL comprehensive report."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"# Report: {topic}\n\n"
                    f"**Requirements**: {requirements or 'Comprehensive analysis'}\n\n"
                    f"## Executive Summary\n\n{executive_summary}\n\n"
                    f"## Research Sections\n\n{sections_full}\n\n"
                    f"{sources_md}\n\n"
                    "Synthesize into a polished, EXTREMELY DETAILED final report. "
                    "Minimum 3000 words. Include ALL findings, data, and analysis."
                ),
            },
        ]

        try:
            result = await self.llm.chat(messages, temperature=0.4, max_tokens=self._get_adaptive_max_tokens(65536))
            return result.get("content", "")
        except Exception as e:
            logger.warning("final_comprehensive_report_failed", error=str(e))
            # Fallback: return assembled sections without LLM synthesis
            return (
                f"# {topic}\n\n"
                f"*Generated by MekongAI Nexus ReACT Report Agent*\n\n"
                f"---\n\n"
                f"## Executive Summary\n\n{executive_summary}\n\n"
                f"---\n\n"
                f"{sections_full}"
                f"{sources_md}"
            )

    # ================================================================
    # Chat Mode
    # ================================================================

    async def chat(
        self,
        message: str,
        report_context: str = "",
        chat_history: list[dict[str, str]] | None = None,
        max_tool_calls: int = 2,
    ) -> dict[str, Any]:
        """
        Chat with ReportAgent about a generated report.

        Agent can use tools to find additional information.
        Simplified ReACT: max 2 tool calls per chat turn.

        Args:
            message: User message.
            report_context: Previously generated report markdown
                (injected into system prompt).
            chat_history: Previous chat messages
                ``[{"role": "user/assistant", "content": "..."}]``.
            max_tool_calls: Max tool calls per chat turn.

        Returns:
            ``{"response": "...", "tool_calls": [...], "sources": [...]}``
        """
        tool_calls_made: list[dict[str, Any]] = []
        sources: list[str] = []

        try:
            # Build system prompt
            truncated_context = report_context[:CHAT_MAX_REPORT_CONTEXT_CHARS] if report_context else "(No report provided)"
            tools_desc = self._get_tools_description()

            system_content = (
                CHAT_SYSTEM_PROMPT
                .replace("{report_context}", truncated_context)
                .replace("{max_tool_calls}", str(max_tool_calls))
                .replace("{tools_description}", tools_desc)
            )

            # Assemble messages: system + history (last 10) + current
            messages: list[dict[str, str]] = [
                {"role": "system", "content": system_content},
            ]

            if chat_history:
                for msg in chat_history[-10:]:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if role in ("user", "assistant") and content:
                        messages.append({"role": role, "content": content})

            messages.append({"role": "user", "content": message})

            # Simplified ReACT loop: max_tool_calls iterations
            for _iteration in range(max_tool_calls):
                result = await self.llm.chat(
                    messages, temperature=0.4, max_tokens=self._get_adaptive_max_tokens(4096),
                )
                response_text = result.get("content", "")
                if not response_text:
                    break

                # Parse tool calls from response
                parsed_calls = self._parse_tool_calls(response_text)

                if not parsed_calls:
                    # No tool call → return response directly
                    clean_response = self._strip_tool_call_xml(response_text)
                    return {
                        "response": clean_response,
                        "tool_calls": tool_calls_made,
                        "sources": sources,
                    }

                # Execute first tool call found
                call = parsed_calls[0]
                tool_name = call.get("name", "")
                tool_params = call.get("parameters", {})

                tool_result = await self._execute_tool_safe(tool_name, tool_params)

                tool_calls_made.append({
                    "tool": tool_name,
                    "parameters": tool_params,
                    "result_preview": tool_result[:500],
                })

                # Track sources
                if tool_name == "web_search":
                    query = tool_params.get("query", "")
                    if query:
                        sources.append(f"web_search: {query}")
                elif tool_name == "fetch_url":
                    url = tool_params.get("url", "")
                    if url:
                        sources.append(url)

                # Add assistant response + observation, then loop
                messages.append({"role": "assistant", "content": response_text})
                messages.append({
                    "role": "user",
                    "content": f"Tool result for {tool_name}:\n{tool_result[:3000]}",
                })

            # After loop: one final LLM call to synthesize
            final_result = await self.llm.chat(
                messages, temperature=0.4, max_tokens=self._get_adaptive_max_tokens(4096),
            )
            final_text = final_result.get("content", "")
            clean_response = self._strip_tool_call_xml(final_text)

            return {
                "response": clean_response,
                "tool_calls": tool_calls_made,
                "sources": sources,
            }

        except Exception as exc:
            logger.error("report_chat_error", error=str(exc))
            return {
                "response": f"Sorry, an error occurred while processing your message: {exc}",
                "tool_calls": tool_calls_made,
                "sources": sources,
            }

    # ================================================================
    # Chat Helpers
    # ================================================================

    def _get_tools_description(self) -> str:
        """Build a human-readable tools description for the chat system prompt."""
        return (
            "- web_search: Search the web for information. "
            'Parameters: {"query": "search terms"}\n'
            "- fetch_url: Fetch content from a URL. "
            'Parameters: {"url": "https://..."}\n'
            "- read_file: Read a local file. "
            'Parameters: {"path": "file/path"}\n'
            "- execute_code: Run code. "
            'Parameters: {"language": "python", "code": "..."}'
        )

    def _parse_tool_calls(self, text: str) -> list[dict[str, Any]]:
        """Parse ``<tool_call>...</tool_call>`` blocks from LLM response.

        Returns:
            List of dicts with ``name`` and ``parameters`` keys.
        """
        calls: list[dict[str, Any]] = []
        pattern = re.compile(
            r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            try:
                data = json.loads(match.group(1))
                name = data.get("name", "")
                params = data.get("parameters", {})
                if name:
                    calls.append({"name": name, "parameters": params})
            except (json.JSONDecodeError, TypeError):
                continue
        return calls

    @staticmethod
    def _strip_tool_call_xml(text: str) -> str:
        """Remove ``<tool_call>...</tool_call>`` blocks from response text."""
        cleaned = re.sub(
            r"<tool_call>.*?</tool_call>",
            "",
            text,
            flags=re.DOTALL,
        )
        # Collapse multiple blank lines left by removal
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()


# ============================================================
# Module singleton
# ============================================================

_report_agent: Optional[ReportAgent] = None


def get_report_agent() -> ReportAgent:
    """Get or create the module-level ReportAgent singleton."""
    global _report_agent
    if _report_agent is None:
        _report_agent = ReportAgent(
            llm=get_core_llm(),
            tool_executor=ToolExecutor(),
        )
    return _report_agent


# ============================================================
# Helpers
# ============================================================


def _elapsed_ms(start: float) -> float:
    """Milliseconds elapsed since start."""
    return (time.perf_counter() - start) * 1000
