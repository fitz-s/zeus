# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.decision_kernel import claims
from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.ledger import DecisionCertificateLedger
from src.decision_kernel.verifier import verify_actionable_trade


NOW = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)


def test_actionable_requires_live_mode():
    parents, action = actionable_graph(mode="NO_SUBMIT")

    with pytest.raises(CertificateVerificationError, match="LIVE mode"):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_redecision_as_forecast_lane():
    parents, action = actionable_graph(
        action_payload={"event_type": "EDLI_REDECISION_PENDING"}
    )

    verify_actionable_trade(action, parents)


def test_actionable_accepts_day0_observation_authority_with_qkernel():
    parents, action = actionable_graph(
        action_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "raw_value": 20.0,
            "rounded_value": 20,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "day0_probability_authority": _day0_probability_authority(),
            "_edli_q_source": "day0_remaining_day",
            "_edli_day0_q_mode": "remaining_day",
            "_edli_day0_remaining_models": 3,
            "_edli_day0_lcb_transform": _day0_lcb_transform(),
            "qkernel_execution_economics": _day0_qkernel_economics(),
        },
        extra_parent_payloads={
            claims.DAY0_AUTHORITY: {
                "event_id": "event-1",
                "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
            },
            claims.ABSORBING_BOUNDARY: {
                "event_id": "event-1",
                "boundary": "day0_absorbing_hard_fact",
            },
        },
    )

    verify_actionable_trade(action, parents)


def test_actionable_rejects_degenerate_day0_remaining_window_q():
    parents, action = actionable_graph(
        action_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "raw_value": 20.0,
            "rounded_value": 20,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "day0_probability_authority": _day0_probability_authority(),
            "_edli_q_source": "day0_remaining_day",
            "_edli_day0_q_mode": "remaining_day",
            "_edli_day0_remaining_models": 3,
            "_edli_day0_lcb_transform": _day0_lcb_transform(),
            "q_live": 0.6,
            "q_lcb_5pct": 0.6,
            "qkernel_execution_economics": {
                **_day0_qkernel_economics(),
                "payoff_q_point": 0.6,
                "payoff_q_lcb": 0.6,
            },
        },
        extra_parent_payloads={
            claims.DAY0_AUTHORITY: {
                "event_id": "event-1",
                "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
            },
            claims.ABSORBING_BOUNDARY: {
                "event_id": "event-1",
                "boundary": "day0_absorbing_hard_fact",
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="degenerate with q_live"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_day0_observed_boundary_as_entry_qkernel_guard():
    parents, action = actionable_graph(
        action_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "raw_value": 32.0,
            "rounded_value": 32,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "day0_probability_authority": _day0_probability_authority(),
            "_edli_q_source": "day0_remaining_day",
            "_edli_day0_q_mode": "remaining_day",
            "_edli_day0_remaining_models": 3,
            "_edli_day0_lcb_transform": _day0_lcb_transform(),
            "qkernel_execution_economics": {
                **_day0_qkernel_economics(),
                "q_lcb_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                "selection_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                "q_lcb_guard_cell_key": "day0_observed_boundary",
                "selection_guard_cell_key": "day0_observed_boundary",
            },
        },
        extra_parent_payloads={
            claims.DAY0_AUTHORITY: {
                "event_id": "event-1",
                "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
            },
            claims.ABSORBING_BOUNDARY: {
                "event_id": "event-1",
                "boundary": "day0_absorbing_hard_fact",
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="DAY0_OBSERVED_BOUNDARY"):
        verify_actionable_trade(action, parents)


def _day0_lcb_transform():
    return {
        "yes_lcb_by_condition": {"condition-1": 0.6},
        "no_lcb_by_condition": {"condition-1": 0.2},
        "mask": [1.0],
        "absorbing_yes_conditions": [],
        "absorbing_no_conditions": [],
        "staleness_suppressed_conditions": [],
        "immature_finite_yes_suppressed_conditions": [],
        "day0_exit_authority_status": "mature",
        "day0_exit_authority_reason": "day0_high_extreme_post_peak",
        "rounded_extreme": 20.0,
        "metric": "high",
    }


def _day0_probability_authority():
    return {
        "q_source": "day0_remaining_day",
        "q_mode": "remaining_day",
        "remaining_models": 3,
        "rounded_value": 20,
        "observation_time": "2026-05-25T11:30:00+00:00",
        "observation_available_at": "2026-05-25T11:35:00+00:00",
        "lcb_transform": _day0_lcb_transform(),
    }


def _day0_qkernel_economics() -> dict:
    economics = dict(_action_payload()["qkernel_execution_economics"])
    economics.update(
        {
            "q_lcb_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "day0_remaining_day_q_lcb",
            "selection_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
            "selection_guard_abstained": False,
            "selection_guard_cell_key": "day0_remaining_day_q_lcb",
            "selection_guard_n": 0,
            "selection_guard_q_safe": economics["payoff_q_lcb"],
        }
    )
    return economics


def test_actionable_requires_positive_action_score():
    parents, action = actionable_graph(action_payload={"action_score": 0.0})

    with pytest.raises(CertificateVerificationError, match="action_score"):
        verify_actionable_trade(action, parents)


def test_actionable_requires_positive_trade_score():
    parents, action = actionable_graph(action_payload={"trade_score": 0.0})

    with pytest.raises(CertificateVerificationError, match="trade_score"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_no_native_quote():
    parents, action = actionable_graph(action_payload={"native_quote_available": False})

    with pytest.raises(CertificateVerificationError, match="native_quote_available"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_p_fill_lcb_zero():
    parents, action = actionable_graph(action_payload={"p_fill_lcb": 0.0})

    with pytest.raises(CertificateVerificationError, match="p_fill_lcb"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_q_lcb_above_q_live():
    parents, action = actionable_graph(action_payload={"q_live": 0.55, "q_lcb_5pct": 0.56})

    with pytest.raises(CertificateVerificationError, match="q_lcb_5pct exceeds q_live"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_center_yes_below_quality_floor():
    parents, action = actionable_graph(
        action_payload={
            "q_live": 0.24833093804728934,
            "q_lcb_5pct": 0.0990451308919892,
            "c_fee_adjusted": 0.041,
            "c_cost_95pct": 0.041,
            "trade_score": 0.0580451308919892,
            "action_score": 0.0580451308919892,
            "qkernel_execution_economics": {
                **_action_payload()["qkernel_execution_economics"],
                "payoff_q_point": 0.24833093804728934,
                "payoff_q_lcb": 0.0990451308919892,
                "cost": 0.041,
                "edge_lcb": 0.0580451308919892,
                "selection_guard_q_safe": 0.0990451308919892,
            },
        }
    )

    with pytest.raises(
        CertificateVerificationError,
        match="ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR",
    ):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_center_yes_below_binary_floor_when_quality_clear():
    parents, action = actionable_graph(
        action_payload={
            "q_live": 0.36,
            "q_lcb_5pct": 0.30,
            "c_fee_adjusted": 0.12,
            "c_cost_95pct": 0.12,
            "trade_score": 0.18,
            "action_score": 0.18,
            "qkernel_execution_economics": {
                **_action_payload()["qkernel_execution_economics"],
                "payoff_q_point": 0.36,
                "payoff_q_lcb": 0.30,
                "cost": 0.12,
                "edge_lcb": 0.18,
                "selection_guard_q_safe": 0.30,
            },
        }
    )

    verify_actionable_trade(action, parents)


def test_actionable_requires_qkernel_spine_selection_authority():
    parents, action = actionable_graph(action_payload={"selection_authority_applied": None})

    with pytest.raises(CertificateVerificationError, match="selection_authority_applied"):
        verify_actionable_trade(action, parents)


def test_actionable_requires_qkernel_selection_guard():
    payload = _action_payload()
    economics = dict(payload["qkernel_execution_economics"])
    economics.pop("selection_guard_basis")
    economics.pop("selection_guard_abstained")
    economics.pop("selection_guard_q_safe")
    parents, action = actionable_graph(
        action_payload={"qkernel_execution_economics": economics}
    )

    with pytest.raises(CertificateVerificationError, match="selection_guard_basis"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_side_not_armed_qkernel_selection_guard():
    parents, action = actionable_graph(
        action_payload={
            "qkernel_execution_economics": {
                **_action_payload()["qkernel_execution_economics"],
                "selection_guard_basis": "SIDE_NOT_ARMED",
            }
        }
    )

    with pytest.raises(CertificateVerificationError, match="selection_guard_basis blocks side"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_qkernel_payoff_probability_mismatch():
    parents, action = actionable_graph(
        action_payload={
            "q_live": 0.65,
            "q_lcb_5pct": 0.60,
            "c_fee_adjusted": 0.40,
            "c_cost_95pct": 0.40,
            "p_fill_lcb": 0.9997671696598043,
            "trade_score": 0.04049776073684555,
            "action_score": 0.04049776073684555,
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.22351072116676574,
                "payoff_q_lcb": 0.05049776073684555,
                "cost": 0.01,
                "edge_lcb": 0.04049776073684555,
                "optimal_delta_u": 0.013993788651471595,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 5.0,
                "false_edge_rate": 0.02599350162459385,
                "direction_law_ok": False,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.003,
            },
        }
    )

    with pytest.raises(CertificateVerificationError, match="payoff_q_point mismatches|payoff_q_lcb mismatches"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_oof_reliability_direction_override_for_yes():
    parents, action = actionable_graph(
        action_payload={
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.7,
                "payoff_q_lcb": 0.6,
                "cost": 0.4,
                "edge_lcb": 0.2,
                "optimal_delta_u": 0.01,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 5.0,
                "false_edge_rate": 0.01,
                "direction_law_ok": False,
                "q_lcb_guard_basis": "OOF_WILSON_95",
                "q_lcb_guard_abstained": False,
                "q_lcb_guard_cell_key": "high|L2_3|YES|nonmodal|qb2|coarse_global",
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.6,
            },
        }
    )

    with pytest.raises(CertificateVerificationError, match="qkernel direction admission"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_execution_command_id_present():
    parents, action = actionable_graph(action_payload={"execution_command_id": "cmd-1"})

    with pytest.raises(CertificateVerificationError, match="execution_command_id"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_wrong_cost_source_for_buy_no():
    parents, action = actionable_graph(
        action_payload={
            "direction": "buy_no",
            "token_id": "no-1",
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "NO",
                "payoff_q_point": 0.7,
                "payoff_q_lcb": 0.6,
                "cost": 0.4,
                "edge_lcb": 0.2,
                "optimal_delta_u": 0.01,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 5.0,
                "false_edge_rate": 0.01,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.6,
            },
        },
        parent_overrides={
            claims.CANDIDATE_EVIDENCE: {"direction": "buy_no", "selected_token_id": "no-1"},
            claims.EXECUTABLE_SNAPSHOT: {"token_id": "no-1"},
            claims.QUOTE_FEASIBILITY: {"direction": "buy_no", "token_id": "no-1", "cost_source": "native_orderbook_bid"},
            claims.COST_MODEL: {"direction": "buy_no", "token_id": "no-1", "cost_source": "native_orderbook_bid"},
        },
    )

    with pytest.raises(CertificateVerificationError, match="quote.cost_source"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_wrong_cost_source_for_sell_yes():
    parents, action = actionable_graph(
        action_payload={"direction": "sell_yes"},
        parent_overrides={
            claims.CANDIDATE_EVIDENCE: {"direction": "sell_yes"},
            claims.QUOTE_FEASIBILITY: {"direction": "sell_yes", "cost_source": "native_orderbook_ask"},
            claims.COST_MODEL: {"direction": "sell_yes", "cost_source": "native_orderbook_ask"},
        },
    )

    with pytest.raises(CertificateVerificationError, match="quote.cost_source"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_low_price_yes_below_roi_frontier_confidence_floor():
    parents, action = actionable_graph(
        action_payload={
            "q_live": 0.65,
            "q_lcb_5pct": 0.55,
            "c_fee_adjusted": 0.54,
            "c_cost_95pct": 0.54,
            "p_fill_lcb": 0.95,
            "trade_score": 0.01,
            "action_score": 0.01,
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.65,
                "payoff_q_lcb": 0.55,
                "cost": 0.54,
                "edge_lcb": 0.01,
                "optimal_delta_u": 0.00009152233738979263,
                "delta_u_at_min": 0.00009152233738979263,
                "optimal_stake_usd": "1.4412832709285736083984375",
                "false_edge_rate": 0.05,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.55,
            },
        }
    )

    with pytest.raises(CertificateVerificationError, match="roi frontier not useful"):
        verify_actionable_trade(action, parents)


@pytest.mark.parametrize("bad_source", ["midpoint", "complement_price", "last_trade_price"])
def test_actionable_rejects_forbidden_cost_sources(bad_source):
    parents, action = actionable_graph(parent_overrides={claims.COST_MODEL: {"cost_source": bad_source}})

    with pytest.raises(CertificateVerificationError, match="cost.cost_source"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_fdr_family_mismatch():
    parents, action = actionable_graph(parent_overrides={claims.FDR: {"fdr_family_id": "other-family"}})

    with pytest.raises(CertificateVerificationError, match="actionable.family_id"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_fdr_missing_candidate_hypothesis():
    parents, action = actionable_graph(parent_overrides={claims.FDR: {"selected_hypotheses": ("other",)}})

    with pytest.raises(CertificateVerificationError, match="selected_hypotheses"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_kelly_cost_basis_mismatch():
    parents, action = actionable_graph(parent_overrides={claims.KELLY_DRY_RUN: {"cost_basis_id": "other-cost"}})

    with pytest.raises(CertificateVerificationError, match="kelly.cost_basis_id"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_risk_not_passed():
    parents, action = actionable_graph(parent_overrides={claims.RISK_LEVEL: {"passed": False}})

    with pytest.raises(CertificateVerificationError, match="risk.passed"):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_conservative_bootstrap_when_coverage_history_is_thin():
    parents, action = actionable_graph(
        parent_overrides={
            claims.CALIBRATION: {
                "authority": "FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB",
                "coverage_status": "INSUFFICIENT_DATA",
                "q_lcb_basis": "fused_center_bootstrap_p05",
                "bootstrap_draws": 200,
                "n_samples": 0,
            },
        },
    )

    verify_actionable_trade(action, parents)


def test_actionable_rejects_conservative_bootstrap_without_draws():
    parents, action = actionable_graph(
        parent_overrides={
            claims.CALIBRATION: {
                "authority": "FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB",
                "coverage_status": "INSUFFICIENT_DATA",
                "q_lcb_basis": "fused_center_bootstrap_p05",
                "bootstrap_draws": 10,
                "n_samples": 0,
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="bootstrap draw floor"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_fused_bootstrap_below_live_sample_floor():
    parents, action = actionable_graph(
        parent_overrides={
            claims.CALIBRATION: {
                "authority": "FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE",
                "coverage_status": "LICENSED",
                "n_samples": 3,
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="sample floor"):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_fused_bootstrap_with_sampled_license():
    parents, action = actionable_graph(
        parent_overrides={
            claims.CALIBRATION: {
                "authority": "FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE",
                "coverage_status": "LICENSED",
                "n_samples": 60,
            },
        },
    )

    verify_actionable_trade(action, parents)


def test_actionable_rejects_unreserved_live_cap():
    parents, action = actionable_graph(parent_overrides={claims.LIVE_CAP: {"reservation_status": "RELEASED"}})

    with pytest.raises(CertificateVerificationError, match="reservation_status"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_public_market_channel_fill_parent():
    parents, action = actionable_graph(extra_parent_payloads={claims.FILL: {"source_kind": claims.PUBLIC_MARKET_CHANNEL_SOURCE}})

    with pytest.raises(CertificateVerificationError, match="market-channel"):
        verify_actionable_trade(action, parents)


def test_ledger_rejects_forged_actionable_trade_certificate():
    parents, action = actionable_graph(action_payload={"trade_score": -1.0})

    with pytest.raises(CertificateVerificationError, match="trade_score"):
        DecisionCertificateLedger(_conn()).persist_all(parents + (action,))


def test_ledger_rejects_actionable_with_generic_verifier_only_path():
    _, action = actionable_graph(action_payload={"trade_score": -1.0})

    with pytest.raises(CertificateVerificationError, match="missing parent|trade_score"):
        DecisionCertificateLedger(_conn()).insert_idempotent(action)


def actionable_graph(
    *,
    mode: str = "LIVE",
    action_payload: dict | None = None,
    parent_overrides: dict[str, dict] | None = None,
    extra_parent_payloads: dict[str, dict] | None = None,
):
    parent_overrides = parent_overrides or {}
    parent_payloads = _parent_payloads()
    parent_payloads.update(extra_parent_payloads or {})
    parents = []
    for certificate_type, payload in parent_payloads.items():
        merged = {**payload, **parent_overrides.get(certificate_type, {})}
        parents.append(_cert(certificate_type, f"{certificate_type}:event-1", merged, mode="LIVE"))
    parent_tuple = tuple(parents)
    payload = {**_action_payload(), **(action_payload or {})}
    action = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1:candidate-1",
        payload,
        mode=mode,
        parents=parent_tuple,
    )
    return parent_tuple, action


def _parent_payloads() -> dict[str, dict]:
    return {
        claims.CLOCK_MODE: {"mode": "LIVE"},
        claims.CAUSAL_EVENT: {"event_id": "event-1", "causal_snapshot_id": "snap-1"},
        claims.SOURCE_TRUTH: {"event_id": "event-1", "source_status": "LIVE_ELIGIBLE"},
        claims.MARKET_TOPOLOGY: {"family_id": "family-1"},
        claims.FAMILY_CLOSURE: {"family_id": "family-1"},
        claims.FORECAST_AUTHORITY: {"snapshot_id": "snap-1"},
        claims.CALIBRATION: {"calibrator_model_key": "model-1"},
        claims.MODEL_CONFIG: {"calibrator_model_key": "model-1"},
        claims.BELIEF: {"forecast_snapshot_id": "snap-1"},
        claims.EXECUTABLE_SNAPSHOT: {"executable_snapshot_id": "exec-1", "condition_id": "condition-1", "token_id": "yes-1"},
        claims.QUOTE_FEASIBILITY: {
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "direction": "buy_yes",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
        },
        claims.COST_MODEL: {
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "direction": "buy_yes",
            "cost_basis_id": "cost-1",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
        },
        claims.PRE_TRADE_EVIDENCE: {"native_quote_available": True},
        claims.CANDIDATE_EVIDENCE: {
            "family_id": "family-1",
            "candidate_id": "candidate-1",
            "condition_id": "condition-1",
            "selected_token_id": "yes-1",
            "direction": "buy_yes",
            "hypothesis_id": "family-1:yes-1",
        },
        claims.TESTING_PROTOCOL: {"protocol": "live_canary"},
        claims.FDR: {"fdr_family_id": "family-1", "selected_hypotheses": ("family-1:yes-1",)},
        claims.KELLY_DRY_RUN: {"kelly_decision_id": "kelly-1", "cost_basis_id": "cost-1", "passed": True},
        claims.RISK_LEVEL: {"risk_decision_id": "risk-1", "passed": True},
        claims.LIVE_CAP: {
            "usage_id": "cap-1",
            "event_id": "event-1",
            "reservation_status": "RESERVED",
            "max_notional_usd": 5.0,
        },
    }


def _action_payload() -> dict:
    return {
        "event_id": "event-1",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "snap-1",
        "family_id": "family-1",
        "candidate_id": "candidate-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "direction": "buy_yes",
        "strategy_key": "center_buy",
        "executable_snapshot_id": "exec-1",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "c_cost_95pct": 0.45,
        "p_fill_lcb": 0.1,
        "trade_score": 0.2,
        "action_score": 0.2,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.7,
            "payoff_q_lcb": 0.6,
            "cost": 0.4,
            "edge_lcb": 0.2,
            "optimal_delta_u": 0.01,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 5.0,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.6,
        },
        "fdr_family_id": "family-1",
        "kelly_decision_id": "kelly-1",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-1",
        "live_cap_usage_id": "cap-1",
        "final_intent_id": "intent-1",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
    }


def _cert(certificate_type: str, semantic_key: str, payload: dict, *, mode: str = "LIVE", parents=()):
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode=mode,
        decision_time=NOW,
        source_available_at=NOW,
        agent_received_at=NOW,
        persisted_at=NOW,
        payload=payload,
        parent_edges=tuple(ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type) for parent in parents),
        parent_certificates=tuple(parents),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def _role(certificate_type: str) -> str:
    import re

    base = certificate_type.removesuffix("Certificate").replace("Evidence", "")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
