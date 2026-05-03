# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 2 entry_forecast config contract.
"""Strict entry_forecast config contract tests.

PLAN_v4 separates live forecast-entry authority from legacy ensemble.primary.
The default rollout is blocked, so adding this config cannot unlock live money.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.config import (
    EntryForecastCalibrationPolicyId,
    EntryForecastRolloutMode,
    EntryForecastSourceTransport,
    Settings,
    entry_forecast_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _settings_data() -> dict:
    return json.loads((PROJECT_ROOT / "config/settings.json").read_text())


def test_entry_forecast_config_is_required(tmp_path) -> None:
    data = _settings_data()
    data.pop("entry_forecast")
    path = tmp_path / "settings-missing-entry-forecast.json"
    path.write_text(json.dumps(data))

    with pytest.raises(KeyError, match="entry_forecast"):
        Settings(path=path)


def test_entry_forecast_config_loads_strict_field_types() -> None:
    """Strict-load contract: every non-rollout_mode field must have the
    expected enum/string/int/bool type and the documented value.

    The on-disk ``rollout_mode`` is operator-controlled and tracked by the
    separate canary ``test_settings_json_rollout_mode_matches_plan_declaration``.
    """

    cfg = entry_forecast_config()

    assert cfg.source_id == "ecmwf_open_data"
    assert cfg.source_transport is EntryForecastSourceTransport.ENSEMBLE_SNAPSHOTS_V2_DB_READER
    assert cfg.authority_family == "ecmwf_ifs_ens"
    assert cfg.high_track == "mx2t6_high_full_horizon"
    assert cfg.low_track == "mn2t6_low_full_horizon"
    assert cfg.target_horizon_days == 10
    assert cfg.warm_horizon_days == 10
    assert cfg.source_cycle_policy == "latest_complete_full_horizon"
    assert cfg.allow_short_horizon_06_18 is False
    assert isinstance(cfg.rollout_mode, EntryForecastRolloutMode)
    assert cfg.calibration_policy_id is (
        EntryForecastCalibrationPolicyId.ECMWF_OPEN_DATA_USES_TIGGE_LOCALDAY_CAL_V1
    )
    assert cfg.require_active_market_future_coverage is True


def test_entry_forecast_blocked_override_constructs_cleanly() -> None:
    """``replace(cfg, rollout_mode=BLOCKED)`` must round-trip every field.

    Test consumers in the BLOCKED-branch suites rely on this idiom; this
    test pins the support so that an accidental schema change to
    ``EntryForecastConfig`` cannot silently break those overrides.
    """

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.BLOCKED)

    assert cfg.rollout_mode is EntryForecastRolloutMode.BLOCKED
    assert cfg.source_id == "ecmwf_open_data"
    assert cfg.high_track == "mx2t6_high_full_horizon"
    assert cfg.low_track == "mn2t6_low_full_horizon"


def test_settings_json_rollout_mode_matches_plan_declaration() -> None:
    """Cross-file antibody: settings.json rollout_mode must match the
    single-source-of-truth declaration in CURRENT_ROLLOUT_MODE.md.

    This replaces the retired ``test_entry_forecast_config_loads_blocked_default``
    canary. The retired test asserted "the on-disk default is BLOCKED";
    after operator authorized a live unblock, that fact no longer holds.
    The new antibody asserts a stronger property: regardless of the
    chosen value, the on-disk JSON and the operator-authored declaration
    must agree. A drift between the two is the failure mode this antibody
    catches (e.g. the operator flips JSON without updating the doc, or
    vice versa).
    """

    declaration = (
        PROJECT_ROOT
        / "docs/operations/task_2026-05-02_live_entry_data_contract/CURRENT_ROLLOUT_MODE.md"
    ).read_text()

    declared_lines = [
        line.strip()
        for line in declaration.splitlines()
        if line.strip().startswith("rollout_mode:")
    ]
    assert declared_lines, "CURRENT_ROLLOUT_MODE.md must declare rollout_mode"
    declared_value = declared_lines[0].split(":", 1)[1].strip()

    cfg = entry_forecast_config()
    assert cfg.rollout_mode.value == declared_value, (
        f"settings.json rollout_mode={cfg.rollout_mode.value!r} disagrees "
        f"with CURRENT_ROLLOUT_MODE.md declaration={declared_value!r}. "
        "Update both files in the same commit."
    )


def test_entry_forecast_config_is_separate_from_ensemble_primary() -> None:
    settings_obj = Settings()
    cfg = entry_forecast_config(settings_obj)

    assert settings_obj["ensemble"]["primary"] == "ecmwf_ifs025"
    assert cfg.source_id == "ecmwf_open_data"
    assert cfg.source_id != settings_obj["ensemble"]["primary"]


def test_entry_forecast_invalid_rollout_mode_fails_closed(tmp_path) -> None:
    data = _settings_data()
    data["entry_forecast"]["rollout_mode"] = "paper"
    path = tmp_path / "settings-invalid-rollout.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="paper"):
        entry_forecast_config(Settings(path=path))


def test_entry_forecast_boolean_fields_are_strict(tmp_path) -> None:
    data = _settings_data()
    data["entry_forecast"]["allow_short_horizon_06_18"] = "false"
    path = tmp_path / "settings-string-bool.json"
    path.write_text(json.dumps(data))

    with pytest.raises(TypeError, match="allow_short_horizon_06_18"):
        entry_forecast_config(Settings(path=path))


def test_entry_forecast_target_horizon_is_bounded(tmp_path) -> None:
    data = _settings_data()
    data["entry_forecast"]["target_horizon_days"] = 11
    data["entry_forecast"]["warm_horizon_days"] = 11
    path = tmp_path / "settings-horizon-out-of-range.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="target_horizon_days"):
        entry_forecast_config(Settings(path=path))
