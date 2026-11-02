"""
Nexus - Visual QA Engine.

Screenshot pages → LLM Vision phân tích → Return issues & suggestions.

Tự động kiểm tra UI bằng cách:
1. Navigate đến page qua Playwright (dùng browser tools)
2. Chụp screenshot
3. Gửi screenshot + expected description → LLM Vision
4. LLM phân tích → trả về issues, score, suggestions

Fallback strategy:
- Playwright available → full screenshot + vision analysis
- Playwright NOT available → text-based: fetch HTML → analyze with LLM
- LLM NOT support vision → text-based fallback
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import structlog

from app.project_builder.models import PageSpec, VisualIssue, VisualQAResult
from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)

# Viewport presets: name → (width, height)
_VIEWPORT_SIZES: dict[str, tuple[int, int]] = {
    "desktop": (1280, 800),
    "tablet": (768, 1024),
    "mobile": (375, 812),
}

class VisualQAEngine:
    """Visual Quality Assurance Engine.

    Tự động kiểm tra UI bằng cách chụp screenshot qua Playwright,
    gửi tới LLM Vision để phân tích, và trả về issues + score.
    Fallback sang text-based QA khi Playwright/Vision không khả dụng.
    """

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_core_llm()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_page(
        self,
        url: str,
        page_spec: PageSpec,
        viewport: str = "desktop",
    ) -> VisualQAResult:
        """Kiểm tra single page.

        Flow:
        1. Navigate to url (playwright)
        2. Set viewport size
        3. Take screenshot → save to task workspace (.screenshots/)
        4. Send screenshot + page_spec → LLM Vision
        5. Parse LLM response → VisualQAResult

        Falls back to text-based QA when Playwright is unavailable.
        """
        logger.info(
            "visual_qa_check_page",
            url=url,
            page=page_spec.name,
            viewport=viewport,
        )

        filename = f"{page_spec.name.replace(' ', '_')}_{viewport}_{uuid.uuid4().hex[:6]}.png"
        screenshot_path = await self._take_screenshot(url, viewport, filename)

        if screenshot_path:
            return await self._analyze_screenshot(screenshot_path, page_spec, viewport)

        # Fallback: text-based QA
        logger.warning(
            "visual_qa_screenshot_failed_fallback",
            page=page_spec.name,
        )
        return await self._text_based_qa(url, page_spec)

    async def check_all_pages(
        self,
        base_url: str,
        pages: list[PageSpec],
        viewport: str = "desktop",
    ) -> list[VisualQAResult]:
        """Kiểm tra tất cả pages. Return list of results."""
        results: list[VisualQAResult] = []
        base = base_url.rstrip("/")

        for page_spec in pages:
            route = page_spec.route.lstrip("/")
            url = f"{base}/{route}" if route else base
            result = await self.check_page(url, page_spec, viewport)
            results.append(result)

        logger.info(
            "visual_qa_all_pages_done",
            total=len(results),
            avg_score=_safe_avg([r.score for r in results]),
        )
        return results

    async def check_responsive(
        self,
        url: str,
        page_spec: PageSpec,
    ) -> list[VisualQAResult]:
        """Check 3 viewports: desktop, tablet, mobile."""
        results: list[VisualQAResult] = []
        for vp in ("desktop", "tablet", "mobile"):
            result = await self.check_page(url, page_spec, vp)
            results.append(result)

        logger.info(
            "visual_qa_responsive_done",
            page=page_spec.name,
            scores={r.page: r.score for r in results},
        )
        return results

    # ------------------------------------------------------------------
    # Screenshot capture
    # ------------------------------------------------------------------

    async def _take_screenshot(
        self,
        url: str,
        viewport: str,
        filename: str,
    ) -> Optional[str]:
        """Take screenshot sử dụng ToolExecutor.

        Gọi browser_navigate → browser_screenshot qua tool registry.
        Returns True nếu screenshot thành công.
        """
        try:
            from app.mcp.tool_executor import ToolExecutor

            executor = ToolExecutor()

            # Navigate to URL
            nav_result = await executor.execute(
                "browser_navigate", {"url": url}
            )
            if not nav_result.get("success"):
                logger.warning(
                    "visual_qa_navigate_failed",
                    url=url,
                    error=nav_result.get("output", ""),
                )
                return None

            # Small delay for rendering after navigation
            import asyncio

            await asyncio.sleep(1.0)

            # Take screenshot — extract filename from save_path
            shot_result = await executor.execute(
                "browser_screenshot",
                {"filename": filename, "full_page": True},
            )

            if not shot_result.get("success"):
                logger.warning(
                    "visual_qa_screenshot_failed",
                    error=shot_result.get("output", ""),
                )
                return None

            screenshot_path = (shot_result.get("data") or {}).get("path") or ""
            if not screenshot_path and shot_result.get("artifacts"):
                try:
                    for a in shot_result["artifacts"]:
                        if isinstance(a, dict) and a.get("type") == "image" and a.get("path"):
                            screenshot_path = str(a["path"])
                            break
                except Exception:
                    pass

            if not screenshot_path:
                logger.warning("visual_qa_screenshot_path_missing", filename=filename)
                return None

            logger.info("visual_qa_screenshot_saved", path=screenshot_path)
            return screenshot_path

        except Exception as exc:
            logger.error("visual_qa_screenshot_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # LLM Vision analysis
    # ------------------------------------------------------------------

    async def _analyze_screenshot(
        self,
        screenshot_path: str,
        page_spec: PageSpec,
        viewport: str,
    ) -> VisualQAResult:
        """Gửi screenshot + description → LLM để phân tích.

        Đọc file → base64 encode → gửi qua LLM vision.
        Nếu LLM không hỗ trợ vision → fallback text-based.
        """
        # Read screenshot file → base64
        try:
            screenshot_file = Path(screenshot_path)
            if not screenshot_file.exists():
                try:
                    from app.mcp.tools.filesystem import WORKSPACE_DIR

                    screenshot_file = WORKSPACE_DIR / screenshot_path
                except Exception:
                    screenshot_file = Path("workspace_data") / screenshot_path

            if not screenshot_file.exists():
                logger.warning(
                    "visual_qa_screenshot_not_found",
                    path=screenshot_path,
                )
                return self._empty_result(page_spec, screenshot_path)

            image_bytes = screenshot_file.read_bytes()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        except Exception as exc:
            logger.error("visual_qa_read_screenshot_error", error=str(exc))
            return self._empty_result(page_spec, screenshot_path)

        expected_desc = page_spec.screenshot_check or page_spec.description
        components_str = ", ".join(page_spec.components) if page_spec.components else "N/A"

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a professional UI/UX reviewer. Analyze the screenshot "
                    "of a web page. Compare it against the expected design and "
                    "identify issues.\n\n"
                    "Return ONLY valid JSON with this structure:\n"
                    "{\n"
                    '  "score": 0.0 to 1.0,\n'
                    '  "issues": [{"description": "...", "severity": "critical|major|minor", "suggestion": "..."}],\n'
                    '  "suggestions": ["..."],\n'
                    '  "overall_assessment": "..."\n'
                    "}"
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Page: {page_spec.name}\n"
                            f"Route: {page_spec.route}\n"
                            f"Expected: {expected_desc}\n"
                            f"Components expected: {components_str}\n"
                            f"Viewport: {viewport}\n\n"
                            "Analyze the screenshot and return JSON with score, "
                            "issues, suggestions, and overall_assessment."
                        ),
                    },
                ],
            },
        ]

        try:
            raw = await self.llm.chat(messages, temperature=0.3, max_tokens=2048)
            content = raw.get("content", "")
            parsed = _parse_json_response(content)
            return self._build_result(parsed, page_spec, screenshot_path)
        except Exception as exc:
            logger.warning(
                "visual_qa_vision_failed_fallback",
                error=str(exc),
                page=page_spec.name,
            )
            # Vision not supported — fallback to text
            return await self._text_based_qa_from_screenshot(
                screenshot_path, page_spec
            )

    async def _text_based_qa_from_screenshot(
        self,
        screenshot_path: str,
        page_spec: PageSpec,
    ) -> VisualQAResult:
        """Fallback khi LLM không support vision — chỉ dùng text-based."""
        logger.info(
            "visual_qa_text_fallback_from_screenshot",
            page=page_spec.name,
        )
        # We can't analyze the image without vision, give a partial result
        return VisualQAResult(
            page=page_spec.name,
            screenshot_path=screenshot_path,
            issues=[
                VisualIssue(
                    page=page_spec.name,
                    description="Vision analysis unavailable — screenshot captured but not analyzed",
                    severity="minor",
                    suggestion="Manually review the screenshot or use a vision-capable LLM",
                )
            ],
            score=0.5,
            suggestions=["Enable a vision-capable LLM for full visual QA"],
        )

    # ------------------------------------------------------------------
    # Text-based QA fallback (no Playwright)
    # ------------------------------------------------------------------

    async def _text_based_qa(
        self,
        url: str,
        page_spec: PageSpec,
    ) -> VisualQAResult:
        """Fallback khi Playwright/Vision không available.

        Fetch HTML via tool executor → LLM phân tích structure.
        """
        html_content = await self._fetch_html(url)

        if not html_content:
            return VisualQAResult(
                page=page_spec.name,
                issues=[
                    VisualIssue(
                        page=page_spec.name,
                        description=f"Cannot access page: {url}",
                        severity="critical",
                        suggestion="Check if dev server is running and URL is correct",
                    )
                ],
                score=0.0,
                suggestions=["Start the dev server and verify the URL"],
            )

        expected_desc = page_spec.screenshot_check or page_spec.description
        components_str = ", ".join(page_spec.components) if page_spec.components else "N/A"

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a professional UI/UX reviewer. Analyze the HTML "
                    "structure of a web page (no screenshot available). "
                    "Check if the expected components and layout are present.\n\n"
                    "Return ONLY valid JSON with this structure:\n"
                    "{\n"
                    '  "score": 0.0 to 1.0,\n'
                    '  "issues": [{"description": "...", "severity": "critical|major|minor", "suggestion": "..."}],\n'
                    '  "suggestions": ["..."],\n'
                    '  "overall_assessment": "..."\n'
                    "}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Page: {page_spec.name}\n"
                    f"Route: {page_spec.route}\n"
                    f"Expected: {expected_desc}\n"
                    f"Components expected: {components_str}\n\n"
                    f"HTML (truncated):\n{html_content[:8000]}\n\n"
                    "Analyze the HTML structure and return JSON."
                ),
            },
        ]

        try:
            parsed = await self.llm.chat_json(messages, temperature=0.3)
            return self._build_result(parsed, page_spec, screenshot_path="")
        except Exception as exc:
            logger.error("visual_qa_text_analysis_failed", error=str(exc))
            return VisualQAResult(
                page=page_spec.name,
                issues=[
                    VisualIssue(
                        page=page_spec.name,
                        description=f"Text-based QA analysis failed: {exc}",
                        severity="major",
                        suggestion="Check LLM connectivity",
                    )
                ],
                score=0.0,
                suggestions=["Ensure LLM service is available"],
            )

    async def _fetch_html(self, url: str) -> str:
        """Fetch page HTML via ToolExecutor (browser_navigate extracts text)."""
        try:
            from app.mcp.tool_executor import ToolExecutor

            executor = ToolExecutor()
            result = await executor.execute("browser_navigate", {"url": url})

            if result.get("success"):
                return result.get("output", "")
            return ""
        except Exception as exc:
            logger.error("visual_qa_fetch_html_error", error=str(exc))
            return ""

    # ------------------------------------------------------------------
    # Viewport helpers
    # ------------------------------------------------------------------

    def _get_viewport_size(self, viewport: str) -> tuple[int, int]:
        """Map viewport name → (width, height)."""
        return _VIEWPORT_SIZES.get(viewport, _VIEWPORT_SIZES["desktop"])

    # ------------------------------------------------------------------
    # Result builders
    # ------------------------------------------------------------------

    def _build_result(
        self,
        parsed: dict[str, Any],
        page_spec: PageSpec,
        screenshot_path: str,
    ) -> VisualQAResult:
        """Build VisualQAResult from parsed LLM JSON response."""
        score = float(parsed.get("score", 0.0))
        score = max(0.0, min(1.0, score))

        issues: list[VisualIssue] = []
        for issue_data in parsed.get("issues", []):
            severity = issue_data.get("severity", "minor")
            if severity not in ("critical", "major", "minor"):
                severity = "minor"
            issues.append(
                VisualIssue(
                    page=page_spec.name,
                    description=issue_data.get("description", ""),
                    severity=severity,
                    suggestion=issue_data.get("suggestion", ""),
                )
            )

        suggestions = parsed.get("suggestions", [])
        if isinstance(suggestions, str):
            suggestions = [suggestions]

        overall = parsed.get("overall_assessment", "")
        if overall and overall not in suggestions:
            suggestions.insert(0, overall)

        return VisualQAResult(
            page=page_spec.name,
            screenshot_path=screenshot_path,
            issues=issues,
            score=score,
            suggestions=suggestions,
        )

    def _empty_result(
        self,
        page_spec: PageSpec,
        screenshot_path: str,
    ) -> VisualQAResult:
        """Return empty result when analysis cannot proceed."""
        return VisualQAResult(
            page=page_spec.name,
            screenshot_path=screenshot_path,
            issues=[],
            score=0.0,
            suggestions=["Analysis could not be completed"],
        )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _parse_json_response(content: str) -> dict[str, Any]:
    """Parse JSON from LLM response, handling markdown fences."""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strip markdown code fences
    text = content
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]

    # Find JSON object boundaries
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"score": 0.0, "issues": [], "suggestions": [], "overall_assessment": ""}


def _safe_avg(values: list[float]) -> float:
    """Calculate average, returning 0.0 for empty list."""
    if not values:
        return 0.0
    return sum(values) / len(values)
