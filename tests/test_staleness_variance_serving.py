#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (b): staleness enters serving ONLY as fitted error variance added to the
#   member's residual second moment (downweight, never exclude); between-spread restricted
#   to the freshest coherent cohort (±3h); everything fails OPEN to byte-identical serving.
"""Serving-side staleness variance + between-spread cohort antibodies.

(1) src/forecast/staleness_variance.v_for: fail-open zeros (artifact absent / unknown
    model / bad lag), bucket mapping (freshest + floor(age/24)), clamp beyond the largest
    fitted bucket.
(2) Materializer center weights: artifact absent => byte-identical basis+weights; artifact
    present + a model served from an older cycle => that model downweighted, staleness_m2
    stamped in the precision basis for that model ONLY; fresh serving with an artifact
    present stays byte-identical (no-op invariant).
(3) _current_evidence_shape_from_values provider_cycles cohort: mixed cycles => between
    over the coherent cohort only; coherent<2 / cycles absent / unparseable => all
    providers, byte-identical shape_hash; cohort provenance stamped only when the filter
    excluded someone; the <2-weighted-providers ValueError stays condition-identical.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date
from pathlib import Path

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod
import src.forecast.staleness_variance as sv
from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (
    _conn,
    _request,
    _seed_current_single_runs,
    _seed_history,
)

MODELS = ["ecmwf_ifs", "ukmo_global_deterministic_10km", "icon_global", "icon_eu"]
STALE_MODEL = "ukmo_global_deterministic_10km"


def _write_artifact(tmp_path: Path, models: dict) -> Path:
    artifact = {
        "schema_version": 1,
        "as_of": "2026-06-01",
        "generated_at": "2026-06-01T00:00:00+00:00",
        "git_sha": "test",
        "unit": "degC2",
        "min_cell_n": 30,
        "settled_cells_used": 999,
        "models": models,
    }
    payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
    name = "staleness_variance_20260601.json"
    (tmp_path / name).write_text(payload, encoding="utf-8")
    (tmp_path / "ACTIVE.json").write_text(
        json.dumps(
            {"artifact": name, "sha256": hashlib.sha256(payload.encode()).hexdigest(),
             "as_of": "2026-06-01"},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_artifact_cache():
    sv._load_active_artifact.cache_clear()
    yield
    sv._load_active_artifact.cache_clear()


# ---------------------------------------------------------------------------
# (1) v_for pure lookup
# ---------------------------------------------------------------------------

def test_v_for_fails_open_to_zero(tmp_path, monkeypatch) -> None:
    # Artifact dir empty -> 0.0 for everything.
    monkeypatch.setenv(sv.ENV_STALENESS_VARIANCE_DIR, str(tmp_path))
    assert sv.v_for("any_model", "high", 48.0) == 0.0
    # Artifact present but model/metric unknown -> 0.0; bad lag -> 0.0.
    _write_artifact(tmp_path, {"m1": {"high": {
        "freshest_lead": 0, "m2_by_lead": {"0": 1.0}, "n_by_lead": {"0": 40},
        "v_by_lead": {"0": 0.0, "1": 2.0},
    }}})
    sv._load_active_artifact.cache_clear()
    assert sv.v_for("unknown", "high", 48.0) == 0.0
    assert sv.v_for("m1", "low", 48.0) == 0.0
    assert sv.v_for("m1", "high", 0.0) == 0.0
    assert sv.v_for("m1", "high", float("nan")) == 0.0
    assert sv.v_for("m1", "high", -5.0) == 0.0


def test_v_for_sha_mismatch_fails_open(tmp_path, monkeypatch) -> None:
    _write_artifact(tmp_path, {"m1": {"high": {
        "freshest_lead": 0, "m2_by_lead": {"0": 1.0}, "n_by_lead": {"0": 40},
        "v_by_lead": {"0": 0.0, "1": 2.0},
    }}})
    pointer = json.loads((tmp_path / "ACTIVE.json").read_text())
    pointer["sha256"] = "0" * 64
    (tmp_path / "ACTIVE.json").write_text(json.dumps(pointer))
    monkeypatch.setenv(sv.ENV_STALENESS_VARIANCE_DIR, str(tmp_path))
    assert sv.v_for("m1", "high", 30.0) == 0.0


def test_v_for_bucket_mapping_and_clamp(tmp_path, monkeypatch) -> None:
    _write_artifact(tmp_path, {"m1": {"high": {
        "freshest_lead": 0,
        "m2_by_lead": {"0": 1.0, "1": 3.0, "3": 9.0},
        "n_by_lead": {"0": 40, "1": 40, "3": 40},
        "v_by_lead": {"0": 0.0, "1": 2.0, "3": 8.0},
    }}})
    monkeypatch.setenv(sv.ENV_STALENESS_VARIANCE_DIR, str(tmp_path))
    assert sv.v_for("m1", "high", 6.0) == 0.0     # bucket 0: same-day lag
    assert sv.v_for("m1", "high", 24.0) == 2.0    # bucket 1
    assert sv.v_for("m1", "high", 47.9) == 2.0    # bucket 1 (floor)
    assert sv.v_for("m1", "high", 48.0) == 2.0    # bucket 2 unfitted -> largest fitted <= 2
    assert sv.v_for("m1", "high", 72.0) == 8.0    # bucket 3
    assert sv.v_for("m1", "high", 24.0 * 30) == 8.0  # clamp to largest fitted (measured only)


# ---------------------------------------------------------------------------
# (2) materializer center-weight inflation
# ---------------------------------------------------------------------------

def _seed_stale_ukmo(conn, *, value: float = 23.0) -> None:
    """Seed the STALE_MODEL's only current row at a cycle 48h BEFORE the request cycle
    (a previous-cycle single_runs substitution) so its served cycle-lag is 48h."""
    req = _request()
    target_date = mod._date_text(req.target_date)
    old_cycle = "2026-06-04T00:00:00+00:00"
    lead = mod._bayes_precision_fusion_city_local_lead_days(
        computed_at=mod._to_utc(req.computed_at, field_name="computed_at"),
        target_local_date=date.fromisoformat(target_date), tz_name="Europe/Paris",
    )
    conn.execute(
        """INSERT INTO raw_model_forecasts
           (model, city, target_date, metric, source_cycle_time, source_available_at,
            captured_at, lead_days, forecast_value_c, endpoint, model_name, source_family)
           VALUES (?, 'Paris', ?, 'high', ?, '2026-06-04T03:00:00+00:00',
                   '2026-06-04T03:30:00+00:00', ?, ?, 'single_runs', ?,
                   'openmeteo_single_runs')""",
        (STALE_MODEL, target_date, old_cycle, lead, value, STALE_MODEL),
    )


def _override_with_stale_ukmo(monkeypatch, artifact_dir: Path | None):
    monkeypatch.setitem(
        cfg.settings["edli"], "replacement_0_1_bayes_precision_fusion_enabled", True
    )
    # None => keep the empty per-test dir the autouse isolation fixture set
    # (artifact-ABSENT); NOT delenv, which would fall back to the live default
    # dir once state/staleness_variance/ holds a fitted artifact.
    if artifact_dir is not None:
        monkeypatch.setenv(sv.ENV_STALENESS_VARIANCE_DIR, str(artifact_dir))
    sv._load_active_artifact.cache_clear()
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=MODELS)
    # Fresh models at the selected cycle; the stale model ONLY at the 48h-older cycle.
    _seed_current_single_runs(conn, values={"icon_global": 23.5, "icon_eu": 23.2})
    _seed_stale_ukmo(conn)
    ov = mod._replacement_bayes_precision_fusion_override(
        _request(), metric="high", anchor_value_corrected_c=27.0, conn=conn
    )
    assert ov is not None
    assert STALE_MODEL in ov.precision_center_basis  # stale member still ENTERS (never excluded)
    return ov


def test_artifact_absent_is_byte_identical(monkeypatch, tmp_path) -> None:
    ov_none = _override_with_stale_ukmo(monkeypatch, None)
    ov_empty = _override_with_stale_ukmo(monkeypatch, tmp_path)  # dir with no ACTIVE.json
    assert ov_none.precision_basis_hash == ov_empty.precision_basis_hash
    assert ov_none.precision_center_basis == ov_empty.precision_center_basis
    assert all(
        "staleness_m2" not in entry for entry in ov_none.precision_center_basis.values()
    )


def test_stale_model_downweighted_fresh_models_gain(monkeypatch, tmp_path) -> None:
    baseline = _override_with_stale_ukmo(monkeypatch, None)
    _write_artifact(tmp_path, {STALE_MODEL: {"high": {
        "freshest_lead": 0,
        "m2_by_lead": {"0": 0.2, "1": 5.0, "2": 25.0},
        "n_by_lead": {"0": 60, "1": 60, "2": 60},
        "v_by_lead": {"0": 0.0, "1": 4.8, "2": 24.8},
    }}})
    inflated = _override_with_stale_ukmo(monkeypatch, tmp_path)

    base_w = {m: e["weight"] for m, e in baseline.precision_center_basis.items()}
    infl_w = {m: e["weight"] for m, e in inflated.precision_center_basis.items()}
    # 48h cycle-lag -> bucket 2 -> v=24.8 degC² added: the stale member is DOWNWEIGHTED...
    assert infl_w[STALE_MODEL] < base_w[STALE_MODEL]
    assert infl_w[STALE_MODEL] > 0.0  # ...but NEVER excluded (E4)
    # ...and the fresh members absorb the mass (ordering flips the right way).
    # (icon_global is family-deduplicated out for Paris — icon_eu is the DWD-ICON rep.)
    assert infl_w["icon_eu"] > base_w["icon_eu"]
    assert infl_w["ecmwf_ifs"] > base_w["ecmwf_ifs"]
    # Provenance: staleness_m2 stamped for the stale member ONLY; raw_m2 stays the
    # UNinflated residual m2 (the inflation is a separate named term, not m2 mutation).
    assert inflated.precision_center_basis[STALE_MODEL]["staleness_m2"] == pytest.approx(24.8)
    assert inflated.precision_center_basis[STALE_MODEL]["raw_m2"] == pytest.approx(
        baseline.precision_center_basis[STALE_MODEL]["raw_m2"]
    )
    for m, entry in inflated.precision_center_basis.items():
        if m != STALE_MODEL:
            assert "staleness_m2" not in entry
    assert inflated.precision_basis_hash != baseline.precision_basis_hash


def test_artifact_present_all_fresh_is_byte_identical(monkeypatch, tmp_path) -> None:
    """No-op invariant: an artifact on disk with every member cycle-fresh changes nothing."""
    monkeypatch.setitem(
        cfg.settings["edli"], "replacement_0_1_bayes_precision_fusion_enabled", True
    )

    def _run(artifact_dir):
        if artifact_dir is None:
            monkeypatch.delenv(sv.ENV_STALENESS_VARIANCE_DIR, raising=False)
        else:
            monkeypatch.setenv(sv.ENV_STALENESS_VARIANCE_DIR, str(artifact_dir))
        sv._load_active_artifact.cache_clear()
        conn = _conn()
        _seed_history(conn, decision=date(2026, 6, 7), models=MODELS)
        _seed_current_single_runs(
            conn,
            values={"ukmo_global_deterministic_10km": 23.0, "icon_global": 23.5, "icon_eu": 23.2},
        )
        ov = mod._replacement_bayes_precision_fusion_override(
            _request(), metric="high", anchor_value_corrected_c=27.0, conn=conn
        )
        assert ov is not None
        return ov

    baseline = _run(None)
    _write_artifact(tmp_path, {STALE_MODEL: {"high": {
        "freshest_lead": 0, "m2_by_lead": {"0": 0.2, "1": 5.0},
        "n_by_lead": {"0": 60, "1": 60}, "v_by_lead": {"0": 0.0, "1": 4.8},
    }}})
    fresh = _run(tmp_path)
    assert fresh.precision_basis_hash == baseline.precision_basis_hash
    assert fresh.precision_center_basis == baseline.precision_center_basis


# ---------------------------------------------------------------------------
# (3) between-spread freshest-coherent-cohort
# ---------------------------------------------------------------------------

_CYCLE_FRESH = "2026-07-10T12:00:00+00:00"
_CYCLE_STALE = "2026-07-10T06:00:00+00:00"  # 6h older: outside the ±3h window


def _shape(provider_cycles=None, provider_values=None, provider_weights=None):
    raw = tuple(range(-25, 26))
    scale = 0.4 / statistics.pstdev(raw)
    members = tuple(10.5 + value * scale for value in raw)
    return mod._current_evidence_shape_from_values(
        snapshot_id=7,
        source_cycle_time=_CYCLE_FRESH,
        source_available_at="2026-07-10T20:00:00+00:00",
        members_c=members,
        provider_values_c=provider_values or {"a": 10.0, "b": 10.6, "c": 12.0},
        provider_weights=provider_weights or {"a": 0.4, "b": 0.4, "c": 0.2},
        center_c=10.5,
        provider_cycles=provider_cycles,
    )


def test_mixed_cycles_between_over_coherent_cohort_only() -> None:
    base = _shape(provider_cycles=None)
    cohort = _shape(provider_cycles={"a": _CYCLE_FRESH, "b": _CYCLE_FRESH, "c": _CYCLE_STALE})
    # The stale outlier "c" (12.0, far from center) leaves the BETWEEN term: cohort
    # between = renormalized {a: 0.5, b: 0.5} spread around the center.
    expected = math.sqrt(0.5 * (10.0 - 10.5) ** 2 + 0.5 * (10.6 - 10.5) ** 2)
    assert cohort.provider_between_sigma_c == pytest.approx(expected)
    assert cohort.provider_between_sigma_c < base.provider_between_sigma_c
    # Provenance stamped ONLY because the filter excluded someone.
    payload = cohort.as_payload()
    assert payload["between_cohort_models"] == ("a", "b")
    assert payload["between_cohort_excluded"] == ("c",)
    # Center inputs untouched: provider_count still counts every provider.
    assert cohort.provider_count == 3


def test_within_3h_cycles_are_coherent_no_filter() -> None:
    base = _shape(provider_cycles=None)
    near = _shape(provider_cycles={
        "a": _CYCLE_FRESH, "b": "2026-07-10T09:30:00+00:00", "c": _CYCLE_FRESH,
    })  # b is 2.5h behind: inside ±3h
    assert near.shape_hash == base.shape_hash
    assert near.provider_between_sigma_c == base.provider_between_sigma_c
    payload = near.as_payload()
    assert "between_cohort_models" not in payload
    assert "between_cohort_excluded" not in payload


def test_absent_and_unparseable_cycles_fail_open_byte_identical() -> None:
    base = _shape(provider_cycles=None)
    empty = _shape(provider_cycles={})
    garbage = _shape(provider_cycles={"a": "not-a-time", "b": "also-bad", "c": ""})
    partial_garbage = _shape(provider_cycles={"c": "not-a-time"})
    for other in (empty, garbage, partial_garbage):
        assert other.shape_hash == base.shape_hash
        assert other.provider_between_sigma_c == base.provider_between_sigma_c
        assert "between_cohort_models" not in other.as_payload()


def test_coherent_cohort_below_two_falls_back_to_all_providers() -> None:
    base = _shape(provider_cycles=None)
    # Only "a" is fresh; b and c are a full cycle behind -> coherent cohort of 1 -> ALL.
    lonely = _shape(provider_cycles={"a": _CYCLE_FRESH, "b": _CYCLE_STALE, "c": _CYCLE_STALE})
    assert lonely.shape_hash == base.shape_hash
    assert lonely.provider_between_sigma_c == base.provider_between_sigma_c
    assert "between_cohort_models" not in lonely.as_payload()


def test_missing_cycle_provider_is_included_in_cohort() -> None:
    # "b" has no cycle stamp: fail-open INCLUDED in the cohort; only stale "c" leaves.
    cohort = _shape(provider_cycles={"a": _CYCLE_FRESH, "c": _CYCLE_STALE})
    expected = math.sqrt(0.5 * (10.0 - 10.5) ** 2 + 0.5 * (10.6 - 10.5) ** 2)
    assert cohort.provider_between_sigma_c == pytest.approx(expected)
    assert cohort.as_payload()["between_cohort_models"] == ("a", "b")


def test_two_provider_valueerror_condition_identical_with_cycles() -> None:
    """The <2-weighted-providers guard must fire on the SAME condition regardless of
    provider_cycles — the cohort filter can never manufacture this error."""
    with pytest.raises(ValueError, match="at least two weighted providers"):
        _shape(provider_values={"a": 10.0}, provider_weights={"a": 1.0})
    with pytest.raises(ValueError, match="at least two weighted providers"):
        _shape(
            provider_values={"a": 10.0},
            provider_weights={"a": 1.0},
            provider_cycles={"a": _CYCLE_FRESH},
        )
    # And conversely: 2 weighted providers with wildly split cycles still build (cohort<2
    # falls back to all providers rather than raising).
    ok = _shape(
        provider_values={"a": 10.0, "b": 10.6},
        provider_weights={"a": 0.5, "b": 0.5},
        provider_cycles={"a": _CYCLE_FRESH, "b": _CYCLE_STALE},
    )
    assert ok.provider_count == 2
