import pytest
import numpy as np
from src.contracts.semantic_types import Direction, EntryMethod
from src.contracts.execution_intent import ExecutionIntent
from src.contracts.edge_context import EdgeContext
from src.state.portfolio import Position
from src.execution.exit_triggers import evaluate_exit_triggers
from src.execution.executor import execute_intent

def test_execution_intent_schema():
    intent = ExecutionIntent(
        direction=Direction("buy_no"),
        target_size_usd=100.0,
        limit_price=0.45,
        toxicity_budget=0.05,
        max_slippage=0.02,
        is_sandbox=True,
        market_id="m123",
        token_id="t123",
        timeout_seconds=3600,
        slice_policy="iceberg",
        reprice_policy="static",
        liquidity_guard=True
    )
    assert intent.limit_price == 0.45
    
    result = execute_intent(intent, edge_vwmp=0.44, label="test")
    assert result.status == "filled"
    assert result.shares > 0

def test_monitoring_chain_trigger():
    pos = Position(
        trade_id="pos123", market_id="m1", city="Dallas", cluster="tx",
        target_date="2026-04-01", bin_label="70-75", direction="buy_yes",
        size_usd=100.0, entry_price=0.30, p_posterior=0.30, edge=0.0,
        entry_ci_width=0.05
    )
    edge_ctx = EdgeContext(
        p_raw=np.array([]), p_cal=np.array([]), p_market=np.array([0.40]),
        p_posterior=0.20, forward_edge=-0.20, alpha=0.0,
        confidence_band_upper=0.05, confidence_band_lower=0.0,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="snap1", n_edges_found=1, n_edges_after_fdr=1,
        market_velocity_1h=0.0, divergence_score=0.20 # High divergence!
    )
    
    signal = evaluate_exit_triggers(pos, edge_ctx, hours_to_settlement=24.0)
    assert signal is not None
    assert signal.trigger == "MODEL_DIVERGENCE_PANIC"
