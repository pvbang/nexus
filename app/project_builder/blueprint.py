"""
Nexus - Project Builder Blueprint Generator.

LLM-powered blueprint generation: phân tích user requirements → tạo
ProjectBlueprint hoàn chỉnh với tech stack, components, data models,
API endpoints, pages, dependencies, và implementation order.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import structlog

from app.project_builder.models import (
    APISpec,
    ComponentSpec,
    DependencySpec,
    FileSpec,
    ModelSpec,
    PageSpec,
    ProjectBlueprint,
    TechStack,
)
from app.project_builder.templates import ProjectTemplateRegistry
from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)


class BlueprintGenerator:
    """Tạo bản thiết kế project hoàn chỉnh từ mô tả yêu cầu.

    Sử dụng LLM để phân tích requirements → tech stack, file structure,
    components, data models, API endpoints, pages, dependencies.

    Flow:
        1. LLM phân tích → chọn tech stack phù hợp
        2. LLM thiết kế → components, pages, data models, API
        3. LLM lên kế hoạch → implementation order
        4. Merge với template scaffold files
    """

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_core_llm()
        self.template_registry = ProjectTemplateRegistry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self, request: str, context: str = ""
    ) -> ProjectBlueprint:
        """Từ mô tả requirements → ProjectBlueprint hoàn chỉnh.

        Args:
            request: Mô tả yêu cầu project (ví dụ: "Tạo website dạy
                tiếng Anh AI với quiz, flashcard, và chatbot").
            context: Thông tin bổ sung (tech preferences, constraints).

        Returns:
            ProjectBlueprint chứa toàn bộ thông tin cần thiết để scaffold,
            implement, test, và iterate trên project.
        """
        logger.info(
            "blueprint_generate_start",
            request_len=len(request),
            has_context=bool(context),
        )

        # Step 1: Analyze & choose tech stack
        tech_analysis = await self._analyze_tech_stack(request, context)
        logger.info(
            "blueprint_tech_analyzed",
            template_match=tech_analysis.get("template_match", ""),
            reasoning=tech_analysis.get("reasoning", "")[:120],
        )

        # Step 2: Design architecture
        architecture = await self._design_architecture(
            request, tech_analysis, context
        )
        logger.info(
            "blueprint_architecture_designed",
            components=len(architecture.get("components", [])),
            pages=len(architecture.get("pages", [])),
            models=len(architecture.get("data_models", [])),
            api_endpoints=len(architecture.get("api_endpoints", [])),
        )

        # Step 3: Create implementation plan
        impl_plan = await self._create_implementation_plan(
            request, architecture
        )
        logger.info(
            "blueprint_plan_created",
            steps=len(impl_plan.get("implementation_order", [])),
            criteria=len(impl_plan.get("success_criteria", [])),
        )

        # Step 4: Assemble ProjectBlueprint
        blueprint = self._assemble_blueprint(
            request, tech_analysis, architecture, impl_plan
        )
        logger.info(
            "blueprint_generated",
            name=blueprint.name,
            total_components=len(blueprint.components),
            total_pages=len(blueprint.pages),
            total_files=len(blueprint.file_structure),
        )

        return blueprint

    async def refine(
        self, blueprint: ProjectBlueprint, feedback: str
    ) -> ProjectBlueprint:
        """Tinh chỉnh blueprint dựa trên feedback từ user hoặc QA.

        Args:
            blueprint: Blueprint hiện tại cần refine.
            feedback: Mô tả thay đổi (ví dụ: "Thêm dark mode toggle",
                "Đổi database sang PostgreSQL").

        Returns:
            ProjectBlueprint đã được cập nhật.
        """
        logger.info(
            "blueprint_refine_start",
            name=blueprint.name,
            feedback_len=len(feedback),
        )

        blueprint_json = json.dumps(blueprint.to_dict(), indent=2)

        messages = [
            {
                "role": "system",
                "content": _REFINE_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"## Current Blueprint\n```json\n{blueprint_json}\n```\n\n"
                    f"## Feedback / Changes Requested\n{feedback}\n\n"
                    "Return the FULL updated blueprint JSON incorporating "
                    "the requested changes. Keep all unchanged fields intact."
                ),
            },
        ]

        result = await self.llm.chat_json(messages, temperature=0.3)
        refined = self._parse_blueprint_dict(result, fallback_name=blueprint.name)

        logger.info(
            "blueprint_refined",
            name=refined.name,
            components=len(refined.components),
            pages=len(refined.pages),
        )

        return refined

    # ------------------------------------------------------------------
    # Step 1: Tech stack analysis
    # ------------------------------------------------------------------

    async def _analyze_tech_stack(
        self, request: str, context: str
    ) -> dict[str, Any]:
        """LLM phân tích → chọn tech stack phù hợp nhất.

        Returns:
            Dict with keys: tech_stack, reasoning, template_match,
            project_name, project_description.
        """
        available_templates = self.template_registry.list_templates()

        messages = [
            {
                "role": "system",
                "content": _TECH_STACK_SYSTEM_PROMPT.replace(
                    "{available_templates}", json.dumps(available_templates)
                ),
            },
            {
                "role": "user",
                "content": (
                    f"## Project Requirements\n{request}\n\n"
                    f"## Additional Context\n{context or 'None provided.'}\n\n"
                    "Analyze the requirements and recommend the best tech stack."
                ),
            },
        ]

        result = await self.llm.chat_json(messages, temperature=0.3)

        # Ensure required keys exist
        result.setdefault("tech_stack", {})
        result.setdefault("reasoning", "")
        result.setdefault("template_match", "")
        result.setdefault("project_name", "my-project")
        result.setdefault("project_description", request[:200])

        return result

    # ------------------------------------------------------------------
    # Step 2: Architecture design
    # ------------------------------------------------------------------

    async def _design_architecture(
        self,
        request: str,
        tech: dict[str, Any],
        context: str,
    ) -> dict[str, Any]:
        """LLM thiết kế architecture: components, pages, models, API.

        Returns:
            Dict with keys: components, pages, data_models,
            api_endpoints, file_structure.
        """
        tech_stack_json = json.dumps(tech.get("tech_stack", {}), indent=2)
        template_match = tech.get("template_match", "")

        messages = [
            {
                "role": "system",
                "content": _ARCHITECTURE_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"## Project Requirements\n{request}\n\n"
                    f"## Additional Context\n{context or 'None provided.'}\n\n"
                    f"## Chosen Tech Stack\n```json\n{tech_stack_json}\n```\n\n"
                    f"## Template Match\n{template_match or 'No matching template — design from scratch.'}\n\n"
                    "Design the complete architecture."
                ),
            },
        ]

        result = await self.llm.chat_json(messages, temperature=0.4)

        # Validate & normalize
        result.setdefault("components", [])
        result.setdefault("pages", [])
        result.setdefault("data_models", [])
        result.setdefault("api_endpoints", [])
        result.setdefault("file_structure", {})
        result.setdefault("dependencies", {})

        # Validate: every page must have a route
        for page in result["pages"]:
            if not page.get("route"):
                name = page.get("name", "unknown")
                page["route"] = f"/{name.lower().replace(' ', '-')}"

        # Validate: every component must have a file_path
        for comp in result["components"]:
            if not comp.get("file_path"):
                name = comp.get("name", "Component")
                comp["file_path"] = f"src/components/{name}.tsx"

        return result

    # ------------------------------------------------------------------
    # Step 3: Implementation plan
    # ------------------------------------------------------------------

    async def _create_implementation_plan(
        self, request: str, arch: dict[str, Any]
    ) -> dict[str, Any]:
        """LLM lên thứ tự implement và success criteria.

        Returns:
            Dict with keys: implementation_order, success_criteria,
            dependencies_between_phases.
        """
        arch_summary = json.dumps(
            {
                "components": [
                    c.get("name", "") for c in arch.get("components", [])
                ],
                "pages": [
                    p.get("name", "") for p in arch.get("pages", [])
                ],
                "data_models": [
                    m.get("name", "") for m in arch.get("data_models", [])
                ],
                "api_endpoints": [
                    f"{e.get('method', 'GET')} {e.get('path', '/')}"
                    for e in arch.get("api_endpoints", [])
                ],
            },
            indent=2,
        )

        messages = [
            {
                "role": "system",
                "content": _IMPL_PLAN_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"## Project Requirements\n{request}\n\n"
                    f"## Architecture Summary\n```json\n{arch_summary}\n```\n\n"
                    "Create the implementation plan."
                ),
            },
        ]

        result = await self.llm.chat_json(messages, temperature=0.3)

        result.setdefault("implementation_order", [])
        result.setdefault("success_criteria", [])
        result.setdefault("dependencies_between_phases", {})

        # Ensure backend phases come before frontend API-dependent phases
        result["implementation_order"] = self._enforce_dependency_order(
            result["implementation_order"],
            result.get("dependencies_between_phases", {}),
        )

        return result

    # ------------------------------------------------------------------
    # Step 4: Assembly
    # ------------------------------------------------------------------

    def _assemble_blueprint(
        self,
        request: str,
        tech: dict[str, Any],
        arch: dict[str, Any],
        plan: dict[str, Any],
    ) -> ProjectBlueprint:
        """Ghép tất cả thành ProjectBlueprint dataclass.

        Merges LLM output với template scaffold files nếu có match.
        """
        project_name = tech.get("project_name", "my-project")
        project_desc = tech.get(
            "project_description", request[:200]
        )

        # Build TechStack
        ts_data = tech.get("tech_stack", {})
        tech_stack = TechStack(
            frontend=ts_data.get("frontend", ""),
            backend=ts_data.get("backend", ""),
            database=ts_data.get("database", ""),
            css_framework=ts_data.get("css_framework", ""),
            ai_integration=ts_data.get("ai_integration", ""),
            package_manager=ts_data.get("package_manager", "npm"),
        )

        # Build ComponentSpecs
        components = [
            ComponentSpec(
                name=c.get("name", ""),
                type=c.get("type", "component"),
                description=c.get("description", ""),
                props=c.get("props", {}),
                state=c.get("state", {}),
                file_path=c.get("file_path", ""),
            )
            for c in arch.get("components", [])
        ]

        # Build ModelSpecs
        data_models = [
            ModelSpec(
                name=m.get("name", ""),
                fields=m.get("fields", {}),
                relationships=m.get("relationships", []),
            )
            for m in arch.get("data_models", [])
        ]

        # Build APISpecs
        api_endpoints = [
            APISpec(
                method=a.get("method", "GET"),
                path=a.get("path", "/"),
                description=a.get("description", ""),
                request_body=a.get("request_body"),
                response_schema=a.get("response_schema"),
            )
            for a in arch.get("api_endpoints", [])
        ]

        # Build PageSpecs
        pages = [
            PageSpec(
                name=p.get("name", ""),
                route=p.get("route", "/"),
                description=p.get("description", ""),
                components=p.get("components", []),
                screenshot_check=p.get("screenshot_check", ""),
            )
            for p in arch.get("pages", [])
        ]

        # Build DependencySpec
        deps_data = arch.get("dependencies", {})
        dependencies = DependencySpec(
            npm=deps_data.get("npm", []),
            npm_dev=deps_data.get("npm_dev", []),
            pip=deps_data.get("pip", []),
        )

        # File structure: merge template scaffold + LLM-generated
        file_structure = dict(arch.get("file_structure", {}))
        template_match = tech.get("template_match", "")
        if template_match:
            scaffold_files = self.template_registry.get_scaffold_files(
                template_match, project_name
            )
            if scaffold_files:
                logger.info(
                    "blueprint_template_merged",
                    template=template_match,
                    scaffold_file_count=len(scaffold_files),
                )
                for sf in scaffold_files:
                    # Template files have lower priority — only add if
                    # not already specified by LLM
                    if sf.path not in file_structure:
                        file_structure[sf.path] = {
                            "description": sf.description,
                            "language": sf.language,
                            "from_template": True,
                        }

                # Merge template dependencies into DependencySpec
                template = self.template_registry.get_template(template_match)
                if template:
                    tmpl_deps: DependencySpec = template.get(
                        "dependencies", DependencySpec()
                    )
                    if isinstance(tmpl_deps, DependencySpec):
                        dependencies = self._merge_dependencies(
                            dependencies, tmpl_deps
                        )

        return ProjectBlueprint(
            name=project_name,
            description=project_desc,
            tech_stack=tech_stack,
            file_structure=file_structure,
            components=components,
            data_models=data_models,
            api_endpoints=api_endpoints,
            pages=pages,
            dependencies=dependencies,
            implementation_order=plan.get("implementation_order", []),
            success_criteria=plan.get("success_criteria", []),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_blueprint_dict(
        self, data: dict[str, Any], fallback_name: str = "my-project"
    ) -> ProjectBlueprint:
        """Parse raw dict (from LLM) into ProjectBlueprint safely."""
        data.setdefault("name", fallback_name)
        try:
            return ProjectBlueprint.from_dict(data)
        except (TypeError, KeyError) as exc:
            logger.warning(
                "blueprint_parse_fallback",
                error=str(exc),
            )
            # Fallback: construct manually with whatever we can extract
            return ProjectBlueprint(
                name=data.get("name", fallback_name),
                description=data.get("description", ""),
            )

    @staticmethod
    def _merge_dependencies(
        primary: DependencySpec, secondary: DependencySpec
    ) -> DependencySpec:
        """Merge two DependencySpecs, primary takes precedence."""
        npm = list(dict.fromkeys(primary.npm + secondary.npm))
        npm_dev = list(dict.fromkeys(primary.npm_dev + secondary.npm_dev))
        pip = list(dict.fromkeys(primary.pip + secondary.pip))
        return DependencySpec(npm=npm, npm_dev=npm_dev, pip=pip)

    @staticmethod
    def _enforce_dependency_order(
        order: list[str],
        deps: dict[str, list[str]],
    ) -> list[str]:
        """Reorder implementation phases respecting dependencies.

        Uses topological-sort-like approach: phases whose dependencies
        are all already placed get scheduled next.
        """
        if not order or not deps:
            return order

        remaining = list(order)
        sorted_order: list[str] = []
        placed: set[str] = set()

        max_iters = len(remaining) * len(remaining) + 1
        iters = 0

        while remaining and iters < max_iters:
            iters += 1
            for phase in remaining:
                phase_deps = deps.get(phase, [])
                if all(d in placed for d in phase_deps):
                    sorted_order.append(phase)
                    placed.add(phase)
                    remaining.remove(phase)
                    break
            else:
                # No phase could be placed — break cycle by forcing first
                forced = remaining.pop(0)
                sorted_order.append(forced)
                placed.add(forced)

        return sorted_order


# ======================================================================
# LLM System Prompts
# ======================================================================

_TECH_STACK_SYSTEM_PROMPT = """You are a senior software architect and tech stack advisor.

Analyze the user's project requirements and recommend the optimal technology stack.

Available project templates (pre-configured scaffolds): {available_templates}

Return a JSON object with EXACTLY this structure:
{{
    "project_name": "kebab-case-name",
    "project_description": "Brief one-line description",
    "tech_stack": {{
        "frontend": "react | next | vue | svelte | none",
        "backend": "fastapi | flask | express | next | none",
        "database": "sqlite | postgres | mongodb | none",
        "css_framework": "tailwindcss | bootstrap | none",
        "ai_integration": "openai | langchain | none",
        "package_manager": "npm | pip | pnpm"
    }},
    "template_match": "template-name-from-available-list or empty string",
    "reasoning": "2-3 sentences explaining the choices"
}}

Rules:
- Choose the simplest stack that meets all requirements.
- Prefer templates when they match 80%+ of needs.
- template_match must be an exact name from the available list, or empty string.
- For full-stack apps, consider "fullstack-react-fastapi" template.
- For AI features, include ai_integration field.
- project_name must be lowercase kebab-case, max 40 chars.

IMPORTANT: Return ONLY valid JSON."""

_ARCHITECTURE_SYSTEM_PROMPT = """You are an expert software architect designing a complete project.

Given the project requirements and chosen tech stack, design the full architecture.

Return a JSON object with EXACTLY this structure:
{
    "components": [
        {
            "name": "ComponentName",
            "type": "page | component | layout | api",
            "description": "What this component does",
            "props": {"propName": "type"},
            "state": {"stateName": "type"},
            "file_path": "src/components/ComponentName.tsx"
        }
    ],
    "pages": [
        {
            "name": "PageName",
            "route": "/path",
            "description": "What this page shows",
            "components": ["ComponentName1", "ComponentName2"],
            "screenshot_check": "Visual description for QA"
        }
    ],
    "data_models": [
        {
            "name": "ModelName",
            "fields": {"fieldName": "string | number | boolean | date"},
            "relationships": ["has_many:OtherModel"]
        }
    ],
    "api_endpoints": [
        {
            "method": "GET | POST | PUT | DELETE",
            "path": "/api/resource",
            "description": "What this endpoint does",
            "request_body": {"field": "type"} | null,
            "response_schema": {"field": "type"} | null
        }
    ],
    "file_structure": {
        "path/to/file.ext": {
            "description": "Purpose of this file",
            "language": "tsx | ts | py | css | json"
        }
    },
    "dependencies": {
        "npm": ["package1", "package2"],
        "npm_dev": ["dev-package1"],
        "pip": ["python-package1"]
    }
}

Rules:
- Every page MUST have a unique route.
- Every component MUST have a file_path.
- Include shared/layout components (Navbar, Footer, Layout).
- API endpoints must cover all CRUD operations for each data model.
- file_structure must list every file that needs to be created.
- Include config files (tailwind.config, tsconfig, etc.) in file_structure.
- For React/Next: use .tsx extensions for components.
- For Python: use standard project layout (app/, main.py, etc.).
- screenshot_check for each page should describe expected visual appearance.

IMPORTANT: Return ONLY valid JSON."""

_IMPL_PLAN_SYSTEM_PROMPT = """You are a project planning expert creating an implementation plan.

Given the project architecture, determine the optimal implementation order and success criteria.

Return a JSON object with EXACTLY this structure:
{
    "implementation_order": [
        "Phase 1: Project scaffold & config files",
        "Phase 2: Data models & database setup",
        "Phase 3: Backend API endpoints",
        "Phase 4: Frontend layout & navigation",
        "Phase 5: Frontend pages & components",
        "Phase 6: API integration & state management",
        "Phase 7: Styling & responsive design",
        "Phase 8: Testing & error handling"
    ],
    "success_criteria": [
        "All pages render without console errors",
        "API endpoints return correct status codes",
        "Navigation between all pages works",
        "Responsive layout works on mobile viewport",
        "All CRUD operations work end-to-end",
        "No TypeScript compilation errors",
        "Lighthouse accessibility score > 80"
    ],
    "dependencies_between_phases": {
        "Phase 3: Backend API endpoints": ["Phase 2: Data models & database setup"],
        "Phase 6: API integration & state management": [
            "Phase 3: Backend API endpoints",
            "Phase 5: Frontend pages & components"
        ]
    }
}

Rules:
- Backend/data phases MUST come before frontend phases that depend on them.
- Config/scaffold is always Phase 1.
- Each phase should be independently testable.
- Success criteria must be measurable and verifiable.
- Include criteria for both functional and visual quality.
- Typically 5-10 phases for a standard project.
- dependencies_between_phases maps phase → list of prerequisite phases.

IMPORTANT: Return ONLY valid JSON."""

_REFINE_SYSTEM_PROMPT = """You are a software architect refining an existing project blueprint.

Given the current blueprint JSON and user feedback, produce an updated blueprint.

Rules:
- Preserve all unchanged fields exactly as-is.
- Only modify fields relevant to the feedback.
- If adding new pages/components, also update file_structure and implementation_order.
- If changing tech stack, update dependencies accordingly.
- Maintain consistency: every page has a route, every component has a file_path.
- Return the COMPLETE blueprint JSON (not a diff).

IMPORTANT: Return ONLY valid JSON matching the exact blueprint schema."""
