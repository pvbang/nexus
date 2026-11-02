"""
Nexus - Ontology Auto-Generation from Documents.

Pattern từ MiroFish: ontology_generator.py
Khi user uploads documents, LLM auto-generates optimal KG schema
trước khi indexing vào knowledge graph.

Flow:
1. Document text → LLM analyzes → extracts entity/relation types
2. Multiple docs → merge ontologies → unified schema
3. Ontology → prompt context → guide KG population
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ============================================================
# Data Classes
# ============================================================


@dataclass
class EntityType:
    """An entity type in the ontology schema."""

    name: str
    description: str = ""
    attributes: list[str] = field(default_factory=list)


@dataclass
class RelationType:
    """A relation type connecting two entity types."""

    name: str
    description: str = ""
    source_type: str = ""
    target_type: str = ""


@dataclass
class Ontology:
    """Knowledge graph ontology schema auto-generated from documents."""

    domain: str = ""
    entity_types: list[EntityType] = field(default_factory=list)
    relation_types: list[RelationType] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_doc_hash: Optional[str] = None


# ============================================================
# Ontology Generator
# ============================================================


class OntologyGenerator:
    """
    LLM-powered ontology generator for knowledge graph schema extraction.

    Given document text, uses LLM to identify:
    - Entity types (5-15): e.g. Person, Organization, Event, Location, Concept
    - Relation types (5-10): e.g. works_at, located_in, caused_by
    - Attributes per entity type: e.g. Person has {name, role, affiliation}
    """

    def __init__(self, llm: Optional[Any] = None) -> None:
        self._llm = llm

    @property
    def llm(self) -> Any:
        """Lazy-load LLM client to avoid circular imports."""
        if self._llm is None:
            from app.services.llm_client import get_core_llm

            self._llm = get_core_llm()
        return self._llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_ontology(
        self,
        text: str,
        domain_hint: str = "",
    ) -> Ontology:
        """
        Analyze document text and generate a knowledge graph ontology schema.

        Args:
            text: Document text to analyze.
            domain_hint: Optional hint about the domain (e.g. "medical", "legal").

        Returns:
            Ontology with entity types, relation types, and attributes.
        """
        doc_hash = hashlib.md5(text[:5000].encode()).hexdigest()
        logger.info(
            "ontology_generation_started",
            text_length=len(text),
            domain_hint=domain_hint or "(auto)",
            doc_hash=doc_hash,
        )

        # Truncate text for prompt (LLM context budget)
        sample_text = text[:8000]
        if len(text) > 8000:
            # Include start + end for better coverage
            sample_text = text[:6000] + "\n\n[...]\n\n" + text[-2000:]

        domain_instruction = ""
        if domain_hint:
            domain_instruction = f"Domain hint: {domain_hint}. "

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an ontology engineer. Analyze the provided text and "
                    "identify the optimal knowledge graph schema.\n\n"
                    f"{domain_instruction}"
                    "Extract:\n"
                    "1. Entity types (5-15): the main types of things/concepts in the text\n"
                    "2. Relation types (5-10): how entities relate to each other\n"
                    "3. Key attributes for each entity type\n\n"
                    "Return ONLY valid JSON with this structure:\n"
                    "{\n"
                    '  "entity_types": [\n'
                    '    {"name": "Person", "description": "A human individual", '
                    '"attributes": ["name", "role", "affiliation"]}\n'
                    "  ],\n"
                    '  "relation_types": [\n'
                    '    {"name": "works_at", "description": "Employment relationship", '
                    '"source_type": "Person", "target_type": "Organization"}\n'
                    "  ],\n"
                    '  "domain": "short domain label"\n'
                    "}\n\n"
                    "Guidelines:\n"
                    "- Entity type names should be PascalCase (Person, Organization)\n"
                    "- Relation names should be snake_case (works_at, located_in)\n"
                    "- Be specific to the document content, not generic\n"
                    "- Include at least 2-5 attributes per entity type\n"
                    "- Every relation must reference valid entity types"
                ),
            },
            {
                "role": "user",
                "content": f"Analyze this text and extract the ontology schema:\n\n{sample_text}",
            },
        ]

        try:
            result = await self.llm.chat_json(messages, temperature=0.2)
            ontology = self._parse_llm_response(result, doc_hash)
            logger.info(
                "ontology_generation_completed",
                domain=ontology.domain,
                entity_types=len(ontology.entity_types),
                relation_types=len(ontology.relation_types),
            )
            return ontology

        except Exception as e:
            logger.error("ontology_generation_failed", error=str(e))
            # Return minimal fallback ontology
            return Ontology(
                domain=domain_hint or "unknown",
                entity_types=[
                    EntityType(
                        name="Entity",
                        description="Generic entity",
                        attributes=["name", "description"],
                    ),
                ],
                relation_types=[
                    RelationType(
                        name="related_to",
                        description="Generic relation",
                        source_type="Entity",
                        target_type="Entity",
                    ),
                ],
                source_doc_hash=doc_hash,
            )

    async def generate_from_document(self, file_path: str) -> Ontology:
        """
        Read a document file, process it, and generate an ontology.

        Uses document_processor to convert file → text, then generates ontology.

        Args:
            file_path: Path to the document file.

        Returns:
            Ontology schema extracted from the document.
        """
        from app.services.document_processor import process_document

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {file_path}")

        logger.info("ontology_from_document_started", file_path=file_path)

        doc = await process_document(
            file_path=path,
            original_filename=path.name,
            max_chars=50_000,
        )

        if doc.error:
            raise ValueError(f"Failed to process document: {doc.error}")

        if not doc.markdown_content:
            raise ValueError(f"Document produced no content: {file_path}")

        domain_hint = _infer_domain_from_filename(path.name)
        return await self.generate_ontology(
            text=doc.markdown_content,
            domain_hint=domain_hint,
        )

    def merge_ontologies(self, ontologies: list[Ontology]) -> Ontology:
        """
        Merge multiple ontologies into a unified schema.

        - Union entity types (deduplicate by name, merge attributes)
        - Union relation types (deduplicate by name)
        - Combine domains

        Args:
            ontologies: List of ontologies to merge.

        Returns:
            Merged ontology with deduplicated types.
        """
        if not ontologies:
            return Ontology()

        if len(ontologies) == 1:
            return ontologies[0]

        # Merge entity types
        entity_map: dict[str, EntityType] = {}
        for onto in ontologies:
            for et in onto.entity_types:
                key = et.name.lower()
                if key in entity_map:
                    # Merge attributes (union)
                    existing = entity_map[key]
                    merged_attrs = list(set(existing.attributes) | set(et.attributes))
                    entity_map[key] = EntityType(
                        name=existing.name,
                        description=existing.description or et.description,
                        attributes=sorted(merged_attrs),
                    )
                else:
                    entity_map[key] = EntityType(
                        name=et.name,
                        description=et.description,
                        attributes=list(et.attributes),
                    )

        # Merge relation types
        relation_map: dict[str, RelationType] = {}
        for onto in ontologies:
            for rt in onto.relation_types:
                key = rt.name.lower()
                if key not in relation_map:
                    relation_map[key] = RelationType(
                        name=rt.name,
                        description=rt.description,
                        source_type=rt.source_type,
                        target_type=rt.target_type,
                    )

        # Combine domains
        domains = list({o.domain for o in ontologies if o.domain})
        merged_domain = ", ".join(sorted(domains)) if domains else "multi-domain"

        merged = Ontology(
            domain=merged_domain,
            entity_types=list(entity_map.values()),
            relation_types=list(relation_map.values()),
            generated_at=datetime.now(timezone.utc),
            source_doc_hash=None,
        )

        logger.info(
            "ontologies_merged",
            input_count=len(ontologies),
            entity_types=len(merged.entity_types),
            relation_types=len(merged.relation_types),
            domain=merged.domain,
        )

        return merged

    def validate_ontology(self, ontology: Ontology) -> list[str]:
        """
        Validate ontology for common issues.

        Checks:
        - Duplicate entity types
        - Relations referencing non-existent entity types
        - Empty attributes
        - Missing descriptions

        Args:
            ontology: Ontology to validate.

        Returns:
            List of warning messages (empty if valid).
        """
        warnings: list[str] = []

        # Check duplicate entity types
        entity_names = [et.name.lower() for et in ontology.entity_types]
        seen: set[str] = set()
        for name in entity_names:
            if name in seen:
                warnings.append(f"Duplicate entity type: '{name}'")
            seen.add(name)

        # Build valid entity type set
        valid_types = {et.name.lower() for et in ontology.entity_types}

        # Check relations reference valid entity types
        for rt in ontology.relation_types:
            if rt.source_type and rt.source_type.lower() not in valid_types:
                warnings.append(
                    f"Relation '{rt.name}' references non-existent "
                    f"source type: '{rt.source_type}'"
                )
            if rt.target_type and rt.target_type.lower() not in valid_types:
                warnings.append(
                    f"Relation '{rt.name}' references non-existent "
                    f"target type: '{rt.target_type}'"
                )

        # Check empty attributes
        for et in ontology.entity_types:
            if not et.attributes:
                warnings.append(
                    f"Entity type '{et.name}' has no attributes"
                )

        # Check missing descriptions
        for et in ontology.entity_types:
            if not et.description:
                warnings.append(
                    f"Entity type '{et.name}' has no description"
                )

        for rt in ontology.relation_types:
            if not rt.description:
                warnings.append(
                    f"Relation type '{rt.name}' has no description"
                )

        if warnings:
            logger.warning(
                "ontology_validation_warnings",
                warning_count=len(warnings),
                warnings=warnings[:5],
            )

        return warnings

    def to_prompt_context(self, ontology: Ontology) -> str:
        """
        Convert ontology to a text representation for use in LLM prompts.

        Useful for guiding entity extraction and KG population.

        Args:
            ontology: Ontology to convert.

        Returns:
            Human-readable text describing the schema.
        """
        lines: list[str] = []

        if ontology.domain:
            lines.append(f"Domain: {ontology.domain}")
            lines.append("")

        if ontology.entity_types:
            lines.append("Entity types:")
            for et in ontology.entity_types:
                attrs = ", ".join(et.attributes) if et.attributes else "none"
                desc = f" — {et.description}" if et.description else ""
                lines.append(f"  - {et.name} ({attrs}){desc}")
            lines.append("")

        if ontology.relation_types:
            lines.append("Relations:")
            for rt in ontology.relation_types:
                arrow = f"{rt.source_type} → {rt.target_type}" if rt.source_type and rt.target_type else "any → any"
                desc = f" — {rt.description}" if rt.description else ""
                lines.append(f"  - {rt.name} ({arrow}){desc}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_llm_response(
        self,
        data: dict[str, Any],
        doc_hash: str,
    ) -> Ontology:
        """Parse LLM JSON response into Ontology dataclass."""
        entity_types: list[EntityType] = []
        for et_data in data.get("entity_types", []):
            if isinstance(et_data, dict) and et_data.get("name"):
                entity_types.append(
                    EntityType(
                        name=et_data["name"],
                        description=et_data.get("description", ""),
                        attributes=et_data.get("attributes", []),
                    )
                )

        relation_types: list[RelationType] = []
        for rt_data in data.get("relation_types", []):
            if isinstance(rt_data, dict) and rt_data.get("name"):
                relation_types.append(
                    RelationType(
                        name=rt_data["name"],
                        description=rt_data.get("description", ""),
                        source_type=rt_data.get("source_type", ""),
                        target_type=rt_data.get("target_type", ""),
                    )
                )

        return Ontology(
            domain=data.get("domain", ""),
            entity_types=entity_types,
            relation_types=relation_types,
            generated_at=datetime.now(timezone.utc),
            source_doc_hash=doc_hash,
        )


# ============================================================
# Helpers
# ============================================================


def _infer_domain_from_filename(filename: str) -> str:
    """Infer a rough domain hint from the filename."""
    lower = filename.lower()
    domain_keywords = {
        "medical": ["medical", "health", "clinical", "patient", "diagnosis"],
        "legal": ["legal", "contract", "law", "court", "regulation"],
        "finance": ["finance", "financial", "budget", "revenue", "investment"],
        "technology": ["tech", "software", "api", "code", "system"],
        "academic": ["research", "paper", "study", "thesis", "journal"],
        "business": ["business", "strategy", "market", "company", "report"],
    }
    for domain, keywords in domain_keywords.items():
        if any(kw in lower for kw in keywords):
            return domain
    return ""


# ============================================================
# Module Singleton
# ============================================================

_ontology_generator: Optional[OntologyGenerator] = None


def get_ontology_generator() -> OntologyGenerator:
    """Get or create the module-level OntologyGenerator singleton."""
    global _ontology_generator
    if _ontology_generator is None:
        _ontology_generator = OntologyGenerator()
    return _ontology_generator
