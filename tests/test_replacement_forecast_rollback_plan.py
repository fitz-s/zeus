# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast rollback planning as a non-mutating operator payload.
# Reuse: Run before adding an operator CLI or control-plane command for replacement forecast rollback.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast rollback plan tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.replacement_forecast_rollback_plan import ROLLBACK_MODE, build_replacement_forecast_rollback_plan
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    ReplacementForecastCapitalObjectiveEvidence,
    ReplacementForecastPromotionEvidence,
    resolve_replacement_forecast_runtime_policy,
)


def _passing_capital_objective_evidence() -> ReplacementForecastCapitalObjectiveEvidence:
    # FIX-1 AND (ITEM B): the second proof required (alongside promotion evidence)
    # before the resolver can reach LIVE_AUTHORITY.
    return ReplacementForecastCapitalObjectiveEvidence(
        selected_label="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00",
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


def _flags(*, shadow: bool = False, veto: bool = False, trade: bool = False, kelly: bool = False, flip: bool = False) -> dict[str, bool]:
    return {
        SHADOW_FLAG: shadow,
        VETO_FLAG: veto,
        TRADE_AUTHORITY_FLAG: trade,
        KELLY_INCREASE_FLAG: kelly,
        DIRECTION_FLIP_FLAG: flip,
    }


def test_rollback_plan_disables_all_replacement_flags_without_side_effects() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))
    plan = build_replacement_forecast_rollback_plan(
        current_policy=policy,
        reason="operator requested disable after shadow regression cluster",
        generated_at=datetime(2026, 6, 6, 8, 0, tzinfo=timezone.utc),
    ).as_dict()

    assert plan["mode"] == ROLLBACK_MODE
    assert plan["current_policy_status"] == "SHADOW_VETO_ONLY"
    assert plan["feature_flag_updates"] == {
        SHADOW_FLAG: False,
        VETO_FLAG: False,
        TRADE_AUTHORITY_FLAG: False,
        KELLY_INCREASE_FLAG: False,
        DIRECTION_FLIP_FLAG: False,
    }
    assert plan["source_ids_to_pause"] == [
        "ecmwf_aifs_ens",
        "openmeteo_ecmwf_ifs_9km",
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
    ]
    assert plan["product_ids_to_quarantine"] == ["openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1"]


def test_rollback_plan_preserves_shadow_rows_and_blocks_truth_mutation() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))
    plan = build_replacement_forecast_rollback_plan(
        current_policy=policy,
        reason="guardrail bucket failed",
        generated_at="2026-06-06T08:00:00+00:00",
    ).as_dict()

    assert plan["shadow_tables_to_preserve"] == [
        "raw_forecast_artifacts",
        "deterministic_forecast_anchors",
        "forecast_posteriors",
        "replacement_shadow_decisions",
    ]
    assert "delete_shadow_rows" in plan["prohibited_actions"]
    assert "write_settlement_truth" in plan["prohibited_actions"]
    assert "enable_trade_authority" in plan["prohibited_actions"]
    assert "increase_kelly" in plan["prohibited_actions"]
    assert "flip_direction" in plan["prohibited_actions"]


def test_rollback_plan_can_describe_live_authority_shutdown_without_authorizing_it() -> None:
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(shadow=True, veto=True, trade=True, kelly=True, flip=True),
        promotion_evidence=ReplacementForecastPromotionEvidence(
            official_days=6,
            official_rows=300,
            after_cost_pnl=1.0,
            q_lcb_coverage=0.96,
            anti_lookahead_violations=0,
            source_availability_violations=0,
            unresolved_regression_clusters=0,
            same_clob_replay_passed=True,
            nested_walk_forward_passed=True,
            same_clob_replay_scored_rows=300,
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
        ),
        capital_objective_evidence=_passing_capital_objective_evidence(),
    )
    plan = build_replacement_forecast_rollback_plan(
        current_policy=policy,
        reason="rollback even if hypothetical promotion state exists",
        generated_at="2026-06-06T08:00:00+00:00",
    ).as_dict()

    assert plan["current_policy_status"] == "LIVE_AUTHORITY"
    assert all(value is False for value in plan["feature_flag_updates"].values())
    assert "initiate_replacement_trade" in plan["prohibited_actions"]


def test_rollback_plan_rejects_missing_reason_naive_time_or_short_alias() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))

    with pytest.raises(ValueError, match="reason"):
        build_replacement_forecast_rollback_plan(current_policy=policy, reason="", generated_at="2026-06-06T08:00:00+00:00")
    with pytest.raises(ValueError, match="timezone-aware"):
        build_replacement_forecast_rollback_plan(current_policy=policy, reason="x", generated_at="2026-06-06T08:00:00")
    with pytest.raises(ValueError, match="full replacement identity"):
        build_replacement_forecast_rollback_plan(
            current_policy=policy,
            reason="x",
            generated_at="2026-06-06T08:00:00+00:00",
            additional_source_ids_to_pause=("short_" + "h" + "3_alias",),
        )
