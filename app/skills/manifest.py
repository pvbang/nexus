"""
MekongAI Nexus - Skill Manifest Schema.

Defines SkillManifest dataclass for describing a single skill
in the Skills Hub marketplace. Compatible with SKILL.md frontmatter format.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# Allowed skill categories
ALLOWED_CATEGORIES: list[str] = [
    "research",
    "coding",
    "data",
    "communication",
    "creative",
    "utility",
    "devops",
    "security",
    "writing",
    "analysis",
]

# Regex patterns for validation
SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?$")


class SkillManifestError(ValueError):
    """Raised when a skill manifest fails validation."""


@dataclass
class SkillManifest:
    """
    Skill manifest — mô tả 1 skill trong Skills Hub.

    Compatible with SKILL.md YAML frontmatter format:
    ---
    skill_id: web-research
    name: Web Research
    description: Deep web research and summarization
    version: 1.0.0
    author: system
    category: research
    tags: [web, search, summarize]
    tools_required: [web_search, fetch_url]
    ---
    """

    skill_id: str
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "system"
    category: str = "utility"
    tags: list[str] = field(default_factory=list)
    system_prompt: str = ""
    tools_required: list[str] = field(default_factory=list)
    model_preference: Optional[str] = None
    temperature: Optional[float] = None
    examples: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    installed_at: Optional[str] = None
    source: str = "local"  # "local", "hub", "generated"

    def __post_init__(self) -> None:
        """Validate manifest fields after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate all manifest fields.

        Raises:
            SkillManifestError: If any field fails validation.
        """
        # skill_id: lowercase alphanumeric + hyphens
        if not self.skill_id or not SKILL_ID_PATTERN.match(self.skill_id):
            raise SkillManifestError(
                f"skill_id must be lowercase alphanumeric with hyphens, "
                f"got: '{self.skill_id}'"
            )

        if len(self.skill_id) > 64:
            raise SkillManifestError(
                f"skill_id must be <= 64 chars, got {len(self.skill_id)}"
            )

        # name: non-empty
        if not self.name or not self.name.strip():
            raise SkillManifestError("name must not be empty")

        # version: semver-like
        if not SEMVER_PATTERN.match(self.version):
            raise SkillManifestError(
                f"version must be semver-like (e.g. 1.0.0), got: '{self.version}'"
            )

        # category: must be in allowed list
        if self.category not in ALLOWED_CATEGORIES:
            raise SkillManifestError(
                f"category must be one of {ALLOWED_CATEGORIES}, "
                f"got: '{self.category}'"
            )

        # source: must be valid
        if self.source not in ("local", "hub", "generated"):
            raise SkillManifestError(
                f"source must be 'local', 'hub', or 'generated', "
                f"got: '{self.source}'"
            )

        # temperature: range check
        if self.temperature is not None and not (0.0 <= self.temperature <= 2.0):
            raise SkillManifestError(
                f"temperature must be between 0.0 and 2.0, got: {self.temperature}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize manifest to dictionary."""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "category": self.category,
            "tags": list(self.tags),
            "system_prompt": self.system_prompt,
            "tools_required": list(self.tools_required),
            "model_preference": self.model_preference,
            "temperature": self.temperature,
            "examples": list(self.examples),
            "enabled": self.enabled,
            "installed_at": self.installed_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillManifest":
        """Create a SkillManifest from a dictionary.

        Handles both full manifest dicts and SKILL.md frontmatter dicts
        (which may use 'name' as skill_id fallback).
        """
        # Resolve skill_id: explicit field, or fallback to 'name'
        skill_id = data.get("skill_id") or data.get("name", "")
        name = data.get("name", skill_id)

        # Handle tools_required from metadata.hermes.requires_toolsets (legacy format)
        tools_required = data.get("tools_required", [])
        if not tools_required:
            metadata = data.get("metadata", {})
            if isinstance(metadata, dict):
                hermes = metadata.get("hermes", {})
                if isinstance(hermes, dict):
                    tools_required = hermes.get("requires_toolsets", [])

        # Parse tags
        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Parse examples
        examples = data.get("examples", [])
        if not isinstance(examples, list):
            examples = []

        return cls(
            skill_id=skill_id,
            name=name,
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "system"),
            category=data.get("category", "utility"),
            tags=tags,
            system_prompt=data.get("system_prompt", ""),
            tools_required=tools_required,
            model_preference=data.get("model_preference"),
            temperature=data.get("temperature"),
            examples=examples,
            enabled=data.get("enabled", True),
            installed_at=data.get("installed_at"),
            source=data.get("source", "local"),
        )
