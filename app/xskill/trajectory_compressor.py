"""
Nexus - Trajectory Compressor.

Post-process task execution logs thành structured training data.
Lấy cảm hứng từ XSkill trajectory_summary.py:
- Compress raw trajectory steps thành structured records
- Extract key decision points và tool usage patterns
- Generate training data (SFT format) và markdown reports

Input: list of trajectory steps (role, content, tool_calls, tool_results, timestamps)
Output: TrajectoryRecord — structured summary cho:
  1. Training data generation (SFT format)
  2. Experience extraction (cho XSkill pipeline)
  3. Task replay/review
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# --- Constants ---
CONTENT_PREVIEW_MAX_LENGTH = 200
# LOGIC-003 FIX: Increased from 200 to 500 for key decisions.
# Tool results often contain critical context (URLs, error messages, data snippets)
# that gets lost at 200 chars. Key decision steps now preserve more context.
TOOL_RESULT_PREVIEW_MAX_LENGTH = 500
MAX_RETRIES = 3


# --- Data classes ---


@dataclass
class TrajectoryStep:
    """Một bước đơn trong trajectory đã được compress."""

    step_num: int
    role: str  # "user", "assistant", "tool"
    action: str  # brief description of what happened
    content_preview: str  # truncated content (max 200 chars)
    tool_name: Optional[str] = None
    tool_result_preview: Optional[str] = None
    is_key_decision: bool = False
    timestamp: Optional[datetime] = None


@dataclass
class TrajectoryRecord:
    """Kết quả compress của toàn bộ trajectory."""

    task_description: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    errors_encountered: list[str] = field(default_factory=list)
    final_outcome: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    compressed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    quality_score: Optional[float] = None  # Set by ExperienceCritique


class TrajectoryCompressor:
    """
    Compress raw trajectory logs thành structured TrajectoryRecord.

    Lấy cảm hứng từ XSkill summarize_rollouts():
    - Lọc bỏ noise (empty responses, repeated errors)
    - Extract key decision points
    - Summarize tool usage patterns
    - Calculate metrics (total_steps, tool_calls_count, error_count, duration)
    """

    def compress(self, trajectory: list[dict[str, Any]]) -> TrajectoryRecord:
        """
        Compress a raw trajectory thành TrajectoryRecord.

        Args:
            trajectory: List of step dicts, each with keys:
                - role: "user" | "assistant" | "tool"
                - content: str (message content)
                - tool_calls: Optional[list[dict]] (tool invocations)
                - tool_results: Optional[list[dict]] (tool outputs)
                - timestamp: Optional[str | datetime] (ISO format or datetime)

        Returns:
            TrajectoryRecord with compressed data and metrics.
        """
        if not trajectory:
            return TrajectoryRecord(
                task_description="(empty trajectory)",
                metrics=self._empty_metrics(),
            )

        # Extract task description from first user message
        task_description = self._extract_task_description(trajectory)

        # Process steps — filter noise, extract info
        steps: list[TrajectoryStep] = []
        tools_used: set[str] = set()
        errors: list[str] = []
        key_decisions: list[str] = []
        tool_calls_count = 0
        tokens_used = 0

        prev_error: Optional[str] = None  # Track repeated errors

        for idx, raw_step in enumerate(trajectory):
            role = raw_step.get("role", "unknown")
            content = raw_step.get("content", "") or ""
            tool_calls = raw_step.get("tool_calls") or []
            tool_results = raw_step.get("tool_results") or []
            timestamp = self._parse_timestamp(raw_step.get("timestamp"))

            # Accumulate token usage if available
            usage = raw_step.get("usage") or {}
            tokens_used += usage.get("total_tokens", 0)

            # Skip noise: empty assistant responses with no tool calls
            if role == "assistant" and not content.strip() and not tool_calls:
                continue

            # --- Detect errors ---
            step_error = self._detect_error(content, tool_results)
            if step_error:
                # Skip repeated identical errors
                if step_error == prev_error:
                    continue
                prev_error = step_error
                errors.append(
                    f"Step {idx + 1}: {step_error[:CONTENT_PREVIEW_MAX_LENGTH]}"
                )
            else:
                prev_error = None

            # --- Process tool calls ---
            tool_name: Optional[str] = None
            tool_result_preview: Optional[str] = None

            if tool_calls:
                tool_calls_count += len(tool_calls)
                # Take first tool call as representative
                first_tc = tool_calls[0] if tool_calls else {}
                func_info = first_tc.get("function", {})
                tool_name = func_info.get("name") or first_tc.get("name")
                if tool_name:
                    tools_used.add(tool_name)

            if tool_results:
                first_result = tool_results[0] if tool_results else {}
                result_content = first_result.get("content", "") or first_result.get(
                    "output", ""
                )
                tool_result_preview = str(result_content)[
                    :TOOL_RESULT_PREVIEW_MAX_LENGTH
                ]

            # --- Detect key decisions ---
            is_key = self._is_key_decision(role, content, tool_calls)
            action = self._summarize_action(role, content, tool_name)

            if is_key:
                key_decisions.append(
                    f"Step {idx + 1}: {action}"
                )

            step = TrajectoryStep(
                step_num=idx + 1,
                role=role,
                action=action,
                content_preview=content[:CONTENT_PREVIEW_MAX_LENGTH],
                tool_name=tool_name,
                tool_result_preview=tool_result_preview,
                is_key_decision=is_key,
                timestamp=timestamp,
            )
            steps.append(step)

        # Extract final outcome from last assistant message
        final_outcome = self._extract_final_outcome(trajectory)

        # Calculate duration
        duration_ms = self._calculate_duration_ms(steps)

        metrics: dict[str, Any] = {
            "total_steps": len(steps),
            "total_raw_steps": len(trajectory),
            "filtered_steps": len(trajectory) - len(steps),
            "tool_calls": tool_calls_count,
            "unique_tools": len(tools_used),
            "errors": len(errors),
            "key_decisions": len(key_decisions),
            "duration_ms": duration_ms,
            "tokens_used": tokens_used,
        }

        record = TrajectoryRecord(
            task_description=task_description,
            steps=steps,
            key_decisions=key_decisions,
            tools_used=sorted(tools_used),
            errors_encountered=errors,
            final_outcome=final_outcome,
            metrics=metrics,
        )

        logger.info(
            "trajectory_compressed",
            task=task_description[:80],
            total_steps=metrics["total_steps"],
            tool_calls=metrics["tool_calls"],
            errors=metrics["errors"],
        )

        return record

    def to_training_format(self, record: TrajectoryRecord) -> dict[str, Any]:
        """
        Convert TrajectoryRecord thành SFT training format.

        Returns:
            {
                "instruction": task description,
                "input": context/tool info,
                "output": final outcome + key decisions,
                "metadata": { metrics, tools, quality_score, ... }
            }
        """
        # Build input context
        input_parts: list[str] = []
        if record.tools_used:
            input_parts.append(f"Available tools: {', '.join(record.tools_used)}")
        if record.key_decisions:
            input_parts.append("Key decisions made:")
            for kd in record.key_decisions:
                input_parts.append(f"  - {kd}")

        # Build output: trace of assistant actions
        output_parts: list[str] = []
        for step in record.steps:
            if step.role == "assistant":
                line = f"[Step {step.step_num}] {step.action}"
                if step.tool_name:
                    line += f" (tool: {step.tool_name})"
                output_parts.append(line)

        if record.final_outcome:
            output_parts.append(f"\nFinal answer: {record.final_outcome}")

        return {
            "instruction": record.task_description,
            "input": "\n".join(input_parts) if input_parts else "",
            "output": "\n".join(output_parts) if output_parts else record.final_outcome,
            "metadata": {
                "metrics": record.metrics,
                "tools_used": record.tools_used,
                "errors": record.errors_encountered,
                "quality_score": record.quality_score,
                "compressed_at": record.compressed_at.isoformat(),
            },
        }

    def to_markdown(self, record: TrajectoryRecord) -> str:
        """
        Generate human-readable markdown report of the trajectory.

        Returns:
            Formatted markdown string.
        """
        lines: list[str] = []
        lines.append(f"# Trajectory Report")
        lines.append("")
        lines.append(f"**Task:** {record.task_description}")
        lines.append(
            f"**Compressed at:** {record.compressed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        if record.quality_score is not None:
            lines.append(f"**Quality Score:** {record.quality_score:.1f}/10")
        lines.append("")

        # Metrics summary
        m = record.metrics
        lines.append("## Metrics")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total Steps | {m.get('total_steps', 0)} |")
        lines.append(f"| Tool Calls | {m.get('tool_calls', 0)} |")
        lines.append(f"| Unique Tools | {m.get('unique_tools', 0)} |")
        lines.append(f"| Errors | {m.get('errors', 0)} |")
        lines.append(f"| Key Decisions | {m.get('key_decisions', 0)} |")
        duration = m.get("duration_ms", 0)
        if duration:
            lines.append(f"| Duration | {duration / 1000:.1f}s |")
        tokens = m.get("tokens_used", 0)
        if tokens:
            lines.append(f"| Tokens Used | {tokens:,} |")
        lines.append("")

        # Tools used
        if record.tools_used:
            lines.append("## Tools Used")
            lines.append("")
            for tool in record.tools_used:
                lines.append(f"- `{tool}`")
            lines.append("")

        # Key decisions
        if record.key_decisions:
            lines.append("## Key Decisions")
            lines.append("")
            for i, kd in enumerate(record.key_decisions, 1):
                lines.append(f"{i}. {kd}")
            lines.append("")

        # Step-by-step trace
        lines.append("## Steps")
        lines.append("")
        for step in record.steps:
            marker = "⭐" if step.is_key_decision else "•"
            tool_info = f" → `{step.tool_name}`" if step.tool_name else ""
            lines.append(
                f"{marker} **Step {step.step_num}** [{step.role}]{tool_info}: "
                f"{step.action}"
            )
            if step.tool_result_preview:
                lines.append(
                    f"  > Result: {step.tool_result_preview[:120]}..."
                )
        lines.append("")

        # Errors
        if record.errors_encountered:
            lines.append("## Errors Encountered")
            lines.append("")
            for err in record.errors_encountered:
                lines.append(f"- ⚠️ {err}")
            lines.append("")

        # Final outcome
        if record.final_outcome:
            lines.append("## Final Outcome")
            lines.append("")
            lines.append(record.final_outcome)
            lines.append("")

        return "\n".join(lines)

    def batch_compress(
        self, trajectories: list[list[dict[str, Any]]]
    ) -> list[TrajectoryRecord]:
        """Compress nhiều trajectories cùng lúc.

        Args:
            trajectories: List of raw trajectory step lists.

        Returns:
            List of TrajectoryRecord, one per trajectory.
        """
        records: list[TrajectoryRecord] = []
        for i, traj in enumerate(trajectories):
            try:
                record = self.compress(traj)
                records.append(record)
            except Exception as exc:
                logger.warning(
                    "batch_compress_item_failed",
                    index=i,
                    error=str(exc),
                )
                # Append a minimal error record
                records.append(
                    TrajectoryRecord(
                        task_description=f"(compression failed: {exc})",
                        metrics=self._empty_metrics(),
                    )
                )
        return records

    # ---- Private helpers ----

    @staticmethod
    def _empty_metrics() -> dict[str, Any]:
        """Return empty metrics dict."""
        return {
            "total_steps": 0,
            "total_raw_steps": 0,
            "filtered_steps": 0,
            "tool_calls": 0,
            "unique_tools": 0,
            "errors": 0,
            "key_decisions": 0,
            "duration_ms": 0,
            "tokens_used": 0,
        }

    @staticmethod
    def _extract_task_description(trajectory: list[dict[str, Any]]) -> str:
        """Extract task description from first user message."""
        for step in trajectory:
            if step.get("role") == "user":
                content = step.get("content", "")
                if content:
                    # Use first 500 chars of first user message
                    return content[:500].strip()
        return "(no task description found)"

    @staticmethod
    def _parse_timestamp(raw: Any) -> Optional[datetime]:
        """Parse timestamp from ISO string or datetime object."""
        if raw is None:
            return None
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except (OSError, ValueError):
                return None
        if isinstance(raw, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            # Fallback: fromisoformat
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    @staticmethod
    def _detect_error(
        content: str, tool_results: list[dict[str, Any]]
    ) -> Optional[str]:
        """Detect if step contains an error."""
        error_markers = ["error", "failed", "exception", "traceback", "tool error"]
        content_lower = content.lower()
        for marker in error_markers:
            if marker in content_lower:
                # Extract relevant error snippet
                idx = content_lower.index(marker)
                start = max(0, idx - 20)
                end = min(len(content), idx + 150)
                return content[start:end].strip()

        # Check tool results for errors
        for result in tool_results:
            result_content = str(
                result.get("content", "") or result.get("output", "")
            ).lower()
            for marker in error_markers:
                if marker in result_content:
                    return str(
                        result.get("content", "") or result.get("output", "")
                    )[:CONTENT_PREVIEW_MAX_LENGTH]

        return None

    @staticmethod
    def _is_key_decision(
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]],
    ) -> bool:
        """Determine if a step is a key decision point."""
        if role != "assistant":
            return False

        # Tool invocations are key decisions
        if tool_calls:
            return True

        # Long assistant responses with reasoning are likely key decisions
        decision_markers = [
            "i'll", "i will", "let me", "my approach",
            "strategy", "plan", "decision", "based on",
            "analyzing", "conclusion", "recommendation",
        ]
        content_lower = content.lower()
        return any(marker in content_lower for marker in decision_markers)

    @staticmethod
    def _summarize_action(
        role: str,
        content: str,
        tool_name: Optional[str],
    ) -> str:
        """Create brief action description for a step."""
        if role == "user":
            return f"User request: {content[:80].strip()}"

        if role == "tool":
            return f"Tool output received"

        if tool_name:
            return f"Called tool: {tool_name}"

        if content:
            # First sentence or first 80 chars
            first_sentence = content.split(".")[0].split("\n")[0][:80].strip()
            return first_sentence or content[:80].strip()

        return "(empty response)"

    @staticmethod
    def _extract_final_outcome(trajectory: list[dict[str, Any]]) -> str:
        """Extract final outcome from last assistant message."""
        for step in reversed(trajectory):
            if step.get("role") == "assistant":
                content = step.get("content", "")
                if content and content.strip():
                    return content[:1000].strip()
        return "(no final outcome)"

    @staticmethod
    def _calculate_duration_ms(steps: list[TrajectoryStep]) -> int:
        """Calculate total duration from step timestamps."""
        timestamps = [s.timestamp for s in steps if s.timestamp is not None]
        if len(timestamps) < 2:
            return 0
        first = min(timestamps)
        last = max(timestamps)
        delta = last - first
        return int(delta.total_seconds() * 1000)
