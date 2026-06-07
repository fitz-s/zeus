# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-07; last_reused=2026-06-07
# Purpose: Protect replacement forecast runtime policy flags and evidence-gated live authority.
# Reuse: Run before any event-reactor or daemon wiring of the replacement posterior.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast runtime policy tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    ReplacementForecastPromotionEvidence,
    ReplacementForecastCapitalObjectiveEvidence,
    resolve_replacement_forecast_runtime_policy,
)
def _flags(**overrides: bool) -> dict[str, bool]:
    flags = {
        SHADOW_FLAG: False,
        VETO_FLAG: False,
        TRADE_AUTHORITY_FLAG: False,
        KELLY_INCREASE_FLAG: False,
        DIRECTION_FLIP_FLAG: False,
    }
    flags.update(overrides)
    return flags


def _passing_evidence() -> ReplacementForecastPromotionEvidence:
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


def _capital_objective_evidence(**overrides: object) -> ReplacementForecastCapitalObjectiveEvidence:
    values = {
        "selected_label": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00",
        "replay_status": "EMPIRICAL_WINNER",
        "after_cost_pnl": 97.65,
        "source_availability_observed": True,
        "source_availability_violations": 0,
        "anti_lookahead_violations": 0,
        "same_clob_replay_passed": True,
        "fee_depth_fill_evidence_passed": True,
        "unit_pnl_only": False,
        "product_specific_refit_passed": True,
    }
    values.update(overrides)
    return ReplacementForecastCapitalObjectiveEvidence(**values)


def test_configured_replacement_forecast_flags_fail_closed_without_runtime_evidence() -> None:
    settings_path = Path(__file__).resolve().parents[1] / "config/settings.json"
    flags = json.loads(settings_path.read_text())["feature_flags"]

    policy = resolve_replacement_forecast_runtime_policy(flags)

    if flags.get(TRADE_AUTHORITY_FLAG) is True:
        assert policy.status == "BLOCKED"
        assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
        assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in policy.reason_codes
        assert policy.can_read_shadow_posterior is False
        assert policy.can_apply_veto is False
    else:
        if flags.get(SHADOW_FLAG) is True and flags.get(VETO_FLAG) is True:
            assert policy.status == "SHADOW_VETO_ONLY"
            assert policy.reason_codes == ("REPLACEMENT_SHADOW_VETO_ONLY",)
            assert policy.can_read_shadow_posterior is True
            assert policy.can_apply_veto is True
        else:
            assert policy.status == "DISABLED"
            assert policy.reason_codes == ("REPLACEMENT_DISABLED_BY_FLAG",)
            assert policy.can_read_shadow_posterior is False
            assert policy.can_apply_veto is False
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_configured_production_replacement_is_live_authority_without_dangerous_escalation() -> None:
    settings_path = Path(__file__).resolve().parents[1] / "config/settings.json"
    settings_payload = json.loads(settings_path.read_text())
    flags = settings_payload["feature_flags"]
    promotion_evidence = _passing_evidence()
    capital_objective_evidence = _capital_objective_evidence()

    assert settings_payload["edli_v1"]["edli_emos_sole_calibrator_enabled"] is True
    assert flags[SHADOW_FLAG] is True
    assert flags[VETO_FLAG] is True
    assert flags[TRADE_AUTHORITY_FLAG] is True
    assert flags[KELLY_INCREASE_FLAG] is False
    assert flags[DIRECTION_FLIP_FLAG] is False
    assert promotion_evidence is not None
    assert capital_objective_evidence is not None

    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=capital_objective_evidence,
    )

    assert policy.status == "LIVE_AUTHORITY"
    assert policy.can_read_shadow_posterior is True
    assert policy.can_apply_veto is True
    assert policy.can_initiate_trade is True
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_replacement_forecast_policy_allows_shadow_then_veto_only() -> None:
    shadow = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True}))
    assert shadow.status == "SHADOW_ONLY"
    assert shadow.can_read_shadow_posterior is True
    assert shadow.can_apply_veto is False
    assert shadow.can_initiate_trade is False

    veto = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True, VETO_FLAG: True}))
    assert veto.status == "SHADOW_VETO_ONLY"
    assert veto.can_read_shadow_posterior is True
    assert veto.can_apply_veto is True
    assert veto.can_initiate_trade is False
    assert veto.can_increase_kelly is False
    assert veto.can_flip_direction is False


def test_replacement_forecast_policy_blocks_dangerous_flag_combinations() -> None:
    no_shadow = resolve_replacement_forecast_runtime_policy(_flags(**{VETO_FLAG: True}))
    assert no_shadow.status == "BLOCKED"
    assert "REPLACEMENT_SHADOW_FLAG_REQUIRED" in no_shadow.reason_codes

    no_veto = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True, TRADE_AUTHORITY_FLAG: True}))
    assert no_veto.status == "BLOCKED"
    assert "REPLACEMENT_VETO_FLAG_REQUIRED_BEFORE_AUTHORITY" in no_veto.reason_codes
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in no_veto.reason_codes

    kelly_without_trade = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True, VETO_FLAG: True, KELLY_INCREASE_FLAG: True}))
    assert kelly_without_trade.status == "BLOCKED"
    assert "REPLACEMENT_TRADE_AUTHORITY_REQUIRED_FOR_DANGEROUS_FLAGS" in kelly_without_trade.reason_codes


def test_replacement_forecast_trade_authority_requires_promotion_evidence() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    blocked = resolve_replacement_forecast_runtime_policy(flags)
    assert blocked.status == "BLOCKED"
    assert blocked.can_initiate_trade is False

    weak_evidence = ReplacementForecastPromotionEvidence(
        official_days=1,
        official_rows=57,
        after_cost_pnl=10.0,
        q_lcb_coverage=0.99,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=57,
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
    still_blocked = resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=weak_evidence)
    assert still_blocked.status == "BLOCKED"

    promoted = resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=_passing_evidence())
    assert promoted.status == "LIVE_AUTHORITY"
    assert promoted.can_initiate_trade is True
    assert promoted.can_increase_kelly is False
    assert promoted.can_flip_direction is False


def test_replacement_forecast_trade_authority_accepts_capital_objective_evidence() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        capital_objective_evidence=_capital_objective_evidence(),
    )

    assert policy.status == "LIVE_AUTHORITY"
    assert policy.reason_codes == ("REPLACEMENT_CAPITAL_OBJECTIVE_LIVE_AUTHORITY",)
    assert policy.can_initiate_trade is True
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_replacement_forecast_trade_authority_rejects_bad_capital_objective_evidence() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    wrong_winner = resolve_replacement_forecast_runtime_policy(
        flags,
        capital_objective_evidence=_capital_objective_evidence(selected_label="B0"),
    )
    assumed_source = resolve_replacement_forecast_runtime_policy(
        flags,
        capital_objective_evidence=_capital_objective_evidence(source_availability_observed=False),
    )

    assert wrong_winner.status == "BLOCKED"
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_SELECTED_LABEL_MISMATCH" in wrong_winner.reason_codes
    assert assumed_source.status == "BLOCKED"
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_SOURCE_AVAILABILITY_NOT_OBSERVED" in assumed_source.reason_codes


def test_replacement_forecast_trade_authority_rejects_unit_pnl_or_incomplete_replay_evidence() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    unit_only = ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=100.0,
        q_lcb_coverage=0.96,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=False,
        unit_pnl_only=True,
    )
    incomplete = ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=100.0,
        q_lcb_coverage=0.96,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=249,
        same_clob_replay_blocked_rows=1,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
    )

    assert resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=unit_only).status == "BLOCKED"
    assert resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=incomplete).status == "BLOCKED"


def test_replacement_forecast_trade_authority_rejects_unstructured_nested_finetune_claim() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    fake_nested = ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=100.0,
        q_lcb_coverage=0.96,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
    )

    policy = resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=fake_nested)

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PROMOTION_NESTED_BRIER_MISSING" in policy.reason_codes
    assert "REPLACEMENT_PROMOTION_PRODUCT_SPECIFIC_REFIT_MISSING" in policy.reason_codes


def test_replacement_forecast_policy_requires_strict_bool_flags() -> None:
    missing = _flags()
    del missing[SHADOW_FLAG]
    with pytest.raises(KeyError, match=SHADOW_FLAG):
        resolve_replacement_forecast_runtime_policy(missing)

    bad_type: dict[str, object] = _flags()
    bad_type[SHADOW_FLAG] = "false"
    with pytest.raises(TypeError, match=SHADOW_FLAG):
        resolve_replacement_forecast_runtime_policy(bad_type)
