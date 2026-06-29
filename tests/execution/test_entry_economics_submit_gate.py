from __future__ import annotations

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
        "min_entry_price": 0.05,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
        "qkernel_execution_economics": _econ(),
    }
    payload.update(overrides)
    return ExecutionIntent(**payload)


def test_entry_economics_blocks_lucknow_style_negative_submit_edge():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.006,
            q_live=0.005426579861923467,
            q_lcb_5pct=0.005426579861923467,
            expected_edge=-0.0019288776308719231,
            min_entry_price=0.0,
            qkernel_execution_economics=_econ(
                payoff_q_point=0.005426579861923467,
                payoff_q_lcb=0.005426579861923467,
                cost=0.006,
                edge_lcb=-0.0005734201380765332,
                false_edge_rate=1.0,
                selection_guard_q_safe=0.005426579861923467,
            ),
        ),
        shares=1497.78,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "expected_edge_non_positive"


def test_entry_economics_allows_low_price_when_qkernel_economics_clear_floors():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.006,
            q_live=0.82,
            q_lcb_5pct=0.72,
            expected_edge=0.714,
            min_entry_price=0.05,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
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

    assert verdict["allowed"] is True
    assert verdict["details"]["submit_edge_density"] > 0.05
    assert verdict["details"]["expected_profit_usd"] > 1.0


def test_entry_economics_blocks_unarmed_selection_guard_even_with_large_raw_edge():
    verdict = _entry_economics_component(
        _intent(
            limit_price=0.003,
            q_live=0.24,
            q_lcb_5pct=0.18,
            expected_edge=0.177,
            min_entry_price=0.0,
            qkernel_execution_economics=_econ(
                payoff_q_point=0.24,
                payoff_q_lcb=0.18,
                cost=0.003,
                edge_lcb=0.177,
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


def test_entry_economics_allows_positive_side_matched_edge():
    verdict = _entry_economics_component(_intent(), shares=10.0)

    assert verdict["allowed"] is True
    assert abs(verdict["details"]["submit_edge"] - 0.12) < 1e-9
    assert abs(verdict["details"]["expected_profit_usd"] - 1.2) < 1e-9


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
