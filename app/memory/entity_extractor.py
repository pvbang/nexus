"""
Nexus - Knowledge Entity Extractor (Enhanced).

Extracts entities and relationships from task results using LLM,
then stores them in the knowledge graph (knowledge_nodes + knowledge_edges).

Enhancements over original:
- Deeper extraction prompt (10-40 entities, 29 types, 24 relation types)
- 8000 char input (up from 4000)
- Entity deduplication (upsert by name+type)
- Incremental extraction helper for tool results + debate proposals
- Fire-and-forget safe for async pipelines
"""

import uuid
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.manager import MemoryManager
from app.models.database import KnowledgeNodeModel
from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)

# ── Enhanced Entity Extraction Prompt ─────────────────────────
EXTRACT_PROMPT = """You are a Knowledge Graph Entity Extractor. Analyze the text DEEPLY and extract ALL meaningful entities and relationships.

Return ONLY valid JSON (no markdown, no code blocks):
{
  "entities": [
    {"name": "entity name", "type": "Category", "description": "2-3 sentence description with key facts", "properties": {"key": "value"}}
  ],
  "relationships": [
    {"source": "entity1 name", "target": "entity2 name", "relation": "relationship_type", "weight": 0.8}
  ]
}

## ENTITY TYPES (use these categories):
Person, Organization, Company, Repository, Technology, Framework, Language,
ProgrammingLanguage, Tool, Product, Service, API, Platform, Protocol,
Topic, Concept, Theory, Method, Algorithm, Metric, Statistic,
Location, Country, City, Region, Event, Date, Period,
Currency, Market, Industry

## RELATIONSHIP TYPES (be specific):
uses, built_with, written_in, created_by, maintained_by, owned_by,
part_of, contains, depends_on, integrates_with, alternative_to, competes_with,
succeeds, precedes, influences, caused_by, results_in, correlates_with,
located_in, headquartered_in, operates_in, trending_in,
has_metric, scored, measured_at

## EXTRACTION RULES:
- Extract 10-40 entities (be THOROUGH — don't miss important ones)
- Extract 5-25 relationships between entities
- EVERY entity should have at least 1 relationship
- Include specific numbers, dates, URLs, metrics as properties
- Names should be concise, consistent (use canonical names)
- Description should include KEY FACTS, not just "a thing that exists"
- Weight: 0.3-1.0 (0.3=weak association, 0.7=moderate, 1.0=definitive)
- Prefer SPECIFIC relationship types over generic "related_to"
- Extract temporal relationships (before/after/during) when relevant
- Extract hierarchical relationships (part_of, contains) for structured data

TEXT TO ANALYZE:
"""

# Lighter prompt for incremental extraction (tool results, debate proposals)
INCREMENTAL_PROMPT = """Extract key entities and relationships from this tool result or agent output.
Focus on FACTS, NAMES, NUMBERS, and CONNECTIONS.

Return ONLY valid JSON:
{
  "entities": [{"name": "...", "type": "...", "description": "..."}],
  "relationships": [{"source": "...", "target": "...", "relation": "...", "weight": 0.7}]
}

Extract 3-15 entities and 2-10 relationships. Be precise and factual.

TEXT:
"""


async def extract_entities_from_text(
    content: str,
    task_id: str,
    db: AsyncSession,
    llm: Optional[LLMClient] = None,
    incremental: bool = False,
) -> dict[str, Any]:
    """
    Extract entities from text and store in knowledge graph.

    Args:
        content: Text to extract entities from
        task_id: Task ID for linking
        db: Database session
        llm: Optional LLM client
        incremental: If True, use lighter prompt (for tool results mid-task)

    Returns:
        {"nodes_created": int, "edges_created": int, "nodes_updated": int, "entities": [...]}
    """
    if not content or len(content.strip()) < 50:
        logger.debug("entity_extraction_skipped_short_content", task_id=task_id)
        return {"nodes_created": 0, "edges_created": 0, "nodes_updated": 0, "entities": []}

    _llm = llm or get_core_llm()
    mm = MemoryManager(db)

    # Use more text for full extraction (8000 chars), less for incremental (3000)
    max_chars = 3000 if incremental else 8000
    text_to_analyze = content[:max_chars]
    prompt = INCREMENTAL_PROMPT if incremental else EXTRACT_PROMPT

    try:
        extraction = await _llm.chat_json(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text_to_analyze},
            ],
            temperature=0.2,  # Lower temp for more consistent extraction
        )
    except Exception as e:
        logger.warning("entity_extraction_llm_failed", task_id=task_id, error=str(e))
        return {"nodes_created": 0, "edges_created": 0, "nodes_updated": 0, "entities": [], "error": str(e)}

    entities = extraction.get("entities", [])
    relationships = extraction.get("relationships", [])

    # ── Store entities with deduplication (upsert) ──
    node_map: dict[str, uuid.UUID] = {}  # lowercase name → node_id
    nodes_created = 0
    nodes_updated = 0

    for entity in entities:
        name = entity.get("name", "").strip()
        if not name:
            continue

        entity_type = entity.get("type", "Concept")
        description = entity.get("description", "")
        properties = entity.get("properties", {})
        properties["task_id"] = task_id

        try:
            # Dedup: check if node with same name+type already exists
            existing = await db.execute(
                select(KnowledgeNodeModel)
                .where(
                    KnowledgeNodeModel.name == name,
                    KnowledgeNodeModel.entity_type == entity_type,
                )
                .limit(1)
            )
            existing_node = existing.scalar_one_or_none()

            if existing_node:
                # Update description if new one is longer/better
                if description and (
                    not existing_node.description
                    or len(description) > len(existing_node.description or "")
                ):
                    existing_node.description = description
                # Merge properties
                merged_props = {**(existing_node.properties or {}), **properties}
                existing_node.properties = merged_props
                node_map[name.lower()] = existing_node.id
                nodes_updated += 1
            else:
                # Create new node
                node = await mm.add_knowledge_node(
                    name=name,
                    entity_type=entity_type,
                    description=description,
                    properties=properties,
                )
                node_map[name.lower()] = node.id
                nodes_created += 1
        except Exception as e:
            logger.debug("entity_node_upsert_failed", name=name, error=str(e))

    await db.flush()

    # ── Store relationships as knowledge edges ──
    edges_created = 0

    for rel in relationships:
        source_name = (rel.get("source", "")).strip().lower()
        target_name = (rel.get("target", "")).strip().lower()
        relation_type = rel.get("relation", "related_to")
        weight = float(rel.get("weight", 0.7))

        source_id = node_map.get(source_name)
        target_id = node_map.get(target_name)

        if source_id and target_id and source_id != target_id:
            try:
                await mm.add_knowledge_edge(
                    source_id=source_id,
                    target_id=target_id,
                    relation_type=relation_type,
                    weight=weight,
                    properties={"task_id": task_id},
                )
                edges_created += 1
            except Exception as e:
                logger.debug("entity_edge_create_failed", error=str(e))

    await db.flush()

    logger.info(
        "entity_extraction_complete",
        task_id=task_id,
        nodes_created=nodes_created,
        nodes_updated=nodes_updated,
        edges=edges_created,
        incremental=incremental,
    )

    return {
        "nodes_created": nodes_created,
        "nodes_updated": nodes_updated,
        "edges_created": edges_created,
        "entities": [{"name": e.get("name"), "type": e.get("type")} for e in entities[:40]],
    }


async def extract_entities_incremental(
    content: str,
    task_id: str,
    db: AsyncSession,
    llm: Optional[LLMClient] = None,
) -> dict[str, Any]:
    """
    Lightweight entity extraction for tool results and debate proposals.

    Called during task execution (not just at the end) to build KG incrementally.
    Uses a lighter prompt and smaller input window for speed.

    Safe to call fire-and-forget — errors are logged but never propagated.
    """
    try:
        return await extract_entities_from_text(
            content=content,
            task_id=task_id,
            db=db,
            llm=llm,
            incremental=True,
        )
    except Exception as e:
        logger.debug("incremental_extraction_failed", task_id=task_id, error=str(e))
        return {"nodes_created": 0, "edges_created": 0, "nodes_updated": 0, "entities": []}
