"""
Nexus - Skills Hub Marketplace.

Discover, install, manage, search, import/export skills.
Uses SKILL.md format with YAML frontmatter for skill definitions.
"""

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

from app.skills import parse_frontmatter
from app.skills.manifest import (
    ALLOWED_CATEGORIES,
    SkillManifest,
    SkillManifestError,
)
from app.skills.security_scanner import scan_skill_content

logger = structlog.get_logger(__name__)

# Default skills directory (absolute path from project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SKILLS_DIR = _PROJECT_ROOT / "skills"


class SkillsHubMarketplace:
    """
    Skills Hub — discover, install, manage skills marketplace.

    Scans skills directories for SKILL.md files, parses YAML frontmatter
    into SkillManifest objects, and provides CRUD + search operations.
    """

    def __init__(self, skills_dir: Path = DEFAULT_SKILLS_DIR) -> None:
        self._skills_dir = skills_dir
        self._cache: dict[str, SkillManifest] = {}
        self._state_file = skills_dir / ".skills_state.json"

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> list[SkillManifest]:
        """Scan skills_dir for SKILL.md files, parse frontmatter → SkillManifest.

        Returns list of all discovered skill manifests.
        """
        self._cache.clear()
        manifests: list[SkillManifest] = []

        if not self._skills_dir.exists():
            logger.debug("skills_dir_not_found", path=str(self._skills_dir))
            return manifests

        for child in sorted(self._skills_dir.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue

            skill_file = child / "SKILL.md"
            if not skill_file.exists():
                continue

            try:
                manifest = self._parse_skill_file(skill_file)
                # Load persisted state (enabled/disabled)
                state = self._load_skill_state(manifest.skill_id)
                if state is not None:
                    manifest.enabled = state.get("enabled", True)
                    manifest.installed_at = state.get("installed_at")

                self._cache[manifest.skill_id] = manifest
                manifests.append(manifest)
                logger.debug("skill_discovered", skill_id=manifest.skill_id)
            except (SkillManifestError, Exception) as exc:
                logger.warning(
                    "skill_parse_failed",
                    path=str(skill_file),
                    error=str(exc),
                )

        logger.info("skills_discovered", count=len(manifests))
        return manifests

    def _parse_skill_file(self, skill_file: Path) -> SkillManifest:
        """Parse a SKILL.md file into a SkillManifest."""
        content = skill_file.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)

        # Extract system prompt from body
        system_prompt = self._extract_system_prompt(body)

        # Extract examples from body
        examples = self._extract_examples(body)

        # Build manifest data from frontmatter + parsed body
        data = dict(frontmatter)
        data.setdefault("skill_id", skill_file.parent.name)
        data.setdefault("name", skill_file.parent.name)

        if system_prompt:
            data.setdefault("system_prompt", system_prompt)
        elif body.strip():
            # Fallback: use entire body as system prompt
            data.setdefault("system_prompt", body.strip())

        if examples:
            data.setdefault("examples", examples)

        data.setdefault("source", "local")

        return SkillManifest.from_dict(data)

    def _extract_system_prompt(self, body: str) -> str:
        """Extract system prompt section from markdown body."""
        lines = body.split("\n")
        in_prompt = False
        prompt_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if re.match(r"^#{1,3}\s+system\s+prompt", stripped, re.IGNORECASE):
                in_prompt = True
                continue
            if in_prompt and re.match(r"^#{1,3}\s+", stripped):
                # Hit next section header
                break
            if in_prompt:
                prompt_lines.append(line)

        result = "\n".join(prompt_lines).strip()
        return result

    def _extract_examples(self, body: str) -> list[dict[str, str]]:
        """Extract examples section from markdown body."""
        lines = body.split("\n")
        in_examples = False
        examples: list[dict[str, str]] = []
        current_input = ""
        current_output = ""
        collecting: Optional[str] = None  # "input" or "output"

        for line in lines:
            stripped = line.strip()

            if re.match(r"^#{1,3}\s+examples?$", stripped, re.IGNORECASE):
                in_examples = True
                continue

            if not in_examples:
                continue

            # Stop at next top-level section (not ### Example N)
            if re.match(r"^#{1,2}\s+(?!example)", stripped, re.IGNORECASE):
                break

            if stripped.lower().startswith("input:"):
                # Save previous example if exists
                if current_input and current_output:
                    examples.append({
                        "input": current_input.strip(),
                        "output": current_output.strip(),
                    })
                current_input = stripped[len("input:"):].strip()
                current_output = ""
                collecting = "input"
            elif stripped.lower().startswith("output:"):
                current_output = stripped[len("output:"):].strip()
                collecting = "output"
            elif collecting == "input" and stripped:
                current_input += " " + stripped
            elif collecting == "output" and stripped:
                current_output += " " + stripped

        # Save last example
        if current_input and current_output:
            examples.append({
                "input": current_input.strip(),
                "output": current_output.strip(),
            })

        return examples

    # ------------------------------------------------------------------
    # Install / Uninstall
    # ------------------------------------------------------------------

    def install(self, manifest: SkillManifest) -> bool:
        """Save skill to skills_dir/{skill_id}/SKILL.md.

        Returns True if installation succeeded.
        """
        try:
            skill_dir = self._skills_dir / manifest.skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)

            # Generate SKILL.md content
            content = self._generate_skill_md(manifest)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(content, encoding="utf-8")

            # Set installed timestamp
            manifest.installed_at = datetime.now(timezone.utc).isoformat()
            self._save_skill_state(manifest.skill_id, {
                "enabled": manifest.enabled,
                "installed_at": manifest.installed_at,
            })

            # Update cache
            self._cache[manifest.skill_id] = manifest

            logger.info("skill_installed", skill_id=manifest.skill_id)
            return True
        except Exception as exc:
            logger.error("skill_install_failed", skill_id=manifest.skill_id, error=str(exc))
            return False

    def uninstall(self, skill_id: str) -> bool:
        """Remove skill directory.

        Returns True if uninstallation succeeded.
        """
        skill_dir = self._skills_dir / skill_id
        if not skill_dir.exists():
            logger.warning("skill_not_found_for_uninstall", skill_id=skill_id)
            return False

        try:
            shutil.rmtree(skill_dir)
            self._cache.pop(skill_id, None)
            self._remove_skill_state(skill_id)
            logger.info("skill_uninstalled", skill_id=skill_id)
            return True
        except Exception as exc:
            logger.error("skill_uninstall_failed", skill_id=skill_id, error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Get / Search
    # ------------------------------------------------------------------

    def get(self, skill_id: str) -> Optional[SkillManifest]:
        """Load a single skill by ID."""
        if skill_id in self._cache:
            return self._cache[skill_id]

        # Try loading from disk
        skill_file = self._skills_dir / skill_id / "SKILL.md"
        if not skill_file.exists():
            return None

        try:
            manifest = self._parse_skill_file(skill_file)
            state = self._load_skill_state(skill_id)
            if state is not None:
                manifest.enabled = state.get("enabled", True)
                manifest.installed_at = state.get("installed_at")
            self._cache[skill_id] = manifest
            return manifest
        except Exception:
            return None

    def search(
        self,
        query: str,
        category: Optional[str] = None,
    ) -> list[SkillManifest]:
        """Search skills by name/description/tags using fuzzy keyword matching.

        Args:
            query: Search query string.
            category: Optional category filter.

        Returns:
            List of matching skill manifests, sorted by relevance.
        """
        if not self._cache:
            self.discover()

        query_lower = query.lower().strip()
        query_words = set(query_lower.split())
        results: list[tuple[float, SkillManifest]] = []

        for manifest in self._cache.values():
            # Filter by category if specified
            if category and manifest.category != category:
                continue

            # Calculate relevance score
            score = self._calculate_relevance(manifest, query_lower, query_words)
            if score > 0:
                results.append((score, manifest))

        # Sort by relevance (descending)
        results.sort(key=lambda x: x[0], reverse=True)
        return [manifest for _, manifest in results]

    def _calculate_relevance(
        self,
        manifest: SkillManifest,
        query_lower: str,
        query_words: set[str],
    ) -> float:
        """Calculate relevance score for a skill against a search query."""
        score = 0.0

        # Exact skill_id match
        if query_lower == manifest.skill_id:
            score += 10.0

        # skill_id contains query
        if query_lower in manifest.skill_id:
            score += 5.0

        # Name match (case-insensitive)
        name_lower = manifest.name.lower()
        if query_lower in name_lower:
            score += 8.0
        elif any(w in name_lower for w in query_words):
            score += 4.0

        # Description match
        desc_lower = manifest.description.lower()
        matching_words = sum(1 for w in query_words if w in desc_lower)
        if matching_words > 0:
            score += 2.0 * matching_words

        # Tags match
        tags_lower = [t.lower() for t in manifest.tags]
        for word in query_words:
            if word in tags_lower:
                score += 3.0
            elif any(word in tag for tag in tags_lower):
                score += 1.5

        return score

    # ------------------------------------------------------------------
    # Enable / Disable
    # ------------------------------------------------------------------

    def enable(self, skill_id: str) -> bool:
        """Enable a skill."""
        manifest = self.get(skill_id)
        if manifest is None:
            return False
        manifest.enabled = True
        self._save_skill_state(skill_id, {
            "enabled": True,
            "installed_at": manifest.installed_at,
        })
        logger.info("skill_enabled", skill_id=skill_id)
        return True

    def disable(self, skill_id: str) -> bool:
        """Disable a skill."""
        manifest = self.get(skill_id)
        if manifest is None:
            return False
        manifest.enabled = False
        self._save_skill_state(skill_id, {
            "enabled": False,
            "installed_at": manifest.installed_at,
        })
        logger.info("skill_disabled", skill_id=skill_id)
        return True

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------

    def export(self, skill_id: str) -> Optional[str]:
        """Export skill as SKILL.md content (shareable).

        Returns the full SKILL.md content string, or None if not found.
        """
        manifest = self.get(skill_id)
        if manifest is None:
            return None
        return self._generate_skill_md(manifest)

    async def import_from_text(self, content: str) -> SkillManifest:
        """Parse SKILL.md text content → install as new skill.

        The content is first screened by a fail-closed LLM security scan
        (prompt-injection / exfiltration / role-override). Blocked content
        is rejected before any disk write.

        Args:
            content: Raw SKILL.md content with YAML frontmatter.

        Returns:
            Installed SkillManifest.

        Raises:
            ValueError: If the security scan blocks the content.
            SkillManifestError: If content is invalid.
        """
        verdict = await scan_skill_content(content, location="SKILL.md")
        if verdict.is_blocked:
            logger.warning("skill_import_blocked", reason=verdict.reason)
            raise ValueError(f"Skill import blocked by security scan: {verdict.reason}")

        frontmatter, body = parse_frontmatter(content)

        if not frontmatter:
            raise SkillManifestError("No YAML frontmatter found in content")

        # Build manifest data
        data = dict(frontmatter)
        system_prompt = self._extract_system_prompt(body)
        examples = self._extract_examples(body)

        if system_prompt:
            data.setdefault("system_prompt", system_prompt)
        elif body.strip():
            data.setdefault("system_prompt", body.strip())

        if examples:
            data.setdefault("examples", examples)

        data.setdefault("source", "hub")

        manifest = SkillManifest.from_dict(data)
        manifest.installed_at = datetime.now(timezone.utc).isoformat()

        success = self.install(manifest)
        if not success:
            raise SkillManifestError(f"Failed to install skill: {manifest.skill_id}")

        return manifest

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return hub statistics."""
        if not self._cache:
            self.discover()

        skills = list(self._cache.values())
        by_category: dict[str, int] = {}
        enabled_count = 0
        disabled_count = 0
        by_source: dict[str, int] = {}

        for s in skills:
            by_category[s.category] = by_category.get(s.category, 0) + 1
            by_source[s.source] = by_source.get(s.source, 0) + 1
            if s.enabled:
                enabled_count += 1
            else:
                disabled_count += 1

        return {
            "total": len(skills),
            "enabled": enabled_count,
            "disabled": disabled_count,
            "by_category": by_category,
            "by_source": by_source,
        }

    def list_categories(self) -> list[dict[str, Any]]:
        """List all categories with skill counts."""
        if not self._cache:
            self.discover()

        counts: dict[str, int] = {}
        for s in self._cache.values():
            counts[s.category] = counts.get(s.category, 0) + 1

        categories: list[dict[str, Any]] = []
        for cat in ALLOWED_CATEGORIES:
            categories.append({
                "name": cat,
                "count": counts.get(cat, 0),
            })

        return categories

    # ------------------------------------------------------------------
    # SKILL.md generation
    # ------------------------------------------------------------------

    def _generate_skill_md(self, manifest: SkillManifest) -> str:
        """Generate SKILL.md content from a SkillManifest."""
        lines: list[str] = []

        # YAML frontmatter
        lines.append("---")
        lines.append(f"skill_id: {manifest.skill_id}")
        lines.append(f"name: {manifest.name}")
        lines.append(f'description: "{manifest.description}"')
        lines.append(f"version: {manifest.version}")
        lines.append(f"author: {manifest.author}")
        lines.append(f"category: {manifest.category}")

        if manifest.tags:
            tags_str = ", ".join(manifest.tags)
            lines.append(f"tags: [{tags_str}]")

        if manifest.tools_required:
            tools_str = ", ".join(manifest.tools_required)
            lines.append(f"tools_required: [{tools_str}]")

        if manifest.model_preference:
            lines.append(f"model_preference: {manifest.model_preference}")

        if manifest.temperature is not None:
            lines.append(f"temperature: {manifest.temperature}")

        lines.append(f"source: {manifest.source}")
        lines.append("---")
        lines.append("")

        # Title
        lines.append(f"# {manifest.name}")
        lines.append("")

        # System Prompt section
        if manifest.system_prompt:
            lines.append("## System Prompt")
            lines.append("")
            lines.append(manifest.system_prompt)
            lines.append("")

        # Examples section
        if manifest.examples:
            lines.append("## Examples")
            lines.append("")
            for i, example in enumerate(manifest.examples, 1):
                lines.append(f"### Example {i}")
                if "input" in example:
                    lines.append(f"Input: {example['input']}")
                if "output" in example:
                    lines.append(f"Output: {example['output']}")
                lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # State persistence (JSON file per-hub)
    # ------------------------------------------------------------------

    def _load_all_states(self) -> dict[str, Any]:
        """Load all skill states from the state file."""
        if not self._state_file.exists():
            return {}
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_all_states(self, states: dict[str, Any]) -> None:
        """Save all skill states to the state file."""
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(states, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_skill_state(self, skill_id: str) -> Optional[dict[str, Any]]:
        """Load state for a single skill."""
        states = self._load_all_states()
        return states.get(skill_id)

    def _save_skill_state(self, skill_id: str, state: dict[str, Any]) -> None:
        """Save state for a single skill."""
        states = self._load_all_states()
        states[skill_id] = state
        self._save_all_states(states)

    def _remove_skill_state(self, skill_id: str) -> None:
        """Remove persisted state for a skill."""
        states = self._load_all_states()
        states.pop(skill_id, None)
        self._save_all_states(states)


# ------------------------------------------------------------------
# Module singleton
# ------------------------------------------------------------------

_marketplace: Optional[SkillsHubMarketplace] = None


def get_skills_marketplace() -> SkillsHubMarketplace:
    """Get or create the global SkillsHubMarketplace singleton."""
    global _marketplace
    if _marketplace is None:
        _marketplace = SkillsHubMarketplace()
        _marketplace.discover()
    return _marketplace
