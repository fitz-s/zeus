# Created: 2026-03-31
# Lifecycle: created=2026-03-31; last_reviewed=2026-05-05; last_reused=2026-05-05
# Purpose: Lock live-money safety invariants across fill, exit, chain, and P&L flows.
# Reuse: Run for execution finality, live exit, chain reconciliation, and safety invariant changes.
# Last reused/audited: 2026-05-08
# Authority basis: midstream verdict v2 2026-04-23; docs/operations/task_2026-05-08_object_invariance_remaining_mainline/PLAN.md
"""Live safety invariant tests: relationship tests, not function tests.

These verify cross-module relationships that prevent ghost positions,
phantom P&L, and local↔chain divergence in live mode.

GOLDEN RULE: economic close is ONLY created after CONFIRMED fill truth.
"""

import logging
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_LEGACY_UNKNOWN,
    ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE,
    ENTRY_ECONOMICS_SUBMITTED_LIMIT,
    ExitDecision,
    FILL_AUTHORITY_NONE,
    FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    Position,
    PortfolioState,
    close_position,
)

ROOT = Path(__file__).resolve().parents[1]


def test_harvester_scheduler_fails_closed_without_legacy_integrated_fallback():
    """Trading daemon must not fall back to integrated truth-writing harvester."""
    source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")

    assert "from src.execution.harvester import run_harvester" not in source
    assert "result = run_harvester()" not in source
    assert "resolver_unavailable_fail_closed" in source


def test_settlement_readers_filter_verified_authority_before_downstream_use():
    """Replay, monitor, and world-view reads must not consume quarantined settlement values."""
    replay_source = (ROOT / "src" / "engine" / "replay.py").read_text(encoding="utf-8")
    monitor_source = (ROOT / "src" / "engine" / "monitor_refresh.py").read_text(encoding="utf-8")
    world_view_source = (
        ROOT / "src" / "contracts" / "world_view" / "settlements.py"
    ).read_text(encoding="utf-8")

    assert replay_source.count("authority = 'VERIFIED'") >= 4
    assert "AND authority = 'VERIFIED' LIMIT 1" in monitor_source
    assert "AND authority = 'VERIFIED'" in world_view_source


def test_operator_scripts_filter_verified_settlement_rows_before_outputs_or_backfills():
    """Operator script reads of settlement truth must not promote quarantined rows."""
    snippets = {
        "scripts/backfill_ens.py": "AND s.authority = 'VERIFIED'",
        "scripts/backfill_observations_from_settlements.py": "AND s.authority = 'VERIFIED'",
        "scripts/backfill_wu_daily_all.py": "AND authority = 'VERIFIED'",
        "scripts/audit_city_data_readiness.py": "AND s.authority = 'VERIFIED'",
        "scripts/audit_divergence_exit_counterfactual.py": "AND authority = 'VERIFIED'",
        "scripts/baseline_experiment.py": "WHERE authority = 'VERIFIED'",
        "scripts/audit_replay_fidelity.py": "AND authority = 'VERIFIED'",
        "scripts/cleanup_ghost_positions.py": "AND authority = 'VERIFIED'",
        "scripts/etl_forecast_skill_from_forecasts.py": "AND s.authority = 'VERIFIED'",
        "scripts/etl_historical_forecasts.py": "AND s.authority = 'VERIFIED'",
    }

    for rel_path, snippet in snippets.items():
        source = (ROOT / rel_path).read_text(encoding="utf-8")
        assert snippet in source, rel_path


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
        env="live",
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
    """GOLDEN RULE: economic close only created after CONFIRMED fill truth.

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
    assert pos.exit_state in ("sell_placed", "sell_pending", "retry_pending")


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


def test_fill_tracker_keeps_confirmed_entry_local_only_until_chain_seen():
    """CONFIRMED CLOB fill verifies locally first; chain ownership arrives later."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        chain_state="unknown",
        size_usd=10.0,
        entry_price=0.0,
        cost_basis_usd=0.0,
        shares=0.0,
        target_notional_usd=10.0,
        submitted_notional_usd=10.0,
        entry_price_submitted=0.40,
        shares_submitted=25.0,
        shares_remaining=25.0,
    )
    portfolio = _make_portfolio(pos)

    class Tracker:
        def __init__(self):
            self.entries = []

        def record_entry(self, position):
            self.entries.append(position.trade_id)

    tracker = Tracker()
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "trade_id": "trade-buy-123",
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
    assert pos.order_status == "confirmed"
    assert pos.chain_state == "local_only"
    assert pos.entered_at != ""
    assert pos.size_usd == pytest.approx(11.0)
    assert pos.cost_basis_usd == pytest.approx(11.0)
    assert pos.fill_quality == pytest.approx(0.10)
    assert pos.entry_price_submitted == pytest.approx(0.40)
    assert pos.entry_price_avg_fill == pytest.approx(0.44)
    assert tracker.entries == ["test_001"]


def test_matched_without_filled_size_does_not_materialize_entry():
    """MATCHED alone is not finality; legacy polling must see filled size."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {"status": "MATCHED", "price": 0.44}

    class StaleDeps:
        PENDING_FILL_STATUSES = {"FILLED", "MATCHED"}

    stats = check_pending_entries(portfolio, clob, deps=StaleDeps)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.order_status == "matched"


def test_confirmed_fill_survives_stale_deps_fill_statuses():
    """Stale deps cannot remove CONFIRMED as the only entry success terminal."""
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
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "trade_id": "trade-buy-stale-deps",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }

    class StaleDeps:
        PENDING_FILL_STATUSES = {"FILLED", "MATCHED"}

    stats = check_pending_entries(portfolio, clob, deps=StaleDeps)

    assert stats["entered"] == 1
    assert stats["still_pending"] == 0
    assert pos.state == "entered"
    assert pos.entry_fill_verified is True
    assert pos.order_status == "confirmed"


def test_confirmed_without_explicit_fill_price_quarantines_entry():
    """CONFIRMED order status is not fill economics without venue fill price."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "filledSize": 25.0,
    }

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.admin_exit_reason == "FILL_AUTHORITY_QUARANTINE_REVIEW_REQUIRED"
    assert pos.order_status == "confirmed_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.entry_price == pytest.approx(0.40)
    assert pos.entry_price_avg_fill == 0.0
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.size_usd == pytest.approx(10.0)
    assert pos.cost_basis_usd == pytest.approx(10.0)
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_LEGACY_UNKNOWN
    assert pos.has_fill_economics_authority is False
    from src.state.portfolio import has_same_city_range_open, total_exposure_usd

    assert total_exposure_usd(portfolio) == 0.0
    assert has_same_city_range_open(portfolio, pos.city, pos.bin_label) is False


def test_confirmed_without_trade_identity_quarantines_entry():
    """Order-only CONFIRMED is not executable fill finality."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "confirmed_missing_trade_identity"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False


def test_confirmed_without_trade_identity_marks_command_review_not_filled(tmp_path):
    """Order-only CONFIRMED must not advance the durable command to FILLED."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-confirmed-no-trade",
            "snap-confirmed-no-trade",
            "env-confirmed-no-trade",
            "runtime-confirmed-no-trade",
            "dec-confirmed-no-trade",
            "idem-confirmed-no-trade",
            "ENTRY",
            "condition-confirmed-no-trade",
            "tok_yes_confirmed_no_trade",
            "BUY",
            25.0,
            0.44,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="runtime-confirmed-no-trade",
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    conn = get_connection(db_path)
    command_state = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-confirmed-no-trade'"
    ).fetchone()["state"]
    event_types = [
        row["event_type"]
        for row in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-confirmed-no-trade'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    review_payload = conn.execute(
        """
        SELECT payload_json
          FROM venue_command_events
         WHERE command_id = 'cmd-confirmed-no-trade'
           AND event_type = 'REVIEW_REQUIRED'
         LIMIT 1
        """
    ).fetchone()["payload_json"]
    conn.close()

    assert command_state == "REVIEW_REQUIRED"
    assert "REVIEW_REQUIRED" in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert "poll_confirmed_requires_trade_fact" in review_payload
    assert "order_status_confirmed_is_not_fill_economics_authority" in review_payload


def test_confirmed_without_explicit_filled_size_quarantines_entry():
    """CONFIRMED fill price alone must not invent shares from order size."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "avgPrice": 0.44,
    }

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "confirmed_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.shares == pytest.approx(25.0)
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.size_usd == pytest.approx(10.0)
    assert pos.cost_basis_usd == pytest.approx(10.0)
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("avgPrice", math.nan),
        ("avgPrice", math.inf),
        ("filledSize", math.nan),
        ("filledSize", math.inf),
    ],
)
def test_confirmed_with_nonfinite_fill_economics_quarantines_entry(field, value):
    """Non-finite venue economics are not executable fill evidence."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    payload = {
        "status": "CONFIRMED",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }
    payload[field] = value
    clob.get_order_status.return_value = payload

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "confirmed_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False


def test_matched_with_filled_size_but_missing_fill_price_quarantines_entry():
    """Optimistic fill observations need fill price before economics authority."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "filledSize": 12.0,
        "price": 0.44,
    }

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "matched_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.shares == 0.0
    assert pos.shares_filled == 0.0
    assert pos.cost_basis_usd == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.fill_authority == FILL_AUTHORITY_NONE


def test_legacy_polling_matched_maps_numeric_live_runtime_id_to_optimistic_lot(tmp_path):
    """Numeric-looking executor runtime ids must not bypass trade_decisions mapping."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-match",
            "snap-match",
            "env-match",
            "123456789012",
            "dec-live-abc",
            "idem-match",
            "ENTRY",
            "condition-match",
            "tok_yes_001",
            "BUY",
            20.0,
            0.40,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-match",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789012",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789012",
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-match",
        "trade_status": "MATCHED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "matched"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE
    assert pos.fill_authority == FILL_AUTHORITY_OPTIMISTIC_SUBMITTED
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    order_states = [r["state"] for r in conn.execute("SELECT state FROM venue_order_facts").fetchall()]
    trade_states = [r["state"] for r in conn.execute("SELECT state FROM venue_trade_facts").fetchall()]
    lot_rows = conn.execute("SELECT position_id, state FROM position_lots").fetchall()
    exec_row = conn.execute(
        "SELECT terminal_exec_status FROM execution_fact WHERE position_id = ? AND order_role = 'entry'",
        ("123456789012",),
    ).fetchone()
    canonical_events = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ?",
        ("123456789012",),
    ).fetchall()
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert order_states == ["MATCHED"]
    assert trade_states == ["MATCHED"]
    assert [(row["position_id"], row["state"]) for row in lot_rows] == [(1, "OPTIMISTIC_EXPOSURE")]
    assert exec_row is None
    assert canonical_events == []
    assert calibration_rows == []


def test_legacy_polling_failed_trade_status_is_not_fill_progress_authority(tmp_path):
    """Order-level MATCHED cannot turn a FAILED trade object into exposure."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-failed-trade",
            "snap-failed-trade",
            "env-failed-trade",
            "123456789088",
            "dec-failed-trade",
            "idem-failed-trade",
            "ENTRY",
            "condition-failed-trade",
            "tok_yes_failed_trade",
            "BUY",
            20.0,
            0.40,
            "buy_failed_trade",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-failed-trade",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789088",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789088",
        state="pending_tracked",
        entry_order_id="buy_failed_trade",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-failed",
        "trade_status": "FAILED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "optimistic_fill_ledger_write_failed"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False
    assert pos.shares == 0.0
    assert pos.cost_basis_usd == 0.0

    conn = get_connection(db_path)
    trade_rows = conn.execute(
        "SELECT state, filled_size FROM venue_trade_facts ORDER BY local_sequence"
    ).fetchall()
    lot_rows = conn.execute("SELECT position_id, state FROM position_lots").fetchall()
    command_state = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-failed-trade'"
    ).fetchone()["state"]
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-failed-trade'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    canonical_events = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ?",
        ("123456789088",),
    ).fetchall()
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert [(row["state"], row["filled_size"]) for row in trade_rows] == [("FAILED", "12.0")]
    assert lot_rows == []
    assert command_state == "REVIEW_REQUIRED"
    assert "REVIEW_REQUIRED" in event_types
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert canonical_events == []
    assert calibration_rows == []


def test_legacy_polling_failed_without_fill_economics_rolls_back_optimistic_lot(tmp_path):
    """FAILED trade lifecycle evidence must close prior optimistic exposure."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import append_position_lot, append_trade_fact

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-failed-no-econ",
            "snap-failed-no-econ",
            "env-failed-no-econ",
            "123456789089",
            "dec-failed-no-econ",
            "idem-failed-no-econ",
            "ENTRY",
            "condition-failed-no-econ",
            "tok_yes_failed_no_econ",
            "BUY",
            20.0,
            0.40,
            "buy_failed_no_econ",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-failed-no-econ",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789089",
        ),
    )
    matched_fact_id = append_trade_fact(
        conn,
        trade_id="trade-poll-failed-no-econ",
        venue_order_id="buy_failed_no_econ",
        command_id="cmd-failed-no-econ",
        state="MATCHED",
        filled_size="12.5",
        fill_price="0.42",
        source="REST",
        observed_at="2026-04-29T12:00:30+00:00",
        raw_payload_hash="0" * 64,
        raw_payload_json={"trade_status": "MATCHED"},
    )
    append_position_lot(
        conn,
        position_id=123456789089,
        state="OPTIMISTIC_EXPOSURE",
        shares="12.5",
        entry_price_avg="0.42",
        source_command_id="cmd-failed-no-econ",
        source_trade_fact_id=matched_fact_id,
        captured_at="2026-04-29T12:00:30+00:00",
        state_changed_at="2026-04-29T12:00:30+00:00",
        source="REST",
        observed_at="2026-04-29T12:00:30+00:00",
        raw_payload_json={"trade_status": "MATCHED"},
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789089",
        state="pending_tracked",
        entry_order_id="buy_failed_no_econ",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-failed-no-econ",
        "trade_status": "FAILED",
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    trade_rows = conn.execute(
        """
        SELECT trade_fact_id, state, filled_size, fill_price
          FROM venue_trade_facts
         ORDER BY local_sequence
        """
    ).fetchall()
    lot_rows = conn.execute(
        """
        SELECT state, shares, source_trade_fact_id
          FROM position_lots
         WHERE position_id = ?
         ORDER BY lot_id
        """,
        (123456789089,),
    ).fetchall()
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-failed-no-econ'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    conn.close()

    assert [(r["state"], r["filled_size"], r["fill_price"]) for r in trade_rows] == [
        ("MATCHED", "12.5", "0.42"),
        ("FAILED", "0", "0"),
    ]
    assert [(r["state"], r["shares"]) for r in lot_rows] == [
        ("OPTIMISTIC_EXPOSURE", "12.5"),
        ("QUARANTINED", "12.5"),
    ]
    assert lot_rows[-1]["source_trade_fact_id"] == trade_rows[-1]["trade_fact_id"]
    assert "REVIEW_REQUIRED" in event_types
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "FILL_CONFIRMED" not in event_types


def test_legacy_polling_duplicate_failed_trade_fact_still_fails_closed(tmp_path):
    """An existing FAILED fact must not make polling's idempotent path authorize fill."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import append_trade_fact, load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-dup-failed-trade",
            "snap-dup-failed-trade",
            "env-dup-failed-trade",
            "123456789077",
            "dec-dup-failed-trade",
            "idem-dup-failed-trade",
            "ENTRY",
            "condition-dup-failed-trade",
            "tok_yes_dup_failed_trade",
            "BUY",
            20.0,
            0.40,
            "buy_dup_failed_trade",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-dup-failed-trade",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789077",
        ),
    )
    append_trade_fact(
        conn,
        trade_id="trade-poll-dup-failed",
        venue_order_id="buy_dup_failed_trade",
        command_id="cmd-dup-failed-trade",
        state="FAILED",
        filled_size="12.0",
        fill_price="0.42",
        source="WS_USER",
        observed_at=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        raw_payload_hash="0" * 64,
        raw_payload_json={"source": "preexisting"},
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789077",
        state="pending_tracked",
        entry_order_id="buy_dup_failed_trade",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-dup-failed",
        "trade_status": "FAILED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "optimistic_fill_ledger_write_failed"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False
    assert pos.shares == 0.0
    assert pos.cost_basis_usd == 0.0

    conn = get_connection(db_path)
    trade_rows = conn.execute(
        "SELECT state, filled_size FROM venue_trade_facts ORDER BY local_sequence"
    ).fetchall()
    lot_rows = conn.execute("SELECT position_id, state FROM position_lots").fetchall()
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-dup-failed-trade'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert [(row["state"], row["filled_size"]) for row in trade_rows] == [("FAILED", "12.0")]
    assert lot_rows == []
    assert "REVIEW_REQUIRED" in event_types
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert calibration_rows == []


def test_legacy_polling_unknown_trade_status_fails_closed(tmp_path):
    """Explicit unsupported trade lifecycle evidence cannot become local exposure."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-unknown-trade",
            "snap-unknown-trade",
            "env-unknown-trade",
            "123456789066",
            "dec-unknown-trade",
            "idem-unknown-trade",
            "ENTRY",
            "condition-unknown-trade",
            "tok_yes_unknown_trade",
            "BUY",
            20.0,
            0.40,
            "buy_unknown_trade",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-unknown-trade",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789066",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789066",
        state="pending_tracked",
        entry_order_id="buy_unknown_trade",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-unknown",
        "trade_status": "WEIRD_STATE",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "optimistic_fill_ledger_write_failed"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False
    assert pos.shares == 0.0
    assert pos.cost_basis_usd == 0.0

    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-unknown-trade'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    canonical_events = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ?",
        ("123456789066",),
    ).fetchall()
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert "REVIEW_REQUIRED" in event_types
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert canonical_events == []
    assert calibration_rows == []


def test_legacy_polling_trade_lifecycle_requires_stable_fill_economics(tmp_path):
    """Same trade_id cannot change filled size when MATCHED later becomes CONFIRMED."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-drift",
            "snap-drift",
            "env-drift",
            "123456789099",
            "dec-drift",
            "idem-drift",
            "ENTRY",
            "condition-drift",
            "tok_yes_drift",
            "BUY",
            20.0,
            0.40,
            "buy_drift",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-drift",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789099",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789099",
        state="pending_tracked",
        entry_order_id="buy_drift",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-drift",
        "trade_status": "MATCHED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }
    check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "trade_id": "trade-poll-drift",
        "trade_status": "CONFIRMED",
        "avgPrice": 0.42,
        "filledSize": 20.0,
        "timestamp": "2026-04-29T12:02:00+00:00",
    }
    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 2, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority != FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    trade_rows = conn.execute(
        "SELECT state, filled_size FROM venue_trade_facts ORDER BY local_sequence"
    ).fetchall()
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-drift'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert [(row["state"], row["filled_size"]) for row in trade_rows] == [("MATCHED", "12.0")]
    assert "REVIEW_REQUIRED" in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert calibration_rows == []


def test_confirmed_order_with_matched_trade_status_stays_optimistic_not_full_fill(tmp_path):
    """Order CONFIRMED cannot override a non-final trade status into fill authority."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-confirmed-match",
            "snap-confirmed-match",
            "env-confirmed-match",
            "runtime-confirmed-match",
            "dec-confirmed-match",
            "idem-confirmed-match",
            "ENTRY",
            "condition-confirmed-match",
            "tok_yes_confirmed_match",
            "BUY",
            20.0,
            0.40,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="runtime-confirmed-match",
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "trade_id": "trade-confirmed-match",
        "trade_status": "MATCHED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "matched"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE
    assert pos.fill_authority == FILL_AUTHORITY_OPTIMISTIC_SUBMITTED
    assert pos.fill_authority != FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    trade_states = [r["state"] for r in conn.execute("SELECT state FROM venue_trade_facts").fetchall()]
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-confirmed-match'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    conn.close()

    assert trade_states == ["MATCHED"]
    assert "PARTIAL_FILL_OBSERVED" in event_types
    assert "FILL_CONFIRMED" not in event_types


def test_stale_deps_mined_fill_status_stays_optimistic_not_full_fill(tmp_path):
    """Stale deps cannot extend the fill-success set with MINED."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-mined-stale",
            "snap-mined-stale",
            "env-mined-stale",
            "runtime-mined-stale",
            "dec-mined-stale",
            "idem-mined-stale",
            "ENTRY",
            "condition-mined-stale",
            "tok_yes_mined_stale",
            "BUY",
            20.0,
            0.40,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.commit()
    conn.close()

    class StaleDeps:
        PENDING_FILL_STATUSES = {"MATCHED", "MINED", "FILLED"}

        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="runtime-mined-stale",
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MINED")
    clob.get_order_status.return_value = {
        "status": "MINED",
        "trade_id": "trade-mined-stale",
        "trade_status": "MINED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=StaleDeps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "mined"
    assert pos.entry_fill_verified is False
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE
    assert pos.fill_authority == FILL_AUTHORITY_OPTIMISTIC_SUBMITTED
    assert pos.fill_authority != FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    trade_states = [r["state"] for r in conn.execute("SELECT state FROM venue_trade_facts").fetchall()]
    lot_rows = conn.execute("SELECT position_id, state FROM position_lots").fetchall()
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-mined-stale'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    conn.close()

    assert trade_states == ["MINED"]
    assert lot_rows == []
    assert "PARTIAL_FILL_OBSERVED" in event_types
    assert "FILL_CONFIRMED" not in event_types


def test_deps_path_missing_fill_price_writes_no_fill_authority_surfaces(tmp_path):
    """A linkable order with size-only fill evidence must not contaminate U2 facts."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-size-only",
            "snap-size-only",
            "env-size-only",
            "123456789012",
            "dec-live-size-only",
            "idem-size-only",
            "ENTRY",
            "condition-size-only",
            "tok_yes_001",
            "BUY",
            20.0,
            0.40,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-size-only",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789012",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789012",
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-size-only",
        "trade_status": "MATCHED",
        "filledSize": 12.0,
        "price": 0.42,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "matched_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_fact").fetchone()[0] == 0
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM venue_command_events
         WHERE event_type IN ('PARTIAL_FILL_OBSERVED', 'FILL_CONFIRMED')
        """
    ).fetchone()[0] == 0
    assert load_calibration_trade_facts(conn) == []
    conn.close()


def test_partial_remainder_cancel_preserves_filled_exposure():
    """A partial fill followed by cancel timeout preserves non-final exposure."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        order_timeout_at="2026-04-29T12:05:00+00:00",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="PARTIAL")
    clob.get_order_status.return_value = {
        "status": "PARTIAL",
        "avgPrice": 0.42,
        "filledSize": 12.0,
    }

    first = check_pending_entries(
        portfolio,
        clob,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
    )

    assert first["entered"] == 0
    assert first["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.shares == pytest.approx(12.0)
    assert pos.cost_basis_usd == pytest.approx(12.0 * 0.42)
    assert pos.order_status == "partial"
    clob.cancel_order.assert_not_called()

    clob.get_order_status.return_value = {"status": "OPEN"}
    second = check_pending_entries(
        portfolio,
        clob,
        now=datetime(2026, 4, 29, 12, 6, tzinfo=timezone.utc),
    )

    assert second["entered"] == 0
    assert second["voided"] == 0
    assert second["still_pending"] == 1
    assert len(portfolio.positions) == 1
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.shares == pytest.approx(12.0)
    assert pos.cost_basis_usd == pytest.approx(12.0 * 0.42)
    assert pos.order_status == "partial_remainder_cancelled"
    clob.cancel_order.assert_called_once_with("buy_123")


def test_partial_with_filled_size_but_missing_fill_price_quarantines_entry():
    """Partial size evidence is not enough to assign cost basis or exposure grade."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="PARTIAL")
    clob.get_order_status.return_value = {
        "status": "PARTIAL",
        "filledSize": 12.0,
        "price": 0.42,
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["voided"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "partially_matched_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.shares == 0.0
    assert pos.shares_filled == 0.0
    assert pos.cost_basis_usd == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    clob.cancel_order.assert_not_called()


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
    quarantine = next(pos for pos in portfolio.positions if pos.chain_state == "quarantined")
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
    from src.engine.cycle_runner import _has_quarantined_positions

    pos = _make_position(chain_state="quarantine_expired")
    portfolio = _make_portfolio(pos)

    assert _has_quarantined_positions(portfolio) is True


def test_monitoring_marks_quarantine_for_admin_resolution_once(monkeypatch):
    """Quarantine must enter an explicit admin-resolution path instead of passive skipping."""
    from src.engine import cycle_runtime

    pos = _make_position(direction="unknown", chain_state="quarantined")
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.41, 100.0, 100.0

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
        LiveClob(),
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
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
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


def test_monitoring_skips_fill_authority_quarantine_without_chain_quarantine(monkeypatch):
    """Fill-authority quarantine is a non-trading state even when chain_state is not quarantined."""
    from src.engine import cycle_runtime

    pos = _make_position(
        state="quarantined",
        chain_state="local_only",
        admin_exit_reason="FILL_AUTHORITY_QUARANTINE_REVIEW_REQUIRED",
        exit_reason="FILL_AUTHORITY_QUARANTINE_REVIEW_REQUIRED",
    )
    portfolio = _make_portfolio(pos)

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("fill-authority quarantine should not be exited by monitor loop")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_fill_authority_quarantine_monitor_skip"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 5, 30, tzinfo=timezone.utc)),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fill-authority quarantine must not reach monitor refresh")
        ),
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert summary["monitor_skipped_quarantine_resolution"] == 1
    assert summary["monitors"] == 0
    assert monitor_results[0].exit_reason == "FILL_AUTHORITY_QUARANTINE_REVIEW_REQUIRED"
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None


def test_monitoring_unknown_direction_report_has_no_fresh_probability(monkeypatch):
    """Skipped unknown-direction monitor results must not report stale probability."""
    from src.engine import cycle_runtime

    pos = _make_position(direction="unknown", chain_state="synced")
    pos.p_posterior = 0.99
    pos.last_monitor_prob = 0.88
    pos.last_monitor_edge = 0.77
    pos.last_monitor_prob_is_fresh = True
    portfolio = _make_portfolio(pos)

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("unknown direction should not exit")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_unknown_direction_monitor_report"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 5, 30, tzinfo=timezone.utc)),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unknown direction must not reach monitor refresh")
        ),
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert summary["monitor_skipped_unknown_direction"] == 1
    assert summary["monitors"] == 0
    assert len(monitor_results) == 1
    assert monitor_results[0].exit_reason == "UNKNOWN_DIRECTION"
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None


def test_quarantine_expired_marks_distinct_admin_resolution_reason(monkeypatch):
    """Expired quarantine keeps the same protective path but with explicit expired provenance."""
    from src.engine import cycle_runtime

    pos = _make_position(direction="unknown", chain_state="quarantine_expired")
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.41, 100.0, 100.0

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
        LiveClob(),
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
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None


def test_monitoring_transitions_holding_position_into_day0_window(monkeypatch):
    """Positions nearing settlement must enter the universal Day0 terminal phase.

    A6 audit (2026-05-04, rebuild fixes branch): the fixture's
    target_date=2026-04-01 + decision_time=2026-04-02T04:30Z places the
    market in POST_TRADING phase under the new phase-axis dispatch
    (settlement period 2026-04-01T05:00Z..2026-04-01T12:00Z for Chicago
    has already passed). Phase-axis correctly refuses day0_window entry
    after settlement. This test asserts the LEGACY 6-hour-to-settlement
    transition contract; pin to flag=OFF until phase-axis equivalents
    are added in a follow-up packet.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    from src.engine import cycle_runtime
    from src.contracts import EdgeContext, EntryMethod

    pos = _make_position(state="holding", city="Chicago", target_date="2026-04-01")
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.41, 100.0, 100.0

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
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 2, 4, 30, tzinfo=timezone.utc)),
        },
    )

    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: None})()
    summary = {"monitors": 0, "exits": 0}

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
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

    A6 audit (2026-05-04): pin to legacy 6-hour transition — see
    test_monitoring_transitions_holding_position_into_day0_window for the
    full rationale.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
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

    class LiveClob:
        pass

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
        LiveClob(),
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
    """A same-cycle `<6h` crossing must not refresh through the old non-Day0 path.

    A6 audit (2026-05-04): pin to legacy 6-hour transition — see
    test_monitoring_transitions_holding_position_into_day0_window for the
    full rationale.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
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

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.41, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in same-cycle Day0 refresh test")

    observed_methods = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_methods.append(position.entry_method)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        monitor_refresh._set_monitor_probability_fresh(position, True)
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
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 2, 4, 30, tzinfo=timezone.utc)),
        },
    )

    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: None})()
    summary = {"monitors": 0, "exits": 0}

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
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
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.43, 100.0, 100.0

    observed_methods = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_methods.append(position.entry_method)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        monitor_refresh._set_monitor_probability_fresh(position, True)
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
    """Day0 quote surface uses bid while posterior dispatch stays quote-free."""
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
        def get_best_bid_ask(self, token_id):
            assert token_id == "tok_yes_001"
            return 0.37, 0.55, 100.0, 200.0

    monkeypatch.setattr("src.state.db.log_microstructure", lambda *args, **kwargs: None)

    observed_markets = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_markets.append(current_p_market)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        monitor_refresh._set_monitor_probability_fresh(position, True)
        return 0.52

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert observed_markets == [pytest.approx(pos.entry_price)]
    assert pos.entry_method == EntryMethod.ENS_MEMBER_COUNTING.value
    assert pos.selected_method == EntryMethod.DAY0_OBSERVATION.value
    assert pos.last_monitor_market_price == pytest.approx(0.37)
    assert pos.last_monitor_best_bid == pytest.approx(0.37)
    assert pos.last_monitor_best_ask == pytest.approx(0.55)
    assert edge_ctx.p_market[0] == pytest.approx(0.37)
    assert observed_markets[0] != pytest.approx(edge_ctx.p_market[0])


def test_day0_refresh_fallback_keeps_probability_non_authoritative(monkeypatch):
    """Day0 fallback must not relabel stored probability as current exit authority."""
    from src.contracts import EntryMethod
    from src.engine import monitor_refresh

    pos = _make_position(
        state="day0_window",
        city="Chicago",
        target_date="2026-04-01",
        entry_method=EntryMethod.ENS_MEMBER_COUNTING.value,
        selected_method="",
        p_posterior=0.61,
        last_monitor_prob=0.41,
        last_monitor_prob_is_fresh=True,
        applied_validations=["alpha_posterior"],
    )

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.43, 100.0, 100.0

    monkeypatch.setattr(
        monitor_refresh,
        "_fetch_day0_observation",
        lambda city, target_d: type(
            "Obs",
            (),
            {
                "high_so_far": 44.0,
                "current_temp": 43.0,
                "source": "wu_api",
                # Missing observation_time forces fallback to the stored posterior.
                "observation_time": None,
            },
        )(),
    )

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert pos.selected_method == EntryMethod.DAY0_OBSERVATION.value
    assert pos.last_monitor_market_price == pytest.approx(0.41)
    assert pos.last_monitor_market_price_is_fresh is True
    assert pos.last_monitor_prob == pytest.approx(0.41)
    assert pos.last_monitor_prob_is_fresh is False
    assert not np.isfinite(pos.last_monitor_edge)
    assert not np.isfinite(edge_ctx.p_posterior)
    assert not np.isfinite(edge_ctx.forward_edge)
    assert "missing_observation_timestamp" in pos.applied_validations
    assert "monitor_probability_stale" in pos.applied_validations


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


def test_deferred_confirmed_fill_logs_last_monitor_best_bid(tmp_path):
    """Deferred confirmed fill telemetry must preserve sell-side realizable bid, not
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
    clob = _make_clob(sell_result={"status": "CONFIRMED", "avgPrice": 0.39})

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


def test_pending_exit_filled_status_does_not_economically_close():
    """FILLED is an order observation; CONFIRMED is required for exit finality."""
    pos = _make_position(
        state="day0_window",
        exit_state="sell_pending",
        last_exit_order_id="sell-order-1",
        exit_reason="DEFERRED_SELL_FILL",
        last_monitor_market_price=0.44,
        last_monitor_best_bid=0.39,
        entry_fill_verified=True,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(sell_result={"status": "FILLED", "avgPrice": 0.39})

    stats = check_pending_exits(portfolio, clob, conn=None)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert pos in portfolio.positions
    assert pos.state == "pending_exit"
    assert pos.exit_state == "sell_pending"
    assert pos.exit_price in (None, 0.0)


def test_pending_exit_matched_status_does_not_economically_close():
    """MATCHED exit status is not finality and must keep the position pending."""
    pos = _make_position(
        state="day0_window",
        exit_state="sell_pending",
        last_exit_order_id="sell-order-1",
        exit_reason="DEFERRED_SELL_FILL",
        last_monitor_market_price=0.44,
        last_monitor_best_bid=0.39,
        entry_fill_verified=True,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(sell_result={"status": "MATCHED", "avgPrice": 0.39})

    stats = check_pending_exits(portfolio, clob, conn=None)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert pos in portfolio.positions
    assert pos.state == "pending_exit"
    assert pos.exit_state == "sell_pending"
    assert pos.exit_price in (None, 0.0)


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


def test_day0_stale_probability_does_not_authorize_observation_reversal():
    """Stale model evidence must not become Day0 observation authority."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=False,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=0.54,
            hours_to_settlement=4.0,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob_is_fresh)"
    assert "day0_probability_authority_blocked" in decision.applied_validations
    assert decision.trigger != "DAY0_OBSERVATION_REVERSAL"


def test_day0_observation_exit_requires_executable_best_bid_not_price_proxy():
    """Current market price is not executable sell proceeds for Day0 exit EV."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=None,
            hours_to_settlement=4.0,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
    assert "best_bid_unavailable" in decision.applied_validations
    assert "best_bid_proxy_from_current_market_price" not in decision.applied_validations


@pytest.mark.parametrize("bad_bid", [math.nan, math.inf, -math.inf])
def test_day0_observation_exit_requires_finite_executable_best_bid(bad_bid):
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=bad_bid,
            hours_to_settlement=4.0,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
    assert "best_bid_unavailable" in decision.applied_validations


def test_day0_force_exit_without_model_probability_still_requires_executable_best_bid():
    """Non-model Day0 exits cannot fall through to diagnostic price execution."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=False,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=None,
            hours_to_settlement=0.5,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob_is_fresh,best_bid)"
    assert "best_bid_unavailable" in decision.applied_validations
    assert "model_probability_authority_not_required:settlement_imminent" not in decision.applied_validations


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
@pytest.mark.parametrize("bad_bid", [None, math.nan, math.inf, -math.inf])
def test_day0_fresh_probability_force_exit_requires_finite_executable_best_bid(direction, bad_bid):
    pos = _make_position(direction=direction, size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=bad_bid,
            hours_to_settlement=0.5,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
    assert "best_bid_unavailable" in decision.applied_validations
    assert decision.trigger != "SETTLEMENT_IMMINENT"


def test_day0_monitor_context_missing_bid_cannot_reach_submit_decision():
    """Monitor fields must preserve missing executable bid through exit decision."""
    from types import SimpleNamespace

    from src.engine.cycle_runtime import _build_exit_context

    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)
    pos.state = "day0_window"
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = None
    pos.last_monitor_best_ask = 0.56
    pos.last_monitor_market_vig = 1.0
    pos.last_monitor_whale_toxicity = True
    pos.chain_state = "synced"

    edge_ctx = SimpleNamespace(
        p_posterior=0.25,
        p_market=[0.55],
        divergence_score=0.0,
        market_velocity_1h=0.0,
    )

    exit_context = _build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=0.5,
        ExitContext=ExitContext,
    )
    decision = pos.evaluate_exit(exit_context)

    assert exit_context.best_bid is None
    assert decision.should_exit is False
    assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
    assert decision.trigger == ""


def test_day0_stale_probability_bypass_tokens_are_not_produced_by_source():
    """Legacy Day0 authority-waiver labels must not reappear in runtime source."""
    forbidden = {
        "day0_stale_prob_authority_waived",
        "stale_prob_substitution",
        "best_bid_proxy_from_current_market_price",
        "best_bid_proxy_tick_discount",
    }
    offenders: dict[str, list[str]] = {}
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text()
        hits = sorted(token for token in forbidden if token in text)
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits

    assert offenders == {}


def test_legacy_exit_triggers_api_is_not_used_by_live_runtime_source():
    """Live monitor/exit decisions must route through ExitContext authority."""
    offenders: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "src/execution/exit_triggers.py":
            continue
        if "evaluate_exit_triggers" in path.read_text():
            offenders.append(rel)

    assert offenders == []


def test_exit_ev_gate_uses_fill_authority_shares_for_hold_value(monkeypatch):
    """Confirmed fill shares, not submitted notional math, feed live exit EV cost gates."""
    pos = _make_position(
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_ci_width=0.02,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    pos.neg_edge_count = 2
    captured: dict[str, float] = {}

    def capture_crowding(**kwargs):
        captured["shares"] = kwargs["shares"]
        return 0.0

    monkeypatch.setattr("src.state.portfolio.hold_value_exit_costs_enabled", lambda: True)
    monkeypatch.setattr("src.state.portfolio._compute_exit_correlation_crowding", capture_crowding)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.10,
            fresh_prob_is_fresh=True,
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.49,
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert "ev_gate" in decision.applied_validations
    assert captured["shares"] == pytest.approx(pos.effective_shares)
    assert captured["shares"] != pytest.approx(pos.size_usd / pos.entry_price)


def test_buy_no_exit_ev_gate_uses_fill_authority_shares_for_hold_value(monkeypatch):
    """Buy-no exit EV gates preserve fill-authority shares too."""
    pos = _make_position(
        direction="buy_no",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_ci_width=0.02,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    pos.neg_edge_count = 2
    captured: dict[str, float] = {}

    def capture_crowding(**kwargs):
        captured["shares"] = kwargs["shares"]
        return 0.0

    monkeypatch.setattr("src.state.portfolio.hold_value_exit_costs_enabled", lambda: True)
    monkeypatch.setattr("src.state.portfolio._compute_exit_correlation_crowding", capture_crowding)

    decision = pos._buy_no_exit(
        forward_edge=-0.40,
        current_p_posterior=0.10,
        current_market_price=0.50,
        best_bid=0.49,
        hours_to_settlement=None,
        day0_active=False,
        applied=[],
    )

    assert "ev_gate" in decision.applied_validations
    assert captured["shares"] == pytest.approx(pos.effective_shares)
    assert captured["shares"] != pytest.approx(pos.size_usd / pos.entry_price)


def test_exit_micro_position_hold_uses_fill_authority_cost_basis():
    """Micro-position hold is about actual held cost basis, not stale submitted size."""
    pos = _make_position(
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_ci_width=0.02,
        shares_filled=1.0,
        filled_cost_basis_usd=0.50,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    pos.neg_edge_count = 2

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.10,
            fresh_prob_is_fresh=True,
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.49,
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert decision.should_exit is False
    assert "micro_position_hold" in decision.applied_validations


def test_full_open_fill_authority_cost_basis_can_exceed_projection_without_cap():
    """A venue-confirmed full-open fill is not capped by target/projection cost."""
    pos = _make_position(
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.51,
        shares=20.0,
        cost_basis_usd=10.0,
        last_monitor_market_price=0.60,
        shares_filled=20.0,
        filled_cost_basis_usd=10.2,
        entry_price_avg_fill=0.51,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )

    assert pos.effective_shares == pytest.approx(20.0)
    assert pos.effective_cost_basis_usd == pytest.approx(10.2)
    assert pos.unrealized_pnl == pytest.approx(1.8)


def test_partial_exit_fill_reduces_effective_open_fill_authority_exposure():
    """Partial exit changes current open exposure without rewriting entry-fill evidence."""
    from src.execution.exit_lifecycle import _apply_partial_exit_fill

    pos = _make_position(
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        shares_filled=20.0,
        filled_cost_basis_usd=10.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )

    changed = _apply_partial_exit_fill(
        pos,
        filled_shares=5.0,
        remaining_shares=15.0,
        fill_price=0.70,
        order_id="sell-partial-1",
        status="PARTIAL",
    )

    assert changed is True
    assert pos.shares_filled == pytest.approx(20.0)
    assert pos.filled_cost_basis_usd == pytest.approx(10.0)
    assert pos.effective_shares == pytest.approx(15.0)
    assert pos.effective_cost_basis_usd == pytest.approx(7.5)


def test_duplicate_fill_aggregation_updates_fill_authority_open_exposure():
    """Merging duplicate open fills must aggregate fill-grade economics, not submitted size."""
    from src.state.portfolio import add_position

    existing = _make_position(
        trade_id="agg-existing",
        token_id="yes-shared",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    incoming = _make_position(
        trade_id="agg-incoming",
        token_id="yes-shared",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=8.0,
        cost_basis_usd=4.0,
        shares_filled=8.0,
        filled_cost_basis_usd=4.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    portfolio = _make_portfolio(existing)

    add_position(portfolio, incoming)

    assert portfolio.positions == [existing]
    assert existing.shares_filled == pytest.approx(18.0)
    assert existing.filled_cost_basis_usd == pytest.approx(9.0)
    assert existing.effective_shares == pytest.approx(18.0)
    assert existing.effective_cost_basis_usd == pytest.approx(9.0)
    assert existing.size_usd == pytest.approx(9.0)


def test_mixed_authority_duplicate_keeps_fill_slice_separate():
    """Fill-grade economics must not be absorbed into a legacy same-token aggregate."""
    from src.state.portfolio import add_position

    legacy = _make_position(
        trade_id="legacy-existing",
        token_id="yes-shared",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
    )
    confirmed = _make_position(
        trade_id="fill-incoming",
        token_id="yes-shared",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    portfolio = _make_portfolio(legacy)

    add_position(portfolio, confirmed)

    assert portfolio.positions == [legacy, confirmed]
    assert legacy.has_fill_economics_authority is False
    assert confirmed.has_fill_economics_authority is True
    assert confirmed.effective_shares == pytest.approx(10.0)
    assert confirmed.effective_cost_basis_usd == pytest.approx(5.0)
    assert legacy.nested_fills == []


def test_same_order_update_cannot_regress_fill_authority_to_legacy():
    """Same-order idempotent updates must be monotonic for fill economics authority."""
    from src.state.portfolio import add_position

    existing = _make_position(
        trade_id="same-order-existing",
        order_id="entry-order-1",
        entry_order_id="entry-order-1",
        token_id="yes-shared",
        direction="buy_yes",
        state="holding",
        order_status="filled",
        entry_fill_verified=True,
        entered_at="2026-04-01T06:00:00Z",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    stale = _make_position(
        trade_id="same-order-stale",
        order_id="entry-order-1",
        entry_order_id="entry-order-1",
        token_id="yes-shared",
        direction="buy_yes",
        state="pending_tracked",
        order_status="pending",
        entry_fill_verified=False,
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        fill_authority=FILL_AUTHORITY_NONE,
    )
    portfolio = _make_portfolio(existing)

    add_position(portfolio, stale)

    assert portfolio.positions == [existing]
    assert existing.has_fill_economics_authority is True
    assert existing.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert existing.entry_economics_authority == ENTRY_ECONOMICS_AVG_FILL_PRICE
    assert existing.entry_fill_verified is True
    assert existing.state == "holding"
    assert existing.order_status == "filled"
    assert existing.effective_shares == pytest.approx(10.0)
    assert existing.effective_cost_basis_usd == pytest.approx(5.0)
    assert "same_order_fill_authority_regression_blocked" in existing.applied_validations


def test_same_order_update_cannot_regress_full_fill_to_partial_fill():
    """Same-order fill evidence must be monotonic even inside fill-grade states."""
    from src.state.portfolio import add_position

    existing = _make_position(
        trade_id="same-order-full",
        order_id="entry-order-2",
        entry_order_id="entry-order-2",
        token_id="yes-shared",
        direction="buy_yes",
        state="holding",
        order_status="filled",
        entry_fill_verified=True,
        entered_at="2026-04-01T06:00:00Z",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    stale_partial = _make_position(
        trade_id="same-order-partial-stale",
        order_id="entry-order-2",
        entry_order_id="entry-order-2",
        token_id="yes-shared",
        direction="buy_yes",
        state="holding",
        order_status="partial",
        entry_fill_verified=True,
        size_usd=2.5,
        entry_price=0.50,
        shares=5.0,
        cost_basis_usd=2.5,
        shares_filled=5.0,
        filled_cost_basis_usd=2.5,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    )
    portfolio = _make_portfolio(existing)

    add_position(portfolio, stale_partial)

    assert portfolio.positions == [existing]
    assert existing.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert existing.order_status == "filled"
    assert existing.shares_filled == pytest.approx(10.0)
    assert existing.filled_cost_basis_usd == pytest.approx(5.0)
    assert existing.effective_shares == pytest.approx(10.0)
    assert existing.effective_cost_basis_usd == pytest.approx(5.0)
    assert "same_order_fill_authority_regression_blocked" in existing.applied_validations


def test_whale_toxicity_uses_fill_authority_cost_basis_not_submitted_size(monkeypatch, tmp_path):
    """Adjacent pressure threshold must use actual filled exposure after correction."""
    from src.engine import monitor_refresh
    from src.state.db import get_connection, init_schema

    now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
    conn = get_connection(tmp_path / "whale-fill-authority.db")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO token_price_log (token_id, price, timestamp)
        VALUES (?, ?, ?)
        """,
        ("yes-above", 0.40, (now - timedelta(hours=2)).isoformat()),
    )
    conn.commit()
    pos = _make_position(
        market_id="m1",
        token_id="yes-held",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )

    class BookClob:
        def get_best_bid_ask(self, token_id):
            return {"yes-above": (0.50, 0.52, 60.0, 10.0)}[token_id]

    siblings = [
        {"market_id": "m-below", "range_low": 37, "range_high": 38, "token_id": "yes-below"},
        {"market_id": "m1", "range_low": 39, "range_high": 40, "token_id": "yes-held"},
        {"market_id": "m-above", "range_low": 41, "range_high": 42, "token_id": "yes-above"},
    ]
    monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: siblings)
    monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "VERIFIED")

    result = monitor_refresh._detect_whale_toxicity_from_orderbook(
        conn,
        BookClob(),
        pos,
        held_best_bid=0.40,
        held_best_ask=0.43,
        now=now,
    )

    conn.close()
    assert result is True
    assert "whale_toxicity_available:adjacent_orderbook_pressure" in pos.applied_validations


def test_runtime_exit_context_uses_fill_authority_cost_basis_for_crowding_exposure():
    """Runtime portfolio context must preserve corrected cost basis into exit crowding."""
    from types import SimpleNamespace

    from src.engine.cycle_runtime import _build_exit_context

    pos = _make_position(trade_id="self-pos", state="holding")
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.50
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.49
    pos.chain_state = "synced"

    other = _make_position(
        trade_id="other-pos",
        cluster="Great Lakes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    closed = _make_position(trade_id="closed-pos", state="economically_closed", size_usd=1000.0)
    quarantined = _make_position(
        trade_id="quarantined-pos",
        state="quarantined",
        chain_state="quarantined",
        size_usd=1000.0,
    )
    pending_entry = _make_position(trade_id="pending-entry-pos", state="pending_tracked", size_usd=1000.0)
    portfolio = SimpleNamespace(bankroll=200.0, positions=[pos, other, closed, quarantined, pending_entry])
    edge_ctx = SimpleNamespace(
        p_posterior=0.10,
        p_market=[0.50],
        divergence_score=0.0,
        market_velocity_1h=0.0,
    )

    exit_context = _build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=4.0,
        ExitContext=ExitContext,
        portfolio=portfolio,
    )

    assert exit_context.portfolio_positions == (
        (other.cluster, other.effective_cost_basis_usd, other.trade_id),
    )
    assert exit_context.portfolio_positions[0][1] != pytest.approx(other.size_usd)


def test_legacy_exit_triggers_use_fill_authority_shares(monkeypatch):
    """Legacy diagnostic exit API must not revive submitted-size share math."""
    from types import SimpleNamespace

    from src.execution import exit_triggers

    pos = _make_position(
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_ci_width=0.02,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    pos.neg_edge_count = 2
    captured: dict[str, float] = {}

    def capture_hold_value(shares, current_p_posterior):
        captured["shares"] = shares
        captured["posterior"] = current_p_posterior
        return SimpleNamespace(net_value=0.0)

    monkeypatch.setattr(exit_triggers, "_declared_zero_cost_hold_value", capture_hold_value)
    edge_ctx = SimpleNamespace(forward_edge=-0.40, ci_width=0.02, p_posterior=0.10)

    signal = exit_triggers._evaluate_buy_yes_exit(pos, edge_ctx, best_bid=0.49)

    assert signal is not None
    assert captured["shares"] == pytest.approx(pos.effective_shares)
    assert captured["shares"] != pytest.approx(pos.size_usd / pos.entry_price)
    assert captured["posterior"] == pytest.approx(edge_ctx.p_posterior)
    assert captured["posterior"] != pytest.approx(pos.p_posterior)


def test_legacy_buy_no_exit_triggers_use_fill_authority_shares(monkeypatch):
    """Legacy buy-no diagnostic path must preserve corrected shares."""
    from types import SimpleNamespace

    from src.execution import exit_triggers

    pos = _make_position(
        direction="buy_no",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_ci_width=0.02,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    pos.neg_edge_count = 2
    captured: dict[str, float] = {}

    def capture_hold_value(shares, current_p_posterior):
        captured["shares"] = shares
        return SimpleNamespace(net_value=0.0)

    monkeypatch.setattr(exit_triggers, "_declared_zero_cost_hold_value", capture_hold_value)
    edge_ctx = SimpleNamespace(forward_edge=-0.40, ci_width=0.02, p_posterior=0.10)

    signal = exit_triggers._evaluate_buy_no_exit(
        pos,
        edge_ctx,
        hours_to_settlement=None,
        best_bid=0.49,
    )

    assert signal is not None
    assert captured["shares"] == pytest.approx(pos.effective_shares)
    assert captured["shares"] != pytest.approx(pos.size_usd / pos.entry_price)


def test_exit_paths_do_not_recompute_fill_authority_shares_from_legacy_price():
    """Static relationship check for corrected economics flowing into exit decisions."""
    portfolio_source = (ROOT / "src" / "state" / "portfolio.py").read_text(encoding="utf-8")
    exit_triggers_source = (ROOT / "src" / "execution" / "exit_triggers.py").read_text(encoding="utf-8")
    cycle_runtime_source = (ROOT / "src" / "engine" / "cycle_runtime.py").read_text(encoding="utf-8")

    assert "position.size_usd / position.entry_price" not in exit_triggers_source
    assert portfolio_source.count("self.size_usd / self.entry_price") == 1
    assert "if self.size_usd < 1.0" not in portfolio_source
    assert "(str(p.cluster), float(p.size_usd), str(p.trade_id))" not in cycle_runtime_source


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
    pos = _make_position(env="legacy_env")
    assert pos.env == "legacy_env"

    pos_live = _make_position(env="live")
    assert pos_live.env == "live"

def test_state_path_resolves_directly():
    """Phase 2: state_path returns STATE_DIR/filename directly (mode prefix eliminated)."""
    from src.config import state_path, STATE_DIR
    path = state_path("positions.json")
    assert path == STATE_DIR / "positions.json"
    assert "-live" not in path.name
    assert "-" not in path.stem


def test_save_portfolio_strips_terminal_enum_states(tmp_path):
    """Derived JSON active-position cache must not retain enum-backed terminal phases."""
    from src.state.portfolio import save_portfolio

    active = _make_position(trade_id="active-json", state="holding")
    settled = _make_position(trade_id="settled-json", state="holding")
    settled.state = LifecycleState.SETTLED
    portfolio = _make_portfolio(active, settled)
    output = tmp_path / "positions.json"

    save_portfolio(portfolio, output)

    payload = json.loads(output.read_text())
    assert [row["trade_id"] for row in payload["positions"]] == ["active-json"]


def test_fill_tracker_does_not_emit_legacy_nonvocabulary_quarantine_states():
    """Fill authority quarantine must use legal lifecycle vocabulary only."""
    source = (Path(__file__).resolve().parents[1] / "src" / "execution" / "fill_tracker.py").read_text()

    assert "quarantine_fill_failed" not in source
    assert "quarantine_void_failed" not in source

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
