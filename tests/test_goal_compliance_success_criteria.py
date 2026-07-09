"""Tests for success_criteria feeding the goal-drift heuristic (A7 review follow-up).

A7 put measurable success_criteria (definition_of_done + artifact verify) on the
GoalProfile, but GoalComplianceChecker._heuristic_score ignored it — so an agent
addressing the success bar (but not echoing the prompt's keywords) could be scored
as drifting, and vice-versa. The criteria terms now contribute to the relevance
score.
"""

from __future__ import annotations

from app.orchestration.goal_compliance import GoalProfile, GoalComplianceChecker


def _checker(message: str, success_criteria: str = "") -> GoalComplianceChecker:
    return GoalComplianceChecker(GoalProfile.from_message(message, success_criteria))


def test_output_matching_success_criteria_scores_higher():
    msg = "Build the data pipeline"
    criteria = "Done when: the nightly ETL job loads orders into the warehouse and reconciles totals"
    with_c = _checker(msg, criteria)
    without_c = _checker(msg, "")
    # Output that speaks to the CRITERIA terms but not the bare prompt keywords.
    output = (
        "I implemented the nightly ETL job; it loads orders into the warehouse and "
        "reconciles totals against the source ledger every night." * 2
    )
    s_with = with_c._heuristic_score(output)
    s_without = without_c._heuristic_score(output)
    assert s_with > s_without


def test_success_criteria_does_not_break_when_absent():
    # No criteria → behaviour unchanged (no crash, score still computed).
    c = _checker("Fix the login bug", "")
    score = c._heuristic_score("I fixed the login bug by patching the auth handler thoroughly.")
    assert 0.0 <= score <= 1.0


def test_success_criteria_terms_extracted_onto_profile():
    p = GoalProfile.from_message(
        "build x", "Done when: render a PDF report and verify it opens"
    )
    # The profile should retain the criteria so the checker can use it.
    assert p.success_criteria
    assert "render" in p.success_criteria.lower()


def test_score_stays_bounded_with_criteria():
    c = _checker("audit the system", "Done when: every finding has reproducible evidence")
    # An output stuffed with criteria words must not exceed 1.0.
    out = "finding evidence reproducible audit system " * 30
    assert c._heuristic_score(out) <= 1.0


def test_adding_criteria_never_lowers_score_no_false_drift():
    # Review CRITICAL: adding criteria must NEVER reduce the score of an output that
    # matches the prompt — otherwise a valid on-track agent gets flagged as drifting.
    # criteria_ratio is an additive BONUS, not a redistribution.
    msg = "Refactor the legacy billing module to use the new payment gateway abstraction"
    crit = "Done when: existing tests still pass and the gateway handles declined transactions gracefully"
    out = (
        "I reworked the billing logic to call the new payment provider. "
        "Charges route through the updated abstraction layer."
    )
    with_c = _checker(msg, crit)
    without_c = _checker(msg, "")
    assert with_c._heuristic_score(out) >= without_c._heuristic_score(out)


def test_adding_criteria_does_not_flip_to_drift(monkeypatch):
    # End-to-end: merely adding criteria must not turn is_drifting True for output
    # that was on-track without criteria.
    msg = "Refactor the legacy billing module to use the new payment gateway abstraction"
    crit = "Done when: existing tests still pass and the gateway handles declined transactions gracefully"
    out = (
        "I reworked the billing logic to call the new payment provider. "
        "Charges route through the updated abstraction layer."
    )
    without_c = _checker(msg, "")
    with_c = _checker(msg, crit)
    if not without_c.check_output("dev", out, turn=1).is_drifting:
        assert not with_c.check_output("dev", out, turn=1).is_drifting


def test_offtopic_output_still_low_even_with_criteria():
    c = _checker(
        "Build a biomass gasifier", "Done when: the generator outputs 20 kWe and passes validation"
    )
    # Completely unrelated output stays low (criteria doesn't rescue real drift).
    score = c._heuristic_score("Here is a recipe for chocolate cake with frosting and sprinkles." * 3)
    assert score < 0.5
