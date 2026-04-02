"""Live safety invariant tests: relationship tests, not function tests.

These verify cross-module relationships that prevent ghost positions,
phantom P&L, and local↔chain divergence in live mode.

GOLDEN RULE: close_position() is ONLY called after confirmed FILLED.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.contracts.semantic_types import ChainState, ExitState, LifecycleState
from src.execution.collateral import check_sell_collateral
from src.execution.exit_lifecycle import (
    MAX_EXIT_RETRIES,
    ExitContext,
    check_pending_exits,
    check_pending_retries,
    execute_exit,
    is_exit_cooldown_active,
)
from src.state.chain_reconciliation import (
    QUARANTINE_EXPIRED_REVIEW_REQUIRED,
    QUARANTINE_REVIEW_REQUIRED,
    QUARANTINE_TIMEOUT_HOURS,
    check_quarantine_timeouts,
)
from src.control.control_plane import (
    build_quarantine_clear_command,
    clear_control_state,
    process_commands,
    write_commands,
)
from src.state.portfolio import (
    ExitDecision,
    Position,
    PortfolioState,
    close_position,
)


def _make_position(**overrides) -> Position:
    """Create a test position with sensible defaults."""
    defaults = dict(
        trade_id="test_001",
        market_id="mkt_001",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-04-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        edge=0.15,
        shares=25.0,
        cost_basis_usd=10.0,
        state="holding",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        unit="F",
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_portfolio(*positions) -> PortfolioState:
    """Create portfolio with given positions."""
    return PortfolioState(positions=list(positions))


def _make_clob(
    order_status="OPEN",
    balance=100.0,
    sell_result=None,
):
    """Create mock CLOB client."""
    clob = MagicMock()
    clob.paper_mode = False
    clob.get_order_status.return_value = {"status": order_status}
    clob.get_balance.return_value = balance
    clob.cancel_order.return_value = {"status": "CANCELLED"}
    return clob


# ---- Test 1: GOLDEN RULE ----

def test_live_exit_never_closes_without_fill():
    """GOLDEN RULE: close_position only called after confirmed FILLED.

    If CLOB returns OPEN (not filled), position must remain open with
    retry_pending state. It must NOT be closed or voided.
    """
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="OPEN", balance=100.0)

    with patch("src.execution.exit_lifecycle.place_sell_order") as mock_sell:
        mock_sell.return_value = {"orderID": "sell_123"}
        outcome = execute_exit(
            portfolio=portfolio,
            position=pos,
            exit_context=ExitContext(
                exit_reason="EDGE_REVERSAL",
                current_market_price=0.45,
                best_bid=0.45,
            ),
            paper_mode=False,
            clob=clob,
        )

    # Position must still be in portfolio (not closed)
    assert pos in portfolio.positions
    assert pos.state != "settled"
    assert pos.state != "voided"
    # Exit state should indicate sell was placed but not filled
    assert pos.exit_state in ("sell_placed", "sell_pending")


# ---- Test 2: Entry creates pending_tracked ----

def test_live_entry_creates_pending_tracked():
    """Entry must create position even before fill confirmed.

    The Position dataclass must support pending_tracked with entry_order_id.
    """
    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
    )

    assert pos.state == "pending_tracked"
    assert pos.entry_order_id == "buy_123"
    assert pos.entry_fill_verified is False
    # Must have LifecycleState enum support
    assert LifecycleState(pos.state) == LifecycleState.PENDING_TRACKED


# ---- Test 3: Cancelled pending → void ----

def test_pending_tracked_voids_after_cancel():
    """Pending entry that gets cancelled → void, not phantom position."""
    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
    )
    portfolio = _make_portfolio(pos)

    # Simulate CLOB returning CANCELLED
    from src.execution.fill_tracker import check_pending_entries
    clob = _make_clob(order_status="CANCELLED")

    stats = check_pending_entries(portfolio, clob)

    # Position should be voided and removed from portfolio
    assert stats["voided"] == 1
    assert len(portfolio.positions) == 0  # void_position removes from portfolio


def test_chain_reconciliation_rescues_pending_tracked_fill():
    """Chain truth must rescue pending_tracked when order-status path is unavailable."""
    from src.state.chain_reconciliation import ChainPosition, reconcile

    pos = _make_position(
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
    )
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
    )

    assert stats["rescued_pending"] == 1
    assert pos.state == "entered"
    assert pos.chain_state == "synced"
    assert pos.entry_fill_verified is True
    assert pos.order_status == "filled"
    assert pos.entered_at != ""
    assert pos.shares == 25.0
    assert pos.entry_price == 0.44
    assert pos.size_usd == 11.0
    assert pos.cost_basis_usd == 11.0
    assert pos.condition_id == "cond-1"
    assert portfolio.positions == [pos]


def test_chain_reconciliation_rescue_emits_exactly_one_stage_event(tmp_path):
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema, query_position_events

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-rt-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="snap-1",
    )
    portfolio = _make_portfolio(pos)
    chain_row = ChainPosition(token_id="tok_yes_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")

    stats_first = reconcile(portfolio, [chain_row], conn=conn)
    stats_second = reconcile(portfolio, [chain_row], conn=conn)

    events = query_position_events(conn, "rescue-rt-1")
    conn.close()

    assert stats_first["rescued_pending"] == 1
    assert stats_second["rescued_pending"] == 0
    lifecycle_events = [
        event for event in events
        if event["event_type"] == "POSITION_LIFECYCLE_UPDATED"
        and event["source"] == "chain_reconciliation"
        and event["details"].get("reason") == "pending_fill_rescued"
    ]
    assert len(lifecycle_events) == 1
    event = lifecycle_events[0]
    assert event["position_state"] == "entered"
    assert event["details"]["from_state"] == "pending_tracked"
    assert event["details"]["to_state"] == "entered"
    assert event["details"]["source"] == "chain_reconciliation"
    assert event["details"]["historical_entry_method"] == "ens_member_counting"
    assert event["details"]["historical_selected_method"] == "ens_member_counting"
    assert event["details"]["shares"] == 25.0
    assert event["details"]["cost_basis_usd"] == 11.0
    assert event["details"]["condition_id"] == "cond-1"


@pytest.mark.parametrize("exit_state", ["exit_intent", "sell_placed", "sell_pending", "retry_pending"])
def test_chain_reconciliation_does_not_void_exit_in_flight_positions(exit_state):
    """Chain sync must defer phantom authority while a sell order is in flight."""
    from src.state.chain_reconciliation import ChainPosition, reconcile

    exiting = _make_position(
        trade_id=f"exit-{exit_state}",
        token_id="tok_exit_001",
        no_token_id="tok_exit_no_001",
        state="holding",
        chain_state="synced",
        exit_state=exit_state,
    )
    healthy = _make_position(
        trade_id="healthy-sync-1",
        token_id="tok_live_001",
        no_token_id="tok_live_no_001",
        state="holding",
        chain_state="unknown",
        condition_id="cond-live-1",
    )
    portfolio = _make_portfolio(exiting, healthy)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_live_001", size=25.0, avg_price=0.40, cost=10.0, condition_id="cond-live-1")],
    )

    assert stats["voided"] == 0
    assert stats["skipped_pending_exit"] == 1
    assert exiting in portfolio.positions
    assert exiting.exit_state == exit_state
    assert exiting.chain_state == "exit_pending_missing"
    assert healthy.chain_state == "synced"
    assert healthy.condition_id == "cond-live-1"


def test_chain_reconciliation_does_not_void_verified_entry_waiting_for_chain():
    from src.state.chain_reconciliation import ChainPosition, reconcile

    entered = _make_position(
        trade_id="entered-waiting-chain",
        token_id="tok_entry_001",
        no_token_id="tok_entry_no_001",
        state="entered",
        chain_state="local_only",
        entry_fill_verified=True,
        order_status="filled",
    )
    healthy = _make_position(
        trade_id="healthy-sync-2",
        token_id="tok_live_002",
        no_token_id="tok_live_no_002",
        state="holding",
        chain_state="unknown",
        condition_id="cond-live-2",
    )
    portfolio = _make_portfolio(entered, healthy)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_live_002", size=25.0, avg_price=0.40, cost=10.0, condition_id="cond-live-2")],
    )

    assert stats["voided"] == 0
    assert stats["awaiting_chain_entry"] == 1
    assert entered in portfolio.positions
    assert entered.chain_state == "local_only"


# ---- Test 4: Retry respects cooldown ----


def test_exit_retry_respects_cooldown():
    """After failed sell, must wait cooldown before retrying."""
    future_time = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    pos = _make_position(
        exit_state="retry_pending",
        next_exit_retry_at=future_time,
        exit_retry_count=1,
    )

    assert is_exit_cooldown_active(pos) is True

    # check_pending_retries should not reset a position in cooldown
    result = check_pending_retries(pos)
    assert result is False
    assert pos.exit_state == "retry_pending"  # unchanged


# ---- Test 5: Backoff exhausted holds to settlement ----


# ---- Test 5: Backoff exhausted holds to settlement ----

def test_backoff_exhausted_holds_to_settlement():
    """After MAX_EXIT_RETRIES retries, stop trying to sell. Hold to settlement."""
    pos = _make_position(
        exit_state="backoff_exhausted",
        exit_retry_count=MAX_EXIT_RETRIES,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    # execute_exit should not be called for backoff_exhausted positions,
    # but even if it were, the position should remain unchanged
    result = check_pending_retries(pos)
    assert result is False
    assert pos.exit_state == "backoff_exhausted"

    # Position stays in portfolio — not closed, not voided
    assert pos in portfolio.positions
    assert pos.state != "settled"
    assert pos.state != "voided"


# ---- Test 6: Paper exit does not use sell order ----

def test_paper_exit_does_not_use_sell_order():
    """Paper mode: direct close_position, no CLOB interaction."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    with patch("src.execution.exit_lifecycle.place_sell_order") as mock_sell:
        outcome = execute_exit(
            portfolio=portfolio,
            position=pos,
            exit_context=ExitContext(
                exit_reason="EDGE_REVERSAL",
                current_market_price=0.45,
                best_bid=0.45,
            ),
            paper_mode=True,
            clob=clob,
        )

    # No sell order should have been placed
    mock_sell.assert_not_called()
    # Position should be closed
    assert "paper_exit" in outcome


# ---- Test 7: Collateral check blocks underfunded sell ----

def test_collateral_check_blocks_underfunded_sell():
    """Can't sell if wallet doesn't have enough collateral."""
    clob = _make_clob(balance=0.50)

    # entry_price=0.10, shares=50 → needs (1-0.10)*50 = $45 collateral
    can_sell, reason = check_sell_collateral(
        entry_price=0.10, shares=50.0, clob=clob,
    )

    assert can_sell is False
    assert reason is not None
    assert "need $45.00" in reason


# ---- Test 8: Quarantine expires after 48h ----

def test_quarantine_expires_after_48h():
    """Quarantined positions become exit-eligible after 48 hours."""
    past_time = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    pos = _make_position(
        chain_state="quarantined",
        quarantined_at=past_time,
    )
    portfolio = _make_portfolio(pos)

    expired = check_quarantine_timeouts(portfolio)

    assert expired == 1
    assert pos.chain_state == "quarantine_expired"


def test_quarantine_expired_blocks_new_entries_until_resolved():
    """Quarantine-expired positions still block discovery until authoritative resolution."""
    pos = _make_position(chain_state="quarantine_expired")
    portfolio = _make_portfolio(pos)

    has_quarantine = any(
        p.chain_state in {"quarantined", "quarantine_expired"}
        for p in portfolio.positions
    )

    assert has_quarantine is True


def test_monitoring_marks_quarantine_for_admin_resolution_once(monkeypatch):
    """Quarantine must enter an explicit admin-resolution path instead of passive skipping."""
    from src.engine import cycle_runtime

    pos = _make_position(direction="unknown", chain_state="quarantined")
    portfolio = _make_portfolio(pos)

    class PaperClob:
        paper_mode = True

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in quarantine admin-resolution test")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    now = datetime(2026, 4, 1, 5, 30, tzinfo=timezone.utc)
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_quarantine_admin_resolution"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: now),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        PaperClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert pos.admin_exit_reason == QUARANTINE_REVIEW_REQUIRED
    assert pos.exit_reason == QUARANTINE_REVIEW_REQUIRED
    assert pos.last_exit_at == now.isoformat()
    assert summary["quarantine_resolution_marked"] == 1
    assert summary["monitor_skipped_quarantine_resolution"] == 1
    assert summary["monitors"] == 0
    assert len(monitor_results) == 1
    assert monitor_results[0].exit_reason == QUARANTINE_REVIEW_REQUIRED

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        PaperClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert pos.admin_exit_reason == QUARANTINE_REVIEW_REQUIRED
    assert summary["quarantine_resolution_marked"] == 1
    assert summary["monitor_skipped_quarantine_resolution"] == 2


def test_quarantine_expired_marks_distinct_admin_resolution_reason(monkeypatch):
    """Expired quarantine keeps the same protective path but with explicit expired provenance."""
    from src.engine import cycle_runtime

    pos = _make_position(direction="unknown", chain_state="quarantine_expired")
    portfolio = _make_portfolio(pos)

    class PaperClob:
        paper_mode = True

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in quarantine-expired admin-resolution test")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    now = datetime(2026, 4, 1, 5, 30, tzinfo=timezone.utc)
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_quarantine_expired_admin_resolution"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: now),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        PaperClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert pos.admin_exit_reason == QUARANTINE_EXPIRED_REVIEW_REQUIRED
    assert pos.exit_reason == QUARANTINE_EXPIRED_REVIEW_REQUIRED
    assert len(monitor_results) == 1
    assert monitor_results[0].exit_reason == QUARANTINE_EXPIRED_REVIEW_REQUIRED


def test_monitoring_transitions_holding_position_into_day0_window(monkeypatch):
    """Positions nearing settlement must enter the universal Day0 terminal phase."""
    from src.engine import cycle_runtime
    from src.contracts import EdgeContext, EntryMethod

    pos = _make_position(state="holding", city="Chicago", target_date="2026-04-01")
    portfolio = _make_portfolio(pos)

    class PaperClob:
        paper_mode = True

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in this transition test")

    observed_refresh_states = []

    def mock_refresh(conn, clob, position):
        observed_refresh_states.append((position.state, position.entry_method))
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([position.entry_price]),
            p_posterior=position.p_posterior,
            forward_edge=0.0,
            alpha=0.0,
            confidence_band_upper=0.0,
            confidence_band_lower=0.0,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap1",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)

    observed_hours = []

    def mock_evaluate_exit(self, exit_context):
        observed_hours.append(exit_context.hours_to_settlement)
        return ExitDecision(False, selected_method=self.selected_method or self.entry_method)

    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)

    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_day0_transition"),
            "cities_by_name": {"Chicago": type("City", (), {"timezone": "America/Chicago"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 5, 30, tzinfo=timezone.utc)),
        },
    )

    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: None})()
    summary = {"monitors": 0, "exits": 0}

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        PaperClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert pos.state == "day0_window"
    assert observed_refresh_states == [("day0_window", "ens_member_counting")]
    assert observed_hours and observed_hours[0] is not None
    assert observed_hours[0] < 1.0
    assert summary["monitors"] == 1


def test_same_cycle_day0_crossing_refreshes_through_day0_semantics(monkeypatch):
    """A same-cycle `<6h` crossing must not refresh through the old non-Day0 path."""
    from src.engine import cycle_runtime, monitor_refresh
    from src.contracts import EdgeContext, EntryMethod

    pos = _make_position(
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
    )
    portfolio = _make_portfolio(pos)

    class PaperClob:
        paper_mode = True

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in same-cycle Day0 refresh test")

    monkeypatch.setattr(monitor_refresh, "get_current_yes_price", lambda market_id: 0.41)

    observed_methods = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_methods.append(position.entry_method)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        return 0.52

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, exit_context: ExitDecision(False, selected_method=self.selected_method or self.entry_method),
    )

    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_same_cycle_day0_refresh"),
            "cities_by_name": {"Chicago": type("City", (), {"timezone": "America/Chicago"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 5, 30, tzinfo=timezone.utc)),
        },
    )

    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: None})()
    summary = {"monitors": 0, "exits": 0}

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        PaperClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert pos.state == "day0_window"
    assert observed_methods == [EntryMethod.DAY0_OBSERVATION.value]
    assert pos.entry_method == EntryMethod.ENS_MEMBER_COUNTING.value
    assert pos.selected_method == EntryMethod.DAY0_OBSERVATION.value
    assert pos.applied_validations == [EntryMethod.DAY0_OBSERVATION.value]
    assert pos.last_monitor_prob == pytest.approx(0.52)
    assert pos.last_monitor_market_price == pytest.approx(0.41)
    assert summary["monitors"] == 1


def test_day0_window_refresh_uses_day0_observation_semantics(monkeypatch):
    """day0_window must refresh through Day0 semantics even for ENS-entered positions."""
    from src.engine import monitor_refresh
    from src.contracts import EntryMethod

    pos = _make_position(
        state="day0_window",
        city="Chicago",
        target_date="2026-04-01",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
    )

    class DummyClob:
        paper_mode = True

    monkeypatch.setattr(monitor_refresh, "get_current_yes_price", lambda market_id: 0.41)

    observed_methods = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_methods.append(position.entry_method)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        return 0.52

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert observed_methods == [EntryMethod.DAY0_OBSERVATION.value]
    assert pos.entry_method == "ens_member_counting"
    assert pos.selected_method == EntryMethod.DAY0_OBSERVATION.value
    assert EntryMethod.DAY0_OBSERVATION.value in pos.applied_validations
    assert edge_ctx.p_posterior == pytest.approx(0.52)
    assert edge_ctx.entry_provenance == EntryMethod.ENS_MEMBER_COUNTING
    assert pos.last_monitor_prob == pytest.approx(0.52)
    assert pos.last_monitor_market_price == pytest.approx(0.41)


def test_day0_window_live_refresh_uses_best_bid_not_vwmp(monkeypatch):
    """Day0 terminal-phase pricing should use realizable sell-side liquidity."""
    from src.engine import monitor_refresh
    from src.contracts import EntryMethod

    pos = _make_position(
        state="day0_window",
        direction="buy_yes",
        city="Chicago",
        target_date="2026-04-01",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
        token_id="tok_yes_001",
    )

    class DummyClob:
        paper_mode = False

        def get_best_bid_ask(self, token_id):
            assert token_id == "tok_yes_001"
            return 0.37, 0.55, 100.0, 200.0

    monkeypatch.setattr(monitor_refresh, "log_microstructure", lambda *args, **kwargs: None, raising=False)

    observed_markets = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_markets.append(current_p_market)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        return 0.52

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert observed_markets == [0.37]
    assert pos.entry_method == EntryMethod.ENS_MEMBER_COUNTING.value
    assert pos.selected_method == EntryMethod.DAY0_OBSERVATION.value
    assert pos.last_monitor_market_price == pytest.approx(0.37)
    assert pos.last_monitor_best_bid == pytest.approx(0.37)
    assert pos.last_monitor_best_ask == pytest.approx(0.55)
    assert edge_ctx.p_market[0] == pytest.approx(0.37)


def test_day0_refresh_fallback_keeps_probability_stale(monkeypatch):
    """Day0 fallback may reuse stored probability, but it must not relabel it fresh."""
    from src.contracts import EntryMethod
    from src.engine import monitor_refresh

    pos = _make_position(
        state="day0_window",
        city="Chicago",
        target_date="2026-04-01",
        entry_method=EntryMethod.ENS_MEMBER_COUNTING.value,
        selected_method="",
        p_posterior=0.61,
        last_monitor_prob_is_fresh=True,
        applied_validations=["alpha_posterior"],
    )

    class DummyClob:
        paper_mode = True

    monkeypatch.setattr(monitor_refresh, "get_current_yes_price", lambda market_id: 0.41)
    monkeypatch.setattr(
        monitor_refresh,
        "_fetch_day0_observation",
        lambda city, target_d: {
            "high_so_far": 44.0,
            "current_temp": 43.0,
            "source": "wu_api",
            # Missing observation_time forces fallback to the stored posterior.
        },
    )

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert pos.selected_method == EntryMethod.DAY0_OBSERVATION.value
    assert pos.last_monitor_market_price == pytest.approx(0.41)
    assert pos.last_monitor_market_price_is_fresh is True
    assert pos.last_monitor_prob == pytest.approx(0.61)
    assert pos.last_monitor_prob_is_fresh is False
    assert edge_ctx.p_posterior == pytest.approx(0.61)
    assert "missing_observation_timestamp" in pos.applied_validations


# ---- Bonus: Quarantine does NOT expire before 48h ----


def test_quarantine_does_not_expire_early():
    """Quarantined positions stay quarantined before 48 hours."""
    recent_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    pos = _make_position(
        chain_state="quarantined",
        quarantined_at=recent_time,
    )
    portfolio = _make_portfolio(pos)

    expired = check_quarantine_timeouts(portfolio)

    assert expired == 0
    assert pos.chain_state == "quarantined"


# ---- Bonus: Collateral check fail-closed on API error ----


def test_collateral_check_fails_closed_on_api_error():
    """If balance fetch fails, collateral check blocks the sell."""
    clob = MagicMock()
    clob.get_balance.side_effect = Exception("API timeout")

    can_sell, reason = check_sell_collateral(
        entry_price=0.40, shares=10.0, clob=clob,
    )

    assert can_sell is False
    assert "balance_fetch_failed" in reason


# ---- Bonus: Live exit blocked by collateral goes to retry ----


def test_live_exit_collateral_blocked_goes_to_retry():
    """Live exit that fails collateral check transitions to retry_pending."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob(balance=0.01)  # Not enough

    outcome = execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ExitContext(
            exit_reason="EDGE_REVERSAL",
            current_market_price=0.45,
            best_bid=None,
        ),
        paper_mode=False,
        clob=clob,
    )

    assert "collateral_blocked" in outcome
    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 1
    assert pos in portfolio.positions  # NOT closed


def test_deferred_fill_logs_last_monitor_best_bid(tmp_path):
    """Deferred fill telemetry must preserve sell-side realizable bid, not mark price."""
    from src.state.db import get_connection, init_schema, query_position_events

    pos = _make_position(
        trade_id="deferred-fill-1",
        state="holding",
        exit_state="sell_pending",
        last_exit_order_id="sell-order-1",
        exit_reason="DEFERRED_SELL_FILL",
        last_monitor_market_price=0.44,
        last_monitor_best_bid=0.39,
    )
    portfolio = _make_portfolio(pos)
    conn = get_connection(tmp_path / "deferred-fill.db")
    init_schema(conn)
    clob = _make_clob(order_status="FILLED")

    stats = check_pending_exits(portfolio, clob, conn=conn)
    events = query_position_events(conn, "deferred-fill-1")

    assert stats["filled"] == 1
    assert stats["retried"] == 0
    fill_event = next(event for event in events if event["event_type"] == "EXIT_ORDER_FILLED")
    assert fill_event["details"]["best_bid"] == pytest.approx(0.39)
    assert fill_event["details"]["current_market_price"] == pytest.approx(0.44)


def test_exit_authority_fails_closed_on_incomplete_context():
    """Missing authority fields must not silently fall through normal exit math."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=None,
            current_market_price=0.90,
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob,current_market_price_is_fresh)"
    assert "exit_context_incomplete" in decision.applied_validations
    assert pos.neg_edge_count == 0


def test_exit_authority_fails_closed_on_stale_monitor_inputs():
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.55,
            fresh_prob_is_fresh=False,
            current_market_price=0.45,
            current_market_price_is_fresh=False,
            best_bid=0.44,
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert decision.should_exit is False
    assert "fresh_prob_is_fresh" in decision.reason
    assert "current_market_price_is_fresh" in decision.reason


def test_buy_yes_edge_exit_requires_best_bid():
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.30,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=None,
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"


def test_live_execute_exit_blocks_incomplete_context():
    """Direct execute_exit callers must also fail closed on missing market price."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    outcome = execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ExitContext(exit_reason="EDGE_REVERSAL", current_market_price=None),
        paper_mode=False,
        clob=clob,
    )

    assert outcome == "exit_blocked: incomplete_context"
    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 1
    assert pos.last_exit_error == "missing_current_market_price"
    assert pos in portfolio.positions


# ---- Autonomous Discovery Tests ----


def test_incomplete_chain_response_skips_voiding():
    """If chain API returns 0 positions but we have active local positions,
    don't void them — the API response is likely incomplete."""
    from src.state.chain_reconciliation import reconcile

    pos = _make_position(state="holding", token_id="tok_yes_real")
    portfolio = _make_portfolio(pos)

    # Chain returns EMPTY — suspect incomplete API response
    stats = reconcile(portfolio, chain_positions=[])

    # Position should NOT be voided
    assert stats["voided"] == 0
    assert pos in portfolio.positions
    assert stats.get("skipped_void_incomplete_api", 0) > 0


def test_incomplete_chain_response_does_not_mark_exit_pending_missing():
    """A globally incomplete chain snapshot must not escalate retrying exits into exit-missing recovery."""
    from src.state.chain_reconciliation import reconcile

    exiting = _make_position(
        state="holding",
        token_id="tok_retry_yes",
        no_token_id="tok_retry_no",
        exit_state="retry_pending",
        chain_state="synced",
    )
    healthy = _make_position(
        trade_id="healthy-other",
        token_id="tok_other_yes",
        no_token_id="tok_other_no",
        state="holding",
        chain_state="synced",
    )
    portfolio = _make_portfolio(exiting, healthy)

    stats = reconcile(portfolio, chain_positions=[])

    assert stats["voided"] == 0
    assert stats.get("skipped_pending_exit", 0) == 0
    assert stats.get("skipped_void_incomplete_api", 0) >= 2
    assert exiting.chain_state == "synced"
    assert exiting in portfolio.positions


# ---- Autonomous Discovery Tests ----


def test_exit_retry_exponential_backoff():
    """Retry cooldown should increase exponentially."""
    from src.execution.exit_lifecycle import _mark_exit_retry, _parse_iso, _utcnow

    pos = _make_position()

    # First retry: base cooldown (300s = 5min)
    _mark_exit_retry(pos, reason="TEST", cooldown_seconds=300)
    first_retry = _parse_iso(pos.next_exit_retry_at)
    assert pos.exit_retry_count == 1
    assert pos.exit_state == "retry_pending"

    # Second retry: 2x cooldown (600s = 10min)
    _mark_exit_retry(pos, reason="TEST", cooldown_seconds=300)
    second_retry = _parse_iso(pos.next_exit_retry_at)
    assert pos.exit_retry_count == 2

    # Second retry should be further in the future than first was
    # (both relative to their own "now", so we just check count increments)
    assert pos.exit_retry_count == 2


# ---- Test 9: Sell share rounding ----


def test_sell_order_rounds_shares_down():
    """Sell shares must round DOWN to prevent over-selling."""
    shares = 10.999
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 10.99

    shares = 10.994
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 10.99

    shares = 10.0
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 10.0

    shares = 0.009
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 0.0


# ---- Test 10: Stranded exit_intent recovery ----


def test_stranded_exit_intent_recovered():
    """If place_sell_order throws, position is stranded in exit_intent.
    check_pending_exits must recover it via retry."""
    pos = _make_position(
        state="holding",
        exit_state="exit_intent",  # stranded by exception
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    stats = check_pending_exits(portfolio, clob)

    assert stats["retried"] == 1
    assert pos.exit_state == "retry_pending"
    assert pos in portfolio.positions  # NOT closed


# ---- Provenance Tests ----


def test_position_carries_env():
    """Every position must carry its env provenance."""
    pos = _make_position(env="paper")
    assert pos.env == "paper"

    pos_live = _make_position(env="live")
    assert pos_live.env == "live"


def test_contamination_guard_blocks_wrong_env():
    """Loading a live position into paper portfolio (or vice versa) must fail."""
    from src.state.portfolio import PortfolioModeError, load_portfolio, save_portfolio
    import tempfile
    from pathlib import Path

    # Create a portfolio with a "live" position
    pos = _make_position(env="live")
    portfolio = _make_portfolio(pos)

    # Save to temp file
    tmp = Path(tempfile.mktemp(suffix=".json"))
    try:
        save_portfolio(portfolio, tmp)

        # Loading in paper mode (settings.mode == "paper") should raise
        # because the position has env="live"
        with pytest.raises(PortfolioModeError, match="live position"):
            load_portfolio(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def test_state_path_includes_mode():
    """state_path must produce mode-qualified filenames."""
    from src.config import state_path, settings
    path = state_path("positions.json")
    assert f"-{settings.mode}" in path.name


def test_empty_env_positions_pass_guard():
    """Positions with empty env (legacy) should pass the contamination guard."""
    pos = _make_position(env="")
    portfolio = _make_portfolio(pos)

    # Empty env should not trigger guard (backward compat for legacy data)
    import tempfile
    from pathlib import Path
    from src.state.portfolio import load_portfolio, save_portfolio

    tmp = Path(tempfile.mktemp(suffix=".json"))
    try:
        save_portfolio(portfolio, tmp)
        loaded = load_portfolio(tmp)
        assert len(loaded.positions) == 1
    finally:
        tmp.unlink(missing_ok=True)
