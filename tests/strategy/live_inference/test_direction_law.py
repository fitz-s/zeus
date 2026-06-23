# Created: 2026-06-10
# Last reused or audited: 2026-06-11
# Authority basis: FIX A antibody for incident 0b5c305e26524042 (Milan 24C first
#   fill, 2026-06-10T02:58Z); docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md.
#   Operator direction doctrine "buy_yes <=> bin ~= forecast" as code.
"""Antibody tests: the direction law makes far-tail buy_yes unconstructable.

Relationship under test (cross-module invariant): for ANY q_lcb pathology, a
buy_yes candidate whose bin is farther than max(1 settlement step, k*sigma) from
the posterior center is rejected with a deterministic reason BEFORE ranking —
and the law's other half keeps buy_no forecast-distant only.
"""
from __future__ import annotations

import pytest

from src.strategy.live_inference.direction_law import (
    DIRECTION_LAW_REASON,
    bin_forecast_distance,
    celsius_delta_to_unit,
    celsius_to_unit,
    direction_law_rejection_reason,
    direction_law_threshold,
)

# Incident posterior 929 (Milan 2026-06-11 high): fused center / fusion sigma.
INCIDENT_MU_C = 26.42049946463696
INCIDENT_SIGMA_C = 1.2630268963735225


class TestMilanIncidentReplay:
    """The exact incident shape must be rejected at DIRECTION_LAW."""

    def test_incident_24c_buy_yes_rejected(self):
        reason = direction_law_rejection_reason(
            direction="buy_yes",
            bin_low=24.0,
            bin_high=24.0,
            bin_unit="C",
            mu=INCIDENT_MU_C,
            predictive_sigma=INCIDENT_SIGMA_C,
        )
        assert reason is not None
        assert reason.startswith(DIRECTION_LAW_REASON)
        assert "buy_yes" in reason

    def test_incident_23c_buy_yes_rejected(self):
        # The #2-ranked candidate of the same incident book.
        reason = direction_law_rejection_reason(
            direction="buy_yes",
            bin_low=23.0,
            bin_high=23.0,
            bin_unit="C",
            mu=INCIDENT_MU_C,
            predictive_sigma=INCIDENT_SIGMA_C,
        )
        assert reason is not None and reason.startswith(DIRECTION_LAW_REASON)

    def test_forecast_adjacent_26c_buy_yes_admitted(self):
        # bin = mu* +- step: the bin containing the fused center must be YES-admissible.
        assert (
            direction_law_rejection_reason(
                direction="buy_yes",
                bin_low=26.0,
                bin_high=26.0,
                bin_unit="C",
                mu=INCIDENT_MU_C,
                predictive_sigma=INCIDENT_SIGMA_C,
            )
            is None
        )

    def test_far_buy_no_admitted_near_buy_no_rejected(self):
        # Law's other half: buy_no on the far 24C bin stays admissible (the
        # BASELINE harvest is forecast-distant NO); buy_no ON the center bin is not.
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=24.0,
                bin_high=24.0,
                bin_unit="C",
                mu=INCIDENT_MU_C,
                predictive_sigma=INCIDENT_SIGMA_C,
            )
            is None
        )
        near_no = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=26.0,
            bin_high=26.0,
            bin_unit="C",
            mu=INCIDENT_MU_C,
            predictive_sigma=INCIDENT_SIGMA_C,
        )
        assert near_no is not None and "buy_no" in near_no

    def test_open_ended_bins_use_nearest_bound(self):
        # "21C or below" (high=21): distance 5.42 -> YES rejected, NO admitted.
        assert (
            direction_law_rejection_reason(
                direction="buy_yes",
                bin_low=None,
                bin_high=21.0,
                bin_unit="C",
                mu=INCIDENT_MU_C,
                predictive_sigma=INCIDENT_SIGMA_C,
            )
            is not None
        )
        # "31C or higher" (low=31): distance 4.58 -> NO admitted.
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=31.0,
                bin_high=None,
                bin_unit="C",
                mu=INCIDENT_MU_C,
                predictive_sigma=INCIDENT_SIGMA_C,
            )
            is None
        )
        # mu beyond an open bound is INSIDE the bin -> YES admissible.
        assert (
            direction_law_rejection_reason(
                direction="buy_yes",
                bin_low=25.0,
                bin_high=None,
                bin_unit="C",
                mu=INCIDENT_MU_C,
                predictive_sigma=INCIDENT_SIGMA_C,
            )
            is None
        )


class TestModalOnlyYesRevert20260623:
    """Operator mandate 2026-06-23: "buy_yes only on its predicted bin ±0.5".

    REVERTS the 2026-06-15 σ-distance relaxation (YES admissible iff
    distance(bin, μ*) ≤ max(1 step, k·σ)) which admitted adjacent NON-forecast
    bins within one predictive sigma. Modal-only YES = legal ONLY on the bin the
    canonically-rounded center settles into — identical to
    FamilyDecisionEngine.direction_law_ok (route.bin_id == forecast_bin). The
    Milan far-tail ban is strictly preserved (a far bin is never the forecast
    bin), so this is NOT a ban-revival — it is the operator's standing law.
    """

    def test_adjacent_within_sigma_nonforecast_buy_yes_rejected(self):
        # mu=26.42 settles 26 (WMO). The 27C bin is adjacent and WITHIN the old
        # σ band (distance 0.58 ≤ threshold max(1, 1.263)=1.263) so σ-distance
        # ADMITTED it. Modal-only must REJECT it: 27 is not the forecast bin.
        reason = direction_law_rejection_reason(
            direction="buy_yes",
            bin_low=27.0,
            bin_high=27.0,
            bin_unit="C",
            mu=INCIDENT_MU_C,
            predictive_sigma=INCIDENT_SIGMA_C,
        )
        assert reason is not None and "buy_yes" in reason, (
            "modal-only: adjacent non-forecast YES (bin 27, mu 26.42→26) must be "
            "rejected even though it is within one predictive sigma"
        )

    def test_forecast_bin_buy_yes_still_admitted(self):
        # The modal bin (26, containing settled 26.42→26) stays YES-admissible.
        assert (
            direction_law_rejection_reason(
                direction="buy_yes",
                bin_low=26.0,
                bin_high=26.0,
                bin_unit="C",
                mu=INCIDENT_MU_C,
                predictive_sigma=INCIDENT_SIGMA_C,
            )
            is None
        )

    def test_modal_only_matches_buy_no_complement(self):
        # YES legal ⟺ bin IS forecast bin; NO legal ⟺ bin is NOT forecast bin.
        # For every bin around mu, exactly one side is admissible (mod boundary
        # zone, which only widens the NO ban). mu=30.795 settles 31.
        for low, high, yes_ok in ((31.0, 31.0, True), (29.0, 29.0, False)):
            yes = direction_law_rejection_reason(
                direction="buy_yes", bin_low=low, bin_high=high, bin_unit="C",
                mu=30.7950, predictive_sigma=1.91,
            )
            no = direction_law_rejection_reason(
                direction="buy_no", bin_low=low, bin_high=high, bin_unit="C",
                mu=30.7950, predictive_sigma=1.91,
            )
            assert (yes is None) is yes_ok, (low, high, yes)
            # forecast bin: YES ok + NO banned; non-forecast far bin: YES banned + NO ok
            assert (yes is None) != (no is None), (low, high)

    def test_truncation_city_modal_only_yes_uses_callable(self):
        # Truncation city (floor): mu=30.85 → floor=30. YES legal only on bin 30,
        # NOT on 31 (which WMO half-up would have called the forecast bin).
        trunc = lambda v: float(__import__("math").floor(v))  # noqa: E731
        assert (
            direction_law_rejection_reason(
                direction="buy_yes", bin_low=30.0, bin_high=30.0, bin_unit="C",
                mu=30.85, predictive_sigma=1.5, settle_value=trunc,
            )
            is None
        )
        reason31 = direction_law_rejection_reason(
            direction="buy_yes", bin_low=31.0, bin_high=31.0, bin_unit="C",
            mu=30.85, predictive_sigma=1.5, settle_value=trunc,
        )
        assert reason31 is not None and "buy_yes" in reason31


class TestThreshold:
    def test_threshold_never_below_one_settlement_step(self):
        assert direction_law_threshold(unit="C", predictive_sigma=0.3) == 1.0
        assert direction_law_threshold(unit="F", predictive_sigma=0.3) == 2.0

    def test_threshold_scales_with_sigma(self):
        assert direction_law_threshold(unit="C", predictive_sigma=2.5) == pytest.approx(2.5)

    def test_missing_sigma_is_strictly_conservative(self):
        # No fusion sigma -> 1 step only. A settlement-floored q-std (~3C) must
        # NEVER widen the band (it would re-admit the incident trade).
        assert direction_law_threshold(unit="C", predictive_sigma=None) == 1.0

    def test_nonfinite_sigma_degrades_to_step(self):
        assert direction_law_threshold(unit="C", predictive_sigma=float("nan")) == 1.0
        assert direction_law_threshold(unit="C", predictive_sigma=-1.0) == 1.0


class TestMissingCenterFailsClosedForYes:
    def test_buy_yes_with_no_center_rejected(self):
        reason = direction_law_rejection_reason(
            direction="buy_yes",
            bin_low=24.0,
            bin_high=24.0,
            bin_unit="C",
            mu=None,
            predictive_sigma=None,
        )
        assert reason is not None and "mu=missing" in reason

    def test_buy_no_with_no_center_abstains(self):
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=24.0,
                bin_high=24.0,
                bin_unit="C",
                mu=None,
                predictive_sigma=None,
            )
            is None
        )

    def test_non_buy_directions_abstain(self):
        assert (
            direction_law_rejection_reason(
                direction="sell_yes",
                bin_low=24.0,
                bin_high=24.0,
                bin_unit="C",
                mu=20.0,
                predictive_sigma=None,
            )
            is None
        )


class TestDistanceAndUnits:
    def test_distance_inside_is_zero(self):
        assert bin_forecast_distance(bin_low=24.0, bin_high=25.0, mu=24.5) == 0.0
        assert bin_forecast_distance(bin_low=24.0, bin_high=24.0, mu=24.0) == 0.0

    def test_distance_to_nearest_bound(self):
        assert bin_forecast_distance(bin_low=24.0, bin_high=24.0, mu=26.42) == pytest.approx(2.42)
        assert bin_forecast_distance(bin_low=24.0, bin_high=24.0, mu=22.0) == pytest.approx(2.0)

    def test_both_bounds_missing_raises(self):
        with pytest.raises(ValueError):
            bin_forecast_distance(bin_low=None, bin_high=None, mu=20.0)

    def test_celsius_to_fahrenheit_point_and_delta(self):
        assert celsius_to_unit(26.0, "F") == pytest.approx(78.8)
        assert celsius_to_unit(26.0, "C") == 26.0
        assert celsius_delta_to_unit(1.0, "F") == pytest.approx(1.8)
        with pytest.raises(ValueError):
            celsius_to_unit(26.0, "K")

    def test_fahrenheit_law_end_to_end(self):
        # NYC-style 2F bins: center 79F, forecast 26.42C = 79.56F -> inside-band YES ok.
        assert (
            direction_law_rejection_reason(
                direction="buy_yes",
                bin_low=78.0,
                bin_high=80.0,
                bin_unit="F",
                mu=celsius_to_unit(INCIDENT_MU_C, "F"),
                predictive_sigma=celsius_delta_to_unit(INCIDENT_SIGMA_C, "F"),
            )
            is None
        )
        # A bin 2.42C (4.36F) away fails even the F threshold max(2, 2.27)=2.27F.
        assert (
            direction_law_rejection_reason(
                direction="buy_yes",
                bin_low=74.0,
                bin_high=76.0,
                bin_unit="F",
                mu=celsius_to_unit(INCIDENT_MU_C, "F"),
                predictive_sigma=celsius_delta_to_unit(INCIDENT_SIGMA_C, "F"),
            )
            is not None
        )


class TestBuyNoDoctrineHalf:
    """Operator standing law restored 2026-06-11: buy_no ⟺ bin≠forecast.

    The ONLY banned bin for buy_no is the FORECAST BIN — the bin the canonically
    rounded center settles into (grade_receipt symmetry: the one bin where buy_no
    loses if the forecast settles exactly). The σ-distance over-implementation
    banned every adjacent bin and structurally zeroed the favorite-longshot
    harvest (live incident: 18 cities' positive-EV adjacent-bin NO candidates,
    +0.14..+0.40 ev/$, all killed while coverage-LICENSED — 2026-06-11 16:07Z).
    """

    def test_moscow_adjacent_bin_buy_no_admitted(self):
        # Live incident replay: mu=30.795 settles 31; the 30C bin is NOT the
        # forecast bin -> buy_no admissible regardless of sigma.
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=30.0,
                bin_high=30.0,
                bin_unit="C",
                mu=30.7950,
                predictive_sigma=1.9102,
            )
            is None
        )

    def test_moscow_forecast_bin_buy_no_rejected(self):
        # mu=30.795 settles 31 -> the 31C bin IS the forecast bin -> banned.
        reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=31.0,
            bin_high=31.0,
            bin_unit="C",
            mu=30.7950,
            predictive_sigma=1.9102,
        )
        assert reason is not None and "forecast_bin" in reason

    def test_fahrenheit_range_bin_rounding(self):
        # Atlanta replay: mu_F=93.5186 settles 94 (WMO half-up) -> the 94-95 bin
        # is the forecast bin (banned). The center sits 0.0186F from the 93.5
        # boundary — inside the boundary zone (0.25 x 2F step = 0.5F) — so the
        # straddling 92-93 bin is ALSO banned (operator directive 2026-06-11,
        # Denver knife-edge class). The next bin out stays admissible.
        reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=94.0,
            bin_high=95.0,
            bin_unit="F",
            mu=93.5186,
            predictive_sigma=1.8287,
        )
        assert reason is not None and "forecast_bin" in reason
        zone_reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=92.0,
            bin_high=93.0,
            bin_unit="F",
            mu=93.5186,
            predictive_sigma=1.8287,
        )
        assert zone_reason is not None and "forecast_boundary_zone" in zone_reason
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=96.0,
                bin_high=97.0,
                bin_unit="F",
                mu=93.5186,
                predictive_sigma=1.8287,
            )
            is None
        )

    def test_caller_supplied_truncation_preimage_wins(self):
        # Hong Kong class (HKO/UMA truncation): mu=30.795 truncates to 30 — the
        # caller passes the per-city mu_settled and the 30C bin becomes the
        # forecast bin (banned) while 31C is admissible: the OPPOSITE of WMO.
        reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=30.0,
            bin_high=30.0,
            bin_unit="C",
            mu=30.7950,
            predictive_sigma=1.5,
            mu_settled=30.0,
        )
        assert reason is not None and "forecast_bin" in reason
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=31.0,
                bin_high=31.0,
                bin_unit="C",
                mu=30.7950,
                predictive_sigma=1.5,
                mu_settled=30.0,
            )
            is None
        )

    def test_open_ended_forecast_bin_still_banned(self):
        # "31C or higher" with mu settling 31 -> the open tail bin IS the
        # forecast bin -> buy_no banned (unchanged protective behavior).
        reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=31.0,
            bin_high=None,
            bin_unit="C",
            mu=31.2,
            predictive_sigma=2.0,
        )
        assert reason is not None and "forecast_bin" in reason

    def test_buy_yes_half_far_tail_still_rejected(self):
        # Milan killer preserved under modal-only: far buy_yes (bin 24, mu 26.42
        # settles 26) is still rejected — a far bin is never the forecast bin.
        reason = direction_law_rejection_reason(
            direction="buy_yes",
            bin_low=24.0,
            bin_high=24.0,
            bin_unit="C",
            mu=INCIDENT_MU_C,
            predictive_sigma=INCIDENT_SIGMA_C,
        )
        assert reason is not None and "buy_yes" in reason


class TestBoundaryZone:
    """Operator directive 2026-06-11 (Denver first fill): mu within 0.25 step of
    a preimage boundary makes BOTH straddling bins forecast bins for buy_no —
    we bought NO at 0.60 on the 90-91F bin while mu=89.37F sat 0.13F from the
    89/90 boundary (q_yes 0.211 vs 0.207, co-modal): betting against our own
    forecast's plausible landing spot, plus spread and fee."""

    def test_denver_replay_both_straddling_bins_banned(self):
        # mu=89.37F settles 89 -> 88-89 is the forecast bin (banned) AND the
        # boundary zone (89.37+0.5=89.87 -> settles 90) bans 90-91 too.
        for low, high in ((88.0, 89.0), (90.0, 91.0)):
            reason = direction_law_rejection_reason(
                direction="buy_no",
                bin_low=low,
                bin_high=high,
                bin_unit="F",
                mu=89.37,
                predictive_sigma=3.6,
            )
            assert reason is not None, (low, high)
        # the next bin out (92-93) stays admissible.
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=92.0,
                bin_high=93.0,
                bin_unit="F",
                mu=89.37,
                predictive_sigma=3.6,
            )
            is None
        )

    def test_moscow_replay_stays_open(self):
        # mu=30.795C: 0.295 step from the 30.5 boundary (> 0.25 zone) -> only
        # the 31C forecast bin is banned; the 30C bin stays admissible.
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=30.0,
                bin_high=30.0,
                bin_unit="C",
                mu=30.7950,
                predictive_sigma=1.91,
            )
            is None
        )
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=31.0,
                bin_high=31.0,
                bin_unit="C",
                mu=30.7950,
                predictive_sigma=1.91,
            )
            is not None
        )


class TestSettleValueCallable:
    """Antibody: settle_value callable is the single rounding authority for
    non-WMO cities.

    The boundary-zone approximation bug: the old code used a WMO-delta shift
    (settled + round_wmo(shifted) − round_wmo(mu)) even when the caller
    supplied a per-city mu_settled from a truncation family.  For a truncation
    city (HKO/UMA: floor rounding) with mu=30.85C and zone=0.25C:

        shifted+ = 31.10C
        truncation: floor(31.10) = 31 → bin [31,31] → INSIDE → BANNED  ✓
        WMO-delta:  settled=30, round_wmo(31.10)=31, round_wmo(30.85)=31
                    shifted_settled = 30 + 31 − 31 = 30 → bin [31,31] DISTANCE=1
                    → NOT banned  ✗  (wrong verdict)

    The settle_value parameter closes this gap: apply the city's actual
    rounding rule directly to each shifted point.

    The WMO replay (Denver, Moscow) must be unchanged to pin non-regression.
    """

    # Truncation (floor) callable — stands in for HKO/UMA SettlementSemantics.
    @staticmethod
    def _truncation_settle(v: float) -> float:
        """floor() rounding in integer °C — the HKO/UMA preimage rule."""
        import math as _math
        return float(_math.floor(v))

    def test_wmo_replay_unchanged_denver(self):
        """WMO cities: settle_value=None gives the same result as before."""
        # Denver: both 88-89 and 90-91 still banned; 92-93 still open.
        for low, high in ((88.0, 89.0), (90.0, 91.0)):
            assert (
                direction_law_rejection_reason(
                    direction="buy_no",
                    bin_low=low,
                    bin_high=high,
                    bin_unit="F",
                    mu=89.37,
                    predictive_sigma=3.6,
                    settle_value=None,
                )
                is not None
            ), f"Denver replay: bin [{low},{high}] should be banned"
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=92.0,
                bin_high=93.0,
                bin_unit="F",
                mu=89.37,
                predictive_sigma=3.6,
                settle_value=None,
            )
            is None
        )

    def test_wmo_replay_unchanged_moscow(self):
        """WMO cities: settle_value=None: Moscow 30C stays open, 31C banned."""
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=30.0,
                bin_high=30.0,
                bin_unit="C",
                mu=30.7950,
                predictive_sigma=1.91,
                settle_value=None,
            )
            is None
        )
        assert (
            direction_law_rejection_reason(
                direction="buy_no",
                bin_low=31.0,
                bin_high=31.0,
                bin_unit="C",
                mu=30.7950,
                predictive_sigma=1.91,
                settle_value=None,
            )
            is not None
        )

    def test_truncation_city_zone_ban_31c_bin(self):
        """Truncation city: mu=30.85C, zone=0.25C.

        truncation settle: floor(30.85)=30, floor(31.10)=31.
        shifted+ = 31.10 → truncation→31 → bin [31,31] INSIDE → BANNED.
        The 31C bin must be banned via the boundary-zone path when the
        truncation callable is supplied.
        """
        reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=31.0,
            bin_high=31.0,
            bin_unit="C",
            mu=30.85,
            predictive_sigma=1.5,
            settle_value=self._truncation_settle,
        )
        assert reason is not None, (
            "truncation city: bin [31,31] must be zone-banned when mu=30.85 and "
            "shifted+=31.10 floors to 31"
        )
        assert "forecast_boundary_zone" in reason

    def test_truncation_city_wmo_delta_gives_different_verdict(self):
        """Honest divergence test: the OLD WMO-delta path gives the WRONG verdict.

        For mu=30.85C with mu_settled=30 (truncation-supplied scalar) and
        no settle_value callable:
            shifted+ = 31.10
            WMO-delta: settled + round_wmo(31.10) − round_wmo(30.85)
                     = 30 + 31 − 31 = 30
            bin [31,31]: distance from 30 = 1 → NOT banned  (wrong)

        With settle_value=truncation: floor(31.10)=31 → bin [31,31] INSIDE → BANNED (correct).

        This test pins that the two paths diverge on this case, proving the
        fix is not vacuous.
        """
        mu = 30.85
        # Old path: mu_settled supplied as truncation scalar, no callable.
        # WMO-delta approximation: round_wmo(30.85)=31, round_wmo(31.10)=31,
        # shifted_settled = 30 + 31 - 31 = 30 → bin [31,31] not banned.
        old_path_reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=31.0,
            bin_high=31.0,
            bin_unit="C",
            mu=mu,
            predictive_sigma=1.5,
            mu_settled=30.0,  # truncation-rounded scalar
            settle_value=None,  # old approximation
        )
        assert old_path_reason is None, (
            "WMO-delta approximation must NOT ban bin [31,31] for mu=30.85 with "
            "mu_settled=30 (this proves the divergence — old code misses this ban)"
        )

        # New path: settle_value callable (truncation).
        # floor(31.10)=31 → bin [31,31] INSIDE → BANNED.
        new_path_reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=31.0,
            bin_high=31.0,
            bin_unit="C",
            mu=mu,
            predictive_sigma=1.5,
            settle_value=self._truncation_settle,
        )
        assert new_path_reason is not None, (
            "settle_value callable must ban bin [31,31] for mu=30.85 via "
            "boundary-zone: floor(31.10)=31 is inside [31,31]"
        )
        assert "forecast_boundary_zone" in new_path_reason

    def test_truncation_city_primary_forecast_bin_uses_callable(self):
        """settle_value is the authority for the PRIMARY test too.

        mu=30.85, truncation settle=30 → bin [30,30] is the forecast bin (banned).
        bin [31,31] is NOT the forecast bin from the primary test; it is ONLY banned
        via the zone path (tested above).
        """
        reason = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=30.0,
            bin_high=30.0,
            bin_unit="C",
            mu=30.85,
            predictive_sigma=1.5,
            settle_value=self._truncation_settle,
        )
        assert reason is not None and "forecast_bin" in reason

    def test_settle_value_wins_over_mu_settled(self):
        """When both settle_value and mu_settled are supplied, settle_value WINS.

        mu=30.85: mu_settled=31 (WMO scalar), settle_value=truncation → settled=30.
        Primary test: bin [30,30] must be banned (callable result), NOT [31,31].
        """
        reason_30 = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=30.0,
            bin_high=30.0,
            bin_unit="C",
            mu=30.85,
            predictive_sigma=1.5,
            mu_settled=31.0,          # WMO scalar (should be ignored)
            settle_value=self._truncation_settle,  # truncation wins
        )
        assert reason_30 is not None and "forecast_bin" in reason_30

        reason_31 = direction_law_rejection_reason(
            direction="buy_no",
            bin_low=31.0,
            bin_high=31.0,
            bin_unit="C",
            mu=30.85,
            predictive_sigma=1.5,
            mu_settled=31.0,          # WMO scalar (should be ignored)
            settle_value=self._truncation_settle,  # truncation wins: settled=30≠31
        )
        # [31,31] is NOT the primary forecast bin (truncation gives 30).
        # It IS zone-banned (shifted+=31.10→31), so reason_31 is still not None
        # but via boundary_zone, not forecast_bin.
        assert reason_31 is not None
        assert "forecast_boundary_zone" in reason_31
