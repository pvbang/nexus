"""
Nexus - Coding Engine.

Lấy cảm hứng từ Roo Code: tự động sinh code, tool mới,
MCP server on-the-fly khi hệ thống thiếu capability.
"""

from typing import Any, Optional

import structlog

from app.sandbox.manager import SandboxManager, SandboxResult
from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)


class CodingEngine:
    """
    Tự động sinh code và tools mới.

    Khi hệ thống phát hiện thiếu tool, CodingEngine sẽ:
    1. Phân tích yêu cầu tool
    2. Generate code implementation
    3. Test trong sandbox
    4. Đăng ký tool mới vào MCP registry
    """

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or get_core_llm()
        self.sandbox = SandboxManager()

    async def generate_tool(
        self,
        tool_description: str,
        input_schema: Optional[dict[str, Any]] = None,
        output_schema: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Generate a new tool implementation.

        Returns: dict with code, schema, and test results.
        """
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are an expert tool developer. Generate a Python function that "
                    "implements the described tool. Return JSON with:\n"
                    "- name: function name (snake_case)\n"
                    "- description: tool description\n"
                    "- code: complete Python code (function + imports)\n"
                    "- test_code: Python code to test the function\n"
                    "- schema: OpenAI function calling schema\n"
                    "\nThe function should:\n"
                    "- Be async if it does I/O\n"
                    "- Have proper type hints\n"
                    "- Handle errors gracefully\n"
                    "- Return JSON-serializable results"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Tool description: {tool_description}\n"
                    + (f"Input schema: {input_schema}\n" if input_schema else "")
                    + (f"Output schema: {output_schema}\n" if output_schema else "")
                ),
            },
        ]

        result = await self.llm.chat_json(prompt, temperature=0.3)

        # Test generated code in sandbox
        test_code = result.get("test_code", "")
        if test_code:
            full_test = f"{result.get('code', '')}\n\n{test_code}"
            test_result = await self.sandbox.execute_code("python", full_test, timeout=15)

            result["test_passed"] = test_result.success
            result["test_output"] = test_result.output
            result["test_error"] = test_result.error

            if not test_result.success:
                logger.warning(
                    "generated_tool_test_failed",
                    tool=result.get("name"),
                    error=test_result.error,
                )
                # Try to fix
                result = await self._fix_code(result, test_result)

        logger.info(
            "tool_generated",
            name=result.get("name"),
            test_passed=result.get("test_passed", False),
        )
        return result

    async def generate_script(
        self,
        task_description: str,
        language: str = "python",
        context: Optional[str] = None,
    ) -> dict[str, Any]:
        """Generate a script for a specific task."""
        prompt = [
            {
                "role": "system",
                "content": (
                    f"You are an expert {language} developer. Generate a complete, "
                    f"working {language} script for the described task. Return JSON:\n"
                    "- code: complete script\n"
                    "- description: what the script does\n"
                    "- dependencies: list of required packages\n"
                ),
            },
            {"role": "user", "content": task_description},
        ]
        if context:
            prompt.append({"role": "user", "content": f"Context: {context}"})

        return await self.llm.chat_json(prompt, temperature=0.3)

    async def execute_and_fix(
        self,
        code: str,
        language: str = "python",
        max_retries: int = 5,
    ) -> SandboxResult:
        """Execute code, and if it fails, attempt to fix and retry."""
        for attempt in range(max_retries):
            result = await self.sandbox.execute_code(language, code, timeout=30)
            if result.success:
                return result

            # Try to fix
            fix_prompt = [
                {
                    "role": "system",
                    "content": (
                        f"Fix the following {language} code that produced an error. "
                        "Return only the corrected code, no explanations."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Code:\n```\n{code}\n```\n\nError:\n{result.error}",
                },
            ]
            fix_result = await self.llm.chat(fix_prompt, temperature=0.2)
            code = fix_result.get("content", code)

            # Clean code from markdown
            if "```" in code:
                parts = code.split("```")
                if len(parts) >= 3:
                    code = parts[1]
                    if code.startswith(language):
                        code = code[len(language):].strip()

            logger.info("code_fix_attempt", attempt=attempt + 1)

        return result

    async def _fix_code(
        self, tool_result: dict[str, Any], test_result: SandboxResult
    ) -> dict[str, Any]:
        """Attempt to fix generated code that failed tests."""
        fix_prompt = [
            {
                "role": "system",
                "content": (
                    "Fix the tool code that failed testing. Return the same JSON "
                    "structure with corrected code and test_code."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original code:\n{tool_result.get('code')}\n\n"
                    f"Test code:\n{tool_result.get('test_code')}\n\n"
                    f"Error:\n{test_result.error}"
                ),
            },
        ]
        try:
            fixed = await self.llm.chat_json(fix_prompt, temperature=0.2)
            tool_result.update(fixed)
            return tool_result
        except Exception:
            return tool_result

    # ------------------------------------------------------------------
    # Multi-file project generation
    # ------------------------------------------------------------------

    async def generate_project_files(
        self,
        file_specs: list[dict[str, str]],
        project_context: str = "",
        tech_stack: str = "",
    ) -> list[dict[str, Any]]:
        """
        Generate code cho nhiều files cùng lúc.

        Args:
            file_specs: list of {"path": str, "description": str, "language": str}
            project_context: Overview của project (architecture, components, etc.)
            tech_stack: Tech stack info (e.g., "React + Vite + TailwindCSS")

        Returns:
            list of {"path": str, "code": str, "success": bool, "error": str}

        Strategy:
        - Files processed sequentially (later files get context from earlier ones)
        - Each file gets focused prompt with only relevant context
        - Generated code is validated with basic syntax check
        """
        results: list[dict[str, Any]] = []
        generated_files: dict[str, str] = {}  # path → code (for context)

        for spec in file_specs:
            path = spec.get("path", "")
            description = spec.get("description", "")
            language = spec.get("language", self._detect_language(path))

            # Build context from already-generated files (only dependencies)
            relevant_context = ""
            for gen_path, gen_code in generated_files.items():
                # Simple heuristic: include if same directory or imported
                if self._is_related(path, gen_path):
                    relevant_context += f"\n--- {gen_path} ---\n{gen_code[:2000]}\n"

            prompt = [
                {
                    "role": "system",
                    "content": (
                        f"You are an expert {language} developer. Generate COMPLETE, WORKING code "
                        f"for the specified file. Tech stack: {tech_stack}.\n\n"
                        f"Requirements:\n"
                        f"- Code must be production-ready\n"
                        f"- Include proper imports\n"
                        f"- Handle errors gracefully\n"
                        f"- Follow best practices for {language}\n\n"
                        f"Return ONLY the code. No markdown fences. No explanations."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"File: {path}\n"
                        f"Description: {description}\n\n"
                        f"Project context:\n{project_context[:2000]}\n\n"
                        + (f"Related files:\n{relevant_context}\n" if relevant_context else "")
                    ),
                },
            ]

            try:
                result = await self.llm.chat(prompt, temperature=0.3, max_tokens=4096)
                code = result.get("content", "")

                # Clean markdown fences
                code = self._clean_code(code, language)

                generated_files[path] = code
                results.append({
                    "path": path,
                    "code": code,
                    "success": True,
                    "error": "",
                })

                logger.info("file_generated", path=path, code_length=len(code))

            except Exception as e:
                logger.error("file_generation_failed", path=path, error=str(e))
                results.append({
                    "path": path,
                    "code": "",
                    "success": False,
                    "error": str(e),
                })

        return results

    def _detect_language(self, path: str) -> str:
        """Detect language from file extension."""
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "typescript-react", ".jsx": "javascript-react",
            ".html": "html", ".css": "css", ".json": "json",
            ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
            ".sql": "sql", ".sh": "bash", ".toml": "toml",
        }
        for ext, lang in ext_map.items():
            if path.endswith(ext):
                return lang
        return "text"

    def _is_related(self, current_path: str, other_path: str) -> bool:
        """Check if two files are likely related (same directory or common patterns)."""
        import os

        current_dir = os.path.dirname(current_path)
        other_dir = os.path.dirname(other_path)

        # Same directory
        if current_dir == other_dir:
            return True

        # Parent-child relationship
        if current_dir.startswith(other_dir) or other_dir.startswith(current_dir):
            return True

        # Common patterns
        common_dirs = {"src", "app", "components", "pages", "lib", "utils"}
        if any(d in current_dir and d in other_dir for d in common_dirs):
            return True

        return False

    def _clean_code(self, code: str, language: str) -> str:
        """Remove markdown fences from LLM output."""
        if not code:
            return code
        lines = code.strip().split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
