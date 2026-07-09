"""
MekongAI Nexus - Visual QA Tools.

Screenshot web pages and analyze UI quality using LLM Vision.
Wraps VisualQAEngine for agent tool-calling.

Tools registered:
    - visual_check: Check a single page visually
    - visual_check_responsive: Check page across desktop/tablet/mobile
"""

from __future__ import annotations

from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_visual_check(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Check a web page visually via screenshot + LLM vision analysis.

    Creates a temporary PageSpec from params, delegates to
    VisualQAEngine.check_page(), and returns score/issues/suggestions.
    """
    url = params.get("url", "")
    description = params.get("description", "")

    if not url:
        return {"success": False, "output": "Missing required param: url", "data": None, "artifacts": []}
    if not description:
        return {"success": False, "output": "Missing required param: description", "data": None, "artifacts": []}

    from app.project_builder.models import PageSpec
    from app.project_builder.visual_qa import VisualQAEngine

    engine = VisualQAEngine()

    page_spec = PageSpec(
        name=params.get("page_name", "Page"),
        route=url,
        description=description,
        screenshot_check=description,
    )

    viewport = params.get("viewport", "desktop")
    if viewport not in ("desktop", "tablet", "mobile"):
        viewport = "desktop"

    try:
        result = await engine.check_page(
            url=url,
            page_spec=page_spec,
            viewport=viewport,
        )
    except Exception as exc:
        logger.error("visual_check_failed", url=url, error=str(exc))
        return {
            "success": False,
            "output": f"Visual check failed: {exc}",
            "data": None,
            "artifacts": [],
        }

    # Format human-readable output
    issues_text = "\n".join(
        f"- [{i.severity}] {i.description}" for i in result.issues
    )
    suggestions_text = "\n".join(f"- {s}" for s in result.suggestions)

    output = f"Visual QA Score: {result.score:.0%}\n"
    if issues_text:
        output += f"\nIssues:\n{issues_text}\n"
    if suggestions_text:
        output += f"\nSuggestions:\n{suggestions_text}\n"
    if result.screenshot_path:
        output += f"\nScreenshot: {result.screenshot_path}"

    logger.info(
        "visual_check_done",
        url=url,
        viewport=viewport,
        score=result.score,
        issue_count=len(result.issues),
    )

    return {
        "success": True,
        "output": output,
        "data": {
            "score": result.score,
            "issues": [
                {
                    "description": i.description,
                    "severity": i.severity,
                    "suggestion": i.suggestion,
                }
                for i in result.issues
            ],
            "suggestions": result.suggestions,
            "screenshot_path": result.screenshot_path,
        },
        "artifacts": [result.screenshot_path] if result.screenshot_path else [],
    }


async def handle_visual_check_responsive(
    params: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    """Check a page across desktop, tablet, and mobile viewports.

    Delegates to VisualQAEngine.check_responsive() and returns
    combined results for all three viewports.
    """
    url = params.get("url", "")
    description = params.get("description", "")

    if not url:
        return {"success": False, "output": "Missing required param: url", "data": None, "artifacts": []}
    if not description:
        return {"success": False, "output": "Missing required param: description", "data": None, "artifacts": []}

    from app.project_builder.models import PageSpec
    from app.project_builder.visual_qa import VisualQAEngine

    engine = VisualQAEngine()

    page_spec = PageSpec(
        name=params.get("page_name", "Page"),
        route=url,
        description=description,
        screenshot_check=description,
    )

    try:
        results = await engine.check_responsive(url=url, page_spec=page_spec)
    except Exception as exc:
        logger.error("visual_check_responsive_failed", url=url, error=str(exc))
        return {
            "success": False,
            "output": f"Responsive visual check failed: {exc}",
            "data": None,
            "artifacts": [],
        }

    # Build combined output
    viewports = ("desktop", "tablet", "mobile")
    viewport_data: list[dict[str, Any]] = []
    artifacts: list[str] = []
    output_parts: list[str] = ["Responsive Visual QA Results:"]

    for vp_name, result in zip(viewports, results):
        issues_text = "; ".join(
            f"[{i.severity}] {i.description}" for i in result.issues
        )
        output_parts.append(
            f"\n  {vp_name.capitalize()}: {result.score:.0%}"
            + (f" — {issues_text}" if issues_text else " — No issues")
        )
        viewport_data.append({
            "viewport": vp_name,
            "score": result.score,
            "issues": [
                {
                    "description": i.description,
                    "severity": i.severity,
                    "suggestion": i.suggestion,
                }
                for i in result.issues
            ],
            "suggestions": result.suggestions,
            "screenshot_path": result.screenshot_path,
        })
        if result.screenshot_path:
            artifacts.append(result.screenshot_path)

    avg_score = sum(r.score for r in results) / len(results) if results else 0.0
    output_parts.append(f"\nAverage Score: {avg_score:.0%}")

    logger.info(
        "visual_check_responsive_done",
        url=url,
        avg_score=avg_score,
    )

    return {
        "success": True,
        "output": "\n".join(output_parts),
        "data": {
            "viewports": viewport_data,
            "average_score": avg_score,
        },
        "artifacts": artifacts,
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "visual_qa"

registry.register(
    name="visual_check",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Take a screenshot of a web page and analyze its visual quality "
            "using AI vision. Returns score, issues, and suggestions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to check (e.g., http://localhost:3100)",
                },
                "description": {
                    "type": "string",
                    "description": "What the page should look like or contain",
                },
                "viewport": {
                    "type": "string",
                    "description": "Viewport size: desktop, tablet, or mobile",
                    "enum": ["desktop", "tablet", "mobile"],
                    "default": "desktop",
                },
                "page_name": {
                    "type": "string",
                    "description": "Name for reporting",
                    "default": "Page",
                },
            },
            "required": ["url", "description"],
        },
    },
    handler=handle_visual_check,
    is_async=True,
    emoji="👁️",
)

registry.register(
    name="visual_check_responsive",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Check a web page across desktop, tablet, and mobile viewports. "
            "Returns combined visual QA results for all three sizes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to check",
                },
                "description": {
                    "type": "string",
                    "description": "Expected page appearance",
                },
                "page_name": {
                    "type": "string",
                    "description": "Name for reporting",
                    "default": "Page",
                },
            },
            "required": ["url", "description"],
        },
    },
    handler=handle_visual_check_responsive,
    is_async=True,
    emoji="📱",
)
