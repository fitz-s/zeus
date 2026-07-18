# Created: 2026-07-01
# Last reused/audited: 2026-07-18
# Authority basis: current q-kernel final-entry economics and selected-side probability quality law.
from __future__ import annotations

import pytest

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps
from src.decision_kernel.canonicalization import qkernel_current_state_identity_hash
from src.execution.executor import (
    _actionable_certificate_intent_mismatch_reason,
    _entry_economics_component,
)


def _econ(**overrides) -> dict:
    payload = {
        "source": "qkernel_spine",
        "side": "YES",
        "payoff_q_point": 0.62,
        "payoff_q_lcb": 0.52,
        "cost": 0.4,
        "edge_lcb": 0.12,
        "delta_u_at_min": 0.01,
        "optimal_stake_usd": 10.0,
        "optimal_delta_u": 0.01,
        "false_edge_rate": 0.01,
        "direction_law_ok": True,
        "coherence_allows": True,
        "selection_guard_basis": "SELECTION_BETA_95",
        "selection_guard_abstained": False,
        "selection_guard_q_safe": 0.52,
    }
    payload.update(overrides)
    return payload


def _current_state_econ(**overrides) -> dict:
    current = dict(
        decision_id="decision-current-1",
        receipt_hash="receipt-current-1",
        q_version="q-current-1",
        sample_hash="current-sample-1",
        q_lcb_guard_basis="CURRENT_POSTERIOR_BAND",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="current-sample-1",
        selection_guard_basis="CURRENT_POSTERIOR_BAND",
        selection_guard_abstained=False,
        selection_guard_cell_key="current-sample-1",
        selection_guard_n=64,
        global_actuation_identity="global-current-1",
        global_optimum_semantics="CUT_TIME_GLOBAL_OPTIMUM",
        global_candidate_id="candidate-current-1",
        global_bin_id="bin-1",
        global_universe_witness_identity="universe-current-1",
        global_wealth_witness_identity="wealth-current-1",
        global_selection_epoch_identity="epoch-current-1",
        global_selection_cut_at="2026-07-13T02:00:00+00:00",
        global_selection_decision_at="2026-07-13T02:00:01+00:00",
        global_jit_book_hash="book-current-1",
        global_jit_venue_book_hash="venue-book-current-1",
        global_jit_book_snapshot_id="snapshot-current-1",
        global_jit_execution_curve_identity="curve-current-1",
        global_target_shares="1",
        global_limit_price="0.44",
        global_expected_fill_price_before_fee="0.44",
        global_expected_cost_usd="0.45",
        global_max_spend_usd="0.45",
        global_robust_delta_log_wealth=0.001,
        global_robust_ev_usd=0.15,
        global_cut_time_win_probability_lcb=0.60,
        global_cut_time_loss_probability_ucb=0.40,
        global_terminal_win_probability_lcb=0.60,
        global_terminal_loss_probability_ucb=0.40,
        global_terminal_loss_payoff_usd="-0.45",
        global_terminal_win_payoff_usd="0.55",
        global_terminal_median_payoff_usd="0.55",
        global_terminal_wealth_after_loss_usd="99.55",
        global_terminal_wealth_after_win_usd="100.55",
        global_cut_time_expected_value_diagnostic_usd=0.15,
        global_expected_value_diagnostic_usd=0.15,
        global_expected_value_semantics="DIAGNOSTIC_EXPECTATION_NOT_REALIZED_GAIN",
        global_terminal_payoff_semantics="BINARY_0_1",
    )
    current.update(overrides)
    payload = _econ(**current)
    for legacy_field in (
        "delta_u_at_min",
        "optimal_stake_usd",
        "optimal_delta_u",
        "false_edge_rate",
        "direction_law_ok",
        "coherence_allows",
    ):
        payload.pop(legacy_field, None)
    payload["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        payload
    )
    return payload


def _day0_econ(**overrides) -> dict:
    payload = _econ(
        q_lcb_guard_basis="DAY0_REMAINING_DAY_Q_LCB",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="day0_remaining_day_q_lcb",
        selection_guard_basis="DAY0_REMAINING_DAY_Q_LCB",
        selection_guard_abstained=False,
        selection_guard_cell_key="day0_remaining_day_q_lcb",
        selection_guard_n=80,
    )
    payload.update(overrides)
    return payload


def _day0_actionable_payload(
    *,
    q_live: float = 0.96,
    q_lcb: float = 0.91,
    remaining_models: int | None = 100,
) -> dict:
    payload = {
        "event_type": "DAY0_EXTREME_UPDATED",
        "condition_id": "condition-1",
        "direction": "buy_yes",
        "q_live": q_live,
        "q_lcb_5pct": q_lcb,
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
        "day0_probability_authority": {
            "q_source": "day0_remaining_day",
            "q_mode": "remaining_day",
            "remaining_models": remaining_models,
            "rounded_value": 20,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "lcb_transform": {
                "yes_lcb_by_condition": {"condition-1": q_lcb},
                "no_lcb_by_condition": {"condition-1": 0.02},
            },
        },
        "_edli_q_source": "day0_remaining_day",
        "_edli_day0_q_mode": "remaining_day",
        "_edli_day0_remaining_models": remaining_models,
        "_edli_day0_lcb_transform": {
            "yes_lcb_by_condition": {"condition-1": q_lcb},
            "no_lcb_by_condition": {"condition-1": 0.02},
        },
    }
    if remaining_models is None:
        payload["day0_probability_authority"].pop("remaining_models", None)
        payload.pop("_edli_day0_remaining_models", None)
    return payload


def _intent(**overrides) -> ExecutionIntent:
    payload = {
        "direction": Direction("buy_yes"),
        "target_size_usd": 9.0,
        "limit_price": 0.4,
        "toxicity_budget": 0.05,
        "max_slippage": SlippageBps(value_bps=0.0, direction="zero"),
        "is_sandbox": False,
        "market_id": "market-1",
        "token_id": "yes-token",
        "timeout_seconds": 60,
        "q_live": 0.62,
        "q_lcb_5pct": 0.52,
        "expected_edge": 0.10,
        "min_entry_price": 0.10,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": _econ(),
    }
    payload.update(overrides)
    return ExecutionIntent(**payload)


def test_entry_economics_blocks_lucknow_style_negative_submit_edge():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.12,
            q_live=0.13,
            q_lcb_5pct=0.115,
            expected_edge=-0.005,
            min_entry_price=0.10,
            qkernel_execution_economics=_econ(
                payoff_q_point=0.13,
                payoff_q_lcb=0.115,
                cost=0.12,
                edge_lcb=-0.005,
                false_edge_rate=1.0,
                selection_guard_q_safe=0.115,
            ),
        ),
        shares=100.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "expected_edge_non_positive"


def test_entry_economics_rejects_direct_qkernel_yes_below_center_buy_floor_even_when_roi_clear():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.05,
            q_live=0.82,
            q_lcb_5pct=0.72,
            expected_edge=0.67,
            min_entry_price=0.10,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            qkernel_execution_economics=_econ(
                route_id="DIRECT_YES:b20@proof",
                route_type="direct",
                payoff_q_point=0.82,
                payoff_q_lcb=0.72,
                cost=0.05,
                edge_lcb=0.67,
                selection_guard_q_safe=0.72,
            ),
        ),
        shares=1497.78,
        actionable_payload={
            "strategy_key": "center_buy",
            "direction": "buy_yes",
        },
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "limit_price_below_strategy_entry_floor"
    assert verdict["details"]["live_min_entry_price"] == 0.05
    assert verdict["details"]["effective_min_entry_price"] == 0.10
    assert verdict["details"]["qkernel_low_price_floor_authorized"] is True


def test_entry_economics_blocks_low_price_without_qkernel_selection_authority():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.05,
            q_live=0.82,
            q_lcb_5pct=0.72,
            expected_edge=0.67,
            min_entry_price=0.10,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            selection_authority_applied=None,
            qkernel_execution_economics=_econ(
                payoff_q_point=0.82,
                payoff_q_lcb=0.72,
                cost=0.05,
                edge_lcb=0.67,
                selection_guard_q_safe=0.72,
            ),
        ),
        shares=1497.78,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "limit_price_below_strategy_entry_floor"


def test_entry_economics_rejects_day0_without_qkernel_at_strategy_floor():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.10,
            q_live=0.50,
            q_lcb_5pct=0.20,
            expected_edge=0.10,
            min_entry_price=0.10,
            min_expected_profit_usd=0.50,
            min_submit_edge_density=0.05,
            selection_authority_applied=None,
            qkernel_execution_economics=None,
        ),
        shares=10.0,
        actionable_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "strategy_key": "center_buy",
            "direction": "buy_yes",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
        },
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "missing_entry_economics"
    assert "qkernel_execution_economics" in verdict["details"]["missing"]


def test_entry_economics_blocks_low_price_even_when_strategy_floor_allows_it():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.05,
            q_live=0.82,
            q_lcb_5pct=0.72,
            expected_edge=0.67,
            min_entry_price=0.005,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            selection_authority_applied=None,
            qkernel_execution_economics=_econ(
                payoff_q_point=0.82,
                payoff_q_lcb=0.72,
                cost=0.05,
                edge_lcb=0.67,
                selection_guard_q_safe=0.72,
            ),
        ),
        shares=1497.78,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "min_entry_price_below_live_floor"
    assert verdict["details"]["live_min_entry_price"] == 0.10


def test_entry_economics_rejects_micro_tail_yes_below_absolute_price_floor():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.024,
            q_live=0.20,
            q_lcb_5pct=0.074,
            expected_edge=0.050,
            min_entry_price=0.02,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            selection_authority_applied="qkernel_spine",
            qkernel_execution_economics=_econ(
                payoff_q_point=0.20,
                payoff_q_lcb=0.074,
                cost=0.024,
                edge_lcb=0.050,
                optimal_delta_u=0.01,
                selection_guard_q_safe=0.074,
            ),
        ),
        shares=30.0,
        actionable_payload={
            "strategy_key": "center_buy",
            "direction": "buy_yes",
        },
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "live_order_unit_price_out_of_bounds"


def test_entry_economics_rejects_buenos_aires_shape_below_absolute_price_floor():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.041,
            q_live=0.24833093804728934,
            q_lcb_5pct=0.0990451308919892,
            expected_edge=0.041246376484684766,
            min_entry_price=0.02,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            selection_authority_applied="qkernel_spine",
            qkernel_execution_economics=_econ(
                payoff_q_point=0.24833093804728934,
                payoff_q_lcb=0.0990451308919892,
                cost=0.057798754407304434,
                edge_lcb=0.041246376484684766,
                delta_u_at_min=0.0001,
                optimal_stake_usd=5.45956,
                optimal_delta_u=0.001,
                false_edge_rate=0.05,
                selection_guard_q_safe=0.0990451308919892,
            ),
        ),
        shares=133.16,
        actionable_payload={
            "strategy_key": "center_buy",
            "direction": "buy_yes",
        },
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "live_order_unit_price_out_of_bounds"


def test_entry_economics_allows_high_confidence_center_buy_yes():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.27,
            q_live=0.80,
            q_lcb_5pct=0.65,
            expected_edge=0.38,
            min_entry_price=0.10,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            selection_authority_applied="qkernel_spine",
            qkernel_execution_economics=_econ(
                payoff_q_point=0.80,
                payoff_q_lcb=0.65,
                cost=0.27,
                edge_lcb=0.38,
                delta_u_at_min=0.01,
                optimal_stake_usd=10.0,
                optimal_delta_u=0.02,
                false_edge_rate=0.01,
                selection_guard_q_safe=0.65,
            ),
        ),
        shares=10.0,
        actionable_payload={
            "strategy_key": "center_buy",
            "direction": "buy_yes",
        },
    )

    assert verdict["allowed"] is True
    assert verdict["details"]["expected_profit_usd"] == pytest.approx(3.8)


def test_entry_economics_allows_center_buy_yes_when_symmetric_quality_floor_clear():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.12,
            q_live=0.60,
            q_lcb_5pct=0.52,
            expected_edge=0.40,
            min_entry_price=0.02,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            selection_authority_applied="qkernel_spine",
            qkernel_execution_economics=_econ(
                payoff_q_point=0.60,
                payoff_q_lcb=0.52,
                cost=0.12,
                edge_lcb=0.40,
                delta_u_at_min=0.01,
                optimal_stake_usd=10.0,
                optimal_delta_u=0.02,
                false_edge_rate=0.01,
                selection_guard_q_safe=0.52,
            ),
        ),
        shares=10.0,
        actionable_payload={
            "strategy_key": "center_buy",
            "direction": "buy_yes",
        },
    )

    assert verdict["allowed"] is True
    assert verdict["details"]["q_lcb_5pct"] == pytest.approx(0.52)


def test_entry_economics_rejects_legacy_low_price_yes_below_absolute_price_floor():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.031,
            q_live=0.12180248510788458,
            q_lcb_5pct=0.06052567908958011,
            expected_edge=0.020510409830349664,
            min_entry_price=0.02,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            selection_authority_applied="qkernel_spine",
            qkernel_execution_economics=_econ(
                payoff_q_point=0.12180248510788458,
                payoff_q_lcb=0.06052567908958011,
                cost=0.04001526925923045,
                edge_lcb=0.020510409830349664,
                delta_u_at_min=0.00009152233738979263,
                optimal_stake_usd=1.4412832709285736,
                optimal_delta_u=0.0006333828915951036,
                selection_guard_q_safe=0.06052567908958011,
            ),
        ),
        shares=46.49,
        actionable_payload={
            "strategy_key": "center_buy",
            "direction": "buy_yes",
        },
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "live_order_unit_price_out_of_bounds"


def test_entry_economics_blocks_unarmed_selection_guard_even_with_large_raw_edge():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.11,
            q_live=0.24,
            q_lcb_5pct=0.18,
            expected_edge=0.07,
            min_entry_price=0.10,
            qkernel_execution_economics=_econ(
                payoff_q_point=0.24,
                payoff_q_lcb=0.18,
                cost=0.11,
                edge_lcb=0.07,
                selection_guard_basis="SIDE_NOT_ARMED",
                selection_guard_abstained=True,
                selection_guard_q_safe=0.0,
            ),
        ),
        shares=300.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "qkernel_selection_guard_abstained"


def test_entry_economics_blocks_missing_selection_guard():
    econ = _econ()
    econ.pop("selection_guard_basis")
    econ.pop("selection_guard_abstained")
    econ.pop("selection_guard_q_safe")
    verdict = _entry_economics_component(
        _intent(qkernel_execution_economics=econ),
        shares=10.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "qkernel_selection_guard_missing"


def test_entry_economics_blocks_missing_receipt_fields():
    verdict = _entry_economics_component(
        _intent(
            q_live=None,
            q_lcb_5pct=None,
            expected_edge=None,
            qkernel_execution_economics=None,
        ),
        shares=10.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "missing_entry_economics"
    assert "qkernel_execution_economics" in verdict["details"]["missing"]


def test_entry_economics_accepts_day0_observation_authority_with_qkernel():
    verdict = _entry_economics_component(
        _intent(
            q_live=0.96,
            q_lcb_5pct=0.91,
            limit_price=0.70,
            expected_edge=0.21,
            min_entry_price=0.10,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            qkernel_execution_economics=_day0_econ(
                payoff_q_point=0.96,
                payoff_q_lcb=0.91,
                cost=0.70,
                edge_lcb=0.21,
                selection_guard_q_safe=0.91,
            ),
        ),
        shares=10.0,
        actionable_payload=_day0_actionable_payload(),
    )

    assert verdict["allowed"] is True
    assert verdict["details"]["qkernel_source"] == "qkernel_spine"
    assert verdict["details"]["day0_observation_authority"] is True
    assert abs(verdict["details"]["submit_edge"] - 0.21) < 1e-9


def test_entry_economics_blocks_day0_observed_boundary_entry_guard():
    verdict = _entry_economics_component(
        _intent(
            q_live=0.90,
            q_lcb_5pct=0.80,
            limit_price=0.44,
            expected_edge=0.25,
            min_entry_price=0.10,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            qkernel_execution_economics=_day0_econ(
                payoff_q_point=0.90,
                payoff_q_lcb=0.80,
                cost=0.55,
                edge_lcb=0.25,
                selection_guard_basis="DAY0_OBSERVED_BOUNDARY",
                q_lcb_guard_basis="DAY0_OBSERVED_BOUNDARY",
                selection_guard_cell_key="day0_observed_boundary",
                q_lcb_guard_cell_key="day0_observed_boundary",
                selection_guard_q_safe=0.80,
            ),
        ),
        shares=40.25,
        actionable_payload=_day0_actionable_payload(q_live=0.90, q_lcb=0.80),
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "day0_qkernel_guard_authority_missing"
    assert "DAY0_OBSERVED_BOUNDARY" in verdict["details"]["day0_qkernel_guard_error"]


def test_entry_economics_blocks_day0_degenerate_remaining_window_lcb():
    verdict = _entry_economics_component(
        _intent(
            q_live=0.960232579405669,
            q_lcb_5pct=0.960232573644274,
            limit_price=0.70,
            expected_edge=0.25,
            min_entry_price=0.10,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            qkernel_execution_economics=_day0_econ(
                payoff_q_point=0.960232579405669,
                payoff_q_lcb=0.960232573644274,
                cost=0.70,
                edge_lcb=0.260232573644274,
                selection_guard_q_safe=0.960232573644274,
            ),
        ),
        shares=10.0,
        actionable_payload=_day0_actionable_payload(
            q_live=0.960232579405669,
            q_lcb=0.960232573644274,
            remaining_models=3,
        ),
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "day0_probability_authority_missing"
    assert "degenerate with q_live" in verdict["details"]["error"]


def test_entry_economics_accepts_day0_selection_guard_without_oof_sample_count():
    verdict = _entry_economics_component(
        _intent(
            q_live=0.70,
            q_lcb_5pct=0.60,
            limit_price=0.40,
            expected_edge=0.20,
            min_entry_price=0.10,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            qkernel_execution_economics=_day0_econ(
                payoff_q_point=0.70,
                payoff_q_lcb=0.60,
                cost=0.40,
                edge_lcb=0.20,
                selection_guard_n=0,
                selection_guard_q_safe=0.60,
            ),
        ),
        shares=10.0,
        actionable_payload=_day0_actionable_payload(
            q_live=0.70,
            q_lcb=0.60,
            remaining_models=80,
        ),
    )

    assert verdict["allowed"] is True
    assert verdict["details"]["day0_observation_authority"] is True
    assert verdict["details"]["qkernel_source"] == "qkernel_spine"


def test_entry_economics_blocks_day0_without_remaining_window_authority_support():
    verdict = _entry_economics_component(
        _intent(
            q_live=0.70,
            q_lcb_5pct=0.60,
            limit_price=0.40,
            expected_edge=0.20,
            min_entry_price=0.10,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            qkernel_execution_economics=_day0_econ(
                payoff_q_point=0.70,
                payoff_q_lcb=0.60,
                cost=0.40,
                edge_lcb=0.20,
                selection_guard_n=0,
                selection_guard_q_safe=0.60,
            ),
        ),
        shares=10.0,
        actionable_payload=_day0_actionable_payload(
            q_live=0.70,
            q_lcb=0.60,
            remaining_models=None,
        ),
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "day0_probability_authority_missing"
    assert "remaining_day_models missing" in verdict["details"]["error"]


def test_entry_economics_allows_positive_side_matched_edge():
    verdict = _entry_economics_component(_intent(), shares=10.0)

    assert verdict["allowed"] is True
    assert abs(verdict["details"]["submit_edge"] - 0.12) < 1e-9
    assert abs(verdict["details"]["expected_profit_usd"] - 1.2) < 1e-9


def test_entry_economics_blocks_qkernel_point_belief_below_served_belief():
    verdict = _entry_economics_component(
        _intent(
            qkernel_execution_economics=_econ(
                payoff_q_point=0.61,
                payoff_q_lcb=0.52,
                cost=0.4,
                edge_lcb=0.12,
            )
        ),
        shares=10.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "qkernel_payoff_q_point_mismatch_q_live"


def test_entry_economics_blocks_qkernel_lcb_below_served_belief_lcb():
    verdict = _entry_economics_component(
        _intent(
            qkernel_execution_economics=_econ(
                payoff_q_point=0.62,
                payoff_q_lcb=0.51,
                cost=0.4,
                edge_lcb=0.11,
            )
        ),
        shares=10.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "qkernel_payoff_q_lcb_mismatch_q_lcb"


def test_entry_economics_allows_maker_price_improvement_below_qkernel_cost_basis():
    verdict = _entry_economics_component(
        _intent(
            direction=Direction("buy_no"),
            limit_price=0.74,
            q_live=0.8844532853426623,
            q_lcb_5pct=0.8165490165359833,
            expected_edge=0.06654901653598333,
            qkernel_execution_economics=_econ(
                side="NO",
                payoff_q_point=0.8844532853426623,
                payoff_q_lcb=0.8165490165359833,
                cost=0.75,
                edge_lcb=0.06654901653598333,
                optimal_delta_u=0.003585897887688278,
                false_edge_rate=0.00024993751562109475,
                selection_guard_q_safe=0.8165490165359833,
            ),
        ),
        shares=20.79,
    )

    assert verdict["allowed"] is True
    assert verdict["details"]["qkernel_cost"] == 0.75
    assert verdict["details"]["limit_price"] == 0.74
    assert verdict["details"]["submit_edge"] > verdict["details"]["qkernel_edge_lcb"]


def test_entry_economics_blocks_submit_price_worse_than_qkernel_cost_basis():
    verdict = _entry_economics_component(
        _intent(
            direction=Direction("buy_no"),
            limit_price=0.76,
            q_live=0.83,
            q_lcb_5pct=0.82,
            expected_edge=0.04,
            qkernel_execution_economics=_econ(
                side="NO",
                payoff_q_point=0.83,
                payoff_q_lcb=0.80,
                cost=0.75,
                edge_lcb=0.05,
                optimal_delta_u=0.003,
                selection_guard_q_safe=0.80,
            ),
        ),
        shares=20.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "submit_price_worse_than_qkernel_cost"


def test_entry_economics_allows_global_multilevel_limit_bound_by_max_spend():
    verdict = _entry_economics_component(
        _intent(
            direction=Direction("buy_no"),
            limit_price=0.33,
            q_live=0.650529153286516,
            q_lcb_5pct=0.607276716608429,
            expected_edge=0.277276716608429,
            qkernel_execution_economics=_econ(
                side="NO",
                payoff_q_point=0.650529153286516,
                payoff_q_lcb=0.607276716608429,
                cost=0.324617595758041,
                edge_lcb=0.282659120850388,
                selection_guard_q_safe=0.607276716608429,
                global_actuation_identity="global-wellington-no",
                global_limit_price="0.33",
                global_target_shares="326.00",
                global_max_spend_usd="107.5800000",
            ),
        ),
        shares=326.0,
    )

    assert verdict["allowed"] is True
    assert verdict["details"]["global_limit_bound_authorized"] is True


def test_entry_economics_rejects_global_limit_without_matching_max_spend():
    verdict = _entry_economics_component(
        _intent(
            direction=Direction("buy_no"),
            limit_price=0.33,
            q_live=0.650529153286516,
            q_lcb_5pct=0.607276716608429,
            expected_edge=0.277276716608429,
            qkernel_execution_economics=_econ(
                side="NO",
                payoff_q_point=0.650529153286516,
                payoff_q_lcb=0.607276716608429,
                cost=0.324617595758041,
                edge_lcb=0.282659120850388,
                selection_guard_q_safe=0.607276716608429,
                global_actuation_identity="global-wellington-no",
                global_limit_price="0.33",
                global_target_shares="326.00",
                global_max_spend_usd="100.00",
            ),
        ),
        shares=326.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "submit_price_worse_than_qkernel_cost"


def test_entry_economics_blocks_weak_jeddah_style_expensive_no_density():
    verdict = _entry_economics_component(
        _intent(
            direction=Direction("buy_no"),
            limit_price=0.95,
            q_live=0.96,
            q_lcb_5pct=0.956,
            expected_edge=0.006,
            qkernel_execution_economics=_econ(
                side="NO",
                payoff_q_point=0.96,
                payoff_q_lcb=0.956,
                cost=0.95,
                edge_lcb=0.006,
                selection_guard_q_safe=0.986,
            ),
        ),
        shares=21.99,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "submit_edge_density_below_floor"


def test_entry_economics_blocks_thin_margin_live_profile_order():
    verdict = _entry_economics_component(
        _intent(
            direction=Direction("buy_no"),
            limit_price=0.77,
            q_live=0.804346,
            q_lcb_5pct=0.794346,
            expected_edge=0.014346,
            min_expected_profit_usd=1.00,
            min_submit_edge_density=0.05,
            qkernel_execution_economics=_econ(
                side="NO",
                payoff_q_point=0.804346,
                payoff_q_lcb=0.794346,
                cost=0.78,
                edge_lcb=0.014346,
                optimal_delta_u=0.002,
                selection_guard_q_safe=0.794346,
            ),
        ),
        shares=18.14,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "expected_profit_below_floor"
    assert verdict["details"]["expected_profit_usd"] < 1.0
    assert verdict["details"]["submit_edge_density"] < 0.05


@pytest.mark.parametrize(
    ("direction", "side"),
    ((Direction("buy_yes"), "YES"), (Direction("buy_no"), "NO")),
)
def test_entry_economics_current_state_winner_ignores_legacy_profit_density_floors(
    direction,
    side,
):
    economics = _current_state_econ(
        side=side,
        payoff_q_point=0.80,
        payoff_q_lcb=0.60,
        cost=0.45,
        edge_lcb=0.15,
        optimal_stake_usd=0.01,
        selection_guard_q_safe=0.60,
    )
    for legacy_field in (
        "route_id",
        "route_type",
        "delta_u_at_min",
        "optimal_stake_usd",
        "optimal_delta_u",
    ):
        economics.pop(legacy_field, None)
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )
    verdict = _entry_economics_component(
        _intent(
            direction=direction,
            limit_price=0.44,
            q_live=0.80,
            q_lcb_5pct=0.60,
            expected_edge=0.15,
            min_entry_price=0.10,
            min_expected_profit_usd=1000.0,
            min_submit_edge_density=1000.0,
            qkernel_execution_economics=economics,
        ),
        shares=1.0,
        actionable_payload={"qkernel_execution_economics": economics},
    )

    assert verdict["allowed"] is True


@pytest.mark.parametrize(
    ("direction", "side"),
    ((Direction("buy_yes"), "YES"), (Direction("buy_no"), "NO")),
)
@pytest.mark.parametrize(
    ("price", "q_lcb", "edge", "shares"),
    ((0.001, 0.80, 0.799, 1000.0), (0.999, 1.0, 0.001, 10.0)),
)
def test_entry_economics_current_state_cannot_waive_absolute_price_floor(
    direction,
    side,
    price,
    q_lcb,
    edge,
    shares,
):
    economics = _current_state_econ(
        side=side,
        payoff_q_point=max(0.92, q_lcb),
        payoff_q_lcb=q_lcb,
        cost=price,
        edge_lcb=edge,
        selection_guard_q_safe=q_lcb,
        global_limit_price=str(price),
        global_expected_fill_price_before_fee=str(price),
        global_expected_cost_usd=str(price * shares),
        global_max_spend_usd=str(price * shares),
        global_target_shares=str(shares),
        global_robust_delta_log_wealth=0.10,
        global_robust_ev_usd=edge * shares,
        global_cut_time_win_probability_lcb=q_lcb,
        global_cut_time_loss_probability_ucb=1.0 - q_lcb,
        global_terminal_win_probability_lcb=q_lcb,
        global_terminal_loss_probability_ucb=1.0 - q_lcb,
        global_terminal_loss_payoff_usd=str(-(price * shares)),
        global_terminal_win_payoff_usd=str((1.0 - price) * shares),
        global_terminal_median_payoff_usd=str((1.0 - price) * shares),
        global_terminal_wealth_after_loss_usd=str(100.0 - price * shares),
        global_terminal_wealth_after_win_usd=str(100.0 + (1.0 - price) * shares),
        global_cut_time_expected_value_diagnostic_usd=edge * shares,
        global_expected_value_diagnostic_usd=edge * shares,
    )
    verdict = _entry_economics_component(
        _intent(
            direction=direction,
            limit_price=price,
            q_live=max(0.92, q_lcb),
            q_lcb_5pct=q_lcb,
            expected_edge=edge,
            min_entry_price=0.10,
            executable_snapshot_min_tick_size="0.001",
            qkernel_execution_economics=economics,
        ),
        shares=shares,
        actionable_payload={"qkernel_execution_economics": economics},
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "live_order_unit_price_out_of_bounds"


@pytest.mark.parametrize("direction", (Direction("buy_yes"), Direction("buy_no")))
def test_entry_economics_current_state_cannot_waive_venue_tick_boundary(direction):
    economics = _current_state_econ(
        side="YES" if direction == Direction("buy_yes") else "NO",
        payoff_q_point=0.92,
        payoff_q_lcb=0.80,
        cost=0.0009,
        edge_lcb=0.7991,
        selection_guard_q_safe=0.80,
        global_limit_price="0.0009",
        global_expected_fill_price_before_fee="0.0009",
        global_expected_cost_usd="0.9",
        global_max_spend_usd="0.9",
        global_target_shares="1000",
        global_robust_delta_log_wealth=0.10,
        global_robust_ev_usd=799.1,
        global_cut_time_win_probability_lcb=0.80,
        global_cut_time_loss_probability_ucb=0.20,
        global_terminal_win_probability_lcb=0.80,
        global_terminal_loss_probability_ucb=0.20,
        global_terminal_loss_payoff_usd="-0.9",
        global_terminal_win_payoff_usd="999.1",
        global_terminal_median_payoff_usd="999.1",
        global_terminal_wealth_after_loss_usd="99.1",
        global_terminal_wealth_after_win_usd="1099.1",
        global_cut_time_expected_value_diagnostic_usd=799.1,
        global_expected_value_diagnostic_usd=799.1,
    )
    verdict = _entry_economics_component(
        _intent(
            direction=direction,
            limit_price=0.0009,
            q_live=0.92,
            q_lcb_5pct=0.80,
            expected_edge=0.7991,
            min_entry_price=0.0,
            executable_snapshot_min_tick_size="0.001",
            qkernel_execution_economics=economics,
        ),
        shares=1000.0,
        actionable_payload={"qkernel_execution_economics": economics},
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "live_order_unit_price_out_of_bounds"


def test_recomputed_current_state_marker_cannot_bypass_durable_legacy_certificate():
    durable = _econ(
        payoff_q_point=0.80,
        payoff_q_lcb=0.60,
        cost=0.45,
        edge_lcb=0.15,
        selection_guard_q_safe=0.60,
    )
    forged = _current_state_econ(
        payoff_q_point=0.80,
        payoff_q_lcb=0.60,
        cost=0.45,
        edge_lcb=0.15,
        selection_guard_q_safe=0.60,
    )
    intent = _intent(
        limit_price=0.44,
        q_live=0.80,
        q_lcb_5pct=0.60,
        expected_edge=0.15,
        min_entry_price=0.95,
        min_expected_profit_usd=1000.0,
        min_submit_edge_density=1000.0,
        qkernel_execution_economics=forged,
    )
    actionable = {
        "token_id": "yes-token",
        "direction": "buy_yes",
        "q_live": 0.80,
        "q_lcb_5pct": 0.60,
        "qkernel_execution_economics": durable,
    }

    assert _actionable_certificate_intent_mismatch_reason(actionable, intent) == (
        "actionable_certificate_qkernel_current_state_missing"
    )
    verdict = _entry_economics_component(
        intent,
        shares=1.0,
        actionable_payload=actionable,
    )
    assert verdict["allowed"] is False
    assert verdict["reason"] == "limit_price_below_strategy_entry_floor"

    durable_current = {**actionable, "qkernel_execution_economics": forged}
    legacy_intent = _intent(
        limit_price=0.44,
        q_live=0.80,
        q_lcb_5pct=0.60,
        expected_edge=0.15,
        qkernel_execution_economics=durable,
    )
    assert _actionable_certificate_intent_mismatch_reason(
        durable_current,
        legacy_intent,
    ) == "actionable_certificate_qkernel_current_state_downgrade"


def test_entry_economics_blocks_non_qkernel_or_self_reported_economics():
    verdict = _entry_economics_component(
        _intent(
            qkernel_execution_economics={
                "side": "YES",
                "payoff_q_point": 0.62,
                "payoff_q_lcb": 0.52,
                "direction_law_ok": True,
                "coherence_allows": True,
            },
        ),
        shares=10.0,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "qkernel_source_missing"
