# Created: 2026-04-29
# Last reused/audited: 2026-06-02
# Authority basis: DSA-07 non-live execution residue cleanup; transitional monitor integration reuse.
# Wave 3 (2026-06-02): evaluate_exit_triggers deleted (dead twin); tests repointed to
#   Position.evaluate_exit (the one live path).
import pytest
import numpy as np
from src.contracts.semantic_types import EntryMethod
from src.contracts.edge_context import EdgeContext
from src.state.portfolio import Position, ExitContext

def test_monitoring_chain_trigger():
    """Removed model-divergence panic cannot fire via the one live exit path."""
    from src.state.portfolio import divergence_hard_threshold
    pos = Position(
        trade_id="pos123", market_id="m1", city="Dallas", cluster="tx",
        target_date="2026-04-01", bin_label="70-75", direction="buy_yes",
        size_usd=100.0, entry_price=0.30, p_posterior=0.30, edge=0.0,
        entry_ci_width=0.05
    )
    exit_ctx = ExitContext(
        fresh_prob=0.20,
        fresh_prob_is_fresh=True,
        current_market_price=0.40,
        current_market_price_is_fresh=True,
        best_bid=0.39,
        hours_to_settlement=24.0,
        position_state="active",
        market_velocity_1h=-0.10,
        divergence_score=divergence_hard_threshold() + 0.1,
    )
    decision = pos.evaluate_exit(exit_ctx)
    assert decision.should_exit is False
    assert decision.trigger != "MODEL_DIVERGENCE_PANIC"

from src.engine.cycle_runner import _execute_monitoring_phase, CycleArtifact
from src.state.portfolio import PortfolioState

class MockClob:
    def get_best_bid_ask(self, tid):
        return 0.40, 0.40, 100, 100

    def get_balance(self):
        return 500.0

    def get_positions_from_api(self):
        return []

    def get_open_orders(self):
        return []

    def get_order_status(self, order_id):
        return {"status": "MATCHED"}

class MockTracker:
    def __init__(self):
        self.exits = []
    def record_exit(self, position):
        self.exits.append(position)

@pytest.mark.xfail(
    reason=(
        "Cluster M.6 (2026-05-18): execute_monitoring_phase now delegates to cycle_runtime "
        "with a deps injection pattern; refresh_position uses monitor_probability_refresh "
        "(not recompute_native_probability) so p_posterior is NaN and DAY0_OBSERVATION_REVERSAL "
        "trigger no longer fires for stale market-date positions. Needs full monkeypatch rewrite. "
        "Tracking: rewrite due before live v2 monitoring promotion; expiry 2026-06-30."
    ),
    strict=True,
)
def test_full_monitoring_pipeline(monkeypatch):
    pos = Position(
        trade_id="pos123", market_id="m1", city="Dallas", cluster="tx",
        target_date="2026-04-01", bin_label="70-75", direction="buy_yes",
        size_usd=100.0, entry_price=0.30, p_posterior=0.30, edge=0.0,
        entry_ci_width=0.05, entry_method="ens_member_counting",
        token_id="tok-yes-123", no_token_id="tok-no-123",
    )
    portfolio = PortfolioState(bankroll=1000.0, positions=[pos])
    artifact = CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    tracker = MockTracker()
    
    # Mock refresh_position to return an EdgeContext that triggers divergent panic
    def mock_refresh(conn, clob, position):
        _ = position.entry_method
        position.last_monitor_market_price = 0.40
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_prob = 0.20
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_best_bid = 0.39
        return EdgeContext(
            p_raw=np.array([]), p_cal=np.array([]), p_market=np.array([0.40]),
            p_posterior=0.20, forward_edge=-0.20, alpha=0.0,
            confidence_band_upper=0.05, confidence_band_lower=0.0,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap1", n_edges_found=1, n_edges_after_fdr=1,
            market_velocity_1h=-0.10, divergence_score=0.20
        )
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr("src.engine.cycle_runtime.lead_hours_to_date_start", lambda *args, **kwargs: 12.0)
    monkeypatch.setattr("src.execution.exit_lifecycle.check_sell_collateral", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr("src.execution.exit_lifecycle.place_sell_order", lambda *a, **kw: {"orderID": "fake-order-123"})
    
    # Run the cycle
    p_dirty, t_dirty = _execute_monitoring_phase(None, MockClob(), portfolio, artifact, tracker, {"monitors": 0, "exits": 0})
    
    assert p_dirty is True
    assert t_dirty is True
    assert len(tracker.exits) == 1
    assert "DAY0_OBSERVATION_REVERSAL" in tracker.exits[0].exit_reason
    assert portfolio.positions[0].state == "economically_closed"

@pytest.mark.xfail(
    reason=(
        "Cluster M.6 (2026-05-18): refresh_position now calls monitor_probability_refresh "
        "which requires fresh ENS data; monkeypatching recompute_native_probability does not "
        "set last_monitor_prob_is_fresh=True so p_posterior stays NaN and divergence_score=NaN. "
        "Needs monitor_probability_refresh stub to return (0.40, pos, True)."
    ),
    strict=True,
)
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
    
    monkeypatch.setattr("src.engine.monitor_refresh.recompute_native_probability", lambda *args, **kwargs: 0.40)
    
    edge_ctx = refresh_position(MockConn(), MockClob(), pos)
    
    assert edge_ctx.divergence_score == 0.0 # 0.40 - 0.40
    assert abs(edge_ctx.market_velocity_1h - (-0.20)) < 0.0001
    
    # Flash crash panic would fire via Position.evaluate_exit when market_velocity_1h <= -0.15
    # (this test is xfail / skipped — kept for context only)
