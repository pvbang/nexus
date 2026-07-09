"""Tests for per-role doctrine scoping in behaviors (audit M4: prompt slimming).

Root cause: get_behaviors_section injected the ~5K-char PRODUCTION_EXCELLENCE
build checklist (web app / API / devops / data per-task-type) into EVERY agent,
including judges/researchers/writers/analysts where it's pure noise — costing
~1.3K tokens per spawn for nothing.

These pin the scoping: PRODUCTION_EXCELLENCE only for build-capable roles
(builder/security/core); the two genuinely universal doctrines (failure recovery,
capability acquisition) stay on for ALL agents — they're quality, not bloat.
"""

from __future__ import annotations

from app.core.prompt_sections.behaviors import get_behaviors_section

_PROD_MARKER = "PRODUCTION EXCELLENCE MANDATE"
_FAILURE_MARKER = "FAILURE RECOVERY DOCTRINE"
_CAPABILITY_MARKER = "CAPABILITY ACQUISITION DOCTRINE"
_QUANT_MARKER = "QUANTITATIVE INTEGRITY DOCTRINE"

# Roles that BUILD code/systems → keep the production checklist.
_BUILD_ROLES = ["developer", "frontend_developer", "backend_developer", "devops",
                "architect", "engineer", "core", "pentester", "exploit_developer"]
# Roles that do NOT build → must not carry the checklist.
_NON_BUILD_ROLES = ["judge", "researcher", "writer", "analyst", "planner",
                    "debugger", "critic", "data_scientist"]


def test_build_roles_keep_production_excellence():
    for role in _BUILD_ROLES:
        assert _PROD_MARKER in get_behaviors_section(role), (
            f"{role} should keep PRODUCTION_EXCELLENCE (it builds code/systems)"
        )


def test_non_build_roles_drop_production_excellence():
    for role in _NON_BUILD_ROLES:
        assert _PROD_MARKER not in get_behaviors_section(role), (
            f"{role} should NOT carry PRODUCTION_EXCELLENCE (it doesn't build code)"
        )


def test_universal_doctrines_present_for_all_roles():
    # Failure recovery + capability acquisition + quantitative integrity are
    # universal — every role keeps them.
    for role in _BUILD_ROLES + _NON_BUILD_ROLES:
        section = get_behaviors_section(role)
        assert _FAILURE_MARKER in section, f"{role} lost FAILURE_RECOVERY"
        assert _CAPABILITY_MARKER in section, f"{role} lost CAPABILITY_ACQUISITION"
        assert _QUANT_MARKER in section, f"{role} lost QUANTITATIVE_INTEGRITY"


def test_quantitative_integrity_doctrine_covers_the_group_II_failure_modes():
    # The doctrine must name each group-II failure mode so an agent self-checks:
    # unreconciled duplicate quantities, fabricated/back-solved numbers, tautology
    # checks, order-of-magnitude implausibility, and failed-sim-as-pass.
    section = get_behaviors_section("engineer").lower()
    assert "one value everywhere" in section or "one source of truth" in section
    assert "tautology" in section
    assert "order-of-magnitude" in section or "order of magnitude" in section
    assert "back-solve" in section or "reverse-fit" in section or "back-solved" in section


def test_non_build_role_is_meaningfully_smaller():
    # The whole point: a non-build role's behaviors must shrink by roughly the
    # size of the production checklist (~5K chars).
    judge = len(get_behaviors_section("judge"))
    developer = len(get_behaviors_section("developer"))
    assert developer - judge > 4000, (
        f"expected ~5K-char gap from dropping the build checklist, got {developer - judge}"
    )
