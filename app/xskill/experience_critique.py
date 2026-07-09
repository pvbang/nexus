"""
MekongAI Nexus - Experience Critique (LLM-as-Judge).

LLM-based evaluation of task execution quality.
Lấy cảm hứng từ XSkill experience_critique.py:
- Đánh giá trajectory theo 5 dimensions (completion, efficiency, tool_usage,
  error_handling, overall)
- Quyết định trajectory có đủ tốt để extract thành experience không
- Generate improvement suggestions

Sử dụng LLMClient.chat_json() cho structured output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.services.llm_client import LLMClient, get_core_llm
from app.xskill.trajectory_compressor import TrajectoryCompressor, TrajectoryRecord

logger = structlog.get_logger(__name__)

# --- Constants ---
QUALITY_THRESHOLD = 7.0  # Minimum overall score to extract experience
MAX_RETRIES = 3

# --- Judge prompt template ---
JUDGE_PROMPT = """\
You are an expert evaluator of AI agent task execution.
Evaluate the following task trajectory and provide scores.

Task: {task_description}

Trajectory Summary:
{trajectory_markdown}

Rate each dimension from 0-10:
1. Task Completion: Was the task fully completed? Did it achieve the goal?
2. Efficiency: Were steps minimal and purposeful? No unnecessary detours?
3. Tool Usage: Were the right tools chosen and used effectively?
4. Error Handling: Were errors recovered from gracefully? Were retries appropriate?
5. Overall Quality: Holistic assessment of the entire execution.

Respond in JSON:
{{
  "scores": {{
    "completion": <0-10>,
    "efficiency": <0-10>,
    "tool_usage": <0-10>,
    "error_handling": <0-10>,
    "overall": <0-10>
  }},
  "strengths": ["strength 1", "strength 2", ...],
  "weaknesses": ["weakness 1", "weakness 2", ...],
  "suggestions": ["suggestion 1", "suggestion 2", ...]
}}
"""


# --- Data classes ---


@dataclass
class CritiqueResult:
    """Kết quả đánh giá chất lượng trajectory."""

    task_description: str
    scores: dict[str, float] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    should_extract: bool = False  # auto-set based on overall score
    raw_response: str = ""
    critiqued_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ExperienceCritique:
    """
    LLM-as-judge để đánh giá chất lượng task execution.

    Lấy cảm hứng từ XSkill intra_sample_experiences():
    - Gọi LLM với judge prompt
    - Parse structured JSON response
    - Determine if trajectory is worth extracting as experience
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        quality_threshold: float = QUALITY_THRESHOLD,
    ):
        self.llm = llm or get_core_llm()
        self.quality_threshold = quality_threshold
        self._compressor = TrajectoryCompressor()

    async def critique(
        self,
        record: TrajectoryRecord,
        task_description: Optional[str] = None,
    ) -> CritiqueResult:
        """
        Critique a trajectory record using LLM-as-judge.

        Args:
            record: TrajectoryRecord to evaluate.
            task_description: Override task description (uses record's if None).

        Returns:
            CritiqueResult with scores, strengths, weaknesses, and suggestions.
        """
        effective_task = task_description or record.task_description

        # Generate markdown summary for LLM evaluation
        trajectory_md = self._compressor.to_markdown(record)

        # Build judge prompt
        prompt_text = (
            JUDGE_PROMPT
            .replace("{task_description}", effective_task)
            .replace("{trajectory_markdown}", trajectory_md)
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert evaluator of AI agent task execution. "
                    "Provide fair, detailed assessments in JSON format.\n\n"
                    "IMPORTANT: Return ONLY valid JSON."
                ),
            },
            {"role": "user", "content": prompt_text},
        ]

        try:
            result = await self.llm.chat_json(messages, temperature=0.2)
            raw_response = str(result)
        except Exception as exc:
            logger.warning(
                "experience_critique_failed",
                task=effective_task[:80],
                error=str(exc),
            )
            # Return fallback critique with neutral scores
            return self._fallback_critique(effective_task, str(exc))

        # Parse scores with validation
        scores = self._validate_scores(result.get("scores", {}))
        strengths = self._ensure_string_list(result.get("strengths", []))
        weaknesses = self._ensure_string_list(result.get("weaknesses", []))
        suggestions = self._ensure_string_list(result.get("suggestions", []))

        overall = scores.get("overall", 0.0)
        should_extract = overall >= self.quality_threshold

        critique = CritiqueResult(
            task_description=effective_task,
            scores=scores,
            strengths=strengths,
            weaknesses=weaknesses,
            suggestions=suggestions,
            should_extract=should_extract,
            raw_response=raw_response,
        )

        logger.info(
            "trajectory_critiqued",
            task=effective_task[:80],
            overall_score=overall,
            should_extract=should_extract,
            strengths_count=len(strengths),
            weaknesses_count=len(weaknesses),
        )

        return critique

    async def batch_critique(
        self,
        records: list[TrajectoryRecord],
    ) -> list[CritiqueResult]:
        """
        Critique multiple trajectory records.

        Args:
            records: List of TrajectoryRecord to evaluate.

        Returns:
            List of CritiqueResult, one per record.
        """
        results: list[CritiqueResult] = []
        for i, record in enumerate(records):
            try:
                critique = await self.critique(record)
                results.append(critique)
            except Exception as exc:
                logger.warning(
                    "batch_critique_item_failed",
                    index=i,
                    error=str(exc),
                )
                results.append(
                    self._fallback_critique(
                        record.task_description, str(exc)
                    )
                )
        return results

    def should_extract_experience(self, critique: CritiqueResult) -> bool:
        """
        Determine if a trajectory is good enough to extract as experience.

        Args:
            critique: CritiqueResult to check.

        Returns:
            True if overall score meets threshold.
        """
        overall = critique.scores.get("overall", 0.0)
        return overall >= self.quality_threshold

    # ---- Private helpers ----

    @staticmethod
    def _validate_scores(raw_scores: Any) -> dict[str, float]:
        """Validate and clamp score values to 0-10 range."""
        expected_keys = [
            "completion",
            "efficiency",
            "tool_usage",
            "error_handling",
            "overall",
        ]
        validated: dict[str, float] = {}

        if not isinstance(raw_scores, dict):
            return {k: 5.0 for k in expected_keys}

        for key in expected_keys:
            try:
                val = float(raw_scores.get(key, 5.0))
                validated[key] = max(0.0, min(10.0, val))
            except (TypeError, ValueError):
                validated[key] = 5.0

        return validated

    @staticmethod
    def _ensure_string_list(raw: Any) -> list[str]:
        """Ensure result is list of strings."""
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if item]

    def _fallback_critique(
        self, task_description: str, error_msg: str
    ) -> CritiqueResult:
        """Return a neutral fallback critique when LLM call fails."""
        return CritiqueResult(
            task_description=task_description,
            scores={
                "completion": 5.0,
                "efficiency": 5.0,
                "tool_usage": 5.0,
                "error_handling": 5.0,
                "overall": 5.0,
            },
            strengths=[],
            weaknesses=[f"Critique failed: {error_msg}"],
            suggestions=["Re-run critique when LLM service is available"],
            should_extract=False,
            raw_response=f"FALLBACK: {error_msg}",
        )
