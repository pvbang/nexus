"""
L1: Atom Extraction Layer.

Extracts structured memory atoms from raw conversations using LLM.
Each atom is:
- Self-contained (understandable without context)
- Typed (persona/episodic/instruction/factual)
- Scored by importance (0.0 - 1.0)
- Optionally deduplicated against existing atoms via text similarity
"""
from __future__ import annotations

import json
import structlog
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.layers.models import (
    MemoryAtomModel,
    MemoryPipelineStateModel,
    AtomType,
    MemoryAtom,
    L1ExtractionResult,
)
from app.memory.layers.config import MemoryLayersConfig
from app.memory.layers.prompts import L1_EXTRACTION_SYSTEM, L1_EXTRACTION_USER
from app.models.database import ConversationMessageModel
from app.services.llm_client import LLMClient

logger = structlog.get_logger(__name__)


class L1Extractor:
    """Extracts structured memory atoms from conversations."""

    def __init__(self, config: MemoryLayersConfig, llm_client: LLMClient):
        self.config = config
        self.llm = llm_client

    async def extract_from_session(
        self,
        db: AsyncSession,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> L1ExtractionResult:
        """
        Extract atoms from unprocessed messages in a session.

        Args:
            db: Database session
            session_id: Conversation session ID
            agent_id: Optional agent ID for scoping
            user_id: Optional user ID for scoping
            messages: Pre-loaded messages (if None, will query from DB)

        Returns:
            L1ExtractionResult with extracted atoms and count
        """
        try:
            # Get pipeline state to know cursor position
            state = await self._get_state(db, session_id)
            if not state:
                logger.warning("l1_no_state", session_id=session_id)
                return L1ExtractionResult()

            # Get unprocessed messages
            if messages is None:
                messages = await self._fetch_unprocessed_messages(
                    db, session_id, state.l1_cursor
                )

            if not messages:
                logger.debug("l1_no_new_messages", session_id=session_id)
                return L1ExtractionResult()

            # Format conversation for LLM
            conversation_text = self._format_conversation(messages)

            # Call LLM for extraction
            atoms = await self._llm_extract(conversation_text)

            if not atoms:
                logger.debug("l1_no_atoms_extracted", session_id=session_id)
                return L1ExtractionResult(messages_processed=len(messages))

            # Deduplicate against existing atoms
            if self.config.dedup_similarity_threshold < 1.0:
                atoms = await self._deduplicate(db, atoms, session_id, user_id)

            # Persist atoms
            saved_count = await self._save_atoms(
                db, atoms, session_id, agent_id, user_id
            )

            logger.info(
                "l1_extraction_complete",
                session_id=session_id,
                messages_processed=len(messages),
                atoms_extracted=len(atoms),
                atoms_saved=saved_count,
            )

            return L1ExtractionResult(
                atoms=atoms,
                messages_processed=len(messages),
            )

        except Exception as e:
            logger.error(
                "l1_extraction_failed",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            return L1ExtractionResult()

    async def _get_state(
        self, db: AsyncSession, session_id: str
    ) -> MemoryPipelineStateModel | None:
        """Get pipeline state for session."""
        stmt = select(MemoryPipelineStateModel).where(
            MemoryPipelineStateModel.session_id == session_id
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _fetch_unprocessed_messages(
        self, db: AsyncSession, session_id: str, cursor: int
    ) -> list[dict[str, Any]]:
        """Fetch messages after the L1 cursor from conversation_messages."""
        stmt = (
            select(ConversationMessageModel)
            .where(ConversationMessageModel.session_id == session_id)
            .where(ConversationMessageModel.role.in_(["user", "assistant"]))
            .order_by(ConversationMessageModel.created_at.asc())
            .offset(cursor)
            .limit(50)  # Process max 50 messages at a time
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

        return [
            {"role": row.role, "content": row.content, "id": str(row.id)}
            for row in rows
        ]

    def _format_conversation(self, messages: list[dict[str, Any]]) -> str:
        """Format messages into conversation text for LLM."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown").capitalize()
            content = msg.get("content", "")
            # Truncate very long messages
            if len(content) > 2000:
                content = content[:2000] + "... [truncated]"
            lines.append(f"{role}: {content}")
        return "\n\n".join(lines)

    async def _llm_extract(self, conversation_text: str) -> list[MemoryAtom]:
        """Call LLM to extract atoms from conversation."""
        user_prompt = (
            L1_EXTRACTION_USER
            .replace("{conversation}", conversation_text)
            .replace("{max_atoms}", str(self.config.l1_max_atoms_per_extraction))
        )

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": L1_EXTRACTION_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
            )

            content = response.get("content", "")

            # Parse JSON response
            atoms_data = self._parse_json_array(content)

            atoms = []
            for item in atoms_data:
                try:
                    atom_type_str = item.get("atom_type", "factual")
                    # Validate atom_type against enum values
                    try:
                        atom_type = AtomType(atom_type_str)
                    except ValueError:
                        atom_type = AtomType.FACTUAL

                    atom = MemoryAtom(
                        content=item["content"],
                        atom_type=atom_type,
                        importance=max(0.0, min(1.0, float(item.get("importance", 0.5)))),
                    )
                    atoms.append(atom)
                except (KeyError, ValueError) as e:
                    logger.debug("l1_skip_invalid_atom", error=str(e), item=item)
                    continue

            return atoms[: self.config.l1_max_atoms_per_extraction]

        except Exception as e:
            logger.error("l1_llm_call_failed", error=str(e))
            return []

    def _parse_json_array(self, text: str) -> list[dict[str, Any]]:
        """Parse JSON array from LLM response, handling markdown code blocks."""
        text = text.strip()

        # Strip markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # Try direct parse
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
            return []
        except json.JSONDecodeError:
            pass

        # Try to find JSON array in text
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("l1_json_parse_failed", text_preview=text[:200])
        return []

    async def _deduplicate(
        self,
        db: AsyncSession,
        atoms: list[MemoryAtom],
        session_id: str,
        user_id: str | None,
    ) -> list[MemoryAtom]:
        """Remove atoms that are too similar to existing ones."""
        # Text-based dedup (embedding-based can be layered on later)
        existing_stmt = select(MemoryAtomModel.content).where(
            MemoryAtomModel.is_active == True,  # noqa: E712
        )
        if user_id:
            existing_stmt = existing_stmt.where(MemoryAtomModel.user_id == user_id)
        else:
            existing_stmt = existing_stmt.where(
                MemoryAtomModel.session_id == session_id
            )

        existing_stmt = existing_stmt.order_by(
            MemoryAtomModel.created_at.desc()
        ).limit(100)

        result = await db.execute(existing_stmt)
        existing_contents = set(row[0].lower().strip() for row in result.all())

        deduplicated = []
        for atom in atoms:
            content_normalized = atom.content.lower().strip()
            # Simple substring/exact match dedup
            is_dup = any(
                content_normalized in existing or existing in content_normalized
                for existing in existing_contents
                if len(existing) > 20  # Only check meaningful entries
            )
            if not is_dup:
                deduplicated.append(atom)
                existing_contents.add(content_normalized)

        if len(deduplicated) < len(atoms):
            logger.info(
                "l1_dedup_removed",
                original=len(atoms),
                kept=len(deduplicated),
                removed=len(atoms) - len(deduplicated),
            )

        return deduplicated

    async def _save_atoms(
        self,
        db: AsyncSession,
        atoms: list[MemoryAtom],
        session_id: str,
        agent_id: str | None,
        user_id: str | None,
    ) -> int:
        """Persist extracted atoms to database."""
        saved = 0
        for atom in atoms:
            model = MemoryAtomModel(
                session_id=session_id,
                agent_id=agent_id,
                user_id=user_id,
                content=atom.content,
                atom_type=atom.atom_type,
                importance=atom.importance,
                source_message_ids=atom.source_message_ids,
                metadata_=atom.metadata,
            )
            db.add(model)
            saved += 1

        await db.flush()
        return saved

    async def get_atom_count(
        self,
        db: AsyncSession,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> int:
        """Get total active atom count for user/agent."""
        stmt = select(func.count(MemoryAtomModel.id)).where(
            MemoryAtomModel.is_active == True  # noqa: E712
        )
        if user_id:
            stmt = stmt.where(MemoryAtomModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(MemoryAtomModel.agent_id == agent_id)

        result = await db.execute(stmt)
        return result.scalar() or 0
