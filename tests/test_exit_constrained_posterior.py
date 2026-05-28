# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Exit Strategy math review (operator, 2026-05-27)
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
#
# Purpose: lock the math of D2 constrain_family_posterior_by_observation +
# the contradiction-fail-closed contract D3/D5 will depend on.
"""Tests for src/strategy/exit_constrained_posterior.py (D2)."""
from __future__ import annotations

import math

import pytest

from src.strategy.exit_constrained_posterior import (
    constrain_family_posterior_by_observation,
)
from src.strategy.exit_observation_constraint import (
    SettlementProgressConstraint,
    build_settlement_progress_constraint,
)
from src.types.market import Bin


# ----- helpers -----


def _wbin(low: float | None, high: float | None, unit: str = "F") -> Bin:
    label_low = "shoulder_lo" if low is None else f"{low}"
    label_high = "shoulder_hi" if high is None else f"{high}"
    return Bin(low=low, high=high, unit=unit, label=f"{label_low}-{label_high}°{unit}")


def _det_high(value: float) -> SettlementProgressConstraint:
    return build_settlement_progress_constraint({
        "temperature_metric": "high",
        "high_so_far": value,
        "low_so_far": None,
        "source_authorized_for_settlement": 1,
        "local_date_matches_target": 1,
        "coverage_status": "OK",
        "freshness_status": "FRESH",
    })


def _advisory() -> SettlementProgressConstraint:
    return build_settlement_progress_constraint(None)


# ----- truncation + renormalisation math -----


class TestTruncationRenormalization:
    def test_impossible_bins_zeroed_remaining_renormalises_to_one(self):
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        p = [0.30, 0.50, 0.20]
        # observed=66 → bin (60,61) and (62,63) and (64,65) all hi<66 → all
        # impossible. Use observed=63 instead so only (60,61) is impossible.
        constraint = _det_high(value=63.0)
        result = constrain_family_posterior_by_observation(p, bins, constraint)
        assert result.impossible_mask == (True, False, False)
        assert result.p_obs[0] == 0.0
        # remaining mass = 0.50 + 0.20 = 0.70; renormalised to 5/7 and 2/7.
        assert math.isclose(result.p_obs[1], 0.50 / 0.70, rel_tol=1e-12)
        assert math.isclose(result.p_obs[2], 0.20 / 0.70, rel_tol=1e-12)
        # Sum equals 1 within float epsilon.
        assert math.isclose(sum(result.p_obs), 1.0, rel_tol=1e-12)
        assert math.isclose(result.renormalization_mass, 0.70, rel_tol=1e-12)
        assert result.contradiction_flag is False
        assert result.authority_status == "DETERMINISTIC"

    def test_no_impossible_bins_returns_input_renormalised_to_self(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.4, 0.6]
        # observed=58 → both bins lo>58 → both feasible, none impossible.
        constraint = _det_high(value=58.0)
        result = constrain_family_posterior_by_observation(p, bins, constraint)
        assert result.impossible_mask == (False, False)
        assert math.isclose(result.p_obs[0], 0.4, rel_tol=1e-12)
        assert math.isclose(result.p_obs[1], 0.6, rel_tol=1e-12)
        assert result.contradiction_flag is False

    def test_unnormalised_input_renormalises_correctly(self):
        # Caller may pass an unnormalised vector; the math is the same.
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        p = [3.0, 5.0, 2.0]  # not summing to 1
        constraint = _det_high(value=63.0)
        result = constrain_family_posterior_by_observation(p, bins, constraint)
        # feasible mass = 5 + 2 = 7; renormalised to 5/7 + 2/7.
        assert math.isclose(result.p_obs[1], 5.0 / 7.0, rel_tol=1e-12)
        assert math.isclose(result.p_obs[2], 2.0 / 7.0, rel_tol=1e-12)
        assert math.isclose(sum(result.p_obs), 1.0, rel_tol=1e-12)


# ----- contradiction detection -----


class TestContradictionFlag:
    def test_all_bins_impossible_flags_contradiction_and_returns_zeros(self):
        # observed=80 makes every bin below 80 impossible.
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.5, 0.5]
        constraint = _det_high(value=80.0)
        result = constrain_family_posterior_by_observation(p, bins, constraint)
        assert result.impossible_mask == (True, True)
        assert result.p_obs == (0.0, 0.0)
        assert result.contradiction_flag is True
        assert result.renormalization_mass == 0.0

    def test_remaining_mass_below_eps_flags_contradiction(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [1.0 - 1e-15, 1e-15]  # essentially all mass in impossible bin
        constraint = _det_high(value=63.0)  # only (60,61) impossible
        result = constrain_family_posterior_by_observation(p, bins, constraint)
        # impossible mass 1.0-1e-15; remaining = 1e-15 ≤ eps=1e-9.
        assert result.impossible_mask == (True, False)
        assert result.contradiction_flag is True
        assert result.p_obs == (0.0, 0.0)

    def test_remaining_mass_just_above_eps_does_not_flag(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [1.0, 1e-6]  # remaining 1e-6 > eps=1e-9
        constraint = _det_high(value=63.0)
        result = constrain_family_posterior_by_observation(p, bins, constraint)
        assert result.contradiction_flag is False
        assert math.isclose(result.p_obs[1], 1.0, rel_tol=1e-12)


# ----- ADVISORY_ONLY pass-through -----


class TestAdvisoryPassthrough:
    def test_advisory_constraint_returns_renormalized_input(self):
        # Input already sums to 1.0 — renormalization is a no-op.
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        p = [0.3, 0.5, 0.2]
        result = constrain_family_posterior_by_observation(p, bins, _advisory())
        assert result.authority_status == "ADVISORY_ONLY"
        assert result.impossible_mask == (False, False, False)
        assert result.p_obs == tuple(p)
        assert result.contradiction_flag is False
        assert math.isclose(result.renormalization_mass, 1.0, rel_tol=1e-12)

    def test_advisory_constraint_renormalizes_unnormalized_input(self):
        # Critic-pass-3 antibody (Copilot 2026-05-27): unnormalized p_family
        # + ADVISORY_ONLY must not feed raw weights into optimize_exit_family.
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        p = [3.0, 5.0, 2.0]  # sum = 10.0
        result = constrain_family_posterior_by_observation(p, bins, _advisory())
        assert result.authority_status == "ADVISORY_ONLY"
        assert result.impossible_mask == (False, False, False)
        assert result.p_obs == (0.3, 0.5, 0.2)
        assert math.isclose(result.renormalization_mass, 10.0, rel_tol=1e-12)
        assert result.contradiction_flag is False


# ----- input validation (fail-closed entry invariants) -----


class TestInputValidation:
    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="length mismatch"):
            constrain_family_posterior_by_observation(
                [0.5, 0.5],
                [_wbin(60, 61)],
                _det_high(value=63.0),
            )

    def test_nan_probability_raises(self):
        with pytest.raises(ValueError, match="NaN"):
            constrain_family_posterior_by_observation(
                [0.5, float("nan")],
                [_wbin(60, 61), _wbin(62, 63)],
                _det_high(value=63.0),
            )

    def test_negative_probability_raises(self):
        with pytest.raises(ValueError, match="< 0"):
            constrain_family_posterior_by_observation(
                [0.7, -0.05],
                [_wbin(60, 61), _wbin(62, 63)],
                _det_high(value=63.0),
            )


# ----- non-mutation invariant (exit-only contract) -----


class TestExitOnlyContract:
    def test_input_p_family_is_not_mutated(self):
        """p_obs must be a NEW object; p_family must be untouched.

        Wave-6 historic regression: an exit-side transform leaked into entry
        sizing via shared list reference. This locks the contract.
        """
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        p_in = [0.3, 0.5, 0.2]
        p_snapshot = list(p_in)
        result = constrain_family_posterior_by_observation(
            p_in, bins, _det_high(value=63.0)
        )
        assert p_in == p_snapshot, "input p_family was mutated"
        assert result.p_obs is not p_in
