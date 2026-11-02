"""
Nexus - Memory Manager.

Quản lý hệ thống ký ức đa tầng:
- Short-term: conversation context (trong RAM)
- Long-term: vector-indexed memories (PostgreSQL + pgvector)
- Knowledge Graph: entity relationships (GraphRAG)
- InsightForge: multi-dimensional sub-query decomposition retrieval
- PanoramaSearch: broad context retrieval (all nodes + edges, active/historical)
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AgentMemoryModel, KnowledgeEdgeModel, KnowledgeNodeModel

logger = structlog.get_logger(__name__)


# ============================================================
# InsightForge Data Classes
# ============================================================


@dataclass
class InsightResult:
    """Result of InsightForge multi-dimensional retrieval."""

    query: str
    sub_queries: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    entity_insights: list[dict[str, Any]] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)
    synthesis: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================
# PanoramaSearch Data Classes
# ============================================================


@dataclass
class PanoramaResult:
    """Result of PanoramaSearch broad context retrieval.

    Collects ALL knowledge graph nodes and edges, classifies edges
    into active (currently valid) vs historical (expired/invalidated),
    and ranks results by keyword-based relevance scoring.

    Adapted from MiroFish PanoramaSearch pattern.
    """

    query: str
    active_facts: list[dict[str, Any]] = field(default_factory=list)
    historical_facts: list[dict[str, Any]] = field(default_factory=list)
    all_nodes: list[dict[str, Any]] = field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0


class MemoryManager:
    """
    Quản lý ký ức của agents và knowledge graph chung.

    Lấy cảm hứng từ MiroFish GraphRAG + IronClaw persistent memory.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ========================================
    # Agent Memory (Long-term per-agent)
    # ========================================

    async def store_memory(
        self,
        agent_id: uuid.UUID,
        content: str,
        memory_type: str = "episodic",
        importance: float = 0.5,
        embedding: Optional[list[float]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AgentMemoryModel:
        """Lưu một ký ức mới cho agent."""
        memory = AgentMemoryModel(
            agent_id=agent_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            embedding=embedding,
            metadata_=metadata or {},
        )
        self.db.add(memory)
        await self.db.flush()

        logger.debug(
            "memory_stored",
            agent_id=str(agent_id),
            memory_type=memory_type,
            importance=importance,
        )
        return memory

    async def recall_memories(
        self,
        agent_id: uuid.UUID,
        query_embedding: Optional[list[float]] = None,
        memory_type: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Recall memories cho agent.

        Nếu có query_embedding → vector similarity search.
        Nếu không → lấy theo thời gian và importance.
        """
        if query_embedding:
            # Vector similarity search
            result = await self.db.execute(
                text("""
                    SELECT id, content, memory_type, importance, metadata, created_at,
                           embedding <=> :embedding AS distance
                    FROM agent_memories
                    WHERE agent_id = :agent_id
                    ORDER BY distance ASC
                    LIMIT :limit
                """),
                {
                    "agent_id": str(agent_id),
                    "embedding": str(query_embedding),
                    "limit": limit,
                },
            )
            rows = result.fetchall()
            return [
                {
                    "id": str(row[0]),
                    "content": row[1],
                    "memory_type": row[2],
                    "importance": row[3],
                    "metadata": row[4],
                    "created_at": row[5].isoformat() if row[5] else None,
                    "distance": row[6],
                }
                for row in rows
            ]
        else:
            # Fallback: sort by importance and recency
            stmt = (
                select(AgentMemoryModel)
                .where(AgentMemoryModel.agent_id == agent_id)
            )
            if memory_type:
                stmt = stmt.where(AgentMemoryModel.memory_type == memory_type)
            stmt = stmt.order_by(
                AgentMemoryModel.importance.desc(),
                AgentMemoryModel.created_at.desc(),
            ).limit(limit)

            result = await self.db.execute(stmt)
            memories = result.scalars().all()
            return [
                {
                    "id": str(m.id),
                    "content": m.content,
                    "memory_type": m.memory_type,
                    "importance": m.importance,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in memories
            ]

    async def search_memories(
        self,
        query: str,
        agent_id: Optional[uuid.UUID] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Text-based memory search (used by ActiveMemoryRecall).

        Searches agent memories by content keyword matching.
        Falls back to recall_memories with importance/recency ranking
        when no embedding is available.

        Args:
            query: Text query to search for.
            agent_id: Optional agent UUID to filter by.
            limit: Max results to return.

        Returns:
            List of memory dicts with content, similarity, memory_type, created_at.
        """
        from sqlalchemy import or_

        # Escape SQL wildcards in user input
        escaped_query = query.replace("%", r"\%").replace("_", r"\_")

        # Split query into keywords for multi-keyword matching
        keywords = [kw for kw in escaped_query.lower().split() if len(kw) >= 2]
        if not keywords:
            return []

        # Build OR conditions across keywords (match ANY keyword in content)
        conditions = []
        for kw in keywords[:5]:  # Cap at 5 keywords
            conditions.append(AgentMemoryModel.content.ilike(f"%{kw}%"))

        stmt = select(AgentMemoryModel).where(or_(*conditions))

        if agent_id is not None:
            stmt = stmt.where(AgentMemoryModel.agent_id == agent_id)

        stmt = stmt.order_by(
            AgentMemoryModel.importance.desc(),
            AgentMemoryModel.created_at.desc(),
        ).limit(limit)

        result = await self.db.execute(stmt)
        memories = result.scalars().all()

        return [
            {
                "content": m.content,
                "similarity": m.importance,  # Use importance as proxy score
                "memory_type": m.memory_type,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in memories
        ]

    # ========================================
    # Knowledge Graph (Shared GraphRAG)
    # ========================================

    async def add_knowledge_node(
        self,
        name: str,
        entity_type: str,
        description: Optional[str] = None,
        properties: Optional[dict[str, Any]] = None,
        embedding: Optional[list[float]] = None,
    ) -> KnowledgeNodeModel:
        """Thêm node vào knowledge graph."""
        node = KnowledgeNodeModel(
            name=name,
            entity_type=entity_type,
            description=description,
            properties=properties or {},
            embedding=embedding,
        )
        self.db.add(node)
        await self.db.flush()
        return node

    async def add_knowledge_edge(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        relation_type: str,
        weight: float = 1.0,
        properties: Optional[dict[str, Any]] = None,
    ) -> KnowledgeEdgeModel:
        """Thêm edge vào knowledge graph."""
        edge = KnowledgeEdgeModel(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            weight=weight,
            properties=properties or {},
        )
        self.db.add(edge)
        await self.db.flush()
        return edge

    async def search_knowledge(
        self,
        query: str,
        entity_type: Optional[str] = None,
        limit: int = 20,
        include_relations: bool = False,
        max_hops: int = 1,
    ) -> list[dict[str, Any]]:
        """Tìm kiếm trong knowledge graph — upgraded with multi-keyword + graph traversal.

        Improvements over simple ILIKE:
        1. Multi-keyword: splits query into words, matches ANY keyword
        2. Graph traversal: optionally follows edges 1-2 hops from matched nodes
        3. Relationship context: includes connected nodes and edge types

        Args:
            query: Search query text.
            entity_type: Filter by entity type (optional).
            limit: Max results to return.
            include_relations: If True, include 1-hop related nodes with context.
            max_hops: Max graph traversal depth (1 or 2).
        """
        from sqlalchemy import or_

        # --- Phase 1: Multi-keyword text search ---
        # Split query into meaningful keywords (>= 2 chars)
        keywords = [
            kw.replace("%", r"\%").replace("_", r"\_")
            for kw in query.lower().split()
            if len(kw) >= 2
        ]

        if not keywords:
            return []  # PERF-04: No meaningful keywords → skip DB query

        # Build OR conditions for each keyword across name + description
        keyword_conditions = []
        for kw in keywords[:3]:  # PERF-04: Cap at 3 keywords to reduce OR explosion
            keyword_conditions.append(KnowledgeNodeModel.name.ilike(f"%{kw}%"))
            keyword_conditions.append(KnowledgeNodeModel.description.ilike(f"%{kw}%"))

        stmt = select(KnowledgeNodeModel)
        if entity_type:
            stmt = stmt.where(KnowledgeNodeModel.entity_type == entity_type)
        stmt = stmt.where(or_(*keyword_conditions)).limit(limit)

        result = await self.db.execute(stmt)
        nodes = result.scalars().all()

        # Score nodes by number of keyword matches
        scored_nodes: list[tuple[Any, float]] = []
        for node in nodes:
            score = 0.0
            searchable = f"{node.name} {node.description or ''}".lower()
            for kw in keywords:
                if kw in searchable:
                    score += 1.0
                    # Bonus for name match (more relevant)
                    if kw in (node.name or "").lower():
                        score += 0.5
            scored_nodes.append((node, score))

        # Sort by relevance score
        scored_nodes.sort(key=lambda x: x[1], reverse=True)

        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for node, score in scored_nodes[:limit]:
            node_id_str = str(node.id)
            if node_id_str in seen_ids:
                continue
            seen_ids.add(node_id_str)

            node_data: dict[str, Any] = {
                "id": node_id_str,
                "name": node.name,
                "entity_type": node.entity_type,
                "description": node.description,
                "properties": node.properties,
                "relevance_score": round(score, 2),
            }

            results.append(node_data)

        # --- Phase 2: Batch graph traversal (1-hop) for all matched nodes ---
        if include_relations and max_hops >= 1 and results:
            try:
                node_ids = [uuid.UUID(r["id"]) for r in results]
                relations_map = await self._batch_get_relations(node_ids)
                for node_data in results:
                    nid = uuid.UUID(node_data["id"])
                    relations = relations_map.get(nid, [])
                    if relations:
                        node_data["relations"] = relations[:5]
                        rel_parts = []
                        for rel in relations[:5]:
                            if rel.get("direction") == "outgoing":
                                target = rel.get("target", {})
                                rel_parts.append(
                                    f"{node_data['name']} —[{rel.get('relation', '')}]→ {target.get('name', '?')}"
                                )
                            else:
                                source = rel.get("source", {})
                                rel_parts.append(
                                    f"{source.get('name', '?')} —[{rel.get('relation', '')}]→ {node_data['name']}"
                                )
                        if rel_parts:
                            node_data["relationship_context"] = "; ".join(rel_parts)
            except Exception:
                pass

        logger.debug(
            "search_knowledge_complete",
            query=query[:100],
            keywords=keywords[:5],
            results=len(results),
            include_relations=include_relations,
        )

        return results

    async def _batch_get_relations(
        self,
        node_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[dict[str, Any]]]:
        """Batch-load relations for multiple nodes in 2 queries (outgoing + incoming)."""
        result_map: dict[uuid.UUID, list[dict[str, Any]]] = {nid: [] for nid in node_ids}
        if not node_ids:
            return result_map

        # Outgoing edges
        result = await self.db.execute(
            select(KnowledgeEdgeModel, KnowledgeNodeModel)
            .join(KnowledgeNodeModel, KnowledgeEdgeModel.target_id == KnowledgeNodeModel.id)
            .where(KnowledgeEdgeModel.source_id.in_(node_ids))
        )
        for edge, target in result.all():
            result_map[edge.source_id].append({
                "direction": "outgoing",
                "relation": edge.relation_type,
                "target": {"id": str(target.id), "name": target.name, "type": target.entity_type},
                "weight": edge.weight,
            })

        # Incoming edges
        result = await self.db.execute(
            select(KnowledgeEdgeModel, KnowledgeNodeModel)
            .join(KnowledgeNodeModel, KnowledgeEdgeModel.source_id == KnowledgeNodeModel.id)
            .where(KnowledgeEdgeModel.target_id.in_(node_ids))
        )
        for edge, source in result.all():
            result_map[edge.target_id].append({
                "direction": "incoming",
                "relation": edge.relation_type,
                "source": {"id": str(source.id), "name": source.name, "type": source.entity_type},
                "weight": edge.weight,
            })

        return result_map

    async def get_node_relations(
        self,
        node_id: uuid.UUID,
        direction: str = "both",  # outgoing, incoming, both
    ) -> list[dict[str, Any]]:
        """Lấy tất cả relations của một node."""
        relations = []

        if direction in ("outgoing", "both"):
            result = await self.db.execute(
                select(KnowledgeEdgeModel, KnowledgeNodeModel)
                .join(KnowledgeNodeModel, KnowledgeEdgeModel.target_id == KnowledgeNodeModel.id)
                .where(KnowledgeEdgeModel.source_id == node_id)
            )
            for edge, target in result.all():
                relations.append({
                    "direction": "outgoing",
                    "relation": edge.relation_type,
                    "target": {"id": str(target.id), "name": target.name, "type": target.entity_type},
                    "weight": edge.weight,
                })

        if direction in ("incoming", "both"):
            result = await self.db.execute(
                select(KnowledgeEdgeModel, KnowledgeNodeModel)
                .join(KnowledgeNodeModel, KnowledgeEdgeModel.source_id == KnowledgeNodeModel.id)
                .where(KnowledgeEdgeModel.target_id == node_id)
            )
            for edge, source in result.all():
                relations.append({
                    "direction": "incoming",
                    "relation": edge.relation_type,
                    "source": {"id": str(source.id), "name": source.name, "type": source.entity_type},
                    "weight": edge.weight,
                })

        return relations

    async def get_graph_stats(self) -> dict[str, Any]:
        """Thống kê knowledge graph."""
        node_count = await self.db.execute(
            text("SELECT COUNT(*) FROM knowledge_nodes")
        )
        edge_count = await self.db.execute(
            text("SELECT COUNT(*) FROM knowledge_edges")
        )
        types = await self.db.execute(
            text("SELECT DISTINCT entity_type FROM knowledge_nodes")
        )

        return {
            "node_count": node_count.scalar() or 0,
            "edge_count": edge_count.scalar() or 0,
            "entity_types": [row[0] for row in types.all()],
        }

    async def get_full_graph(
        self,
        limit_nodes: int = 500,
        entity_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lấy toàn bộ knowledge graph cho visualization.

        Returns nodes + edges + stats cho frontend graph renderer.
        """
        # Fetch nodes
        node_stmt = select(KnowledgeNodeModel).limit(limit_nodes)
        if entity_type:
            node_stmt = node_stmt.where(
                KnowledgeNodeModel.entity_type == entity_type
            )
        node_stmt = node_stmt.order_by(KnowledgeNodeModel.created_at.desc())

        node_result = await self.db.execute(node_stmt)
        nodes_raw = node_result.scalars().all()
        node_ids = {n.id for n in nodes_raw}

        nodes = []
        for n in nodes_raw:
            nodes.append({
                "id": str(n.id),
                "name": n.name,
                "entity_type": n.entity_type,
                "description": n.description or "",
                "properties": n.properties or {},
            })

        # Fetch edges (only those connecting fetched nodes)
        if node_ids:
            edge_stmt = select(KnowledgeEdgeModel).where(
                KnowledgeEdgeModel.source_id.in_(node_ids),
                KnowledgeEdgeModel.target_id.in_(node_ids),
            )
            edge_result = await self.db.execute(edge_stmt)
            edges_raw = edge_result.scalars().all()
        else:
            edges_raw = []

        edges = []
        for e in edges_raw:
            edges.append({
                "id": str(e.id),
                "source": str(e.source_id),
                "target": str(e.target_id),
                "relation_type": e.relation_type,
                "weight": e.weight if e.weight else 1.0,
                "properties": e.properties or {},
            })

        # Collect entity type stats
        type_counts: dict[str, int] = {}
        for n in nodes:
            t = n["entity_type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "entity_types": sorted(type_counts.keys()),
                "type_counts": type_counts,
            },
        }

    # ========================================
    # PanoramaSearch — Broad Context Retrieval
    # ========================================

    async def panorama_search(
        self,
        query: str,
        include_expired: bool = True,
        limit: int = 50,
    ) -> "PanoramaResult":
        """
        Broad context retrieval: fetch ALL knowledge graph nodes and edges,
        classify into active vs historical facts, rank by keyword relevance.

        PanoramaSearch pattern from MiroFish:
        1. Query ALL knowledge_nodes from PostgreSQL
        2. Query ALL knowledge_edges with joined source/target node names
        3. Classify edges: active (no expired_at/invalid_at in properties)
           vs historical (has expired_at or invalid_at in properties)
        4. Keyword-based relevance scoring for sorting
        5. Return PanoramaResult with active_facts, historical_facts, counts

        Args:
            query: Search query for relevance scoring.
            include_expired: Whether to include historical/expired facts.
            limit: Max number of facts per category (active/historical).

        Returns:
            PanoramaResult with active_facts, historical_facts, all_nodes, counts.
        """
        start_time = time.perf_counter()
        logger.info("panorama_search_started", query=query[:100])

        # Step 1: Fetch all knowledge nodes
        node_stmt = select(KnowledgeNodeModel).order_by(
            KnowledgeNodeModel.created_at.desc(),
        )
        node_result = await self.db.execute(node_stmt)
        nodes_raw = node_result.scalars().all()

        all_nodes: list[dict[str, Any]] = []
        node_lookup: dict[str, str] = {}  # id -> name for edge enrichment

        for n in nodes_raw:
            node_id_str = str(n.id)
            node_lookup[node_id_str] = n.name
            all_nodes.append({
                "id": node_id_str,
                "name": n.name,
                "entity_type": n.entity_type,
                "description": n.description or "",
                "properties": n.properties or {},
            })

        # Step 2: Fetch all knowledge edges
        edge_stmt = select(KnowledgeEdgeModel)
        edge_result = await self.db.execute(edge_stmt)
        edges_raw = edge_result.scalars().all()

        # Step 3: Classify edges into active vs historical
        active_facts: list[dict[str, Any]] = []
        historical_facts: list[dict[str, Any]] = []

        for edge in edges_raw:
            props = edge.properties or {}
            source_name = node_lookup.get(str(edge.source_id), "unknown")
            target_name = node_lookup.get(str(edge.target_id), "unknown")

            fact = {
                "edge_id": str(edge.id),
                "source_id": str(edge.source_id),
                "source_name": source_name,
                "target_id": str(edge.target_id),
                "target_name": target_name,
                "relation_type": edge.relation_type,
                "weight": edge.weight if edge.weight else 1.0,
                "properties": props,
                "fact_text": f"{source_name} —[{edge.relation_type}]→ {target_name}",
            }

            # Compute keyword relevance score
            fact["relevance_score"] = self._compute_keyword_relevance(
                query, fact["fact_text"], source_name, target_name,
                edge.relation_type, props,
            )

            # Classify: historical if properties contain expired_at or invalid_at
            is_expired = bool(
                props.get("expired_at") or props.get("invalid_at")
            )

            if is_expired:
                historical_facts.append(fact)
            else:
                active_facts.append(fact)

        # Step 4: Sort by relevance score (descending)
        active_facts.sort(
            key=lambda f: f.get("relevance_score", 0.0), reverse=True,
        )
        historical_facts.sort(
            key=lambda f: f.get("relevance_score", 0.0), reverse=True,
        )

        # Apply limit
        active_limited = active_facts[:limit]
        historical_limited = historical_facts[:limit] if include_expired else []

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        result = PanoramaResult(
            query=query,
            active_facts=active_limited,
            historical_facts=historical_limited,
            all_nodes=all_nodes,
            total_nodes=len(all_nodes),
            total_edges=len(edges_raw),
            active_count=len(active_facts),
            historical_count=len(historical_facts),
        )

        logger.info(
            "panorama_search_completed",
            total_nodes=result.total_nodes,
            total_edges=result.total_edges,
            active_count=result.active_count,
            historical_count=result.historical_count,
            returned_active=len(active_limited),
            returned_historical=len(historical_limited),
            time_ms=round(elapsed_ms),
        )

        return result

    @staticmethod
    def _compute_keyword_relevance(
        query: str,
        fact_text: str,
        source_name: str,
        target_name: str,
        relation_type: str,
        properties: dict[str, Any],
    ) -> float:
        """
        Compute keyword-based relevance score for a fact against a query.

        Splits query into lowercase keywords and counts matches across
        fact text, node names, relation type, and property values.

        Args:
            query: Original search query.
            fact_text: Human-readable fact representation.
            source_name: Source node name.
            target_name: Target node name.
            relation_type: Edge relation type.
            properties: Edge properties dict.

        Returns:
            Relevance score (0.0+). Higher = more relevant.
        """
        keywords = [kw for kw in query.lower().split() if len(kw) >= 2]
        if not keywords:
            return 0.0

        # Build searchable text corpus from all fact fields
        searchable_parts = [
            fact_text.lower(),
            source_name.lower(),
            target_name.lower(),
            relation_type.lower(),
        ]
        # Include string values from properties
        for value in properties.values():
            if isinstance(value, str):
                searchable_parts.append(value.lower())

        corpus = " ".join(searchable_parts)

        # Count keyword matches
        match_count = sum(1 for kw in keywords if kw in corpus)

        # Normalize: proportion of keywords matched
        score = match_count / len(keywords)

        # Bonus for exact phrase match in fact_text
        if query.lower() in fact_text.lower():
            score += 0.5

        return round(score, 4)

    # ========================================
    # InsightForge — Sub-query Decomposition
    # ========================================

    async def insight_forge(
        self,
        query: str,
        max_sub_queries: int = 5,
        llm: Optional[Any] = None,
    ) -> "InsightResult":
        """
        Multi-dimensional retrieval: decompose complex query → parallel
        sub-queries → deduplicate → extract entities → synthesize.

        InsightForge pattern from MiroFish:
        1. LLM decomposes query into N sub-queries (different angles)
        2. Parallel search for each sub-query (recall_memories + search_knowledge)
        3. Deduplicate and rank results
        4. Extract entity relationships and build chains
        5. Synthesize comprehensive insight

        Args:
            query: Complex user query to decompose.
            max_sub_queries: Max number of sub-queries to generate.
            llm: Optional LLMClient for decomposition / synthesis.

        Returns:
            InsightResult with sub_queries, facts, entity_insights, chains, synthesis.
        """
        start_time = time.perf_counter()

        # Lazy import to avoid circular dependency
        if llm is None:
            from app.services.llm_client import get_core_llm
            llm = get_core_llm()

        logger.info("insight_forge_started", query=query[:100])

        # Step 1: Decompose query into sub-queries
        sub_queries = await self._decompose_query(query, max_sub_queries, llm)
        logger.info("insight_forge_decomposed", sub_queries=sub_queries)

        # Step 2: Parallel search for each sub-query
        search_tasks = [
            self._search_sub_query(sq) for sq in sub_queries
        ]
        sub_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Step 3: Collect and deduplicate facts
        all_facts: list[dict[str, Any]] = []
        seen_contents: set[str] = set()

        for i, result in enumerate(sub_results):
            if isinstance(result, BaseException):
                logger.warning(
                    "insight_forge_subquery_failed",
                    sub_query=sub_queries[i] if i < len(sub_queries) else "?",
                    error=str(result),
                )
                continue

            for fact in result:
                content_key = fact.get("content", "")[:200]
                if content_key and content_key not in seen_contents:
                    seen_contents.add(content_key)
                    all_facts.append(fact)

        # Sort by relevance score (higher first)
        all_facts.sort(key=lambda f: f.get("relevance_score", 0.0), reverse=True)

        # Step 4: Extract entity insights and relationship chains
        entity_insights = await self._extract_entity_insights(all_facts)
        chains = await self._build_relationship_chains(entity_insights)

        # Step 5: Synthesize comprehensive answer
        synthesis = await self._synthesize_insight(
            query, sub_queries, all_facts, entity_insights, chains, llm,
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        result = InsightResult(
            query=query,
            sub_queries=sub_queries,
            facts=all_facts,
            entity_insights=entity_insights,
            chains=chains,
            synthesis=synthesis,
            metadata={
                "total_results": len(all_facts),
                "search_time_ms": round(elapsed_ms),
                "sub_query_count": len(sub_queries),
                "entity_count": len(entity_insights),
                "chain_count": len(chains),
            },
        )

        logger.info(
            "insight_forge_completed",
            facts=len(all_facts),
            entities=len(entity_insights),
            chains=len(chains),
            time_ms=round(elapsed_ms),
        )

        return result

    async def _decompose_query(
        self,
        query: str,
        max_sub_queries: int,
        llm: Any,
    ) -> list[str]:
        """LLM decomposes a complex query into sub-queries."""
        messages = [
            {
                "role": "system",
                "content": (
                    "Decompose the user's complex query into multiple simpler "
                    "sub-queries, each exploring a different dimension or angle.\n\n"
                    "Return ONLY valid JSON:\n"
                    '{"sub_queries": ["query1", "query2", ...]}\n\n'
                    "Guidelines:\n"
                    f"- Generate 2 to {max_sub_queries} sub-queries\n"
                    "- Each sub-query should be self-contained and searchable\n"
                    "- Cover different aspects: who, what, when, why, how\n"
                    "- Include both broad and specific queries\n"
                    "- First sub-query should be close to the original query"
                ),
            },
            {"role": "user", "content": query},
        ]

        try:
            result = await llm.chat_json(messages, temperature=0.3)
            sub_queries = result.get("sub_queries", [query])
            return sub_queries[:max_sub_queries] if sub_queries else [query]
        except Exception as e:
            logger.warning("insight_forge_decompose_failed", error=str(e))
            return [query]

    async def _search_sub_query(
        self,
        sub_query: str,
    ) -> list[dict[str, Any]]:
        """Search both memories and knowledge for a single sub-query."""
        facts: list[dict[str, Any]] = []

        # Search knowledge graph nodes
        try:
            kg_results = await self.search_knowledge(query=sub_query, limit=10)
            for node in kg_results:
                facts.append({
                    "content": (
                        f"{node.get('name', '')}: "
                        f"{node.get('description', '') or ''}"
                    ),
                    "source": "knowledge_graph",
                    "relevance_score": 0.8,
                    "entity_type": node.get("entity_type"),
                    "node_id": node.get("id"),
                    "properties": node.get("properties", {}),
                })
        except Exception as e:
            logger.debug("insight_forge_kg_search_failed", error=str(e))

        # Search agent memories (text-based, across all agents)
        try:
            escaped_query = sub_query.replace("%", r"\%").replace("_", r"\_")
            stmt = (
                select(AgentMemoryModel)
                .where(
                    AgentMemoryModel.content.ilike(f"%{escaped_query}%")
                )
                .order_by(
                    AgentMemoryModel.importance.desc(),
                    AgentMemoryModel.created_at.desc(),
                )
                .limit(10)
            )
            result = await self.db.execute(stmt)
            memories = result.scalars().all()
            for mem in memories:
                facts.append({
                    "content": mem.content,
                    "source": "agent_memory",
                    "relevance_score": 0.6 + (mem.importance * 0.3),
                    "memory_type": mem.memory_type,
                    "agent_id": str(mem.agent_id),
                })
        except Exception as e:
            logger.debug("insight_forge_memory_search_failed", error=str(e))

        return facts

    async def _extract_entity_insights(
        self,
        facts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Extract entity insights from collected facts."""
        entity_map: dict[str, dict[str, Any]] = {}

        for fact in facts:
            node_id = fact.get("node_id")
            if not node_id:
                continue

            name = fact.get("content", "").split(":")[0].strip()
            if name not in entity_map:
                entity_map[name] = {
                    "entity": name,
                    "type": fact.get("entity_type", "unknown"),
                    "relations": [],
                    "context": fact.get("content", ""),
                    "node_id": node_id,
                }

        # Fetch relations for discovered entities
        for entity_name, info in entity_map.items():
            node_id = info.get("node_id")
            if not node_id:
                continue
            try:
                relations = await self.get_node_relations(
                    uuid.UUID(node_id), direction="both",
                )
                info["relations"] = relations
            except Exception:
                pass

        return list(entity_map.values())

    async def _build_relationship_chains(
        self,
        entity_insights: list[dict[str, Any]],
    ) -> list[str]:
        """Build relationship chains from entity insights: A → B → C."""
        chains: list[str] = []

        for entity in entity_insights:
            name = entity.get("entity", "?")
            for rel in entity.get("relations", []):
                direction = rel.get("direction", "outgoing")
                rel_type = rel.get("relation", "related_to")

                if direction == "outgoing":
                    target = rel.get("target", {}).get("name", "?")
                    chain = f"{name} —[{rel_type}]→ {target}"
                else:
                    source = rel.get("source", {}).get("name", "?")
                    chain = f"{source} —[{rel_type}]→ {name}"

                if chain not in chains:
                    chains.append(chain)

        return chains

    async def _synthesize_insight(
        self,
        query: str,
        sub_queries: list[str],
        facts: list[dict[str, Any]],
        entity_insights: list[dict[str, Any]],
        chains: list[str],
        llm: Any,
    ) -> str:
        """LLM synthesizes comprehensive answer from all gathered data."""
        if not facts and not entity_insights:
            return "No relevant information found in the knowledge base."

        facts_text = "\n".join(
            f"- {f.get('content', '')}" for f in facts[:20]
        )
        entities_text = "\n".join(
            f"- {e.get('entity', '')} ({e.get('type', '')}): "
            f"{len(e.get('relations', []))} relations"
            for e in entity_insights[:10]
        )
        chains_text = "\n".join(f"- {c}" for c in chains[:15])

        messages = [
            {
                "role": "system",
                "content": (
                    "Synthesize a comprehensive answer to the user's query using "
                    "the research data below. Be thorough, cite specific facts, "
                    "and highlight relationships between entities."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original query: {query}\n\n"
                    f"Sub-queries explored: {', '.join(sub_queries)}\n\n"
                    f"Facts gathered:\n{facts_text}\n\n"
                    f"Entity insights:\n{entities_text}\n\n"
                    f"Relationship chains:\n{chains_text}\n\n"
                    "Provide a comprehensive answer."
                ),
            },
        ]

        try:
            result = await llm.chat(messages, temperature=0.4, max_tokens=4096)
            return result.get("content", "Synthesis unavailable.")
        except Exception as e:
            logger.warning("insight_forge_synthesis_failed", error=str(e))
            return f"Found {len(facts)} facts and {len(entity_insights)} entities."
