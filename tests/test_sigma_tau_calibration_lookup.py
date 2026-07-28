# Created: 2026-07-28
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md. The sigma-tau calibration
#   is a NEW artifact (state/sigma_tau_calibration.json, scripts/fit_sigma_tau_calibration.py) that
#   replaces the hardcoded neutral (1.0, 0.0, 0.0) at the CURRENT-EVIDENCE materializer site. These
#   antibodies lock the fail-soft/fail-closed contract, mirroring
#   tests/test_replacement_sigma_scale_f_family.py's pattern for the sibling historical-path
#   artifact.
"""Antibodies for the sigma-tau calibration loader -- the fitted artifact is the SOLE authority.

Invariants proven:
  1. Artifact absent -> exactly (1.0, 0.0, 0.0, None). Byte-identical to the prior hardcode.
  2. Malformed JSON -> exactly (1.0, 0.0, 0.0, None).
  3. tau_bucket is None (unbucketable lead) -> exactly (1.0, 0.0, 0.0, None), regardless of the
     artifact's content.
  4. A group (unit, metric) with fitted=False -> inert (1.0, 0.0, 0.0), but the artifact hash is
     still returned (full audit trail even on an inert outcome).
  5. A fitted group + fitted bucket + no city -> k_eff equals the bucket's k exactly; w and
     floor_steps are always 0.0.
  6. A fitted group + UNFITTED bucket -> inherits the group's global_k.
  7. A fitted group whose global_k is itself invalid -> inert (nothing valid to inherit).
  8. A fitted group + fitted bucket + a city with a shrunk c -> k_eff = k_bucket * c_shrunk.
  9. A city absent from the artifact's cities map contributes c=1.0 (no per-city adjustment).
  10. _effective_sigma_tau_scale is a pure delegate to the lookup (no allow-list).
  11. _lead_target_h / _sigma_tau_bucket_label boundary correctness.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod


def _write_artifact(tmp_path: Path, monkeypatch, families: dict) -> None:
    path = tmp_path / "sigma_tau_calibration.json"
    path.write_text(json.dumps({"_meta": {"authority": "sigma_tau_calibration_v1_mle"}, "families": families}))
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)


def _fitted_c_high() -> dict:
    return {
        "C": {
            "high": {
                "fitted": True,
                "global_k": 1.08,
                "n": 14700,
                "buckets": {
                    "[0,6)": {"k": 0.91, "n": 689, "fitted": True},
                    "[6,12)": {"k": 1.37, "n": 1103, "fitted": True},
                    "[12,24)": {"k": 1.29, "n": 2773, "fitted": True},
                    "[24,36)": {"k": 1.20, "n": 1933, "fitted": True},
                    "[36,48)": {"k": None, "n": 40, "fitted": False},
                    "[48,72)": {"k": 1.20, "n": 1675, "fitted": True},
                    "[72,inf)": {"k": None, "n": 0, "fitted": False},
                },
                "cities": {
                    "Seoul": {"c_raw": 1.02, "c_shrunk": 1.01, "n": 145},
                    "Ankara": {"c_raw": 0.59, "c_shrunk": 0.73, "n": 254},
                },
            }
        }
    }


def _refused_f_low() -> dict:
    return {
        "F": {
            "low": {
                "fitted": False,
                "global_k": 1.0,
                "n": 12,
                "refusal_reason": "INSUFFICIENT_N:12<60",
                "buckets": {},
                "cities": {},
            }
        }
    }


# ---------------------------------------------------------------------------
# 1. Artifact absent -> inert, no hash
# ---------------------------------------------------------------------------

def test_artifact_absent_is_inert(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    assert mod._sigma_tau_calibration_lookup("C", "high", "[12,24)", "Seoul") == (1.0, 0.0, 0.0, None)


# ---------------------------------------------------------------------------
# 2. Malformed JSON -> inert, no hash
# ---------------------------------------------------------------------------

def test_malformed_artifact_is_inert(monkeypatch, tmp_path) -> None:
    path = tmp_path / "sigma_tau_calibration.json"
    path.write_text("{not json")
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    assert mod._sigma_tau_calibration_lookup("C", "high", "[12,24)", "Seoul") == (1.0, 0.0, 0.0, None)


# ---------------------------------------------------------------------------
# 3. tau_bucket None -> inert regardless of artifact content
# ---------------------------------------------------------------------------

def test_none_tau_bucket_is_inert(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _fitted_c_high())
    assert mod._sigma_tau_calibration_lookup("C", "high", None, "Seoul") == (1.0, 0.0, 0.0, None)


# ---------------------------------------------------------------------------
# 4. Refused group stays inert, but the artifact hash is still reported
# ---------------------------------------------------------------------------

def test_refused_group_is_inert_but_hash_reported(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _refused_f_low())
    k, w, floor, artifact_hash = mod._sigma_tau_calibration_lookup("F", "low", "[12,24)", None)
    assert (k, w, floor) == (1.0, 0.0, 0.0)
    assert artifact_hash is not None, "the artifact WAS read/parsed -- hash must be reported even when inert"


# ---------------------------------------------------------------------------
# 5. Fitted group + fitted bucket + no city -> k_eff equals the bucket k exactly
# ---------------------------------------------------------------------------

def test_fitted_bucket_no_city_applies_bucket_k(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _fitted_c_high())
    k, w, floor, artifact_hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)
    assert k == 1.20
    assert w == 0.0
    assert floor == 0.0
    assert artifact_hash is not None


# ---------------------------------------------------------------------------
# 6. Unfitted bucket inherits the group global_k
# ---------------------------------------------------------------------------

def test_unfitted_bucket_inherits_global_k(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _fitted_c_high())
    k, w, floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[36,48)", None)
    assert k == 1.08, "an UNFITTED bucket (n=40<60) must inherit the family/metric-global pooled k"


def test_bucket_absent_from_artifact_also_inherits_global_k(monkeypatch, tmp_path) -> None:
    """A bucket key entirely missing from the artifact (not just fitted=False) is treated the
    same as an unfitted bucket -- inherit global_k, never raise."""
    families = _fitted_c_high()
    del families["C"]["high"]["buckets"]["[24,36)"]
    _write_artifact(tmp_path, monkeypatch, families)
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)
    assert k == 1.08


# ---------------------------------------------------------------------------
# 7. Group with an invalid global_k has nothing valid to inherit -> inert
# ---------------------------------------------------------------------------

def test_unfitted_bucket_with_invalid_global_k_is_inert(monkeypatch, tmp_path) -> None:
    families = _fitted_c_high()
    families["C"]["high"]["global_k"] = -1.0  # non-positive: invalid
    _write_artifact(tmp_path, monkeypatch, families)
    k, w, floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[36,48)", None)
    assert (k, w, floor) == (1.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 8. City variance shrinkage multiplies the bucket k
# ---------------------------------------------------------------------------

def test_city_shrinkage_multiplies_bucket_k(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _fitted_c_high())
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Ankara")
    assert k == pytest.approx(1.20 * 0.73)


# ---------------------------------------------------------------------------
# 9. A city absent from the artifact contributes c=1.0 -- no adjustment, no failure
# ---------------------------------------------------------------------------

def test_unknown_city_contributes_no_adjustment(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _fitted_c_high())
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Nowhereville")
    assert k == 1.20


def test_no_city_argument_contributes_no_adjustment(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _fitted_c_high())
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)
    assert k == 1.20


def test_non_positive_city_c_shrunk_is_ignored(monkeypatch, tmp_path) -> None:
    families = _fitted_c_high()
    families["C"]["high"]["cities"]["Seoul"]["c_shrunk"] = 0.0
    _write_artifact(tmp_path, monkeypatch, families)
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul")
    assert k == 1.20, "a malformed non-positive c_shrunk must fall back to c=1.0, never propagate"


# ---------------------------------------------------------------------------
# 10. _effective_sigma_tau_scale is a pure delegate (no allow-list)
# ---------------------------------------------------------------------------

def test_effective_scale_equals_lookup(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _fitted_c_high())
    for unit, metric, bucket, city in [("C", "high", "[24,36)", "Ankara"), ("F", "low", "[0,6)", None)]:
        assert mod._effective_sigma_tau_scale(unit, metric, bucket, city) == mod._sigma_tau_calibration_lookup(
            unit, metric, bucket, city
        )


# ---------------------------------------------------------------------------
# 11. _lead_target_h / _sigma_tau_bucket_label correctness
# ---------------------------------------------------------------------------

def test_lead_target_h_measures_to_end_of_target_date_utc() -> None:
    # target_date 2026-07-20 ends at 2026-07-21T00:00:00Z; computed_at 18h earlier.
    computed_at = datetime(2026, 7, 20, 6, 0, 0, tzinfo=timezone.utc)
    lead_h = mod._lead_target_h(date(2026, 7, 20), computed_at)
    assert lead_h == pytest.approx(18.0)


def test_lead_target_h_accepts_string_target_date() -> None:
    computed_at = "2026-07-20T00:00:00+00:00"
    lead_h = mod._lead_target_h("2026-07-20", computed_at)
    assert lead_h == pytest.approx(24.0)


def test_bucket_label_boundaries() -> None:
    assert mod._sigma_tau_bucket_label(0.0) == "[0,6)"
    assert mod._sigma_tau_bucket_label(5.999) == "[0,6)"
    assert mod._sigma_tau_bucket_label(6.0) == "[6,12)"
    assert mod._sigma_tau_bucket_label(72.0) == "[72,inf)"
    assert mod._sigma_tau_bucket_label(10_000.0) == "[72,inf)"


def test_bucket_label_none_for_negative_or_nonfinite_lead() -> None:
    assert mod._sigma_tau_bucket_label(-0.001) is None
    assert mod._sigma_tau_bucket_label(math.nan) is None
    assert mod._sigma_tau_bucket_label(math.inf) is None, (
        "an infinite lead is non-finite input, not a real trading lead -- unbucketable, "
        "distinct from a large-but-finite lead which correctly buckets to [72,inf)"
    )
