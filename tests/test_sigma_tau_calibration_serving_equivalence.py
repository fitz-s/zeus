# Created: 2026-07-28
# Last reused/audited: 2026-07-28 (FIX 1/FIX 6/FIX 7 deep-review corrections)
# Lifecycle: created=2026-07-28; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Full-pipeline safety-property antibody for the sigma-tau calibration wiring -- proves
#   the historical path is untouched and the current-evidence path is byte-identical to today when
#   the artifact is absent, at the FULL materialize_replacement_forecast_live() level.
# Reuse: Re-run whenever the artifact schema (authority/schema_version/tau_clock/bucket keys) or the
#   materializer's provenance dict shape changes; the fixtures here must be kept in sync with both.
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md. Proves the two safety
#   properties the sigma-tau calibration wiring must hold at the FULL materializer pipeline level
#   (not just the pure-function loader antibodies in test_sigma_tau_calibration_lookup.py):
#     (a) the HISTORICAL (non-current-evidence) path is COMPLETELY UNCHANGED by this artifact's
#         presence -- it never reads state/sigma_tau_calibration.json;
#     (b) the CURRENT-EVIDENCE path with NO artifact present is BYTE-IDENTICAL (FULL provenance
#         dict, not selected fields, plus the full q/q_lcb/q_ucb vectors) to the prior hardcoded
#         neutral (1.0, 0.0, 0.0), and WITH a fitted+gate-passed artifact present, the applied k/
#         artifact-hash actually reach the persisted posterior's provenance end-to-end.
#   FIX 7: sigma_tau_artifact_hash is OMITTED (not merely null) from the inert provenance dict, so
#   the equivalence claim is a literal dict-key-set equality, not "same values, extra null key".
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


# The default _request() resolves to city=Shanghai (unit 'C', timezone Asia/Shanghai, a FIXED
# +8h offset with no DST), metric 'high', target_date=2026-06-07, source_cycle_time=
# 2026-06-06T06:00Z (must be a valid 00/06/12/18 UTC ECMWF cycle). FIX 1: tau is now measured to
# the city's LOCAL target-date end, not UTC -- Shanghai local midnight of 2026-06-08 is
# 2026-06-07T16:00Z (UTC+8), so lead_target_h = 16:00 (Jun 7) - 06:00 (Jun 6) = 34.0h -> bucket
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
                    "global_k": global_k,
                    "oos_gate": {"passed": True, "censored_delta": 0.05},
                    "n": 5000,
                    "buckets": buckets,
                    "cities": {"Shanghai": {"c_raw": 0.9, "c_shrunk": city_c, "n": 200}},
                }
            }
        },
    }


_EXPECTED_APPLIED_K = 1.25 * 0.95


# ---------------------------------------------------------------------------
# (a) Historical path is byte-identical whether or not the tau artifact exists
# ---------------------------------------------------------------------------

def test_historical_path_ignores_sigma_tau_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)

    conn_without = _conn()
    _install_live_fusion(monkeypatch)  # current_evidence_shape stays None -> historical branch
    result_without = materialize_replacement_forecast_live(conn_without, _request(**_REQUEST_KWARGS))
    assert result_without.ok is True
    full_without = _full_row(conn_without)

    (tmp_path / "sigma_tau_calibration.json").write_text(json.dumps(_fitted_artifact_for_default_request()))

    conn_with = _conn()
    _install_live_fusion(monkeypatch)
    result_with = materialize_replacement_forecast_live(conn_with, _request(**_REQUEST_KWARGS))
    assert result_with.ok is True
    full_with = _full_row(conn_with)

    assert full_without["q"] == full_with["q"]
    assert full_without["q_lcb"] == full_with["q_lcb"]
    assert full_without["q_ucb"] == full_with["q_ucb"]
    assert full_without["provenance"] == full_with["provenance"], (
        "the historical path's FULL provenance dict (every key) must be unaffected by whether "
        "state/sigma_tau_calibration.json exists"
    )
    assert "sigma_tau_artifact_hash" not in full_with["provenance"], (
        "the historical path must never read state/sigma_tau_calibration.json, and the key must be "
        "OMITTED (FIX 7), not merely null"
    )
    assert full_without["provenance"]["replacement_sigma_basis"] == "fused_center_residual_std"


# ---------------------------------------------------------------------------
# (b) Current-evidence path, no artifact -> byte-identical to the prior hardcoded neutral
# ---------------------------------------------------------------------------

def test_current_evidence_path_no_artifact_is_neutral(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)  # no file written -> absent
    conn = _conn()
    _install_current_evidence_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(conn, _request(**_REQUEST_KWARGS))
    assert result.ok is True
    full = _full_row(conn)

    assert full["provenance"]["replacement_sigma_basis"] == "decision_time_current_ensemble_within_plus_provider_between"
    assert full["provenance"]["sigma_scale_k_applied"] is None, "k must stay exactly 1.0 (untamped) with no artifact"
    assert "sigma_tau_artifact_hash" not in full["provenance"], (
        "FIX 7: the key must be OMITTED entirely when inert, not present with a null value"
    )


def test_current_evidence_path_no_artifact_matches_historical_path_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A second angle on (b): with no artifact, the CURRENT-EVIDENCE path's provenance key set
    (aside from the fields that legitimately differ between the two probability regimes, e.g.
    replacement_sigma_basis and the current_evidence_shape fields) never gains an extra
    sigma_tau_artifact_hash key that the pre-artifact code never had."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)

    conn_hist = _conn()
    _install_live_fusion(monkeypatch)
    result_hist = materialize_replacement_forecast_live(conn_hist, _request(**_REQUEST_KWARGS))
    assert result_hist.ok is True
    prov_hist = _full_row(conn_hist)["provenance"]

    conn_current = _conn()
    _install_current_evidence_fusion(monkeypatch)
    result_current = materialize_replacement_forecast_live(conn_current, _request(**_REQUEST_KWARGS))
    assert result_current.ok is True
    prov_current = _full_row(conn_current)["provenance"]

    assert "sigma_tau_artifact_hash" not in prov_hist
    assert "sigma_tau_artifact_hash" not in prov_current


# ---------------------------------------------------------------------------
# (c) Current-evidence path, WITH a fitted+gate-passed artifact -> k and hash reach provenance
# ---------------------------------------------------------------------------

def test_current_evidence_path_applies_fitted_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    artifact_bytes = json.dumps(_fitted_artifact_for_default_request()).encode("utf-8")
    (tmp_path / "sigma_tau_calibration.json").write_bytes(artifact_bytes)
    expected_hash = hashlib.sha256(artifact_bytes).hexdigest()

    conn = _conn()
    _install_current_evidence_fusion(monkeypatch)
    result = materialize_replacement_forecast_live(conn, _request(**_REQUEST_KWARGS))
    assert result.ok is True
    prov = _full_row(conn)["provenance"]

    assert prov["sigma_tau_artifact_hash"] == expected_hash
    assert prov["sigma_scale_k_applied"] == pytest.approx(_EXPECTED_APPLIED_K)


def test_current_evidence_path_rejects_artifact_missing_oos_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A group with fitted=True but NO oos_gate is a FIX-6 schema violation -- must resolve to
    neutral, exactly as if the artifact were absent, with the hash still reported (bytes were
    read)."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    artifact = _fitted_artifact_for_default_request()
    del artifact["families"]["C"]["high"]["oos_gate"]
    artifact_bytes = json.dumps(artifact).encode("utf-8")
    (tmp_path / "sigma_tau_calibration.json").write_bytes(artifact_bytes)
    expected_hash = hashlib.sha256(artifact_bytes).hexdigest()

    conn = _conn()
    _install_current_evidence_fusion(monkeypatch)
    result = materialize_replacement_forecast_live(conn, _request(**_REQUEST_KWARGS))
    assert result.ok is True
    prov = _full_row(conn)["provenance"]

    assert prov["sigma_scale_k_applied"] is None
    assert prov["sigma_tau_artifact_hash"] == expected_hash


def test_current_evidence_path_rejects_wrong_tau_clock_declaration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An artifact whose _meta.tau_clock does not match this module's serving-clock constant must
    be entirely rejected (FIX 6) -- protects against silently consuming an artifact fit under a
    different tau convention (e.g. a pre-FIX-1 UTC-anchored artifact)."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    artifact = _fitted_artifact_for_default_request()
    artifact["_meta"]["tau_clock"] = "computed_at_utc_v0"
    (tmp_path / "sigma_tau_calibration.json").write_text(json.dumps(artifact))

    conn = _conn()
    _install_current_evidence_fusion(monkeypatch)
    result = materialize_replacement_forecast_live(conn, _request(**_REQUEST_KWARGS))
    assert result.ok is True
    prov = _full_row(conn)["provenance"]

    assert prov["sigma_scale_k_applied"] is None
