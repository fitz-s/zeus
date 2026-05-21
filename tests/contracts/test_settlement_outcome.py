# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase7_settlement_type_gate/PHASE_7_PLAN.md §T1-T2 acceptance criteria
"""Tests for SettlementOutcome enum, transition rules, and classify_settlement_outcome."""
from __future__ import annotations

import pytest

from src.contracts.settlement_outcome import (
    InvalidSettlementTransition,
    SettlementOutcome,
    VALID_FORWARD_TRANSITIONS,
    apply_transition,
    classify_settlement_outcome,
)


# ---------------------------------------------------------------------------
# T1: Enum contract
# ---------------------------------------------------------------------------

class TestSettlementOutcomeEnum:
    def test_ten_members(self):
        assert len(list(SettlementOutcome)) == 10

    def test_exact_values(self):
        assert SettlementOutcome.UNRESOLVED == 0
        assert SettlementOutcome.PHYSICALLY_CONFIRMED == 1
        assert SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED == 2
        assert SettlementOutcome.VENUE_RESOLVED_WIN == 3
        assert SettlementOutcome.VENUE_RESOLVED_LOSE == 4
        assert SettlementOutcome.REDEEMED == 5
        assert SettlementOutcome.OBSERVATION_REVISED == 6
        assert SettlementOutcome.DISPUTED == 100
        assert SettlementOutcome.UMA_UNKNOWN_50_50 == 101
        assert SettlementOutcome.SOURCE_REVISION == 102

    def test_is_int_enum(self):
        assert isinstance(SettlementOutcome.UNRESOLVED, int)


# ---------------------------------------------------------------------------
# T1: Transition rules
# ---------------------------------------------------------------------------

class TestApplyTransition:
    def test_happy_chain(self):
        """Full UNRESOLVED→PHYSICALLY_CONFIRMED→SOURCE_PUBLISHED_VENUE_UNRESOLVED→VENUE_RESOLVED_WIN→REDEEMED."""
        s = SettlementOutcome.UNRESOLVED
        s = apply_transition(s, SettlementOutcome.PHYSICALLY_CONFIRMED)
        s = apply_transition(s, SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED)
        s = apply_transition(s, SettlementOutcome.VENUE_RESOLVED_WIN)
        s = apply_transition(s, SettlementOutcome.REDEEMED)
        assert s == SettlementOutcome.REDEEMED

    def test_backward_raises(self):
        with pytest.raises(InvalidSettlementTransition):
            apply_transition(SettlementOutcome.REDEEMED, SettlementOutcome.UNRESOLVED)

    def test_observation_revised_to_source_revision_succeeds(self):
        result = apply_transition(SettlementOutcome.OBSERVATION_REVISED, SettlementOutcome.SOURCE_REVISION)
        assert result == SettlementOutcome.SOURCE_REVISION

    def test_observation_revised_to_physically_confirmed_raises(self):
        with pytest.raises(InvalidSettlementTransition):
            apply_transition(SettlementOutcome.OBSERVATION_REVISED, SettlementOutcome.PHYSICALLY_CONFIRMED)

    def test_observation_revised_to_source_published_raises(self):
        with pytest.raises(InvalidSettlementTransition):
            apply_transition(
                SettlementOutcome.OBSERVATION_REVISED,
                SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED,
            )

    def test_observation_revised_to_unresolved_raises(self):
        with pytest.raises(InvalidSettlementTransition):
            apply_transition(SettlementOutcome.OBSERVATION_REVISED, SettlementOutcome.UNRESOLVED)

    def test_physically_confirmed_to_disputed_succeeds(self):
        result = apply_transition(SettlementOutcome.PHYSICALLY_CONFIRMED, SettlementOutcome.DISPUTED)
        assert result == SettlementOutcome.DISPUTED

    def test_venue_resolved_win_to_redeemed_succeeds(self):
        result = apply_transition(SettlementOutcome.VENUE_RESOLVED_WIN, SettlementOutcome.REDEEMED)
        assert result == SettlementOutcome.REDEEMED

    def test_venue_resolved_lose_to_redeemed_succeeds(self):
        result = apply_transition(SettlementOutcome.VENUE_RESOLVED_LOSE, SettlementOutcome.REDEEMED)
        assert result == SettlementOutcome.REDEEMED

    def test_terminal_state_no_transitions(self):
        """REDEEMED is terminal — any forward attempt raises."""
        for target in SettlementOutcome:
            if target != SettlementOutcome.REDEEMED:
                with pytest.raises(InvalidSettlementTransition):
                    apply_transition(SettlementOutcome.REDEEMED, target)


# ---------------------------------------------------------------------------
# T2: Classifier
# ---------------------------------------------------------------------------

class TestClassifySettlementOutcome:
    def test_win(self):
        result = classify_settlement_outcome(
            {"umaResolutionStatus": "resolved", "outcomePrices": ["1", "0"]}
        )
        assert result == SettlementOutcome.VENUE_RESOLVED_WIN

    def test_lose(self):
        result = classify_settlement_outcome(
            {"umaResolutionStatus": "resolved", "outcomePrices": ["0", "1"]}
        )
        assert result == SettlementOutcome.VENUE_RESOLVED_LOSE

    def test_missing_outcome_prices_fail_closed(self):
        """Missing outcomePrices → SOURCE_PUBLISHED_VENUE_UNRESOLVED, never WIN."""
        result = classify_settlement_outcome({"umaResolutionStatus": "resolved"})
        assert result == SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED

    def test_50_50_fail_closed(self):
        result = classify_settlement_outcome(
            {"umaResolutionStatus": "resolved", "outcomePrices": ["0.5", "0.5"]}
        )
        assert result == SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED

    def test_empty_dict(self):
        result = classify_settlement_outcome({})
        assert result == SettlementOutcome.UNRESOLVED

    def test_not_resolved_status(self):
        result = classify_settlement_outcome({"umaResolutionStatus": "pending"})
        assert result == SettlementOutcome.UNRESOLVED

    def test_malformed_prices_fail_closed(self):
        """Non-numeric prices → fail-closed."""
        result = classify_settlement_outcome(
            {"umaResolutionStatus": "resolved", "outcomePrices": ["yes", "no"]}
        )
        assert result == SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED

    def test_empty_prices_list_fail_closed(self):
        result = classify_settlement_outcome(
            {"umaResolutionStatus": "resolved", "outcomePrices": []}
        )
        assert result == SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED

    def test_partial_prices_fail_closed(self):
        """Single-element list — not binary."""
        result = classify_settlement_outcome(
            {"umaResolutionStatus": "resolved", "outcomePrices": ["1"]}
        )
        assert result == SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED
