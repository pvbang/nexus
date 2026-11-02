"""
Nexus - Smart Document Chunker.

Uses LangChain text splitters for intelligent document chunking.
Supports semantic (markdown heading-aware), recursive, and token-based splitting.

Usage::

    from app.rag.chunker import SmartChunker
    chunks = SmartChunker().chunk_document(text, source="report.pdf")
"""

from __future__ import annotations

import re
from typing import Optional

import structlog

from app.rag.models import Chunk

logger = structlog.get_logger(__name__)


class SmartChunker:
    """Chunk documents using LangChain text splitters with smart strategy selection."""

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        min_chunk_size: int = 100,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    def chunk_document(
        self,
        text: str,
        source: str = "",
        doc_id: str = "",
        metadata: Optional[dict] = None,
    ) -> list[Chunk]:
        """Chunk a document using the best strategy for its content.

        Args:
            text: Full document text (markdown).
            source: Source filename or identifier.
            doc_id: Unique document ID.
            metadata: Extra metadata to attach to each chunk.

        Returns:
            List of Chunk objects with embeddings=None (caller must embed).
        """
        if not text or not text.strip():
            return []

        # Select strategy based on content
        if self._has_markdown_structure(text):
            raw_chunks = self._chunk_markdown(text)
        else:
            raw_chunks = self._chunk_recursive(text)

        # Filter too-small chunks and build Chunk objects
        total = len(raw_chunks)
        chunks: list[Chunk] = []
        char_pos = 0

        for idx, content in enumerate(raw_chunks):
            if len(content.strip()) < self.min_chunk_size:
                # Merge tiny chunks with previous if possible
                if chunks:
                    chunks[-1].content += "\n" + content
                    chunks[-1].char_end = char_pos + len(content)
                    continue
                elif idx < total - 1:
                    # Will be merged with next
                    continue

            section = self._extract_section_title(content)
            chunk = Chunk(
                content=content.strip(),
                doc_id=doc_id,
                chunk_index=len(chunks),
                total_chunks=0,  # Set after loop
                source=source,
                section_title=section,
                char_start=char_pos,
                char_end=char_pos + len(content),
                metadata=metadata or {},
            )
            chunks.append(chunk)
            char_pos += len(content)

        # Fix total_chunks
        for c in chunks:
            c.total_chunks = len(chunks)

        logger.info(
            "document_chunked",
            source=source,
            total_chars=len(text),
            chunk_count=len(chunks),
            strategy="markdown" if self._has_markdown_structure(text) else "recursive",
        )
        return chunks

    def _has_markdown_structure(self, text: str) -> bool:
        """Check if text has markdown headings."""
        heading_pattern = re.compile(r'^#{1,6}\s+\S', re.MULTILINE)
        headings = heading_pattern.findall(text)
        return len(headings) >= 2

    def _chunk_markdown(self, text: str) -> list[str]:
        """Chunk markdown text by headings with overlap."""
        try:
            from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

            # First split by headers
            headers_to_split = [
                ("#", "h1"),
                ("##", "h2"),
                ("###", "h3"),
                ("####", "h4"),
            ]
            md_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=headers_to_split,
                strip_headers=False,
            )
            md_docs = md_splitter.split_text(text)

            # Then recursively split large sections
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                length_function=len,
            )

            chunks: list[str] = []
            for doc in md_docs:
                section_text = doc.page_content
                # Prepend header context
                header_parts = []
                for key in ["h1", "h2", "h3", "h4"]:
                    if key in doc.metadata:
                        header_parts.append(doc.metadata[key])
                header_prefix = " > ".join(header_parts)
                if header_prefix:
                    section_text = f"**{header_prefix}**\n\n{section_text}"

                if len(section_text) > self.chunk_size:
                    sub_chunks = text_splitter.split_text(section_text)
                    chunks.extend(sub_chunks)
                else:
                    chunks.append(section_text)

            return chunks

        except ImportError:
            logger.warning("langchain_text_splitters_not_installed, falling back to recursive")
            return self._chunk_recursive(text)

    def _chunk_recursive(self, text: str) -> list[str]:
        """Recursive character text splitting with smart separators."""
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                separators=["\n\n", "\n", ". ", ", ", " ", ""],
                length_function=len,
            )
            return splitter.split_text(text)

        except ImportError:
            logger.warning("langchain_text_splitters_not_installed, using basic splitting")
            return self._chunk_basic(text)

    def _chunk_basic(self, text: str) -> list[str]:
        """Fallback: basic paragraph-based chunking without LangChain."""
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current = ""

        for para in paragraphs:
            if len(current) + len(para) > self.chunk_size:
                if current:
                    chunks.append(current)
                current = para
            else:
                current = f"{current}\n\n{para}" if current else para

        if current:
            chunks.append(current)

        return chunks

    @staticmethod
    def _extract_section_title(content: str) -> str:
        """Extract the first heading from chunk content."""
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("#").strip()
            if line.startswith("**") and line.endswith("**"):
                return line.strip("*").strip()
        return ""
