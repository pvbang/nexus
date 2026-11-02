"""
Nexus - Project Builder Pipeline.

End-to-end pipeline cho tự động build full project:
Blueprint → Scaffold → Implement → Test → Visual QA → Iterate → Deliver

Tích hợp tất cả components của project_builder module.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from calendar import timegm
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import structlog

from app.core.environment_details import detect_available_runtimes, format_runtime_constraints
from app.project_builder.blueprint import BlueprintGenerator
from app.project_builder.dev_server import DevServerManager
from app.project_builder.error_patterns import ErrorPatternMatcher
from app.project_builder.iteration import FixPlan, IterationController
from app.project_builder.models import (
    BuildResult,
    FileSpec,
    ImplementResult,
    IterationState,
    PageSpec,
    ProjectBlueprint,
    ProjectProgress,
    ProjectResult,
    ScaffoldResult,
    ServerInfo,
    TestResult,
    VisualQAResult,
)
from app.project_builder.templates import ProjectTemplateRegistry
from app.project_builder.visual_qa import VisualQAEngine
from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)

# Base workspace cho projects
PROJECTS_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data")) / "projects"

PROJECT_BUILDER_PRODUCTION_STANDARDS = (
    "\n\n## Production Standards for Project Generation:\n"
    "- PHẢI dùng modern framework (React/Next.js/Vue + FastAPI/Express/NestJS)\n"
    "- PHẢI có proper project structure (src/, components/, api/, etc.)\n"
    "- PHẢI có authentication (login/register) nếu app có user concept\n"
    "- PHẢI có database schema + migrations\n"
    "- PHẢI có environment config (.env, config files)\n"
    "- PHẢI responsive design (mobile-first)\n"
    "- PHẢI có error boundaries, loading states, 404 pages\n"
    "- PHẢI deploy local sau khi build xong\n"
    "- KHÔNG BAO GIỜ chỉ tạo single HTML file cho website request\n"
)


class ProjectBuilderPipeline:
    """End-to-end pipeline cho tự động build full project.

    Pipeline phases:
        1. Blueprint — LLM thiết kế project hoàn chỉnh
        2. Scaffold — Tạo project structure + install dependencies
        3. Implement — Code từng file theo implementation order
        4. Build & Test — Compile, check errors
        5. Visual QA — Screenshot pages, check UI quality
        6. Iterate — Fix issues, rebuild, recheck
        7. Deliver — Start dev server, return preview URL

    Event emission: emit events qua callback cho real-time frontend updates.
    """

    def __init__(
        self,
        llm: LLMClient | None = None,
        event_callback: Callable | None = None,
        max_iterations: int = 10,
    ) -> None:
        self.llm = llm or get_core_llm()
        self.event_callback = event_callback

        # Sub-components
        self.blueprint_gen = BlueprintGenerator(self.llm)
        self.template_registry = ProjectTemplateRegistry()
        self.dev_server = DevServerManager()
        self.visual_qa = VisualQAEngine(self.llm)
        self.iteration_ctrl = IterationController(
            self.llm, max_iterations=max_iterations,
        )
        self.error_matcher = ErrorPatternMatcher(self.llm)

        # Tool executor for file operations and commands — lazy import
        from app.mcp.tool_executor import ToolExecutor

        self.tool_executor = ToolExecutor()

        # State
        self._progress = ProjectProgress()
        self._project_path: Path | None = None
        self._blueprint: ProjectBlueprint | None = None
        self._server: ServerInfo | None = None
        self._written_files: dict[str, str] = {}  # path → content for context

    # ================================================================
    # Main entry point
    # ================================================================

    async def execute(
        self, request: str, user_id: str = "default",
    ) -> ProjectResult:
        """Build full project từ mô tả requirements.

        Flow:
            1. phase_blueprint() → ProjectBlueprint
            2. phase_scaffold() → ScaffoldResult
            3. phase_implement() → implement all files
            4. phase_build_and_test() → BuildResult + TestResult
            5. phase_visual_qa() → list[VisualQAResult] (nếu có dev server)
            6. phase_iterate() → fix loop cho đến khi quality đạt
            7. Return ProjectResult

        Mỗi phase emit events qua event_callback.
        Bất kỳ phase nào fail → log error, continue nếu có thể,
        hoặc return partial result.
        """
        start_ts = time.time()
        result = ProjectResult()
        self._progress = ProjectProgress(
            current_phase="blueprint",
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

        try:
            # Phase 1: Blueprint
            await self._emit("project_phase", {"phase": "blueprint", "status": "active"})
            blueprint = await self.phase_blueprint(request)
            result.blueprint = blueprint
            self._blueprint = blueprint
            self._progress.current_phase = "scaffold"
            self._progress.phases_completed.append("blueprint")
            self._progress.total_files = len(blueprint.file_structure)
            await self._emit("project_phase", {
                "phase": "blueprint",
                "status": "completed",
                "blueprint_summary": blueprint.name,
            })

            # Phase 2: Scaffold
            await self._emit("project_phase", {"phase": "scaffold", "status": "active"})
            scaffold = await self.phase_scaffold(blueprint)
            result.scaffold = scaffold
            self._progress.phases_completed.append("scaffold")
            await self._emit("project_phase", {
                "phase": "scaffold",
                "status": "completed",
                "files": len(scaffold.files_created),
            })

            if not scaffold.files_created:
                result.error = "Scaffold failed: no files created"
                return result

            # Phase 3: Implement
            await self._emit("project_phase", {"phase": "implement", "status": "active"})
            impl_results = await self.phase_implement(blueprint)
            self._progress.phases_completed.append("implement")
            self._progress.files_written = len(
                [r for r in impl_results if r.success],
            )
            await self._emit("project_phase", {
                "phase": "implement",
                "status": "completed",
                "files_written": self._progress.files_written,
            })

            # Phase 4: Build & Test
            await self._emit("project_phase", {"phase": "test", "status": "active"})
            build_result, test_result = await self.phase_build_and_test()
            self._progress.phases_completed.append("test")
            await self._emit("project_phase", {
                "phase": "test",
                "status": "completed",
                "build_ok": build_result.success,
            })

            # Phase 5: Visual QA (nếu có dev server)
            visual_results: list[VisualQAResult] = []
            try:
                await self._emit("project_phase", {
                    "phase": "visual_qa", "status": "active",
                })
                server = await self._start_preview_server(blueprint)
                if server:
                    self._server = server
                    result.server = server
                    visual_results = await self.phase_visual_qa(
                        server.url, blueprint.pages,
                    )
                self._progress.phases_completed.append("visual_qa")
                await self._emit("project_phase", {
                    "phase": "visual_qa", "status": "completed",
                })
            except Exception as exc:
                logger.warning("visual_qa_phase_skipped", error=str(exc))

            # Phase 6: Iterate
            await self._emit("project_phase", {"phase": "iterate", "status": "active"})
            await self.phase_iterate(
                build_result, test_result, visual_results, blueprint,
            )
            self._progress.phases_completed.append("iterate")
            await self._emit("project_phase", {"phase": "iterate", "status": "completed"})

            # Calculate final quality
            result.iterations = self.iteration_ctrl.state.iteration_number
            # Get quality from last evaluation or progress
            last_quality = self._progress.quality_score
            if self.iteration_ctrl._history:
                last_quality = self.iteration_ctrl._history[-1].quality_score
            result.final_quality = last_quality
            result.progress = self._progress
            result.success = build_result.success or result.final_quality >= 0.6

            await self._emit("project_complete", {
                "success": result.success,
                "quality": result.final_quality,
                "iterations": result.iterations,
                "server_url": self._server.url if self._server else None,
            })

        except Exception as exc:
            logger.error("project_builder_pipeline_failed", error=str(exc))
            result.error = str(exc)
            await self._emit("project_error", {"error": str(exc)})

        finally:
            parsed_start = self._parse_time(self._progress.started_at)
            self._progress.elapsed_seconds = (
                time.time() - (parsed_start or start_ts)
            )

        return result

    # ================================================================
    # Phase methods
    # ================================================================

    async def phase_blueprint(self, request: str) -> ProjectBlueprint:
        """Phase 1: Generate project blueprint từ requirements."""
        logger.info("phase_blueprint_start", request_len=len(request))

        # Detect environment runtimes and inject constraints into request
        runtimes = await detect_available_runtimes()
        runtime_constraints = format_runtime_constraints(runtimes)
        augmented_request = f"{request}\n\n{runtime_constraints}"

        blueprint = await self.blueprint_gen.generate(augmented_request)
        logger.info(
            "blueprint_generated",
            name=blueprint.name,
            components=len(blueprint.components),
            pages=len(blueprint.pages),
            impl_phases=len(blueprint.implementation_order),
        )
        return blueprint

    async def phase_scaffold(
        self, blueprint: ProjectBlueprint,
    ) -> ScaffoldResult:
        """Phase 2: Tạo project structure + install dependencies.

        1. Tạo project directory
        2. Viết scaffold files (từ template hoặc blueprint)
        3. Install dependencies (npm install / pip install)
        """
        logger.info("phase_scaffold_start", project=blueprint.name)
        scaffold = ScaffoldResult()

        try:
            # 1. Sanitize name, create directory
            project_name = self._sanitize_project_name(blueprint.name)
            project_path = PROJECTS_DIR / project_name
            project_path.mkdir(parents=True, exist_ok=True)
            self._project_path = project_path

            # 2. Get scaffold files from template OR blueprint
            template_tech = self._detect_template_tech(blueprint)
            scaffold_files = self.template_registry.get_scaffold_files(
                template_tech, project_name,
            )

            # If no template match, build FileSpecs from blueprint.file_structure
            if not scaffold_files:
                scaffold_files = self._blueprint_to_scaffold_files(blueprint)

            # 3. Write each scaffold file
            dirs_created: set[str] = set()
            for file_spec in scaffold_files:
                file_path = project_path / file_spec.path
                file_path.parent.mkdir(parents=True, exist_ok=True)

                rel_dir = str(file_path.parent.relative_to(project_path))
                if rel_dir != ".":
                    dirs_created.add(rel_dir)

                file_path.write_text(file_spec.content, encoding="utf-8")
                scaffold.files_created.append(file_spec.path)
                self._written_files[file_spec.path] = file_spec.content

                await self._emit("file_scaffolded", {"path": file_spec.path})

            scaffold.dirs_created = sorted(dirs_created)

            # 4. Install dependencies
            deps = blueprint.dependencies
            deps_installed = False

            if deps.npm or deps.npm_dev:
                npm_result = await self._run_command_in_project("npm install")
                if npm_result.get("success"):
                    deps_installed = True
                else:
                    scaffold.errors.append(
                        f"npm install failed: {npm_result.get('output', '')[:500]}",
                    )

            if deps.pip:
                req_path = project_path / "requirements.txt"
                if req_path.exists():
                    pip_cmd = "pip install -r requirements.txt"
                else:
                    pip_cmd = f"pip install {' '.join(deps.pip)}"
                pip_result = await self._run_command_in_project(pip_cmd)
                if pip_result.get("success"):
                    deps_installed = True
                else:
                    scaffold.errors.append(
                        f"pip install failed: {pip_result.get('output', '')[:500]}",
                    )

            scaffold.deps_installed = deps_installed

            logger.info(
                "phase_scaffold_done",
                files=len(scaffold.files_created),
                dirs=len(scaffold.dirs_created),
                deps_installed=deps_installed,
            )

        except Exception as exc:
            logger.error("phase_scaffold_failed", error=str(exc))
            scaffold.errors.append(str(exc))

        return scaffold

    async def phase_implement(
        self, blueprint: ProjectBlueprint,
    ) -> list[ImplementResult]:
        """Phase 3: Code từng file theo implementation order.

        Cho mỗi phase trong implementation_order:
        1. Xác định files cần viết (từ blueprint.get_files_for_phase)
        2. LLM generate code cho file đó
        3. Write file to project directory
        4. Emit progress event

        Context window management: chỉ pass relevant files, không ALL.
        """
        logger.info(
            "phase_implement_start",
            phases=len(blueprint.implementation_order),
        )
        results: list[ImplementResult] = []

        for phase_idx, phase_description in enumerate(
            blueprint.implementation_order,
        ):
            # Determine which files belong to this phase
            files_for_phase = self._determine_files_for_phase(
                phase_idx, phase_description, blueprint,
            )

            if not files_for_phase:
                logger.debug(
                    "phase_implement_no_files",
                    phase=phase_description,
                    index=phase_idx,
                )
                continue

            for file_spec in files_for_phase:
                # Skip if already written during scaffold with real content
                existing = self._written_files.get(file_spec.path, "")
                if existing and len(existing) > 50:
                    results.append(ImplementResult(
                        file_path=file_spec.path,
                        success=True,
                        content_preview=existing[:200],
                    ))
                    continue

                impl_result = await self._implement_single_file(
                    file_spec,
                    blueprint,
                    list(self._written_files.keys()),
                )
                results.append(impl_result)

                if impl_result.success:
                    self._written_files[file_spec.path] = (
                        impl_result.content_preview
                    )

                await self._emit("file_implemented", {
                    "path": file_spec.path,
                    "success": impl_result.success,
                    "phase": phase_description,
                })

        logger.info(
            "phase_implement_done",
            total=len(results),
            success=len([r for r in results if r.success]),
            failed=len([r for r in results if not r.success]),
        )
        return results

    async def phase_build_and_test(self) -> tuple[BuildResult, TestResult]:
        """Phase 4: Build project + run tests.

        1. Detect build command (from template or blueprint)
        2. Execute build in project directory
        3. Parse output for errors/warnings
        4. Run tests if test command exists
        5. Use ErrorPatternMatcher to classify errors
        """
        logger.info("phase_build_test_start")
        build_result = BuildResult(success=True)
        test_result = TestResult(success=True)

        if not self._project_path or not self._blueprint:
            build_result.success = False
            build_result.errors.append("No project path or blueprint set")
            test_result.success = False
            return build_result, test_result

        # Build
        build_cmd = await self._detect_build_command(self._blueprint)
        if build_cmd:
            try:
                cmd_result = await self._run_command_in_project(build_cmd)
                output = cmd_result.get("output", "")
                build_result.output = output
                build_result.success = cmd_result.get("success", False)

                # Parse errors/warnings using error_matcher
                if not build_result.success:
                    for line in output.split("\n"):
                        line_stripped = line.strip()
                        if not line_stripped:
                            continue
                        match = self.error_matcher.match(line_stripped)
                        if match and match.category in (
                            "syntax", "import", "type", "build",
                        ):
                            build_result.errors.append(
                                f"[{match.pattern_name}] {line_stripped}",
                            )
                        elif "warning" in line.lower():
                            build_result.warnings.append(line_stripped)

                    # If no structured errors found, add raw output
                    if not build_result.errors:
                        build_result.errors.append(output[:2000])

            except Exception as exc:
                logger.error("build_command_failed", error=str(exc))
                build_result.success = False
                build_result.errors.append(str(exc))
        else:
            logger.info("no_build_command_detected")

        # Test
        test_cmd = await self._detect_test_command(self._blueprint)
        if test_cmd:
            try:
                cmd_result = await self._run_command_in_project(test_cmd)
                output = cmd_result.get("output", "")
                test_result.output = output
                test_result.success = cmd_result.get("success", False)

                # Parse test counts from output
                self._parse_test_counts(test_result, output)

            except Exception as exc:
                logger.error("test_command_failed", error=str(exc))
                test_result.success = False
                test_result.errors.append(str(exc))
        else:
            logger.info("no_test_command_detected")

        logger.info(
            "phase_build_test_done",
            build_ok=build_result.success,
            test_ok=test_result.success,
            build_errors=len(build_result.errors),
            build_warnings=len(build_result.warnings),
        )
        return build_result, test_result

    async def phase_visual_qa(
        self, server_url: str, pages: list[PageSpec],
    ) -> list[VisualQAResult]:
        """Phase 5: Visual QA — screenshot + analyze mỗi page."""
        logger.info(
            "phase_visual_qa_start",
            url=server_url,
            pages=len(pages),
        )

        if not pages:
            logger.info("phase_visual_qa_no_pages")
            return []

        results = await self.visual_qa.check_all_pages(
            server_url, pages, viewport="desktop",
        )

        avg_score = (
            sum(r.score for r in results) / len(results) if results else 0.0
        )
        self._progress.quality_score = avg_score

        logger.info(
            "phase_visual_qa_done",
            results=len(results),
            avg_score=round(avg_score, 3),
        )
        return results

    async def phase_iterate(
        self,
        build_result: BuildResult,
        test_result: TestResult,
        visual_results: list[VisualQAResult],
        blueprint: ProjectBlueprint,
    ) -> None:
        """Phase 6: Iterative fix loop.

        Loop:
        1. iteration_ctrl.evaluate_iteration() → decision
        2. If done/stalled/max_reached → break
        3. iteration_ctrl.plan_fixes() → list[FixPlan]
        4. Apply fixes (write_file hoặc apply_diff)
        5. Rebuild + retest
        6. Record iteration
        7. Check should_continue()
        """
        logger.info("phase_iterate_start")

        while self.iteration_ctrl.should_continue():
            # Evaluate
            decision = await self.iteration_ctrl.evaluate_iteration(
                build_result, test_result, visual_results,
            )

            if decision.action == "completed":
                logger.info(
                    "iteration_done_quality_met",
                    quality=decision.quality_score,
                )
                self._progress.quality_score = decision.quality_score
                break

            if decision.action in ("stalled", "max_reached"):
                logger.warning(
                    "iteration_stopped",
                    reason=decision.action,
                    quality=decision.quality_score,
                )
                self._progress.quality_score = decision.quality_score
                break

            # Plan fixes
            project_context = self._build_project_context_summary()
            fix_plans = await self.iteration_ctrl.plan_fixes(
                decision.issues_to_fix, project_context,
            )

            # Apply fixes
            fixes_applied = 0
            for fix in fix_plans:
                try:
                    success = await self._apply_fix(fix)
                    if success:
                        fixes_applied += 1
                except Exception as exc:
                    logger.warning(
                        "fix_apply_failed",
                        file=fix.file_path,
                        error=str(exc),
                    )

            self._progress.errors_fixed += fixes_applied

            # Rebuild + retest
            build_result, test_result = await self.phase_build_and_test()

            # Visual recheck if server running
            if self._server:
                try:
                    visual_results = await self.phase_visual_qa(
                        self._server.url, blueprint.pages,
                    )
                except Exception as exc:
                    logger.warning(
                        "iteration_visual_qa_failed", error=str(exc),
                    )

            # Record
            self.iteration_ctrl.record_iteration(
                build_result, test_result, visual_results, fixes_applied,
            )

            self._progress.iteration_count = (
                self.iteration_ctrl.state.iteration_number
            )
            self._progress.quality_score = decision.quality_score

            await self._emit("iteration_complete", {
                "iteration": self.iteration_ctrl.state.iteration_number,
                "quality": decision.quality_score,
                "fixes": fixes_applied,
            })

        logger.info(
            "phase_iterate_done",
            iterations=self.iteration_ctrl.state.iteration_number,
            quality=self._progress.quality_score,
        )

    # ================================================================
    # Helper methods — file implementation
    # ================================================================

    async def _implement_single_file(
        self,
        file_spec: FileSpec,
        blueprint: ProjectBlueprint,
        existing_files: list[str],
    ) -> ImplementResult:
        """Generate code cho single file bằng LLM.

        Context injection:
        - Tech stack info
        - Related files content (only files this file depends on)
        - Component/page/model specs from blueprint
        - Keep prompt focused — don't dump entire project
        """
        logger.debug("implement_file_start", path=file_spec.path)

        try:
            # Build context for LLM
            context_parts = [
                f"Project: {blueprint.name}",
                f"Description: {blueprint.description}",
                f"Tech Stack: frontend={blueprint.tech_stack.frontend}, "
                f"backend={blueprint.tech_stack.backend}, "
                f"css={blueprint.tech_stack.css_framework}, "
                f"db={blueprint.tech_stack.database}",
                f"File to create: {file_spec.path}",
                f"File description: {file_spec.description or 'Not specified'}",
                f"Language: {file_spec.language or 'auto-detect from extension'}",
            ]

            # Add dependency files context (only deps, not ALL files)
            for dep_path in file_spec.depends_on:
                content = self._read_project_file(dep_path)
                if content:
                    truncated = content[:2000]
                    context_parts.append(
                        f"\n--- Dependency: {dep_path} ---\n{truncated}",
                    )

            # Add relevant specs from blueprint
            specs_context = self._get_relevant_specs(file_spec, blueprint)
            if specs_context:
                context_parts.append(f"\n--- Relevant Specs ---\n{specs_context}")

            # LLM generate
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an expert developer. Generate COMPLETE, "
                        "WORKING code for the specified file. The code must "
                        "be production-ready, well-structured, and follow "
                        "best practices for the technology stack.\n\n"
                        "Return ONLY the code. No markdown fences, no "
                        "explanation, no comments about what to do — just "
                        "the complete file content."
                        + PROJECT_BUILDER_PRODUCTION_STANDARDS
                    ),
                },
                {
                    "role": "user",
                    "content": "\n".join(context_parts),
                },
            ]

            result = await self.llm.chat(
                messages, temperature=0.3, max_tokens=4096,
            )
            code = result.get("content", "")

            # Clean code output
            code = self._clean_code_output(code)

            if not code.strip():
                return ImplementResult(
                    file_path=file_spec.path,
                    success=False,
                    errors=["LLM returned empty content"],
                )

            # Write file
            if self._project_path:
                file_path = self._project_path / file_spec.path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(code, encoding="utf-8")

            logger.debug(
                "implement_file_done",
                path=file_spec.path,
                code_len=len(code),
            )
            return ImplementResult(
                file_path=file_spec.path,
                success=True,
                content_preview=code[:200],
            )

        except Exception as exc:
            logger.error(
                "implement_file_failed",
                path=file_spec.path,
                error=str(exc),
            )
            return ImplementResult(
                file_path=file_spec.path,
                success=False,
                errors=[str(exc)],
            )

    async def _apply_fix(self, fix: FixPlan) -> bool:
        """Apply single fix: create, modify, or delete file.

        For modify: use search_replace pairs via direct file write.
        For create: write new file.
        For delete: delete file.
        """
        if not self._project_path:
            return False

        target_path = self._project_path / fix.file_path
        logger.debug(
            "apply_fix",
            file=fix.file_path,
            action=fix.action,
            description=fix.description[:100],
        )

        try:
            if fix.action == "delete":
                if target_path.exists():
                    target_path.unlink()
                    self._written_files.pop(fix.file_path, None)
                return True

            if fix.action == "create":
                target_path.parent.mkdir(parents=True, exist_ok=True)
                content = fix.new_content
                if not content:
                    # Generate via LLM if no content provided
                    content = await self._generate_fix_content(fix)
                target_path.write_text(content, encoding="utf-8")
                self._written_files[fix.file_path] = content[:200]
                return True

            if fix.action == "modify":
                if not target_path.exists():
                    logger.warning(
                        "fix_modify_file_not_found", path=fix.file_path,
                    )
                    return False

                content = target_path.read_text(encoding="utf-8")

                if fix.search_replace:
                    # Apply search/replace pairs
                    for search_text, replace_text in fix.search_replace:
                        if search_text in content:
                            content = content.replace(
                                search_text, replace_text, 1,
                            )
                        else:
                            logger.debug(
                                "search_text_not_found",
                                file=fix.file_path,
                                search=search_text[:80],
                            )
                elif fix.new_content:
                    # Full file replacement
                    content = fix.new_content

                target_path.write_text(content, encoding="utf-8")
                self._written_files[fix.file_path] = content[:200]
                return True

        except Exception as exc:
            logger.error(
                "apply_fix_error",
                file=fix.file_path,
                action=fix.action,
                error=str(exc),
            )

        return False

    # ================================================================
    # Helper methods — server & commands
    # ================================================================

    async def _start_preview_server(
        self, blueprint: ProjectBlueprint,
    ) -> ServerInfo | None:
        """Start dev server for preview."""
        if not self._project_path:
            return None

        tech = self._detect_server_tech(blueprint)
        if not tech:
            logger.info("no_server_tech_detected")
            return None

        try:
            info = await self.dev_server.start_server(
                str(self._project_path), tech,
            )

            # If running behind Docker/NAT and we don't have an explicit external host,
            # create a real public URL via tunnel (no auth; trycloudflare.com).
            try:
                import os
                from pathlib import Path
                from app.config import get_settings

                s = get_settings()
                in_docker = Path("/.dockerenv").exists() or os.environ.get("RUNNING_IN_DOCKER") == "1"
                has_explicit_external = bool((s.external_base_url or "").strip() or (s.public_host or "").strip())
                if in_docker and not has_explicit_external and self.tool_executor:
                    r = await self.tool_executor.execute("expose_port", {"port": int(info.port)})
                    url = (r.get("data") or {}).get("url") if isinstance(r, dict) else None
                    if url:
                        info.public_url = str(url).strip()
            except Exception:
                pass

            logger.info(
                "preview_server_started",
                url=info.url,
                external_url=info.external_url,
                public_url=getattr(info, "public_url", ""),
                tech=tech,
                server_id=info.server_id,
            )
            return info
        except Exception as exc:
            logger.warning("preview_server_start_failed", error=str(exc))
            return None

    async def _run_command_in_project(
        self, command: str,
    ) -> dict[str, Any]:
        """Execute command in project directory via ToolExecutor."""
        if not self._project_path:
            return {"success": False, "output": "No project path set"}

        full_command = f"cd {self._project_path} && {command}"
        try:
            return await self.tool_executor.execute(
                "execute_command", {"command": full_command},
            )
        except Exception as exc:
            logger.error(
                "command_execution_error",
                command=command,
                error=str(exc),
            )
            return {"success": False, "output": str(exc)}

    # ================================================================
    # Helper methods — detection & parsing
    # ================================================================

    async def _detect_build_command(
        self, blueprint: ProjectBlueprint,
    ) -> str:
        """Detect appropriate build command based on tech stack."""
        # Check template registry first
        tech = self._detect_template_tech(blueprint)
        template = self.template_registry.get_template(tech)
        if template and template.get("build_command"):
            return template["build_command"]

        # Fallback: detect from project files
        if self._project_path:
            pkg_json = self._project_path / "package.json"
            if pkg_json.exists():
                try:
                    pkg_data = json.loads(
                        pkg_json.read_text(encoding="utf-8"),
                    )
                    scripts = pkg_data.get("scripts", {})
                    if "build" in scripts:
                        return "npm run build"
                    if "tsc" in scripts.get("build", ""):
                        return "npx tsc --noEmit"
                except (json.JSONDecodeError, OSError):
                    pass

            pyproject = self._project_path / "pyproject.toml"
            if pyproject.exists():
                return "python -m py_compile main.py"

        # For frontend-only projects without explicit build
        if blueprint.tech_stack.frontend in ("react", "vue", "svelte"):
            return "npm run build"
        if blueprint.tech_stack.frontend == "next":
            return "npm run build"

        return ""

    async def _detect_test_command(
        self, blueprint: ProjectBlueprint,
    ) -> str | None:
        """Detect test command. Return None if no tests."""
        if not self._project_path:
            return None

        pkg_json = self._project_path / "package.json"
        if pkg_json.exists():
            try:
                pkg_data = json.loads(
                    pkg_json.read_text(encoding="utf-8"),
                )
                scripts = pkg_data.get("scripts", {})
                if "test" in scripts and scripts["test"] != 'echo "Error: no test specified" && exit 1':
                    return "npm test"
            except (json.JSONDecodeError, OSError):
                pass

        # Python tests
        if (self._project_path / "pytest.ini").exists() or (
            self._project_path / "tests"
        ).is_dir():
            return "python -m pytest"

        return None

    def _detect_template_tech(self, blueprint: ProjectBlueprint) -> str:
        """Detect best matching template tech from blueprint."""
        frontend = blueprint.tech_stack.frontend
        backend = blueprint.tech_stack.backend

        # Check for fullstack match first
        if frontend and backend:
            fullstack_key = f"fullstack-{frontend}-{backend}"
            if self.template_registry.get_template(fullstack_key):
                return fullstack_key

        # Frontend-specific templates
        tech_map: dict[str, str] = {
            "react": "react-vite-tailwind",
            "next": "next-app",
        }
        if frontend in tech_map:
            return tech_map[frontend]

        # Backend-specific templates
        if backend == "fastapi":
            return "fastapi"

        return frontend or backend or ""

    def _detect_server_tech(self, blueprint: ProjectBlueprint) -> str:
        """Detect dev server technology from blueprint."""
        frontend = blueprint.tech_stack.frontend
        backend = blueprint.tech_stack.backend

        if frontend in ("react", "vue", "svelte"):
            return "vite"
        if frontend == "next":
            return "next"
        if backend == "fastapi":
            return "fastapi"
        if backend == "flask":
            return "flask"
        if backend == "express":
            return "express"

        return frontend or backend or ""

    def _determine_files_for_phase(
        self,
        phase_idx: int,
        description: str,
        blueprint: ProjectBlueprint,
    ) -> list[FileSpec]:
        """Map phase description → list of FileSpec to implement."""
        # Use blueprint's built-in method first
        files = blueprint.get_files_for_phase(phase_idx)
        if files:
            return files

        # Fallback: match description keywords against file_structure
        desc_lower = description.lower()
        matched_files: list[FileSpec] = []

        for file_path, file_info in blueprint.file_structure.items():
            file_desc = ""
            if isinstance(file_info, dict):
                file_desc = file_info.get("description", "").lower()
                language = file_info.get("language", "")
            else:
                language = ""

            # Keyword matching
            path_lower = file_path.lower()
            if (
                any(
                    kw in desc_lower
                    for kw in _extract_keywords(path_lower)
                )
                or any(
                    kw in desc_lower
                    for kw in _extract_keywords(file_desc)
                )
            ):
                # Avoid duplicates — skip if already written with real content
                existing = self._written_files.get(file_path, "")
                if existing and len(existing) > 50:
                    continue

                matched_files.append(FileSpec(
                    path=file_path,
                    description=file_desc,
                    language=language,
                ))

        return matched_files

    def _blueprint_to_scaffold_files(
        self, blueprint: ProjectBlueprint,
    ) -> list[FileSpec]:
        """Convert blueprint.file_structure to scaffold FileSpecs.

        Only includes files that have content hints (config files, etc.).
        Implementation files are left for phase_implement.
        """
        scaffold_files: list[FileSpec] = []
        config_extensions = {
            ".json", ".yml", ".yaml", ".toml", ".config.js",
            ".config.ts", ".css", ".html",
        }

        for file_path, file_info in blueprint.file_structure.items():
            is_config = any(file_path.endswith(ext) for ext in config_extensions)
            if not is_config:
                continue

            description = ""
            language = ""
            if isinstance(file_info, dict):
                description = file_info.get("description", "")
                language = file_info.get("language", "")

            scaffold_files.append(FileSpec(
                path=file_path,
                content="",  # Will be generated during implement
                description=description,
                language=language,
            ))

        return scaffold_files

    def _get_relevant_specs(
        self, file_spec: FileSpec, blueprint: ProjectBlueprint,
    ) -> str:
        """Extract relevant component/page/model specs for a file."""
        parts: list[str] = []
        path_lower = file_spec.path.lower()

        # Match components
        for comp in blueprint.components:
            if (
                comp.file_path.lower() == path_lower
                or comp.name.lower() in path_lower
            ):
                parts.append(
                    f"Component: {comp.name} (type={comp.type})\n"
                    f"  Description: {comp.description}\n"
                    f"  Props: {json.dumps(comp.props)}\n"
                    f"  State: {json.dumps(comp.state)}",
                )

        # Match pages
        for page in blueprint.pages:
            if page.name.lower().replace(" ", "_") in path_lower:
                parts.append(
                    f"Page: {page.name}\n"
                    f"  Route: {page.route}\n"
                    f"  Description: {page.description}\n"
                    f"  Components: {', '.join(page.components)}",
                )

        # Match data models
        for model in blueprint.data_models:
            if model.name.lower() in path_lower:
                parts.append(
                    f"Model: {model.name}\n"
                    f"  Fields: {json.dumps(model.fields)}\n"
                    f"  Relationships: {', '.join(model.relationships)}",
                )

        # Match API endpoints
        if "api" in path_lower or "route" in path_lower:
            for endpoint in blueprint.api_endpoints:
                parts.append(
                    f"Endpoint: {endpoint.method} {endpoint.path}\n"
                    f"  Description: {endpoint.description}",
                )

        return "\n\n".join(parts)

    async def _generate_fix_content(self, fix: FixPlan) -> str:
        """Generate file content for a create fix via LLM."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert developer. Generate COMPLETE, WORKING "
                    "code for the specified file. Return ONLY the code, no "
                    "markdown fences, no explanation."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Create file: {fix.file_path}\n"
                    f"Purpose: {fix.description}\n\n"
                    "Generate the complete file content."
                ),
            },
        ]

        result = await self.llm.chat(
            messages, temperature=0.3, max_tokens=4096,
        )
        code = result.get("content", "")
        return self._clean_code_output(code)

    def _build_project_context_summary(self) -> str:
        """Build summary of project state for fix planning."""
        parts: list[str] = []

        if self._blueprint:
            parts.append(f"Project: {self._blueprint.name}")
            parts.append(
                f"Tech: {self._blueprint.tech_stack.frontend} / "
                f"{self._blueprint.tech_stack.backend}",
            )

        parts.append(f"Files written: {len(self._written_files)}")
        for path in sorted(self._written_files.keys()):
            parts.append(f"  - {path}")

        return "\n".join(parts)

    @staticmethod
    def _parse_test_counts(test_result: TestResult, output: str) -> None:
        """Parse test counts from command output."""
        # Jest / Vitest pattern: Tests: 5 passed, 2 failed, 7 total
        jest_match = re.search(
            r"Tests?:\s*(\d+)\s*passed.*?(\d+)\s*failed.*?(\d+)\s*total",
            output,
            re.IGNORECASE,
        )
        if jest_match:
            test_result.passed = int(jest_match.group(1))
            test_result.failed = int(jest_match.group(2))
            test_result.total = int(jest_match.group(3))
            return

        # Pytest pattern: 5 passed, 2 failed
        pytest_match = re.search(
            r"(\d+)\s*passed", output, re.IGNORECASE,
        )
        pytest_fail = re.search(
            r"(\d+)\s*failed", output, re.IGNORECASE,
        )
        if pytest_match:
            test_result.passed = int(pytest_match.group(1))
        if pytest_fail:
            test_result.failed = int(pytest_fail.group(1))
        test_result.total = test_result.passed + test_result.failed

    # ================================================================
    # Helper methods — utilities
    # ================================================================

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit event via callback. Best-effort, never raises."""
        if self.event_callback:
            try:
                if asyncio.iscoroutinefunction(self.event_callback):
                    await self.event_callback(
                        {"event_type": event_type, **data},
                    )
                else:
                    self.event_callback({"event_type": event_type, **data})
            except Exception:
                pass  # Best-effort emission

    @staticmethod
    def _sanitize_project_name(name: str) -> str:
        """Sanitize project name for filesystem.

        Replace spaces and special chars → hyphens, lowercase, max 60 chars.
        """
        sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", name.lower())
        sanitized = re.sub(r"-+", "-", sanitized).strip("-")
        return sanitized[:60] or "my-project"

    @staticmethod
    def _parse_time(iso_str: str) -> float | None:
        """Parse ISO time string to timestamp."""
        if not iso_str:
            return None
        try:
            dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _clean_code_output(code: str) -> str:
        """Remove markdown fences from LLM output."""
        text = code.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first ``` line (may include language tag)
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Remove trailing ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines)
        return text

    def _read_project_file(self, rel_path: str) -> str:
        """Read file from project directory."""
        if self._project_path:
            path = self._project_path / rel_path
            if path.exists():
                try:
                    return path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
        return ""

    # ================================================================
    # Public accessors
    # ================================================================

    def get_progress(self) -> ProjectProgress:
        """Get current progress."""
        return self._progress

    def get_project_path(self) -> Path | None:
        """Get project directory path."""
        return self._project_path

    def get_file_tree(self) -> dict[str, Any]:
        """Get project file tree as nested dict."""
        if not self._project_path or not self._project_path.exists():
            return {}

        tree: dict[str, Any] = {}
        try:
            for path in sorted(self._project_path.rglob("*")):
                if path.is_file():
                    # Skip node_modules, __pycache__, .git
                    rel = path.relative_to(self._project_path)
                    parts = rel.parts
                    if any(
                        p in ("node_modules", "__pycache__", ".git", ".venv")
                        for p in parts
                    ):
                        continue

                    # Build nested dict
                    current = tree
                    for part in parts[:-1]:
                        current = current.setdefault(part, {})
                    current[parts[-1]] = path.stat().st_size
        except OSError:
            pass

        return tree


    # ================================================================
    # Integration verification
    # ================================================================

    async def verify_project_runnable(self, workspace_path: str) -> tuple[bool, list[str]]:
        """
        Verify that a project in workspace_path is runnable/deployable.

        Checks:
        - Entry point exists (app.py, main.py, index.js, manage.py, server.py, etc.)
        - Dependency manifest present (requirements.txt, package.json, pyproject.toml)
        - All Python imports resolvable (basic static analysis: scan `import X`,
          verify X exists as file or in requirements)
        - No empty files (size > 0)

        Args:
            workspace_path: Absolute or relative path to the project directory.

        Returns:
            Tuple of (runnable: bool, issues: list[str]).
            runnable is True only if zero critical issues found.
        """
        issues: list[str] = []
        project_path = Path(workspace_path)

        if not project_path.exists():
            return False, [f"Project path does not exist: {workspace_path}"]

        # --- Check 1: Entry point exists ---
        _ENTRY_POINTS = [
            "app.py", "main.py", "index.js", "index.ts", "server.py",
            "manage.py", "wsgi.py", "asgi.py", "run.py", "start.py",
            "src/index.js", "src/index.ts", "src/main.py", "src/app.py",
        ]
        entry_found = False
        for ep in _ENTRY_POINTS:
            if (project_path / ep).exists():
                entry_found = True
                break
        if not entry_found:
            issues.append(
                f"No entry point found. Expected one of: {', '.join(_ENTRY_POINTS[:6])}"
            )

        # --- Check 2: Dependency manifest present ---
        _DEP_FILES = [
            "requirements.txt", "package.json", "pyproject.toml",
            "Pipfile", "setup.py", "setup.cfg",
        ]
        dep_found = False
        for dep_file in _DEP_FILES:
            if (project_path / dep_file).exists():
                dep_found = True
                break
        if not dep_found:
            issues.append(
                f"No dependency manifest found. Expected one of: {', '.join(_DEP_FILES)}"
            )

        # --- Check 3: Python import analysis (basic static check) ---
        # Collect all Python files and their imports
        _stdlib_modules = {
            "os", "sys", "re", "json", "time", "datetime", "pathlib",
            "typing", "collections", "itertools", "functools", "math",
            "random", "hashlib", "uuid", "asyncio", "logging", "io",
            "copy", "abc", "enum", "dataclasses", "contextlib",
            "subprocess", "shutil", "tempfile", "glob", "string",
            "traceback", "threading", "multiprocessing", "socket",
            "http", "urllib", "base64", "hmac", "secrets", "struct",
            "pickle", "csv", "xml", "html", "email", "sqlite3",
        }

        # Get requirements if available
        _requirements_modules: set[str] = set()
        req_path = project_path / "requirements.txt"
        if req_path.exists():
            try:
                for line in req_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("-"):
                        # Extract package name (before ==, >=, etc.)
                        pkg_name = re.split(r"[>=<!~\[]", line)[0].strip()
                        if pkg_name:
                            # Normalize: replace - with _ for import matching
                            _requirements_modules.add(pkg_name.lower().replace("-", "_"))
            except OSError:
                pass

        # Get local Python modules (files in project)
        _local_modules: set[str] = set()
        for py_file in project_path.rglob("*.py"):
            try:
                rel = py_file.relative_to(project_path)
                # Module name from file path: foo/bar.py → foo.bar
                parts = list(rel.parts)
                if parts[-1] == "__init__.py":
                    parts = parts[:-1]
                else:
                    parts[-1] = parts[-1].replace(".py", "")
                if parts:
                    _local_modules.add(parts[0])  # Top-level package
            except (ValueError, IndexError):
                continue

        # Scan imports
        _unresolved_imports: list[str] = []
        for py_file in project_path.rglob("*.py"):
            # Skip venv/node_modules
            try:
                rel_parts = py_file.relative_to(project_path).parts
            except ValueError:
                continue
            if any(p in (".venv", "venv", "node_modules", "__pycache__") for p in rel_parts):
                continue

            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for line in content.splitlines():
                line_stripped = line.strip()
                # Match: import X or from X import Y
                import_match = re.match(
                    r"^(?:from\s+(\S+)\s+import|import\s+(\S+))", line_stripped
                )
                if not import_match:
                    continue
                module_name = (import_match.group(1) or import_match.group(2) or "").split(".")[0]
                if not module_name or module_name.startswith("_"):
                    continue

                # Check if resolvable
                module_lower = module_name.lower().replace("-", "_")
                if (
                    module_name in _stdlib_modules
                    or module_lower in _requirements_modules
                    or module_name in _local_modules
                    or module_lower in _local_modules
                ):
                    continue

                # Common aliases that don't match package name
                _KNOWN_ALIASES = {
                    "PIL": "pillow", "cv2": "opencv_python", "sklearn": "scikit_learn",
                    "yaml": "pyyaml", "bs4": "beautifulsoup4", "dotenv": "python_dotenv",
                    "jwt": "pyjwt", "gi": "pygobject", "attr": "attrs",
                }
                if module_name in _KNOWN_ALIASES:
                    alias_pkg = _KNOWN_ALIASES[module_name]
                    if alias_pkg in _requirements_modules:
                        continue

                _unresolved_imports.append(f"{module_name} (in {py_file.name})")

        # Deduplicate and limit
        _unique_unresolved = sorted(set(_unresolved_imports))
        if _unique_unresolved:
            issues.append(
                f"Unresolved imports ({len(_unique_unresolved)}): "
                + ", ".join(_unique_unresolved[:10])
            )

        # --- Check 4: No empty files ---
        _empty_files: list[str] = []
        _exclude_dirs = {"node_modules", ".venv", "venv", "__pycache__", ".git"}
        for item in project_path.rglob("*"):
            if item.is_dir():
                continue
            try:
                rel_parts = item.relative_to(project_path).parts
            except ValueError:
                continue
            if any(p in _exclude_dirs for p in rel_parts):
                continue
            if item.name == "__init__.py":
                continue  # __init__.py is often empty intentionally
            try:
                if item.stat().st_size == 0:
                    _empty_files.append(str(item.relative_to(project_path)))
            except OSError:
                continue

        if _empty_files:
            issues.append(
                f"Empty files ({len(_empty_files)}): "
                + ", ".join(_empty_files[:5])
            )

        runnable = len(issues) == 0
        return runnable, issues


# ================================================================
# Module-level helpers
# ================================================================


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from text for matching."""
    # Split on common separators
    words = re.split(r"[\s/\\_\-.]", text)
    # Filter short/common words
    stopwords = {
        "", "the", "a", "an", "and", "or", "for", "to", "in", "of",
        "src", "app", "index", "tsx", "ts", "js", "py", "css",
    }
    return [w.lower() for w in words if len(w) > 2 and w.lower() not in stopwords]
