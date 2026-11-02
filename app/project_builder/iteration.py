"""
Nexus - Iteration Controller.

Quản lý vòng lặp iterative refinement:
Build → Test → Fix → Preview → Evaluate → (loop or done)

Safety mechanisms:
- Max iterations (default 10)
- Quality threshold (default 0.85)
- Stall detection: nếu N iterations liên tiếp không có progress → stop
- Budget tracking: count LLM calls, time elapsed
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.project_builder.models import (
    BuildResult,
    IterationState,
    TestResult,
    VisualQAResult,
)
from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helper dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FixPlan:
    """Plan for fixing a single issue.

    action: "create" | "modify" | "delete"
    estimated_impact: "high" | "medium" | "low"
    """

    file_path: str
    action: str  # create | modify | delete
    description: str
    search_replace: list[tuple[str, str]] = field(default_factory=list)
    new_content: str = ""
    estimated_impact: str = "medium"


@dataclass
class IterationDecision:
    """Decision made after evaluating an iteration.

    action: "continue" | "completed" | "stalled" | "max_reached"
    """

    action: str  # continue | done | stalled | max_reached
    quality_score: float
    issues_to_fix: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class _IterationRecord:
    """Internal record for a single iteration's results."""

    iteration: int
    quality_score: float
    build_success: bool = False
    tests_passed: int = 0
    tests_total: int = 0
    visual_avg_score: float = 0.0
    fixes_applied: int = 0
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# IterationController
# ---------------------------------------------------------------------------


class IterationController:
    """Quản lý vòng lặp iterative refinement.

    Build → Test → Fix → Preview → Evaluate → (loop or done)

    Safety:
    - Max iterations (default 10)
    - Quality threshold (default 0.85)
    - Stall detection: nếu 3 iterations liên tiếp không có progress → stop
    - Budget tracking: count LLM calls, time elapsed
    """

    def __init__(
        self,
        llm: LLMClient | None = None,
        max_iterations: int = 10,
        quality_threshold: float = 0.85,
        stall_limit: int = 3,
    ) -> None:
        self.llm = llm or get_core_llm()
        self.max_iterations = max_iterations
        self.quality_threshold = quality_threshold
        self.stall_limit = stall_limit
        self.state = IterationState(
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
        )
        self._history: list[_IterationRecord] = []
        self._started_at: float = time.time()

    # ------------------------------------------------------------------
    # Evaluate current iteration
    # ------------------------------------------------------------------

    async def evaluate_iteration(
        self,
        build_result: BuildResult,
        test_result: TestResult | None = None,
        visual_results: list[VisualQAResult] | None = None,
    ) -> IterationDecision:
        """Đánh giá kết quả iteration hiện tại.

        Returns IterationDecision:
        - action: "continue" | "completed" | "stalled" | "max_reached"
        - quality_score: float
        - issues_to_fix: list[dict]
        - reasoning: str
        """
        quality = self._calculate_quality(build_result, test_result, visual_results)

        # Check termination conditions
        if self.state.iteration_number >= self.max_iterations:
            return IterationDecision(
                action="max_reached",
                quality_score=quality,
                reasoning=f"Maximum iterations ({self.max_iterations}) reached.",
            )

        if quality >= self.quality_threshold:
            return IterationDecision(
                action="completed",
                quality_score=quality,
                reasoning=(
                    f"Quality score {quality:.2f} meets threshold "
                    f"{self.quality_threshold:.2f}."
                ),
            )

        if self._detect_stall():
            return IterationDecision(
                action="stalled",
                quality_score=quality,
                reasoning=(
                    f"Quality has not improved in the last "
                    f"{self.stall_limit} iterations."
                ),
            )

        # Collect issues to fix
        issues = self._collect_issues(build_result, test_result, visual_results)

        return IterationDecision(
            action="continue",
            quality_score=quality,
            issues_to_fix=issues,
            reasoning=f"Quality {quality:.2f} < threshold {self.quality_threshold:.2f}. {len(issues)} issue(s) to fix.",
        )

    # ------------------------------------------------------------------
    # Plan fixes via LLM
    # ------------------------------------------------------------------

    async def plan_fixes(
        self,
        issues: list[dict[str, Any]],
        project_context: str = "",
    ) -> list[FixPlan]:
        """LLM phân tích issues → lên kế hoạch fix.

        Returns list[FixPlan] sorted by estimated_impact (high first).
        """
        if not issues:
            return []

        issues_text = json.dumps(issues, indent=2, default=str)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert software engineer. Given a list of issues "
                    "from a build/test/visual QA cycle, plan the fixes.\n\n"
                    "Return ONLY valid JSON: an array of fix plans:\n"
                    "[\n"
                    "  {\n"
                    '    "file_path": "src/...",\n'
                    '    "action": "create|modify|delete",\n'
                    '    "description": "what to fix",\n'
                    '    "search_replace": [["old text", "new text"]],\n'
                    '    "new_content": "full content if action=create",\n'
                    '    "estimated_impact": "high|medium|low"\n'
                    "  }\n"
                    "]"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Issues to fix:\n{issues_text}\n\n"
                    f"Project context:\n{project_context[:4000] if project_context else 'N/A'}\n\n"
                    "Plan fixes for these issues. Prioritize critical issues first."
                ),
            },
        ]

        try:
            raw = await self.llm.chat_json(messages, temperature=0.3)

            # Response may be a list or {"fixes": [...]}
            fixes_data: list[dict[str, Any]] = []
            if isinstance(raw, list):
                fixes_data = raw
            elif isinstance(raw, dict) and "fixes" in raw:
                fixes_data = raw["fixes"]
            elif isinstance(raw, dict):
                # Single fix object
                fixes_data = [raw]

            plans: list[FixPlan] = []
            for fix in fixes_data:
                search_replace_raw = fix.get("search_replace", [])
                search_replace: list[tuple[str, str]] = []
                for pair in search_replace_raw:
                    if isinstance(pair, (list, tuple)) and len(pair) == 2:
                        search_replace.append((str(pair[0]), str(pair[1])))

                impact = fix.get("estimated_impact", "medium")
                if impact not in ("high", "medium", "low"):
                    impact = "medium"

                plans.append(
                    FixPlan(
                        file_path=fix.get("file_path", ""),
                        action=fix.get("action", "modify"),
                        description=fix.get("description", ""),
                        search_replace=search_replace,
                        new_content=fix.get("new_content", ""),
                        estimated_impact=impact,
                    )
                )

            # Sort by impact priority
            impact_order = {"high": 0, "medium": 1, "low": 2}
            plans.sort(key=lambda p: impact_order.get(p.estimated_impact, 1))

            logger.info("iteration_fixes_planned", count=len(plans))
            return plans

        except Exception as exc:
            logger.error("iteration_plan_fixes_failed", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Record & state management
    # ------------------------------------------------------------------

    def record_iteration(
        self,
        build_result: BuildResult,
        test_result: TestResult | None = None,
        visual_results: list[VisualQAResult] | None = None,
        fixes_applied: int = 0,
    ) -> None:
        """Record kết quả iteration vào state."""
        self.state.iteration_number += 1
        quality = self._calculate_quality(build_result, test_result, visual_results)

        # Update state
        self.state.build_results.append(build_result)
        if visual_results:
            self.state.visual_results.extend(visual_results)

        # Track resolved/remaining issues
        if build_result.success and (test_result is None or test_result.success):
            self.state.issues_resolved.append(
                f"Iteration {self.state.iteration_number}: build+tests pass"
            )

        remaining = []
        for err in build_result.errors:
            remaining.append(err)
        if test_result:
            for err in test_result.errors:
                remaining.append(err)
        self.state.issues_remaining = remaining

        # Record history
        record = _IterationRecord(
            iteration=self.state.iteration_number,
            quality_score=quality,
            build_success=build_result.success,
            tests_passed=test_result.passed if test_result else 0,
            tests_total=test_result.total if test_result else 0,
            visual_avg_score=_safe_avg([v.score for v in visual_results]) if visual_results else 0.0,
            fixes_applied=fixes_applied,
        )
        self._history.append(record)

        logger.info(
            "iteration_recorded",
            iteration=self.state.iteration_number,
            quality=quality,
            fixes_applied=fixes_applied,
        )

    def should_continue(self) -> bool:
        """Check if should continue iterating."""
        if self.state.iteration_number >= self.max_iterations:
            return False

        if self._detect_stall():
            return False

        return self.state.should_continue()

    # ------------------------------------------------------------------
    # Stall detection
    # ------------------------------------------------------------------

    def _detect_stall(self) -> bool:
        """Detect nếu quality không cải thiện sau N iterations.

        Returns True nếu last `stall_limit` iterations có quality
        không tăng (delta < 0.01).
        """
        if len(self._history) < self.stall_limit:
            return False

        recent = self._history[-self.stall_limit:]
        first_score = recent[0].quality_score

        # Check if any improvement > 1%
        for record in recent[1:]:
            if record.quality_score > first_score + 0.01:
                return False

        return True

    # ------------------------------------------------------------------
    # Quality calculation
    # ------------------------------------------------------------------

    def _calculate_quality(
        self,
        build: BuildResult,
        test: TestResult | None,
        visual: list[VisualQAResult] | None,
    ) -> float:
        """Tính quality score tổng hợp.

        Weights:
        - Build success: 40%
        - Tests pass ratio: 30%
        - Visual QA average score: 30%
        """
        # Build score: 1.0 if success, 0.0 if failed
        build_score = 1.0 if build.success else 0.0
        # Deduct for warnings
        if build.warnings:
            build_score = max(0.5, build_score - 0.05 * len(build.warnings))

        # Test score: passed / total ratio
        test_score = 0.5  # Default when no tests
        if test is not None and test.total > 0:
            test_score = test.passed / test.total
        elif test is not None and test.success:
            test_score = 1.0
        elif test is not None:
            test_score = 0.0

        # Visual QA score: average of all page scores
        visual_score = 0.5  # Default when no visual QA
        if visual:
            visual_score = _safe_avg([v.score for v in visual])

        quality = (
            build_score * 0.4
            + test_score * 0.3
            + visual_score * 0.3
        )

        return round(max(0.0, min(1.0, quality)), 3)

    # ------------------------------------------------------------------
    # Issue collection
    # ------------------------------------------------------------------

    def _collect_issues(
        self,
        build_result: BuildResult,
        test_result: TestResult | None,
        visual_results: list[VisualQAResult] | None,
    ) -> list[dict[str, Any]]:
        """Collect all issues from build/test/visual results."""
        issues: list[dict[str, Any]] = []

        # Build errors (highest priority)
        for error in build_result.errors:
            issues.append({
                "source": "build",
                "severity": "critical",
                "description": error,
            })

        # Build warnings
        for warning in build_result.warnings:
            issues.append({
                "source": "build",
                "severity": "minor",
                "description": warning,
            })

        # Test failures
        if test_result:
            for error in test_result.errors:
                issues.append({
                    "source": "test",
                    "severity": "major",
                    "description": error,
                })

        # Visual QA issues
        if visual_results:
            for vr in visual_results:
                for issue in vr.issues:
                    issues.append({
                        "source": "visual_qa",
                        "severity": issue.severity,
                        "page": issue.page,
                        "description": issue.description,
                        "suggestion": issue.suggestion,
                    })

        return issues

    # ------------------------------------------------------------------
    # Progress summary
    # ------------------------------------------------------------------

    def get_progress_summary(self) -> str:
        """Human-readable summary of all iterations."""
        if not self._history:
            return "No iterations recorded yet."

        elapsed = time.time() - self._started_at
        lines = [
            f"Iteration Progress ({self.state.iteration_number}/{self.max_iterations})",
            f"Elapsed: {elapsed:.1f}s",
            f"Quality threshold: {self.quality_threshold:.2f}",
            "",
        ]

        for record in self._history:
            status = "✅" if record.quality_score >= self.quality_threshold else "🔄"
            test_info = (
                f"tests {record.tests_passed}/{record.tests_total}"
                if record.tests_total > 0
                else "no tests"
            )
            lines.append(
                f"  {status} Iter {record.iteration}: "
                f"quality={record.quality_score:.3f}, "
                f"build={'✓' if record.build_success else '✗'}, "
                f"{test_info}, "
                f"visual={record.visual_avg_score:.2f}, "
                f"fixes={record.fixes_applied}"
            )

        # Current status
        latest = self._history[-1]
        if latest.quality_score >= self.quality_threshold:
            lines.append(f"\n🎉 Quality threshold met ({latest.quality_score:.3f})!")
        elif self._detect_stall():
            lines.append(f"\n⚠️ Stall detected — quality not improving.")
        elif self.state.iteration_number >= self.max_iterations:
            lines.append(f"\n⛔ Max iterations reached.")
        else:
            gap = self.quality_threshold - latest.quality_score
            lines.append(f"\n📊 Gap to threshold: {gap:.3f}")

        return "\n".join(lines)

    def get_state(self) -> IterationState:
        """Return current state."""
        return self.state


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _safe_avg(values: list[float]) -> float:
    """Calculate average, returning 0.0 for empty list."""
    if not values:
        return 0.0
    return sum(values) / len(values)
