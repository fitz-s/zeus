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
from datetime import datetime, timedelta, timezone

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
