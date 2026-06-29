# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Exit Strategy math review (operator, 2026-05-27)
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
#
# Purpose: lock the math + authority gating of D1 SettlementProgressConstraint.
# These are PURE-MATH tests (no DB, no fixtures); they protect the contract
# that downstream D2 posterior truncation + D5 short-circuit will depend on.
"""Tests for src/strategy/exit_observation_constraint.py (D1)."""
from __future__ import annotations

import pytest

from src.strategy.exit_observation_constraint import (
    SettlementProgressConstraint,
    build_settlement_progress_constraint,
)
from src.types.market import Bin


# ----- helpers -----


def _interior_bin(low: float, high: float | None = None, unit: str = "F", label: str = "") -> Bin:
    # Bin validation: F non-shoulder must be width-2 (high-low+1==2 ⇒ high=low+1).
    # C non-shoulder must be width-1 (point bins, high==low).
    if high is None:
        high = low + 1 if unit == "F" else low
    return Bin(low=low, high=high, unit=unit, label=label or f"{low}-{high}°{unit}")


def _upper_shoulder(threshold: float, unit: str = "F") -> Bin:
    # "X or higher": low=X, high=None
    return Bin(low=threshold, high=None, unit=unit, label=f"{threshold}°{unit} or higher")


def _lower_shoulder(threshold: float, unit: str = "F") -> Bin:
    # "X or below": low=None, high=X
    return Bin(low=None, high=threshold, unit=unit, label=f"{threshold}°{unit} or below")


def _full_authority_row(metric: str, value: float) -> dict:
    """Row that PASSES every gate. Tests subtract gates one at a time."""
    return {
        "temperature_metric": metric,
        "high_so_far": value if metric == "high" else None,
        "low_so_far": value if metric == "low" else None,
        "source_authorized_for_settlement": 1,
        "local_date_matches_target": 1,
        "coverage_status": "OK",
        "freshness_status": "FRESH",
    }


# ----- HIGH market interior bin feasibility -----


class TestHighMarketInteriorBinMath:
    """For HIGH markets, observed = high_so_far, final = max_t Temp_t."""

    def test_bin_above_observed_is_feasible(self):
        # F bins have width 2 (60-62 means 60,61,62 settle here per current Bin
        # validation), so use width-2 interior bins for HIGH market tests.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=60.0)
        )
        # observed=60, bin lo=62 → bin entirely above current max → feasible
        assert constraint.feasibility(_interior_bin(62, 63)) == "feasible"

    def test_bin_containing_observed_is_current_record(self):
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=63.0)
        )
        # observed=63, bin 62-64 → current max in bin
        assert constraint.feasibility(_interior_bin(62, 63)) == "contains_current_record"

    def test_bin_below_observed_is_impossible(self):
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=65.0)
        )
        # observed=65, bin 62-64 → current max already exceeds bin upper
        assert constraint.feasibility(_interior_bin(62, 63)) == "impossible"

    def test_bin_upper_edge_equals_observed_is_current_record(self):
        # Edge case: observed exactly equals bin.high.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=63.0)
        )
        assert constraint.feasibility(_interior_bin(62, 63)) == "contains_current_record"

    def test_bin_lower_edge_equals_observed_is_current_record(self):
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=62.0)
        )
        assert constraint.feasibility(_interior_bin(62, 63)) == "contains_current_record"


# ----- HIGH market shoulder feasibility -----


class TestHighMarketShoulderMath:
    def test_upper_shoulder_containing_observed(self):
        # "65 or higher": low=65, high=None. observed=70 → in shoulder.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=70.0)
        )
        assert constraint.feasibility(_upper_shoulder(65)) == "contains_current_record"

    def test_upper_shoulder_observed_below_threshold_is_feasible(self):
        # "65 or higher": observed=60. Max might rise to >=65, so feasible.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=60.0)
        )
        assert constraint.feasibility(_upper_shoulder(65)) == "feasible"

    def test_lower_shoulder_observed_below_threshold_is_current_record(self):
        # "50 or below" bin: low=None, high=50. observed=45 → in shoulder.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=45.0)
        )
        assert constraint.feasibility(_lower_shoulder(50)) == "contains_current_record"

    def test_lower_shoulder_observed_above_threshold_is_impossible(self):
        # "50 or below" bin: observed=55. Max already above threshold,
        # can't settle in "<=50" bucket.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=55.0)
        )
        assert constraint.feasibility(_lower_shoulder(50)) == "impossible"


# ----- LOW market interior bin feasibility -----


class TestLowMarketInteriorBinMath:
    """For LOW markets, observed = low_so_far, final = min_t Temp_t."""

    def test_bin_below_observed_is_feasible(self):
        # observed=50, bin 40-42 → bin entirely below current min → feasible
        # (min might fall to land in 40-42 bucket).
        constraint = build_settlement_progress_constraint(
            _full_authority_row("low", value=50.0)
        )
        assert constraint.feasibility(_interior_bin(40, 41)) == "feasible"

    def test_bin_containing_observed_is_current_record(self):
        constraint = build_settlement_progress_constraint(
            _full_authority_row("low", value=40.5)
        )
        assert constraint.feasibility(_interior_bin(40, 41)) == "contains_current_record"

    def test_bin_above_observed_is_impossible(self):
        constraint = build_settlement_progress_constraint(
            _full_authority_row("low", value=38.0)
        )
        # observed=38, bin 40-42: current min already below bin → impossible
        assert constraint.feasibility(_interior_bin(40, 41)) == "impossible"


# ----- LOW market shoulder feasibility -----


class TestLowMarketShoulderMath:
    def test_lower_shoulder_observed_below_threshold_is_current_record(self):
        # "30 or below" bin: low=None, high=30. observed=25 → in shoulder.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("low", value=25.0)
        )
        assert constraint.feasibility(_lower_shoulder(30, unit="F")) == "contains_current_record"

    def test_lower_shoulder_observed_above_threshold_is_feasible(self):
        # observed=35, "30 or below" shoulder: min might fall.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("low", value=35.0)
        )
        assert constraint.feasibility(_lower_shoulder(30, unit="F")) == "feasible"

    def test_upper_shoulder_observed_above_threshold_is_current_record(self):
        # "60 or higher" upper shoulder, observed=65 → in shoulder.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("low", value=65.0)
        )
        assert constraint.feasibility(_upper_shoulder(60, unit="F")) == "contains_current_record"

    def test_upper_shoulder_observed_below_threshold_is_impossible(self):
        # "60 or higher" upper shoulder, observed=55. Min already below 60,
        # final min cannot rise — bin impossible.
        constraint = build_settlement_progress_constraint(
            _full_authority_row("low", value=55.0)
        )
        assert constraint.feasibility(_upper_shoulder(60, unit="F")) == "impossible"


# ----- Authority gating: every failing gate must downgrade to ADVISORY_ONLY -----


class TestAuthorityGating:
    """Each gate failure must make feasibility() return 'unknown' for every
    bin. D5 must NOT short-circuit on ADVISORY_ONLY."""

    @pytest.mark.parametrize(
        "mutation,reason_fragment",
        [
            ({"source_authorized_for_settlement": 0}, "source_not_authorized"),
            ({"source_authorized_for_settlement": None}, "source_not_authorized"),
            ({"local_date_matches_target": 0}, "local_date_mismatch"),
            ({"local_date_matches_target": None}, "local_date_mismatch"),
            ({"coverage_status": "NON_SETTLEMENT_SOURCE"}, "coverage_not_ok"),
            ({"coverage_status": "LOW"}, "coverage_not_ok"),
            ({"coverage_status": None}, "coverage_not_ok"),
            ({"freshness_status": "DEGRADED"}, "freshness_not_fresh"),
            ({"freshness_status": "MISSING"}, "freshness_not_fresh"),
            ({"freshness_status": None}, "freshness_not_fresh"),
            ({"temperature_metric": "humidity"}, "temperature_metric_invalid"),
            ({"temperature_metric": None}, "temperature_metric_invalid"),
            ({"high_so_far": None}, "observed_value"),
            ({"high_so_far": float("nan")}, "observed_value_non_finite"),
            ({"high_so_far": float("inf")}, "observed_value_non_finite"),
        ],
    )
    def test_failing_gate_downgrades_to_advisory_only(self, mutation, reason_fragment):
        row = _full_authority_row("high", value=63.0)
        row.update(mutation)
        constraint = build_settlement_progress_constraint(row)
        assert constraint.authority_status == "ADVISORY_ONLY"
        assert constraint.feasibility(_interior_bin(62, 63)) == "unknown"
        assert any(reason_fragment in r for r in constraint.gate_reasons), (
            f"expected '{reason_fragment}' in gate_reasons={constraint.gate_reasons}"
        )

    def test_none_row_is_advisory_only(self):
        constraint = build_settlement_progress_constraint(None)
        assert constraint.authority_status == "ADVISORY_ONLY"
        assert constraint.gate_reasons == ("row_missing",)
        assert constraint.feasibility(_interior_bin(62, 63)) == "unknown"

    def test_full_gates_pass_yields_deterministic(self):
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=63.0)
        )
        assert constraint.authority_status == "DETERMINISTIC"
        assert constraint.is_deterministic() is True


# ----- mask() over a family of bins -----


class TestMaskVectorization:
    def test_mask_over_high_family_classifies_all_three_cases(self):
        # Build a 3-bin family centred on a current high of 63.
        bins = (
            _interior_bin(60, 61),  # hi=61 < observed=63 → impossible
            _interior_bin(62, 63),  # contains observed=63 → current_record
            _interior_bin(64, 65),  # lo=64 > observed=63 → feasible
        )
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=63.0)
        )
        verdicts = constraint.mask(bins)
        assert verdicts == (
            "impossible",
            "contains_current_record",
            "feasible",
        )

    def test_mask_under_advisory_returns_all_unknown(self):
        bins = (_interior_bin(60, 61), _interior_bin(62, 63))
        constraint = build_settlement_progress_constraint(
            {**_full_authority_row("high", value=63.0), "freshness_status": "DEGRADED"}
        )
        assert constraint.mask(bins) == ("unknown", "unknown")


# ----- Anti-regression: math direction is correct for each metric -----


class TestMathDirectionInvariants:
    """A 'final extreme >= observed' (HIGH) / 'final <= observed' (LOW)
    invariant must be preserved by the feasibility verdicts."""

    def test_high_market_no_bin_strictly_below_observed_is_feasible(self):
        constraint = build_settlement_progress_constraint(
            _full_authority_row("high", value=80.0)
        )
        # Any width-2 F bin entirely below observed must be impossible
        # (a HIGH market never reverts; final >= observed).
        assert constraint.feasibility(_interior_bin(60, 61)) == "impossible"
        assert constraint.feasibility(_interior_bin(70, 71)) == "impossible"
        # Bin (80,81) straddles observed=80 (lo=80<=80) → contains_current_record.
        assert constraint.feasibility(_interior_bin(80, 81)) == "contains_current_record"

    def test_low_market_no_bin_strictly_above_observed_is_feasible(self):
        constraint = build_settlement_progress_constraint(
            _full_authority_row("low", value=20.0)
        )
        # Final LOW only goes down; any bin entirely above observed is unreachable.
        assert constraint.feasibility(_interior_bin(40, 41)) == "impossible"
        assert constraint.feasibility(_interior_bin(30, 31)) == "impossible"
        # Bin (20,21) straddles observed=20 → contains_current_record.
        assert constraint.feasibility(_interior_bin(20, 21)) == "contains_current_record"
