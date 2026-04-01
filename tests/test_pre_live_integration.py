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
        market_velocity_1h=-0.10, divergence_score=0.20
    )
    
    signal = evaluate_exit_triggers(pos, edge_ctx, hours_to_settlement=24.0)
    assert signal is not None
    assert signal.trigger == "MODEL_DIVERGENCE_PANIC"

from src.engine.cycle_runner import _execute_monitoring_phase, CycleArtifact
from src.state.portfolio import PortfolioState

class MockClob:
    paper_mode = True
    
    def get_best_bid_ask(self, tid):
        return 0.40, 0.42, 100, 100

class MockTracker:
    def __init__(self):
        self.exits = []
    def record_exit(self, position):
        self.exits.append(position)

def test_full_monitoring_pipeline(monkeypatch):
    pos = Position(
        trade_id="pos123", market_id="m1", city="Dallas", cluster="tx",
        target_date="2026-04-01", bin_label="70-75", direction="buy_yes",
        size_usd=100.0, entry_price=0.30, p_posterior=0.30, edge=0.0,
        entry_ci_width=0.05, entry_method="ens_member_counting"
    )
    portfolio = PortfolioState(bankroll=1000.0, positions=[pos])
    artifact = CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    tracker = MockTracker()
    
    # Mock refresh_position to return an EdgeContext that triggers divergent panic
    def mock_refresh(conn, clob, position):
        return EdgeContext(
            p_raw=np.array([]), p_cal=np.array([]), p_market=np.array([0.40]),
            p_posterior=0.20, forward_edge=-0.20, alpha=0.0,
            confidence_band_upper=0.05, confidence_band_lower=0.0,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap1", n_edges_found=1, n_edges_after_fdr=1,
            market_velocity_1h=-0.10, divergence_score=0.20
        )
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    
    # Run the cycle
    p_dirty, t_dirty = _execute_monitoring_phase(None, MockClob(), portfolio, artifact, tracker, {"monitors": 0, "exits": 0})
    
    assert p_dirty is True
    assert t_dirty is True
    assert len(tracker.exits) == 1
    assert "Model-Market divergence score 0.20" in tracker.exits[0].exit_reason
    assert len(portfolio.positions) == 0 # Closed

def test_refresh_position_true_metrics(monkeypatch):
    from src.engine.monitor_refresh import refresh_position
    
    pos = Position(
        trade_id="pos123", market_id="m1", city="Dallas", cluster="tx",
        target_date="2026-04-01", bin_label="70-75", direction="buy_yes",
        size_usd=100.0, entry_price=0.30, p_posterior=0.30, edge=0.0,
        entry_ci_width=0.05, entry_method="ens_member_counting", token_id="token1"
    )

    class MockConn:
        def execute(self, query, params):
            class MockCursor:
                def fetchone(self):
                    return {"price": 0.60} # Price 1h ago was 0.60
            return MockCursor()
    
    # Mock external price fetching
    monkeypatch.setattr("src.engine.monitor_refresh.get_current_yes_price", lambda *args, **kwargs: 0.40)
    monkeypatch.setattr("src.engine.monitor_refresh.recompute_native_probability", lambda *args, **kwargs: 0.40)
    
    edge_ctx = refresh_position(MockConn(), MockClob(), pos)
    
    assert edge_ctx.divergence_score == 0.0 # 0.40 - 0.40
    assert abs(edge_ctx.market_velocity_1h - (-0.20)) < 0.0001
    
    # Prove it triggers FLASH_CRASH_PANIC natively
    from src.execution.exit_triggers import evaluate_exit_triggers
    signal = evaluate_exit_triggers(pos, edge_ctx, hours_to_settlement=24.0)
    assert signal is not None
    assert signal.trigger == "FLASH_CRASH_PANIC"
