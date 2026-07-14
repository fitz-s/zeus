# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect replacement forecast live config switch planning.
# Reuse: Run before changing replacement forecast runtime flag wiring.
# Authority basis: Operator-directed live-only switch for Open-Meteo ECMWF IFS 9km plus Bayes fusion.
"""Replacement forecast config switch tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.data.replacement_forecast_config_switch import (
    TARGET_LIVE_FLAGS,
    TARGET_LIVE_MATERIALIZATION_CONFIG,
    apply_replacement_forecast_config_switch,
    apply_replacement_forecast_live_config_switch,
    build_replacement_forecast_config_switch_plan,
    build_replacement_forecast_live_config_switch_plan,
)
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    LIVE_FLAG,
    LIVE_STATUS,
    ReplacementForecastCapitalObjectiveEvidence,
    ReplacementForecastPromotionEvidence,
    resolve_replacement_forecast_runtime_policy,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _settings(flags: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "feature_flags": {
            LIVE_FLAG: False,
            KELLY_INCREASE_FLAG: False,
            DIRECTION_FLIP_FLAG: False,
            **(flags or {}),
        }
    }


def _passing_promotion_evidence() -> ReplacementForecastPromotionEvidence:
    return ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=1.0,
        q_lcb_coverage=0.95,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        nested_holdout_brier=0.20,
        nested_holdout_log_loss=0.50,
        nested_selected_anchor_weight=0.80,
        nested_selected_anchor_sigma_c=3.00,
        nested_guardrail_bucket_count=1,
        nested_guardrail_bucket_min_rows=20,
        product_specific_refit_passed=True,
    )


def _passing_capital_objective_evidence() -> ReplacementForecastCapitalObjectiveEvidence:
    return ReplacementForecastCapitalObjectiveEvidence(
        selected_label="openmeteo_ecmwf_ifs9_bayes_fusion",
        replay_status="EMPIRICAL_WINNER",
        after_cost_pnl=97.65,
        source_availability_observed=True,
        source_availability_violations=0,
        anti_lookahead_violations=0,
        same_clob_replay_passed=True,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        product_specific_refit_passed=True,
    )


def _weak_promotion_evidence() -> ReplacementForecastPromotionEvidence:
    return ReplacementForecastPromotionEvidence(
        official_days=3,
        official_rows=28,
        after_cost_pnl=-1.0,
        q_lcb_coverage=0.95,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=28,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        nested_holdout_brier=0.20,
        nested_holdout_log_loss=0.50,
        nested_selected_anchor_weight=0.80,
        nested_selected_anchor_sigma_c=3.00,
        nested_guardrail_bucket_count=1,
        nested_guardrail_bucket_min_rows=20,
        product_specific_refit_passed=True,
    )


def test_config_switch_plan_targets_live_flags() -> None:
    plan = build_replacement_forecast_config_switch_plan(_settings())

    assert plan.ok is True
    assert TARGET_LIVE_MATERIALIZATION_CONFIG["materialization_interval_min"] == 1
    assert plan.status == "READY"
    assert plan.policy_status_after == LIVE_STATUS
    assert dict(plan.target_flags) == TARGET_LIVE_FLAGS
    assert dict(plan.target_flags)[LIVE_FLAG] is True
    assert dict(plan.target_flags)[KELLY_INCREASE_FLAG] is True
    assert dict(plan.target_flags)[DIRECTION_FLIP_FLAG] is True
    assert dict(plan.target_materialization_config) == TARGET_LIVE_MATERIALIZATION_CONFIG
    assert {item["path"] for item in plan.json_patch if str(item["path"]).startswith("/feature_flags/")} == {
        f"/feature_flags/{LIVE_FLAG}",
        f"/feature_flags/{KELLY_INCREASE_FLAG}",
        f"/feature_flags/{DIRECTION_FLIP_FLAG}",
    }
    assert "/replacement_forecast_live/request_dir" in {item["path"] for item in plan.json_patch}


def test_config_switch_application_persists_live_runtime_policy(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(_settings()), encoding="utf-8")

    plan = apply_replacement_forecast_config_switch(settings_path)
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    policy = resolve_replacement_forecast_runtime_policy(payload["feature_flags"])

    assert plan.ok is True
    assert payload["feature_flags"][LIVE_FLAG] is True
    assert payload["feature_flags"][KELLY_INCREASE_FLAG] is True
    assert payload["feature_flags"][DIRECTION_FLIP_FLAG] is True
    assert payload["replacement_forecast_live"] == TARGET_LIVE_MATERIALIZATION_CONFIG
    assert policy.status == LIVE_STATUS
    assert policy.can_initiate_trade is True


def test_live_config_switch_ignores_legacy_promotion_evidence() -> None:
    plan = build_replacement_forecast_live_config_switch_plan(
        _settings(),
        promotion_evidence=_weak_promotion_evidence(),
    )

    assert plan.ok is True
    assert plan.policy_status_after == LIVE_STATUS


def test_live_config_switch_accepts_capital_objective_evidence() -> None:
    plan = build_replacement_forecast_live_config_switch_plan(
        _settings(),
        promotion_evidence=_weak_promotion_evidence(),
        capital_objective_evidence=_passing_capital_objective_evidence(),
    )

    assert plan.ok is True
    assert plan.policy_status_after == LIVE_STATUS
    assert dict(plan.target_flags) == TARGET_LIVE_FLAGS
    assert dict(plan.target_flags)[KELLY_INCREASE_FLAG] is True
    assert dict(plan.target_flags)[DIRECTION_FLIP_FLAG] is True


def test_live_config_switch_targets_full_live(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(_settings()), encoding="utf-8")

    plan = apply_replacement_forecast_live_config_switch(
        settings_path,
        promotion_evidence=_passing_promotion_evidence(),
    )
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    policy = resolve_replacement_forecast_runtime_policy(
        payload["feature_flags"],
        promotion_evidence=_passing_promotion_evidence(),
    )

    assert plan.ok is True
    assert plan.policy_status_after == LIVE_STATUS
    assert dict(plan.target_flags) == TARGET_LIVE_FLAGS
    assert payload["feature_flags"][LIVE_FLAG] is True
    assert payload["feature_flags"][KELLY_INCREASE_FLAG] is True
    assert payload["feature_flags"][DIRECTION_FLIP_FLAG] is True
    assert payload["replacement_forecast_live"] == TARGET_LIVE_MATERIALIZATION_CONFIG
    assert policy.status == LIVE_STATUS
    assert policy.can_initiate_trade is True
    assert policy.can_increase_kelly is True
    assert policy.can_flip_direction is True


def test_config_switch_can_add_missing_replacement_flags(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"feature_flags": {"some_other_flag": True}}), encoding="utf-8")

    plan = apply_replacement_forecast_config_switch(settings_path)
    payload = json.loads(settings_path.read_text(encoding="utf-8"))

    assert plan.ok is True
    assert "REPLACEMENT_CONFIG_FLAGS_WILL_BE_ADDED" in plan.reason_codes
    assert payload["feature_flags"]["some_other_flag"] is True
    for key, value in TARGET_LIVE_FLAGS.items():
        assert payload["feature_flags"][key] is value
    for key, value in TARGET_LIVE_MATERIALIZATION_CONFIG.items():
        assert payload["replacement_forecast_live"][key] == value


def test_config_switch_can_create_missing_feature_flags_object(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"ensemble": {"primary": "ecmwf_ifs025"}}), encoding="utf-8")

    plan = apply_replacement_forecast_config_switch(settings_path)
    payload = json.loads(settings_path.read_text(encoding="utf-8"))

    assert plan.ok is True
    assert "feature_flags" in payload
    assert "replacement_forecast_live" in payload
    for key, value in TARGET_LIVE_FLAGS.items():
        assert payload["feature_flags"][key] is value
    for key, value in TARGET_LIVE_MATERIALIZATION_CONFIG.items():
        assert payload["replacement_forecast_live"][key] == value


def test_config_switch_blocks_non_bool_flags_before_writing(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    before = _settings({LIVE_FLAG: "true"})
    settings_path.write_text(json.dumps(before), encoding="utf-8")

    plan = build_replacement_forecast_config_switch_plan(before)

    assert plan.ok is False
    assert "REPLACEMENT_CONFIG_FLAG_NOT_BOOL" in plan.reason_codes
    with pytest.raises(ValueError, match="REPLACEMENT_CONFIG_FLAG_NOT_BOOL"):
        apply_replacement_forecast_config_switch(settings_path)
    assert json.loads(settings_path.read_text(encoding="utf-8")) == before


def test_config_switch_cli_defaults_to_read_only_plan(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_payload = _settings()
    settings_path.write_text(json.dumps(settings_payload), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/plan_replacement_forecast_live_config.py",
            "--settings-json",
            str(settings_path),
            "--stdout",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "READY"
    assert payload["applied"] is False
    assert payload["policy_status_after"] == LIVE_STATUS
    assert json.loads(settings_path.read_text(encoding="utf-8")) == settings_payload


def test_config_switch_cli_apply_updates_temp_settings_only(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(_settings()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/plan_replacement_forecast_live_config.py",
            "--settings-json",
            str(settings_path),
            "--apply",
            "--stdout",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["applied"] is True
    assert settings_payload["feature_flags"][LIVE_FLAG] is True
    assert settings_payload["feature_flags"][KELLY_INCREASE_FLAG] is True
    assert settings_payload["feature_flags"][DIRECTION_FLIP_FLAG] is True
