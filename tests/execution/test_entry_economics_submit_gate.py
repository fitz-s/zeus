from __future__ import annotations

import pytest

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps
from src.execution.executor import _entry_economics_component


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


def _day0_actionable_payload(*, q_lcb: float = 0.91, remaining_models: int | None = 100) -> dict:
    payload = {
        "event_type": "DAY0_EXTREME_UPDATED",
        "condition_id": "condition-1",
        "direction": "buy_yes",
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
            limit_price=0.006,
            q_live=0.82,
            q_lcb_5pct=0.72,
            expected_edge=0.714,
            min_entry_price=0.10,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            qkernel_execution_economics=_econ(
                route_id="DIRECT_YES:b20@proof",
                route_type="direct",
                payoff_q_point=0.82,
                payoff_q_lcb=0.72,
                cost=0.006,
                edge_lcb=0.714,
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
    assert verdict["details"]["live_min_entry_price"] == 0.02
    assert verdict["details"]["effective_min_entry_price"] == 0.02
    assert verdict["details"]["qkernel_low_price_floor_authorized"] is True


def test_entry_economics_blocks_low_price_without_qkernel_selection_authority():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.006,
            q_live=0.82,
            q_lcb_5pct=0.72,
            expected_edge=0.714,
            min_entry_price=0.10,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            selection_authority_applied=None,
            qkernel_execution_economics=_econ(
                payoff_q_point=0.82,
                payoff_q_lcb=0.72,
                cost=0.006,
                edge_lcb=0.714,
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
            limit_price=0.006,
            q_live=0.82,
            q_lcb_5pct=0.72,
            expected_edge=0.714,
            min_entry_price=0.005,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            selection_authority_applied=None,
            qkernel_execution_economics=_econ(
                payoff_q_point=0.82,
                payoff_q_lcb=0.72,
                cost=0.006,
                edge_lcb=0.714,
                selection_guard_q_safe=0.72,
            ),
        ),
        shares=1497.78,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "min_entry_price_below_live_floor"
    assert verdict["details"]["live_min_entry_price"] == 0.10


def test_entry_economics_blocks_center_buy_micro_tail_yes_below_quality_floor():
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
    assert verdict["reason"].startswith("ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR:")
    assert verdict["details"]["q_lcb_5pct"] == pytest.approx(0.074)


def test_entry_economics_blocks_buenos_aires_tail_yes_live_incident():
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
    assert verdict["reason"].startswith("ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR:")
    assert verdict["details"]["q_lcb_5pct"] == pytest.approx(0.0990451308919892)


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


def test_entry_economics_allows_center_buy_yes_below_binary_floor_when_quality_clear():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.12,
            q_live=0.36,
            q_lcb_5pct=0.30,
            expected_edge=0.18,
            min_entry_price=0.02,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            selection_authority_applied="qkernel_spine",
            qkernel_execution_economics=_econ(
                payoff_q_point=0.36,
                payoff_q_lcb=0.30,
                cost=0.12,
                edge_lcb=0.18,
                delta_u_at_min=0.01,
                optimal_stake_usd=10.0,
                optimal_delta_u=0.02,
                false_edge_rate=0.01,
                selection_guard_q_safe=0.30,
            ),
        ),
        shares=10.0,
        actionable_payload={
            "strategy_key": "center_buy",
            "direction": "buy_yes",
        },
    )

    assert verdict["allowed"] is True
    assert verdict["details"]["q_lcb_5pct"] == pytest.approx(0.30)


def test_entry_economics_blocks_low_price_yes_below_center_yes_quality_floor():
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
    assert verdict["reason"].startswith("ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR:")
    assert verdict["details"]["q_lcb_5pct"] == pytest.approx(0.06052567908958011)


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
        actionable_payload=_day0_actionable_payload(q_lcb=0.80),
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
        actionable_payload=_day0_actionable_payload(q_lcb=0.60, remaining_models=80),
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
        actionable_payload=_day0_actionable_payload(q_lcb=0.60, remaining_models=None),
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


def test_entry_economics_blocks_weak_jeddah_style_expensive_no_density():
    verdict = _entry_economics_component(
        _intent(
            direction=Direction("buy_no"),
            limit_price=0.98,
            q_live=0.99,
            q_lcb_5pct=0.986,
            expected_edge=0.006,
            qkernel_execution_economics=_econ(
                side="NO",
                payoff_q_point=0.99,
                payoff_q_lcb=0.986,
                cost=0.98,
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
