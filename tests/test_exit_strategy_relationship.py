# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Exit Strategy math review (operator, 2026-05-27); Fitz
#   methodology: "Test relationships, not just functions" — these tests are
#   the cross-module invariants for D1 → D2 → D3 pure-math wiring.
#
# Purpose: prove the seam between D1/D2/D3 holds when the data flows end-to-
# end, not just when each module is exercised in isolation. Each function-
# level suite is unit-only. This suite is the boundary contract.
"""Cross-module relationship tests for the Exit Strategy pure-math layer."""
from __future__ import annotations

import math

import pytest

from src.strategy.exit_constrained_posterior import (
    constrain_family_posterior_by_observation,
)
from src.strategy.exit_family_optimizer import (
    ExitLegInput,
    optimize_exit_family,
)
from src.strategy.exit_observation_constraint import (
    build_settlement_progress_constraint,
)
from src.types.market import Bin


# ----- helpers -----


def _wbin(low: float | None, high: float | None) -> Bin:
    return Bin(
        low=low, high=high, unit="F",
        label=("shoulder" if low is None or high is None else f"{low}-{high}°F"),
    )


def _full_high_row(value: float) -> dict:
    return {
        "temperature_metric": "high",
        "high_so_far": value,
        "low_so_far": None,
        "source_authorized_for_settlement": 1,
        "local_date_matches_target": 1,
        "coverage_status": "OK",
        "freshness_status": "FRESH",
    }


def _full_low_row(value: float) -> dict:
    return {
        "temperature_metric": "low",
        "high_so_far": None,
        "low_so_far": value,
        "source_authorized_for_settlement": 1,
        "local_date_matches_target": 1,
        "coverage_status": "OK",
        "freshness_status": "FRESH",
    }


# ----- Invariant 1: feasibility mask flows correctly from D1 → D2 -----


class TestD1FeasibilityMaskFlowsIntoD2:
    """The boolean mask D2 carries MUST equal D1.feasibility() == 'impossible'
    for each bin — not 'unknown', not 'feasible'. Regression here would
    silently zero correct bins or leave impossible bins live."""

    def test_d2_impossible_mask_matches_d1_impossibility(self):
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        p = [0.3, 0.4, 0.3]
        constraint = build_settlement_progress_constraint(_full_high_row(63.0))
        result = constrain_family_posterior_by_observation(p, bins, constraint)
        # D1 says bin0 impossible, bin1 contains, bin2 feasible.
        # D2 mask must be (True, False, False) — only 'impossible' is True.
        assert result.impossible_mask == (True, False, False)
        # Verify against D1 directly to lock the boundary.
        d1_verdicts = constraint.mask(bins)
        for d1_v, d2_mask in zip(d1_verdicts, result.impossible_mask):
            assert d2_mask == (d1_v == "impossible")

    def test_advisory_d1_yields_no_d2_impossibility(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.5, 0.5]
        constraint = build_settlement_progress_constraint({
            **_full_high_row(63.0), "freshness_status": "DEGRADED",
        })
        result = constrain_family_posterior_by_observation(p, bins, constraint)
        assert result.impossible_mask == (False, False)
        assert result.authority_status == "ADVISORY_ONLY"


# ----- Invariant 2: p_obs flows correctly from D2 → D3 -----


class TestD2PosteriorFlowsIntoD3:
    """D3.optimize_exit_family must consume D2.p_obs/impossible_mask as the
    SOLE posterior-side input — never the original p_family. A regression
    would let optimizer EV gates use pre-truncation probabilities."""

    def test_optimizer_uses_p_obs_not_p_pre(self):
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        # Pre-observation belief: heavy on bin (62,63).
        p_pre = [0.1, 0.7, 0.2]
        constraint = build_settlement_progress_constraint(_full_high_row(63.0))
        posterior = constrain_family_posterior_by_observation(p_pre, bins, constraint)
        # Post-observation: bin (62,63) renormalises to 0.7 / (0.7+0.2) = 7/9.
        expected_p_obs_62_63 = 0.7 / 0.9
        # Use a bid that splits the two posteriors:
        #   bid=0.5; pre-obs leg hold_value=100*0.7=70; sell=100*0.5=50 → HOLD.
        #   post-obs leg hold_value=100*7/9≈77.78; sell=50 → HOLD (still).
        # So pick a bid that flips between them:
        #   bid=0.75; pre-obs hold=70; sell=75 → SELL.
        #   post-obs hold=77.78; sell=75 → HOLD.
        # If the optimizer used p_pre, this leg would SELL; if it uses p_obs,
        # it must HOLD.
        legs = [
            ExitLegInput("leg62", 1, "62-63", "buy_yes", 100.0, 0.75),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.legs[0].action == "HOLD"
        assert decision.legs[0].reason == "HOLD_DOMINANT"
        assert math.isclose(
            decision.legs[0].p_obs, expected_p_obs_62_63, rel_tol=1e-12
        )


# ----- Invariant 3: D1 impossibility → D3 deterministic exit (when bid present) -----


class TestD1ImpossibilityYieldsD3DeterministicExit:
    """Operator §5: 'For any held bin impossible by authorized WU/HKO
    observation: sell all executable shares if bid > min_exit_bid. No edge
    confirmation.' — this is the round-trip the safety-critical category
    of bug depends on."""

    @pytest.mark.parametrize("observed,impossible_bin_low,impossible_bin_high", [
        # HIGH market — the observed=65 case makes bin (60,61) impossible.
        (65.0, 60, 61),
        # HIGH market — observed=70 makes both bins below 70 impossible.
        (70.0, 62, 63),
    ])
    def test_high_market_deterministic_sell_full(
        self, observed, impossible_bin_low, impossible_bin_high,
    ):
        bins = [_wbin(impossible_bin_low, impossible_bin_high), _wbin(80, 81)]
        p = [0.5, 0.5]
        constraint = build_settlement_progress_constraint(_full_high_row(observed))
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput(
                "loser", 0,
                f"{impossible_bin_low}-{impossible_bin_high}",
                "buy_yes", 100.0, 0.03,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        assert leg.action == "SELL_FULL"
        assert leg.reason == "OBSERVATION_IMPOSSIBLE_HIGH"
        assert leg.sell_shares == 100.0

    def test_low_market_deterministic_sell_full(self):
        # LOW market — observed=20 makes bin (40,41) impossible.
        bins = [_wbin(40, 41), _wbin(20, 21)]
        p = [0.5, 0.5]
        constraint = build_settlement_progress_constraint(_full_low_row(20.0))
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput("loser_40", 0, "40-41", "buy_yes", 50.0, 0.04),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        assert leg.action == "SELL_FULL"
        assert leg.reason == "OBSERVATION_IMPOSSIBLE_LOW"


# ----- Invariant 4: ADVISORY ⇒ no deterministic exits propagated -----


class TestAdvisoryNeverYieldsDeterministicExit:
    """When the D1 constraint is ADVISORY_ONLY (stale obs / wrong source /
    DST mismatch), D3 must NEVER emit OBSERVATION_IMPOSSIBLE_* — the
    integration's safety contract: stale obs ≠ authoritative impossibility."""

    @pytest.mark.parametrize("breaking_mutation", [
        {"source_authorized_for_settlement": 0},
        {"local_date_matches_target": 0},
        {"freshness_status": "DEGRADED"},
        {"coverage_status": "LOW"},
    ])
    def test_advisory_constraint_no_observation_impossible_exit(self, breaking_mutation):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.5, 0.5]
        row = _full_high_row(63.0)
        row.update(breaking_mutation)
        constraint = build_settlement_progress_constraint(row)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        # Bid is high enough that EV cash-out might fire — but the operator
        # contract says no IMPOSSIBLE-prefixed reason is allowed on advisory.
        legs = [
            ExitLegInput("x", 0, "60-61", "buy_yes", 100.0, 0.10),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.any_deterministic_exit() is False
        for leg in decision.legs:
            assert not leg.reason.startswith("OBSERVATION_IMPOSSIBLE")
            assert leg.reason != "OBSERVATION_CONTRADICTION_FAIL_CLOSED"


# ----- Invariant 5: contradiction never silently fabricates a posterior -----


class TestContradictionNeverFabricatesPosterior:
    """When D2 flags contradiction, the optimizer's posterior must be zeros
    everywhere — there is NO valid hold_value baseline. Sell what we can,
    record the fail-closed."""

    def test_contradiction_all_zero_p_obs_propagates_to_d3(self):
        """D2 contradiction zeros every p_obs entry — verify the optimizer
        actually consumes those zeros (not the pre-contradiction p_family).
        Uses a buy_yes leg on a feasible-but-zero-p bin so the contradiction
        branch fires (impossibility branch is buy_yes + mask=True only)."""
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(90, 91)]
        p = [0.5, 0.5, 0.0]
        # observed=80 makes (60,61) and (62,63) impossible; (90,91) feasible
        # but with 0 mass → feasible_mass=0 → contradiction.
        constraint = build_settlement_progress_constraint(_full_high_row(80.0))
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        assert posterior.contradiction_flag is True
        assert all(x == 0.0 for x in posterior.p_obs)
        legs = [
            ExitLegInput("x", 2, "90-91", "buy_yes", 10.0, 0.5),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        assert decision.contradiction is True
        assert leg.p_obs == 0.0
        assert leg.action == "SELL_FULL"
        assert leg.reason == "OBSERVATION_CONTRADICTION_FAIL_CLOSED"


# ----- Invariant 6: family-mass conservation across the whole pipeline -----


class TestFamilyMassConservation:
    """Σ p_obs == 1 over feasible bins (renormalisation invariant) when D2
    doesn't flag contradiction. Holds across D1 verdicts mixed for any
    multi-bin family."""

    def test_mass_sums_to_one_under_partial_truncation(self):
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65), _wbin(66, 67)]
        p = [0.05, 0.30, 0.50, 0.15]
        constraint = build_settlement_progress_constraint(_full_high_row(63.0))
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        # Only bin0 is impossible; bins 1/2/3 share the remaining mass.
        assert posterior.impossible_mask == (True, False, False, False)
        assert math.isclose(sum(posterior.p_obs), 1.0, rel_tol=1e-12)

    def test_no_mass_when_all_impossible(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.5, 0.5]
        constraint = build_settlement_progress_constraint(_full_high_row(80.0))
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        assert sum(posterior.p_obs) == 0.0
        assert posterior.contradiction_flag is True


# ----- Invariant 6.5: direction-aware hold_value across the seam (critic F-1) -----


class TestBuyNoDirectionFlipAcrossSeam:
    """Critic F-1 (2026-05-27): the optimizer must apply the YES→held-side
    direction flip when consuming D2.p_obs for a buy_no leg. Without the
    flip, a buy_no on an impossible YES bin is liquidated at the bid even
    though it is the guaranteed winner. This is the cross-module contract
    that locks the buy_no direction semantics — D1 marks the YES bin
    impossible, D2 zeros the YES mass, D3 must compute held_p = 1 - p_obs
    so the NO holder's hold_value reflects guaranteed-winner status."""

    def test_buy_no_on_impossible_yes_bin_yields_hold_dominant(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.30, 0.70]
        # observed=63 makes (60,61) impossible.
        constraint = build_settlement_progress_constraint(_full_high_row(63.0))
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        # Confirm D2 has zeroed bin 0's YES mass.
        assert posterior.impossible_mask == (True, False)
        assert posterior.p_obs[0] == 0.0

        # buy_no on impossible YES bin: held_p must be 1 - 0 = 1.0.
        # hold_value = 100 * 1.0 = 100. sell_value ≈ 100*0.85 = 85. HOLD.
        from src.strategy.exit_family_optimizer import ExitLegInput, optimize_exit_family
        legs = [
            ExitLegInput("no_winner", 0, "60-61", "buy_no", 100.0, 0.85),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        assert leg.action == "HOLD"
        assert leg.reason == "HOLD_DOMINANT"
        # If the F-1 regression came back (held_p naively = p_obs = 0.0),
        # hold_value would be 0 < sell_value (85), and SELL_FULL EV_CASH_OUT
        # would fire — this assertion catches the inversion.
        assert leg.hold_value > leg.sell_value
        # Stronger lock: the actual hold_value is shares × (1 - p_obs).
        assert math.isclose(leg.hold_value, 100.0 * (1.0 - 0.0), rel_tol=1e-12)

    def test_buy_yes_on_impossible_yes_bin_yields_immediate_sell(self):
        """Sister case: buy_yes IS the loser, must sell. Locks the
        direction-asymmetry: same family + same impossible bin yields
        opposite actions for the two directions."""
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.30, 0.70]
        constraint = build_settlement_progress_constraint(_full_high_row(63.0))
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        from src.strategy.exit_family_optimizer import ExitLegInput, optimize_exit_family
        legs = [
            ExitLegInput("yes_loser", 0, "60-61", "buy_yes", 100.0, 0.05),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        assert leg.action == "SELL_FULL"
        assert leg.reason == "OBSERVATION_IMPOSSIBLE_HIGH"


# ----- Invariant 7: leg.feasibility on optimizer output ↔ D1 verdict -----


class TestOptimizerLegFeasibilityMatchesD1:
    """The feasibility string the optimizer records for each leg must match
    D1's verdict (or 'unknown' under advisory). Trace consumers and post-
    mortem audits depend on this round-trip."""

    def test_deterministic_leg_feasibility_records_impossible(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.3, 0.7]
        constraint = build_settlement_progress_constraint(_full_high_row(63.0))
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput("loser", 0, "60-61", "buy_yes", 100.0, 0.05),
            ExitLegInput("winner", 1, "62-63", "buy_yes", 100.0, 0.20),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.legs[0].feasibility == "impossible"
        # D1.feasibility(_wbin(62,63)) at obs=63 is 'contains_current_record'
        # which the optimizer records as 'feasible_or_current' (collapsed).
        assert decision.legs[1].feasibility == "feasible_or_current"

    def test_advisory_leg_feasibility_records_unknown(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.5, 0.5]
        row = _full_high_row(63.0)
        row["freshness_status"] = "DEGRADED"
        constraint = build_settlement_progress_constraint(row)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput("a", 0, "60-61", "buy_yes", 100.0, 0.05),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.legs[0].feasibility == "unknown"
