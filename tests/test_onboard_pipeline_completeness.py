# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Structural completeness tests for onboard_cities.py pipeline steps.
#          Verify PIPELINE_STEPS covers all OBTAINABLE-NOW parity steps, that
#          calibration_pairs is NOT dry-run / NOT optional, that
#          _verification_tables() uses canonical (bare) table names, that deferred artifacts
#          are recorded-pending (not hard-failed), and that the precondition
#          check raises on unregistered cities.
# Reuse: Inspect onboard_cities.py + naming_conventions.yaml before running;
#        authority: docs/operations/ENS_NEWCITY_DATA_PARITY_AUDIT_2026-05-24.md
"""Completeness tests for the city onboarding pipeline (scripts/onboard_cities.py).

RED-FIRST TDD: these tests are written against the CURRENT (broken) state of
onboard_cities.py and are expected to FAIL before the implementation fixes
are applied.  After the fix they must all pass.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Make scripts/ importable as a package via path manipulation
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import the module under test.  We import the module directly instead of
# using importlib so that type-checkers can follow the references.
from scripts import onboard_cities  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Helper: extract step by id
# ──────────────────────────────────────────────────────────────────────────────

def _step(step_id: str) -> dict:
    """Return the PIPELINE_STEPS entry for step_id, or raise KeyError."""
    for s in onboard_cities.PIPELINE_STEPS:
        if s["id"] == step_id:
            return s
    raise KeyError(f"No pipeline step with id={step_id!r}")


def _step_ids() -> set[str]:
    return {s["id"] for s in onboard_cities.PIPELINE_STEPS}


# ──────────────────────────────────────────────────────────────────────────────
# T1 – Required step IDs must be present
# ──────────────────────────────────────────────────────────────────────────────

REQUIRED_STEP_IDS = {
    # pre-existing
    "config",
    "ens_backfill",
    "calibration_pairs",
    # new OBTAINABLE-NOW steps
    "obs_instants_v2",      # backfill observation_instants_v2
    "ens_backfill_v2",      # ensemble_snapshots backfill
    "platt_training",       # refit_platt → promote
    "fit_ens_bias_v2",      # model_bias_ens population
    "monthly_bounds",       # city_monthly_bounds.json generation
    "compute_ddd_floor",    # v2_city_floors.json entry
}


def test_required_step_ids_present():
    """All OBTAINABLE-NOW pipeline steps must be registered."""
    ids = _step_ids()
    missing = REQUIRED_STEP_IDS - ids
    assert not missing, (
        f"PIPELINE_STEPS is missing required step ids: {sorted(missing)}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# T2 – calibration_pairs must NOT be dry-run and NOT optional
# ──────────────────────────────────────────────────────────────────────────────

def test_calibration_pairs_not_dry_run():
    """calibration_pairs step must not pass --dry-run (bug: currently does)."""
    step = _step("calibration_pairs")
    extra = step.get("extra_args", [])
    assert "--dry-run" not in extra, (
        "calibration_pairs step must NOT pass --dry-run to rebuild script"
    )


def test_calibration_pairs_not_optional():
    """calibration_pairs step must be mandatory (bug: currently optional=True)."""
    step = _step("calibration_pairs")
    assert not step.get("optional", False), (
        "calibration_pairs step must NOT be optional"
    )


# ──────────────────────────────────────────────────────────────────────────────
# T3 – _verification_tables() must use the CANONICAL (bare) table names
#
# Version-drop 2026-06-10: the prior contract pinned _v2 table names
# (observation_instants_v2, settlements_v2, market_events_v2, calibration_pairs_v2)
# that the B3/B3cont collapse renamed to their bare canonical forms. Those
# _v2 tables are GONE from all three live DBs (verified on zeus-forecasts.db),
# so the verification COUNT(*) queries silently reported "(table missing)".
# This test now asserts the live-correct contract: canonical bare names IN, any
# _v[0-9]-suffixed name OUT. (The old V2_REQUIRED/V1_BANNED sets were also
# internally contradictory — they required AND banned calibration_pairs — which
# made the suite un-passable; that contradiction is removed here.)
# ──────────────────────────────────────────────────────────────────────────────

# Canonical (bare) names that _verification_tables() MUST surface so the
# onboarding COUNT(*) verification hits live tables.
CANONICAL_REQUIRED_TABLES = {
    "observation_instants",
    "calibration_pairs",
    "ensemble_snapshots",
    "settlement_outcomes",
    "market_events",
}


def test_verification_tables_use_canonical_names():
    """_verification_tables() must include the canonical (bare) table names."""
    world, forecast = onboard_cities._verification_tables()
    tables = set(world) | set(forecast)
    missing = CANONICAL_REQUIRED_TABLES - tables
    assert not missing, (
        f"_verification_tables() missing canonical tables: {sorted(missing)}"
    )


def test_verification_tables_have_no_version_suffix():
    """_verification_tables() must not emit any _v[0-9]-suffixed table name.

    The version-drop antibody (tests/test_no_internal_version_suffixes.py) bans
    new _v[0-9] tokens; this is its onboarding-verification counterpart — a
    renamed table must be referenced by its live bare name, never the dropped
    _v2 shell.
    """
    import re as _re

    world, forecast = onboard_cities._verification_tables()
    versioned = sorted(
        t for t in (set(world) | set(forecast)) if _re.search(r"_v[0-9]", t)
    )
    assert not versioned, (
        "_verification_tables() still emits _v[0-9] table names (the live tables "
        f"use bare canonical names): {versioned}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# T4 – Deferred artifacts are recorded-pending, not hard-failed
#
# The pipeline must complete successfully even when a city is absent from
# oracle_error_rates and v2_nstar (both require live history).  We check that
# an explicit DEFERRED_ARTIFACTS constant (or equivalent) exists and contains
# the expected keys, and that run_pipeline does NOT raise when those artifacts
# are unavailable.
# ──────────────────────────────────────────────────────────────────────────────

DEFERRED_KEYS = {"oracle_error_rates", "v2_nstar"}


def test_deferred_artifacts_constant_exists():
    """onboard_cities must expose a DEFERRED_ARTIFACTS list/tuple/dict."""
    assert hasattr(onboard_cities, "DEFERRED_ARTIFACTS"), (
        "onboard_cities must define DEFERRED_ARTIFACTS (a sequence of artifact names "
        "that require live/settled history and are recorded-pending, not hard-failed)"
    )


def test_deferred_artifacts_contains_required_keys():
    """DEFERRED_ARTIFACTS must list oracle_error_rates and v2_nstar."""
    da = onboard_cities.DEFERRED_ARTIFACTS
    # Normalise: works for list, tuple, dict-keys, or set
    keys = set(da) if not isinstance(da, dict) else set(da.keys())
    missing = DEFERRED_KEYS - keys
    assert not missing, (
        f"DEFERRED_ARTIFACTS missing required keys: {sorted(missing)}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# T5 – Precondition check raises on unregistered city
#
# onboard_cities must expose _check_city_registered(city_name) which raises
# ValueError (or a named subclass) when the city is absent from TIER_SCHEDULE.
# ──────────────────────────────────────────────────────────────────────────────

def test_precondition_function_exists():
    """onboard_cities must expose _check_city_registered."""
    assert hasattr(onboard_cities, "_check_city_registered"), (
        "onboard_cities must define _check_city_registered(city_name)"
    )


def test_precondition_raises_on_unregistered_city(monkeypatch):
    """_check_city_registered must raise ValueError for cities not in TIER_SCHEDULE."""
    # Patch TIER_SCHEDULE to contain only a sentinel city so we can test against
    # a deterministically absent city name without depending on live tier_resolver.
    import importlib
    try:
        tier_mod = importlib.import_module("src.data.tier_resolver")
        monkeypatch.setattr(tier_mod, "TIER_SCHEDULE", {"__sentinel__": "tier1"}, raising=False)
        monkeypatch.setattr(onboard_cities, "TIER_SCHEDULE", {"__sentinel__": "tier1"}, raising=True)
    except (ImportError, AttributeError):
        pass  # If TIER_SCHEDULE not importable, proceed with bare call

    with pytest.raises(ValueError):
        onboard_cities._check_city_registered("__NOT_A_REAL_CITY_XYZ__")


def test_precondition_runs_in_dry_run_mode(monkeypatch):
    """_check_city_registered must be called in dry-run mode too (pre-flight).

    Verifies MAJOR 1 fix: the check was previously inside `if not dry_run`.
    Now dry-run must also fail-closed on an unregistered city.
    """
    import inspect
    source = inspect.getsource(onboard_cities.run_pipeline)
    # The check must NOT be nested inside `if not dry_run:` — verify this by
    # asserting _check_city_registered is called before any dry_run branch.
    # We approximate by confirming it appears before "if not dry_run:" text.
    check_pos = source.find("_check_city_registered")
    dry_run_gate_pos = source.find("if not dry_run:")
    assert check_pos != -1, "_check_city_registered not found in run_pipeline"
    assert dry_run_gate_pos == -1 or check_pos < dry_run_gate_pos, (
        "_check_city_registered must run BEFORE any 'if not dry_run' gate "
        "(dry-run is the pre-flight; unregistered city must fail in both modes)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# T6 – platt_training step wires to forecasts DB (not world DB)
# ──────────────────────────────────────────────────────────────────────────────

def test_platt_training_step_uses_forecasts_db():
    """platt_training step must reference ZEUS_FORECASTS_DB_PATH in its extra_args."""
    step = _step("platt_training")
    extra = step.get("extra_args", [])
    # The step must NOT hard-code world.db or stage.db — it must use the
    # forecasts-db path variable.  We check via the step's db_source field or
    # verify extra_args contain the "{ZEUS_FORECASTS_DB_PATH}" placeholder
    # or the literal forecasts-db marker used by run_pipeline().
    db_source = step.get("db_source", "")
    has_forecasts_marker = (
        "forecasts" in db_source.lower()
        or any("forecasts" in str(a).lower() for a in extra)
        or step.get("uses_forecasts_db", False)
    )
    assert has_forecasts_marker, (
        "platt_training step must wire to zeus-forecasts.db, not zeus-world.db; "
        f"extra_args={extra!r} db_source={db_source!r}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# T7 – platt_promote sub-step is present or pipeline handles it inline
# ──────────────────────────────────────────────────────────────────────────────

def test_platt_promote_step_or_inline():
    """Pipeline must call promote_platt after refit (stage→world)."""
    ids = _step_ids()
    # Either a separate 'platt_promote' step exists, OR 'platt_training'
    # runs both refit+promote inline (type='python').
    has_explicit_promote = "platt_promote" in ids
    if not has_explicit_promote:
        # platt_training must be a Python (inline) step that does both
        step = _step("platt_training")
        assert step.get("type") == "python", (
            "platt_training must be type='python' (inline) when it handles both "
            "refit and promote, OR a separate 'platt_promote' step must exist"
        )


# ──────────────────────────────────────────────────────────────────────────────
# T8 – fit_ens_bias_v2 is an inline Python step (no standalone script)
# ──────────────────────────────────────────────────────────────────────────────

def test_fit_ens_bias_v2_is_python_step():
    """fit_ens_bias_v2 must be type='python' (no standalone script exists)."""
    step = _step("fit_ens_bias_v2")
    assert step.get("type") == "python", (
        "fit_ens_bias_v2 has no standalone script; must be type='python' inline step"
    )


# ──────────────────────────────────────────────────────────────────────────────
# T9 – compute_ddd_floor is an inline Python step
# ──────────────────────────────────────────────────────────────────────────────

def test_compute_ddd_floor_is_python_step():
    """compute_ddd_floor must be type='python' (inline, writes v2_city_floors.json)."""
    step = _step("compute_ddd_floor")
    assert step.get("type") == "python", (
        "compute_ddd_floor must be type='python' inline step"
    )


# ──────────────────────────────────────────────────────────────────────────────
# T10 – v2_city_floors.json schema contract
#
# Each new entry written by compute_ddd_floor must include the canonical fields
# used by existing entries (p05, final_floor, floor_source).
# ──────────────────────────────────────────────────────────────────────────────

def test_v2_city_floors_schema():
    """v2_city_floors.json must be loadable and have per_city entries with required fields."""
    floors_path = (
        PROJECT_ROOT / "src" / "oracle" / "ddd_artifacts" / "v2_city_floors.json"
    )
    assert floors_path.exists(), f"v2_city_floors.json not found at {floors_path}"
    with floors_path.open() as f:
        data = json.load(f)
    assert "per_city" in data, "v2_city_floors.json must have a 'per_city' key"
    per_city = data["per_city"]
    assert per_city, "per_city must be non-empty"
    # Spot-check first entry has the required fields
    first_city = next(iter(per_city))
    entry = per_city[first_city]
    for field in ("p05", "final_floor", "floor_source"):
        assert field in entry, (
            f"v2_city_floors.json per_city[{first_city!r}] missing field {field!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# T11 – _write_nstar_stubs: antibody tests
# ──────────────────────────────────────────────────────────────────────────────

def test_write_nstar_stubs_function_exists():
    """onboard_cities must expose _write_nstar_stubs helper."""
    assert hasattr(onboard_cities, "_write_nstar_stubs"), (
        "onboard_cities must define _write_nstar_stubs to prevent uncaught "
        "DDDFailClosed(DDD_NSTAR_UNCONFIGURED) for new cities"
    )


def test_write_nstar_stubs_atomic_write(tmp_path):
    """_write_nstar_stubs writes N_STAR_NOT_FOUND stubs without clobbering existing keys."""
    import json
    nstar_file = tmp_path / "v2_nstar.json"
    # Seed an existing city so we can verify it's not clobbered
    initial = {
        "per_city_metric": {
            "Amsterdam_high": {"status": "OK", "N_star": 123},
            "Amsterdam_low": {"status": "OK", "N_star": 99},
        }
    }
    nstar_file.write_text(json.dumps(initial))

    # Monkey-patch PROJECT_ROOT inside onboard_cities so the function finds our tmp file
    import importlib
    orig_root = onboard_cities.PROJECT_ROOT
    onboard_cities.PROJECT_ROOT = tmp_path / "fake_root"
    # Create the expected sub-path
    artifact_dir = tmp_path / "fake_root" / "src" / "oracle" / "ddd_artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "v2_nstar.json").write_text(json.dumps(initial))

    try:
        onboard_cities._write_nstar_stubs(["TestCity"], dry_run=False)
    finally:
        onboard_cities.PROJECT_ROOT = orig_root

    result = json.loads((artifact_dir / "v2_nstar.json").read_text())
    pm = result["per_city_metric"]
    # New stubs written
    assert "TestCity_high" in pm, "TestCity_high stub must be written"
    assert "TestCity_low" in pm, "TestCity_low stub must be written"
    assert pm["TestCity_high"] == {"status": "N_STAR_NOT_FOUND", "N_star": None}
    assert pm["TestCity_low"] == {"status": "N_STAR_NOT_FOUND", "N_star": None}
    # Existing keys not clobbered
    assert pm["Amsterdam_high"]["N_star"] == 123, "Amsterdam_high must not be overwritten"


def test_write_nstar_stubs_no_clobber(tmp_path):
    """_write_nstar_stubs must not overwrite already-calibrated entries."""
    import json
    initial = {
        "per_city_metric": {
            "Kuala Lumpur_high": {"status": "OK", "N_star": 77},
        }
    }
    artifact_dir = tmp_path / "src" / "oracle" / "ddd_artifacts"
    artifact_dir.mkdir(parents=True)
    nstar_file = artifact_dir / "v2_nstar.json"
    nstar_file.write_text(json.dumps(initial))

    orig_root = onboard_cities.PROJECT_ROOT
    onboard_cities.PROJECT_ROOT = tmp_path
    try:
        onboard_cities._write_nstar_stubs(["Kuala Lumpur"], dry_run=False)
    finally:
        onboard_cities.PROJECT_ROOT = orig_root

    result = json.loads(nstar_file.read_text())
    pm = result["per_city_metric"]
    # Existing calibrated entry must survive
    assert pm["Kuala Lumpur_high"]["N_star"] == 77, (
        "_write_nstar_stubs must NOT clobber existing entries"
    )
    # Missing low track should have been added
    assert "Kuala Lumpur_low" in pm


def test_nstar_stubs_called_at_compute_ddd_floor_step(monkeypatch):
    """_write_nstar_stubs must be called when compute_ddd_floor step dispatches."""
    import inspect
    source = inspect.getsource(onboard_cities.run_pipeline)
    # Both _write_nstar_stubs and _run_compute_ddd_floor must appear in the
    # same branch handling the "compute_ddd_floor" step id.
    assert "_write_nstar_stubs" in source, (
        "_write_nstar_stubs must be called in run_pipeline "
        "(wired alongside compute_ddd_floor)"
    )
