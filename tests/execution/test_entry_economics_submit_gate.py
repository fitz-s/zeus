from __future__ import annotations

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps
from src.execution.executor import _entry_economics_component


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
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
        "qkernel_execution_economics": {
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
        },
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
            qkernel_execution_economics={
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.005426579861923467,
                "payoff_q_lcb": 0.005426579861923467,
                "cost": 0.006,
                "edge_lcb": -0.0005734201380765332,
                "optimal_delta_u": 0.01,
                "false_edge_rate": 1.0,
                "direction_law_ok": True,
                "coherence_allows": True,
            },
        ),
        shares=1497.78,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "expected_edge_non_positive"


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


def test_entry_economics_blocks_weak_jeddah_style_expensive_no_density():
    verdict = _entry_economics_component(
        _intent(
            direction=Direction("buy_no"),
            limit_price=0.98,
            q_live=0.99,
            q_lcb_5pct=0.986,
            expected_edge=0.006,
            qkernel_execution_economics={
                "source": "qkernel_spine",
                "side": "NO",
                "payoff_q_point": 0.99,
                "payoff_q_lcb": 0.986,
                "cost": 0.98,
                "edge_lcb": 0.006,
                "optimal_delta_u": 0.01,
                "false_edge_rate": 0.01,
                "direction_law_ok": True,
                "coherence_allows": True,
            },
        ),
        shares=21.99,
    )

    assert verdict["allowed"] is False
    assert verdict["reason"] == "submit_edge_density_below_floor"


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
