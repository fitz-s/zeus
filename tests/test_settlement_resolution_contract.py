# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL replay redesign §3 Finding 2 + §4.2. Verifies
#   SettlementResolution derives the winning bin from settlement_value (truth)
#   and treats the stored winning_bin string as evidence only, and that
#   exceptional resolutions are excluded from promotion/learning.
"""Tests for the SettlementResolution contract (value-derived winner truth)."""

from __future__ import annotations

import pytest

from src.contracts.calibration_bins import F_CANONICAL_GRID
from src.contracts.settlement_outcome import SettlementOutcome
from src.contracts.settlement_resolution import (
    SettlementResolution,
    SettlementResolutionUndeterminedError,
)

GRID = F_CANONICAL_GRID


def _value_in_some_bin():
    """Pick a settlement value, the bin it lands in, and a DIFFERENT bin label.

    Grid-label-agnostic: we ask the grid itself which bin a value lands in, so
    the test proves the invariant without hard-coding grid boundaries.
    """
    bins = GRID.as_bins()
    # Choose an interior (non-shoulder) bin with finite bounds for a clean value.
    interior = next(
        b for b in bins if not b.is_shoulder and b.low is not None and b.high is not None
    )
    value = (interior.low + interior.high) / 2.0
    derived = GRID.bin_for_value(value)
    other = next(b for b in bins if b.label != derived.label)
    return value, derived.label, other.label


def _row(**over):
    base = {
        "city": "nyc",
        "target_date": "2026-05-20",
        "temperature_metric": "high",
        "settlement_value": None,
        "winning_bin": None,
        "settlement_unit": "F",
        "settlement_source": "https://example/obs",
        "authority": "VERIFIED",
    }
    base.update(over)
    return base


def test_stored_label_wrong_value_derived_winner_wins():
    """The core Finding-2 invariant: stored winning_bin is WRONG, but the
    value-derived bin is authoritative."""
    value, true_label, wrong_label = _value_in_some_bin()
    res = SettlementResolution.from_settlement_row(
        _row(settlement_value=value, winning_bin=wrong_label), GRID
    )
    assert res.winning_bin_label == true_label
    assert res.stored_winning_bin_evidence == wrong_label
    assert res.stored_matches_derived is False
    assert res.truth_source == "settlement_value_derived"
    # index is consistent with the ordered labels it reports
    assert res.ordered_bin_labels[res.winning_bin_index] == res.winning_bin_label


def test_stored_label_matching_is_flagged_true():
    value, true_label, _ = _value_in_some_bin()
    res = SettlementResolution.from_settlement_row(
        _row(settlement_value=value, winning_bin=true_label), GRID
    )
    assert res.stored_matches_derived is True
    assert res.winning_bin_label == true_label


def test_no_stored_label_still_derives_and_flags_none():
    value, true_label, _ = _value_in_some_bin()
    res = SettlementResolution.from_settlement_row(
        _row(settlement_value=value, winning_bin=None), GRID
    )
    assert res.winning_bin_label == true_label
    assert res.stored_winning_bin_evidence is None
    assert res.stored_matches_derived is None


def test_missing_settlement_value_refused():
    with pytest.raises(SettlementResolutionUndeterminedError, match="settlement_value"):
        SettlementResolution.from_settlement_row(_row(settlement_value=None), GRID)


def test_default_outcome_is_promotion_eligible():
    value, _, _ = _value_in_some_bin()
    res = SettlementResolution.from_settlement_row(_row(settlement_value=value), GRID)
    assert res.outcome_state is SettlementOutcome.PHYSICALLY_CONFIRMED
    assert res.promotion_eligible is True
    assert res.learning_eligible is True
    assert res.resolution_status == "resolved"


@pytest.mark.parametrize(
    "state",
    [
        SettlementOutcome.UMA_UNKNOWN_50_50,
        SettlementOutcome.DISPUTED,
        SettlementOutcome.UNRESOLVED,
        SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED,
    ],
)
def test_exceptional_outcomes_excluded_from_promotion(state):
    value, true_label, _ = _value_in_some_bin()
    res = SettlementResolution.from_settlement_row(
        _row(settlement_value=value), GRID, outcome_state=state
    )
    # Winner is still derivable, but the resolution is not promotion/learning grade.
    assert res.winning_bin_label == true_label
    assert res.promotion_eligible is False
    assert res.learning_eligible is False
    assert res.resolution_status == "exceptional"


def test_outcome_type_int_column_coerced():
    value, _, _ = _value_in_some_bin()
    # 101 == UMA_UNKNOWN_50_50 → exceptional
    res = SettlementResolution.from_settlement_row(
        _row(settlement_value=value, outcome_type=101), GRID
    )
    assert res.outcome_state is SettlementOutcome.UMA_UNKNOWN_50_50
    assert res.promotion_eligible is False


def test_unknown_outcome_type_fails_closed_to_unresolved():
    value, _, _ = _value_in_some_bin()
    res = SettlementResolution.from_settlement_row(
        _row(settlement_value=value, outcome_type=9999), GRID
    )
    assert res.outcome_state is SettlementOutcome.UNRESOLVED
    assert res.promotion_eligible is False


def test_metric_carried_through_for_high_low_isolation():
    value, _, _ = _value_in_some_bin()
    hi = SettlementResolution.from_settlement_row(
        _row(settlement_value=value, temperature_metric="high"), GRID
    )
    lo = SettlementResolution.from_settlement_row(
        _row(settlement_value=value, temperature_metric="low"), GRID
    )
    assert hi.temperature_metric == "high"
    assert lo.temperature_metric == "low"
