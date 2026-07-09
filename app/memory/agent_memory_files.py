"""
MekongAI Nexus - Agent Memory Files (Claude-code memdir pattern).

Mỗi agent có một thư mục MEMORY riêng:
- MEMORY.md: ghi chú, preferences, learned patterns
- history.jsonl: conversation history archive
- skills/: skill files agent đã học

Pattern từ claude-code memdir/memdir.ts:
- Persistent across sessions
- Auto-loaded vào system prompt
- Agent tự ghi/đọc qua tools
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

AGENT_MEMORY_BASE = Path("workspace_data/.agent_memories")
AGENT_MEMORY_BASE.mkdir(parents=True, exist_ok=True)


class AgentMemoryFiles:
    """
    File-based persistent memory per agent.

    Claude-code pattern: mỗi agent có MEMORY.md + history.
    """

    def __init__(self, agent_id: str, agent_name: str = ""):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.base_dir = AGENT_MEMORY_BASE / agent_id
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.base_dir / "MEMORY.md"
        self.history_file = self.base_dir / "history.jsonl"

    def load_memory_prompt(self) -> str:
        """Load MEMORY.md content để inject vào system prompt."""
        if not self.memory_file.exists():
            return ""

        content = self.memory_file.read_text(encoding="utf-8")
        # Truncate nếu quá dài (claude-code MAX_ENTRYPOINT_LINES = 200)
        lines = content.strip().split("\n")
        if len(lines) > 200:
            content = "\n".join(lines[:200])
            content += "\n\n[... truncated, memory file too large ...]"

        return f"\n## Agent Memory\n{content}\n"

    def save_memory(self, content: str):
        """Ghi/cập nhật MEMORY.md."""
        self.memory_file.write_text(content, encoding="utf-8")
        logger.debug("agent_memory_saved", agent_id=self.agent_id)

    def append_memory(self, note: str):
        """Thêm ghi chú vào MEMORY.md."""
        existing = ""
        if self.memory_file.exists():
            existing = self.memory_file.read_text(encoding="utf-8")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n- [{timestamp}] {note}"
        self.memory_file.write_text(existing + entry, encoding="utf-8")

    def append_history(self, role: str, content: str, metadata: Optional[dict] = None):
        """Append vào history.jsonl (archived conversations)."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "role": role,
            "content": content[:2000],  # Limit per entry
            "metadata": metadata or {},
        }
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load_recent_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Load N entries gần nhất từ history."""
        if not self.history_file.exists():
            return []

        lines = self.history_file.read_text(encoding="utf-8").strip().split("\n")
        recent = lines[-limit:]
        entries = []
        for line in recent:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def get_stats(self) -> dict[str, Any]:
        """Thống kê memory files."""
        memory_size = self.memory_file.stat().st_size if self.memory_file.exists() else 0
        history_lines = 0
        if self.history_file.exists():
            history_lines = sum(1 for _ in open(self.history_file))

        return {
            "agent_id": self.agent_id,
            "memory_file_size": memory_size,
            "history_entries": history_lines,
        }
