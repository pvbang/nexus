"""Long message processor - handles chunked reading of large user inputs."""

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ChunkResult:
    """Result from processing a single chunk."""

    chunk_index: int
    total_chunks: int
    summary: str
    key_points: list[str]


@dataclass
class LongMessageResult:
    """Final result from processing a long message."""

    strategy_used: str  # "passthrough" | "map_reduce" | "sequential"
    processed_message: str  # The condensed/processed message to send to LLM
    original_length: int  # Original char count
    was_chunked: bool
    chunks_processed: int
    token_estimate: int


class LongMessageProcessor:
    """Processes long user messages by chunking and summarizing."""

    def __init__(self, settings, llm_client):
        self.settings = settings
        self.llm_client = llm_client
        self._char_threshold = getattr(settings, "long_message_char_threshold", 80_000)
        self._chunk_size_tokens = getattr(settings, "long_message_chunk_size_tokens", 30_000)
        self._chunk_overlap_tokens = getattr(settings, "long_message_chunk_overlap_tokens", 500)
        self._max_chunks = getattr(settings, "long_message_max_chunks", 20)
        self._strategy = getattr(settings, "long_message_strategy", "auto")

    def needs_chunking(self, message: str) -> bool:
        """Check if message exceeds threshold and needs chunked processing."""
        return len(message) > self._char_threshold

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count using heuristic.

        ~3.5 chars/token for English, ~1.5 for Vietnamese/CJK.
        Use weighted average based on content detection.
        """
        # Count CJK/Vietnamese characters (multi-byte)
        cjk_pattern = re.compile(r"[\u0080-\uffff]")
        cjk_chars = len(cjk_pattern.findall(text))
        ascii_chars = len(text) - cjk_chars

        # Weighted estimate
        tokens = (ascii_chars / 3.5) + (cjk_chars / 1.5)
        return int(tokens)

    def _split_into_chunks(self, text: str) -> list[str]:
        """Split text into semantic chunks at paragraph boundaries."""
        # Target chars per chunk (approximate from token target)
        chars_per_chunk = self._chunk_size_tokens * 3  # ~3 chars/token average
        overlap_chars = self._chunk_overlap_tokens * 3

        # Split by paragraphs first
        paragraphs = re.split(r"\n\s*\n", text)

        chunks: list[str] = []
        current_chunk = ""

        for para in paragraphs:
            # If adding this paragraph exceeds chunk size, start new chunk
            if len(current_chunk) + len(para) > chars_per_chunk and current_chunk:
                chunks.append(current_chunk.strip())
                # Keep overlap from end of previous chunk
                if overlap_chars > 0 and len(current_chunk) > overlap_chars:
                    current_chunk = current_chunk[-overlap_chars:] + "\n\n" + para
                else:
                    current_chunk = para
            else:
                current_chunk = current_chunk + "\n\n" + para if current_chunk else para

        # Don't forget the last chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        # Enforce max_chunks limit
        if len(chunks) > self._max_chunks:
            logger.warning(
                "Text split into %d chunks, exceeds max %d. Merging smaller chunks.",
                len(chunks),
                self._max_chunks,
            )
            # Merge chunks to fit within limit
            merged: list[str] = []
            merge_size = len(chunks) // self._max_chunks + 1
            for i in range(0, len(chunks), merge_size):
                merged.append("\n\n".join(chunks[i : i + merge_size]))
            chunks = merged[: self._max_chunks]

        return chunks

    async def process(
        self,
        message: str,
        *,
        session_id: Optional[str] = None,
        progress_callback=None,
    ) -> LongMessageResult:
        """Process a long message, chunking if necessary.

        Args:
            message: The user's full message
            session_id: Optional session ID for context
            progress_callback: Optional async callback(phase, current, total) for progress updates

        Returns:
            LongMessageResult with processed/condensed message
        """
        original_length = len(message)
        token_estimate = self.estimate_tokens(message)

        # Short message - passthrough
        if not self.needs_chunking(message):
            return LongMessageResult(
                strategy_used="passthrough",
                processed_message=message,
                original_length=original_length,
                was_chunked=False,
                chunks_processed=0,
                token_estimate=token_estimate,
            )

        logger.info(
            "Long message detected: %d chars, ~%d tokens. Initiating chunked processing.",
            original_length,
            token_estimate,
        )

        # Determine strategy
        strategy = self._strategy
        if strategy == "auto":
            # Use map_reduce for very long texts, sequential for moderate
            strategy = "map_reduce" if token_estimate > 50_000 else "sequential"

        # Split into chunks
        chunks = self._split_into_chunks(message)
        total_chunks = len(chunks)

        logger.info("Split into %d chunks, using strategy: %s", total_chunks, strategy)

        if strategy == "map_reduce":
            result = await self._map_reduce(chunks, message, progress_callback)
        else:
            result = await self._sequential_read(chunks, message, progress_callback)

        return LongMessageResult(
            strategy_used=strategy,
            processed_message=result,
            original_length=original_length,
            was_chunked=True,
            chunks_processed=total_chunks,
            token_estimate=token_estimate,
        )

    async def _map_reduce(
        self, chunks: list[str], original_message: str, progress_callback=None
    ) -> str:
        """Map-Reduce strategy: summarize each chunk, then combine."""
        summaries: list[str] = []

        for i, chunk in enumerate(chunks):
            if progress_callback:
                await progress_callback("summarizing", i + 1, len(chunks))

            summary = await self._summarize_chunk(chunk, i, len(chunks))
            summaries.append(summary)

        if progress_callback:
            await progress_callback("combining", len(chunks), len(chunks))

        # Combine summaries into final condensed message
        combined = "\n\n---\n\n".join(
            f"[Phần {i + 1}/{len(summaries)}]\n{s}" for i, s in enumerate(summaries)
        )

        # Create final processed message with context
        processed = (
            f"[Văn bản gốc: {len(original_message):,} ký tự, được chia thành {len(chunks)} phần]\n\n"
            f"Nội dung đầy đủ đã được đọc và tóm tắt từng phần:\n\n{combined}"
        )

        return processed

    async def _sequential_read(
        self, chunks: list[str], original_message: str, progress_callback=None
    ) -> str:
        """Sequential strategy: read chunks in order, accumulate understanding."""
        accumulated_understanding = ""

        for i, chunk in enumerate(chunks):
            if progress_callback:
                await progress_callback("reading", i + 1, len(chunks))

            # Ask LLM to read this chunk and update understanding
            prompt = f"Bạn đang đọc một văn bản dài, phần {i + 1}/{len(chunks)}.\n\n"
            if accumulated_understanding:
                prompt += f"Hiểu biết tích lũy từ các phần trước:\n{accumulated_understanding}\n\n"
            prompt += (
                f"Nội dung phần này:\n{chunk}\n\n"
                f"Hãy cập nhật và tóm tắt hiểu biết của bạn về toàn bộ văn bản "
                f"(bao gồm cả phần trước và phần hiện tại). Giữ lại tất cả thông tin quan trọng."
            )

            response = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                model=None,  # Use default model
                max_tokens=2000,
            )
            accumulated_understanding = (
                response.get("content", "") if isinstance(response, dict) else str(response)
            )

        if progress_callback:
            await progress_callback("finalizing", len(chunks), len(chunks))

        processed = (
            f"[Văn bản gốc: {len(original_message):,} ký tự, đã đọc tuần tự {len(chunks)} phần]\n\n"
            f"Nội dung đầy đủ đã được đọc và hiểu:\n\n{accumulated_understanding}"
        )

        return processed

    async def _summarize_chunk(self, chunk: str, index: int, total: int) -> str:
        """Summarize a single chunk using LLM."""
        prompt = (
            f"Tóm tắt nội dung sau đây (phần {index + 1}/{total} của một văn bản dài). "
            f"Giữ lại TẤT CẢ thông tin quan trọng, số liệu, tên riêng, và chi tiết cụ thể. "
            f"Không bỏ sót ý chính nào.\n\n"
            f"Nội dung:\n{chunk}"
        )

        response = await self.llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=None,
            max_tokens=2000,
        )

        if isinstance(response, dict):
            return response.get("content", "")
        return str(response)
