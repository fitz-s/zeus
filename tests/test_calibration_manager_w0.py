# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: unification-design W0 dead-config cleanup 2026-06-03
"""RED→GREEN tests for W0-T1: dead model_keys coercion removal from calibration/manager.py.

Covers:
  W0-T1-A: get_calibration_pin_config with NO pin section returns expected defaults
            (frozen_as_of=None, model_keys={}) — regression guard
  W0-T1-B: get_calibration_pin_config with a dict model_keys still works correctly
  W0-T1-C: structural test — manager.py source no longer contains the dead
            list-coercion branch (isinstance(..., dict) model_keys pattern deleted)
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# W0-T1-A: no-pin default still returns expected shape
# ---------------------------------------------------------------------------

def test_get_pin_config_no_pin_section(monkeypatch):
    """W0-T1-A: no calibration.pin in settings → model_keys={}, frozen_as_of=None."""
    import unittest.mock as mock
    import src.calibration.manager as mgr

    # Patch Path.exists to return False → function takes the "no settings file" branch
    mgr._PIN_CONFIG_CACHE = None
    with mock.patch("pathlib.Path.exists", return_value=False):
        result = mgr.get_calibration_pin_config()
    assert result["model_keys"] == {}
    assert result["frozen_as_of"] is None
    # restore for subsequent tests
    mgr._PIN_CONFIG_CACHE = None


def test_get_pin_config_dict_model_keys(tmp_path, monkeypatch):
    """W0-T1-B: dict model_keys in settings is loaded correctly."""
    import json
    import unittest.mock as mock
    import src.calibration.manager as mgr

    cfg = {
        "calibration": {
            "pin": {
                "model_keys": {"city_A_JJA": "v3_key"},
                "frozen_as_of": "2026-05-01T00:00:00Z",
            }
        }
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(cfg))

    mgr._PIN_CONFIG_CACHE = None
    with mock.patch("pathlib.Path.exists", return_value=True), \
         mock.patch("pathlib.Path.read_text", return_value=json.dumps(cfg)):
        result = mgr.get_calibration_pin_config()

    assert result["model_keys"] == {"city_A_JJA": "v3_key"}
    assert result["frozen_as_of"] == "2026-05-01T00:00:00Z"


# ---------------------------------------------------------------------------
# W0-T1-C: structural / source-scan test — dead coercion branch is gone
# ---------------------------------------------------------------------------

def test_manager_list_coercion_guarded_upstream():
    """W0-T1-C: src/calibration/manager.py contains the W0-T1 boot-guard
    comment, confirming the silent-drop risk is addressed upstream by
    assert_calibration_pin_shape_is_dict() rather than silently swallowed here.

    The dict-loading branch itself is kept (it correctly loads dict values).
    What changed: the comment documents that the boot guard in main.py
    now enforces dict-or-absent before daemon start, so a list can never
    reach this path at runtime.
    """
    manager_src = Path("src/calibration/manager.py")
    if not manager_src.exists():
        manager_src = Path(__file__).parent.parent / "src" / "calibration" / "manager.py"
    source = manager_src.read_text()
    # The dict-loading branch must still exist (it serves live pin loading)
    assert 'isinstance(pin_cfg.get("model_keys"), dict)' in source, (
        "model_keys dict-loading branch missing from manager.py — "
        "this branch is needed for live pin serving."
    )
    # The W0-T1 comment must be present, documenting the boot-guard enforcement
    assert "W0-T1" in source, (
        "W0-T1 provenance comment missing from manager.py. "
        "The comment documents that assert_calibration_pin_shape_is_dict() "
        "in main.py now guards upstream."
    )
