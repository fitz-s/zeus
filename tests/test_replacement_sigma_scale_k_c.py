# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: OPERATOR LAW 2026-06-12 "没有一个人可以在没有数学支持下决定一个 hard coded value" — the
#   σ-scale correction factor must be FITTED, never operator-picked. The materializer reads the FITTED
#   artifact state/sigma_scale_fit.json (k AND uniform-mixture w), written only by scripts/fit_sigma_scale.py.
#   Supersedes the old settings-key design (replacement_sigma_scale_k_c, deleted). Data basis:
#   docs/operations/c3_sigma_calibration_surface_2026-06-12.md (C posterior ~2.5x too peaked).
"""Artifact-read antibodies for the FITTED σ_pred scale (k) + uniform-mixture (w), C3 surface.

Category killed: a HAND-SET correction factor. The materializer must take k and w ONLY from the fitted
artifact; an absent / unfitted-family artifact must leave the posterior byte-identical (inert).

Invariants proven here:
  1. Missing artifact -> inert (k=1, w=0): byte-identical q + identity hash to pre-correction.
  2. Fitted C artifact -> k AND w applied exactly once; mode-bin q drops; provenance records both.
  3. F-unit city is NEVER corrected (artifact family unfitted -> (1,0)); even a (hypothetically) fitted
     F entry is blocked by the defense-in-depth unit gate.
  4. Provenance fields sigma_scale_k_applied + uniform_mixture_w_applied present (float) when applied,
     None when inert.
  5. Artifact reader fail-soft: malformed / unfitted / out-of-range -> (1.0, 0.0). Never raises.
"""
from __future__ import annotations

import json
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

_MODELS = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enable_fused_shape(monkeypatch) -> None:
    monkeypatch.setitem(cfg.settings["edli"], "replacement_0_1_fused_q_shape_enabled", True)


def _write_artifact(tmp_path, monkeypatch, families: dict) -> str:
    """Write a sigma_scale_fit.json and point the materializer at it. Returns the path."""
    path = tmp_path / "sigma_scale_fit.json"
    path.write_text(json.dumps({"_meta": {"authority": "sigma_scale_fit_v1_mle"}, "families": families}))
    monkeypatch.setattr(mod, "_SIGMA_SCALE_FIT_PATH", str(path))
    return str(path)


def _no_artifact(tmp_path, monkeypatch) -> None:
    """Point the materializer at a nonexistent artifact path (missing -> inert)."""
    monkeypatch.setattr(mod, "_SIGMA_SCALE_FIT_PATH", str(tmp_path / "does_not_exist.json"))


def _fitted_c(k: float = 2.0, w: float = 0.1) -> dict:
    return {
        "C": {"fitted": True, "k": k, "w": w, "n_cells": 215},
        "F": {"fitted": False, "k": 1.0, "w": 0.0, "n_cells": 47, "refusal_reason": "INSUFFICIENT_CELLS:47<60"},
    }


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


def _materialize_seeded(conn, monkeypatch, f_bins: bool = False):
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    _seed_history(conn, decision=date(2026, 6, 7), models=_MODELS)
    _seed_current_single_runs(conn, values=_live_values())
    return _materialize_with_f_bins(conn) if f_bins else _materialize(conn)


# ---------------------------------------------------------------------------
# 1. Missing artifact -> inert (byte-identical q + identity hash)
# ---------------------------------------------------------------------------

def test_missing_artifact_is_inert(monkeypatch, tmp_path) -> None:
    """No artifact file -> k=1,w=0: provenance fields None, q sums to 1."""
    _no_artifact(tmp_path, monkeypatch)
    conn = _conn()
    pid = _materialize_seeded(conn, monkeypatch)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    q = json.loads(row["q_json"])

    assert prov.get("sigma_scale_k_applied") is None
    assert prov.get("uniform_mixture_w_applied") is None
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-9)


def test_unfitted_artifact_byte_identical_to_missing(monkeypatch, tmp_path) -> None:
    """A C family with fitted=False must yield BYTE-IDENTICAL q + identity hash to the missing case."""
    # Run 1: missing artifact
    _no_artifact(tmp_path, monkeypatch)
    conn1 = _conn()
    pid1 = _materialize_seeded(conn1, monkeypatch)
    row1 = _row(conn1, pid1)
    q1, h1 = json.loads(row1["q_json"]), row1["posterior_identity_hash"]

    # Run 2: artifact present but C unfitted (REFUSED)
    _write_artifact(tmp_path, monkeypatch, {
        "C": {"fitted": False, "k": 1.0, "w": 0.0, "n_cells": 10, "refusal_reason": "INSUFFICIENT_CELLS:10<60"},
    })
    conn2 = _conn()
    pid2 = _materialize_seeded(conn2, monkeypatch)
    row2 = _row(conn2, pid2)
    q2, h2 = json.loads(row2["q_json"]), row2["posterior_identity_hash"]

    assert q1 == q2, "unfitted family must be byte-identical to missing artifact"
    assert h1 == h2


# ---------------------------------------------------------------------------
# 2. Fitted C artifact -> k AND w applied; mode-bin q drops; provenance records both
# ---------------------------------------------------------------------------

def test_fitted_c_applies_k_and_w(monkeypatch, tmp_path) -> None:
    # Baseline: inert
    _no_artifact(tmp_path, monkeypatch)
    conn0 = _conn()
    pid0 = _materialize_seeded(conn0, monkeypatch)
    q0 = json.loads(_row(conn0, pid0)["q_json"])

    # Fitted C: k=2.0, w=0.1
    _write_artifact(tmp_path, monkeypatch, _fitted_c(k=2.0, w=0.1))
    conn1 = _conn()
    pid1 = _materialize_seeded(conn1, monkeypatch)
    row1 = _row(conn1, pid1)
    prov1 = json.loads(row1["provenance_json"])
    q1 = json.loads(row1["q_json"])

    assert prov1["sigma_scale_k_applied"] == pytest.approx(2.0)
    assert prov1["uniform_mixture_w_applied"] == pytest.approx(0.1)
    # Wider sigma + uniform floor -> mode bin LOWER
    mode_bin = max(q0, key=q0.get)
    assert q1[mode_bin] < q0[mode_bin]
    assert sum(q1.values()) == pytest.approx(1.0, abs=1e-9)


def test_w_only_lifts_floor_without_changing_sigma(monkeypatch, tmp_path) -> None:
    """k=1.0 + w>0: σ unchanged but the uniform floor still lifts low-q bins (w applied, k not)."""
    _write_artifact(tmp_path, monkeypatch, {
        "C": {"fitted": True, "k": 1.0, "w": 0.15, "n_cells": 215},
    })
    conn = _conn()
    pid = _materialize_seeded(conn, monkeypatch)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    q = json.loads(row["q_json"])

    # k=1.0 -> sigma_scale_k_applied stays None (no widening), but w fired.
    assert prov.get("sigma_scale_k_applied") is None
    assert prov["uniform_mixture_w_applied"] == pytest.approx(0.15)
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-9)
    # The mixture must pull the mode bin TOWARD uniform (lower) vs the inert no-artifact baseline.
    # (Open-ended catch-all bins are re-capped at their honest mass and may stay below w/n — that is
    # the catch-all coherence invariant, not a floor violation.)
    _no_artifact(tmp_path, monkeypatch)
    conn0 = _conn()
    q0 = json.loads(_row(conn0, _materialize_seeded(conn0, monkeypatch))["q_json"])
    mode_bin = max(q0, key=q0.get)
    assert q[mode_bin] < q0[mode_bin], "uniform mixture must pull the mode bin toward uniform"


# ---------------------------------------------------------------------------
# 3. F-unit city is NEVER corrected
# ---------------------------------------------------------------------------

def test_f_unit_never_corrected_even_with_fitted_f_entry(monkeypatch, tmp_path) -> None:
    """Defense-in-depth: even a (hypothetical) fitted F entry must not touch an F-unit city."""
    _write_artifact(tmp_path, monkeypatch, {
        "C": {"fitted": True, "k": 2.0, "w": 0.1, "n_cells": 215},
        "F": {"fitted": True, "k": 2.0, "w": 0.1, "n_cells": 999},  # hypothetical — must be ignored
    })
    conn_f = _conn()
    pid_f = _materialize_seeded(conn_f, monkeypatch, f_bins=True)
    prov_f = json.loads(_row(conn_f, pid_f)["provenance_json"])

    assert prov_f.get("sigma_scale_k_applied") is None, "F-unit city must NEVER be σ-scaled"
    assert prov_f.get("uniform_mixture_w_applied") is None, "F-unit city must NEVER be uniform-mixed"

    # Same artifact, C-unit city: correction DOES fire.
    conn_c = _conn()
    pid_c = _materialize_seeded(conn_c, monkeypatch)
    prov_c = json.loads(_row(conn_c, pid_c)["provenance_json"])
    assert prov_c.get("sigma_scale_k_applied") == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 4. Composition with the settlement sigma floor (floor still a lower bound after scaling)
# ---------------------------------------------------------------------------

def test_sigma_floor_enforced_after_scaling(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _fitted_c(k=2.0, w=0.05))
    monkeypatch.setitem(cfg.settings["edli"], "edli_settlement_sigma_floor_enabled", True)

    def _large_floor(req, *, metric):
        return 999.0, None
    monkeypatch.setattr(mod, "_replacement_settlement_sigma_floor_lookup", _large_floor)

    conn = _conn()
    pid = _materialize_seeded(conn, monkeypatch)
    prov = json.loads(_row(conn, pid)["provenance_json"])

    assert prov["settlement_sigma_floor_applied"] is True
    assert prov["settlement_sigma_floor_c"] == pytest.approx(999.0)
    # Scale still recorded (it ran before the floor check)
    assert prov.get("sigma_scale_k_applied") == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 5. Artifact reader fail-soft semantics
# ---------------------------------------------------------------------------

def test_reader_missing_returns_inert(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mod, "_SIGMA_SCALE_FIT_PATH", str(tmp_path / "nope.json"))
    assert mod._replacement_sigma_scale_lookup("C") == (1.0, 0.0)


def test_reader_unfitted_family_returns_inert(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, {"C": {"fitted": False, "k": 9.0, "w": 0.9}})
    assert mod._replacement_sigma_scale_lookup("C") == (1.0, 0.0)


def test_reader_out_of_range_clamps_to_inert(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, {
        "C": {"fitted": True, "k": float("nan"), "w": 0.1},
        "F": {"fitted": True, "k": 2.0, "w": 5.0},  # w out of [0,1]
    })
    assert mod._replacement_sigma_scale_lookup("C") == (1.0, 0.1)  # bad k -> 1.0, valid w kept
    assert mod._replacement_sigma_scale_lookup("F") == (2.0, 0.0)  # bad w -> 0.0, valid k kept


def test_reader_malformed_json_returns_inert(monkeypatch, tmp_path) -> None:
    path = tmp_path / "sigma_scale_fit.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(mod, "_SIGMA_SCALE_FIT_PATH", str(path))
    assert mod._replacement_sigma_scale_lookup("C") == (1.0, 0.0)


# ---------------------------------------------------------------------------
# 6. _city_settlement_unit_from_bins still derives unit correctly (retained behavior)
# ---------------------------------------------------------------------------

def test_city_settlement_unit_from_bins_c_unit() -> None:
    assert mod._city_settlement_unit_from_bins(_request()) == "C"


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
