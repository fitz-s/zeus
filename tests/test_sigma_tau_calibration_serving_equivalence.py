# Created: 2026-07-28
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md. Proves the two safety
#   properties the sigma-tau calibration wiring must hold at the FULL materializer pipeline level
#   (not just the pure-function loader antibodies in test_sigma_tau_calibration_lookup.py):
#     (a) the HISTORICAL (non-current-evidence) path is COMPLETELY UNCHANGED by this artifact's
#         presence -- it never reads state/sigma_tau_calibration.json;
#     (b) the CURRENT-EVIDENCE path with NO artifact present is byte-identical to the prior
#         hardcoded neutral (1.0, 0.0, 0.0), and WITH a fitted artifact present, the applied k/
#         artifact-hash actually reach the persisted posterior's provenance -- i.e. the wiring, not
#         just the pure lookup function, is correct end-to-end.
"""Serving-equivalence antibodies for the sigma-tau calibration wiring."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as materializer_mod
from src.data.replacement_forecast_materializer import (
    _BayesPrecisionFusionFusionOverride,
    _current_evidence_shape_from_values,
    materialize_replacement_forecast_live,
)
from tests.test_replacement_forecast_materializer import (
    _conn,
    _dt,
    _install_live_fusion,
    _request,
)


def _current_shape_members() -> tuple[float, ...]:
    return tuple(25.0 + 0.3 * ((i % 7) - 3) for i in range(24))


def _install_current_evidence_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same fixed override as _install_live_fusion, but with current_evidence_shape POPULATED --
    routes materialization through the `_current_shape is not None` (Day0/current-evidence) branch
    this change wires into the new sigma-tau lookup."""
    shape = _current_evidence_shape_from_values(
        snapshot_id=42,
        source_cycle_time="2026-06-06T00:00:00+00:00",
        source_available_at="2026-06-06T02:00:00+00:00",
        members_c=_current_shape_members(),
        provider_values_c={"ecmwf_ifs": 24.8, "icon_global": 25.2},
        provider_weights={"ecmwf_ifs": 0.5, "icon_global": 0.5},
        center_c=25.0,
    )
    override = _BayesPrecisionFusionFusionOverride(
        anchor_value_c=25.0,
        anchor_sigma_c=0.35,
        method="test_bayes_precision_fusion",
        used_models=("ecmwf_ifs9", "gfs", "icon", "gem", "jma"),
        model_set_hash="test-model-set",
        resolution_mix_hash="test-resolution-mix",
        lead_bucket="d1",
        dropped_models=(),
        excluded_regionals=(),
        dropped_aliases=(),
        raw_model_forecast_ids=(101, 102, 103),
        anchor_bridge={"test": True},
        predictive_sigma_c=2.0,
        decorrelated_providers_complete=True,
        decorrelated_providers_served=5,
        decorrelated_providers_expected=5,
        current_value_serving={"ecmwf_ifs9": {"served_via": "single_runs"}},
        current_evidence_shape=shape.as_payload(),
        current_evidence_members_c=shape.members_c,
    )
    monkeypatch.setattr(materializer_mod, "_replacement_bayes_precision_fusion_override", lambda *args, **kwargs: override)


def _provenance(conn) -> dict:
    row = conn.execute("SELECT provenance_json FROM forecast_posteriors ORDER BY posterior_id DESC LIMIT 1").fetchone()
    return json.loads(row["provenance_json"])


def _fitted_artifact_for_default_request() -> dict:
    """A fitted artifact keyed to EXACTLY the (unit, metric, bucket, city) the default
    ``_request()`` resolves to: city=Shanghai (unit 'C'), metric 'high', target_date=2026-06-07,
    computed_at=2026-06-06T04:00Z -> lead_target_h=44.0h -> bucket [36,48)."""
    return {
        "_meta": {"authority": "sigma_tau_calibration_v1_mle"},
        "families": {
            "C": {
                "high": {
                    "fitted": True,
                    "global_k": 1.10,
                    "n": 5000,
                    "buckets": {"[36,48)": {"k": 1.25, "n": 400, "fitted": True}},
                    "cities": {"Shanghai": {"c_raw": 0.9, "c_shrunk": 0.95, "n": 200}},
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# (a) Historical path is byte-identical whether or not the tau artifact exists
# ---------------------------------------------------------------------------

def test_historical_path_ignores_sigma_tau_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)

    conn_without = _conn()
    _install_live_fusion(monkeypatch)  # current_evidence_shape stays None -> historical branch
    result_without = materialize_replacement_forecast_live(
        conn_without, _request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12))
    )
    assert result_without.ok is True
    prov_without = _provenance(conn_without)

    (tmp_path / "sigma_tau_calibration.json").write_text(json.dumps(_fitted_artifact_for_default_request()))

    conn_with = _conn()
    _install_live_fusion(monkeypatch)
    result_with = materialize_replacement_forecast_live(
        conn_with, _request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12))
    )
    assert result_with.ok is True
    prov_with = _provenance(conn_with)

    assert prov_without["q_shape"] == prov_with["q_shape"]
    assert prov_without["sigma_scale_k_applied"] == prov_with["sigma_scale_k_applied"]
    assert prov_with["sigma_tau_artifact_hash"] is None, (
        "the historical path must never read state/sigma_tau_calibration.json"
    )
    assert prov_without["replacement_sigma_basis"] == "fused_center_residual_std"


# ---------------------------------------------------------------------------
# (b) Current-evidence path, no artifact -> byte-identical to the prior hardcoded neutral
# ---------------------------------------------------------------------------

def test_current_evidence_path_no_artifact_is_neutral(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)  # no file written -> absent
    conn = _conn()
    _install_current_evidence_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(
        conn, _request(source_cycle_time=_dt(0), computed_at=_dt(4), expires_at=_dt(6))
    )
    assert result.ok is True
    prov = _provenance(conn)

    assert prov["replacement_sigma_basis"] == "decision_time_current_ensemble_within_plus_provider_between"
    assert prov["sigma_scale_k_applied"] is None, "k must stay exactly 1.0 (untamped) with no artifact"
    assert prov["sigma_tau_artifact_hash"] is None


# ---------------------------------------------------------------------------
# (c) Current-evidence path, WITH a fitted artifact -> k and artifact hash reach provenance
# ---------------------------------------------------------------------------

def test_current_evidence_path_applies_fitted_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    artifact_bytes = json.dumps(_fitted_artifact_for_default_request()).encode("utf-8")
    (tmp_path / "sigma_tau_calibration.json").write_bytes(artifact_bytes)
    expected_hash = hashlib.sha256(artifact_bytes).hexdigest()

    conn = _conn()
    _install_current_evidence_fusion(monkeypatch)
    result = materialize_replacement_forecast_live(
        conn, _request(source_cycle_time=_dt(0), computed_at=_dt(4), expires_at=_dt(6))
    )
    assert result.ok is True
    prov = _provenance(conn)

    assert prov["sigma_tau_artifact_hash"] == expected_hash
    assert prov["sigma_scale_k_applied"] == pytest.approx(1.25 * 0.95)
