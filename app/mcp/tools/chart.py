"""
MekongAI Nexus - Chart Generation Tool.

Generate charts/graphs from data using matplotlib.
Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error
from app.mcp.tools.filesystem import get_task_workspace

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))


def _get_charts_dir() -> Path:
    """Get charts output directory within current task workspace."""
    ws = get_task_workspace()
    charts_dir = ws / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    return charts_dir


def _check_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


async def _handle_generate_chart(args: dict[str, Any]) -> str:
    """Generate a chart from data."""
    chart_type = args.get("type", "bar").lower()
    title = args.get("title", "Chart")
    data = args.get("data", {})
    filename = args.get("filename", f"chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")

    if not data:
        return tool_error("Data is required. Provide {labels: [...], values: [...]} or {datasets: [...]}")

    try:
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
    except ImportError:
        return tool_error("matplotlib not installed. Run: pip install matplotlib")

    charts_dir = _get_charts_dir()

    # Parse data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return tool_error("Invalid JSON data")

    labels = data.get("labels", [])
    values = data.get("values", [])
    datasets = data.get("datasets", [])
    x_label = data.get("x_label", "")
    y_label = data.get("y_label", "")

    if not labels and not datasets:
        return tool_error("Data must contain 'labels' and 'values', or 'datasets'")

    try:
        fig, ax = plt.subplots(figsize=(10, 6))

        if chart_type == "bar":
            if datasets:
                import numpy as np
                x = np.arange(len(labels))
                width = 0.8 / len(datasets)
                for i, ds in enumerate(datasets):
                    offset = (i - len(datasets) / 2 + 0.5) * width
                    ax.bar(x + offset, ds.get("values", []), width, label=ds.get("label", f"Series {i+1}"))
                ax.set_xticks(x)
                ax.set_xticklabels(labels, rotation=45, ha="right")
                ax.legend()
            else:
                ax.bar(labels, values, color="steelblue")
                plt.xticks(rotation=45, ha="right")

        elif chart_type == "line":
            if datasets:
                for ds in datasets:
                    ax.plot(labels, ds.get("values", []), marker="o", label=ds.get("label", ""))
                ax.legend()
            else:
                ax.plot(labels, values, marker="o", color="steelblue")

        elif chart_type == "pie":
            ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")

        elif chart_type == "scatter":
            x_vals = data.get("x", values)
            y_vals = data.get("y", [])
            if not y_vals:
                return tool_error("Scatter chart needs 'x' and 'y' arrays in data")
            ax.scatter(x_vals, y_vals, alpha=0.7)

        elif chart_type == "horizontal_bar":
            ax.barh(labels, values, color="steelblue")

        else:
            return tool_error(f"Unsupported chart type: {chart_type}. Use: bar, line, pie, scatter, horizontal_bar")

        ax.set_title(title, fontsize=14, fontweight="bold")
        if x_label:
            ax.set_xlabel(x_label)
        if y_label:
            ax.set_ylabel(y_label)

        plt.tight_layout()
        output_path = charts_dir / filename
        fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
        plt.close(fig)

        size = output_path.stat().st_size
        relative_path = output_path.relative_to(WORKSPACE_DIR)
        download_url = f"/api/v1/artifacts/download/{relative_path.as_posix()}"

        return tool_result(
            success=True,
            output=(
                f"📊 Chart generated: {filename} ({size:,} bytes)\n"
                f"Type: {chart_type} | Title: {title}\n"
                f"Download: {download_url}"
            ),
            data={"path": str(relative_path.as_posix()), "size": size, "type": chart_type, "download_url": download_url},
        )
    except Exception as e:
        plt.close("all")
        logger.error("chart_generation_failed", error=str(e))
        return tool_error(f"Chart generation failed: {e}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="generate_chart",
    toolset="data",
    schema={
        "description": (
            "Generate a chart/graph image from data. Supports bar, line, pie, scatter, horizontal_bar. "
            "Returns a downloadable PNG image."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["bar", "line", "pie", "scatter", "horizontal_bar"],
                    "description": "Chart type (default: bar)",
                },
                "title": {"type": "string", "description": "Chart title"},
                "data": {
                    "type": "object",
                    "description": (
                        "Chart data. Format: {labels: ['A','B'], values: [10,20]} "
                        "or {labels: [...], datasets: [{label:'S1', values:[...]}, ...]} "
                        "or for scatter: {x: [...], y: [...]}"
                    ),
                },
                "filename": {"type": "string", "description": "Output filename (optional)"},
            },
            "required": ["title", "data"],
        },
    },
    handler=_handle_generate_chart,
    is_async=True,
    check_fn=_check_matplotlib,
    emoji="📊",
)
