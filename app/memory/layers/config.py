"""Memory layers configuration with token budget allocation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LayerTokenBudget:
    """Token budget allocation per layer for recall injection."""

    l3_persona: int = 800    # Persona summary (highest priority)
    l2_scenes: int = 1200    # Relevant scene blocks
    l1_atoms: int = 1000     # Specific facts
    reserve: int = 1000      # Buffer for formatting/separators

    @property
    def total(self) -> int:
        return self.l3_persona + self.l2_scenes + self.l1_atoms + self.reserve


@dataclass
class MemoryLayersConfig:
    """Configuration for the memory layers system."""

    enabled: bool = True

    # L1 Extraction triggers
    l1_every_n_turns: int = 5
    l1_idle_timeout_seconds: int = 600
    l1_max_atoms_per_extraction: int = 10
    l1_warmup_enabled: bool = True  # Start from 1, double each time

    # L2 Scene aggregation
    l2_min_interval_seconds: int = 900
    l2_max_interval_seconds: int = 3600
    l2_delay_after_l1_seconds: int = 90
    l2_max_scenes: int = 15

    # L3 Persona synthesis
    l3_trigger_every_n_atoms: int = 50
    l3_backup_count: int = 3

    # Recall settings
    recall_max_results: int = 5
    recall_score_threshold: float = 0.3
    recall_strategy: str = "hybrid"  # "keyword", "embedding", "hybrid"
    recall_timeout_ms: int = 3000

    # Token budgets
    token_budget: LayerTokenBudget = field(default_factory=LayerTokenBudget)

    # Deduplication
    dedup_similarity_threshold: float = 0.85

    @classmethod
    def from_app_config(cls, config) -> MemoryLayersConfig:
        """Create from app Settings object."""
        return cls(
            enabled=getattr(config, "memory_layers_enabled", True),
            l1_every_n_turns=getattr(config, "memory_l1_every_n_turns", 5),
            l1_idle_timeout_seconds=getattr(config, "memory_l1_idle_timeout_seconds", 600),
            l2_min_interval_seconds=getattr(config, "memory_l2_min_interval_seconds", 900),
            l3_trigger_every_n_atoms=getattr(config, "memory_l3_trigger_every_n_atoms", 50),
            recall_max_results=getattr(config, "memory_recall_max_results", 5),
            recall_strategy=getattr(config, "memory_recall_strategy", "hybrid"),
            recall_timeout_ms=getattr(config, "memory_recall_timeout_ms", 3000),
            dedup_similarity_threshold=getattr(config, "memory_dedup_threshold", 0.85),
            token_budget=LayerTokenBudget(
                l3_persona=getattr(config, "memory_budget_l3_tokens", 800),
                l2_scenes=getattr(config, "memory_budget_l2_tokens", 1200),
                l1_atoms=getattr(config, "memory_budget_l1_tokens", 1000),
            ),
        )
