"""
Nexus - Skills Hub (Hermes-agent inspired).

Self-improving skills system with:
- YAML frontmatter metadata (name, description, platforms, config)
- Skill discovery from filesystem (skills/ directory)
- Skill invocation via /command slash commands
- Conditional activation (requires_toolsets, fallback_for_toolsets)
- External skill directories
- Config variable injection

Inspired by: hermes-agent/agent/skill_utils.py + skill_commands.py + tools/skills_hub.py
"""

import os
import re
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Default skills directory (absolute path from project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = _PROJECT_ROOT / "skills"
EXCLUDED_DIRS = frozenset((".git", ".github", "__pycache__", "node_modules"))


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown string.

    Returns (frontmatter_dict, remaining_body).
    """
    frontmatter: dict[str, Any] = {}
    body = content

    if not content.startswith("---"):
        return frontmatter, body

    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return frontmatter, body

    yaml_content = content[3: end_match.start() + 3]
    body = content[end_match.end() + 3:]

    try:
        import yaml
        parsed = yaml.safe_load(yaml_content)
        if isinstance(parsed, dict):
            frontmatter = parsed
    except ImportError:
        # Fallback: simple key:value parsing
        for line in yaml_content.strip().split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()
    except Exception:
        for line in yaml_content.strip().split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()

    return frontmatter, body


class SkillInfo:
    """Metadata for a discovered skill."""

    def __init__(
        self,
        name: str,
        description: str = "",
        content: str = "",
        path: Optional[Path] = None,
        frontmatter: Optional[dict] = None,
        supporting_files: Optional[list[str]] = None,
    ):
        self.name = name
        self.description = description
        self.content = content
        self.path = path
        self.frontmatter = frontmatter or {}
        self.supporting_files = supporting_files or []

    @property
    def command_name(self) -> str:
        """Generate slash command name from skill name."""
        slug = self.name.lower().replace(" ", "-").replace("_", "-")
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        slug = re.sub(r"-{2,}", "-", slug).strip("-")
        return f"/{slug}" if slug else f"/skill-{id(self)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description[:200],
            "command": self.command_name,
            "path": str(self.path) if self.path else None,
            "has_supporting_files": len(self.supporting_files) > 0,
            "frontmatter": self.frontmatter,
        }


class SkillsHub:
    """
    Manages skill discovery, loading, and invocation.

    Skills are markdown files (SKILL.md) in the skills directory with
    optional YAML frontmatter for metadata.
    """

    def __init__(self, skills_dirs: Optional[list[Path]] = None):
        self._skills_dirs = skills_dirs or [SKILLS_DIR]
        self._skills: dict[str, SkillInfo] = {}
        self._commands: dict[str, str] = {}  # /command -> skill_name
        self._disabled: set[str] = set()

    def scan(self) -> dict[str, SkillInfo]:
        """Scan all skills directories and discover available skills."""
        self._skills.clear()
        self._commands.clear()

        for skills_dir in self._skills_dirs:
            if not skills_dir.exists():
                continue
            self._scan_directory(skills_dir)

        logger.info("skills_scanned", count=len(self._skills))
        return dict(self._skills)

    def _scan_directory(self, skills_dir: Path) -> None:
        """Scan a single skills directory."""
        for root, dirs, files in os.walk(skills_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            if "SKILL.md" in files:
                skill_path = Path(root) / "SKILL.md"
                try:
                    self._load_skill(skill_path, skills_dir)
                except Exception as e:
                    logger.warning("skill_load_failed", path=str(skill_path), error=str(e))

    def _load_skill(self, skill_path: Path, base_dir: Path) -> None:
        """Load a single skill from its SKILL.md file."""
        content = skill_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)

        name = frontmatter.get("name", skill_path.parent.name)
        if name in self._disabled:
            return

        description = frontmatter.get("description", "")
        if not description:
            for line in body.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    description = line[:80]
                    break

        # Discover supporting files
        supporting = []
        skill_dir = skill_path.parent
        for subdir_name in ("references", "templates", "scripts", "assets"):
            subdir = skill_dir / subdir_name
            if subdir.exists():
                for f in sorted(subdir.rglob("*")):
                    if f.is_file():
                        supporting.append(str(f.relative_to(skill_dir)))

        skill = SkillInfo(
            name=name,
            description=description,
            content=body,
            path=skill_path,
            frontmatter=frontmatter,
            supporting_files=supporting,
        )

        self._skills[name] = skill
        self._commands[skill.command_name] = name

    def get_skill(self, name: str) -> Optional[SkillInfo]:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_skill_by_command(self, command: str) -> Optional[SkillInfo]:
        """Get a skill by its slash command."""
        name = self._commands.get(command)
        if name:
            return self._skills.get(name)
        # Try with underscores -> hyphens
        normalized = f"/{command.lstrip('/').replace('_', '-')}"
        name = self._commands.get(normalized)
        return self._skills.get(name) if name else None

    def list_skills(self) -> list[dict[str, Any]]:
        """List all discovered skills."""
        return [s.to_dict() for s in sorted(self._skills.values(), key=lambda s: s.name)]

    def list_commands(self) -> dict[str, dict[str, Any]]:
        """Return mapping of /command -> skill info."""
        result = {}
        for cmd, name in self._commands.items():
            skill = self._skills.get(name)
            if skill:
                result[cmd] = {
                    "name": name,
                    "description": skill.description,
                }
        return result

    def invoke_skill(
        self,
        skill_name: str,
        user_instruction: str = "",
        task_id: Optional[str] = None,
    ) -> Optional[str]:
        """Build the invocation message for a skill.

        Returns the formatted message content or None if skill not found.
        """
        skill = self.get_skill(skill_name)
        if not skill:
            # Try by command
            skill = self.get_skill_by_command(skill_name)
        if not skill:
            return None

        parts = [
            f'[SYSTEM: The user has invoked the "{skill.name}" skill. '
            "Follow its instructions below.]",
            "",
            skill.content.strip(),
        ]

        if skill.supporting_files:
            parts.append("")
            parts.append("[This skill has supporting files:]")
            for sf in skill.supporting_files:
                parts.append(f"- {sf}")

        if user_instruction:
            parts.append("")
            parts.append(f"User instruction: {user_instruction}")

        return "\n".join(parts)

    def disable_skill(self, name: str) -> None:
        """Disable a skill by name."""
        self._disabled.add(name)
        self._skills.pop(name, None)

    def enable_skill(self, name: str) -> None:
        """Re-enable a skill."""
        self._disabled.discard(name)

    def add_skills_dir(self, path: Path) -> None:
        """Add an external skills directory."""
        if path not in self._skills_dirs:
            self._skills_dirs.append(path)


# Module-level singleton
_hub: Optional[SkillsHub] = None


def get_skills_hub() -> SkillsHub:
    """Get or create the global skills hub."""
    global _hub
    if _hub is None:
        _hub = SkillsHub()
        _hub.scan()
    return _hub
