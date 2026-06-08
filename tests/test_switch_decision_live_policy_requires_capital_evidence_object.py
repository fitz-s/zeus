# Created: 2026-06-08
# Last reused/audited: 2026-06-08
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: Prove BLOCKER 8 fail-closed boundary -- evaluate_replacement_forecast_switch_decision
#   must NOT trust that the runtime-policy resolver was the only producer of a LIVE_AUTHORITY
#   status. A LIVE_AUTHORITY policy arriving at this consuming boundary with
#   capital_objective_evidence is None on the switch input must FAIL CLOSED to BLOCKED and
#   grant no live trade authority, emitting REPLACEMENT_SWITCH_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED.
#   The runtime policy object does NOT retain the capital evidence (see runtime_policy.py:194
#   ReplacementForecastRuntimePolicy field list), so the switch input is the only carrier;
#   the boundary must therefore re-verify it, not assume the resolver already did.
# Authority basis: Operator PR#400 (thepath/audit-realign) BLOCKER 8 -- switch decision must
#   fail closed on capital evidence at the LIVE_AUTHORITY admission boundary.
"""Replacement forecast switch decision: LIVE policy must carry a capital-objective evidence object."""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.data.replacement_forecast_live_switch_surface import (
    REQUIRED_EVIDENCE_GATES,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_LIVE_READ_FILES,
    REQUIRED_TRADE_TABLES,
    REQUIRED_WORLD_TABLES,
    ReplacementForecastLiveSwitchInput,
    build_replacement_forecast_live_switch_report,
)
from src.data.replacement_forecast_readiness import (
    PRODUCT_ID,
    SOURCE_ID,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_refit_gate import (
    REQUIRED_REFIT_EVIDENCE,
    ReplacementForecastRefitEvidence,
    evaluate_replacement_forecast_refit_gate,
)
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    EXPECTED_CAPITAL_OBJECTIVE_LABEL,
    KELLY_INCREASE_FLAG,
    LIVE_AUTHORITY_STATUS,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    ReplacementForecastCapitalObjectiveEvidence,
    ReplacementForecastPromotionEvidence,
    resolve_replacement_forecast_runtime_policy,
)
from src.data.replacement_forecast_switch_decision import (
    ReplacementForecastSwitchDecisionInput,
    evaluate_replacement_forecast_switch_decision,
)


UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _flags(*, shadow: bool = True, veto: bool = True, trade: bool = True) -> dict[str, bool]:
    return {
        SHADOW_FLAG: shadow,
        VETO_FLAG: veto,
        TRADE_AUTHORITY_FLAG: trade,
        KELLY_INCREASE_FLAG: False,
        DIRECTION_FLIP_FLAG: False,
    }


def _promotion_evidence() -> ReplacementForecastPromotionEvidence:
    return ReplacementForecastPromotionEvidence(
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
    )


def _capital_objective_evidence() -> ReplacementForecastCapitalObjectiveEvidence:
    return ReplacementForecastCapitalObjectiveEvidence(
        selected_label=EXPECTED_CAPITAL_OBJECTIVE_LABEL,
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


def _live_authority_policy():
    # A genuine LIVE_AUTHORITY policy: trade flag armed AND both evidence objects passing.
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(trade=True),
        promotion_evidence=_promotion_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )
    assert policy.status == LIVE_AUTHORITY_STATUS
    return policy


def _live_switch(policy):
    return build_replacement_forecast_live_switch_report(
        ReplacementForecastLiveSwitchInput(
            runtime_policy=policy,
            available_files=tuple(REQUIRED_LIVE_READ_FILES),
            forecast_tables=tuple(REQUIRED_FORECAST_TABLES),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            trade_tables=tuple(REQUIRED_TRADE_TABLES),
            enabled_evidence_gates=tuple(REQUIRED_EVIDENCE_GATES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )


def _readiness():
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="b0-run",
            source_available_at=_dt(2),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id="aifs-run",
            source_available_at=_dt(2, 30),
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(3),
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
            source_run_id="posterior-run",
            source_available_at=_dt(3, 5),
            posterior_id=77,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        computed_at=_dt(4, 1),
        expires_at=_dt(6),
        dependencies=dependencies,
    )


def _refit_live_promotion():
    return evaluate_replacement_forecast_refit_gate(
        ReplacementForecastRefitEvidence(
            official_days=5,
            official_rows=250,
            temperature_metric="high",
            source_family="derived_posterior",
            product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            calibration_method="soft_anchor_product_specific_nested_refit",
            enabled_evidence=tuple(REQUIRED_REFIT_EVIDENCE),
            min_guardrail_bucket_rows=20,
            emos_key_includes_product=True,
            emos_key_schema="replacement_product_keyed_v1",
            emos_identity_evidence_status="REPLACEMENT_EMOS_PRODUCT_IDENTITY_READY",
            data_refit_requested=True,
            live_promotion_requested=True,
        )
    )


def test_live_policy_with_capital_evidence_none_fails_closed_to_blocked() -> None:
    # BLOCKER 8: the switch decision is a CONSUMING boundary. The runtime policy object
    # does not retain the capital-objective evidence, so a LIVE_AUTHORITY status arriving
    # here is an unverified claim from upstream. With capital_objective_evidence is None on
    # the switch input, the boundary must FAIL CLOSED -- it must NOT trust the resolver was
    # the only producer and silently grant trade authority.
    policy = _live_authority_policy()
    decision = evaluate_replacement_forecast_switch_decision(
        ReplacementForecastSwitchDecisionInput(
            runtime_policy=policy,
            live_switch_report=_live_switch(policy),
            readiness=_readiness(),
            refit_decision=_refit_live_promotion(),
            capital_objective_evidence=None,
        )
    )

    assert decision.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in decision.reason_codes
    assert decision.can_initiate_trade is False
    assert decision.can_increase_kelly is False
    assert decision.can_flip_direction is False


def test_live_policy_with_failing_capital_evidence_object_blocks_with_its_own_codes() -> None:
    # When a capital-objective evidence object IS supplied but is itself failing, the
    # boundary folds the object's own blocking_reason_codes() (existing behavior preserved
    # by the fix). after_cost_pnl <= 0 yields the after-cost-EV blocking code.
    policy = _live_authority_policy()
    failing_capital = ReplacementForecastCapitalObjectiveEvidence(
        selected_label=EXPECTED_CAPITAL_OBJECTIVE_LABEL,
        replay_status="EMPIRICAL_WINNER",
        after_cost_pnl=-1.0,
        source_availability_observed=True,
        source_availability_violations=0,
        anti_lookahead_violations=0,
        same_clob_replay_passed=True,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        product_specific_refit_passed=True,
    )
    decision = evaluate_replacement_forecast_switch_decision(
        ReplacementForecastSwitchDecisionInput(
            runtime_policy=policy,
            live_switch_report=_live_switch(policy),
            readiness=_readiness(),
            refit_decision=_refit_live_promotion(),
            capital_objective_evidence=failing_capital,
        )
    )

    assert decision.status == "BLOCKED"
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_AFTER_COST_PNL_NOT_POSITIVE" in decision.reason_codes
    assert "REPLACEMENT_SWITCH_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" not in decision.reason_codes
    assert decision.can_initiate_trade is False


def test_live_policy_with_passing_capital_evidence_object_admits_live_authority() -> None:
    # Positive path: a LIVE_AUTHORITY policy with the refit live-promotion grant AND a
    # passing capital-objective evidence object supplied to the switch input is admitted.
    policy = _live_authority_policy()
    decision = evaluate_replacement_forecast_switch_decision(
        ReplacementForecastSwitchDecisionInput(
            runtime_policy=policy,
            live_switch_report=_live_switch(policy),
            readiness=_readiness(),
            refit_decision=_refit_live_promotion(),
            capital_objective_evidence=_capital_objective_evidence(),
        )
    )

    assert decision.status == "LIVE_AUTHORITY"
    assert decision.reason_codes == ("REPLACEMENT_SWITCH_LIVE_AUTHORITY_ADMITTED",)
    assert decision.can_initiate_trade is True
    assert decision.can_apply_veto is True
    assert decision.can_read_shadow_posterior is True
