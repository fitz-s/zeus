# Created: 2026-07-28
# Last audited: 2026-09-29
# Purpose: Current-evidence q/bounds use the declared physical sigma and center.
# Fitted historical calibration remains diagnostic and cannot create live authority.
"""Current-shape serving equivalence and independent settlement-integral antibodies."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as materializer_mod
from src.data.replacement_forecast_materializer import (
    materialize_replacement_forecast_live,
)
from tests.test_replacement_forecast_materializer import (
    _conn,
    _hko_dt as _dt,
    _install_hko_live_fusion as _install_live_fusion,
    _hko_request as _request,
    _TemperatureBin,
    _fixed_center_debias,
    _hko_source_surface,
    _hko_native_surfaces,
    _current_baseline_data_version,
)

pytestmark = pytest.mark.usefixtures("_hko_source_surface")


def _install_current_evidence_fusion(monkeypatch: pytest.MonkeyPatch, conn, request=None):
    # Downstream write seam; real shape/geometry constructors, no authority mock.
    return _install_live_fusion(monkeypatch,conn=conn,request=request or _request(**_REQUEST_KWARGS),
                               shape_cycle_time=_dt(6))


def _full_row(conn) -> dict:
    """The FULL persisted posterior row (FIX 7): q_json, q_lcb_json, q_ucb_json vectors and the
    complete provenance dict -- not selected fields."""
    row = conn.execute(
        "SELECT q_json, q_lcb_json, q_ucb_json, provenance_json FROM forecast_posteriors ORDER BY posterior_id DESC LIMIT 1"
    ).fetchone()
    return {
        "q": json.loads(row["q_json"]),
        "q_lcb": json.loads(row["q_lcb_json"]) if row["q_lcb_json"] is not None else None,
        "q_ucb": json.loads(row["q_ucb_json"]) if row["q_ucb_json"] is not None else None,
        "provenance": json.loads(row["provenance_json"]),
    }


# The physical request uses Hong Kong's official ground proof after possession.
# Sep30 06Z -> Oct1 local-day end (Oct1 16Z) preserves the original +8h
# geometry: lead_target_h = 34.0h -> bucket
# [24,36) (NOT [36,48), which is where the UTC-anchored cut would have placed it).
_REQUEST_KWARGS = dict(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12))
_EXPECTED_BUCKET = "[24,36)"


def _fitted_artifact_for_default_request() -> dict:
    """A fitted, GATE-PASSED artifact keyed to EXACTLY the (unit, metric, bucket, city) the
    default request resolves to, satisfying the full FIX 6 strict schema."""
    global_k = 1.10
    bucket_k = 1.25
    city_c = 0.95
    buckets = {
        lab: ({"k": bucket_k, "fitted": True} if lab == _EXPECTED_BUCKET else {"k": global_k, "fitted": False})
        for lab in materializer_mod._SIGMA_TAU_BUCKET_LABELS
    }
    return {
        "_meta": {
            "authority": materializer_mod._SIGMA_TAU_ARTIFACT_AUTHORITY,
            "schema_version": materializer_mod._SIGMA_TAU_SCHEMA_VERSION,
            "tau_clock": materializer_mod._SIGMA_TAU_CLOCK_ID,
        },
        "families": {
            "C": {
                "high": {
                    "fitted": True,
                    # Varying bucket k + a non-empty city correction -> this fixture's shape is
                    # bucket_city_k_v1 (B2); a global_k_v1 declaration would fail the loader's
                    # shape-mismatch check against this shape.
                    "model_type": materializer_mod._SIGMA_TAU_MODEL_TYPE_BUCKET_CITY_K_V1,
                    "global_k": global_k,
                    "oos_gate": {"passed": True, "censored_delta": 0.05},
                    "n": 5000,
                    "buckets": buckets,
                    "cities": {"Hong Kong": {"c_raw": 0.9, "c_shrunk": city_c, "n": 200}},
                }
            }
        },
    }


_EXPECTED_APPLIED_K = 1.25 * 0.95


# ---------------------------------------------------------------------------
# (a) Historical path is byte-identical whether or not the tau artifact exists
# ---------------------------------------------------------------------------

def test_historical_path_ignores_sigma_tau_artifact(monkeypatch, tmp_path) -> None:
    """A diagnostic historical carrier stays artifact-inert and cannot become live."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    _install_current_evidence_fusion(monkeypatch, _conn())
    current = materializer_mod._replacement_bayes_precision_fusion_override(None)
    monkeypatch.setattr(materializer_mod, "_replacement_bayes_precision_fusion_override",
                        lambda *args, **kwargs: replace(current, current_evidence_shape=None))
    request = _request(**_REQUEST_KWARGS)
    without = materializer_mod._compute_posterior_payload(_conn(), request, metric="high", anchor_id=1)
    (tmp_path / "sigma_tau_calibration.json").write_text(json.dumps(_fitted_artifact_for_default_request()))
    with_artifact = materializer_mod._compute_posterior_payload(_conn(), request, metric="high", anchor_id=1)
    assert not without.live_eligible and not with_artifact.live_eligible
    assert (without.q, without.q_lcb_map, without.q_ucb_map, without.provenance_payload) == (
        with_artifact.q, with_artifact.q_lcb_map, with_artifact.q_ucb_map, with_artifact.provenance_payload)
    assert with_artifact.provenance_payload is None  # no historical certificate
    assert not materialize_replacement_forecast_live(_conn(), request).ok


# ---------------------------------------------------------------------------
# (b) Current-evidence path, no artifact -> byte-identical to the prior hardcoded neutral
# ---------------------------------------------------------------------------

def test_current_evidence_path_no_artifact_is_neutral(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)  # no file written -> absent
    conn = _conn()
    request = _install_current_evidence_fusion(monkeypatch,conn)

    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok is True
    full = _full_row(conn)

    assert full["provenance"]["replacement_sigma_basis"] == "decision_time_current_ensemble_within_plus_provider_between"
    assert full["provenance"]["sigma_scale_k_applied"] is None, "k must stay exactly 1.0 (untamped) with no artifact"
    assert "sigma_tau_artifact_hash" not in full["provenance"], (
        "FIX 7: the key must be OMITTED entirely when inert, not present with a null value"
    )


def test_current_evidence_path_no_artifact_keeps_stable_provenance_key_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Identical current evidence keeps a stable key set without an inert fitted hash."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)

    conn_hist = _conn()
    request_hist = _install_current_evidence_fusion(monkeypatch,conn_hist)
    result_hist = materialize_replacement_forecast_live(conn_hist, request_hist)
    assert result_hist.ok is True
    prov_hist = _full_row(conn_hist)["provenance"]

    conn_current = _conn()
    request_current = _install_current_evidence_fusion(monkeypatch,conn_current)
    result_current = materialize_replacement_forecast_live(conn_current, request_current)
    assert result_current.ok is True
    prov_current = _full_row(conn_current)["provenance"]

    assert "sigma_tau_artifact_hash" not in prov_hist
    assert "sigma_tau_artifact_hash" not in prov_current


# ---------------------------------------------------------------------------
# (c) Even a valid non-neutral fitted artifact cannot alter the current live regime
# ---------------------------------------------------------------------------

def test_current_evidence_path_ignores_valid_fitted_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    baseline_conn = _conn()
    request = _install_current_evidence_fusion(monkeypatch,baseline_conn)
    assert materialize_replacement_forecast_live(baseline_conn, request).ok
    baseline = _full_row(baseline_conn)
    artifact_bytes = json.dumps(_fitted_artifact_for_default_request()).encode("utf-8")
    (tmp_path / "sigma_tau_calibration.json").write_bytes(artifact_bytes)
    expected_hash = hashlib.sha256(artifact_bytes).hexdigest()

    # Hold one actual DB/body/ground/anchor input fixed; change only the fitted
    # artifact so full-row equality is not accidentally a cross-DB comparison.
    conn = baseline_conn
    _install_current_evidence_fusion(monkeypatch,conn,request)
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok is True
    prov = _full_row(conn)["provenance"]

    resolved = materializer_mod._resolve_sigma_tau_calibration(_request(**_REQUEST_KWARGS), "C", "high")
    assert resolved[:3] == pytest.approx((_EXPECTED_APPLIED_K, 0.0, 0.0))
    assert resolved[3] == expected_hash
    assert _full_row(conn) == baseline
    assert "sigma_tau_artifact_hash" not in prov
    assert prov["sigma_scale_k_applied"] is None


def test_current_evidence_path_rejects_artifact_missing_oos_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A group with fitted=True but NO oos_gate is a FIX-6 schema violation -- must resolve to
    neutral, exactly as if the artifact were absent. B5 (deep-review): a REJECTED artifact must
    never surface a hash in provenance -- the key is OMITTED entirely, matching the true-absent
    case, not merely null."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    artifact = _fitted_artifact_for_default_request()
    del artifact["families"]["C"]["high"]["oos_gate"]
    artifact_bytes = json.dumps(artifact).encode("utf-8")
    (tmp_path / "sigma_tau_calibration.json").write_bytes(artifact_bytes)

    conn = _conn()
    request = _install_current_evidence_fusion(monkeypatch,conn)
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok is True
    prov = _full_row(conn)["provenance"]

    assert prov["sigma_scale_k_applied"] is None
    assert "sigma_tau_artifact_hash" not in prov, (
        "B5: a rejected artifact (missing oos_gate) must never surface a hash -- the rejection is "
        "logged instead of stamped into provenance"
    )


def test_current_evidence_path_rejects_wrong_tau_clock_declaration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An artifact whose _meta.tau_clock does not match this module's serving-clock constant must
    be entirely rejected (FIX 6) -- protects against silently consuming an artifact fit under a
    different tau convention (e.g. a pre-FIX-1 UTC-anchored artifact)."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    artifact = _fitted_artifact_for_default_request()
    artifact["_meta"]["tau_clock"] = "computed_at_utc_v0"
    (tmp_path / "sigma_tau_calibration.json").write_text(json.dumps(artifact))

    conn = _conn()
    request = _install_current_evidence_fusion(monkeypatch,conn)
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok is True
    prov = _full_row(conn)["provenance"]

    assert prov["sigma_scale_k_applied"] is None


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("unit", ("C", "F"))
def test_current_shape_integrates_declared_sigma_despite_hostile_fitted_width_and_bias(monkeypatch, tmp_path, metric, unit):
    """Point q and bound draws share physical sigma, not fitted/floored width."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    artifact = _fitted_artifact_for_default_request()
    group = artifact["families"]["C"]["high"]
    artifact["families"] = {unit: {metric: group}}
    (tmp_path / "sigma_tau_calibration.json").write_text(json.dumps(artifact))
    sigma = .593198
    _fixed_center_debias(monkeypatch, shift_c=1.0, metric=metric)
    monkeypatch.setattr(materializer_mod, "_replacement_settlement_sigma_floor_lookup",
                        lambda *args, **kwargs: pytest.fail("current shape read fitted floor"))
    if unit == "C":
        step, left, right = 1.0, 24.5, 25.5
        bins = (_TemperatureBin("below", upper_c=24), _TemperatureBin("point", lower_c=25, upper_c=25),
                _TemperatureBin("above", lower_c=26))
    else:
        step, left, right = 5/9, (76.5-32)*5/9, (77.5-32)*5/9
        bins = (_TemperatureBin("below", upper_c=(76-32)*5/9, display_unit="F", settlement_unit="F"),
                _TemperatureBin("point", lower_c=25, upper_c=25, display_unit="F", settlement_unit="F"),
                _TemperatureBin("above", lower_c=(78-32)*5/9, display_unit="F", settlement_unit="F"))
    request = replace(_request(**_REQUEST_KWARGS, baseline_data_version=_current_baseline_data_version(metric)),
                      temperature_metric=metric, bins=bins, settlement_step_c=step)
    resolved = materializer_mod._resolve_sigma_tau_calibration(request, unit, metric)
    assert resolved[0] == pytest.approx(_EXPECTED_APPLIED_K) and resolved[3]
    original = materializer_mod._build_fused_q_bounds
    calls = []
    def observe_bounds(**kwargs):
        calls.append((kwargs["mu_star"], kwargs["predictive_sigma_c"]))
        return original(**kwargs)
    monkeypatch.setattr(materializer_mod, "_build_fused_q_bounds", observe_bounds)
    conn = _conn()
    request = _install_live_fusion(monkeypatch,conn=conn,request=request,
                                  shape_cycle_time=_dt(6),predictive_sigma_c=sigma)
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok, result.reason_codes
    full = _full_row(conn)
    cdf = lambda x: .5*(1+math.erf((x-25.0)/(sigma*math.sqrt(2))))
    expected = {"below": cdf(left), "point": cdf(right)-cdf(left), "above": 1-cdf(right)}
    assert full["q"] == pytest.approx(expected, abs=1e-12)
    assert calls and all(mu == 25 and width == sigma for mu, width in calls)
    prov = full["provenance"]
    assert prov["settlement_sigma_floor_applied"] is False
    assert prov["settlement_sigma_floor_c"] is None
    assert prov["sigma_scale_k_applied"] is None
    assert prov["center_debias_c"] is None
    assert "sigma_tau_artifact_hash" not in prov
    assert all(full["q_lcb"][key] <= full["q"][key] <= full["q_ucb"][key] for key in expected)
