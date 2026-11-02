"""
Nexus - Task Decomposer Service.

Phân tích task phức tạp thành các subtasks nhỏ hơn,
xác định dependencies và thứ tự thực thi.

Hỗ trợ tree search planning cho complex tasks (complexity > 7).
"""

import re
import uuid
from typing import Any, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.environment_details import detect_available_runtimes, format_runtime_constraints
from app.core.exceptions import TaskDecompositionError
from app.models.database import TaskModel
from app.services.llm_client import LLMClient, get_core_llm
from app.xskill.tree_search import PlanTree, TreeSearchPlanner, get_tree_search_planner

logger = structlog.get_logger(__name__)

# ── Agent count caps by task complexity ──
MAX_AGENTS_BY_COMPLEXITY: dict[str, int] = {
    "simple": 3,
    "medium": 5,
    "complex": 8,
}

# ── Technology references for environment detection (informational only) ──
# NOTE: These match exact tool/framework names (fixed identifiers, not semantic).
# This is acceptable — "docker" always means Docker, "npm" always means npm.
_TECH_PATTERNS: dict[str, dict[str, Any]] = {
    "node_js": {
        "keywords": ["next.js", "nextjs", "react", "vue", "angular", "npm", "yarn", "node", "express", "nuxt"],
        "runtime_key": "node",
        "install_cmd": "Install Node.js: apt-get install nodejs npm, or nvm install node, or download from nodejs.org",
    },
    "docker": {
        "keywords": ["docker", "container", "dockerfile", "docker-compose"],
        "runtime_key": "docker",
        "install_cmd": "Install Docker: curl -fsSL https://get.docker.com | sh, or download Docker Desktop",
    },
}


def _update_brief_for_constraints(original_brief: str, runtimes: dict[str, Any]) -> str:
    """Generate installation notice if task references tools not yet on PATH.

    IMPORTANT: This does NOT replace or downgrade the tech stack.
    It only adds a notice that installation is needed as a first step.

    Args:
        original_brief: Original task description text.
        runtimes: Dict from detect_available_runtimes() with runtime availability.

    Returns:
        Notice string with installation instructions, or empty string if all tools available.
    """
    brief_lower = original_brief.lower()
    install_notices: list[str] = []

    # Check Node.js-dependent tech
    node_available = bool(runtimes.get("node"))
    if not node_available:
        node_keywords = _TECH_PATTERNS["node_js"]["keywords"]
        found_node_refs = [kw for kw in node_keywords if kw in brief_lower]
        if found_node_refs:
            install_notices.append(
                f"Task uses {', '.join(found_node_refs)} — {_TECH_PATTERNS['node_js']['install_cmd']}"
            )

    # Check Docker
    docker_available = bool(runtimes.get("docker"))
    if not docker_available:
        docker_keywords = _TECH_PATTERNS["docker"]["keywords"]
        found_docker_refs = [kw for kw in docker_keywords if kw in brief_lower]
        if found_docker_refs:
            install_notices.append(
                f"Task uses {', '.join(found_docker_refs)} — {_TECH_PATTERNS['docker']['install_cmd']}"
            )

    if not install_notices:
        return ""

    return (
        "\n\nINSTALLATION REQUIRED (add as first subtask): "
        + "; ".join(install_notices)
        + "\nDo NOT downgrade the tech stack \u2014 install the required tools instead."
    )


def estimate_task_complexity(task_description: str) -> str:
    """Estimate task complexity using structural heuristics (no keyword matching).

    This is a FAST sync estimate for agent-cap decisions before LLM is called.
    For accurate complexity analysis, use TaskDecomposer.analyze_complexity() (LLM-based).

    Structural signals (language-agnostic):
    - Message length correlates with task complexity
    - Number of distinct sentences/clauses
    - Presence of structured lists (numbered steps, bullets)
    - Code blocks or technical formatting

    Returns:
        One of: "simple", "medium", "complex"
    """
    text = task_description.strip()
    if not text:
        return "simple"

    # Structural signal: total length
    char_count = len(text)

    # Structural signal: number of lines/sentences (more = more complex)
    line_count = text.count("\n") + 1
    sentence_count = text.count(".") + text.count("?") + text.count("!") + text.count("\n")

    # Structural signal: numbered lists or bullet points (indicates multi-step)
    has_list = bool(re.search(r"(?m)^\s*[\d]+[.)]\s|^\s*[-*•]\s", text))

    # Structural signal: code blocks
    has_code = "```" in text or "`" in text

    # Structural signal: clause density (commas, conjunctions, "with", "and")
    # More clauses = more requirements packed into one sentence
    clause_separators = text.count(",") + text.count(" and ") + text.count(" with ") + text.count(" or ")

    # Decision based on structural complexity
    if char_count > 400 or line_count > 6 or (has_list and line_count > 4):
        return "complex"
    elif char_count < 60 and line_count <= 2 and not has_list and not has_code and clause_separators <= 1:
        return "simple"
    else:
        return "medium"


class TaskDecomposer:
    """
    Phân tích task thành subtasks có thể thực thi.

    Sử dụng Core LLM để hiểu task và chia nhỏ thành
    các bước có thể giao cho các agent khác nhau.
    """

    def __init__(self, db: AsyncSession, llm: Optional[LLMClient] = None):
        self.db = db
        self.llm = llm or get_core_llm()

    async def decompose(
        self,
        task: TaskModel,
    ) -> list[TaskModel]:
        """
        Decompose a task into subtasks.

        Returns list of subtasks with proper ordering and dependencies.
        """
        # Estimate complexity and determine agent cap
        task_text = f"{task.title} {task.description or ''}"
        complexity = estimate_task_complexity(task_text)
        max_agents = MAX_AGENTS_BY_COMPLEXITY[complexity]

        logger.info(
            "task_complexity_estimated",
            task_id=str(task.id),
            complexity=complexity,
            max_agents=max_agents,
        )

        # Detect available runtimes before planning
        runtimes = await detect_available_runtimes()
        runtime_constraints = format_runtime_constraints(runtimes)

        # Auto-update brief if original task references unavailable tools
        task_text = f"{task.title} {task.description or ''}"
        updated_brief_notice = _update_brief_for_constraints(task_text, runtimes)
        if updated_brief_notice:
            logger.info(
                "task_brief_auto_updated",
                task_id=str(task.id),
                notice="Original plan referenced unavailable tools, adapted to environment",
            )
            runtime_constraints = runtime_constraints + updated_brief_notice

        decompose_prompt = [
            {
                "role": "system",
                "content": (
                    "You are a Task Decomposition Expert. Given a task, break it down into "
                    "small, actionable subtasks. Return JSON with:\n"
                    "- subtasks: list of subtask objects, each with:\n"
                    "  - title: clear, actionable title\n"
                    "  - description: detailed description of what needs to be done\n"
                    "  - agent_role: recommended agent role (researcher/developer/analyst/"
                    "writer/coordinator)\n"
                    "  - priority: 1-10 (higher = more important)\n"
                    "  - depends_on: list of subtask indices (0-based) this depends on\n"
                    "  - estimated_complexity: simple|medium|complex\n"
                    "- is_simple: boolean - true if task doesn't need decomposition\n"
                    "- reasoning: why you decomposed it this way\n"
                    "\nRules:\n"
                    "- Keep subtasks focused and single-responsibility\n"
                    "- Minimize dependencies between subtasks for parallelism\n"
                    "- If task is simple (single step), set is_simple=true and return "
                    "one subtask\n"
                    "- Use as many subtasks as needed for completeness (typically 5-15 for complex tasks)\n"
                    "- For BUILDING tasks (websites, apps, systems): include ALL necessary components — "
                    "do NOT artificially limit scope. Completeness > brevity.\n"
                    "- A 'complete website' needs: setup, frontend pages, backend API, database, auth, "
                    "styling, testing, deployment — decompose ALL of these\n\n"
                    f"{runtime_constraints}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task Title: {task.title}\n"
                    f"Task Description: {task.description or 'No additional description'}\n"
                    f"Priority: {task.priority}\n\n"
                    f"CONSTRAINT: Maximum {max_agents} agents allowed for this "
                    f"task complexity level ({complexity})."
                ),
            },
        ]

        try:
            result = await self.llm.chat_json(decompose_prompt, temperature=0.3)
        except Exception as e:
            logger.error("task_decomposition_failed", task_id=str(task.id), error=str(e))
            raise TaskDecompositionError(f"Failed to decompose task: {e}")

        if result.get("is_simple", False):
            logger.info("task_is_simple", task_id=str(task.id))
            return [task]

        subtasks: list[TaskModel] = []
        for idx, st_def in enumerate(result.get("subtasks", [])):
            subtask = TaskModel(
                title=st_def.get("title", f"Subtask {idx + 1}"),
                description=st_def.get("description"),
                status="pending",
                priority=st_def.get("priority", task.priority),
                user_id=task.user_id,
                parent_task_id=task.id,
                metadata_={
                    "agent_role": st_def.get("agent_role", "generalist"),
                    "depends_on_indices": st_def.get("depends_on", []),
                    "estimated_complexity": st_def.get("estimated_complexity", "medium"),
                    "order": idx,
                },
            )
            self.db.add(subtask)
            subtasks.append(subtask)

        await self.db.flush()

        # Update parent task status
        task.status = "decomposed"
        task.metadata_ = {
            **(task.metadata_ or {}),
            "subtask_count": len(subtasks),
            "decomposition_reasoning": result.get("reasoning", ""),
            "complexity": complexity,
            "max_agents": max_agents,
        }

        logger.info(
            "task_decomposed",
            task_id=str(task.id),
            subtask_count=len(subtasks),
        )
        return subtasks

    async def decompose_with_tree_search(
        self,
        task: str,
        context: str = "",
        max_depth: int = 3,
        branching_factor: int = 3,
        beam_width: int = 2,
    ) -> dict[str, Any]:
        """
        Dùng TreeSearchPlanner cho complex tasks.

        Thay vì greedy decomposition, explore nhiều plan paths
        song song và chọn optimal path.

        Args:
            task: Mô tả task cần plan.
            context: Thông tin bổ sung.
            max_depth: Độ sâu tối đa của tree.
            branching_factor: Số lượng children mỗi node.
            beam_width: Số node giữ lại mỗi depth sau pruning.

        Returns:
            Dict chứa: tree (serialized), best_path, steps (executable list).
        """
        planner = TreeSearchPlanner(
            llm=self.llm,
            max_depth=max_depth,
            branching_factor=branching_factor,
            beam_width=beam_width,
        )

        # Detect available runtimes and prepend to context
        runtimes = await detect_available_runtimes()
        runtime_constraints = format_runtime_constraints(runtimes)
        context = f"{runtime_constraints}\n\n{context}" if context else runtime_constraints

        logger.info(
            "decompose_with_tree_search",
            task=task[:100],
            max_depth=max_depth,
            branching_factor=branching_factor,
            beam_width=beam_width,
        )

        tree = await planner.plan(task, context)
        steps = planner.plan_to_steps(tree)
        best_path_nodes = [
            tree.nodes[nid].to_dict()
            for nid in tree.best_path
            if nid in tree.nodes
        ]

        return {
            "tree": tree.to_dict(),
            "best_path": best_path_nodes,
            "steps": steps,
            "markdown": tree.to_markdown(),
        }

    async def analyze_complexity(self, task_description: str) -> dict[str, Any]:
        """
        Phân tích độ phức tạp của task mà không decompose.

        Nếu estimated_steps > 7, tự động suggest tree search planning.
        """
        prompt = [
            {
                "role": "system",
                "content": (
                    "Analyze the complexity of a task. Return JSON with:\n"
                    "- complexity: simple|medium|complex|expert\n"
                    "- complexity_score: integer 1-10\n"
                    "- estimated_agents: number of agents needed\n"
                    "- estimated_steps: number of steps\n"
                    "- needs_tools: list of tool categories needed\n"
                    "- reasoning: brief analysis"
                ),
            },
            {"role": "user", "content": task_description},
        ]
        result = await self.llm.chat_json(prompt, temperature=0.2)

        # Suggest tree search for high-complexity tasks
        complexity_score = result.get("complexity_score", 0)
        estimated_steps = result.get("estimated_steps", 0)
        if complexity_score > 7 or estimated_steps > 7:
            result["suggest_tree_search"] = True
            result["tree_search_reason"] = (
                f"Task complexity score ({complexity_score}/10) or estimated steps "
                f"({estimated_steps}) exceeds threshold. Tree search planning "
                "recommended for exploring multiple approaches."
            )
        else:
            result["suggest_tree_search"] = False

        return result
