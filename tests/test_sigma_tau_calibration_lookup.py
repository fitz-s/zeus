# Created: 2026-07-28
# Last reused/audited: 2026-07-28 (FIX 1/FIX 6 deep-review corrections)
# Lifecycle: created=2026-07-28; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Pure-function antibody for the sigma-tau calibration loader's strict typed-schema
#   fail-soft/fail-closed contract -- the artifact is the SOLE licensing authority and ANY schema
#   deviation must make it (or the narrower scope it affects) inert, never partially trusted.
# Reuse: Re-run whenever _sigma_tau_calibration_lookup's validation constants
#   (_SIGMA_TAU_ARTIFACT_AUTHORITY / _SIGMA_TAU_SCHEMA_VERSION / _SIGMA_TAU_CLOCK_ID / the k-range
#   bounds) change; these fixtures hardcode the CURRENT expected values.
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md. The sigma-tau calibration
#   is a NEW artifact (state/sigma_tau_calibration.json, scripts/fit_sigma_tau_calibration.py) that
#   replaces the hardcoded neutral (1.0, 0.0, 0.0) at the CURRENT-EVIDENCE materializer site. These
#   antibodies lock the STRICT typed-schema fail-soft/fail-closed contract (FIX 6): a served k must
#   be a real, in-range number (never a bool or an out-of-range float), `fitted`/`oos_gate.passed`
#   must be EXACT booleans, the bucket key set must match exactly, and the artifact's
#   authority/schema_version/tau_clock declaration must match this module's own constants exactly.
"""Antibodies for the sigma-tau calibration loader -- the fitted artifact is the SOLE authority,
and ANY schema deviation makes it (or the narrower scope it affects) inert, never partially
trusted.

Invariants proven:
  1. Artifact absent -> exactly (1.0, 0.0, 0.0, None).
  2. Malformed JSON -> exactly (1.0, 0.0, 0.0, None).
  3. tau_bucket is None (unbucketable lead) -> exactly (1.0, 0.0, 0.0, None), regardless of the
     artifact's content.
  4. Wrong top-level authority / schema_version / tau_clock declaration -> the WHOLE artifact is
     inert (hash still reported -- the bytes were read).
  5. A group with fitted=False, or oos_gate missing/passed=False -> inert (hash reported).
  6. `"fitted": "true"` (a STRING, not a bool) -> rejected (not truthy-coerced).
  7. `fitted=True` with oos_gate MISSING entirely -> rejected (oos_gate.passed=True is REQUIRED).
  8. A bool used as k (`true`/`false`) -> rejected (bool is an int subclass in Python; must be
     explicitly excluded).
  9. k outside [0.25, 4.0] (e.g. 1e100, or a too-small value) -> rejected.
  10. The bucket key set must match the expected 7 labels EXACTLY -- a missing or extra key
      invalidates the group.
  11. A fitted group + fitted bucket + no city -> k_eff equals the bucket's k exactly; w and
      floor_steps are always 0.0.
  12. An UNFITTED bucket (or one absent from the artifact, or one that itself fails validation)
      inherits the group's (already-validated) global_k.
  13. City variance shrinkage multiplies the bucket k; an unknown city, a missing city argument, or
      an out-of-range c_shrunk all contribute exactly c=1.0.
  14. _effective_sigma_tau_scale is a pure delegate to the lookup (no allow-list).
  15. _lead_target_h uses the CITY'S LOCAL midnight (DST-aware), not UTC.
  16. The artifact is read+validated once per file generation (mtime-keyed cache); a genuine new
      generation IS picked up.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod


def _write_artifact(tmp_path: Path, monkeypatch, artifact: dict, *, filename: str = "sigma_tau_calibration.json") -> Path:
    path = tmp_path / filename
    path.write_text(json.dumps(artifact))
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    return path


def _valid_meta() -> dict:
    return {
        "authority": mod._SIGMA_TAU_ARTIFACT_AUTHORITY,
        "schema_version": mod._SIGMA_TAU_SCHEMA_VERSION,
        "tau_clock": mod._SIGMA_TAU_CLOCK_ID,
    }


def _valid_buckets(*, fitted_bucket: str = "[24,36)", bucket_k: float = 1.30, global_k: float = 1.08) -> dict:
    return {
        lab: (
            {"k": bucket_k, "fitted": True}
            if lab == fitted_bucket
            else {"k": global_k, "fitted": False}
        )
        for lab in mod._SIGMA_TAU_BUCKET_LABELS
    }


def _valid_group(**overrides) -> dict:
    global_k = overrides.pop("global_k", 1.08)
    bucket_k = overrides.pop("bucket_k", 1.30)
    cities = overrides.pop("cities", {"Seoul": {"c_shrunk": 1.01}, "Ankara": {"c_shrunk": 0.73}})
    # Varying bucket k + non-empty cities -> this fixture's NATURAL model_type is bucket_city_k_v1
    # (a global_k_v1 declaration would fail the B2 shape-mismatch check against this shape).
    model_type = overrides.pop("model_type", mod._SIGMA_TAU_MODEL_TYPE_BUCKET_CITY_K_V1)
    group = {
        "fitted": True,
        "model_type": model_type,
        "global_k": global_k,
        "oos_gate": {"passed": True, "censored_delta": 0.05},
        "buckets": _valid_buckets(bucket_k=bucket_k, global_k=global_k),
        "cities": cities,
    }
    group.update(overrides)
    return group


def _artifact(families: dict, *, meta_overrides: dict | None = None) -> dict:
    meta = _valid_meta()
    if meta_overrides:
        meta.update(meta_overrides)
    return {"_meta": meta, "families": families}


def _fitted_c_high() -> dict:
    return {"C": {"high": _valid_group()}}


# ---------------------------------------------------------------------------
# 1-2. Artifact absent / malformed -> inert, no hash
# ---------------------------------------------------------------------------

def test_artifact_absent_is_inert(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul") == (1.0, 0.0, 0.0, None)


def test_malformed_artifact_is_inert(monkeypatch, tmp_path) -> None:
    path = tmp_path / "sigma_tau_calibration.json"
    path.write_text("{not json")
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)
    k, w, floor, artifact_hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul")
    assert (k, w, floor) == (1.0, 0.0, 0.0)
    assert artifact_hash is None, "B5: a rejected artifact must never surface a hash, even though its bytes were read -- the rejection is logged instead"


# ---------------------------------------------------------------------------
# 3. tau_bucket None -> inert regardless of artifact content
# ---------------------------------------------------------------------------

def test_none_tau_bucket_is_inert(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    assert mod._sigma_tau_calibration_lookup("C", "high", None, "Seoul") == (1.0, 0.0, 0.0, None)


# ---------------------------------------------------------------------------
# 4. Top-level authority / schema_version / tau_clock mismatch -> WHOLE artifact inert
# ---------------------------------------------------------------------------

def test_wrong_authority_is_inert_with_no_hash(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high(), meta_overrides={"authority": "some_other_authority"}))
    k, w, floor, artifact_hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul")
    assert (k, w, floor) == (1.0, 0.0, 0.0)
    assert artifact_hash is None  # B5: rejected -> no hash, rejection logged instead


def test_wrong_schema_version_is_inert(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high(), meta_overrides={"schema_version": 2}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul")[:3] == (1.0, 0.0, 0.0)


def test_schema_version_as_bool_is_rejected(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high(), meta_overrides={"schema_version": True}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul")[:3] == (1.0, 0.0, 0.0)


def test_wrong_tau_clock_is_inert(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high(), meta_overrides={"tau_clock": "computed_at_utc_v0"}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul")[:3] == (1.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 5. fitted=False / oos_gate missing or passed=False -> group inert
# ---------------------------------------------------------------------------

def test_group_fitted_false_is_inert(monkeypatch, tmp_path) -> None:
    families = {"F": {"low": _valid_group(fitted=False, buckets={}, cities={})}}
    _write_artifact(tmp_path, monkeypatch, _artifact(families))
    k, w, floor, artifact_hash = mod._sigma_tau_calibration_lookup("F", "low", "[12,24)", None)
    assert (k, w, floor) == (1.0, 0.0, 0.0)
    assert artifact_hash is None  # B5: rejected -> no hash, rejection logged instead


def test_group_missing_oos_gate_is_rejected_even_when_fitted_true(monkeypatch, tmp_path) -> None:
    """fitted=True with NO oos_gate key at all must be rejected -- oos_gate.passed=True is
    REQUIRED, not merely consulted when present."""
    group = _valid_group()
    del group["oos_gate"]
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_group_oos_gate_passed_false_is_inert(monkeypatch, tmp_path) -> None:
    group = _valid_group(oos_gate={"passed": False, "censored_delta": -0.03})
    _write_artifact(tmp_path, monkeypatch, _artifact({"F": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("F", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_group_fitted_as_string_is_rejected(monkeypatch, tmp_path) -> None:
    """`"fitted": "true"` is a STRING, not a bool -- must not be truthy-coerced."""
    group = _valid_group(fitted="true")
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_oos_gate_passed_as_string_is_rejected(monkeypatch, tmp_path) -> None:
    group = _valid_group(oos_gate={"passed": "true"})
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 8-9. k typed as bool, or out of [0.25, 4.0] range -> rejected
# ---------------------------------------------------------------------------

def test_global_k_as_bool_is_rejected(monkeypatch, tmp_path) -> None:
    group = _valid_group()
    group["global_k"] = True
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_global_k_absurdly_large_is_rejected(monkeypatch, tmp_path) -> None:
    group = _valid_group(global_k=1e100)
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_global_k_too_small_is_rejected(monkeypatch, tmp_path) -> None:
    group = _valid_group(global_k=0.05)
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_bucket_k_as_bool_invalidates_whole_group(monkeypatch, tmp_path) -> None:
    """B6 (deep-review): a malformed bucket subtree (fitted=True with a bool used as k) INVALIDATES
    THE WHOLE GROUP -- it is treated as corruption, not as "unfitted", so it must NOT silently fall
    back to inheriting global_k."""
    group = _valid_group(global_k=1.08)
    group["buckets"]["[24,36)"] = {"k": True, "fitted": True}
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_bucket_k_out_of_range_invalidates_whole_group(monkeypatch, tmp_path) -> None:
    """B6: fitted=True with an out-of-range k is corruption, not "unfitted" -- invalidates the
    whole group rather than silently inheriting global_k."""
    group = _valid_group(global_k=1.08)
    group["buckets"]["[24,36)"] = {"k": 9.9, "fitted": True}
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_schema_valid_unfitted_bucket_still_inherits_global(monkeypatch, tmp_path) -> None:
    """The ONLY legitimate inherit-global case: a bucket entry that is fully schema-valid with
    fitted EXACTLY False (the fitter's honest "not enough events" signal, not corruption)."""
    group = _valid_group(global_k=1.08)
    group["buckets"]["[24,36)"] = {"k": None, "fitted": False}
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)
    assert k == 1.08


# ---------------------------------------------------------------------------
# 10. Bucket key set must match exactly
# ---------------------------------------------------------------------------

def test_missing_bucket_key_invalidates_group(monkeypatch, tmp_path) -> None:
    group = _valid_group()
    del group["buckets"]["[72,inf)"]
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


def test_extra_bucket_key_invalidates_group(monkeypatch, tmp_path) -> None:
    group = _valid_group()
    group["buckets"]["[96,inf)"] = {"k": 1.0, "fitted": True}
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)[:3] == (1.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 11-12. Fitted bucket applies its own k; unfitted/absent/invalid bucket inherits global_k
# ---------------------------------------------------------------------------

def test_fitted_bucket_no_city_applies_bucket_k(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    k, w, floor, artifact_hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)
    assert k == 1.30
    assert w == 0.0
    assert floor == 0.0
    assert artifact_hash is not None


def test_unfitted_bucket_inherits_global_k(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[36,48)", None)
    assert k == 1.08


# ---------------------------------------------------------------------------
# 13. City variance shrinkage; unknown/absent/out-of-range city -> c=1.0
# ---------------------------------------------------------------------------

def test_city_shrinkage_multiplies_bucket_k(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Ankara")
    assert k == pytest.approx(1.30 * 0.73)


def test_unknown_city_contributes_no_adjustment(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Nowhereville")
    assert k == 1.30


def test_no_city_argument_contributes_no_adjustment(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)
    assert k == 1.30


def test_out_of_range_city_c_shrunk_invalidates_whole_group(monkeypatch, tmp_path) -> None:
    """B6: a PRESENT city entry with an out-of-range c_shrunk is corruption -- invalidates the
    whole group (unlike an ABSENT city, which safely contributes c=1.0 via .get(city, 1.0))."""
    group = _valid_group(cities={"Seoul": {"c_shrunk": 9.9}})
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul")[:3] == (1.0, 0.0, 0.0)


def test_city_c_shrunk_as_bool_invalidates_whole_group(monkeypatch, tmp_path) -> None:
    group = _valid_group(cities={"Seoul": {"c_shrunk": True}})
    _write_artifact(tmp_path, monkeypatch, _artifact({"C": {"high": group}}))
    assert mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "Seoul")[:3] == (1.0, 0.0, 0.0)


def test_absent_city_still_safe_when_group_otherwise_valid(monkeypatch, tmp_path) -> None:
    """Contrast with the invalidation cases above: a city simply NOT present as a key at all is
    the only legitimate "no adjustment" case -- it does not touch group validity."""
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    k, _w, _floor, _hash = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", "SomeAbsentCity")
    assert k == 1.30


# ---------------------------------------------------------------------------
# 14. _effective_sigma_tau_scale is a pure delegate (no allow-list)
# ---------------------------------------------------------------------------

def test_effective_scale_equals_lookup(monkeypatch, tmp_path) -> None:
    _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    for unit, metric, bucket, city in [("C", "high", "[24,36)", "Ankara"), ("F", "low", "[0,6)", None)]:
        assert mod._effective_sigma_tau_scale(unit, metric, bucket, city) == mod._sigma_tau_calibration_lookup(
            unit, metric, bucket, city
        )


# ---------------------------------------------------------------------------
# 15. _lead_target_h uses the CITY'S LOCAL midnight (DST-aware), not UTC
# ---------------------------------------------------------------------------

def test_lead_target_h_uses_city_local_midnight_not_utc() -> None:
    # target_date 2026-07-20; Asia/Shanghai is a FIXED +8h offset (no DST). Local midnight of
    # 2026-07-21 Shanghai == 2026-07-20T16:00:00Z, NOT 2026-07-21T00:00:00Z (the old UTC cut).
    issue_time = datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc)
    lead_h = mod._lead_target_h("2026-07-20", issue_time, "Asia/Shanghai")
    assert lead_h == pytest.approx(6.0), "expected the Shanghai LOCAL end (16:00Z), not the UTC end (00:00Z next day, which would give 14.0h)"


def test_lead_target_h_is_dst_aware_for_chicago() -> None:
    # 2026-07-20 is within US Central Daylight Time (UTC-5). Local midnight of 2026-07-21 Chicago
    # (CDT, -5) == 2026-07-21T05:00:00Z.
    issue_time = datetime(2026, 7, 20, 5, 0, 0, tzinfo=timezone.utc)
    lead_h = mod._lead_target_h("2026-07-20", issue_time, "America/Chicago")
    assert lead_h == pytest.approx(24.0)


def test_lead_target_h_accepts_string_target_date_and_issue_time() -> None:
    lead_h = mod._lead_target_h("2026-07-20", "2026-07-20T10:00:00+00:00", "Asia/Shanghai")
    assert lead_h == pytest.approx(6.0)


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


# ---------------------------------------------------------------------------
# 16. Generation-pinned cache: read+validate once per mtime, not per lookup
# ---------------------------------------------------------------------------

def test_artifact_cache_reflects_a_new_generation(monkeypatch, tmp_path) -> None:
    path = _write_artifact(tmp_path, monkeypatch, _artifact(_fitted_c_high()))
    k1, *_ = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)
    assert k1 == 1.30

    # Rewrite with DIFFERENT content and force a distinct mtime (a genuinely new generation) --
    # the next lookup must observe the new content, not a stale cached one.
    new_group = _valid_group(global_k=2.0, bucket_k=2.5)
    path.write_text(json.dumps(_artifact({"C": {"high": new_group}})))
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5.0))

    k2, *_ = mod._sigma_tau_calibration_lookup("C", "high", "[24,36)", None)
    assert k2 == 2.5, "a genuinely new file generation (different mtime) must be picked up"
