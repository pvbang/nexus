"""
Nexus - Graph Updater.

Feed agent activities back into knowledge graph.

Pattern từ MiroFish: zep_graph_memory_updater.py
Agent actions/decisions during swarm execution → convert to episodes → update KG.

Fire-and-forget design: graph operations NEVER block main execution.
All KG writes are wrapped in try/except for graceful degradation.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import structlog

logger = structlog.get_logger(__name__)


# ============================================================
# Data Classes
# ============================================================


@dataclass
class AgentActivity:
    """Structured record of a single agent activity."""

    agent_id: str
    agent_name: str
    action_type: str  # "thought", "tool_call", "response", "debate", "decision"
    description: str
    outcome: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================
# GraphUpdater
# ============================================================


class GraphUpdater:
    """
    Buffer agent activities and flush them to the knowledge graph in batches.

    Design principles:
    - Fire-and-forget: never blocks the caller
    - Graceful degradation: all KG ops wrapped in try/except
    - Batch writes: buffers activities and flushes at threshold
    - Self-sufficient: creates its own DB session when no MemoryManager is injected
    """

    def __init__(
        self,
        memory_manager: Optional[Any] = None,
        batch_size: int = 10,
    ) -> None:
        from app.memory.manager import MemoryManager

        self._memory_manager: Optional[MemoryManager] = memory_manager
        self._buffer: list[AgentActivity] = []
        self._batch_size = batch_size
        self._total_processed: int = 0
        self._last_flush_time: Optional[datetime] = None
        self._lock = asyncio.Lock()

    def set_memory_manager(self, mm: Any) -> None:
        """Set or replace the MemoryManager instance (useful for DI)."""
        self._memory_manager = mm

    @asynccontextmanager
    async def _db_scope(self) -> AsyncIterator[Optional[Any]]:
        """
        Provide a MemoryManager for KG operations.

        - If an external MemoryManager was injected (DI / tests) → yield it as-is.
        - Otherwise, create a fresh AsyncSession from the global session factory,
          wrap it in a MemoryManager, commit on success, rollback on error.
        - If the DB engine is not yet initialised → yield None (graceful skip).
        """
        from app.memory.manager import MemoryManager

        # Path 1: externally injected MemoryManager — caller owns the session.
        if self._memory_manager is not None:
            yield self._memory_manager
            return

        # Path 2: create a scoped session on-the-fly.
        from app.core.dependencies import _session_factory

        if _session_factory is None:
            logger.warning(
                "graph_updater_no_session_factory — KG writes will be skipped. "
                "Ensure app startup initializes the database session factory."
            )
            yield None
            return

        async with _session_factory() as session:
            mm = MemoryManager(session)
            try:
                yield mm
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ========================================
    # Public API
    # ========================================

    async def record_activity(self, activity: AgentActivity) -> None:
        """
        Buffer an activity. Auto-flush when buffer reaches batch_size.

        This method is fire-and-forget safe — errors are logged, never raised.
        """
        try:
            async with self._lock:
                self._buffer.append(activity)
                should_flush = len(self._buffer) >= self._batch_size

            if should_flush:
                # Fire-and-forget flush — schedule but don't await blocking
                asyncio.ensure_future(self._safe_flush())

        except Exception as exc:
            logger.warning("graph_updater_record_failed", error=str(exc))

    async def flush(self) -> int:
        """
        Process all buffered activities → KG nodes/edges.

        Returns count of activities processed.
        """
        async with self._lock:
            if not self._buffer:
                return 0
            batch = list(self._buffer)
            self._buffer.clear()

        count = 0
        async with self._db_scope() as mm:
            if not mm:
                logger.debug("graph_updater_flush_skipped_no_mm")
                return 0

            for activity in batch:
                try:
                    await self._process_activity(activity, mm)
                    count += 1
                except Exception as exc:
                    logger.warning(
                        "graph_updater_process_failed",
                        agent=activity.agent_name,
                        action=activity.action_type,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

        self._total_processed += count
        self._last_flush_time = datetime.now(timezone.utc)

        logger.info(
            "graph_updater_flushed",
            processed=count,
            total=self._total_processed,
        )
        return count

    def activity_to_episode(self, activity: AgentActivity) -> str:
        """Convert structured activity to natural language episode text."""
        parts = [
            f"Agent {activity.agent_name} performed {activity.action_type}:",
            f"{activity.description}.",
        ]
        if activity.outcome:
            parts.append(f"Result: {activity.outcome}.")
        parts.append(f"At {activity.timestamp.isoformat()}.")
        return " ".join(parts)

    async def record_swarm_round(self, round_data: dict[str, Any]) -> None:
        """
        Record a complete swarm round as interconnected KG nodes.

        Creates:
        - One node per agent proposal
        - Edges: proposes, rebuts, agrees_with
        """
        try:
            async with self._db_scope() as mm:
                if not mm:
                    return

                round_num = round_data.get("round", 0)
                proposals = round_data.get("proposals", [])
                rebuttals = round_data.get("rebuttals", [])

                # Create proposal nodes
                proposal_nodes: dict[str, Any] = {}
                for proposal in proposals:
                    agent_name = proposal.get("agent", "unknown")
                    content = proposal.get("content", "")[:500]

                    node = await mm.add_knowledge_node(
                        name=f"proposal_{agent_name}_r{round_num}",
                        entity_type="swarm_proposal",
                        description=content,
                        properties={
                            "agent": agent_name,
                            "role": proposal.get("role", ""),
                            "round": round_num,
                            "tools_used": proposal.get("tools_used", []),
                        },
                    )
                    proposal_nodes[agent_name] = node

                # Create rebuttal edges
                for rebuttal in rebuttals:
                    agent_name = rebuttal.get("agent", "unknown")
                    if agent_name in proposal_nodes:
                        source_node = proposal_nodes[agent_name]
                        # Link rebuttal to other proposals
                        for other_name, other_node in proposal_nodes.items():
                            if other_name != agent_name:
                                await mm.add_knowledge_edge(
                                    source_id=source_node.id,
                                    target_id=other_node.id,
                                    relation_type="rebuts",
                                    weight=0.8,
                                    properties={"round": round_num},
                                )

                logger.debug(
                    "graph_updater_swarm_round",
                    round=round_num,
                    proposals=len(proposals),
                    rebuttals=len(rebuttals),
                )

        except Exception as exc:
            logger.warning("graph_updater_swarm_round_failed", error=str(exc))

    async def record_tool_usage(
        self,
        agent_name: str,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None:
        """
        Record tool usage as knowledge.

        Creates:
        - Node: tool execution event
        - Edge: agent → used_tool → tool_name
        """
        try:
            async with self._db_scope() as mm:
                if not mm:
                    return

                # Create tool execution node
                tool_node = await mm.add_knowledge_node(
                    name=f"tool_exec_{tool_name}_{uuid.uuid4().hex[:8]}",
                    entity_type="tool_execution",
                    description=f"{agent_name} used {tool_name}: {result[:300]}",
                    properties={
                        "agent": agent_name,
                        "tool": tool_name,
                        "args_summary": str(args)[:200],
                        "result_preview": result[:300],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )

                # Try to find or create agent node to link
                agent_nodes = await mm.search_knowledge(
                    query=agent_name,
                    entity_type="agent",
                    limit=1,
                )
                if agent_nodes:
                    agent_node_id = uuid.UUID(agent_nodes[0]["id"])
                    await mm.add_knowledge_edge(
                        source_id=agent_node_id,
                        target_id=tool_node.id,
                        relation_type="used_tool",
                        weight=1.0,
                        properties={"tool": tool_name},
                    )

                logger.debug(
                    "graph_updater_tool_usage",
                    agent=agent_name,
                    tool=tool_name,
                )

        except Exception as exc:
            logger.warning("graph_updater_tool_usage_failed", error=str(exc))

    def get_stats(self) -> dict[str, Any]:
        """Return buffer size, total processed, last flush time."""
        return {
            "buffer_size": len(self._buffer),
            "total_processed": self._total_processed,
            "last_flush_time": (
                self._last_flush_time.isoformat() if self._last_flush_time else None
            ),
            "batch_size": self._batch_size,
        }

    # ========================================
    # Internal
    # ========================================

    async def _safe_flush(self) -> None:
        """Flush wrapped in try/except — safe for fire-and-forget."""
        try:
            await self.flush()
        except Exception as exc:
            logger.warning("graph_updater_safe_flush_failed", error=str(exc))

    async def _process_activity(self, activity: AgentActivity, mm: Any) -> None:
        """Convert a single activity to KG node + optional edges."""
        episode_text = self.activity_to_episode(activity)

        # Create activity node
        node = await mm.add_knowledge_node(
            name=f"activity_{activity.agent_name}_{activity.timestamp.strftime('%H%M%S')}",
            entity_type=f"agent_activity_{activity.action_type}",
            description=episode_text,
            properties={
                "agent_id": activity.agent_id,
                "agent_name": activity.agent_name,
                "action_type": activity.action_type,
                "outcome": activity.outcome,
                **activity.metadata,
            },
        )

        logger.debug(
            "graph_updater_node_created",
            node_name=f"activity_{activity.agent_name}_{activity.timestamp.strftime('%H%M%S')}",
            agent=activity.agent_name,
            action=activity.action_type,
        )

        # Try to link to agent node if exists
        agent_nodes = await mm.search_knowledge(
            query=activity.agent_name,
            entity_type="agent",
            limit=1,
        )
        if agent_nodes:
            agent_node_id = uuid.UUID(agent_nodes[0]["id"])
            await mm.add_knowledge_edge(
                source_id=agent_node_id,
                target_id=node.id,
                relation_type="performed",
                weight=0.9,
                properties={"action_type": activity.action_type},
            )


# ============================================================
# Module Singleton
# ============================================================

_graph_updater: Optional[GraphUpdater] = None


def get_graph_updater() -> GraphUpdater:
    """Get or create the global GraphUpdater singleton."""
    global _graph_updater
    if _graph_updater is None:
        _graph_updater = GraphUpdater()
    return _graph_updater
