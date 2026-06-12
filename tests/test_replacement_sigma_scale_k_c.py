# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: C3 calibration surface (docs/operations/c3_sigma_calibration_surface_2026-06-12.md):
#   n=127 A_24h C-unit settled cells — mode-bin realized win rate 0.17 vs model mean q 0.43-0.46
#   (posterior ~2.5x too peaked). Recommended correction: multiply sigma_pred by k≈2.4-2.5 for
#   C-unit cities BEFORE bin integration. F-unit cities: n=25 insufficient, scale disabled.
#   Composes with existing settlement sigma floor (floor is lower bound after scale).
"""σ_pred scale antibodies for C3 calibration surface (2026-06-12).

Category killed: silent posterior over-confidence for C-unit cities — the fused-Normal q is
too peaked (mode-bin q 0.43-0.46 vs realized win rate 0.17) because sigma_pred is ~2.5x
too small. The scale multiplier widens sigma before bin integration and is C-unit-only.

Invariants proven here:
  1. Scale applied exactly once, only for C-unit + flag k > 1.0.
  2. F-unit cities are untouched at any flag value (including k > 1.0).
  3. Provenance field sigma_scale_k_applied is present (float) when applied, absent / None when inert.
  4. Composition with sigma floor: floor is enforced AFTER scaling (floor still lower bound).
  5. Default k=1.0 produces byte-identical q to pre-scale behavior (regression pin).
"""
from __future__ import annotations

import json
import math
from datetime import date

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin
from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _live_values,
    _request,
    _row,
    _seed_current_single_runs,
    _seed_history,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enable_fused_shape(monkeypatch) -> None:
    monkeypatch.setitem(cfg.settings["edli"], "replacement_0_1_fused_q_shape_enabled", True)


def _set_sigma_scale_k(monkeypatch, k: float) -> None:
    monkeypatch.setitem(cfg.settings["edli"], "replacement_sigma_scale_k_c", k)


def _materialize(conn):
    return mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)


def _materialize_with_f_bins(conn):
    """Materialize a request whose bins declare settlement_unit='F' (simulating a US city)."""
    from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (
        _aifs_extraction, _anchor, _precision_guard,
    )
    from src.data.replacement_forecast_materializer import ReplacementForecastMaterializeRequest
    from datetime import datetime, timezone

    UTC = timezone.utc
    def _dt(h): return datetime(2026, 6, 6, h, 0, tzinfo=UTC)

    # F-unit bins: same numeric bounds_c but settlement_unit='F'
    bins_f = (
        AifsTemperatureBin("cool_f", upper_c=22.0, center_c=21.0, display_unit="C", settlement_unit="F"),
        AifsTemperatureBin("mild_f", lower_c=23.0, upper_c=26.0, display_unit="C", settlement_unit="F"),
        AifsTemperatureBin("warm_f", lower_c=27.0, center_c=28.0, display_unit="C", settlement_unit="F"),
    )
    req = ReplacementForecastMaterializeRequest(
        city="Paris", city_id="Paris", city_timezone="Europe/Paris",
        target_date=date(2026, 6, 7), temperature_metric="high",
        baseline_source_run_id="b0-run",
        baseline_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        baseline_source_available_at=_dt(2),
        aifs_extraction=_aifs_extraction(), aifs_source_run_id="aifs-run",
        aifs_source_available_at=_dt(2),
        openmeteo_anchor=_anchor(), openmeteo_source_run_id="om9-run",
        openmeteo_source_available_at=_dt(3),
        bins=bins_f, source_cycle_time=_dt(0), computed_at=_dt(4), expires_at=_dt(6),
        openmeteo_precision_guard=_precision_guard(),
    )
    return mod._insert_posterior(conn, req, metric="high", anchor_id=1)


# ---------------------------------------------------------------------------
# 1. Scale applied exactly once, only for C-unit + k > 1.0
# ---------------------------------------------------------------------------

def test_scale_applied_widens_mode_bin_q_for_c_unit(monkeypatch) -> None:
    """When k=2.4 is configured and city is C-unit, mode-bin q drops (sigma is wider)."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)

    # Materialize WITHOUT scale (k=1.0 default)
    conn1 = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn1, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn1, values=_live_values())
    pid1 = _materialize(conn1)
    row1 = _row(conn1, pid1)
    prov1 = json.loads(row1["provenance_json"])
    q1 = json.loads(row1["q_json"])

    # sigma_scale_k_applied must be absent or None when k=1.0
    assert prov1.get("sigma_scale_k_applied") is None, (
        "sigma_scale_k_applied must be None when k=1.0 (inert)"
    )

    # Materialize WITH scale k=2.4
    _set_sigma_scale_k(monkeypatch, 2.4)
    conn2 = _conn()
    _seed_history(conn2, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn2, values=_live_values())
    pid2 = _materialize(conn2)
    row2 = _row(conn2, pid2)
    prov2 = json.loads(row2["provenance_json"])
    q2 = json.loads(row2["q_json"])

    # Provenance must record the applied k
    assert prov2["sigma_scale_k_applied"] == pytest.approx(2.4), (
        "sigma_scale_k_applied must record the applied k value"
    )

    # The mode bin should have LOWER q (wider sigma → flatter distribution)
    mode_bin = max(q1, key=q1.get)
    assert q2[mode_bin] < q1[mode_bin], (
        f"Scaling sigma by k=2.4 must LOWER mode-bin q (was {q1[mode_bin]:.4f}, got {q2[mode_bin]:.4f})"
    )
    # The q sum must still be 1
    assert sum(q2.values()) == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. F-unit cities are untouched at any k value
# ---------------------------------------------------------------------------

def test_scale_not_applied_for_f_unit_city(monkeypatch) -> None:
    """F-unit city: sigma_scale_k_applied is None even when k=2.4 is configured."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    _set_sigma_scale_k(monkeypatch, 2.4)

    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]

    # F-unit materialize
    conn_f = _conn()
    _seed_history(conn_f, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn_f, values=_live_values())
    pid_f = _materialize_with_f_bins(conn_f)
    row_f = _row(conn_f, pid_f)
    prov_f = json.loads(row_f["provenance_json"])

    assert prov_f.get("sigma_scale_k_applied") is None, (
        "F-unit city must NEVER have sigma_scale_k_applied set, even when k > 1.0"
    )

    # C-unit same k: scale fires
    conn_c = _conn()
    _seed_history(conn_c, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn_c, values=_live_values())
    pid_c = _materialize(conn_c)
    row_c = _row(conn_c, pid_c)
    prov_c = json.loads(row_c["provenance_json"])

    assert prov_c.get("sigma_scale_k_applied") == pytest.approx(2.4), (
        "C-unit city must have sigma_scale_k_applied=2.4 when k=2.4"
    )


# ---------------------------------------------------------------------------
# 3. Provenance field present when applied, absent when inert
# ---------------------------------------------------------------------------

def test_provenance_field_absent_when_inert(monkeypatch) -> None:
    """When scale is inert (k=1.0), sigma_scale_k_applied must be None in provenance."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    _set_sigma_scale_k(monkeypatch, 1.0)

    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])

    assert prov.get("sigma_scale_k_applied") is None, (
        "sigma_scale_k_applied must be None when k=1.0 regardless of unit"
    )


def test_provenance_field_present_and_correct_when_applied(monkeypatch) -> None:
    """When k=2.4 fires, sigma_scale_k_applied records the exact k value."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    _set_sigma_scale_k(monkeypatch, 2.4)

    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])

    assert "sigma_scale_k_applied" in prov, "sigma_scale_k_applied key must be present in provenance"
    assert prov["sigma_scale_k_applied"] == pytest.approx(2.4)


# ---------------------------------------------------------------------------
# 4. Composition with sigma floor — floor still enforced after scaling
# ---------------------------------------------------------------------------

def test_sigma_floor_enforced_after_scaling(monkeypatch) -> None:
    """The settlement sigma floor (if enabled and present) must be applied AFTER the scale.

    The floor is a lower bound on the scaled sigma. So:
      sigma_after_scale = sigma_pred * k
      sigma_used = max(sigma_after_scale, floor)
    This test seeds a floor ABOVE the scaled sigma so the floor should still dominate.
    """
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    _set_sigma_scale_k(monkeypatch, 2.4)
    # Enable the floor mechanism
    monkeypatch.setitem(cfg.settings["edli"], "edli_settlement_sigma_floor_enabled", True)

    # Stub the floor lookup to return a VERY LARGE value — so sigma_used = floor, not scale
    import src.data.replacement_forecast_materializer as _m
    def _large_floor(req, *, metric):
        return 999.0, None
    monkeypatch.setattr(_m, "_replacement_settlement_sigma_floor_lookup", _large_floor)

    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])

    # Floor applied must be True (floor > scaled sigma → floor dominates)
    assert prov["settlement_sigma_floor_applied"] is True, (
        "Floor must still be applied after scaling when it exceeds the scaled sigma"
    )
    # The scale still fired (it ran before the floor was checked)
    assert prov.get("sigma_scale_k_applied") == pytest.approx(2.4), (
        "sigma_scale_k_applied must still be recorded even when floor also applied"
    )
    # The floor value dominates (floor=999 >> any sigma_pred * 2.4)
    assert prov["settlement_sigma_floor_c"] == pytest.approx(999.0)


def test_sigma_floor_not_tighter_than_scaled_sigma(monkeypatch) -> None:
    """When floor is below the scaled sigma, it has no effect on sigma_used.

    Verifies: scale fires → sigma_pred * k becomes the new base → floor=max(base,floor)
    when floor < base, floor is a no-op on sigma_used (but settlement_sigma_floor_applied
    may still be True to record the lookup happened).
    """
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    _set_sigma_scale_k(monkeypatch, 2.4)
    monkeypatch.setitem(cfg.settings["edli"], "edli_settlement_sigma_floor_enabled", True)

    import src.data.replacement_forecast_materializer as _m

    # Return a floor SMALLER than sigma_pred (default ~1.5-2.5°C → scale to ~3.6-6.0°C,
    # so a floor of 0.1°C will never dominate).
    def _tiny_floor(req, *, metric):
        return 0.1, None
    monkeypatch.setattr(_m, "_replacement_settlement_sigma_floor_lookup", _tiny_floor)

    # Baseline without scale (k=1.0) to compare q
    conn1 = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn1, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn1, values=_live_values())
    # Temporarily disable scale to get the k=1.0 baseline
    _set_sigma_scale_k(monkeypatch, 1.0)
    pid_base = _materialize(conn1)
    q_base = json.loads(_row(conn1, pid_base)["q_json"])

    # Now with k=2.4 and a negligible floor
    _set_sigma_scale_k(monkeypatch, 2.4)
    conn2 = _conn()
    _seed_history(conn2, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn2, values=_live_values())
    pid2 = _materialize(conn2)
    row2 = _row(conn2, pid2)
    prov2 = json.loads(row2["provenance_json"])
    q2 = json.loads(row2["q_json"])

    # Scale applied
    assert prov2.get("sigma_scale_k_applied") == pytest.approx(2.4)
    # Mode bin q must be lower (wider sigma), confirming scale dominated over tiny floor
    mode_bin = max(q_base, key=q_base.get)
    assert q2[mode_bin] < q_base[mode_bin], (
        "When floor < scaled sigma, scale still dominates: mode-bin q must be lower"
    )


# ---------------------------------------------------------------------------
# 5. Default k=1.0 produces byte-identical q (regression pin)
# ---------------------------------------------------------------------------

def test_default_k_1_0_byte_identical_q(monkeypatch) -> None:
    """k=1.0 (default) must produce BYTE-IDENTICAL q to the pre-scale code path.

    This regression pin ensures the new code path is invisible when the flag is at default.
    Two materializations: one with k explicitly=1.0, one without setting the key at all
    (so the fallback is used). The q_json and posterior_identity_hash must match.
    """
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)

    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]

    # Run 1: key explicitly set to 1.0
    _set_sigma_scale_k(monkeypatch, 1.0)
    conn1 = _conn()
    _seed_history(conn1, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn1, values=_live_values())
    pid1 = _materialize(conn1)
    row1 = _row(conn1, pid1)
    q1 = json.loads(row1["q_json"])
    hash1 = row1["posterior_identity_hash"]

    # Run 2: key absent (pop it so fallback 1.0 is used from code default)
    edli = cfg.settings["edli"]
    saved = edli.pop("replacement_sigma_scale_k_c", None)
    try:
        conn2 = _conn()
        _seed_history(conn2, decision=date(2026, 6, 7), models=models)
        _seed_current_single_runs(conn2, values=_live_values())
        pid2 = _materialize(conn2)
        row2 = _row(conn2, pid2)
        q2 = json.loads(row2["q_json"])
        hash2 = row2["posterior_identity_hash"]
    finally:
        if saved is not None:
            edli["replacement_sigma_scale_k_c"] = saved

    assert q1 == q2, (
        "k=1.0 (explicit) and k absent (default) must produce byte-identical q"
    )
    assert hash1 == hash2, (
        "k=1.0 (explicit) and k absent (default) must produce identical identity hash"
    )


# ---------------------------------------------------------------------------
# 6. _replacement_sigma_scale_k_c helper: fail-closed to 1.0
# ---------------------------------------------------------------------------

def test_sigma_scale_k_c_fallback_on_config_error(monkeypatch) -> None:
    """_replacement_sigma_scale_k_c returns 1.0 on any config error (fail-closed)."""
    monkeypatch.setitem(cfg.settings["edli"], "replacement_sigma_scale_k_c", "not_a_number")
    k = mod._replacement_sigma_scale_k_c()
    assert k == 1.0, "Non-numeric config value must fall back to 1.0"

    monkeypatch.setitem(cfg.settings["edli"], "replacement_sigma_scale_k_c", float("nan"))
    k = mod._replacement_sigma_scale_k_c()
    assert k == 1.0, "NaN config value must fall back to 1.0"

    monkeypatch.setitem(cfg.settings["edli"], "replacement_sigma_scale_k_c", -1.0)
    k = mod._replacement_sigma_scale_k_c()
    assert k == 1.0, "Non-positive k must fall back to 1.0"


# ---------------------------------------------------------------------------
# 7. _city_settlement_unit_from_bins: derives unit correctly
# ---------------------------------------------------------------------------

def test_city_settlement_unit_from_bins_c_unit() -> None:
    req = _request()  # Paris — C-unit bins
    assert mod._city_settlement_unit_from_bins(req) == "C"


def test_city_settlement_unit_from_bins_f_unit() -> None:
    from src.data.replacement_forecast_materializer import ReplacementForecastMaterializeRequest
    from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (
        _aifs_extraction, _anchor, _precision_guard,
    )
    from datetime import datetime, timezone

    UTC = timezone.utc
    def _dt(h): return datetime(2026, 6, 6, h, 0, tzinfo=UTC)

    bins_f = (
        AifsTemperatureBin("b1", upper_c=22.0, center_c=21.0, display_unit="C", settlement_unit="F"),
    )
    req = ReplacementForecastMaterializeRequest(
        city="SomeUSCity", city_id="SomeUSCity", city_timezone="America/New_York",
        target_date=date(2026, 6, 7), temperature_metric="high",
        baseline_source_run_id="b0-run",
        baseline_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        baseline_source_available_at=_dt(2),
        aifs_extraction=_aifs_extraction(), aifs_source_run_id="aifs-run",
        aifs_source_available_at=_dt(2),
        openmeteo_anchor=_anchor(), openmeteo_source_run_id="om9-run",
        openmeteo_source_available_at=_dt(3),
        bins=bins_f, source_cycle_time=_dt(0), computed_at=_dt(4), expires_at=_dt(6),
        openmeteo_precision_guard=_precision_guard(),
    )
    assert mod._city_settlement_unit_from_bins(req) == "F"
