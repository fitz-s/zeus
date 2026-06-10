# Created: 2026-06-07
# Last reused/audited: 2026-06-09
# Lifecycle: created=2026-06-07; last_reviewed=2026-06-07; last_reused=2026-06-09
# 2026-06-09 STALE_LAW re-pin: smoothing flag promoted default-OFF -> default-ON
#   (config edli_v1.replacement_0_1_member_vote_smoothing_enabled=true). The shipped-
#   config resolver test now asserts the default alpha; OFF inertness stays covered by
#   the monkeypatch tests.
# Purpose: Protect the replacement_forecast_materializer wiring of the flag-gated AIFS
#   member-vote (Laplace/Dirichlet) smoothing: flag-OFF materialized posterior byte-identical
#   to today, flag-ON written q matches the direct alpha-smoothed construction, and the shipped
#   default-OFF flag leaves the real resolver inert. Confirms the resolver reads the flag once,
#   fail-closes, and feeds build_openmeteo_ifs9_aifs_soft_anchor_result.
# Authority basis: THE_PATH member-vote smoothing; reuse soft-anchor fusion (no parallel path).
"""Replacement_0_1 member-vote smoothing materializer-wiring tests."""

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
from src.data.replacement_forecast_materializer import ReplacementForecastMaterializeRequest
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import (
    MEMBER_VOTE_SMOOTHING_ALPHA,
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
    # 'warm' (21-30) gets every member high vote (24,26 -> warm; 32 -> hot). 'cool' (<=20) is a
    # ZERO-vote bin; the smoothing lifts its veto so the anchor can mass it. This makes the
    # flag-ON written q DIFFER from raw, which the flag-on test asserts.
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


def _expected_q(*, alpha: float | None) -> dict:
    res = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_aifs_extraction(), openmeteo_anchor=_anchor(), metric="high", bins=_bins(),
        config=SoftAnchorConfig(anchor_weight=0.80, anchor_sigma_c=3.0), settlement_step_c=1.0,
        bias_shift_c=None, member_vote_smoothing_alpha=alpha,
    )
    return {k: float(v) for k, v in res.posterior.probabilities.items()}


# NOTE: the full materialize path enforces strict prewrite gates the lightweight fixtures do not
# satisfy; the smoothing lands in _insert_posterior, so the wiring is exercised directly there.
# The EB-bias resolver is held at None in every test to isolate the smoothing effect.


def test_flag_off_materialized_posterior_byte_identical_to_today(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_replacement_eb_bias_shift_c", lambda request, *, metric: None)
    monkeypatch.setattr(mod, "_replacement_member_vote_smoothing_alpha", lambda: None)
    conn = _conn()
    posterior_id = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    assert _materialized_q(conn, posterior_id) == pytest.approx(_expected_q(alpha=None))


def test_flag_on_materialized_posterior_matches_smoothed_construction(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_replacement_eb_bias_shift_c", lambda request, *, metric: None)
    monkeypatch.setattr(mod, "_replacement_member_vote_smoothing_alpha", lambda: MEMBER_VOTE_SMOOTHING_ALPHA)
    conn = _conn()
    posterior_id = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    got = _materialized_q(conn, posterior_id)
    assert got == pytest.approx(_expected_q(alpha=MEMBER_VOTE_SMOOTHING_ALPHA))
    # The 0-vote 'cool' bin: flag-OFF carries only the negligible structural floor (Fault A fix,
    # never literal-zero / un-hittable), while flag-ON carries MEANINGFUL smoothed mass. The
    # materialized flag-ON q must be that meaningful mass, orders of magnitude above the floor.
    off_cool = _expected_q(alpha=None)["cool"]
    assert 0.0 < off_cool < 1e-9  # structural floor only -- negligible, not a trade
    assert got["cool"] > 1e-9  # flag-ON: the meaningful trade-relevant smoothed mass
    assert got["cool"] > off_cool * 1e6


def test_resolver_shipped_config_flag_is_on_returns_default_alpha() -> None:
    # STALE_LAW re-pin 2026-06-09: replacement_0_1_member_vote_smoothing_enabled was
    # promoted from default-OFF to default-ON (authority: config edli_v1.
    # replacement_0_1_member_vote_smoothing_enabled=true; the alpha key is absent so the
    # resolver falls back to MEMBER_VOTE_SMOOTHING_ALPHA=0.05). The live path now applies
    # Laplace smoothing. (Flag-OFF inertness is still covered by the monkeypatch tests.)
    assert mod._replacement_member_vote_smoothing_alpha() == pytest.approx(
        MEMBER_VOTE_SMOOTHING_ALPHA
    )


def test_resolver_returns_alpha_when_flag_enabled(monkeypatch) -> None:
    import src.config as cfg

    edli = cfg.settings["edli_v1"]  # the underlying mutable dict
    monkeypatch.setitem(edli, "replacement_0_1_member_vote_smoothing_enabled", True)
    monkeypatch.setitem(edli, "replacement_0_1_member_vote_smoothing_alpha", MEMBER_VOTE_SMOOTHING_ALPHA)
    assert mod._replacement_member_vote_smoothing_alpha() == pytest.approx(MEMBER_VOTE_SMOOTHING_ALPHA)


def test_resolver_fail_closed_on_nonpositive_alpha(monkeypatch) -> None:
    import src.config as cfg

    edli = cfg.settings["edli_v1"]  # the underlying mutable dict
    monkeypatch.setitem(edli, "replacement_0_1_member_vote_smoothing_enabled", True)
    monkeypatch.setitem(edli, "replacement_0_1_member_vote_smoothing_alpha", 0.0)  # invalid -> fail-closed
    assert mod._replacement_member_vote_smoothing_alpha() is None
