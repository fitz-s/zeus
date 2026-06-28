# Created: 2026-06-25
# Last reused/audited: 2026-06-25
"""Source-clock vNext admission law antibodies."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from src.strategy.live_inference.source_clock_vnext import (
    CityMetricLeadSourceWeight,
    IntraHourExtremeCorrection,
    MarketReactionState,
    SourceClockAdmissionInput,
    SourceRunClock,
    SourceWeightObservation,
    SettlementStationGeometry,
    admission_calibration_gap,
    apply_intra_hour_extreme_correction,
    geometry_admits_finer_signal,
    market_reaction_bucket,
    market_reaction_fraction,
    no_side_lcb_from_yes_ucb,
    provider_family_for_source,
    source_clock_log_utility_admission,
    source_is_publicly_usable,
    sparse_settlement_source_weights,
    source_clock_redecision_scope,
)


BASE_TIME = datetime(2026, 6, 25, 14, 0, tzinfo=UTC)


def _run(
    *,
    available_at: datetime = BASE_TIME,
    freshness_state: str = "FRESH",
    spatial_resolution_km: float = 13.0,
) -> SourceRunClock:
    return SourceRunClock(
        source_id="icon_global",
        provider_family="icon",
        run_initialisation_time=available_at - timedelta(hours=4),
        run_availability_time=available_at,
        zeus_observed_time=available_at,
        update_interval_seconds=6 * 3600,
        temporal_resolution_seconds=3600,
        spatial_resolution_km=spatial_resolution_km,
        forecast_horizon_hours=72,
        api_surface="open_meteo_model_updates",
        freshness_state=freshness_state,
    )


def _reaction(*, rho: float | None) -> MarketReactionState:
    return MarketReactionState(
        token_id="token-1",
        source_update_id="icon_global|2026-06-25T14:00:00Z",
        q_shock=0.10,
        price_pre=0.42,
        price_15m=None if rho is None else 0.42 + 0.10 * rho,
        reaction_fraction=rho,
    )


def test_source_clock_waits_for_public_availability_plus_ten_minutes() -> None:
    run = _run()
    assert not source_is_publicly_usable(run, decision_time=BASE_TIME + timedelta(minutes=9, seconds=59))
    assert source_is_publicly_usable(run, decision_time=BASE_TIME + timedelta(minutes=10))


def test_source_clock_admission_rejects_before_availability_wait() -> None:
    decision = source_clock_log_utility_admission(
        SourceClockAdmissionInput(
            q_point=0.72,
            q_lcb=0.66,
            executable_cost=0.52,
            decision_time=BASE_TIME + timedelta(minutes=5),
            source_run=_run(),
            market_reaction=_reaction(rho=0.0),
        )
    )

    assert not decision.admitted
    assert decision.reason == "SOURCE_CLOCK_NOT_PUBLICLY_USABLE"


def test_underreacted_market_with_positive_log_utility_admits() -> None:
    decision = source_clock_log_utility_admission(
        SourceClockAdmissionInput(
            q_point=0.72,
            q_lcb=0.66,
            executable_cost=0.52,
            decision_time=BASE_TIME + timedelta(minutes=12),
            source_run=_run(),
            market_reaction=_reaction(rho=0.10),
        )
    )

    assert decision.admitted
    assert decision.reason is None
    assert decision.market_reaction_bucket == "underreacted"
    assert decision.q_exec_lcb == pytest.approx(0.646)
    assert decision.expected_log_growth > 0.0
    assert decision.kelly_spend_fraction > 0.0


def test_absorbed_market_reaction_removes_q_exec_edge() -> None:
    decision = source_clock_log_utility_admission(
        SourceClockAdmissionInput(
            q_point=0.72,
            q_lcb=0.66,
            executable_cost=0.52,
            decision_time=BASE_TIME + timedelta(minutes=12),
            source_run=_run(),
            market_reaction=_reaction(rho=1.0),
        )
    )

    assert not decision.admitted
    assert decision.reason == "SOURCE_CLOCK_LOG_UTILITY_NON_POSITIVE"
    assert decision.q_exec_lcb == pytest.approx(0.52)
    assert decision.edge_lcb == pytest.approx(0.0)


def test_unknown_market_reaction_fails_closed_by_default() -> None:
    decision = source_clock_log_utility_admission(
        SourceClockAdmissionInput(
            q_point=0.72,
            q_lcb=0.66,
            executable_cost=0.52,
            decision_time=BASE_TIME + timedelta(minutes=12),
            source_run=_run(),
            market_reaction=None,
        )
    )

    assert not decision.admitted
    assert decision.reason == "SOURCE_CLOCK_MARKET_REACTION_UNKNOWN"


def test_lcb_above_point_is_rejected_before_trade_math() -> None:
    decision = source_clock_log_utility_admission(
        SourceClockAdmissionInput(
            q_point=0.61,
            q_lcb=0.62,
            executable_cost=0.52,
            decision_time=BASE_TIME + timedelta(minutes=12),
            source_run=_run(),
            market_reaction=_reaction(rho=0.0),
        )
    )

    assert not decision.admitted
    assert decision.reason == "SOURCE_CLOCK_ADMISSION_LCB_EXCEEDS_POINT"


def test_no_side_lcb_uses_yes_ucb_not_yes_lcb() -> None:
    yes_q_lcb = 0.62
    yes_q_ucb = 0.74

    assert no_side_lcb_from_yes_ucb(yes_q_ucb) == pytest.approx(0.26)
    assert no_side_lcb_from_yes_ucb(yes_q_ucb) != pytest.approx(1.0 - yes_q_lcb)


def test_sparse_weights_family_dedup_and_stale_zero_weight() -> None:
    weights = sparse_settlement_source_weights(
        [
            SourceWeightObservation(
                source_id="ecmwf_ifs",
                provider_family="ecmwf",
                settlement_logloss=0.68,
                brier=0.21,
            ),
            SourceWeightObservation(
                source_id="ecmwf_aifs",
                provider_family="ecmwf",
                settlement_logloss=0.61,
                brier=0.20,
            ),
            SourceWeightObservation(
                source_id="icon_global",
                provider_family="icon",
                settlement_logloss=0.63,
                brier=0.19,
            ),
            SourceWeightObservation(
                source_id="gfs_hrrr",
                provider_family="ncep",
                settlement_logloss=0.55,
                brier=0.18,
                stale=True,
            ),
        ],
        softmax_temperature=0.10,
    )

    by_source = {w.source_id: w for w in weights}
    assert set(by_source) == {"ecmwf_aifs", "icon_global"}
    assert "ecmwf_ifs" not in by_source
    assert "gfs_hrrr" not in by_source
    assert sum(w.weight for w in weights) == pytest.approx(1.0)
    assert all(w.weight >= 0.0 for w in weights)


def test_provider_family_normalizes_ncep_regional_sources() -> None:
    assert provider_family_for_source("gfs_hrrr") == "ncep"
    assert provider_family_for_source("nam_conus") == "ncep"
    assert provider_family_for_source("ncep_nbm_conus") == "ncep"
    assert provider_family_for_source("icon_eu") == "dwd_icon"


def test_station_geometry_blocks_coarse_source_without_certificate() -> None:
    geometry = SettlementStationGeometry(
        city="Los Angeles",
        metric="high",
        market_source="WU",
        settlement_station_id="KCA",
        station_lat=34.0,
        station_lon=-118.0,
        station_elevation=90.0,
        source_id="icon_global",
        model_grid_lat=34.1,
        model_grid_lon=-118.1,
        model_grid_elevation=120.0,
        grid_distance_km=11.0,
        elevation_delta_m=30.0,
        cell_selection="nearest",
        station_alignment_score=0.2,
    )
    coarse = _run(spatial_resolution_km=11.0)

    assert not geometry_admits_finer_signal(run=coarse, geometry=geometry)
    assert geometry_admits_finer_signal(
        run=coarse,
        geometry=geometry,
        station_aligned_certificate=True,
    )


def test_intra_hour_extreme_correction_is_metric_separated() -> None:
    high_correction = IntraHourExtremeCorrection(
        city="Paris",
        metric="high",
        lead=1,
        source_id="meteofrance_arome_france_hd",
        season_bucket="summer",
        correction_c=0.35,
    )

    assert apply_intra_hour_extreme_correction(
        raw_daily_extreme_c=28.0,
        metric="high",
        correction=high_correction,
    ) == pytest.approx(28.35)
    with pytest.raises(ValueError):
        apply_intra_hour_extreme_correction(
            raw_daily_extreme_c=18.0,
            metric="low",
            correction=high_correction,
        )


def test_source_clock_redecision_scope_only_returns_affected_positive_weight_families() -> None:
    scope = source_clock_redecision_scope(
        ["icon_global"],
        [
            CityMetricLeadSourceWeight("Miami", "high", 1, "icon_global", "dwd_icon", 0.6),
            CityMetricLeadSourceWeight("Miami", "high", 2, "ecmwf_ifs", "ecmwf", 0.4),
            CityMetricLeadSourceWeight("NYC", "low", 1, "icon_global", "dwd_icon", 0.0),
            CityMetricLeadSourceWeight("Paris", "high", 1, "icon_eu", "dwd_icon", 0.8),
        ],
    )

    assert scope == (("Miami", "high", 1),)


def test_market_reaction_fraction_and_bucket() -> None:
    rho = market_reaction_fraction(q_shock=0.10, price_pre=0.40, price_after=0.42)
    assert rho == pytest.approx(0.20)
    assert market_reaction_bucket(rho) == "underreacted"
    assert market_reaction_bucket(1.1) == "absorbed"
    assert market_reaction_bucket(1.4) == "overreacted"
    assert market_reaction_bucket(None) == "reaction_unknown"


def test_admission_calibration_gap_is_realized_minus_q_point() -> None:
    assert admission_calibration_gap(q_point=0.64, realized_hit=True) == pytest.approx(0.36)
    assert admission_calibration_gap(q_point=0.64, realized_hit=False) == pytest.approx(-0.64)
    with pytest.raises(ValueError):
        admission_calibration_gap(q_point=math.nan, realized_hit=True)
