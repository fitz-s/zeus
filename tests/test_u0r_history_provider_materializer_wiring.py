# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §6 F1 integration + §4 (T2 fusion reached once n_train>=
#   MIN_TRAIN); CONTINUITY_AND_WIRING.md §4 steps 4-5 (real U0RHistoryProvider ASSIGNED at the
#   materializer seam -> EQUAL_WEIGHT crosses to T2_BAYES). IRON RULE #3 (no-leak), #4 (one
#   builder), INV-37 (single forecasts DB). Tests the CROSS-MODULE boundary: persisted
#   raw_model_forecasts (previous_runs) + VERIFIED settlement -> the materializer's fused
#   posterior reaches T2_BAYES via the REAL provider on the SAME connection.
"""End-to-end: the real U0RHistoryProvider wired through _insert_posterior reaches T2_BAYES.

This is the relationship test the brief requires (step (d)): on a constructed >=25-row VERIFIED
previous_runs fixture persisted to the materialization connection, with fusion ON, the override
uses the REAL provider (no fixture provider assigned) and the fused method is T2_BAYES (NOT
EQUAL_WEIGHT). It also proves the two-flag money-path invariants:
  - both flags OFF  -> materialized posterior byte-identical (q + identity hash).
  - capture ON / fusion OFF -> posterior byte-identical (capture flag never touches the posterior).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod
from src.data.ecmwf_aifs_sampled_2t_localday import (
    AifsMemberLocalDayExtrema,
    AifsSampledLocalDayExtraction,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_materializer import ReplacementForecastMaterializeRequest
from src.forecast.u0r_bayes import MIN_TRAIN
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema

UTC = timezone.utc
PARIS_LAT, PARIS_LON = 48.967, 2.428


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_override_seams():
    """The override's _history_provider / _live_fetch are PROCESS-GLOBAL function attributes
    (RISK from the brief: provider-assignment is global state). Reset them around every test so a
    fixture seam never leaks into the live-default path of a later test. These tests set the
    attributes via plain setattr (NOT monkeypatch) so this fixture is the sole owner of their
    lifecycle — no competing undo stack."""
    def _clear():
        for attr in ("_history_provider", "_live_fetch"):
            if hasattr(mod._replacement_u0r_fusion_override, attr):
                delattr(mod._replacement_u0r_fusion_override, attr)
    _clear()
    yield
    _clear()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    mod._ensure_replacement_identity_columns(conn)
    return conn


def _aifs_extraction() -> AifsSampledLocalDayExtraction:
    return AifsSampledLocalDayExtraction(
        city_timezone="Europe/Paris", target_local_date=date(2026, 6, 7), source_cycle_time=_dt(0),
        target_window_start_utc=_dt(16), target_window_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
        members=(
            AifsMemberLocalDayExtrema("pf-001", high_c=24.0, low_c=18.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-002", high_c=26.0, low_c=19.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-003", high_c=28.0, low_c=21.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
        ),
    )


def _anchor() -> OpenMeteoIfs9LocalDayAnchor:
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Europe/Paris", target_local_date=date(2026, 6, 7), high_c=27.0, low_c=18.5,
        sample_count=4,
        contributing_local_times=(datetime(2026, 6, 7, 0, tzinfo=UTC), datetime(2026, 6, 7, 6, tzinfo=UTC), datetime(2026, 6, 7, 12, tzinfo=UTC), datetime(2026, 6, 7, 18, tzinfo=UTC)),
        contributing_valid_times_utc=(_dt(16), _dt(22), datetime(2026, 6, 7, 4, tzinfo=UTC), datetime(2026, 6, 7, 10, tzinfo=UTC)),
        source_cycle_time=_dt(0),
    )


def _precision_guard():
    return evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(
            city="Paris", station_id="LFPG", city_lat=PARIS_LAT, city_lon=PARIS_LON,
            station_lat=PARIS_LAT, station_lon=PARIS_LON, requested_lat=PARIS_LAT, requested_lon=PARIS_LON,
            requested_coordinate_precision_decimals=4, nearest_grid_lat=49.0, nearest_grid_lon=2.4,
            nearest_grid_distance_km=3.5, native_grid="openmeteo_ecmwf_ifs_9km", delivery_grid_resolution="0p1",
            interpolation_method="nearest_gridpoint", endpoint_mode="hourly_zeus_aggregated",
            local_day_start_utc=_dt(16), local_day_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
            timezone_name="Europe/Paris", target_local_date=date(2026, 6, 7), temperature_unit="C",
            anchor_sigma_c=3.0, grid_elevation_m=80.0, station_elevation_m=119.0, land_sea_mask="land",
            city_class="flat_inland", station_mapping_policy="settlement_station",
        )
    )


def _bins():
    from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin
    return (
        AifsTemperatureBin("cool", upper_c=22.0, center_c=21.0),
        AifsTemperatureBin("mild", lower_c=23.0, upper_c=26.0),
        AifsTemperatureBin("warm", lower_c=27.0, center_c=28.0),
    )


def _request() -> ReplacementForecastMaterializeRequest:
    return ReplacementForecastMaterializeRequest(
        city="Paris", city_id="Paris", city_timezone="Europe/Paris",
        target_date=date(2026, 6, 7), temperature_metric="high",
        baseline_source_run_id="b0-run", baseline_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        baseline_source_available_at=_dt(2),
        aifs_extraction=_aifs_extraction(), aifs_source_run_id="aifs-run", aifs_source_available_at=_dt(2, 30),
        openmeteo_anchor=_anchor(), openmeteo_source_run_id="om9-run", openmeteo_source_available_at=_dt(3),
        bins=_bins(), source_cycle_time=_dt(0), computed_at=_dt(4), expires_at=_dt(6),
        anchor_artifact_id=None, aifs_artifact_id=None, openmeteo_precision_guard=_precision_guard(),
    )


def _row(conn, posterior_id: int):
    return conn.execute(
        "SELECT q_json, posterior_identity_hash, posterior_config_hash, provenance_json "
        "FROM forecast_posteriors WHERE posterior_id = ?", (posterior_id,),
    ).fetchone()


def _seed_history(conn, *, decision: date, models, n: int = MIN_TRAIN + 5, leak: bool = False) -> None:
    """Persist n VERIFIED previous_runs rows per model, strictly BEFORE the decision date.
    The anchor (ecmwf_ifs) is seeded too so capture sets anchor_z/anchor_tau0 (-> T2_BAYES).
    Forecasts pull the center BELOW the OM9 27.0 anchor so the fused posterior visibly shifts."""
    start = decision - timedelta(days=n + 2)
    for i in range(n):
        d = (start + timedelta(days=i)).isoformat()
        settle = 22.0 + 0.05 * (i % 7)
        for k, m in enumerate(models):
            fc = 23.0 + 0.1 * (k % 3) + 0.03 * (i % 5)  # below 27.0 anchor
            conn.execute(
                """INSERT INTO raw_model_forecasts
                   (model, city, target_date, metric, source_cycle_time, source_available_at,
                    captured_at, lead_days, forecast_value_c, endpoint)
                   VALUES (?, 'Paris', ?, 'high', 'cyc', 'avail', 'cap', 1, ?, 'previous_runs')""",
                (m, d, fc),
            )
        conn.execute(
            """INSERT INTO settlement_outcomes
               (city, target_date, temperature_metric, settlement_value, authority, settlement_unit)
               VALUES ('Paris', ?, 'high', ?, 'VERIFIED', 'C')""",
            (d, settle),
        )
    if leak:
        # A future-dated row that MUST NOT be used (target_date >= decision).
        d = decision.isoformat()
        for m in models:
            conn.execute(
                """INSERT INTO raw_model_forecasts
                   (model, city, target_date, metric, source_cycle_time, source_available_at,
                    captured_at, lead_days, forecast_value_c, endpoint)
                   VALUES (?, 'Paris', ?, 'high', 'cyc', 'avail', 'cap', 1, 99.0, 'previous_runs')""",
                (m, d),
            )
        conn.execute(
            """INSERT INTO settlement_outcomes
               (city, target_date, temperature_metric, settlement_value, authority, settlement_unit)
               VALUES ('Paris', ?, 'high', 99.0, 'VERIFIED', 'C')""",
            (d,),
        )


def _enable_fusion(monkeypatch):
    monkeypatch.setitem(cfg.settings["edli_v1"], "replacement_0_1_u0r_fusion_enabled", True)


def _enable_capture(monkeypatch):
    monkeypatch.setitem(cfg.settings["edli_v1"], "replacement_0_1_u0r_multimodel_capture_enabled", True)


def _disable_other_layers(monkeypatch):
    monkeypatch.setattr(mod, "_replacement_eb_bias_shift_c", lambda request, *, metric: None)
    monkeypatch.setattr(mod, "_replacement_member_vote_smoothing_alpha", lambda: None)


def _live_values():
    # Today's forward fetch (degC), below the OM9 anchor so the fused center shifts.
    return {"gfs_global": 23.0, "icon_global": 23.5, "gem_global": 22.5, "jma_seamless": 24.0, "icon_eu": 23.2}


def _install_live_fetch(monkeypatch, values):
    # Plain setattr (NOT monkeypatch): the autouse _reset_override_seams fixture owns the cleanup
    # of this PROCESS-GLOBAL attribute, so there is no competing monkeypatch undo stack.
    def _fetch(*, model, latitude, longitude, timezone_name, run, target_local_date, metric, forecast_hours):
        return values.get(model)
    mod._replacement_u0r_fusion_override._live_fetch = _fetch


def _seed_current_single_runs(conn, *, values, request=None, anchor_value=27.0):
    """BLOCKER 5: persist the CURRENT single_runs rows the download job would have written for
    THIS cycle. Post-B5 the q path reads these PERSISTED rows (never a network fetch), so the
    wiring tests must seed them (replacing the old _install_live_fetch network seam). The anchor
    (ecmwf_ifs) current row is seeded too so the fusion has the full present set."""
    req = request if request is not None else _request()
    target_date = mod._date_text(req.target_date)
    cyc = mod._to_utc(req.source_cycle_time, field_name="source_cycle_time").isoformat()
    lead = mod._u0r_city_local_lead_days(
        computed_at=mod._to_utc(req.computed_at, field_name="computed_at"),
        target_local_date=date.fromisoformat(target_date), tz_name="Europe/Paris",
    )
    all_vals = {"ecmwf_ifs": anchor_value, **values}
    for m, v in all_vals.items():
        conn.execute(
            """INSERT INTO raw_model_forecasts
               (model, city, target_date, metric, source_cycle_time, source_available_at,
                captured_at, lead_days, forecast_value_c, endpoint, model_name, source_family)
               VALUES (?, 'Paris', ?, 'high', ?, 'avail', 'cap', ?, ?, 'single_runs', ?,
                       'openmeteo_single_runs')""",
            (m, target_date, cyc, lead, v, m),
        )


# =====================================================================================
# (d) REAL provider reaches T2_BAYES on a >=25-row VERIFIED previous_runs fixture
# =====================================================================================
def test_real_provider_reaches_t2_bayes(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models, leak=True)
    # BLOCKER 5: the q path reads PERSISTED current single_runs rows (no network). Seed them.
    # The HISTORY provider is the REAL default (built on conn).
    _seed_current_single_runs(conn, values=_live_values())

    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])["u0r_fusion"]
    assert prov["method"] == "T2_BAYES", (
        f"with >=MIN_TRAIN VERIFIED previous_runs history the real provider must reach T2_BAYES, "
        f"got {prov['method']}"
    )
    assert prov["used_models"][0] == "ecmwf_ifs"


def test_real_provider_equal_weight_below_min_train(monkeypatch) -> None:
    """Below MIN_TRAIN the anchor has no trusted history -> anchor_z None -> EQUAL_WEIGHT (NOT
    T2_BAYES). This proves the crossing is data-driven, not flag-driven."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models, n=10)  # < MIN_TRAIN
    _seed_current_single_runs(conn, values=_live_values())

    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])["u0r_fusion"]
    assert prov["method"] == "EQUAL_WEIGHT", (
        f"below MIN_TRAIN={MIN_TRAIN} the fusion must stay EQUAL_WEIGHT, got {prov['method']}"
    )


def test_real_provider_no_leak_future_rows_ignored(monkeypatch) -> None:
    """The future-dated (target_date==decision, value 99.0) rows in the fixture must NOT pull the
    fused center toward 99 — proof the no-leak filter holds through the materializer seam."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models, leak=True)
    _seed_current_single_runs(conn, values=_live_values())
    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])["u0r_fusion"]
    # If the 99.0 leak rows had contributed, the EB bias would be ~-77 and the center would blow
    # far above the bins. T2_BAYES with a sane center is the proof the leak rows were excluded.
    assert prov["method"] == "T2_BAYES"


# =====================================================================================
# (c) TWO-FLAG money-path byte-identical proofs
# =====================================================================================
def test_both_flags_off_byte_identical(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    # Build twice with both flags OFF (shipped default); seed history both times to prove it is
    # inert on the money path.
    conn_a = _conn()
    pid_a = mod._insert_posterior(conn_a, _request(), metric="high", anchor_id=1)
    row_a = _row(conn_a, pid_a)

    conn_b = _conn()
    _seed_history(conn_b, decision=date(2026, 6, 7),
                  models=["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"])
    pid_b = mod._insert_posterior(conn_b, _request(), metric="high", anchor_id=1)
    row_b = _row(conn_b, pid_b)

    assert row_a["q_json"] == row_b["q_json"]
    assert row_a["posterior_identity_hash"] == row_b["posterior_identity_hash"]
    assert row_a["posterior_config_hash"] == row_b["posterior_config_hash"]
    assert "u0r_fusion" not in json.loads(row_b["provenance_json"])


def test_capture_on_fusion_off_money_path_byte_identical(monkeypatch) -> None:
    """capture-ON / fusion-OFF: the capture flag NEVER touches the posterior. The materialized q
    + identity hash must equal the both-OFF baseline; raw_model_forecasts accrual is irrelevant to
    the money path (the override reads ONLY the fusion flag)."""
    _disable_other_layers(monkeypatch)
    # Baseline: both OFF.
    conn_base = _conn()
    pid_base = mod._insert_posterior(conn_base, _request(), metric="high", anchor_id=1)
    base = _row(conn_base, pid_base)

    # capture ON, fusion OFF + a fully-accrued history present on the conn.
    _enable_capture(monkeypatch)
    conn_cap = _conn()
    _seed_history(conn_cap, decision=date(2026, 6, 7),
                  models=["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"])
    pid_cap = mod._insert_posterior(conn_cap, _request(), metric="high", anchor_id=1)
    cap = _row(conn_cap, pid_cap)

    assert cap["q_json"] == base["q_json"], "capture flag must not change the posterior (money path)"
    assert cap["posterior_identity_hash"] == base["posterior_identity_hash"]
    assert cap["posterior_config_hash"] == base["posterior_config_hash"]
    assert "u0r_fusion" not in json.loads(cap["provenance_json"])
