# Created: 2026-06-06
# Last reused/audited: 2026-06-08
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-08; last_reused=2026-06-08
# 2026-06-08 audit: re-authored live-authority test to FIX-1 AND-gate invariant
#   (LIVE_AUTHORITY requires BOTH promotion + capital-objective evidence, not promotion
#   alone). OBSOLETE_INVARIANT re-authored, not relaxed. See runtime_policy.py:286.
# Purpose: Prove replacement forecast runtime switch admission composes policy, live-state, readiness, and refit gates.
# Reuse: Run before wiring replacement forecast switch decisions into daemon or event reactor.
# Authority basis: Operator-directed replacement forecast worktree integration; simple switch must be safe and reversible.
"""Replacement forecast runtime switch decision tests."""

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
    READY_STATUS,
    SOURCE_ID,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_refit_gate import REQUIRED_REFIT_EVIDENCE, ReplacementForecastRefitEvidence, evaluate_replacement_forecast_refit_gate
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    EXPECTED_CAPITAL_OBJECTIVE_LABEL,
    KELLY_INCREASE_FLAG,
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


def _flags(*, shadow: bool = False, veto: bool = False, trade: bool = False, kelly: bool = False, flip: bool = False) -> dict[str, bool]:
    return {
        SHADOW_FLAG: shadow,
        VETO_FLAG: veto,
        TRADE_AUTHORITY_FLAG: trade,
        KELLY_INCREASE_FLAG: kelly,
        DIRECTION_FLIP_FLAG: flip,
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


def _policy(*, shadow: bool = True, veto: bool = True, trade: bool = False, promotion: bool = True, capital: bool = True):
    # FIX-1 (runtime_policy.py:286 replacement_live_authority_evidence_gate, AND-gate):
    # LIVE_AUTHORITY requires the flag ladder (trade=True) AND BOTH evidence objects
    # passing. The promotion / capital knobs let a test omit one proof to exercise the
    # "one proof is necessary but not sufficient" ban; default both=True so trade=True
    # composes a genuine LIVE_AUTHORITY policy.
    promotion_evidence = _promotion_evidence() if (trade and promotion) else None
    capital_objective_evidence = _capital_objective_evidence() if (trade and capital) else None
    return resolve_replacement_forecast_runtime_policy(
        _flags(shadow=shadow, veto=veto, trade=trade),
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=capital_objective_evidence,
    )


def _live_switch(policy=None, *, current: bool = True):
    policy = policy or _policy()
    return build_replacement_forecast_live_switch_report(
        ReplacementForecastLiveSwitchInput(
            runtime_policy=policy,
            available_files=tuple(REQUIRED_LIVE_READ_FILES),
            forecast_tables=tuple(REQUIRED_FORECAST_TABLES),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            trade_tables=tuple(REQUIRED_TRADE_TABLES),
            enabled_evidence_gates=tuple(REQUIRED_EVIDENCE_GATES),
            source_fact_status="CURRENT_FOR_LIVE" if current else "STALE_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE" if current else "STALE_FOR_LIVE",
        )
    )


def _readiness(*, ready: bool = True):
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
            source_available_at=_dt(3, 5) if ready else _dt(5),
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


def _refit(*, live_promotion: bool = False):
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
            live_promotion_requested=live_promotion,
        )
    )


_DEFAULT = object()


def _decision(policy=None, live_switch=None, readiness=_DEFAULT, refit=_DEFAULT):
    policy = policy or _policy()
    return evaluate_replacement_forecast_switch_decision(
        ReplacementForecastSwitchDecisionInput(
            runtime_policy=policy,
            live_switch_report=live_switch or _live_switch(policy),
            readiness=_readiness() if readiness is _DEFAULT else readiness,
            refit_decision=_refit() if refit is _DEFAULT else refit,
        )
    )


def test_switch_decision_disabled_is_safe_noop() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags())
    decision = _decision(policy=policy, live_switch=_live_switch(policy), readiness=None)

    assert decision.status == "DISABLED"
    assert decision.can_read_shadow_posterior is False
    assert decision.can_apply_reactor_hook is False
    assert decision.can_apply_veto is False


def test_switch_decision_admits_shadow_only_read_without_reactor_hook() -> None:
    policy = _policy(shadow=True, veto=False)
    decision = _decision(policy=policy, live_switch=_live_switch(policy))

    assert decision.status == "SHADOW_ONLY"
    assert decision.can_read_shadow_posterior is True
    assert decision.can_apply_reactor_hook is False
    assert decision.can_apply_veto is False
    assert decision.can_initiate_trade is False


def test_switch_decision_admits_veto_only_when_all_runtime_inputs_are_ready() -> None:
    decision = _decision()

    assert decision.status == "SHADOW_VETO_ONLY"
    assert decision.can_read_shadow_posterior is True
    assert decision.can_apply_reactor_hook is True
    assert decision.can_apply_veto is True
    assert decision.can_initiate_trade is False
    assert decision.can_increase_kelly is False
    assert decision.can_flip_direction is False
    assert decision.readiness_id is not None


def test_switch_decision_admits_veto_without_product_specific_refit() -> None:
    decision = _decision(refit=None)

    assert decision.status == "SHADOW_VETO_ONLY"
    assert "REPLACEMENT_SWITCH_SHADOW_VETO_ONLY_ADMITTED" in decision.reason_codes
    assert decision.can_apply_veto is True
    assert decision.can_initiate_trade is False
    assert decision.can_increase_kelly is False
    assert decision.can_flip_direction is False


def test_switch_decision_blocks_stale_live_switch_or_missing_readiness() -> None:
    stale = _decision(live_switch=_live_switch(current=False))
    missing = _decision(readiness=None)

    assert stale.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_SOURCE_FACTS_STALE" in stale.reason_codes
    assert missing.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_READINESS_MISSING" in missing.reason_codes


def test_switch_decision_blocks_not_ready_dependencies() -> None:
    decision = _decision(readiness=_readiness(ready=False))

    assert decision.status == "BLOCKED"
    assert "REPLACEMENT_DEPENDENCY_AFTER_DECISION_TIME" in decision.reason_codes


def test_switch_decision_blocks_refit_promotion_without_live_policy() -> None:
    refit_promotion = _decision(refit=_refit(live_promotion=True))

    assert refit_promotion.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_REFIT_PROMOTION_NOT_ADMITTED" in refit_promotion.reason_codes


def test_switch_decision_admits_live_authority_only_with_policy_and_both_evidence_and_refit_promotion() -> None:
    # FIX-1 (commit cbc454e17e / 544c5030fc, runtime_policy.py:286
    # replacement_live_authority_evidence_gate) tightened OR -> AND: LIVE_AUTHORITY now
    # requires the flag ladder AND BOTH the promotion (statistical-validation) and the
    # capital-objective (empirical-winner + after-cost-EV) evidence objects passing,
    # *plus* the refit live-promotion grant. The pre-FIX-1 premise that policy + refit
    # promotion ALONE (promotion evidence only, capital evidence absent) admits
    # LIVE_AUTHORITY is the OBSOLETE invariant and must NOT be restored.
    #
    # A policy carrying BOTH passing evidence objects reaches LIVE_AUTHORITY status; the
    # switch decision then still requires the refit live-promotion grant.
    policy = _policy(trade=True)
    assert policy.status == "LIVE_AUTHORITY"

    missing_refit_promotion = _decision(policy=policy, live_switch=_live_switch(policy))
    live_authority = _decision(policy=policy, live_switch=_live_switch(policy), refit=_refit(live_promotion=True))

    assert missing_refit_promotion.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_REFIT_LIVE_PROMOTION_REQUIRED" in missing_refit_promotion.reason_codes
    assert live_authority.status == "LIVE_AUTHORITY"
    assert live_authority.reason_codes == ("REPLACEMENT_SWITCH_LIVE_AUTHORITY_ADMITTED",)
    assert live_authority.can_read_shadow_posterior is True
    assert live_authority.can_apply_reactor_hook is True
    assert live_authority.can_apply_veto is True
    assert live_authority.can_initiate_trade is True
    assert live_authority.can_increase_kelly is False
    assert live_authority.can_flip_direction is False


def test_switch_decision_blocks_live_authority_when_only_one_evidence_proof_present() -> None:
    # FIX-1 AND-gate antibody: a single passing proof is necessary but NOT sufficient.
    # When trade_authority is armed but only ONE evidence object is supplied, the runtime
    # policy fails closed to BLOCKED (never LIVE_AUTHORITY), so the composed switch
    # decision is BLOCKED and grants no trade authority. Both directions are checked so
    # neither proof can stand alone.
    promotion_only_policy = _policy(trade=True, capital=False)
    capital_only_policy = _policy(trade=True, promotion=False)

    assert promotion_only_policy.status == "BLOCKED"
    assert "REPLACEMENT_LIVE_AUTHORITY_REQUIRES_EVIDENCE" in promotion_only_policy.reason_codes
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in promotion_only_policy.reason_codes
    assert capital_only_policy.status == "BLOCKED"
    assert "REPLACEMENT_LIVE_AUTHORITY_REQUIRES_EVIDENCE" in capital_only_policy.reason_codes
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in capital_only_policy.reason_codes

    for policy in (promotion_only_policy, capital_only_policy):
        decision = _decision(
            policy=policy,
            live_switch=_live_switch(policy),
            refit=_refit(live_promotion=True),
        )
        assert decision.status == "BLOCKED"
        assert decision.can_initiate_trade is False
        assert decision.can_increase_kelly is False
        assert decision.can_flip_direction is False


def test_switch_decision_payload_is_json_ready_and_non_authoritative() -> None:
    payload = _decision().as_dict()

    assert payload["status"] == "SHADOW_VETO_ONLY"
    assert payload["can_initiate_trade"] is False
    assert payload["can_increase_kelly"] is False
    assert payload["can_flip_direction"] is False
