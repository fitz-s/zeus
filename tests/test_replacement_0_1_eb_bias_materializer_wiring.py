# Created: 2026-06-07
# Last reused/audited: 2026-06-09
# Lifecycle: created=2026-06-07; last_reviewed=2026-06-07; last_reused=2026-06-09
# 2026-06-09 STALE_LAW re-pin: replacement_0_1_member_vote_smoothing_enabled promoted
#   to default-ON; _expected_q now mirrors _insert_posterior's smoothing-alpha wiring
#   (authority: config edli.replacement_0_1_member_vote_smoothing_enabled=true).
# Purpose: Protect the replacement_forecast_materializer wiring of the per-city EB
#   bias-correction: flag-OFF materialized posterior byte-identical to today, flag-ON
#   posterior q matches the direct bias-shifted construction, fail-closed when no shift.
# Authority basis: docs/the_path/P2_BLEND.md §3,§4,§5; reuse zeus-world.model_bias_ens.
"""Replacement_0_1 EB bias-correction materializer-wiring tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

import src.data.replacement_forecast_materializer as mod
from src.data.ecmwf_aifs_sampled_2t_localday import AifsMemberLocalDayExtrema, AifsSampledLocalDayExtraction
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_materializer import (
    ReplacementForecastMaterializeRequest,
    materialize_replacement_forecast_shadow,
)
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import (
    AifsTemperatureBin,
    build_openmeteo_ifs9_aifs_soft_anchor_result,
)
from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import SoftAnchorConfig


UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    # _insert_posterior writes posterior-identity columns added by this helper (normally
    # invoked by materialize_replacement_forecast_shadow before _insert_posterior).
    mod._ensure_replacement_identity_columns(conn)
    return conn


def _aifs_extraction() -> AifsSampledLocalDayExtraction:
    return AifsSampledLocalDayExtraction(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        source_cycle_time=_dt(0),
        target_window_start_utc=_dt(16),
        target_window_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
        members=(
            AifsMemberLocalDayExtrema("pf-001", high_c=24.0, low_c=18.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-002", high_c=26.0, low_c=19.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-003", high_c=32.0, low_c=21.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
        ),
    )


def _anchor() -> OpenMeteoIfs9LocalDayAnchor:
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        high_c=27.0,
        low_c=18.5,
        sample_count=4,
        contributing_local_times=(
            datetime(2026, 6, 7, 0, tzinfo=UTC),
            datetime(2026, 6, 7, 6, tzinfo=UTC),
            datetime(2026, 6, 7, 12, tzinfo=UTC),
            datetime(2026, 6, 7, 18, tzinfo=UTC),
        ),
        contributing_valid_times_utc=(_dt(16), _dt(22), datetime(2026, 6, 7, 4, tzinfo=UTC), datetime(2026, 6, 7, 10, tzinfo=UTC)),
        source_cycle_time=_dt(0),
    )


def _precision_guard():
    return evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(
            city="Shanghai", station_id="ZSSS", city_lat=31.2304, city_lon=121.4737,
            station_lat=31.1979, station_lon=121.3363, requested_lat=31.1979, requested_lon=121.3363,
            requested_coordinate_precision_decimals=4, nearest_grid_lat=31.2, nearest_grid_lon=121.3,
            nearest_grid_distance_km=3.5, native_grid="openmeteo_ecmwf_ifs_9km", delivery_grid_resolution="0p1",
            interpolation_method="nearest_gridpoint", endpoint_mode="hourly_zeus_aggregated",
            local_day_start_utc=_dt(16), local_day_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
            timezone_name="Asia/Shanghai", target_local_date=date(2026, 6, 7), temperature_unit="C",
            anchor_sigma_c=3.0, grid_elevation_m=4.0, station_elevation_m=3.0, land_sea_mask="land",
            city_class="flat_inland", station_mapping_policy="settlement_station",
        )
    )


def _bins() -> tuple[AifsTemperatureBin, ...]:
    return (
        AifsTemperatureBin("cool", upper_c=20.0, center_c=19.0),
        AifsTemperatureBin("warm", lower_c=21.0, upper_c=30.0),
        AifsTemperatureBin("hot", lower_c=31.0, center_c=32.0),
    )


def _request() -> ReplacementForecastMaterializeRequest:
    return ReplacementForecastMaterializeRequest(
        city="Shanghai", city_id="Shanghai", city_timezone="Asia/Shanghai",
        target_date=date(2026, 6, 7), temperature_metric="high",
        baseline_source_run_id="b0-run",
        baseline_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        baseline_source_available_at=_dt(2),
        aifs_extraction=_aifs_extraction(), aifs_source_run_id="aifs-run", aifs_source_available_at=_dt(2, 30),
        openmeteo_anchor=_anchor(), openmeteo_source_run_id="om9-run", openmeteo_source_available_at=_dt(3),
        bins=_bins(), source_cycle_time=_dt(0), computed_at=_dt(4), expires_at=_dt(6),
        anchor_artifact_id=None, aifs_artifact_id=None, openmeteo_precision_guard=_precision_guard(),
    )


def _materialized_q(conn, posterior_id: int) -> dict:
    row = conn.execute("SELECT q_json FROM forecast_posteriors WHERE posterior_id = ?", (posterior_id,)).fetchone()
    return json.loads(row["q_json"])


def _expected_q(*, bias_shift_c: float | None) -> dict:
    # Mirror _insert_posterior EXACTLY: it passes member_vote_smoothing_alpha from the live
    # config helper. The smoothing flag (replacement_0_1_member_vote_smoothing_enabled) was
    # promoted to default-ON 2026-06-09, so the materialized q now carries Laplace smoothing
    # (every bin strictly positive). Reading the SAME helper keeps the expected construction
    # in lockstep with the materializer under any flag state (STALE_LAW re-pin: the previous
    # _expected_q omitted the smoothing alpha and pinned the pre-smoothing shape).
    res = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_aifs_extraction(), openmeteo_anchor=_anchor(), metric="high", bins=_bins(),
        config=SoftAnchorConfig(anchor_weight=0.80, anchor_sigma_c=3.0), settlement_step_c=1.0,
        bias_shift_c=bias_shift_c,
        member_vote_smoothing_alpha=mod._replacement_member_vote_smoothing_alpha(),
    )
    return {k: float(v) for k, v in res.posterior.probabilities.items()}


# NOTE: the full materialize_replacement_forecast_shadow path enforces strict prewrite gates
# (51-AIFS-member coverage, full OM9 hourly coverage) that the lightweight fixtures here do not
# satisfy — that gate is pre-existing and unrelated to this change. The bias-correction lands
# in _insert_posterior, so the wiring is exercised directly at that function (it performs the
# real flag-gated resolve + the soft-anchor construction + the DB write).


def test_flag_off_materialized_posterior_byte_identical_to_today(monkeypatch) -> None:
    # Flag OFF (helper returns None) -> the written q must equal the un-corrected construction.
    monkeypatch.setattr(mod, "_replacement_eb_bias_shift_c", lambda request, *, metric: None)
    conn = _conn()
    posterior_id = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    assert _materialized_q(conn, posterior_id) == pytest.approx(_expected_q(bias_shift_c=None))


def test_flag_on_materialized_posterior_matches_bias_shifted_construction(monkeypatch) -> None:
    # Simulate flag-ON + a resolved per-city cold bias of -3.0C: the written q must equal the
    # direct construction with bias_shift_c=-3.0 (votes + anchor warmed by 3C), and differ from raw.
    monkeypatch.setattr(mod, "_replacement_eb_bias_shift_c", lambda request, *, metric: -3.0)
    conn = _conn()
    posterior_id = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    got = _materialized_q(conn, posterior_id)
    assert got == pytest.approx(_expected_q(bias_shift_c=-3.0))
    assert got != pytest.approx(_expected_q(bias_shift_c=None))


def test_resolver_fail_closed_yields_uncorrected_posterior(monkeypatch) -> None:
    # A fail-closed resolve (no VERIFIED row) -> None -> the written posterior is un-corrected.
    monkeypatch.setattr(mod, "_replacement_eb_bias_shift_c", lambda request, *, metric: None)
    conn = _conn()
    posterior_id = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    assert _materialized_q(conn, posterior_id) == pytest.approx(_expected_q(bias_shift_c=None))


def test_default_config_flag_is_off_so_real_helper_returns_none() -> None:
    # The shipped default-OFF flag means the REAL wiring helper returns None for a city even if
    # model_bias_ens has rows — proving the live path is inert until the operator flips the flag.
    assert mod._replacement_eb_bias_shift_c(_request(), metric="high") is None
