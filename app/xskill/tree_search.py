"""
Nexus - Tree Search Planning Engine.

Port từ XSkill eval/search/tree.py pattern, adapt cho multi-step planning.

Core concept: Khi gặp complex task, thay vì greedy execution (1 path),
explore nhiều approaches song song và chọn path tốt nhất thông qua
beam search với LLM-as-judge scoring.

Architecture:
- PlanNode: Đại diện một bước trong plan tree
- PlanTree: Container quản lý toàn bộ tree + traversal/pruning
- TreeSearchPlanner: Async engine dùng LLM để expand/evaluate/select
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

import structlog

from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# PlanNode
# ---------------------------------------------------------------------------


@dataclass
class PlanNode:
    """
    Một node trong plan tree, đại diện cho một action/step.

    Inspired by XSkill SearchNode nhưng đơn giản hoá cho planning use case.
    """

    node_id: str = field(default_factory=lambda: str(uuid4()))
    parent_id: Optional[str] = None
    depth: int = 0
    action: str = ""
    reasoning: str = ""
    children: list[str] = field(default_factory=list)
    score: float = 0.0
    status: str = "pending"  # pending | expanding | evaluated | pruned | selected
    result: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize node thành dict."""
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "action": self.action,
            "reasoning": self.reasoning,
            "children": list(self.children),
            "score": self.score,
            "status": self.status,
            "result": self.result,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def __repr__(self) -> str:
        status_icon = {
            "pending": "⏳",
            "expanding": "🔄",
            "evaluated": "✅",
            "pruned": "✂️",
            "selected": "⭐",
        }.get(self.status, "?")
        return (
            f"PlanNode({status_icon} id={self.node_id[:8]}… "
            f"depth={self.depth} score={self.score:.2f} "
            f"action={self.action[:40]!r})"
        )


# ---------------------------------------------------------------------------
# PlanTree
# ---------------------------------------------------------------------------


class PlanTree:
    """
    Container cho toàn bộ plan tree.

    Quản lý nodes, traversal, pruning và selection.
    Inspired by XSkill tree structure nhưng dùng flat dict thay vì
    parent/children references để dễ serialize.
    """

    def __init__(self, root: PlanNode) -> None:
        self.root = root
        self.nodes: dict[str, PlanNode] = {root.node_id: root}
        self.best_path: list[str] = []
        self._task: str = ""
        self._context: str = ""

    # -- Mutation --------------------------------------------------------

    def add_node(
        self,
        parent_id: str,
        action: str,
        reasoning: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> PlanNode:
        """Thêm child node cho *parent_id*. Trả về node mới."""
        parent = self.nodes.get(parent_id)
        if parent is None:
            raise KeyError(f"Parent node {parent_id!r} not found in tree")

        child = PlanNode(
            parent_id=parent_id,
            depth=parent.depth + 1,
            action=action,
            reasoning=reasoning,
            metadata=metadata or {},
        )
        parent.children.append(child.node_id)
        self.nodes[child.node_id] = child
        return child

    # -- Query -----------------------------------------------------------

    def get_node(self, node_id: str) -> PlanNode:
        """Lấy node theo ID, raise KeyError nếu không tìm thấy."""
        node = self.nodes.get(node_id)
        if node is None:
            raise KeyError(f"Node {node_id!r} not found")
        return node

    def get_children(self, node_id: str) -> list[PlanNode]:
        """Trả về danh sách children của một node."""
        node = self.get_node(node_id)
        return [self.nodes[cid] for cid in node.children if cid in self.nodes]

    def get_path_to_node(self, node_id: str) -> list[PlanNode]:
        """Trả về path từ root đến node (inclusive)."""
        path: list[PlanNode] = []
        current_id: Optional[str] = node_id
        while current_id is not None:
            node = self.nodes.get(current_id)
            if node is None:
                break
            path.append(node)
            current_id = node.parent_id
        return list(reversed(path))

    def get_leaf_nodes(self) -> list[PlanNode]:
        """Trả về tất cả leaf nodes (không có children hoặc children đều pruned)."""
        leaves: list[PlanNode] = []
        for node in self.nodes.values():
            if node.status == "pruned":
                continue
            active_children = [
                cid
                for cid in node.children
                if cid in self.nodes and self.nodes[cid].status != "pruned"
            ]
            if not active_children:
                leaves.append(node)
        return leaves

    def get_nodes_at_depth(self, depth: int) -> list[PlanNode]:
        """Trả về tất cả nodes tại một depth nhất định (trừ pruned)."""
        return [
            n
            for n in self.nodes.values()
            if n.depth == depth and n.status != "pruned"
        ]

    # -- Pruning ---------------------------------------------------------

    def prune(self, node_id: str) -> None:
        """Mark subtree rooted tại *node_id* là pruned (recursive)."""
        node = self.nodes.get(node_id)
        if node is None:
            return
        node.status = "pruned"
        for child_id in node.children:
            self.prune(child_id)

    # -- Selection -------------------------------------------------------

    def select_best_path(self) -> list[PlanNode]:
        """
        Walk tree từ root, mỗi depth chọn child có score cao nhất.

        Trả về list[PlanNode] từ root đến leaf đã chọn.
        """
        path: list[PlanNode] = [self.root]
        current = self.root

        while True:
            children = [
                self.nodes[cid]
                for cid in current.children
                if cid in self.nodes and self.nodes[cid].status != "pruned"
            ]
            if not children:
                break
            best_child = max(children, key=lambda n: n.score)
            best_child.status = "selected"
            path.append(best_child)
            current = best_child

        self.best_path = [n.node_id for n in path]
        return path

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize toàn bộ tree thành dict."""
        return {
            "root_id": self.root.node_id,
            "best_path": list(self.best_path),
            "task": self._task,
            "context": self._context,
            "total_nodes": len(self.nodes),
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
        }

    def to_markdown(self) -> str:
        """Visualize tree as readable markdown."""
        lines: list[str] = [
            "# 🌳 Plan Tree",
            "",
            f"**Task:** {self._task}",
        ]
        if self._context:
            lines.append(f"**Context:** {self._context}")
        lines.append(f"**Total nodes:** {len(self.nodes)}")
        if self.best_path:
            lines.append(f"**Best path length:** {len(self.best_path)}")
        lines.append("")

        # Recursive render
        def _render(node_id: str, indent: int = 0) -> None:
            node = self.nodes.get(node_id)
            if node is None:
                return
            prefix = "  " * indent
            status_icon = {
                "pending": "⏳",
                "expanding": "🔄",
                "evaluated": "✅",
                "pruned": "✂️",
                "selected": "⭐",
            }.get(node.status, "❓")
            is_on_best = node.node_id in self.best_path
            marker = " **← BEST**" if is_on_best else ""
            lines.append(
                f"{prefix}- {status_icon} **{node.action or '(root)'}** "
                f"(score: {node.score:.2f}){marker}"
            )
            if node.reasoning:
                lines.append(f"{prefix}  _Reasoning: {node.reasoning}_")
            for cid in node.children:
                _render(cid, indent + 1)

        _render(self.root.node_id)
        lines.append("")

        # Best path summary
        if self.best_path:
            lines.append("## ⭐ Best Path")
            lines.append("")
            for idx, nid in enumerate(self.best_path):
                node = self.nodes.get(nid)
                if node is None:
                    continue
                action_label = node.action or "(root)"
                lines.append(
                    f"{idx}. **{action_label}** — score {node.score:.2f}"
                )
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TreeSearchPlanner
# ---------------------------------------------------------------------------


class TreeSearchPlanner:
    """
    Tree Search Planning Engine.

    Pipeline:
    1. Generate root node từ task description
    2. Expand: cho mỗi leaf, generate *branching_factor* children (LLM call)
    3. Evaluate: score mỗi node (LLM-as-judge)
    4. Prune: keep top *beam_width* nodes per depth
    5. Repeat until *max_depth*
    6. Select best path

    Inspired by XSkill SearchNode tree structure + beam search.
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        max_depth: int = 3,
        branching_factor: int = 3,
        beam_width: int = 2,
    ) -> None:
        self.llm = llm
        self.max_depth = max_depth
        self.branching_factor = branching_factor
        self.beam_width = beam_width

    def _get_llm(self) -> LLMClient:
        """Lazy-init LLM client."""
        if self.llm is None:
            self.llm = get_core_llm()
        return self.llm

    # -- Main pipeline ---------------------------------------------------

    async def plan(self, task: str, context: str = "") -> PlanTree:
        """
        Full planning pipeline: expand → evaluate → prune → repeat → select.

        Args:
            task: Mô tả task cần plan.
            context: Thông tin bổ sung (domain, constraints, …).

        Returns:
            PlanTree đã có best_path selected.
        """
        logger.info(
            "tree_search_start",
            task=task[:100],
            max_depth=self.max_depth,
            branching_factor=self.branching_factor,
            beam_width=self.beam_width,
        )

        # 1. Create root
        root = PlanNode(
            depth=0,
            action=task,
            reasoning="Root task node",
            status="evaluated",
            score=1.0,
        )
        tree = PlanTree(root)
        tree._task = task
        tree._context = context

        # 2. Iterative expand → evaluate → prune
        for depth in range(1, self.max_depth + 1):
            leaves = tree.get_leaf_nodes()
            if not leaves:
                logger.warning("tree_search_no_leaves", depth=depth)
                break

            # Expand all leaves in parallel
            expand_tasks = [
                self.expand_node(tree, leaf.node_id, task, context)
                for leaf in leaves
            ]
            expand_results = await asyncio.gather(*expand_tasks, return_exceptions=True)

            # Collect newly created children
            new_children: list[PlanNode] = []
            for result in expand_results:
                if isinstance(result, Exception):
                    logger.error("tree_search_expand_error", error=str(result))
                    continue
                new_children.extend(result)

            if not new_children:
                logger.info("tree_search_no_new_children", depth=depth)
                break

            # Evaluate all new children in parallel
            eval_tasks = [
                self.evaluate_node(tree, child.node_id, task)
                for child in new_children
            ]
            eval_results = await asyncio.gather(*eval_tasks, return_exceptions=True)

            for child, result in zip(new_children, eval_results):
                if isinstance(result, Exception):
                    logger.error(
                        "tree_search_eval_error",
                        node_id=child.node_id[:8],
                        error=str(result),
                    )
                    child.score = 0.0
                else:
                    child.score = result
                child.status = "evaluated"

            # Prune: keep top beam_width nodes per depth
            self._prune_beam(tree, depth)

            logger.info(
                "tree_search_depth_done",
                depth=depth,
                new_nodes=len(new_children),
                active_at_depth=len(tree.get_nodes_at_depth(depth)),
            )

        # 3. Select best path
        best_path = tree.select_best_path()
        tree.best_path = [n.node_id for n in best_path]

        logger.info(
            "tree_search_done",
            total_nodes=len(tree.nodes),
            best_path_length=len(best_path),
            best_path_score=sum(n.score for n in best_path) / max(len(best_path), 1),
        )

        return tree

    # -- Expand ----------------------------------------------------------

    async def expand_node(
        self,
        tree: PlanTree,
        node_id: str,
        task: str,
        context: str = "",
    ) -> list[PlanNode]:
        """
        Generate *branching_factor* children cho một node bằng LLM.

        Returns danh sách child PlanNode đã add vào tree.
        """
        node = tree.get_node(node_id)
        node.status = "expanding"

        # Build path context
        path = tree.get_path_to_node(node_id)
        path_actions = " → ".join(n.action for n in path if n.action)

        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a strategic planning expert. Given a task and the current "
                    "execution path, propose exactly {bf} different NEXT actions to take.\n"
                    "Each action should be a distinct approach or step.\n\n"
                    "Return JSON:\n"
                    '{{"actions": [\n'
                    '  {{"action": "concise description of what to do", '
                    '"reasoning": "why this is a good next step"}},\n'
                    "  ...\n"
                    "]}}\n\n"
                    "Rules:\n"
                    "- Each action must be specific and actionable\n"
                    "- Actions should be diverse (different approaches)\n"
                    "- Consider feasibility, efficiency, and progress toward the goal\n"
                    "- Do NOT repeat actions already in the path"
                ).format(bf=self.branching_factor),
            },
            {
                "role": "user",
                "content": (
                    f"Task: {task}\n"
                    f"Context: {context or 'None'}\n"
                    f"Current path: {path_actions}\n"
                    f"Current step: {node.action}\n"
                    f"Depth: {node.depth}/{self.max_depth}\n\n"
                    f"Propose {self.branching_factor} different next actions."
                ),
            },
        ]

        llm = self._get_llm()
        try:
            result = await llm.chat_json(prompt, temperature=0.7)
        except Exception as e:
            logger.error("tree_search_expand_llm_error", node_id=node_id[:8], error=str(e))
            node.status = "evaluated"
            return []

        actions = result.get("actions", [])
        children: list[PlanNode] = []
        for act in actions[: self.branching_factor]:
            child = tree.add_node(
                parent_id=node_id,
                action=act.get("action", "unknown action"),
                reasoning=act.get("reasoning", ""),
            )
            children.append(child)

        node.status = "evaluated"
        return children

    # -- Evaluate --------------------------------------------------------

    async def evaluate_node(
        self,
        tree: PlanTree,
        node_id: str,
        task: str,
    ) -> float:
        """
        Score một node (0-1) bằng LLM-as-judge.

        Evaluates: feasibility, progress toward goal, efficiency.
        """
        node = tree.get_node(node_id)
        path = tree.get_path_to_node(node_id)
        path_actions = " → ".join(n.action for n in path if n.action)

        prompt = [
            {
                "role": "system",
                "content": (
                    "You are an expert plan evaluator. Score a plan step on a scale "
                    "of 0.0 to 1.0.\n\n"
                    "Return JSON:\n"
                    '{"score": 0.85, "analysis": "brief explanation of the score"}\n\n'
                    "Scoring criteria:\n"
                    "- Feasibility (0.3 weight): Can this step actually be executed?\n"
                    "- Progress (0.4 weight): Does this move meaningfully toward the goal?\n"
                    "- Efficiency (0.3 weight): Is this the most direct/efficient approach?\n\n"
                    "Return a single float between 0.0 and 1.0."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task: {task}\n"
                    f"Path so far: {path_actions}\n"
                    f"Current step to evaluate: {node.action}\n"
                    f"Reasoning: {node.reasoning}\n"
                    f"Depth: {node.depth}"
                ),
            },
        ]

        llm = self._get_llm()
        try:
            result = await llm.chat_json(prompt, temperature=0.1)
            raw_score = result.get("score", 0.5)
            score = max(0.0, min(1.0, float(raw_score)))
            node.metadata["evaluation_analysis"] = result.get("analysis", "")
            return score
        except Exception as e:
            logger.error("tree_search_eval_llm_error", node_id=node_id[:8], error=str(e))
            return 0.5  # Neutral score on failure

    # -- Prune -----------------------------------------------------------

    def _prune_beam(self, tree: PlanTree, depth: int) -> None:
        """Keep top *beam_width* nodes tại *depth*, prune phần còn lại."""
        nodes_at_depth = tree.get_nodes_at_depth(depth)
        if len(nodes_at_depth) <= self.beam_width:
            return

        # Sort by score descending
        sorted_nodes = sorted(nodes_at_depth, key=lambda n: n.score, reverse=True)
        keep = set(n.node_id for n in sorted_nodes[: self.beam_width])

        for node in sorted_nodes:
            if node.node_id not in keep:
                tree.prune(node.node_id)

        logger.debug(
            "tree_search_pruned",
            depth=depth,
            kept=len(keep),
            pruned=len(nodes_at_depth) - len(keep),
        )

    # -- Post-processing -------------------------------------------------

    def select_best_path(self, tree: PlanTree) -> list[PlanNode]:
        """Wrapper: Walk tree chọn highest-scored children tại mỗi depth."""
        return tree.select_best_path()

    def plan_to_steps(self, tree: PlanTree) -> list[dict[str, Any]]:
        """
        Convert best path thành executable step list.

        Returns:
            [{"step": 1, "action": "...", "reasoning": "...", "score": 0.9}, ...]
        """
        if not tree.best_path:
            tree.select_best_path()

        steps: list[dict[str, Any]] = []
        for idx, node_id in enumerate(tree.best_path):
            node = tree.nodes.get(node_id)
            if node is None:
                continue
            # Skip root node (it's just the task description)
            if idx == 0:
                continue
            steps.append(
                {
                    "step": idx,
                    "action": node.action,
                    "reasoning": node.reasoning,
                    "score": round(node.score, 3),
                    "node_id": node.node_id,
                    "depth": node.depth,
                }
            )
        return steps


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tree_search_planner: Optional[TreeSearchPlanner] = None


def get_tree_search_planner(
    max_depth: int = 3,
    branching_factor: int = 3,
    beam_width: int = 2,
) -> TreeSearchPlanner:
    """Get or create singleton TreeSearchPlanner."""
    global _tree_search_planner
    if _tree_search_planner is None:
        _tree_search_planner = TreeSearchPlanner(
            max_depth=max_depth,
            branching_factor=branching_factor,
            beam_width=beam_width,
        )
    return _tree_search_planner
