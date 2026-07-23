# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: RED->GREEN tests for W0 boot guards (calibration pin shape + frozen_as_of staleness).
# Reuse: Run with pytest; update with src/main.py boot-guard semantics.
# Authority basis: unification-design W0 boot-guards 2026-06-03
# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: unification-design W0 boot-guards 2026-06-03
"""RED→GREEN tests for boot-guard calibration pin-shape + staleness checks.

Covers:
  W0-T2-A: assert_calibration_pin_shape_is_dict raises when model_keys is a list
  W0-T2-B: assert_calibration_pin_shape_is_dict passes when model_keys is a dict
  W0-T2-C: assert_calibration_pin_shape_is_dict passes when model_keys is absent
  W0-T2-D: assert_frozen_as_of_not_stale WARN-only at 15 days, FATAL at 25 days
  W0-T2-E: assert_frozen_as_of_not_stale FATAL is skipped when ZEUS_FREEZE_GUARD_DISABLE=1
  W0-T2-F: assert_frozen_as_of_not_stale passes for a fresh frozen_as_of (3 days ago)
  W0-T2-G: assert_frozen_as_of_not_stale is a no-op when frozen_as_of is absent
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import pytest


def _cfg(model_keys=None, frozen_as_of=None):
    """Build a minimal settings cfg dict for pin testing."""
    pin = {}
    if model_keys is not None:
        pin["model_keys"] = model_keys
    if frozen_as_of is not None:
        pin["frozen_as_of"] = frozen_as_of
    if not pin:
        return {}
    return {"calibration": {"pin": pin}}


def _replacement_qkernel_cfg(*, frozen_as_of: str) -> dict:
    cfg = _cfg(frozen_as_of=frozen_as_of)
    cfg["edli"] = {
        "replacement_0_1_bayes_precision_fusion_enabled": True,
        "replacement_0_1_fused_q_shape_enabled": True,
    }
    return cfg


# ---------------------------------------------------------------------------
# W0-T2-A/B/C — assert_calibration_pin_shape_is_dict
# ---------------------------------------------------------------------------

def test_pin_shape_list_raises():
    """W0-T2-A: model_keys as list → MODEL_KEYS_MUST_BE_DICT RuntimeError."""
    from src.main import assert_calibration_pin_shape_is_dict  # noqa: F401 (will fail RED)
    with pytest.raises(RuntimeError, match="MODEL_KEYS_MUST_BE_DICT"):
        assert_calibration_pin_shape_is_dict(_cfg(model_keys=["a", "b"]))


def test_pin_shape_dict_passes():
    """W0-T2-B: model_keys as dict → no error."""
    from src.main import assert_calibration_pin_shape_is_dict
    assert_calibration_pin_shape_is_dict(_cfg(model_keys={"city_A_JJA": "v3"}))


def test_pin_shape_absent_passes():
    """W0-T2-C: model_keys absent → no error (pin section absent entirely)."""
    from src.main import assert_calibration_pin_shape_is_dict
    assert_calibration_pin_shape_is_dict({})


def test_pin_shape_empty_dict_passes():
    """W0-T2-C2: empty dict is valid."""
    from src.main import assert_calibration_pin_shape_is_dict
    assert_calibration_pin_shape_is_dict(_cfg(model_keys={}))


# ---------------------------------------------------------------------------
# W0-T2-D/E/F/G — assert_frozen_as_of_not_stale
# ---------------------------------------------------------------------------

def _iso(days_ago: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_frozen_stale_25_days_raises():
    """W0-T2-D: frozen_as_of 25 days ago still kills legacy Platt-live boot."""
    from src.main import assert_frozen_as_of_not_stale
    now = datetime.now(tz=timezone.utc)
    frozen = (now - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = _cfg(frozen_as_of=frozen)
    with pytest.raises(RuntimeError, match="FROZEN_AS_OF_STALE"):
        assert_frozen_as_of_not_stale(cfg, now=now)


def test_frozen_stale_25_days_nonfatal_for_replacement_qkernel_live(caplog):
    """The live replacement qkernel path is not blocked by stale legacy Platt pin."""
    from src.main import assert_frozen_as_of_not_stale

    now = datetime.now(tz=timezone.utc)
    frozen = (now - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = _replacement_qkernel_cfg(frozen_as_of=frozen)
    with caplog.at_level(logging.WARNING):
        assert_frozen_as_of_not_stale(cfg, now=now)
    assert any("replacement_0_1/qkernel" in r.message for r in caplog.records)


def test_frozen_stale_15_days_warns_not_raises(caplog):
    """W0-T2-D: frozen_as_of 15 days ago → WARNING logged, no raise."""
    from src.main import assert_frozen_as_of_not_stale
    now = datetime.now(tz=timezone.utc)
    frozen = (now - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = _cfg(frozen_as_of=frozen)
    with caplog.at_level(logging.WARNING):
        assert_frozen_as_of_not_stale(cfg, now=now)  # must not raise
    assert any("FROZEN_AS_OF" in r.message for r in caplog.records), \
        "Expected a WARNING log containing FROZEN_AS_OF"


def test_frozen_stale_fatal_disabled_by_env(monkeypatch):
    """W0-T2-E: ZEUS_FREEZE_GUARD_DISABLE=1 → FATAL is skipped even at 25 days."""
    from src.main import assert_frozen_as_of_not_stale
    monkeypatch.setenv("ZEUS_FREEZE_GUARD_DISABLE", "1")
    now = datetime.now(tz=timezone.utc)
    frozen = (now - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = _cfg(frozen_as_of=frozen)
    assert_frozen_as_of_not_stale(cfg, now=now)  # must not raise


def test_frozen_fresh_passes():
    """W0-T2-F: frozen_as_of 3 days ago → no warn, no raise."""
    from src.main import assert_frozen_as_of_not_stale
    now = datetime.now(tz=timezone.utc)
    frozen = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = _cfg(frozen_as_of=frozen)
    assert_frozen_as_of_not_stale(cfg, now=now)


def test_frozen_absent_noop():
    """W0-T2-G: frozen_as_of absent → no-op."""
    from src.main import assert_frozen_as_of_not_stale
    now = datetime.now(tz=timezone.utc)
    assert_frozen_as_of_not_stale({}, now=now)
    assert_frozen_as_of_not_stale({"calibration": {}}, now=now)
    assert_frozen_as_of_not_stale({"calibration": {"pin": {}}}, now=now)
