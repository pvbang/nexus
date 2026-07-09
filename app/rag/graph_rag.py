"""
MekongAI Nexus - GraphRAG Retriever.

Uses the existing Knowledge Graph (knowledge_nodes + knowledge_edges)
for relationship-aware document retrieval.

Flow:
1. Extract entities from query (via LLM or simple matching)
2. Find matching nodes in KG
3. Traverse edges to related nodes (depth 1-2)
4. Return content from connected nodes as context chunks
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.models import GraphChunk

logger = structlog.get_logger(__name__)


class GraphRAGRetriever:
    """Knowledge Graph-based retrieval for RAG."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        depth: int = 2,
    ) -> list[GraphChunk]:
        """Retrieve relevant chunks via Knowledge Graph traversal.

        Args:
            query: Natural language query.
            top_k: Max results.
            depth: Graph traversal depth (1 = direct neighbors, 2 = 2-hop).

        Returns:
            List of GraphChunk with entity relationship context.
        """
        # 1. Extract entities from query
        query_entities = await self._extract_entities(query)
        if not query_entities:
            return []

        # 2. Find matching nodes in KG
        matched_nodes = await self._find_nodes(query_entities)
        if not matched_nodes:
            return []

        # 3. Traverse graph to find related content
        chunks: list[GraphChunk] = []
        seen_nodes: set[str] = set()

        for node in matched_nodes[:10]:  # Limit starting nodes
            node_id = str(node["id"])
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)

            # Add the matched node itself
            if node.get("description"):
                chunks.append(GraphChunk(
                    content=f"{node['name']} ({node.get('entity_type', '')}): {node.get('description', '')}",
                    entity=node["name"],
                    entity_type=node.get("entity_type", ""),
                    relation="self",
                    related_entity="",
                    depth=0,
                    score=0.9,
                    metadata=node.get("properties", {}),
                ))

            # Traverse edges
            if depth >= 1:
                neighbors = await self._get_neighbors(node_id, seen_nodes)
                for edge, neighbor in neighbors:
                    if neighbor.get("description"):
                        chunks.append(GraphChunk(
                            content=f"{node['name']} {edge['relation_type']} {neighbor['name']}: {neighbor.get('description', '')}",
                            entity=node["name"],
                            entity_type=node.get("entity_type", ""),
                            relation=edge.get("relation_type", "related_to"),
                            related_entity=neighbor["name"],
                            depth=1,
                            score=0.7 * float(edge.get("weight", 0.5)),
                            metadata=neighbor.get("properties", {}),
                        ))
                        seen_nodes.add(str(neighbor["id"]))

                        # Depth 2: neighbors of neighbors
                        if depth >= 2:
                            hop2 = await self._get_neighbors(str(neighbor["id"]), seen_nodes)
                            for e2, n2 in hop2[:3]:  # Limit 2-hop expansion
                                if n2.get("description"):
                                    chunks.append(GraphChunk(
                                        content=f"{neighbor['name']} {e2['relation_type']} {n2['name']}: {n2.get('description', '')}",
                                        entity=neighbor["name"],
                                        entity_type=neighbor.get("entity_type", ""),
                                        relation=e2.get("relation_type", "related_to"),
                                        related_entity=n2["name"],
                                        depth=2,
                                        score=0.5 * float(e2.get("weight", 0.5)),
                                        metadata=n2.get("properties", {}),
                                    ))
                                    seen_nodes.add(str(n2["id"]))

        # Sort by score and limit
        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks[:top_k]

    async def _extract_entities(self, query: str) -> list[str]:
        """Extract entity names from query using simple keyword matching + KG lookup.

        Falls back to splitting query into significant words and matching against KG.
        """
        # Simple approach: split query into words, filter stop words, match against KG
        import re
        words = re.findall(r'\b[A-Za-z][a-zA-Z0-9._-]{2,}\b', query)

        # Also extract capitalized phrases (likely proper nouns / tech names)
        capitalized = re.findall(r'\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*\b', query)
        words.extend(capitalized)

        # Extract quoted phrases
        quoted = re.findall(r'"([^"]+)"', query)
        words.extend(quoted)

        # Deduplicate
        entities = list(set(w.strip() for w in words if len(w.strip()) > 2))

        if not entities:
            # Last resort: use first few significant words
            simple_words = query.split()[:5]
            entities = [w for w in simple_words if len(w) > 3]

        return entities[:20]  # Limit

    async def _find_nodes(self, entities: list[str]) -> list[dict[str, Any]]:
        """Find KG nodes matching entity names (case-insensitive ILIKE)."""
        if not entities:
            return []

        # Build dynamic OR conditions for ILIKE matching
        conditions = []
        params: dict[str, Any] = {}
        for i, entity in enumerate(entities):
            key = f"e{i}"
            conditions.append(f"LOWER(name) LIKE LOWER(:{key})")
            params[key] = f"%{entity}%"

        where = " OR ".join(conditions)

        try:
            result = await self.db.execute(
                text(f"""
                    SELECT id, name, entity_type, description, properties
                    FROM knowledge_nodes
                    WHERE {where}
                    LIMIT 20
                """),
                params,
            )
            return [dict(row._mapping) for row in result.fetchall()]
        except Exception as e:
            logger.warning("graph_node_search_failed", error=str(e))
            return []

    async def _get_neighbors(
        self,
        node_id: str,
        seen: set[str],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Get neighboring nodes via edges (both directions)."""
        try:
            result = await self.db.execute(
                text("""
                    SELECT e.relation_type, e.weight, e.properties AS edge_props,
                           n.id, n.name, n.entity_type, n.description, n.properties
                    FROM knowledge_edges e
                    JOIN knowledge_nodes n ON (
                        (e.target_id = n.id AND e.source_id = CAST(:node_id AS uuid))
                        OR (e.source_id = n.id AND e.target_id = CAST(:node_id AS uuid))
                    )
                    WHERE NOT (n.id::text = ANY(:seen))
                    LIMIT 10
                """),
                {"node_id": node_id, "seen": list(seen) or ["__none__"]},
            )
            neighbors: list[tuple[dict, dict]] = []
            for row in result.fetchall():
                edge = {
                    "relation_type": row.relation_type,
                    "weight": row.weight or 0.5,
                    "properties": row.edge_props or {},
                }
                node = {
                    "id": str(row.id),
                    "name": row.name,
                    "entity_type": row.entity_type,
                    "description": row.description,
                    "properties": row.properties or {},
                }
                neighbors.append((edge, node))
            return neighbors

        except Exception as e:
            logger.debug("graph_neighbor_search_failed", error=str(e))
            return []
