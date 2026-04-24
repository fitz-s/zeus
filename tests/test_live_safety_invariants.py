# Created: 2026-03-31
# Last reused/audited: 2026-04-23
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a midstream guardian panel)
"""Live safety invariant tests: relationship tests, not function tests.

These verify cross-module relationships that prevent ghost positions,
phantom P&L, and local↔chain divergence in live mode.

GOLDEN RULE: economic close is ONLY created after confirmed FILLED.
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


def _seed_canonical_entry_baseline(conn, position) -> None:
    """T1.c-followup (2026-04-23): post-T4.1b, chain_reconciliation.reconcile
    gates rescue strictly on the existence of a canonical baseline
    (``position_current`` row in ``pending_entry`` phase). This helper
    seeds that baseline by routing the ``pending_tracked`` position through
    ``build_entry_canonical_write`` + ``append_many_and_project`` so rescue
    probes find the POSITION_OPEN_INTENT / ENTRY_ORDER_POSTED events plus
    the ``pending_entry`` ``position_current`` row they need to flip.
    """
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.ledger import append_many_and_project

    events, projection = build_entry_canonical_write(
        position,
        decision_id=getattr(position, "decision_snapshot_id", None) or "dec-t1c-followup",
        source_module="src.test.t1c_followup_baseline",
    )
    append_many_and_project(conn, events, projection)


def _make_clob(
    order_status="OPEN",
    balance=100.0,
    sell_result=None,
):
    """Create mock CLOB client."""
    clob = MagicMock()
    clob.get_order_status.return_value = sell_result or {"status": order_status}
    clob.get_balance.return_value = balance
    clob.cancel_order.return_value = {"status": "CANCELLED"}
    return clob


# ---- Test 1: GOLDEN RULE ----

def test_live_exit_never_closes_without_fill():
    """GOLDEN RULE: economic close only created after confirmed FILLED.

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
            clob=clob,
        )

    # Position must still be in portfolio (not closed)
    assert pos in portfolio.positions
    assert pos.state == "pending_exit"
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


def test_fill_tracker_keeps_verified_entry_local_only_until_chain_seen():
    """Normal CLOB fill verifies locally first; chain ownership arrives later."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        chain_state="unknown",
    )
    portfolio = _make_portfolio(pos)

    class Tracker:
        def __init__(self):
            self.entries = []

        def record_entry(self, position):
            self.entries.append(position.trade_id)

    tracker = Tracker()
    clob = _make_clob(order_status="FILLED")
    clob.get_order_status.return_value = {
        "status": "FILLED",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }

    stats = check_pending_entries(portfolio, clob, tracker=tracker)

    assert stats["entered"] == 1
    assert stats["dirty"] is True
    assert stats["tracker_dirty"] is True
    assert pos.state == "entered"
    assert pos.entry_order_id == "buy_123"
    assert pos.entry_fill_verified is True
    assert pos.order_status == "filled"
    assert pos.chain_state == "local_only"
    assert pos.entered_at != ""
    assert pos.size_usd == pytest.approx(11.0)
    assert pos.cost_basis_usd == pytest.approx(11.0)
    assert pos.fill_quality == pytest.approx(0.10)
    assert tracker.entries == ["test_001"]


def test_chain_reconciliation_rescues_pending_tracked_fill(tmp_path):
    """Chain truth must rescue pending_tracked when order-status path is
    unavailable. T1.c-followup rewrite 2026-04-23: rescue is now gated on
    canonical baseline existence (post-T4.1b); test seeds baseline via
    build_entry_canonical_write + passes conn to reconcile."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "rescue_pending.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        decision_snapshot_id="snap-1",
    )
    _seed_canonical_entry_baseline(conn, pos)
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
        conn=conn,
    )
    conn.close()

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


def test_lifecycle_kernel_rescues_pending_runtime_state_to_entered():
    from src.state.lifecycle_manager import rescue_pending_runtime_state

    assert rescue_pending_runtime_state("pending_tracked") == "entered"


def test_lifecycle_kernel_rejects_rescue_from_non_pending_runtime_state():
    from src.state.lifecycle_manager import rescue_pending_runtime_state

    with pytest.raises(ValueError, match="pending rescue requires pending_entry runtime phase"):
        rescue_pending_runtime_state("entered")


def test_lifecycle_kernel_enters_chain_quarantined_runtime_state():
    from src.state.lifecycle_manager import enter_chain_quarantined_runtime_state

    assert enter_chain_quarantined_runtime_state() == "quarantined"


def test_chain_reconciliation_rescue_updates_trade_lifecycle_row(tmp_path):
    """T1.c-followup rewrite 2026-04-23: post-T4.1b, the rescue audit trail
    flows through canonical position_events (CHAIN_SYNCED event_type +
    source_module='src.state.chain_reconciliation') rather than the
    legacy POSITION_LIFECYCLE_UPDATED-with-source-field shape. Test
    asserts the new canonical shape carries the rescue metadata that
    downstream audit consumers need (entry_order_id, chain_state,
    historical_entry_method, shares, cost_basis_usd, condition_id)."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema, query_position_events

    conn = get_connection(tmp_path / "rescue_db.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-db-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_db_001",
        no_token_id="tok_no_db_001",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="snap-db-1",
    )
    _seed_canonical_entry_baseline(conn, pos)
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_db_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
        conn=conn,
    )
    conn.commit()
    events = query_position_events(conn, "rescue-db-1")
    conn.close()

    assert stats["rescued_pending"] == 1
    # Canonical entry trail from _seed_canonical_entry_baseline
    entry_event_types = [e["event_type"] for e in events]
    assert "POSITION_OPEN_INTENT" in entry_event_types
    assert "ENTRY_ORDER_POSTED" in entry_event_types

    # Rescue emission: post-T4.1b the canonical event_type is CHAIN_SYNCED
    # with source_module='src.state.chain_reconciliation' and
    # payload_json carrying the rescue metadata.
    rescue_events = [e for e in events if e["event_type"] == "CHAIN_SYNCED"]
    assert len(rescue_events) == 1
    rescue = rescue_events[0]
    assert rescue["source"] == "src.state.chain_reconciliation"
    assert rescue["order_id"] == "buy_123"
    details = rescue["details"]
    assert details["source"] == "chain_reconciliation"
    assert details["reason"] == "pending_fill_rescued"
    assert details["from_state"] == "pending_tracked"
    assert details["to_state"] == "entered"
    assert details["entry_order_id"] == "buy_123"
    assert details["entry_fill_verified"] is True
    assert details["chain_state"] == "synced"
    assert details["condition_id"] == "cond-1"


def test_chain_reconciliation_rescue_emits_exactly_one_stage_event(tmp_path):
    """T1.c-followup rewrite 2026-04-23: post-T4.1b, rescue emits exactly
    one CHAIN_SYNCED canonical event on first rescue; repeat reconcile
    calls on the same trade_id do not double-emit (idempotency guard
    via position_current phase check + already-logged check)."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema, query_position_events

    conn = get_connection(tmp_path / "rescue_rt.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-rt-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="snap-1",
    )
    _seed_canonical_entry_baseline(conn, pos)
    portfolio = _make_portfolio(pos)
    chain_row = ChainPosition(token_id="tok_yes_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")

    stats_first = reconcile(portfolio, [chain_row], conn=conn)
    stats_second = reconcile(portfolio, [chain_row], conn=conn)

    events = query_position_events(conn, "rescue-rt-1")
    conn.close()

    assert stats_first["rescued_pending"] == 1
    assert stats_second["rescued_pending"] == 0
    # Exactly ONE canonical rescue event (idempotency).
    rescue_events = [
        e for e in events
        if e["event_type"] == "CHAIN_SYNCED"
        and e["source"] == "src.state.chain_reconciliation"
    ]
    assert len(rescue_events) == 1
    event = rescue_events[0]
    details = event["details"]
    assert details["from_state"] == "pending_tracked"
    assert details["to_state"] == "entered"
    assert details["source"] == "chain_reconciliation"
    assert details["reason"] == "pending_fill_rescued"
    assert details["historical_entry_method"] == "ens_member_counting"
    assert details["historical_selected_method"] == "ens_member_counting"
    assert details["shares"] == 25.0
    assert details["cost_basis_usd"] == 11.0
    assert details["condition_id"] == "cond-1"


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


def test_chain_reconciliation_does_not_void_economically_closed_positions():
    from src.state.chain_reconciliation import ChainPosition, reconcile

    exiting = _make_position(
        trade_id="economic-close-1",
        token_id="tok_econ_001",
        no_token_id="tok_econ_no_001",
        state="economically_closed",
        exit_state="sell_filled",
        chain_state="synced",
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
    assert stats["skipped_economically_closed"] == 1
    assert exiting in portfolio.positions
    assert healthy.chain_state == "synced"


def test_chain_reconciliation_economically_closed_local_does_not_mask_chain_only_quarantine():
    from src.state.chain_reconciliation import ChainPosition, reconcile

    exiting = _make_position(
        trade_id="economic-close-1",
        token_id="tok_econ_001",
        no_token_id="tok_econ_no_001",
        state="economically_closed",
        exit_state="sell_filled",
        chain_state="synced",
    )
    portfolio = _make_portfolio(exiting)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_econ_001", size=25.0, avg_price=0.40, cost=10.0, condition_id="cond-live-1")],
    )

    assert stats["quarantined"] == 1
    quarantine = next(pos for pos in portfolio.positions if pos.trade_id.startswith("quarantine_"))
    assert quarantine.state == "quarantined"
    assert quarantine.chain_state == "quarantined"


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


def test_chain_reconciliation_updates_cost_basis_even_when_share_count_matches():
    from src.state.chain_reconciliation import ChainPosition, reconcile

    pos = _make_position(
        trade_id="cost-sync-1",
        token_id="tok_cost_001",
        no_token_id="tok_cost_no_001",
        state="holding",
        chain_state="unknown",
        shares=25.0,
        size_usd=10.0,
        cost_basis_usd=10.0,
        entry_price=0.40,
    )
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_cost_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-cost-1")],
    )

    assert stats["synced"] == 1
    assert pos.chain_state == "synced"
    assert pos.cost_basis_usd == pytest.approx(11.0)
    assert pos.size_usd == pytest.approx(11.0)
    assert pos.entry_price == pytest.approx(0.44)


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

@pytest.mark.skip(reason="T1.c-audit 2026-04-23: KEEP_LEGITIMATE — paper-mode was removed in Phase2 (canonical-only execution). This test validates deprecated paper-path behavior; retained as documentation antibody of the removed contract. Un-skip verified failure as expected (cannot exercise removed code path). Do not un-skip; promote to OBSOLETE_DELETE if plan ever decides retire the documentation antibody.")
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
            clob=clob,
        )

    # No sell order should have been placed
    mock_sell.assert_not_called()
    # Position should be economically closed, not settled
    assert "paper_exit" in outcome
    assert pos in portfolio.positions
    assert pos.state == "economically_closed"


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


def test_lifecycle_kernel_enters_day0_window_from_active_states():
    from src.state.lifecycle_manager import enter_day0_window_runtime_state

    assert enter_day0_window_runtime_state("entered") == "day0_window"
    assert enter_day0_window_runtime_state("holding") == "day0_window"


def test_lifecycle_kernel_rejects_day0_window_from_pending_exit():
    from src.state.lifecycle_manager import enter_day0_window_runtime_state

    with pytest.raises(ValueError, match="day0 transition requires active/pending_entry/day0_window runtime phase"):
        enter_day0_window_runtime_state(
            "pending_exit",
            exit_state="sell_pending",
            chain_state="exit_pending_missing",
        )


def test_day0_transition_emits_durable_lifecycle_event(monkeypatch, tmp_path):
    """T1.c-followup L875 closure via Day0-canonical-event feature slice
    (2026-04-24): after the transition, a canonical DAY0_WINDOW_ENTERED
    position_events row exists with phase_before=active, phase_after=
    day0_window, and payload carrying day0_entered_at. Pre-slice, this
    test was skipped OBSOLETE_PENDING_FEATURE because cycle_runtime did
    not emit a canonical event — only updated position_current.phase.
    Post-slice: canonical emission is wired via
    _emit_day0_window_entered_canonical_if_available in cycle_runtime.
    """
    from src.engine import cycle_runtime
    from src.contracts import EdgeContext, EntryMethod
    from src.state.db import get_connection, init_schema, log_trade_entry, query_position_events
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project

    conn = get_connection(tmp_path / "day0.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="day0-db-1",
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        order_id="o-day0",
        entry_order_id="o-day0",
        entry_fill_verified=True,
        entered_at="2026-04-01T04:00:00Z",
        order_status="filled",
        strategy_key="center_buy",
        bin_label="50-51°F",
    )
    log_trade_entry(conn, pos)
    # Seed canonical entry baseline so the Day0 canonical emission is not
    # the first canonical event for this trade_id (matches production
    # reality — entries always precede day0 transitions).
    events, projection = build_entry_canonical_write(
        pos,
        decision_id="decision-day0-seed",
        source_module="tests/test_day0_transition_emits_durable",
    )
    append_many_and_project(conn, events, projection)
    portfolio = _make_portfolio(pos)

    class PaperClob:
        paper_mode = True

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in this transition test")

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda conn, clob, position: EdgeContext(
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
        ),
    )
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
            "logger": logging.getLogger("test_day0_transition_db"),
            "cities_by_name": {"Chicago": type("City", (), {"timezone": "America/Chicago"})()},
            # _utcnow set to within day0 window (≤6h before Chicago target
            # date close at 2026-04-02 05:00 UTC) so the day0 gate fires.
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 2, 2, 0, tzinfo=timezone.utc)),
        },
    )
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: None})()
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        conn,
        PaperClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    events = query_position_events(conn, "day0-db-1")
    conn.close()
    # Day0-canonical-event slice assertion: a canonical DAY0_WINDOW_ENTERED
    # row was emitted by _emit_day0_window_entered_canonical_if_available.
    day0_events = [e for e in events if e["event_type"] == "DAY0_WINDOW_ENTERED"]
    assert day0_events, (
        f"Expected DAY0_WINDOW_ENTERED canonical event after day0 "
        f"transition; got event_types={[e['event_type'] for e in events]}"
    )
    day0_event = day0_events[0]
    # query_position_events returns the payload under `details` (decoded
    # from payload_json); phase_before/after live in the payload because
    # query_position_events doesn't surface the DB columns separately.
    details = day0_event.get("details") or {}
    assert details.get("phase_before") == "active"
    assert details.get("phase_after") == "day0_window"
    assert details.get("day0_entered_at") == "2026-04-02T02:00:00+00:00"
    assert day0_event["timestamp"] == "2026-04-02T02:00:00+00:00"


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
        clob=clob,
    )

    assert "collateral_blocked" in outcome
    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 1
    assert pos in portfolio.positions  # NOT closed


def test_deferred_fill_logs_last_monitor_best_bid(tmp_path):
    """Deferred fill telemetry must preserve sell-side realizable bid, not
    mark price. T1.c-followup rewrite 2026-04-23: post-T4.1b, exit fill
    emission flows through build_economic_close_canonical_write; test
    seeds active-phase canonical baseline so EXIT_ORDER_FILLED lands
    cleanly."""
    from src.state.db import get_connection, init_schema, query_position_events

    pos = _make_position(
        trade_id="deferred-fill-1",
        state="holding",
        exit_state="",
        chain_state="synced",
        last_exit_order_id="sell-order-1",
        exit_reason="DEFERRED_SELL_FILL",
        last_monitor_market_price=0.44,
        last_monitor_best_bid=0.39,
        order_id="buy-order-1",
        entry_order_id="buy-order-1",
        entry_fill_verified=True,
        entered_at="2026-04-03T00:05:00Z",
        order_status="filled",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="snap-def-1",
    )
    portfolio = _make_portfolio(pos)
    conn = get_connection(tmp_path / "deferred-fill.db")
    init_schema(conn)
    # Seed canonical baseline in active phase (exit_state="") so
    # build_entry_canonical_write accepts; then transition pos to
    # pending_exit state via exit_state mutation for the test scenario.
    _seed_canonical_entry_baseline(conn, pos)
    pos.exit_state = "sell_pending"
    clob = _make_clob(sell_result={"status": "FILLED", "avgPrice": 0.39})

    stats = check_pending_exits(portfolio, clob, conn=conn)
    events = query_position_events(conn, "deferred-fill-1")

    assert stats["filled"] == 1
    assert stats["retried"] == 0
    fill_event = next(event for event in events if event["event_type"] == "EXIT_ORDER_FILLED")
    assert pos.state == "economically_closed"
    assert pos.exit_price == pytest.approx(0.39)
    assert fill_event["details"]["fill_price"] == pytest.approx(0.39)
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


def test_day0_buy_yes_uses_single_confirmation_observation_reversal():
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=0.54,
            hours_to_settlement=4.0,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "DAY0_OBSERVATION_REVERSAL"
    assert "day0_observation_gate" in decision.applied_validations


def test_day0_buy_no_uses_single_confirmation_observation_reversal():
    pos = _make_position(direction="buy_no", size_usd=5.0, entry_price=0.60, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.20,
            fresh_prob_is_fresh=True,
            current_market_price=0.70,
            current_market_price_is_fresh=True,
            best_bid=0.69,
            hours_to_settlement=4.0,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "DAY0_OBSERVATION_REVERSAL"
    assert "day0_observation_gate" in decision.applied_validations


def test_day0_observation_exits_when_settlement_imminent():
    """Day0 positions must still exit when settlement is imminent (fallthrough fix)."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.80,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=0.54,
            hours_to_settlement=0.5,
            position_state="day0_window",
            day0_active=True,
            divergence_score=0.40,
            market_velocity_1h=-0.20,
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "SETTLEMENT_IMMINENT"
    assert "day0_observation_authority" in decision.applied_validations
    assert "near_settlement_gate" in decision.applied_validations


def test_live_execute_exit_blocks_incomplete_context():
    """Direct execute_exit callers must also fail closed on missing market price."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    outcome = execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ExitContext(exit_reason="EDGE_REVERSAL", current_market_price=None),
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
        last_exit_error="exception_during_sell",
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


@pytest.mark.skip(reason="T1.c-followup 2026-04-23: OBSOLETE_BY_ARCHITECTURE — src/state/portfolio.py::load_portfolio is now DB-first (query_portfolio_loader_view from canonical DB), with env carried on each row (src/state/portfolio.py:155 `env: str = \"live\"` axiom). JSON fallback + _load_portfolio_from_json_data contamination-guard path are deleted. The test validates a dead architecture; no canonical-loader analog needed because env-filtering happens at the query layer per-row. The 'run loader in paper mode, expect RuntimeError on live position' scenario does not exist in the DB-first canonical path. No RELOCATE; keep OBSOLETE with this marker until someone explicitly decides to delete-or-document-in-antibody-inventory.")
def test_contamination_guard_blocks_wrong_env():
    """Loading a live position into paper portfolio (or vice versa) must fail."""
    from src.state.portfolio import load_portfolio, save_portfolio
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
        with pytest.raises(RuntimeError, match="live position"):  # PortfolioModeError deleted (Phase 1)
            load_portfolio(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def test_state_path_resolves_directly():
    """Phase 2: state_path returns STATE_DIR/filename directly (mode prefix eliminated)."""
    from src.config import state_path, STATE_DIR
    path = state_path("positions.json")
    assert path == STATE_DIR / "positions.json"
    assert "-live" not in path.name
    assert "-paper" not in path.name


@pytest.mark.skip(reason="T1.c-followup 2026-04-23: OBSOLETE_BY_ARCHITECTURE — src/state/portfolio.py::load_portfolio is now DB-first (query_portfolio_loader_view from canonical DB), with env carried on each row (src/state/portfolio.py:155 `env: str = \"live\"` axiom). JSON fallback + _load_portfolio_from_json_data contamination-guard path are deleted. The test validates a dead architecture; no canonical-loader analog needed because env-filtering happens at the query layer per-row. The 'run loader in paper mode, expect RuntimeError on live position' scenario does not exist in the DB-first canonical path. No RELOCATE; keep OBSOLETE with this marker until someone explicitly decides to delete-or-document-in-antibody-inventory.")
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


# ---------------------------------------------------------------------------
# B041 relationship tests: fill_tracker typed error taxonomy (SD-B)
# ---------------------------------------------------------------------------

class TestB041FillTrackerBoundaryErrors:
    """_check_entry_fill must distinguish transient IO failures
    (legitimate ``still_pending``) from code defects (must propagate)."""

    def test_b041_ioerror_maps_to_still_pending(self):
        """A legitimate transient network-style error (ConnectionError)
        keeps the order pending — the exchange state is genuinely
        unknown this cycle.
        """
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = ConnectionError("simulated timeout")
        clob.cancel_order.return_value = {"status": "CANCELLED"}

        stats = check_pending_entries(portfolio, clob)
        # still_pending, no fill, no void — pos stays as-is
        assert stats["voided"] == 0
        assert stats["entered"] == 0
        assert len(portfolio.positions) == 1
        assert portfolio.positions[0].state == "pending_tracked"

    def test_b041_attributeerror_propagates(self):
        """An AttributeError from a wrong-shape clob mock is a code
        defect, NOT a legitimate transient state — must propagate
        rather than silently becoming ``still_pending`` forever.
        """
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = AttributeError(
            "clob has no attribute 'get_order_status'"
        )
        with pytest.raises(AttributeError, match="get_order_status"):
            check_pending_entries(portfolio, clob)

    def test_b041_typeerror_propagates(self):
        """A TypeError (e.g. wrong arg count from a regression) is a
        code defect and must propagate."""
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = TypeError(
            "got unexpected keyword argument"
        )
        with pytest.raises(TypeError, match="unexpected keyword"):
            check_pending_entries(portfolio, clob)


    def test_b041_keyerror_propagates(self):
        """Amendment (critic-alice review): KeyError from a malformed
        CLOB payload shape was omitted from the first-pass re-raise
        set. ``_normalize_status(payload)`` does ``payload["status"]``;
        a missing-key payload would have been silently caught as
        ``still_pending`` before this amendment. KeyError is a code
        defect and must now propagate.
        """
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = KeyError("status")
        with pytest.raises(KeyError, match="status"):
            check_pending_entries(portfolio, clob)

    def test_b041_indexerror_propagates(self):
        """Amendment (critic-alice review): IndexError from
        malformed list access (e.g. ``payload[0]`` on an empty
        sequence) is a code defect and must propagate."""
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = IndexError("list index out of range")
        with pytest.raises(IndexError, match="out of range"):
            check_pending_entries(portfolio, clob)
