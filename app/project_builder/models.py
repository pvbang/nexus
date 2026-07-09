"""
MekongAI Nexus - Project Builder Data Models.

Tất cả dataclasses cho Project Builder Pipeline — hệ thống cho phép AI
tự động build full project (scaffold → code → test → preview → iterate).

Sử dụng @dataclass (không Pydantic) ở service layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tech Stack & File specifications
# ---------------------------------------------------------------------------


@dataclass
class TechStack:
    """Technology stack configuration for a project."""

    frontend: str = ""
    backend: str = ""
    database: str = ""
    css_framework: str = ""
    ai_integration: str = ""
    package_manager: str = "npm"


@dataclass
class FileSpec:
    """Specification for a single file to generate."""

    path: str
    content: str = ""
    description: str = ""
    language: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class DependencySpec:
    """Project dependencies across package managers."""

    npm: list[str] = field(default_factory=list)
    npm_dev: list[str] = field(default_factory=list)
    pip: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Component, Model, API, Page specifications
# ---------------------------------------------------------------------------


@dataclass
class ComponentSpec:
    """Specification for a UI component.

    type: "page" | "component" | "layout" | "api"
    """

    name: str
    type: str  # page | component | layout | api
    description: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""


@dataclass
class ModelSpec:
    """Specification for a data model / database entity."""

    name: str
    fields: dict[str, str] = field(default_factory=dict)
    relationships: list[str] = field(default_factory=list)


@dataclass
class APISpec:
    """Specification for an API endpoint."""

    method: str
    path: str
    description: str = ""
    request_body: Optional[dict[str, Any]] = None
    response_schema: Optional[dict[str, Any]] = None


@dataclass
class PageSpec:
    """Specification for a page/route.

    screenshot_check: mô tả visual expected cho visual QA.
    """

    name: str
    route: str
    description: str = ""
    components: list[str] = field(default_factory=list)
    screenshot_check: str = ""


# ---------------------------------------------------------------------------
# ProjectBlueprint — the core planning artifact
# ---------------------------------------------------------------------------


@dataclass
class ProjectBlueprint:
    """Complete project blueprint — đầu ra của planning phase.

    Chứa toàn bộ thông tin cần thiết để scaffold, implement, test, và
    iterate trên một project.
    """

    name: str
    description: str = ""
    tech_stack: TechStack = field(default_factory=TechStack)
    file_structure: dict[str, Any] = field(default_factory=dict)
    components: list[ComponentSpec] = field(default_factory=list)
    data_models: list[ModelSpec] = field(default_factory=list)
    api_endpoints: list[APISpec] = field(default_factory=list)
    pages: list[PageSpec] = field(default_factory=list)
    dependencies: DependencySpec = field(default_factory=DependencySpec)
    implementation_order: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize blueprint to a plain dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectBlueprint:
        """Deserialize blueprint from a plain dict."""
        tech_stack = TechStack(**data.get("tech_stack", {}))
        components = [
            ComponentSpec(**c) for c in data.get("components", [])
        ]
        data_models = [
            ModelSpec(**m) for m in data.get("data_models", [])
        ]
        api_endpoints = [
            APISpec(**a) for a in data.get("api_endpoints", [])
        ]
        pages = [PageSpec(**p) for p in data.get("pages", [])]
        dependencies = DependencySpec(**data.get("dependencies", {}))

        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            tech_stack=tech_stack,
            file_structure=data.get("file_structure", {}),
            components=components,
            data_models=data_models,
            api_endpoints=api_endpoints,
            pages=pages,
            dependencies=dependencies,
            implementation_order=data.get("implementation_order", []),
            success_criteria=data.get("success_criteria", []),
        )

    def get_files_for_phase(self, phase_index: int) -> list[FileSpec]:
        """Trả về danh sách FileSpec cho phase cụ thể trong implementation_order.

        Mỗi phase name trong implementation_order ánh xạ tới components/pages
        tương ứng. Nếu phase_index ngoài phạm vi → trả về list rỗng.
        """
        if phase_index < 0 or phase_index >= len(self.implementation_order):
            return []

        phase_name = self.implementation_order[phase_index].lower()
        files: list[FileSpec] = []

        # Tìm components có file_path match với phase name
        for comp in self.components:
            if comp.file_path and (
                phase_name in comp.name.lower()
                or phase_name in comp.file_path.lower()
            ):
                files.append(
                    FileSpec(
                        path=comp.file_path,
                        description=comp.description,
                        language=_guess_language(comp.file_path),
                    )
                )

        # Tìm pages match
        for page in self.pages:
            if phase_name in page.name.lower():
                route_path = page.route.strip("/") or "index"
                files.append(
                    FileSpec(
                        path=f"src/pages/{route_path}.tsx",
                        description=page.description,
                        language="tsx",
                    )
                )

        return files


# ---------------------------------------------------------------------------
# Execution results
# ---------------------------------------------------------------------------


@dataclass
class ImplementResult:
    """Result of implementing a single file."""

    file_path: str
    success: bool
    errors: list[str] = field(default_factory=list)
    content_preview: str = ""


@dataclass
class ScaffoldResult:
    """Result of project scaffolding phase."""

    files_created: list[str] = field(default_factory=list)
    dirs_created: list[str] = field(default_factory=list)
    deps_installed: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    """Result of running tests."""

    success: bool
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    output: str = ""


@dataclass
class BuildResult:
    """Result of a build/compile step."""

    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output: str = ""


# ---------------------------------------------------------------------------
# Visual QA
# ---------------------------------------------------------------------------


@dataclass
class VisualIssue:
    """A visual issue found during QA.

    severity: "critical" | "major" | "minor"
    """

    page: str
    description: str
    severity: str = "minor"  # critical | major | minor
    suggestion: str = ""


@dataclass
class VisualQAResult:
    """Result of visual QA for a single page."""

    page: str
    screenshot_path: str = ""
    issues: list[VisualIssue] = field(default_factory=list)
    score: float = 0.0
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Iteration & Progress tracking
# ---------------------------------------------------------------------------


@dataclass
class IterationState:
    """Tracks the current iteration state of the build-fix loop."""

    iteration_number: int = 0
    max_iterations: int = 10
    quality_threshold: float = 0.85
    issues_resolved: list[str] = field(default_factory=list)
    issues_remaining: list[str] = field(default_factory=list)
    build_results: list[BuildResult] = field(default_factory=list)
    visual_results: list[VisualQAResult] = field(default_factory=list)

    def should_continue(self) -> bool:
        """True nếu chưa đạt quality threshold và còn iterations."""
        if self.iteration_number >= self.max_iterations:
            return False

        # Nếu chưa có visual results → còn cần chạy tiếp
        if not self.visual_results:
            return True

        # Kiểm tra average quality score
        avg_score = sum(v.score for v in self.visual_results) / len(
            self.visual_results
        )
        return avg_score < self.quality_threshold


@dataclass
class ProjectProgress:
    """Overall progress tracking cho project builder pipeline."""

    current_phase: str = ""
    phases_completed: list[str] = field(default_factory=list)
    total_files: int = 0
    files_written: int = 0
    errors_fixed: int = 0
    iteration_count: int = 0
    quality_score: float = 0.0
    started_at: str = ""
    elapsed_seconds: float = 0.0

    def completion_pct(self) -> float:
        """Tính phần trăm hoàn thành dựa trên files written."""
        if self.total_files <= 0:
            return 0.0
        return min(100.0, (self.files_written / self.total_files) * 100.0)


# ---------------------------------------------------------------------------
# Server & Final result
# ---------------------------------------------------------------------------


@dataclass
class ServerInfo:
    """Information about a running dev/preview server."""

    server_id: str
    url: str  # Internal URL (localhost) — for health checks
    port: int
    tech: str
    pid: int = 0
    status: str = "running"
    project_path: str = ""
    external_url: str = ""  # External URL — for user access from outside
    public_url: str = ""    # Public URL (tunnel) — preferred behind NAT/Docker


@dataclass
class ProjectResult:
    """Final result of the entire project builder pipeline."""

    blueprint: Optional[ProjectBlueprint] = None
    scaffold: Optional[ScaffoldResult] = None
    progress: Optional[ProjectProgress] = None
    server: Optional[ServerInfo] = None
    final_quality: float = 0.0
    iterations: int = 0
    success: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".css": "css",
    ".html": "html",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".md": "markdown",
    ".sql": "sql",
    ".toml": "toml",
}


def _guess_language(file_path: str) -> str:
    """Đoán ngôn ngữ từ file extension."""
    for ext, lang in _EXTENSION_LANGUAGE_MAP.items():
        if file_path.endswith(ext):
            return lang
    return ""
