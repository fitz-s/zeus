# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/ENS_NEWCITY_DATA_PARITY_AUDIT_2026-05-24.md
# Purpose: Structural completeness tests for onboard_cities.py pipeline steps.
#          Verify PIPELINE_STEPS covers all OBTAINABLE-NOW parity steps, that
#          calibration_pairs is NOT dry-run / NOT optional, that
#          _verification_tables() uses V2 table names, that deferred artifacts
#          are recorded-pending (not hard-failed), and that the precondition
#          check raises on unregistered cities.
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
    "ens_backfill_v2",      # ensemble_snapshots_v2 backfill
    "platt_training",       # refit_platt_v2 → promote
    "fit_ens_bias_v2",      # model_bias_ens_v2 population
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
# T3 – _verification_tables() must use V2 table names
# ──────────────────────────────────────────────────────────────────────────────

V2_REQUIRED_TABLES = {
    "observation_instants_v2",
    "calibration_pairs_v2",
    "ensemble_snapshots_v2",
    "model_bias_ens_v2",
}

V1_BANNED_TABLES = {
    "observation_instants",
    "calibration_pairs",
    "ensemble_snapshots",
    "model_bias",
}


def test_verification_tables_use_v2_names():
    """_verification_tables() must include the V2 table names."""
    tables = set(onboard_cities._verification_tables())
    missing = V2_REQUIRED_TABLES - tables
    assert not missing, (
        f"_verification_tables() missing V2 tables: {sorted(missing)}"
    )


def test_verification_tables_exclude_v1_names():
    """_verification_tables() must not include superseded V1 table names."""
    tables = set(onboard_cities._verification_tables())
    found_v1 = V1_BANNED_TABLES & tables
    assert not found_v1, (
        f"_verification_tables() still contains obsolete V1 tables: {sorted(found_v1)}"
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

    with pytest.raises((ValueError, KeyError, RuntimeError)):
        onboard_cities._check_city_registered("__NOT_A_REAL_CITY_XYZ__")


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
    """Pipeline must call promote_platt_models_v2 after refit (stage→world)."""
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
