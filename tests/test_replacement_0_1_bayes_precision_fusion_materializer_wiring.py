# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Reuse: Run with pytest; update if bayes_precision_fusion fusion, materializer wiring, or flag-gate semantics change.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Purpose: Protect the replacement_forecast_materializer wiring of the flag-gated BAYES_PRECISION_FUSION-Bayes
#   multi-model fusion. (a) flag-OFF materialized posterior BYTE-IDENTICAL to today (hash
#   unchanged); (b) flag-ON: the fused mu*/sigma REPLACE the single-anchor center/spread and the
#   written q changes + the fused product gets its OWN EMOS cell identity (F6); (c) FAIL-SOFT: a
#   dropped global -> fusion uses remaining; all extras absent -> anchor fallback (byte-identical),
#   no crash; (d) regional gate: icon_d2 in-polygon enters, Moscow out-of-polygon ABSENT, dedup
#   drops icon_seamless. The capture's live fetch + walk-forward history are injected (no network).
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 integration; BAYES_PRECISION_FUSION_PROOF_RESULT.md; src/forecast/bayes_precision_fusion.py.
"""Replacement_0_1 BAYES_PRECISION_FUSION-Bayes fusion materializer-wiring tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

import src.data.replacement_forecast_materializer as mod
import src.data.bayes_precision_fusion_capture as capture_mod
from src.data.ecmwf_aifs_sampled_2t_localday import AifsMemberLocalDayExtrema, AifsSampledLocalDayExtraction
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_materializer import ReplacementForecastMaterializeRequest
from src.data.bayes_precision_fusion_capture import ModelHistory
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema

UTC = timezone.utc

# Paris settlement coordinate (in Central-EU polygon AND France polygon).
PARIS_LAT, PARIS_LON = 48.967, 2.428
MOSCOW_LAT, MOSCOW_LON = 55.592, 37.261


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
        city_timezone="Europe/Paris",
        target_local_date=date(2026, 6, 7),
        source_cycle_time=_dt(0),
        target_window_start_utc=_dt(16),
        target_window_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
        members=(
            AifsMemberLocalDayExtrema("pf-001", high_c=24.0, low_c=18.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-002", high_c=26.0, low_c=19.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-003", high_c=28.0, low_c=21.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
        ),
    )


def _anchor() -> OpenMeteoIfs9LocalDayAnchor:
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Europe/Paris",
        target_local_date=date(2026, 6, 7),
        high_c=27.0,
        low_c=18.5,
        sample_count=4,
        contributing_local_times=(
            datetime(2026, 6, 7, 0, tzinfo=UTC), datetime(2026, 6, 7, 6, tzinfo=UTC),
            datetime(2026, 6, 7, 12, tzinfo=UTC), datetime(2026, 6, 7, 18, tzinfo=UTC),
        ),
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


def _request(city: str = "Paris", tz: str = "Europe/Paris") -> ReplacementForecastMaterializeRequest:
    return ReplacementForecastMaterializeRequest(
        city=city, city_id=city, city_timezone=tz,
        target_date=date(2026, 6, 7), temperature_metric="high",
        baseline_source_run_id="b0-run",
        baseline_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        baseline_source_available_at=_dt(2),
        aifs_extraction=_aifs_extraction(), aifs_source_run_id="aifs-run", aifs_source_available_at=_dt(2, 30),
        openmeteo_anchor=_anchor(), openmeteo_source_run_id="om9-run", openmeteo_source_available_at=_dt(3),
        bins=_bins(), source_cycle_time=_dt(0), computed_at=_dt(4), expires_at=_dt(6),
        anchor_artifact_id=None, aifs_artifact_id=None, openmeteo_precision_guard=_precision_guard(),
    )


def _row(conn, posterior_id: int):
    return conn.execute(
        "SELECT q_json, posterior_identity_hash, posterior_config_hash, provenance_json "
        "FROM forecast_posteriors WHERE posterior_id = ?",
        (posterior_id,),
    ).fetchone()


# ---- deterministic capture seams (no network) ----
def _make_live_fetch(values: dict[str, float]):
    """Return a live-fetch fn that yields `values[model]` (degC) or None (dropped)."""
    def _fetch(*, model, latitude, longitude, timezone_name, run, target_local_date, metric, forecast_hours):
        return values.get(model)
    return _fetch


def _make_history(models: list[str], n: int = 30):
    """Walk-forward history with small, model-specific residuals (enough to trust the anchor)."""
    out = {}
    base_settle = [20.0 + (i % 5) for i in range(n)]
    for k, m in enumerate(models):
        fc = [s + 0.3 * ((k % 3) - 1) + 0.1 * (i % 2) for i, s in enumerate(base_settle)]
        out[m] = ModelHistory(model=m, forecast_values=tuple(fc), settlement_values=tuple(base_settle))
    return out


def _install_seams(monkeypatch, *, live_values: dict[str, float], history_models: list[str]):
    monkeypatch.setattr(mod._replacement_bayes_precision_fusion_override, "_live_fetch", _make_live_fetch(live_values), raising=False)
    hist = _make_history(history_models)

    def _provider(*, city, metric, lead_days, target_date, models):
        return {m: hist[m] for m in models if m in hist}

    monkeypatch.setattr(mod._replacement_bayes_precision_fusion_override, "_history_provider", _provider, raising=False)


def _seed_current_single_runs(conn, *, live_values: dict[str, float], request=None,
                              anchor_value: float = 27.0):
    """BLOCKER 5: the q path reads PERSISTED current single_runs rows (never a network fetch), so
    these flag-ON fusion tests must persist the current values the download job would have written
    for THIS cycle. The anchor (ecmwf_ifs) current row is seeded too so the present set is full."""
    from datetime import date as _date
    req = request if request is not None else _request()
    target_date = mod._date_text(req.target_date)
    cyc = mod._to_utc(req.source_cycle_time, field_name="source_cycle_time").isoformat()
    tz = req.city_timezone
    lead = mod._bayes_precision_fusion_city_local_lead_days(
        computed_at=mod._to_utc(req.computed_at, field_name="computed_at"),
        target_local_date=_date.fromisoformat(target_date), tz_name=tz,
    )
    all_vals = {"ecmwf_ifs": anchor_value, **live_values}
    for m, v in all_vals.items():
        conn.execute(
            """INSERT INTO raw_model_forecasts
               (model, city, target_date, metric, source_cycle_time, source_available_at,
                captured_at, lead_days, forecast_value_c, endpoint, model_name, source_family)
               VALUES (?, ?, ?, 'high', ?, 'avail', 'cap', ?, ?, 'single_runs', ?,
                       'openmeteo_single_runs')""",
            (m, req.city, target_date, cyc, lead, v, m),
        )


def _enable_flag(monkeypatch):
    import src.config as cfg
    monkeypatch.setitem(cfg.settings["edli"], "replacement_0_1_bayes_precision_fusion_enabled", True)


def _disable_other_layers(monkeypatch):
    monkeypatch.setattr(mod, "_replacement_eb_bias_shift_c", lambda request, *, metric: None)
    monkeypatch.setattr(mod, "_replacement_member_vote_smoothing_alpha", lambda: None)


# =====================================================================================
# (a) flag-OFF byte-identical
# =====================================================================================
def test_flag_off_materialized_posterior_byte_identical(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    # Flag OFF (shipped default). Build the baseline twice; the hash must match exactly.
    conn_a = _conn()
    pid_a = mod._insert_posterior(conn_a, _request(), metric="high", anchor_id=1)
    row_a = _row(conn_a, pid_a)

    # Even with seams installed and history present, OFF flag means the resolver returns None.
    _install_seams(monkeypatch, live_values={"gfs_global": 25.0, "icon_global": 26.0,
                                             "gem_global": 24.0, "jma_seamless": 27.0, "icon_eu": 25.5},
                   history_models=["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"])
    conn_b = _conn()
    pid_b = mod._insert_posterior(conn_b, _request(), metric="high", anchor_id=1)
    row_b = _row(conn_b, pid_b)

    assert row_a["q_json"] == row_b["q_json"]
    assert row_a["posterior_identity_hash"] == row_b["posterior_identity_hash"]
    assert row_a["posterior_config_hash"] == row_b["posterior_config_hash"]
    # No BAYES_PRECISION_FUSION provenance written when OFF.
    assert "bayes_precision_fusion" not in json.loads(row_b["provenance_json"])


# =====================================================================================
# (b) flag-ON: fusion replaces center/spread; q changes; F6 EMOS identity present
# =====================================================================================
def test_flag_on_fusion_changes_posterior_and_writes_emos_identity(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    conn_off = _conn()
    pid_off = mod._insert_posterior(conn_off, _request(), metric="high", anchor_id=1)
    q_off = _row(conn_off, pid_off)["q_json"]
    cfg_off = _row(conn_off, pid_off)["posterior_config_hash"]

    _enable_flag(monkeypatch)
    # Globals pull the center well below the OM9 27.0 anchor -> the fused center shifts mass.
    _install_seams(monkeypatch, live_values={"gfs_global": 23.0, "icon_global": 23.5,
                                             "gem_global": 22.5, "jma_seamless": 24.0, "icon_eu": 23.2},
                   history_models=["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"])
    conn_on = _conn()
    _seed_current_single_runs(conn_on, live_values={"gfs_global": 23.0, "icon_global": 23.5,
                                                    "gem_global": 22.5, "jma_seamless": 24.0, "icon_eu": 23.2})
    pid_on = mod._insert_posterior(conn_on, _request(), metric="high", anchor_id=1)
    row_on = _row(conn_on, pid_on)
    q_on = row_on["q_json"]
    prov = json.loads(row_on["provenance_json"])

    assert q_on != q_off, "fused posterior must differ from the single-anchor posterior"
    assert row_on["posterior_config_hash"] != cfg_off, "fused product needs its own EMOS cell"
    fusion = prov["bayes_precision_fusion"]
    assert fusion["method"] == "T2_BAYES"
    assert fusion["used_models"][0] == "ecmwf_ifs"
    # BLOCKER 9 / spec §4(2): one representative per provider family. Paris is in the Central-EU
    # box at lead 1, so the DWD/ICON family rep is icon_eu (the in-EU nest); icon_global is the
    # suppressed provider duplicate. The non-ICON decorrelated globals all stay.
    used = set(fusion["used_models"][1:])
    assert used >= {"gfs_global", "gem_global", "jma_seamless"}
    icon_family = used & {"icon_global", "icon_eu", "icon_d2"}
    assert icon_family == {"icon_eu"}, f"exactly one DWD-ICON rep (icon_eu in-EU), got {icon_family}"
    # F6 identity components present.
    cfg = mod  # for clarity
    pc = conn_on.execute(
        "SELECT posterior_method FROM forecast_posteriors WHERE posterior_id=?", (pid_on,)
    ).fetchone()
    assert pc["posterior_method"] == "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"  # column unchanged
    assert fusion["model_set_hash"] and fusion["resolution_mix_hash"] and fusion["lead_bucket"]


# =====================================================================================
# (c) fail-soft: a dropped global -> fusion uses remaining; all absent -> anchor fallback
# =====================================================================================
def test_flag_on_dropped_global_fuses_with_remaining(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_flag(monkeypatch)
    # icon_global FAILS to fetch (None) -> dropped; the rest fuse.
    _install_seams(monkeypatch, live_values={"gfs_global": 23.0, "icon_global": None,  # type: ignore[dict-item]
                                             "gem_global": 22.5, "jma_seamless": 24.0, "icon_eu": 23.2},
                   history_models=["ecmwf_ifs", "gfs_global", "gem_global", "jma_seamless", "icon_eu"])
    conn = _conn()
    # icon_global has NO persisted current row -> it is dropped (the q path never network-fetches
    # it). The rest are persisted and fuse.
    _seed_current_single_runs(conn, live_values={"gfs_global": 23.0,
                                                 "gem_global": 22.5, "jma_seamless": 24.0, "icon_eu": 23.2})
    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])["bayes_precision_fusion"]
    assert "icon_global" in prov["dropped_models"]
    assert "icon_global" not in prov["used_models"]
    assert prov["method"] == "T2_BAYES"
    assert {"gfs_global", "gem_global", "jma_seamless", "icon_eu"} <= set(prov["used_models"])


def test_flag_on_all_extras_absent_falls_back_byte_identical(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    # OFF baseline.
    conn_off = _conn()
    pid_off = mod._insert_posterior(conn_off, _request(), metric="high", anchor_id=1)
    q_off = _row(conn_off, pid_off)["q_json"]

    _enable_flag(monkeypatch)
    # EVERY extra fetch returns None -> no extras survive -> override None -> single-anchor path.
    _install_seams(monkeypatch, live_values={}, history_models=["ecmwf_ifs"])
    conn_on = _conn()
    pid_on = mod._insert_posterior(conn_on, _request(), metric="high", anchor_id=1)
    row_on = _row(conn_on, pid_on)
    assert row_on["q_json"] == q_off, "all-extras-absent must fall back to the byte-identical anchor posterior"
    assert "bayes_precision_fusion" not in json.loads(row_on["provenance_json"])


def test_flag_on_capture_exception_is_failsoft(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    # Baseline with the flag OFF (must NOT touch any live fetch).
    conn_off = _conn()
    pid_off = mod._insert_posterior(conn_off, _request(), metric="high", anchor_id=1)
    q_off = _row(conn_off, pid_off)["q_json"]

    _enable_flag(monkeypatch)
    # A live-fetch that RAISES must not crash the cycle (fail-soft -> byte-identical fallback).
    def _boom(**kwargs):
        raise RuntimeError("simulated network blowup")

    monkeypatch.setattr(mod._replacement_bayes_precision_fusion_override, "_live_fetch", _boom, raising=False)
    monkeypatch.setattr(mod._replacement_bayes_precision_fusion_override, "_history_provider", None, raising=False)
    conn_on = _conn()
    pid_on = mod._insert_posterior(conn_on, _request(), metric="high", anchor_id=1)
    assert _row(conn_on, pid_on)["q_json"] == q_off


# =====================================================================================
# (d) regional gate: icon_d2 in-polygon enters; Moscow ABSENT; dedup drops icon_seamless
# =====================================================================================
def test_flag_on_icon_d2_enters_in_paris_polygon(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_flag(monkeypatch)
    _install_seams(monkeypatch, live_values={"gfs_global": 23.0, "icon_global": 23.5, "gem_global": 22.5,
                                             "jma_seamless": 24.0, "icon_eu": 23.2, "icon_d2": 23.1},
                   history_models=["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu", "icon_d2"])
    conn = _conn()
    _seed_current_single_runs(conn, live_values={"gfs_global": 23.0, "icon_global": 23.5, "gem_global": 22.5,
                                                 "jma_seamless": 24.0, "icon_eu": 23.2, "icon_d2": 23.1})
    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])["bayes_precision_fusion"]
    assert "icon_d2" in prov["used_models"]
    assert "icon_d2" not in prov["excluded_regionals"]


def test_flag_on_icon_d2_absent_in_moscow_zero_leak(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_flag(monkeypatch)
    # Patch Moscow coords into the city resolver path by faking runtime_cities_by_name.
    import src.config as cfg

    class _City:
        lat, lon, timezone, settlement_unit = MOSCOW_LAT, MOSCOW_LON, "Europe/Moscow", "C"

    monkeypatch.setattr(cfg, "runtime_cities_by_name", lambda: {"Moscow": _City()})
    monkeypatch.setattr("src.data.replacement_forecast_materializer.runtime_cities_by_name", lambda: {"Moscow": _City()}, raising=False)
    _install_seams(monkeypatch, live_values={"gfs_global": 23.0, "icon_global": 23.5, "gem_global": 22.5,
                                             "jma_seamless": 24.0, "icon_eu": 23.2, "icon_d2": 23.1},
                   history_models=["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu", "icon_d2"])
    conn = _conn()
    moscow_req = _request(city="Moscow", tz="Europe/Moscow")
    _seed_current_single_runs(conn, request=moscow_req,
                              live_values={"gfs_global": 23.0, "icon_global": 23.5, "gem_global": 22.5,
                                           "jma_seamless": 24.0, "icon_eu": 23.2, "icon_d2": 23.1})
    pid = mod._insert_posterior(conn, moscow_req, metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])["bayes_precision_fusion"]
    assert "icon_d2" not in prov["used_models"], "Moscow out-of-polygon: icon_d2 must be ABSENT (zero-leak)"
    assert "icon_d2" in prov["excluded_regionals"]


def test_flag_on_dedup_drops_icon_seamless(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_flag(monkeypatch)
    # icon_seamless fetched, but bit-identical history to icon_d2 -> deduped.
    monkeypatch.setattr(mod._replacement_bayes_precision_fusion_override, "_live_fetch",
                        _make_live_fetch({"gfs_global": 23.0, "icon_eu": 23.2,
                                          "icon_d2": 23.1, "icon_seamless": 23.1}), raising=False)
    hist = _make_history(["ecmwf_ifs", "gfs_global", "icon_eu"])
    # icon_d2 and icon_seamless share an IDENTICAL forecast series (alias).
    shared = ModelHistory(model="icon_d2", forecast_values=tuple(20.0 + 0.1 * i for i in range(30)),
                          settlement_values=tuple(20.0 + 0.0 * i for i in range(30)))
    hist["icon_d2"] = shared
    hist["icon_seamless"] = ModelHistory(model="icon_seamless",
                                         forecast_values=shared.forecast_values,
                                         settlement_values=shared.settlement_values)

    def _provider(*, city, metric, lead_days, target_date, models):
        return {m: hist[m] for m in models if m in hist}

    monkeypatch.setattr(mod._replacement_bayes_precision_fusion_override, "_history_provider", _provider, raising=False)
    conn = _conn()
    _seed_current_single_runs(conn, live_values={"gfs_global": 23.0, "icon_eu": 23.2,
                                                 "icon_d2": 23.1, "icon_seamless": 23.1})
    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])["bayes_precision_fusion"]
    assert "icon_seamless" in prov["dropped_aliases"]
    assert "icon_seamless" not in prov["used_models"]
    assert "icon_d2" in prov["used_models"]


# ---- resolver flag discipline ----
def test_resolver_default_flag_off_returns_none() -> None:
    assert mod._replacement_bayes_precision_fusion_override(_request(), metric="high", anchor_value_corrected_c=27.0) is None


# =====================================================================================
# (e) Task #32 follow-up (2026-06-11): generalized previous_runs current-value substitution.
# RELATIONSHIP PIN: an instrument whose CURRENT value is served via the previous_runs
# substitution (same value, same history) fuses BYTE-IDENTICALLY to the same instrument
# served via single_runs — there is NO special-casing / manual down-weighting of a
# substituted instrument; the lead-bucket walk-forward residual variance is the ONLY
# mechanism pricing the older run. The substitution is BRANDED in provenance
# (current_value_serving.<model>.served_via = "previous_runs"), never silent.
# =====================================================================================
def _seed_current_previous_runs(conn, *, model: str, value: float, request=None):
    """Persist one model's current value as a previous_runs row at the SAME natural key
    (the JMA-at-06Z shape: no single_runs row exists for this model at the cycle)."""
    from datetime import date as _date
    req = request if request is not None else _request()
    target_date = mod._date_text(req.target_date)
    cyc = mod._to_utc(req.source_cycle_time, field_name="source_cycle_time").isoformat()
    lead = mod._bayes_precision_fusion_city_local_lead_days(
        computed_at=mod._to_utc(req.computed_at, field_name="computed_at"),
        target_local_date=_date.fromisoformat(target_date), tz_name=req.city_timezone,
    )
    conn.execute(
        """INSERT INTO raw_model_forecasts
           (model, city, target_date, metric, source_cycle_time, source_available_at,
            captured_at, lead_days, forecast_value_c, endpoint, model_name, source_family)
           VALUES (?, ?, ?, 'high', ?, 'avail', 'cap', ?, ?, 'previous_runs', ?,
                   'openmeteo_previous_runs')""",
        (model, req.city, target_date, cyc, lead, value, model),
    )


def test_flag_on_previous_runs_substitution_fuses_identically_and_is_branded(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_flag(monkeypatch)
    live = {"gfs_global": 23.0, "icon_global": 23.5, "gem_global": 22.5,
            "jma_seamless": 24.0, "icon_eu": 23.2}
    hist = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]

    # A: jma served via single_runs (the forward capture).
    _install_seams(monkeypatch, live_values=live, history_models=hist)
    conn_a = _conn()
    _seed_current_single_runs(conn_a, live_values=live)
    pid_a = mod._insert_posterior(conn_a, _request(), metric="high", anchor_id=1)
    row_a = _row(conn_a, pid_a)

    # B: jma's single_runs row ABSENT (the JMA-at-06Z structural case); the SAME value persisted
    # as its previous_runs row at the same natural key. The injected live seam serves NOTHING for
    # jma (None), so if the substitution broke, jma would be DROPPED and q would differ — the
    # byte-identity below is therefore discriminating, not vacuous.
    live_without_jma = {k: v for k, v in live.items() if k != "jma_seamless"}
    _install_seams(monkeypatch, live_values=live_without_jma, history_models=hist)
    conn_b = _conn()
    _seed_current_single_runs(conn_b, live_values=live_without_jma)
    _seed_current_previous_runs(conn_b, model="jma_seamless", value=24.0)
    pid_b = mod._insert_posterior(conn_b, _request(), metric="high", anchor_id=1)
    row_b = _row(conn_b, pid_b)

    assert row_a["q_json"] == row_b["q_json"], (
        "the fused q must be BYTE-IDENTICAL whether jma's current value arrived via single_runs "
        "or via the previous_runs substitution — any divergence means the substituted instrument "
        "was special-cased (manual down-weighting / drop), which is forbidden: the lead-bucket "
        "history residual variance is the only honest pricing of the older run"
    )
    prov_a = json.loads(row_a["provenance_json"])["bayes_precision_fusion"]
    prov_b = json.loads(row_b["provenance_json"])["bayes_precision_fusion"]
    assert prov_a["used_models"] == prov_b["used_models"]
    assert prov_a["decorrelated_providers_served"] == prov_b["decorrelated_providers_served"]
    # Brand law: the substitution is recorded per instrument, never silent.
    serving_b = prov_b["current_value_serving"]
    assert serving_b["jma_seamless"]["served_via"] == "previous_runs"
    assert serving_b["jma_seamless"]["previous_run_substitution"] is True
    assert serving_b["gfs_global"]["served_via"] == "single_runs"
    serving_a = prov_a["current_value_serving"]
    assert serving_a["jma_seamless"]["served_via"] == "single_runs"
    assert serving_a["jma_seamless"]["previous_run_substitution"] is False


def test_upgrade_trigger_note_lands_on_the_posterior_provenance(monkeypatch) -> None:
    """Task #32: the honest re-materialization note must live on the POSTERIOR provenance —
    the anchor row is INSERT-OR-IGNOREd on a same-cycle re-materialization (the existing anchor
    wins), so an anchor-only note never surfaces (live finding 2026-06-11: the first 8 upgraded
    posteriors carried current_value_serving but upgrade_trigger=None)."""
    import dataclasses

    _disable_other_layers(monkeypatch)
    conn = _conn()
    req = dataclasses.replace(_request(), upgrade_trigger="instrument_set_expansion")
    pid = mod._insert_posterior(conn, req, metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])
    assert prov.get("upgrade_trigger") == "instrument_set_expansion"
    # A normal request (no trigger) stays byte-identical: no key at all.
    conn_plain = _conn()
    pid_plain = mod._insert_posterior(conn_plain, _request(), metric="high", anchor_id=1)
    prov_plain = json.loads(_row(conn_plain, pid_plain)["provenance_json"])
    assert "upgrade_trigger" not in prov_plain
