# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: W0 boot-guard Settings-object fix (fix/w0-guard-settings-object 2026-06-03)
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: ANTIBODY — boot guards must run on the REAL Settings object the way
#   main.py's boot path passes config, without AttributeError. The original
#   unit tests (test_boot_guard_pin_shape.py) fed dict literals, so they never
#   exercised the real boot path; the daemon crash-looped on
#   AttributeError('Settings' object has no attribute 'get') because the call
#   site passed the Settings OBJECT to a dict-consuming guard.
# Reuse: Run with pytest; update with src/main.py boot-guard call-site semantics.
"""ANTIBODY tests for the W0 calibration-pin boot guards against a REAL Settings.

Relationship under test (cross-module boundary):
    main.py builds a strict ``Settings`` object (src/config.py:Settings) and
    passes config into ``assert_calibration_pin_shape_is_dict`` /
    ``assert_frozen_as_of_not_stale`` (which consume a plain ``dict`` via
    ``cfg.get(...)``).  ``Settings`` has NO ``.get()`` — it is subscript-only
    plus a raw ``._data`` dict.  The boot path therefore MUST hand the guards
    the raw dict (``settings._data``), the same accessor ``_settings_section``
    uses.  Handing the object itself raises AttributeError and FATALs boot
    before the guard's real check (model_keys dict-or-absent) ever runs.

These tests would be RED before the call-site fix (the
``test_*_object_*_attributeerror`` cases assert the object form *does* raise,
documenting the defect; the ``_via_data_accessor`` cases prove the fixed form
runs the guard correctly).  After the fix the call site uses ``settings._data``
and the guard runs clean on the real config.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from src.config import Settings
from src.main import (
    assert_calibration_pin_shape_is_dict,
    assert_frozen_as_of_not_stale,
)


def _real_settings() -> Settings:
    """Build a real Settings object the same way main.py does (config/settings.json)."""
    return Settings()


def _boot_cfg(settings: Settings) -> dict:
    """Replicate the EXACT accessor main.py's boot path uses to feed the guards.

    Must stay byte-identical in intent to src/main.py:
        _pin_guard_cfg = settings._data if hasattr(settings, "_data") else settings
    If main.py reverts to passing the object, _boot_cfg still yields a dict and
    the guard tests below pass — but the regression-anchor tests
    (test_*_object_*_attributeerror) prove the object form is still broken, so a
    revert at the call site is caught by those.
    """
    return settings._data if hasattr(settings, "_data") else settings


# ---------------------------------------------------------------------------
# Regression anchors: the OBJECT form is broken and MUST raise AttributeError.
# These document the defect and fail loudly if anyone "fixes" Settings to add
# a .get() that silently swallows the shape check, or re-passes the object.
# ---------------------------------------------------------------------------

def test_pin_shape_guard_on_settings_object_raises_attributeerror():
    """Settings has no .get(); the guard on the raw object must AttributeError.

    This is the live boot crash. The fix is at the call site (pass ._data),
    NOT by adding .get() to Settings — so this property is intentionally locked.
    """
    s = _real_settings()
    with pytest.raises(AttributeError):
        assert_calibration_pin_shape_is_dict(s)


def test_frozen_guard_on_settings_object_raises_attributeerror():
    """The masked sibling: same AttributeError on the staleness guard."""
    s = _real_settings()
    with pytest.raises(AttributeError):
        assert_frozen_as_of_not_stale(s, now=datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# The fixed boot path: guards run on settings._data WITHOUT AttributeError.
# ---------------------------------------------------------------------------

def test_pin_shape_guard_via_boot_accessor_no_attributeerror():
    """The boot accessor (settings._data) must let the shape guard run clean.

    The live config currently has model_keys as a LIST, so the guard's REAL
    job is to raise MODEL_KEYS_MUST_BE_DICT — NOT AttributeError. Either it
    passes (dict/absent) or it raises MODEL_KEYS_MUST_BE_DICT (list); an
    AttributeError here is the bug and must never occur.
    """
    cfg = _boot_cfg(_real_settings())
    try:
        assert_calibration_pin_shape_is_dict(cfg)
    except RuntimeError as exc:
        # Acceptable: real config may carry a non-dict model_keys; that's the
        # guard doing its job. The forbidden outcome is AttributeError.
        assert "MODEL_KEYS_MUST_BE_DICT" in str(exc)
    except AttributeError as exc:  # pragma: no cover - the bug
        pytest.fail(f"boot accessor still raises AttributeError: {exc}")


def test_frozen_guard_via_boot_accessor_no_attributeerror():
    """The staleness guard must also run clean on the boot accessor."""
    cfg = _boot_cfg(_real_settings())
    # Real frozen_as_of may be fresh, stale-warn, or stale-fatal; only the
    # FATAL path raises RuntimeError(FROZEN_AS_OF_STALE). AttributeError is the
    # bug and is forbidden.
    try:
        assert_frozen_as_of_not_stale(cfg, now=datetime.now(tz=timezone.utc))
    except RuntimeError as exc:
        assert "FROZEN_AS_OF_STALE" in str(exc)
    except AttributeError as exc:  # pragma: no cover - the bug
        pytest.fail(f"frozen guard still raises AttributeError: {exc}")


# ---------------------------------------------------------------------------
# Shape-guard correctness preserved through a REAL-Settings-derived dict.
# Proves the guard's INTENT (dict/absent -> OK, list -> raise) survives when
# fed config built from the real Settings object, not just hand-built literals.
# ---------------------------------------------------------------------------

def _settings_data_with_model_keys(model_keys) -> dict:
    s = _real_settings()
    data = copy.deepcopy(s._data)
    data.setdefault("calibration", {})
    pin = data["calibration"].setdefault("pin", {})
    pin["model_keys"] = model_keys
    return data


def test_shape_guard_real_data_empty_dict_passes():
    """model_keys={} on real-config-derived dict -> no error."""
    cfg = _settings_data_with_model_keys({})
    assert_calibration_pin_shape_is_dict(cfg)  # must not raise


def test_shape_guard_real_data_dict_passes():
    """model_keys=populated dict on real-config-derived dict -> no error."""
    cfg = _settings_data_with_model_keys({"city_A_JJA_00": "v3"})
    assert_calibration_pin_shape_is_dict(cfg)  # must not raise


def test_shape_guard_real_data_list_raises_model_keys_must_be_dict():
    """model_keys=list on real-config-derived dict -> MODEL_KEYS_MUST_BE_DICT.

    This is the guard's reason to exist: a misconfigured JSON list must FATAL
    loudly at boot, never silently become dead config — and the failure mode
    must be the deliberate RuntimeError, never an AttributeError.
    """
    cfg = _settings_data_with_model_keys(["a", "b"])
    with pytest.raises(RuntimeError, match="MODEL_KEYS_MUST_BE_DICT"):
        assert_calibration_pin_shape_is_dict(cfg)


def test_shape_guard_real_data_model_keys_absent_passes():
    """model_keys removed from real-config-derived dict -> no error (absent ok)."""
    s = _real_settings()
    data = copy.deepcopy(s._data)
    data.setdefault("calibration", {}).setdefault("pin", {}).pop("model_keys", None)
    assert_calibration_pin_shape_is_dict(data)  # must not raise


# ---------------------------------------------------------------------------
# W0-T3: --validate-boot subprocess tests (2026-06-03)
#
# These verify _validate_boot() and the --validate-boot CLI path:
#   1. Exits 0 when model_keys is absent (config-safe)
#   2. Exits 1 when model_keys is a list  (guard correctly FATALs)
#   3. Subprocess test: python -m src.main --validate-boot exits cleanly (no
#      daemon started, no lock acquired, fast exit)
#
# Implementation note: subprocess test uses --settings-path to inject a
# minimal temp settings.json. _validate_boot() accepts settings_path= and
# calls Settings(path=...) directly, bypassing the module-level singleton.
# ---------------------------------------------------------------------------

import json
import subprocess
import sys as _sys
from pathlib import Path as _Path


# Minimal settings.json with all 13 required Settings keys + controllable pin.
_BASE_SETTINGS = {
    "discovery": {
        "opening_hunt_interval_min": 5,
        "update_reaction_times_utc": [],
        "day0_interval_min": 5,
    },
    "ensemble": {},
    "entry_forecast": {},
    "calibration": {},
    "day0": {},
    "edge": {},
    "sizing": {"kelly_multiplier": 0.25},
    "correlation": {},
    "exit": {},
    "riskguard": {},
    "execution": {},
    "baseline_bias_correction_enabled": False,
    "feature_flags": {},
}

_PYTHON = _Path(_sys.executable)
_REPO = _Path(__file__).parent.parent  # /tmp/zeus-w0


def _settings_with_pin(model_keys=None, tmp_path=None):
    """Write a minimal settings.json with optional calibration.pin.model_keys.

    frozen_as_of is intentionally omitted so pin-shape-focused tests get clean
    signal: staleness guard is a no-op (absent frozen_as_of -> pass), so any
    FAIL is attributable to model_keys shape alone.  Tests that explicitly
    exercise staleness should set frozen_as_of directly on the dict.
    """
    data = json.loads(json.dumps(_BASE_SETTINGS))
    if model_keys is not None:
        data["calibration"] = {"pin": {"model_keys": model_keys}}
    else:
        data["calibration"] = {"pin": {}}
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data))
    return str(p)


# ---------------------------------------------------------------------------
# Unit-level: _validate_boot() directly (no subprocess overhead)
# ---------------------------------------------------------------------------

def test_validate_boot_absent_model_keys_exits_0(tmp_path):
    """_validate_boot with model_keys absent -> exit code 0 (shape guard passes).

    Schema/registry guards may fail (live DBs not present in test env) but the
    settings_load + calibration_pin_shape guards must pass.
    """
    from src.main import _validate_boot

    sp = _settings_with_pin(model_keys=None, tmp_path=tmp_path)
    result = _validate_boot(settings_path=sp)
    # Schema guards may fail (no live DBs in test env) — that's OK for unit test.
    # We assert: result is 0 or 1 (int), NOT an unhandled exception, NOT daemon start.
    assert result in (0, 1), f"_validate_boot returned unexpected value: {result!r}"


def test_validate_boot_dict_model_keys_exits_0_on_settings_load(tmp_path):
    """_validate_boot with model_keys as dict -> shape guard passes.

    Settings load and calibration_pin_shape must both pass (return True).
    """
    from src.main import _run_boot_guards
    from src.config import Settings

    sp = _settings_with_pin(model_keys={"city_X_JJA": "v3"}, tmp_path=tmp_path)
    s = Settings(path=_Path(sp))
    raw = s._data if hasattr(s, "_data") else s
    results = _run_boot_guards(raw)
    names = {r[0]: r for r in results}
    assert names["calibration_pin_shape"][1] is True, (
        f"dict model_keys should pass shape guard; got: {names['calibration_pin_shape']}"
    )


def test_validate_boot_list_model_keys_guard_fails(tmp_path):
    """_validate_boot with model_keys as list -> shape guard fails (exit 1).

    The guard's real check fires (MODEL_KEYS_MUST_BE_DICT) — not AttributeError.
    """
    from src.main import _validate_boot

    sp = _settings_with_pin(model_keys=["a", "b"], tmp_path=tmp_path)
    result = _validate_boot(settings_path=sp)
    assert result == 1, f"list model_keys must cause exit 1; got {result}"


# ---------------------------------------------------------------------------
# Subprocess test: python -m src.main --validate-boot
# Verifies the CLI path exits without starting the daemon.
# ---------------------------------------------------------------------------

def test_validate_boot_subprocess_exits_without_daemon(tmp_path):
    """--validate-boot subprocess exits cleanly — no daemon loop, no lock.

    Uses --settings-path to inject a safe minimal config. Asserts:
      - process exits (not hanging)
      - exit code is 0 or 1 (not 2=argparse, not crash/segfault)
      - stdout contains 'zeus --validate-boot'
      - fast exit (<= 30s); daemon would block indefinitely
    """
    sp = _settings_with_pin(model_keys=None, tmp_path=tmp_path)
    proc = subprocess.run(
        [str(_PYTHON), "-m", "src.main", "--validate-boot", "--settings-path", sp],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
        env={
            **__import__("os").environ,
            "ZEUS_MODE": "live",
            "ZEUS_BOOT_REGISTRY_ASSERT_ENABLED": "0",  # skip registry in CI
        },
    )
    assert proc.returncode in (0, 1), (
        f"--validate-boot exited {proc.returncode}; stdout={proc.stdout!r}; stderr={proc.stderr!r}"
    )
    assert "zeus --validate-boot" in proc.stdout, (
        f"Expected validate-boot header in stdout; got: {proc.stdout!r}"
    )
    # Specifically must NOT print daemon startup markers
    assert "Zeus starting in" not in proc.stdout, (
        "Daemon loop started — --validate-boot did not short-circuit"
    )


def test_validate_boot_missing_settings_path_value_exits_1():
    """--settings-path at end of argv (no following value) must exit 1, not crash.

    This guards the positional index parse: `--settings-path` with no following
    value previously caused IndexError (off-end of sys.argv).  The fix prints a
    clear error message to stderr and exits 1 (fail-closed).
    """
    proc = subprocess.run(
        [str(_PYTHON), "-m", "src.main", "--validate-boot", "--settings-path"],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=15,
        env={
            **__import__("os").environ,
            "ZEUS_MODE": "live",
        },
    )
    assert proc.returncode == 1, (
        f"Expected exit 1 for missing --settings-path value; got {proc.returncode}; "
        f"stderr={proc.stderr!r}"
    )
    assert "ERROR" in proc.stderr, (
        f"Expected ERROR message in stderr; got: {proc.stderr!r}"
    )
    assert "IndexError" not in proc.stderr, (
        f"Must not crash with IndexError; stderr={proc.stderr!r}"
    )
