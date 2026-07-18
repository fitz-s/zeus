# Created: 2026-07-11
# Last reused/audited: 2026-07-14
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §1f;
# current-evidence finite-sample and moment-ambiguity algebra.
"""First-principles symmetry tests for source-clock executable probability."""

from __future__ import annotations

import math

from src.data.replacement_forecast_materializer import (
    FAR_TAIL_LCB_FLOOR,
    _build_fused_q_bounds,
    _current_evidence_tail_ucb_floors,
    _finite_evidence_binomial_ucb,
    _finite_evidence_zero_hit_ucb_floor,
    _stress_coherent_samples_to_marginal_ucb_floors,
)


class _Bin:
    def __init__(
        self,
        bin_id: str,
        lower_c: float | None,
        upper_c: float | None,
    ) -> None:
        self.bin_id = bin_id
        self.lower_c = lower_c
        self.upper_c = upper_c


def _bins() -> list[_Bin]:
    return [
        _Bin("low", None, 18.0),
        _Bin("far", 19.0, 20.0),
        _Bin("center", 21.0, 23.0),
        _Bin("high", 24.0, None),
    ]


def test_current_tail_floor_combines_member_limit_and_moment_ambiguity() -> None:
    bins = [_Bin("39C", 39.0, 39.0)]
    mu = 36.5151
    sigma = 0.527789
    zero_hit_members = [36.0] * 51
    zero_hit = 1.0 - 0.05 ** (1.0 / 51.0)
    gap = 38.5 - mu
    cantelli = sigma**2 / (sigma**2 + gap**2)

    floors = _current_evidence_tail_ucb_floors(
        mu_star=mu,
        predictive_sigma_c=sigma,
        bins=bins,
        half_step=0.5,
        rounding_rule="wmo_half_up",
        members_c=zero_hit_members,
    )

    assert math.isclose(_finite_evidence_zero_hit_ucb_floor(51), zero_hit)
    assert cantelli > zero_hit
    assert math.isclose(floors["39C"], cantelli, rel_tol=0.0, abs_tol=1e-15)
    assert 1.0 - floors["39C"] < 0.934

    one_hit_floor = _current_evidence_tail_ucb_floors(
        mu_star=mu,
        predictive_sigma_c=sigma,
        bins=bins,
        half_step=0.5,
        rounding_rule="wmo_half_up",
        members_c=[36.0] * 50 + [39.0],
    )["39C"]
    assert math.isclose(one_hit_floor, _finite_evidence_binomial_ucb(1, 51))
    assert one_hit_floor > floors["39C"]


def test_source_clock_band_is_symmetric_coherent_and_has_no_historical_floor() -> None:
    bins = _bins()
    q_point = {"low": 0.04, "far": 0.18, "center": 0.55, "high": 0.23}
    members = [18.0] * 2 + [19.5] * 9 + [22.0] * 28 + [25.0] * 12
    floors = _current_evidence_tail_ucb_floors(
        mu_star=22.0,
        predictive_sigma_c=2.0,
        bins=bins,
        half_step=0.5,
        rounding_rule="wmo_half_up",
        members_c=members,
    )

    lcb, ucb, samples = _build_fused_q_bounds(
        mu_star=22.0,
        center_sigma_c=0.1,
        predictive_sigma_c=2.0,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=400,
        rounding_rule="wmo_half_up",
        evidence_members_c=members,
        return_samples=True,
    )

    assert lcb["low"] > FAR_TAIL_LCB_FLOOR
    assert math.isclose(ucb["low"], floors["low"], rel_tol=0.0, abs_tol=1e-12)
    rows = zip(*(samples[bin_.bin_id] for bin_ in bins), strict=True)
    assert all(math.isclose(sum(row), 1.0, abs_tol=1e-12) for row in rows)
    no_samples = sorted(1.0 - value for value in samples["low"])
    no_lower_cvar = sum(no_samples[:20]) / 20
    assert no_lower_cvar <= 1.0 - floors["low"] + 1e-12


def test_zero_hit_floor_is_encoded_in_one_coherent_simplex() -> None:
    raw = [[0.0, 1.0] for _ in range(100)]
    zero_hit_ucb = _finite_evidence_zero_hit_ucb_floor(2)

    stressed = _stress_coherent_samples_to_marginal_ucb_floors(
        raw,
        [zero_hit_ucb, 1.0],
    )

    assert all(math.isclose(float(row.sum()), 1.0, abs_tol=1e-12) for row in stressed)
    assert float(sorted(stressed[:, 0])[94]) >= zero_hit_ucb
    no_samples = sorted(1.0 - float(value) for value in stressed[:, 0])
    assert sum(no_samples[:5]) / 5.0 <= 1.0 - zero_hit_ucb + 1e-12


def test_tail_stress_preserves_existing_certain_rows_and_raises_only_deficit() -> None:
    raw = [[1.0, 0.0] for _ in range(4)] + [[0.0, 1.0] for _ in range(96)]
    zero_hit_ucb = _finite_evidence_zero_hit_ucb_floor(2)

    stressed = _stress_coherent_samples_to_marginal_ucb_floors(
        raw,
        [zero_hit_ucb, 1.0],
    )

    assert all(math.isclose(float(row.sum()), 1.0, abs_tol=1e-12) for row in stressed)
    assert all(stressed[index, 0] == 1.0 for index in range(4))
    assert float(sorted(stressed[:, 0])[94]) >= zero_hit_ucb


def test_day0_absorbing_fact_dominates_forecast_ambiguity() -> None:
    # obs=24 makes low/far/center all settlement-IMPOSSIBLE (upper preimage <= obs);
    # an impossible bin must never carry the finite-evidence tail floor.
    bins = _bins()
    floor = _finite_evidence_zero_hit_ucb_floor(51)

    _, ucb = _build_fused_q_bounds(
        mu_star=22.0,
        center_sigma_c=0.1,
        predictive_sigma_c=2.0,
        bins=bins,
        half_step=0.5,
        q_point={"low": 0.0, "far": 0.0, "center": 0.0, "high": 1.0},
        n_draws=400,
        rounding_rule="wmo_half_up",
        day0_observed_extreme_c=24.0,
        day0_metric="high",
        evidence_members_c=[22.0] * 51,
    )

    assert ucb["low"] < floor


def test_day0_possible_bin_keeps_finite_evidence_tail_floor() -> None:
    # Regression (2026-07-18): Day0 conditioning absorbs the IMPOSSIBLE bins, but the
    # remaining POSSIBLE bins are still a finite-current-evidence forecast and must keep
    # the member/moment tail floor. Previously _build_fused_q_bounds skipped the floor
    # whenever day0_obs was set, leaving Day0 possible-bin q_ucb overconfident
    # (settlement-graded proof: docs/evidence/upstream_physical_2026_07_17/
    # day0_possible_bin_humility_floor_proof.md).
    bins = _bins()  # low<=18, far 19-20, center 21-23, high 24+
    obs = 19.0
    mu, sigma = 22.0, 2.0
    members = [22.0] * 51  # all members at the center -> "far" is a ZERO-HIT possible bin

    floors = _current_evidence_tail_ucb_floors(
        mu_star=mu,
        predictive_sigma_c=sigma,
        bins=bins,
        half_step=0.5,
        rounding_rule="wmo_half_up",
        members_c=members,
        day0_observed_extreme_c=obs,
        day0_metric="high",
    )
    # "low" (upper preimage 18.5 <= obs) is settlement-impossible -> floor masked to 0.
    assert floors["low"] == 0.0
    # "far" preimage [18.5, 20.5] straddles/sits below mu and is POSSIBLE -> its Cantelli
    # moment floor survives (dominates the zero-hit binomial UCB here).
    gap = mu - 20.5
    moment_far = sigma**2 / (sigma**2 + gap**2)
    assert math.isclose(floors["far"], moment_far, rel_tol=0.0, abs_tol=1e-12)
    assert floors["far"] > _finite_evidence_zero_hit_ucb_floor(51)

    _, ucb = _build_fused_q_bounds(
        mu_star=mu,
        center_sigma_c=0.1,
        predictive_sigma_c=sigma,
        bins=bins,
        half_step=0.5,
        q_point={"low": 0.0, "far": 0.0, "center": 0.55, "high": 0.45},
        n_draws=400,
        rounding_rule="wmo_half_up",
        day0_observed_extreme_c=obs,
        day0_metric="high",
        evidence_members_c=members,
    )
    # The possible zero-hit bin retains its tail floor even under Day0 conditioning...
    assert ucb["far"] >= floors["far"] - 1e-9
    # ...while the impossible below-obs bin stays hard-zero (settlement support intact).
    assert ucb["low"] <= 1e-9
