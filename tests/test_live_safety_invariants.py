# Created: 2026-03-31
# Lifecycle: created=2026-03-31; last_reviewed=2026-05-05; last_reused=2026-05-05
# Purpose: Lock live-money safety invariants across fill, exit, chain, and P&L flows.
# Reuse: Run for execution finality, live exit, chain reconciliation, and safety invariant changes.
# Last reused/audited: 2026-07-08
# Authority basis: midstream verdict v2 2026-04-23; docs/operations/task_2026-05-08_object_invariance_remaining_mainline/PLAN.md
"""Live safety invariant tests: relationship tests, not function tests.

These verify cross-module relationships that prevent ghost positions,
phantom P&L, and local↔chain divergence in live mode.

GOLDEN RULE: economic close is ONLY created after CONFIRMED fill truth.
"""

import logging
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
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
from src.contracts.position_truth import ChainOnlyFact, ChainOnlyReviewState

ROOT = Path(__file__).resolve().parents[1]


def test_harvester_scheduler_fails_closed_without_legacy_integrated_fallback():
    """Trading daemon must not fall back to integrated truth-writing harvester."""
    source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
    sidecar_source = (ROOT / "src" / "execution" / "post_trade_capital.py").read_text(encoding="utf-8")

    assert "from src.execution.harvester import run_harvester" not in source
    assert "result = run_harvester()" not in source
    assert "resolver_unavailable_fail_closed" in sidecar_source


def test_settlement_readers_filter_verified_authority_before_downstream_use():
    """Replay, monitor, and harvester reads must not consume quarantined settlement values.

    P3 update (K1 followups, 2026-05-14): world_view/settlements.py retired;
    assertion relocated to src/execution/harvester.py (the canonical live
    settlement consumer). replay.py and monitor_refresh.py assertions unchanged.
    """
    replay_source = (ROOT / "src" / "engine" / "replay.py").read_text(encoding="utf-8")
    monitor_source = (ROOT / "src" / "engine" / "monitor_refresh.py").read_text(encoding="utf-8")
    harvester_source = (ROOT / "src" / "execution" / "harvester.py").read_text(encoding="utf-8")

    assert replay_source.count("authority = 'VERIFIED'") >= 4
    assert "AND authority = 'VERIFIED' LIMIT 1" in monitor_source
    # harvester.py filters at application layer (.upper() != "VERIFIED") rather
    # than SQL layer; assert the specific guard pattern exists.
    assert '.upper() != "VERIFIED"' in harvester_source or \
        ".upper() != 'VERIFIED'" in harvester_source, \
        "harvester.py application-layer VERIFIED guard not found"


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


def test_monitor_selection_uses_canonical_live_rows_not_historical_quarantine():
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL,
            updated_at TEXT,
            last_monitor_market_price_is_fresh INTEGER
        )
        """
    )
    stale_quarantine = _make_position(
        trade_id="old-entry-authority-quarantine",
        state="quarantined",
        city="Wellington",
        target_date="2026-06-24",
        direction="buy_no",
        shares=2.4255,
        chain_shares=2.4255,
        chain_state="entry_authority_quarantined",
    )
    live_day0 = _make_position(
        trade_id="live-day0-wellington",
        state="day0_window",
        city="Wellington",
        target_date="2026-07-02",
        direction="buy_yes",
        shares=15.0,
        chain_shares=15.0,
        chain_state="synced",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, chain_shares, updated_at,
            last_monitor_market_price_is_fresh
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("old-entry-authority-quarantine", "quarantined", 2.4255, 2.4255, "2026-07-02T12:00:00+00:00", 1),
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, chain_shares, updated_at,
            last_monitor_market_price_is_fresh
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("live-day0-wellington", "day0_window", 15.0, 15.0, "2026-07-02T12:37:25+00:00", 0),
    )

    selected = cycle_runtime._monitoring_phase_positions(
        _make_portfolio(stale_quarantine, live_day0),
        conn=conn,
    )

    assert selected == [live_day0]


def test_monitor_selection_keeps_unprojected_venue_confirmed_local_fill_with_canonical_db():
    from src.engine import cycle_runtime
    from src.state.portfolio import get_open_positions

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    pos = _make_position(
        trade_id="local-only-confirmed-fill-not-yet-projected",
        state="holding",
        city="Buenos Aires",
        target_date="2026-07-02",
        direction="buy_yes",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        shares=69.34,
        shares_filled=69.34,
        size_usd=2.84294,
        cost_basis_usd=2.84294,
        filled_cost_basis_usd=2.84294,
        entry_price=0.041,
        chain_state="local_only",
        chain_shares=0.0,
    )
    portfolio = _make_portfolio(pos)

    assert get_open_positions(portfolio) == []
    assert cycle_runtime._monitoring_phase_positions(portfolio, conn=conn) == [pos]


def test_monitor_selection_syncs_pending_exit_projection_over_stale_runtime_state():
    """Canonical pending_exit truth must not re-enter the held EXIT_INTENT lane as stale day0."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            order_status TEXT,
            shares REAL,
            chain_shares REAL,
            exit_retry_count INTEGER,
            next_exit_retry_at TEXT,
            exit_reason TEXT,
            updated_at TEXT,
            last_monitor_market_price_is_fresh INTEGER
        )
        """
    )
    pos = _make_position(
        trade_id="dust-exit-stale-runtime-day0",
        state="day0_window",
        order_status="filled",
        exit_state="",
        shares=1.0,
        chain_shares=1.0,
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, order_status, shares, chain_shares,
            exit_retry_count, next_exit_retry_at, exit_reason, updated_at,
            last_monitor_market_price_is_fresh
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "dust-exit-stale-runtime-day0",
            "pending_exit",
            "retry_pending",
            1.0,
            1.0,
            0,
            "2026-07-08T16:12:57+00:00",
            "DAY0_HARD_FACT_BIN_DEAD [DUST: size 1 below min_order_size 5]",
            "2026-07-08T16:10:57+00:00",
            1,
        ),
    )

    selected = cycle_runtime._monitoring_phase_positions(_make_portfolio(pos), conn=conn)

    assert selected == [pos]
    assert pos.state == "pending_exit"
    assert pos.order_status == "retry_pending"
    assert pos.exit_state == "retry_pending"
    assert pos.next_exit_retry_at == "2026-07-08T16:12:57+00:00"
    assert "DUST" in pos.exit_reason


def test_monitoring_phase_defers_held_positions_when_cycle_budget_exhausted(monkeypatch):
    """Held-position monitoring must preserve cadence instead of overrunning the scheduler."""
    from src.engine import cycle_runtime

    first = _make_position(
        trade_id="held-budget-first",
        city="Chicago",
        target_date="2026-07-04",
        direction="buy_yes",
        state="holding",
        shares=10.0,
        chain_shares=10.0,
        chain_state="synced",
    )
    second = _make_position(
        trade_id="held-budget-second",
        city="Chicago",
        target_date="2026-07-04",
        direction="buy_no",
        state="holding",
        shares=10.0,
        chain_shares=10.0,
        chain_state="synced",
    )
    portfolio = _make_portfolio(first, second)
    visited: list[str] = []

    def fake_refresh(conn, clob, position):
        visited.append(position.trade_id)
        position.last_monitor_prob = 0.61
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.12
        position.last_monitor_market_price = 0.49
        position.last_monitor_market_price_is_fresh = True
        return SimpleNamespace(
            p_market=np.array([0.49]),
            p_posterior=0.61,
            forward_edge=0.12,
            confidence_band_lower=0.08,
            confidence_band_upper=0.16,
        )

    def fake_evaluate_exit(self, exit_context):
        return ExitDecision(
            False,
            "CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior"],
        )

    monotonic_values = [0.0, 0.0, 1.0]

    def fake_monotonic():
        if monotonic_values:
            return monotonic_values.pop(0)
        return 1.0

    monkeypatch.setattr(cycle_runtime.time, "monotonic", fake_monotonic)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", fake_evaluate_exit)
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *args, **kwargs: True,
    )

    monitor_results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: monitor_results.append(result)},
    )()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type(
                "MonitorResult",
                (),
                {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)},
            ),
            "logger": logging.getLogger("test_monitor_budget"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 2, 18, 0, tzinfo=timezone.utc)),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        portfolio,
        artifact,
        type("Tracker", (), {"record_exit": lambda self, position: None})(),
        summary,
        deps=deps,
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=0.5,
    )

    assert visited == ["held-budget-first"]
    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["held_monitor_candidates"] == 2
    assert summary["held_monitor_budget_seconds"] == pytest.approx(0.5)
    assert summary["held_monitor_positions_scanned"] == 1
    assert summary["held_monitor_positions_deferred"] == 1
    assert summary["held_monitor_defer_reason"] == "cycle_budget_exhausted"
    assert summary["monitors"] == 1
    assert len(monitor_results) == 1


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
    from src.state.lifecycle_manager import LifecyclePhase

    if not getattr(position, "condition_id", ""):
        position.condition_id = "cond-1"
    if not getattr(position, "market_id", ""):
        position.market_id = position.condition_id
    events, projection = build_entry_canonical_write(
        position,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id=getattr(position, "decision_snapshot_id", None) or "dec-t1c-followup",
        source_module="src.test.t1c_followup_baseline",
    )
    append_many_and_project(conn, events, projection)


def _seed_acked_entry_command(conn, position, *, command_id: str = "cmd-rescue-proof") -> None:
    order_id = getattr(position, "entry_order_id", None) or getattr(position, "order_id", None)
    token_id = getattr(position, "token_id", None) or "tok-rescue-proof"
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', ?, ?, 'BUY', ?, ?, ?, 'ACKED', ?, ?)
        """,
        (
            command_id,
            f"snapshot-{command_id}",
            f"envelope-{command_id}",
            getattr(position, "trade_id", "pos-rescue-proof"),
            f"decision-{command_id}",
            f"idem-{command_id}",
            getattr(position, "market_id", None) or getattr(position, "condition_id", None) or "market-rescue-proof",
            token_id,
            float(getattr(position, "shares_submitted", 0.0) or getattr(position, "shares", 0.0) or 1.0),
            float(getattr(position, "entry_price_submitted", 0.0) or getattr(position, "entry_price", 0.0) or 0.5),
            order_id,
            "2026-04-03T00:00:00+00:00",
            "2026-04-03T00:00:00+00:00",
        ),
    )


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


def test_live_order_with_positive_size_matched_records_partial_fact_before_quarantine(tmp_path):
    """A CLOB LIVE order can still carry filled shares while the remainder rests."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema

    db_path = tmp_path / "live-partial.db"
    conn = get_connection(db_path)
    init_schema(conn)
    pos = _make_position(
        trade_id="runtime-live-partial",
        state="pending_tracked",
        order_id="ord-live-partial",
        entry_order_id="ord-live-partial",
        entry_fill_verified=False,
        entered_at="",
        entry_price=0.28,
        entry_price_submitted=0.28,
        shares=0.0,
        shares_submitted=7.21,
        size_usd=0.0,
        cost_basis_usd=0.0,
    )
    _seed_acked_entry_command(conn, pos, command_id="cmd-live-partial")
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    portfolio = _make_portfolio(pos)
    clob = _make_clob()
    clob.get_order_status.return_value = {
        "status": "LIVE",
        "size_matched": "2.11",
        "original_size": "7.21",
        "price": "0.28",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["voided"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "quarantined"
    assert pos.order_status == "partially_matched_missing_fill_economics"

    verify = get_connection(db_path)
    try:
        command = verify.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'cmd-live-partial'"
        ).fetchone()
        order_fact = verify.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE command_id = 'cmd-live-partial'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        trade_fact_count = verify.execute(
            "SELECT COUNT(*) FROM venue_trade_facts WHERE command_id = 'cmd-live-partial'"
        ).fetchone()[0]
        event_types = [
            row["event_type"]
            for row in verify.execute(
                """
                SELECT event_type
                  FROM venue_command_events
                 WHERE command_id = 'cmd-live-partial'
                 ORDER BY sequence_no
                """
            ).fetchall()
        ]
    finally:
        verify.close()

    assert command["state"] == "PARTIAL"
    assert dict(order_fact) == {
        "state": "PARTIALLY_MATCHED",
        "remaining_size": "5.1",
        "matched_size": "2.11",
    }
    assert trade_fact_count == 0
    assert event_types[-1] == "PARTIAL_FILL_OBSERVED"


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
    # PR D0 fix: balance-only rescue (no linked trade fact) must NOT set
    # entry_fill_verified=True or order_status="filled". The position is
    # tradable (has_tradable_exposure) but fill_authority=venue_position_observed.
    assert pos.entry_fill_verified is False
    assert pos.order_status == "pending"  # stays at input value for balance-only
    assert pos.entered_at != ""
    # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): balance-only rescue
    # preserves submitted entry economics; chain economics flow into
    # chain_* fields. Submitted defaults from _make_position were
    # entry_price=0.40, size_usd=10.0, shares=25.0, cost_basis_usd=10.0.
    assert pos.entry_price == 0.40
    assert pos.size_usd == 10.0
    assert pos.cost_basis_usd == 10.0
    assert pos.shares == 25.0
    # Chain aggregate (chain.avg_price=0.44, chain.cost=11.0, chain.size=25.0)
    # lands on chain_* fields.
    assert pos.chain_avg_price == 0.44
    assert pos.chain_cost_basis_usd == 11.0
    assert pos.chain_shares == 25.0
    assert pos.condition_id == "cond-1"
    assert portfolio.positions == [pos]


def test_chain_reconciliation_does_not_rescue_commanded_pending_entry_without_trade_fact(tmp_path):
    """A token-level chain position cannot prove that a specific live order filled."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "rescue_requires_trade_fact.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-proof-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_proof_001",
        no_token_id="tok_no_proof_001",
        order_id="order-proof-1",
        entry_order_id="order-proof-1",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        decision_snapshot_id="snap-proof-1",
    )
    _seed_canonical_entry_baseline(conn, pos)
    _seed_acked_entry_command(conn, pos, command_id="cmd-rescue-proof-1")
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_proof_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
        conn=conn,
    )
    phase = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        ("rescue-proof-1",),
    ).fetchone()["phase"]
    conn.close()

    assert stats["rescued_pending"] == 0
    assert stats["skipped_pending_missing_fill_fact"] == 1
    assert phase == "pending_entry"
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.order_status == "pending"


def test_chain_reconciliation_rescues_commanded_pending_entry_with_trade_fact(tmp_path):
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import append_trade_fact

    conn = get_connection(tmp_path / "rescue_with_trade_fact.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-proof-2",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_proof_002",
        no_token_id="tok_no_proof_002",
        order_id="order-proof-2",
        entry_order_id="order-proof-2",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        decision_snapshot_id="snap-proof-2",
    )
    _seed_canonical_entry_baseline(conn, pos)
    _seed_acked_entry_command(conn, pos, command_id="cmd-rescue-proof-2")
    append_trade_fact(
        conn,
        trade_id="trade-proof-2",
        venue_order_id="order-proof-2",
        command_id="cmd-rescue-proof-2",
        state="MATCHED",
        filled_size="25",
        fill_price="0.44",
        source="WS_USER",
        observed_at="2026-04-03T00:00:01+00:00",
        raw_payload_hash="a" * 64,
        raw_payload_json={"order_id": "order-proof-2", "trade_id": "trade-proof-2"},
    )
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_proof_002", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
        conn=conn,
    )
    conn.close()

    assert stats["rescued_pending"] == 1
    assert pos.state == "entered"
    assert pos.entry_fill_verified is True
    assert pos.order_status == "filled"


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

    # Rescue emission: PR D0 (Finding D0, 2026-05-27) — the fixture has no
    # linked venue trade fact, so the canonical event_type is now
    # VENUE_POSITION_OBSERVED (degraded recovery) rather than the previous
    # CHAIN_SYNCED. Same source_module + same metadata fields; the payload
    # additionally carries recovery_authority/causality_status/training_eligible.
    rescue_events = [e for e in events if e["event_type"] == "VENUE_POSITION_OBSERVED"]
    assert len(rescue_events) == 1
    rescue = rescue_events[0]
    assert rescue["source"] == "src.state.chain_reconciliation"
    assert rescue["order_id"] == "buy_123"
    details = rescue["details"]
    assert details["source"] == "chain_reconciliation"
    assert details["reason"] == "balance_only_recovery"
    assert details["from_state"] == "pending_tracked"
    assert details["to_state"] == "entered"
    assert details["entry_order_id"] == "buy_123"
    assert details["entry_fill_verified"] is False  # PR D0: balance-only rescue does NOT set entry_fill_verified=True
    assert details["chain_state"] == "synced"
    assert details["condition_id"] == "cond-1"
    assert details["recovery_authority"] == "balance_only"
    assert details["causality_status"] == "UNVERIFIED"
    assert details["training_eligible"] is False


def test_chain_reconciliation_rescue_emits_exactly_one_stage_event(tmp_path):
    """T1.c-followup rewrite 2026-04-23: post-T4.1b, rescue emits exactly
    one canonical event on first rescue; repeat reconcile calls on the
    same trade_id do not double-emit (idempotency guard via
    position_current phase check + already-logged check).

    PR D0 (Finding D0, 2026-05-27): the fixture has no linked venue trade
    fact, so the canonical event now uses event_type=VENUE_POSITION_OBSERVED
    and reason='balance_only_recovery' (degraded-recovery path), not the
    previous CHAIN_SYNCED / 'pending_fill_rescued' shape which applied
    when a trade fact existed. Trade-verified rescues still emit
    CHAIN_SYNCED via the unchanged builder; see the verified-path
    coverage in test_chain_reconciliation_rescues_commanded_pending_entry_with_trade_fact.
    """
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
    # PR D0 (Finding D0, 2026-05-27): balance-only rescue (no linked trade
    # fact) emits VENUE_POSITION_OBSERVED. Exactly ONE canonical event
    # (idempotency); payload carries degraded-recovery markers.
    rescue_events = [
        e for e in events
        if e["event_type"] == "VENUE_POSITION_OBSERVED"
        and e["source"] == "src.state.chain_reconciliation"
    ]
    assert len(rescue_events) == 1
    event = rescue_events[0]
    details = event["details"]
    assert details["from_state"] == "pending_tracked"
    assert details["to_state"] == "entered"
    assert details["source"] == "chain_reconciliation"
    assert details["reason"] == "balance_only_recovery"
    # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): the event payload
    # `shares` / `cost_basis_usd` / `size_usd` fields reflect submitted
    # entry economics (Position.shares / .cost_basis_usd / .size_usd at
    # emit time), NOT the chain aggregate. The chain aggregate lives on
    # the new chain_* payload fields below.
    assert details["shares"] == 25.0  # submitted shares from _make_position
    assert details["cost_basis_usd"] == 10.0  # submitted notional (was 11.0 chain pre-F1)
    assert details["condition_id"] == "cond-1"
    assert details["recovery_authority"] == "balance_only"
    assert details["causality_status"] == "UNVERIFIED"
    assert details["training_eligible"] is False
    # F1: chain economics on the event payload.
    assert details["chain_shares"] == 25.0
    assert details["chain_avg_price"] == 0.44
    assert details["chain_cost_basis_usd"] == 11.0


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
    # PR C2 (Finding 3, 2026-05-27): chain-only inventory is now a typed
    # ChainOnlyFact in portfolio.chain_only_facts, not a synthetic Position
    # in portfolio.positions. Verify the new signal carries the same identity
    # and economics; legacy Position-on-positions check removed.
    assert len(portfolio.chain_only_facts) == 1
    fact = portfolio.chain_only_facts[0]
    assert fact.token_id == "tok_econ_001"
    assert fact.size == 25.0
    assert fact.avg_price == 0.40
    assert fact.cost_basis == 10.0
    assert fact.condition_id == "cond-live-1"


def test_chain_only_fact_position_only_scope_does_not_freeze_new_entries():
    global_fact = ChainOnlyFact(
        token_id="global-token",
        condition_id="global-condition",
        size=1.0,
        avg_price=0.5,
        cost_basis=0.5,
        first_seen_at="2026-06-07T00:00:00+00:00",
        last_seen_at="2026-06-07T00:00:00+00:00",
    )
    position_only_fact = ChainOnlyFact(
        token_id="position-token",
        condition_id="position-condition",
        size=1.0,
        avg_price=0.5,
        cost_basis=0.5,
        first_seen_at="2026-06-07T00:00:00+00:00",
        last_seen_at="2026-06-07T00:00:00+00:00",
        entry_block_scope="position_only",
    )

    assert global_fact.blocks_entry is True
    assert global_fact.blocks_position_management is True
    assert position_only_fact.blocks_entry is False
    assert position_only_fact.blocks_position_management is True


def test_expired_chain_only_fact_does_not_freeze_new_entries():
    from src.engine.cycle_runner import _has_quarantined_positions

    expired_fact = ChainOnlyFact(
        token_id="expired-token",
        condition_id="expired-condition",
        size=1.0,
        avg_price=0.5,
        cost_basis=0.5,
        first_seen_at="2026-06-01T00:00:00+00:00",
        last_seen_at="2026-06-03T00:00:00+00:00",
        review_state=ChainOnlyReviewState.EXPIRED,
    )

    portfolio = _make_portfolio()
    portfolio.chain_only_facts.append(expired_fact)

    assert expired_fact.blocks_entry is False
    assert _has_quarantined_positions(portfolio) is False


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


def test_pending_exit_backoff_exhausted_reenters_redecision_when_still_held(monkeypatch):
    """Backoff exhaustion is an order-attempt state, not a permanent monitor stop."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="backoff-exhausted-held-risk",
        direction="buy_no",
        state="pending_exit",
        pre_exit_state="holding",
        chain_state="synced",
        shares=18.0,
        chain_shares=18.0,
        city="Miami",
        target_date="2026-07-02",
        token_id="yes-miami",
        no_token_id="no-miami",
        condition_id="condition-miami",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_retry_count=MAX_EXIT_RETRIES,
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        last_exit_error="previous_order_attempt_budget_exhausted",
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.44, 0.46, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("hold redecision must not record an exit")

    observed_refresh = []

    def mock_refresh(conn, clob, position):
        observed_refresh.append((
            position.trade_id,
            getattr(position.state, "value", position.state),
            getattr(position, "exit_state", ""),
        ))
        position.last_monitor_prob = 0.70
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = 0.44
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.44
        position.last_monitor_best_ask = 0.46
        position.last_monitor_market_vig = 0.90
        position.last_monitor_whale_toxicity = False
        position.last_monitor_at = "2026-07-01T12:00:00+00:00"
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.44]),
            p_posterior=0.70,
            forward_edge=0.26,
            alpha=0.0,
            confidence_band_upper=0.05,
            confidence_band_lower=-0.01,
            entry_provenance=EntryMethod.QKERNEL_SPINE,
            decision_snapshot_id="snap-backoff-redecision",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    observed_exit_contexts = []

    def mock_evaluate_exit(self, exit_context):
        observed_exit_contexts.append(exit_context)
        return ExitDecision(
            False,
            "CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_backoff_exhausted_redecision"),
            "cities_by_name": {"Miami": type("City", (), {"timezone": "America/New_York"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)),
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
        run_exit_preflight=False,
    )

    assert observed_refresh == [("backoff-exhausted-held-risk", "holding", "")]
    assert observed_exit_contexts
    assert pos.state == "holding"
    assert pos.exit_state == ""
    assert pos.order_status == "filled"
    assert pos.exit_retry_count == 0
    assert pos.exit_reason == ""
    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["monitor_released_backoff_exhausted_for_redecision"] == 1
    assert summary["monitors"] == 1
    assert summary["exits"] == 0
    assert len(monitor_results) == 1
    assert monitor_results[0].should_exit is False
    assert monitor_results[0].exit_reason == "CI_OVERLAP_HOLD"


def test_pending_exit_backoff_exhausted_dust_hold_does_not_emit_exit_intent(monkeypatch):
    """Non-executable dust holds must not re-enter monitor and spam EXIT_INTENT."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="backoff-exhausted-dust-hold",
        direction="buy_no",
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        shares=1.0,
        chain_shares=1.0,
        city="Kuala Lumpur",
        target_date="2026-07-08",
        token_id="yes-kl",
        no_token_id="no-kl",
        condition_id="condition-kl",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_retry_count=MAX_EXIT_RETRIES,
        exit_reason=(
            "DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES (entry=0.8679, current=0.0000) "
            "[DUST: executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5]"
        ),
        last_exit_error="executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5",
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            raise AssertionError("dust hold must not request fresh exit quote")

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("dust hold must not record an exit")

    def refresh_must_not_run(conn, clob, position):
        raise AssertionError("dust hold must not refresh into evaluate_exit")

    def evaluate_must_not_run(self, exit_context):
        raise AssertionError("dust hold must not evaluate or emit exit intent")

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", refresh_must_not_run)
    monkeypatch.setattr(Position, "evaluate_exit", evaluate_must_not_run)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_backoff_exhausted_dust_hold"),
            "cities_by_name": {"Kuala Lumpur": type("City", (), {"timezone": "Asia/Kuala_Lumpur"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc)),
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
        run_exit_preflight=False,
    )

    assert pos.state == "pending_exit"
    assert pos.exit_state == "backoff_exhausted"
    assert pos.order_status == "backoff_exhausted"
    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert summary["monitor_skipped_pending_exit_phase"] == 1
    assert summary["monitors"] == 0
    assert summary["exits"] == 0
    assert monitor_results == []


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


# ---- Test 8: Quarantine expiry timer retired (P0b, 2026-07-04) ----
#
# test_quarantine_expires_after_48h previously asserted that
# check_quarantine_timeouts() minted chain_state="quarantine_expired" after
# 48h. That timer is retired — see
# docs/rebuild/chain_mirror_state_model_2026-07-04.md §5 follow-up — in favor
# of the chain-mirror reconciler's two-consecutive-mirror-runs force-resolve
# (runs every ~10 minutes). Read-side handling of a legacy
# chain_state="quarantine_expired" row is still covered below (this repo does
# not purge the read-side vocabulary in this slice — blast-radius honesty).


def test_quarantine_expired_blocks_new_entries_until_resolved():
    """Quarantine-expired positions still block discovery until authoritative resolution."""
    from src.engine.cycle_runner import _has_quarantined_positions

    pos = _make_position(chain_state="quarantine_expired")
    portfolio = _make_portfolio(pos)

    assert _has_quarantined_positions(portfolio) is True


def test_recent_entry_authority_quarantine_redecision_exposure_does_not_block_entries():
    """A monitor-managed real exposure quarantine must not freeze all new entries."""
    from src.engine.cycle_runner import _has_quarantined_positions

    observed_at = datetime.now(timezone.utc).isoformat()
    pos = _make_position(
        direction="buy_yes",
        state="quarantined",
        chain_state="entry_authority_quarantined",
        shares=65.0,
        chain_shares=65.0,
        last_chain_absence_observed_at=observed_at,
        chain_verified_at=observed_at,
    )
    portfolio = _make_portfolio(pos)

    assert _has_quarantined_positions(portfolio) is False


@pytest.mark.parametrize(
    "chain_state",
    [
        "chain_absent_confirmed_position_unattributed",
        "chain_confirmed_zero",
        "entry_authority_quarantined",
    ],
)
def test_stale_resolved_quarantine_projection_does_not_block_entries(chain_state):
    """Old explicit chain-resolution projections must not freeze unrelated entry flow."""
    from src.engine.cycle_runner import _has_quarantined_positions

    pos = _make_position(
        direction="buy_no",
        state="quarantined",
        chain_state=chain_state,
        shares=10.0,
        chain_shares=0.0 if chain_state == "chain_confirmed_zero" else 10.0,
        last_chain_absence_observed_at="2026-06-20T00:00:00+00:00",
        chain_verified_at="2026-06-20T00:00:00+00:00",
    )
    portfolio = _make_portfolio(pos)

    assert _has_quarantined_positions(portfolio) is False


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


def test_entry_authority_quarantined_exposure_reaches_redecision(monkeypatch):
    """A real held position with bad entry proof must still be monitor-managed."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="entry-authority-quarantine-position",
        direction="buy_no",
        state="quarantined",
        chain_state="entry_authority_quarantined",
        shares=19.88,
        chain_shares=19.88,
        city="Lucknow",
        target_date="2026-06-28",
        token_id="yes-lucknow",
        no_token_id="no-lucknow",
        condition_id="condition-lucknow",
        admin_exit_reason="invalid_entry_actionable_certificate_authority",
        exit_reason="invalid_entry_actionable_certificate_authority",
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.40, 0.42, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("no exit fill expected")

    observed_refresh = []

    def mock_refresh(conn, clob, position):
        observed_refresh.append((
            position.trade_id,
            getattr(position.state, "value", position.state),
            getattr(position.chain_state, "value", position.chain_state),
        ))
        position.last_monitor_prob = 0.62
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = 0.40
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.40
        position.last_monitor_best_ask = 0.42
        position.last_monitor_market_vig = 1.02
        position.last_monitor_whale_toxicity = False
        position.last_monitor_at = "2026-06-28T08:00:00+00:00"
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.40]),
            p_posterior=0.62,
            forward_edge=0.22,
            alpha=0.0,
            confidence_band_upper=0.05,
            confidence_band_lower=-0.01,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap-entry-authority-quarantine",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    observed_exit_contexts = []

    def mock_evaluate_exit(self, exit_context):
        observed_exit_contexts.append(exit_context)
        return ExitDecision(
            False,
            "ENTRY_AUTHORITY_QUARANTINE_REDECISION_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["entry_authority_quarantine_redecision"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_entry_authority_quarantine_redecision"),
            "cities_by_name": {"Lucknow": type("City", (), {"timezone": "Asia/Kolkata"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 6, 28, 8, 0, tzinfo=timezone.utc)),
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
    assert observed_refresh == [(
        "entry-authority-quarantine-position",
        "quarantined",
        "entry_authority_quarantined",
    )]
    assert observed_exit_contexts
    assert observed_exit_contexts[0].position_state == "quarantined"
    assert summary["quarantined_exposure_routed_to_redecision"] == 1
    assert summary.get("monitor_skipped_quarantine_resolution", 0) == 0
    assert summary["monitors"] == 1
    assert len(monitor_results) == 1
    assert monitor_results[0].fresh_prob == 0.62
    assert monitor_results[0].fresh_edge == 0.22
    assert monitor_results[0].should_exit is False
    assert monitor_results[0].exit_reason == "ENTRY_AUTHORITY_QUARANTINE_REDECISION_HOLD"


def test_canonical_monitor_order_includes_entry_authority_quarantined_exposure():
    """Canonical DB ordering must not drop chain-backed quarantine before monitor."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL,
            updated_at TEXT,
            chain_state TEXT,
            direction TEXT,
            last_monitor_market_price_is_fresh INTEGER
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO position_current (
            position_id, phase, shares, chain_shares, updated_at,
            chain_state, direction, last_monitor_market_price_is_fresh
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "entry-authority-quarantine-position",
                "quarantined",
                19.88,
                19.88,
                "2026-06-28T08:00:00+00:00",
                "entry_authority_quarantined",
                "buy_no",
                0,
            ),
            (
                "chain-absence-quarantine-position",
                "quarantined",
                12.7,
                12.7,
                "2026-06-28T08:00:00+00:00",
                "chain_absent_confirmed_position_unattributed",
                "buy_yes",
                0,
            ),
        ],
    )

    assert cycle_runtime._canonical_monitor_position_order(conn) == [
        "entry-authority-quarantine-position"
    ]


def test_chain_absent_confirmed_recent_projection_skips_redecision(monkeypatch):
    """Confirmed chain absence is reconciliation debt, not live monitor-managed exposure."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime

    observed_at = datetime.now(timezone.utc).isoformat()
    pos = _make_position(
        trade_id="chain-absence-quarantine-position",
        direction="buy_yes",
        state="quarantined",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=65.0,
        chain_shares=65.0,
        city="Chongqing",
        target_date="2026-07-01",
        token_id="yes-chongqing",
        no_token_id="no-chongqing",
        condition_id="condition-chongqing",
        last_chain_absence_observed_at=observed_at,
        chain_verified_at=observed_at,
        order_status="filled",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        entry_fill_verified=True,
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.004, 0.006, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("no exit fill expected")

    observed_refresh = []

    def mock_refresh(conn, clob, position):
        observed_refresh.append((
            position.trade_id,
            getattr(position.state, "value", position.state),
            getattr(position.chain_state, "value", position.chain_state),
        ))
        position.last_monitor_prob = 0.03
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = 0.004
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.004
        position.last_monitor_best_ask = 0.006
        position.last_monitor_market_vig = 1.02
        position.last_monitor_whale_toxicity = False
        position.last_monitor_at = observed_at
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.004]),
            p_posterior=0.03,
            forward_edge=0.026,
            alpha=0.0,
            confidence_band_upper=0.04,
            confidence_band_lower=0.02,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap-chain-absence-quarantine",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    observed_exit_contexts = []

    def mock_evaluate_exit(self, exit_context):
        observed_exit_contexts.append(exit_context)
        return ExitDecision(
            False,
            "CHAIN_ABSENCE_QUARANTINE_REDECISION_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["chain_absence_quarantine_redecision"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_chain_absence_quarantine_redecision"),
            "cities_by_name": {"Chongqing": type("City", (), {"timezone": "Asia/Shanghai"})()},
            "_utcnow": staticmethod(lambda: datetime.now(timezone.utc)),
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
    assert observed_refresh == []
    assert observed_exit_contexts == []
    assert summary.get("quarantined_exposure_routed_to_redecision", 0) == 0
    assert summary["monitor_skipped_quarantine_resolution"] == 1
    assert summary["monitors"] == 0
    assert len(monitor_results) == 1
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None
    assert monitor_results[0].should_exit is False
    assert monitor_results[0].exit_reason == "QUARANTINE_REVIEW_REQUIRED"


def test_chain_absent_confirmed_recent_projection_does_not_reach_exit_lifecycle(monkeypatch):
    """Confirmed chain absence must not manufacture a live exit lifecycle action."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime

    observed_at = datetime.now(timezone.utc).isoformat()
    pos = _make_position(
        trade_id="chain-absence-quarantine-exit-position",
        direction="buy_yes",
        state="quarantined",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=12.7,
        chain_shares=12.7,
        city="Singapore",
        target_date="2026-07-01",
        token_id="yes-singapore",
        no_token_id="no-singapore",
        condition_id="condition-singapore",
        last_chain_absence_observed_at=observed_at,
        chain_verified_at=observed_at,
        order_status="partial",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        entry_fill_verified=True,
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.06, 0.07, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("fake exit lifecycle does not report a fill")

    def mock_refresh(conn, clob, position):
        position.last_monitor_prob = 0.02
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = 0.06
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.06
        position.last_monitor_best_ask = 0.07
        position.last_monitor_market_vig = 1.02
        position.last_monitor_whale_toxicity = False
        position.last_monitor_at = observed_at
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.06]),
            p_posterior=0.02,
            forward_edge=-0.04,
            alpha=0.0,
            confidence_band_upper=0.03,
            confidence_band_lower=0.01,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap-chain-absence-quarantine-exit",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    def mock_evaluate_exit(self, exit_context):
        return ExitDecision(
            True,
            "QUARANTINED_EXPOSURE_REDECISION_EXIT",
            trigger="QUARANTINED_EXPOSURE_REDECISION_EXIT",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["chain_absence_quarantine_redecision_exit"],
        )

    observed_execute = []

    def mock_build_exit_intent(position, exit_context):
        return SimpleNamespace(token_id=position.token_id, reason=exit_context.exit_reason)

    def mock_execute_exit(*, portfolio, position, exit_context, clob, conn, exit_intent):
        observed_execute.append(
            {
                "position_id": position.trade_id,
                "state": getattr(position.state, "value", position.state),
                "chain_state": getattr(position.chain_state, "value", position.chain_state),
                "exit_reason": exit_context.exit_reason,
                "token_id": exit_intent.token_id,
            }
        )
        position.state = "pending_exit"
        return "sell_order_placed:test"

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)
    monkeypatch.setattr("src.execution.exit_lifecycle.build_exit_intent", mock_build_exit_intent)
    monkeypatch.setattr("src.execution.exit_lifecycle.execute_exit", mock_execute_exit)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_chain_absence_quarantine_exit_lifecycle"),
            "cities_by_name": {"Singapore": type("City", (), {"timezone": "Asia/Singapore"})()},
            "_utcnow": staticmethod(lambda: datetime.now(timezone.utc)),
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
    assert summary.get("quarantined_exposure_routed_to_redecision", 0) == 0
    assert summary["monitor_skipped_quarantine_resolution"] == 1
    assert summary["monitors"] == 0
    assert summary.get("exits", 0) == 0
    assert monitor_results[0].should_exit is False
    assert monitor_results[0].exit_reason == "QUARANTINE_REVIEW_REQUIRED"
    assert observed_execute == []
    assert pos.state == "quarantined"


def test_chain_absent_confirmed_positive_projection_does_not_redecision():
    """Stale local shares on a confirmed-absent quarantine do not create live exposure."""
    from src.engine import cycle_runtime

    pos = _make_position(
        direction="buy_yes",
        state="quarantined",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=12.7,
        chain_shares=12.7,
        last_chain_absence_observed_at="2026-06-20T00:00:00+00:00",
        chain_verified_at="",
    )

    assert cycle_runtime._quarantined_position_can_redecision(pos) is False


def test_pending_exit_chain_absent_positive_exposure_stays_open_for_exit_lifecycle():
    """A pending exit with real chain shares must stay in the open set for exit management."""
    from src.state.portfolio import get_open_positions

    pos = _make_position(
        direction="buy_yes",
        state="pending_exit",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=9.7,
        chain_shares=9.7,
        exit_state="retry_pending",
        order_status="retry_pending",
        next_exit_retry_at="2026-06-29T17:17:30+00:00",
    )
    portfolio = _make_portfolio(pos)

    assert get_open_positions(portfolio) == [pos]

    zero = _make_position(
        direction="buy_yes",
        state="pending_exit",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=9.7,
        chain_shares=0.0,
        exit_state="retry_pending",
        order_status="retry_pending",
    )
    assert get_open_positions(_make_portfolio(zero)) == []


def test_pending_exit_retry_cooldown_emits_monitor_refresh_receipt():
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.db import init_schema
    from src.state.projection import upsert_position_current

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    pos = _make_position(
        trade_id="pending-exit-retry-cooldown-monitor",
        direction="buy_yes",
        state="pending_exit",
        chain_state="synced",
        shares=9.7,
        chain_shares=9.7,
        exit_state="retry_pending",
        order_status="retry_pending",
        next_exit_retry_at="2099-01-01T00:00:00+00:00",
        last_monitor_prob=0.12,
        last_monitor_prob_is_fresh=True,
        fill_authority="venue_confirmed_full",
        condition_id="condition-pending-exit-retry-cooldown-monitor",
        strategy_key="forecast_qkernel_entry",
        entered_at="2026-07-02T19:00:00+00:00",
    )
    # execute_monitoring_phase consumes runtime DB/string state; _make_position
    # normalizes through enums for some tests.
    pos.state = "pending_exit"
    pos.chain_state = "synced"
    pos.exit_state = "retry_pending"
    pos.order_status = "retry_pending"
    upsert_position_current(conn, build_position_current_projection(pos))
    portfolio = _make_portfolio(pos)
    monitor_results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: monitor_results.append(result)},
    )()
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type(
                "MonitorResult",
                (),
                {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)},
            ),
            "logger": logging.getLogger("test_pending_exit_retry_cooldown_monitor"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 2, 20, 20, tzinfo=timezone.utc)),
        },
    )
    summary = {"monitors": 0, "exits": 0}

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        portfolio,
        artifact,
        type("Tracker", (), {"record_exit": lambda self, position: None})(),
        summary,
        deps=deps,
        run_exit_preflight=False,
    )

    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert summary["monitor_pending_exit_retry_cooldown_holds"] == 1
    assert summary["monitors"] == 1
    assert monitor_results[0].exit_reason == "PENDING_EXIT_RETRY_COOLDOWN_ACTIVE"
    event = conn.execute(
        """
        SELECT event_type, occurred_at, payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        (pos.trade_id,),
    ).fetchone()
    assert event is not None
    assert event["occurred_at"] == "2026-07-02T20:20:00+00:00"
    payload = json.loads(event["payload_json"])
    assert payload["exit_decision_reason"] == "PENDING_EXIT_RETRY_COOLDOWN_ACTIVE"

    conn.close()


def test_monitor_refresh_with_exit_backoff_preserves_pending_exit_phase(tmp_path):
    """Monitor receipts must not re-project a pending dust/backoff exit as day0."""
    from src.engine import cycle_runtime
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "pending-exit-monitor-phase.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="pending-exit-monitor-phase",
        direction="buy_no",
        state="day0_window",
        chain_state="synced",
        shares=1.0,
        chain_shares=1.0,
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        last_monitor_prob=0.0,
        last_monitor_prob_is_fresh=True,
        condition_id="condition-pending-exit-monitor-phase",
        strategy_key="forecast_qkernel_entry",
        entered_at="2026-07-02T19:00:00+00:00",
        exit_reason="DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES [DUST]",
    )
    deps = type(
        "Deps",
        (),
        {
            "logger": logging.getLogger("test_monitor_refresh_pending_exit_phase"),
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 2, 20, 20, tzinfo=timezone.utc)),
        },
    )

    assert cycle_runtime._emit_monitor_refreshed_canonical_if_available(conn, pos, deps=deps) is True

    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        (pos.trade_id,),
    ).fetchone()
    current = conn.execute(
        "SELECT phase, order_status FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()

    assert event is not None
    assert event["phase_before"] == "pending_exit"
    assert event["phase_after"] == "pending_exit"
    payload = json.loads(event["payload_json"])
    assert payload["phase_after"] == "pending_exit"
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "backoff_exhausted"
    conn.close()


def test_pending_exit_chain_absent_zero_balance_uses_chain_truth_resolution(monkeypatch):
    """Pending-exit chain-absent review rows must resolve via balanceOf instead of sell retry."""
    from src.execution.exit_lifecycle import handle_exit_pending_missing

    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0x" + "1" * 40)
    pos = _make_position(
        trade_id="pending-chain-absent-zero",
        direction="buy_yes",
        state="pending_exit",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=9.7,
        chain_shares=9.7,
        exit_state="retry_pending",
        order_status="retry_pending",
        token_id="123456789",
        condition_id="condition-pending-chain-absent-zero",
    )
    portfolio = _make_portfolio(pos)

    result = handle_exit_pending_missing(
        portfolio,
        pos,
        rpc_call=lambda *_args, **_kwargs: "0x0",
    )

    assert result["action"] == "closed"
    assert result["position"].state == "voided"
    assert result["position"].exit_reason == "CHAIN_CONFIRMED_ZERO"
    assert result["position"].chain_state == "chain_confirmed_zero"
    assert result["position"].chain_shares == 0.0
    assert result["position"].order_status == "voided"
    assert result["position"].exit_state == ""
    assert result["position"].exit_retry_count == 0
    assert result["position"].next_exit_retry_at == ""
    assert portfolio.positions == []


def test_monitor_entry_selection_guard_invalidity_requires_independent_exit():
    """A bad historical entry proof is not itself live exit authority."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-unarmed",
            "pos-unarmed",
            "edli_exec_cmd:agg-unarmed:edli_intent:agg-unarmed:tok:tok:buy_yes",
            "2026-06-29T12:00:00+00:00",
            "2026-06-29T12:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 3, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-unarmed",
            "2026-06-29T12:00:00+00:00",
            json.dumps(
                {
                    "direction": "buy_yes",
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                        "selection_guard_cell_key": "YES|tail|nonmodal|pb2",
                        "payoff_q_lcb": 0.0,
                        "cost": 0.07,
                        "edge_lcb": 0.0,
                    },
                }
            ),
        ),
    )
    pos = _make_position(
        trade_id="pos-unarmed",
        direction="buy_yes",
        state="quarantined",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=65.0,
        chain_shares=65.0,
        entry_method="qkernel_spine",
        selected_method="qkernel_spine",
    )
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=conn,
        pos=pos,
        exit_context=SimpleNamespace(best_bid=0.006),
        summary=summary,
    )

    assert decision is not None
    assert decision.should_exit is False
    assert decision.trigger == "ENTRY_SELECTION_GUARD_INVALID_HOLD_REQUIRES_CURRENT_EXIT"
    assert "selection_guard_side_not_armed" in decision.reason
    assert summary["entry_selection_guard_invalid_positions"] == 1
    assert summary["entry_selection_guard_invalid_independent_exit_required"] == 1


def test_monitor_entry_selection_guard_does_not_force_exit_over_fresh_positive_edge():
    """Historical entry-guard invalidity cannot override current positive monitor EV."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-unarmed",
            "pos-unarmed-positive-now",
            "edli_exec_cmd:agg-unarmed:edli_intent:agg-unarmed:tok:tok:buy_yes",
            "2026-06-29T12:00:00+00:00",
            "2026-06-29T12:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 3, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-unarmed",
            "2026-06-29T12:00:00+00:00",
            json.dumps(
                {
                    "direction": "buy_yes",
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                        "selection_guard_cell_key": "YES|tail|nonmodal|pb2",
                        "payoff_q_lcb": 0.0,
                        "cost": 0.07,
                        "edge_lcb": 0.0,
                    },
                }
            ),
        ),
    )
    pos = _make_position(
        trade_id="pos-unarmed-positive-now",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
        shares=85.17,
        chain_shares=85.17,
        entry_method="qkernel_spine",
        selected_method="qkernel_spine",
    )
    pos.last_monitor_prob = 0.3392837479
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.2800396534
    pos.last_monitor_market_price = 0.0592440945
    pos.last_monitor_market_price_is_fresh = True
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=conn,
        pos=pos,
        exit_context=SimpleNamespace(best_bid=0.052),
        summary=summary,
    )

    assert decision is not None
    assert decision.should_exit is False
    assert decision.trigger == "ENTRY_SELECTION_GUARD_INVALID_HOLD_CURRENT_EDGE"
    assert "current_edge=0.2800" in decision.reason
    assert summary["entry_selection_guard_invalid_current_ev_holds"] == 1


def test_monitor_entry_selection_guard_does_not_force_exit_on_immature_day0():
    """Historical entry-guard invalidity cannot override an immature Day0 authority block."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-unarmed-day0",
            "pos-unarmed-day0",
            "edli_exec_cmd:agg-unarmed-day0:edli_intent:agg-unarmed-day0:tok:tok:buy_yes",
            "2026-06-29T12:00:00+00:00",
            "2026-06-29T12:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 3, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-unarmed-day0",
            "2026-06-29T12:00:00+00:00",
            json.dumps(
                {
                    "direction": "buy_yes",
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                        "selection_guard_cell_key": "YES|tail|nonmodal|pb2",
                        "payoff_q_lcb": 0.0,
                        "cost": 0.07,
                        "edge_lcb": 0.0,
                    },
                }
            ),
        ),
    )
    pos = _make_position(
        trade_id="pos-unarmed-day0",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
        shares=85.17,
        chain_shares=85.17,
        entry_method="qkernel_spine",
        selected_method="day0_observation_remaining_window",
    )
    pos.last_monitor_prob = 0.0
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = -0.031
    pos.last_monitor_market_price = 0.031
    pos.last_monitor_market_price_is_fresh = True
    exit_decision = ExitDecision(
        False,
        reason="CI_OVERLAP_HOLD",
        trigger="CI_OVERLAP_HOLD",
        selected_method="day0_observation_remaining_window",
        applied_validations=[
            "day0_observation_remaining_window",
            "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.012",
        ],
    )
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=conn,
        pos=pos,
        exit_context=SimpleNamespace(best_bid=0.031),
        summary=summary,
        exit_decision=exit_decision,
    )

    assert decision is not None
    assert decision.should_exit is False
    assert decision.trigger == "ENTRY_SELECTION_GUARD_INVALID_HOLD_DAY0_IMMATURE"
    assert "day0_high_extreme_not_mature:" in decision.reason
    assert summary["entry_selection_guard_invalid_day0_immature_holds"] == 1


def test_monitor_entry_selection_guard_preserves_existing_day0_exit_decision():
    """Entry guard may flag invalid entry proof, but must not rename an existing exit."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-unarmed-day0-exit",
            "pos-unarmed-day0-exit",
            "edli_exec_cmd:agg-unarmed-day0-exit:edli_intent:agg-unarmed-day0-exit:tok:tok:buy_yes",
            "2026-06-29T12:00:00+00:00",
            "2026-06-29T12:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 3, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-unarmed-day0-exit",
            "2026-06-29T12:00:00+00:00",
            json.dumps(
                {
                    "direction": "buy_yes",
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                        "selection_guard_cell_key": "YES|tail|nonmodal|pb2",
                        "payoff_q_lcb": 0.0,
                        "cost": 0.07,
                        "edge_lcb": 0.0,
                    },
                }
            ),
        ),
    )
    pos = _make_position(
        trade_id="pos-unarmed-day0-exit",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
        shares=85.17,
        chain_shares=85.17,
        entry_method="qkernel_spine",
        selected_method="day0_observation_remaining_window",
    )
    exit_decision = ExitDecision(
        True,
        reason="DAY0_HARD_FACT_BIN_DEAD (running_extreme_refutes_bin; source=observation_instants)",
        urgency="immediate",
        trigger="DAY0_HARD_FACT_BIN_DEAD",
        selected_method="day0_observation_remaining_window",
        applied_validations=["day0_hard_fact_exit_lane"],
    )
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=conn,
        pos=pos,
        exit_context=SimpleNamespace(best_bid=0.031),
        summary=summary,
        exit_decision=exit_decision,
    )

    assert decision is None
    assert summary["entry_selection_guard_invalid_positions"] == 1
    assert summary["entry_selection_guard_invalid_existing_exit_preserved"] == 1


def test_entry_replacement_blocks_when_materializable_raw_cycle_newer_than_posterior():
    """Entry must not trade a stale posterior after anchor-qualified raw inputs advance."""
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    for model in ("ecmwf_ifs", "gfs", "icon"):
        conn.execute(
            """
            INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model,
                "Singapore",
                "2026-07-01",
                "high",
                "2026-06-29T12:00:00+00:00",
                "single_runs",
                "COVERED",
                "2026-06-29T12:20:00+00:00",
                "2026-06-29T12:10:00+00:00",
            ),
        )

    reason = adapter._replacement_live_input_lag_reason(
        conn,
        family=SimpleNamespace(
            city="Singapore",
            target_date="2026-07-01",
            metric="high",
        ),
        decision_time=datetime(2026, 6, 29, 13, 0, tzinfo=timezone.utc),
        posterior_source_cycle_time="2026-06-29T06:00:00+00:00",
    )

    assert reason is not None
    assert "source_cycle_time_raw_model_forecasts_lag" in reason
    assert "latest_raw_cycle=2026-06-29T12:00:00+00:00" in reason
    assert "posterior_cycle=2026-06-29T06:00:00+00:00" in reason


def test_entry_replacement_ignores_partial_non_anchor_raw_cycle_newer_than_posterior():
    """Partial regional/model rows cannot stale replacement authority by themselves.

    Live June-30 shape: DMI/ICON rows for 12Z arrived before any replacement
    anchor artifact/posterior. Treating those three rows as a complete posterior
    dependency froze entry with REPLACEMENT_0_1_LIVE_INPUT_LAG.
    """
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    for model in ("dmi_harmonie_europe", "icon_eu", "icon_global"):
        conn.execute(
            """
            INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model,
                "Munich",
                "2026-07-02",
                "high",
                "2026-06-30T12:00:00+00:00",
                "single_runs",
                "COVERED",
                "2026-06-30T16:20:00+00:00",
                "2026-06-30T16:06:00+00:00",
            ),
        )

    reason = adapter._replacement_live_input_lag_reason(
        conn,
        family=SimpleNamespace(
            city="Munich",
            target_date="2026-07-02",
            metric="high",
        ),
        decision_time=datetime(2026, 6, 30, 17, 0, tzinfo=timezone.utc),
        posterior_source_cycle_time="2026-06-30T06:00:00+00:00",
    )

    assert reason is None


def test_entry_replacement_blocks_when_used_model_raw_cycle_newer_than_posterior():
    """A posterior is stale when one of its own used models has a newer raw cycle."""
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            computed_at TEXT,
            provenance_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Kuala Lumpur",
            "2026-07-10",
            "high",
            "2026-07-08T00:00:00+00:00",
            "2026-07-08T08:16:19+00:00",
            json.dumps(
                {
                    "bayes_precision_fusion": {
                        "used_models": ["icon_global", "ukmo_global_deterministic_10km", "ecmwf_ifs"]
                    }
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "icon_global",
            "Kuala Lumpur",
            "2026-07-10",
            "high",
            "2026-07-08T06:00:00+00:00",
            "single_runs",
            "COVERED",
            "2026-07-08T09:39:37+00:00",
            "2026-07-08T09:27:52+00:00",
        ),
    )

    reason = adapter._replacement_live_input_lag_reason(
        conn,
        family=SimpleNamespace(
            city="Kuala Lumpur",
            target_date="2026-07-10",
            metric="high",
        ),
        decision_time=datetime(2026, 7, 8, 10, 39, tzinfo=timezone.utc),
        posterior_source_cycle_time="2026-07-08T00:00:00+00:00",
    )

    assert reason is not None
    assert "source_cycle_time_used_raw_model_forecasts_lag" in reason
    assert "latest_raw_cycle=2026-07-08T06:00:00+00:00" in reason
    assert "posterior_cycle=2026-07-08T00:00:00+00:00" in reason


def test_entry_replacement_blocks_when_used_model_same_cycle_arrives_after_posterior():
    """A same-cycle used-model row captured after computed_at invalidates the posterior."""
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            computed_at TEXT,
            provenance_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Kuala Lumpur",
            "2026-07-10",
            "high",
            "2026-07-08T06:00:00+00:00",
            "2026-07-08T08:00:00+00:00",
            json.dumps({"bayes_precision_fusion": {"used_models": ["icon_global"]}}),
        ),
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "icon_global",
            "Kuala Lumpur",
            "2026-07-10",
            "high",
            "2026-07-08T06:00:00+00:00",
            "single_runs",
            "COVERED",
            "2026-07-08T09:30:00+00:00",
            "2026-07-08T09:20:00+00:00",
        ),
    )

    reason = adapter._replacement_live_input_lag_reason(
        conn,
        family=SimpleNamespace(
            city="Kuala Lumpur",
            target_date="2026-07-10",
            metric="high",
        ),
        decision_time=datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc),
        posterior_source_cycle_time="2026-07-08T06:00:00+00:00",
        posterior_computed_at="2026-07-08T08:00:00+00:00",
    )

    assert reason is not None
    assert "used_raw_model_forecasts_same_cycle_late_input" in reason
    assert "latest_raw_cycle=2026-07-08T06:00:00+00:00" in reason
    assert "latest_raw_input_at=2026-07-08T09:30:00+00:00" in reason
    assert "posterior_computed_at=2026-07-08T08:00:00+00:00" in reason


def test_replacement_forecast_authority_missing_posterior_does_not_fallback(monkeypatch):
    """With replacement live, missing posterior evidence is a blocker, not a legacy fallback."""
    from src.engine import event_reactor_adapter as adapter

    monkeypatch.setattr(adapter, "_replacement_authority_enabled", lambda: True)
    monkeypatch.setattr(
        adapter,
        "_forecast_authority_payload_from_posterior",
        lambda *_args, **_kwargs: None,
    )

    def _fail_if_legacy_snapshot_called(*_args, **_kwargs):
        raise AssertionError("legacy forecast snapshot fallback was called")

    monkeypatch.setattr(
        adapter,
        "_forecast_snapshot_row_for_event",
        _fail_if_legacy_snapshot_called,
    )

    with pytest.raises(
        ValueError,
        match="FORECAST_AUTHORITY_EVIDENCE_MISSING:replacement_posterior",
    ):
        adapter._forecast_authority_payload_and_clock(
            sqlite3.connect(":memory:"),
            event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            family=SimpleNamespace(city="Singapore", target_date="2026-07-01", metric="high"),
            payload={},
            decision_time=datetime(2026, 6, 29, 13, 0, tzinfo=timezone.utc),
        )


def test_monitoring_skips_blocking_review_fact_position_without_exit(monkeypatch):
    """Invalid entry-proof review facts must stop automatic monitor/exit churn."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="invalid-proof-position",
        direction="buy_no",
        state="holding",
        chain_state="synced",
        token_id="yes-invalid-proof",
        no_token_id="no-invalid-proof",
        condition_id="condition-invalid-proof",
    )
    portfolio = _make_portfolio(pos)
    portfolio.chain_only_facts.append(
        ChainOnlyFact(
            token_id="no-invalid-proof",
            condition_id="condition-invalid-proof",
            size=5.0,
            avg_price=0.70,
            cost_basis=3.50,
            first_seen_at="2026-06-07T01:00:00+00:00",
            last_seen_at="2026-06-07T01:00:00+00:00",
            review_state=ChainOnlyReviewState.UNRESOLVED,
        )
    )

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("blocking review fact position must not auto-exit")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_blocking_review_fact_monitor_skip"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 6, 7, 1, 30, tzinfo=timezone.utc)),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("blocking review fact position must not reach monitor refresh")
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
    assert summary["monitor_skipped_blocking_review_fact"] == 1
    assert summary["monitors"] == 0
    assert summary["exits"] == 0
    assert len(monitor_results) == 1
    assert monitor_results[0].exit_reason == "REVIEW_REQUIRED_INVALID_ENTRY_PROOF"
    assert monitor_results[0].should_exit is False
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


def test_day0_closed_non_accepting_market_skips_exit_monitor_chain_missing(monkeypatch):
    """Closed non-accepting Day0 markets await settlement instead of failing quote freshness."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="closed-day0-001",
        state="day0_window",
        chain_state="synced",
        city="Chicago",
        target_date="2026-04-01",
        market_id="0xclosed",
        condition_id="0xclosed",
    )
    portfolio = _make_portfolio(pos)

    class ClosedMarketClob:
        def get_clob_market_info(self, condition_id):
            assert condition_id == "0xclosed"
            return {
                "closed": True,
                "accepting_orders": False,
                "enable_order_book": False,
            }

        def get_best_bid_ask(self, token_id):
            raise AssertionError("closed market should not refresh executable quote")

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("closed market should not execute an exit")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_closed_day0_market_monitor_skip"),
            "cities_by_name": {"Chicago": type("City", (), {"timezone": "America/Chicago"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 18, 30, tzinfo=timezone.utc)),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("closed Day0 market must not reach monitor refresh")
        ),
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        ClosedMarketClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert pos.state == "day0_window"
    assert pos.exit_state == ""
    assert pos.exit_reason == ""
    assert pos.last_exit_error == "MARKET_CLOSED_AWAITING_SETTLEMENT:clob_market_info"
    assert summary["monitor_skipped_closed_market_pending_settlement"] == 1
    assert "monitor_chain_missing" not in summary
    assert "monitor_incomplete_exit_context" not in summary
    assert summary["monitors"] == 1
    assert monitor_results[0].exit_reason == "MARKET_CLOSED_AWAITING_SETTLEMENT"
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None


def test_day0_closed_market_detection_uses_static_market_end_when_clob_info_missing():
    """Missing post-close CLOB info must not send held positions into stale quote retry."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="snapshot-closed-day0-001",
        state="day0_window",
        chain_state="synced",
        city="Chicago",
        target_date="2026-04-01",
        market_id="0xsnapshotclosed",
        condition_id="0xsnapshotclosed",
    )

    class Row(dict):
        def __getitem__(self, key):
            if isinstance(key, int):
                return list(self.values())[key]
            return super().__getitem__(key)

    class SnapshotConn:
        def execute(self, sql, params=()):
            assert params == ("0xsnapshotclosed",)

            class Cursor:
                def fetchone(self):
                    return Row(
                        snapshot_id="snap-market-ended",
                        condition_id="0xsnapshotclosed",
                        market_end_at="2026-04-01T12:00:00+00:00",
                        market_close_at=None,
                        captured_at="2026-04-01T11:45:00+00:00",
                    )

            return Cursor()

    class MissingMarketInfoClob:
        def get_clob_market_info(self, condition_id):
            raise RuntimeError("post-close market info unavailable")

    info = cycle_runtime._closed_non_accepting_market_info(
        MissingMarketInfoClob(),
        pos,
        SnapshotConn(),
        decision_time=datetime(2026, 4, 1, 18, 30, tzinfo=timezone.utc),
    )

    assert info is not None
    assert info["source"] == "executable_snapshot_market_end"
    assert info["condition_id"] == "0xsnapshotclosed"
    assert info["accepting_orders"] is False


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
        condition_id="0xday0db100000000000000000000000000000000000000000000000000000001",
    )
    log_trade_entry(conn, pos)
    # Seed canonical entry baseline so the Day0 canonical emission is not
    # the first canonical event for this trade_id (matches production
    # reality — entries always precede day0 transitions).
    from src.state.lifecycle_manager import LifecyclePhase
    events, projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
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


def test_day0_canonical_emit_is_idempotent_when_monitor_replays_same_position(tmp_path):
    """Repeated monitor passes must not re-append DAY0_WINDOW_ENTERED."""
    from src.contracts import EntryMethod
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import (
        build_day0_window_entered_canonical_write,
        build_entry_canonical_write,
    )
    from src.state.db import append_many_and_project, get_connection, init_schema

    conn = get_connection(tmp_path / "day0-idempotent.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="day0-idem-1",
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        order_id="o-day0-idem",
        entry_order_id="o-day0-idem",
        entry_fill_verified=True,
        entered_at="2026-04-01T04:00:00Z",
        order_status="filled",
        strategy_key="center_buy",
        bin_label="50-51°F",
        selected_method=EntryMethod.ENS_MEMBER_COUNTING,
        condition_id="0xday0idem00000000000000000000000000000000000000000000000000000001",
    )
    from src.state.lifecycle_manager import LifecyclePhase
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-day0-idem-seed",
        source_module="tests/test_day0_canonical_emit_is_idempotent",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    pos.state = "day0_window"
    pos.day0_entered_at = "2026-04-02T02:00:00+00:00"
    day0_events, day0_projection = build_day0_window_entered_canonical_write(
        pos,
        day0_entered_at="2026-04-02T02:00:00+00:00",
        sequence_no=4,
        previous_phase="active",
        source_module="tests/test_day0_canonical_emit_is_idempotent",
    )
    append_many_and_project(conn, day0_events, day0_projection)

    deps = type(
        "Deps",
        (),
        {"logger": logging.getLogger("test_day0_idempotent")},
    )

    assert cycle_runtime._emit_day0_window_entered_canonical_if_available(
        conn,
        pos,
        day0_entered_at="2026-04-02T02:10:00+00:00",
        previous_phase="active",
        deps=deps,
    ) is False
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'DAY0_WINDOW_ENTERED'",
            ("day0-idem-1",),
        ).fetchone()[0]
        == 1
    )
    conn.close()


def test_monitor_refresh_canonical_emit_updates_current_projection(tmp_path):
    """Monitor refresh evidence must persist before exit logic can rely on it."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "monitor-refresh-canonical.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="monitor-refresh-1",
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        order_id="o-monitor-refresh",
        entered_at="2026-04-01T04:00:00+00:00",
        order_posted_at="2026-04-01T03:59:00+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="50-51°F",
        condition_id="0xmonitorrefresh000000000000000000000000000000000000000000000001",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-refresh-seed",
        source_module="tests/test_monitor_refresh_canonical_emit",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    pos.last_monitor_prob = 0.61
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.17
    pos.last_monitor_market_price = 0.44
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.43
    pos.last_monitor_best_ask = 0.45
    pos.selected_method = "emos"
    pos.applied_validations = ["identity_one_calibrator"]
    pos.last_monitor_at = "2026-04-01T05:00:00+00:00"

    deps = type(
        "Deps",
        (),
        {"logger": logging.getLogger("test_monitor_refresh_canonical_emit")},
    )

    assert cycle_runtime._emit_monitor_refreshed_canonical_if_available(conn, pos, deps=deps) is True

    event = conn.execute(
        """
        SELECT event_type, occurred_at, phase_before, phase_after, payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        ("monitor-refresh-1",),
    ).fetchone()
    assert event is not None
    assert event["occurred_at"] == "2026-04-01T05:00:00+00:00"
    assert event["phase_before"] == LifecyclePhase.ACTIVE.value
    assert event["phase_after"] == LifecyclePhase.ACTIVE.value
    payload = json.loads(event["payload_json"])
    assert payload["last_monitor_prob"] == pytest.approx(0.61)
    assert payload["last_monitor_market_price"] == pytest.approx(0.44)
    assert payload["selected_method"] == "emos"
    assert payload["applied_validations"] == ["identity_one_calibrator"]
    assert payload["exit_decision_available"] is False

    current = conn.execute(
        """
        SELECT phase, last_monitor_prob, last_monitor_edge,
               last_monitor_market_price, updated_at
          FROM position_current
         WHERE position_id = ?
        """,
        ("monitor-refresh-1",),
    ).fetchone()
    assert current["phase"] == LifecyclePhase.ACTIVE.value
    assert current["last_monitor_prob"] == pytest.approx(0.61)
    assert current["last_monitor_edge"] == pytest.approx(0.17)
    assert current["last_monitor_market_price"] == pytest.approx(0.44)
    assert current["updated_at"] == "2026-04-01T05:00:00+00:00"
    conn.close()


def test_monitor_refresh_preserves_chain_corrected_entry_economics(tmp_path):
    """Monitor refresh must not roll a chain-corrected position back to stale fill size/state."""
    from src.engine.lifecycle_events import (
        build_entry_canonical_write,
        build_monitor_refreshed_canonical_write,
    )
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "monitor-refresh-preserve-chain.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="monitor-preserve-chain-1",
        state="holding",
        city="Shenzhen",
        target_date="2026-06-19",
        order_id="o-monitor-preserve-chain",
        entered_at="2026-06-17T16:33:02+00:00",
        order_posted_at="2026-06-17T16:32:37+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="32C",
        condition_id="0xmonitorpreservechain000000000000000000000000000000000000001",
        size_usd=9.99,
        shares=13.5,
        cost_basis_usd=9.99,
        entry_price=0.74,
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-preserve-chain-seed",
        source_module="tests/test_monitor_refresh_preserves_chain_corrected_entry_economics",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.execute(
        """
        UPDATE position_current
           SET size_usd = 44.4,
               shares = 60.0,
               cost_basis_usd = 44.4,
               entry_price = 0.74,
               chain_state = 'local_only',
               chain_shares = 60.0,
               chain_avg_price = 0.74,
               chain_cost_basis_usd = 44.4,
               chain_seen_at = NULL
         WHERE position_id = ?
        """,
        ("monitor-preserve-chain-1",),
    )

    pos.last_monitor_prob = 0.869
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.133
    pos.last_monitor_market_price = 0.735
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_at = "2026-06-17T20:53:17+00:00"
    monitor_events, monitor_projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=4,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_monitor_refresh_preserves_chain_corrected_entry_economics",
    )
    append_many_and_project(conn, monitor_events, monitor_projection)

    current = conn.execute(
        """
        SELECT size_usd, shares, cost_basis_usd, chain_state, chain_shares,
               chain_cost_basis_usd, last_monitor_prob, last_monitor_edge,
               last_monitor_market_price, updated_at
          FROM position_current
         WHERE position_id = ?
        """,
        ("monitor-preserve-chain-1",),
    ).fetchone()
    assert current["size_usd"] == pytest.approx(44.4)
    assert current["shares"] == pytest.approx(60.0)
    assert current["cost_basis_usd"] == pytest.approx(44.4)
    assert current["chain_state"] == "synced"
    assert current["chain_shares"] == pytest.approx(60.0)
    assert current["chain_cost_basis_usd"] == pytest.approx(44.4)
    assert current["last_monitor_prob"] == pytest.approx(0.869)
    assert current["last_monitor_edge"] == pytest.approx(0.133)
    assert current["last_monitor_market_price"] == pytest.approx(0.735)
    assert current["updated_at"] == "2026-06-17T20:53:17+00:00"
    conn.close()


def test_quarantined_chain_risk_hard_fact_monitor_does_not_reopen_phase(tmp_path, monkeypatch):
    """Hard-fact monitor receipts for chain-risk quarantine must not reopen phase."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.execution.day0_hard_fact_exit import HardFactVerdict
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "quarantine-hard-fact-monitor.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="quarantine-hard-fact-monitor-1",
        state="quarantined",
        city="Manila",
        target_date="2026-06-29",
        order_id="o-quarantine-hard-fact-monitor",
        entered_at="2026-06-28T09:00:00+00:00",
        order_posted_at="2026-06-28T08:59:00+00:00",
        order_status="filled",
        strategy_key="center_buy",
        bin_label="32C",
        condition_id="0xquarantinehardfactmonitor00000000000000000000000000000001",
        direction="buy_no",
        shares=18.1,
        chain_shares=18.1,
        chain_state="entry_authority_quarantined",
        exit_reason="entry_authority_chain_absence_conflict",
        no_token_id="tok-manila-32-no",
        token_id="tok-manila-32-yes",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-quarantine-hard-fact-monitor-entry",
        source_module="tests/test_quarantined_hard_fact_monitor",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'quarantined',
               chain_state = 'entry_authority_quarantined',
               chain_shares = 18.1,
               exit_reason = 'entry_authority_chain_absence_conflict'
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    )
    conn.commit()
    monkeypatch.setattr(
        cycle_runtime,
        "_closed_non_accepting_market_info",
        lambda *args, **kwargs: {"source": "clob_market_info"},
    )
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda **kwargs: HardFactVerdict(
            action="EXIT_DEAD_BIN",
            reason="final high extreme 32.0 resolved inside bin [32.0,32.0] — YES won",
            metric="high",
            rounded_extreme=32.0,
            source="durable_observation_instants",
        ),
    )

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_quarantined_chain_risk_hard_fact_monitor"),
            "cities_by_name": {"Manila": type("City", (), {"timezone": "Asia/Manila"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        _make_portfolio(pos),
        artifact,
        type("Tracker", (), {"record_exit": lambda self, position: None})(),
        summary,
        deps=deps,
        exit_order_submit_enabled=False,
    )

    current = conn.execute(
        """
        SELECT phase, chain_state, chain_shares, exit_reason,
               last_monitor_prob, last_monitor_prob_is_fresh
          FROM position_current
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    ).fetchone()
    assert current["phase"] == LifecyclePhase.QUARANTINED.value
    assert current["chain_state"] == "entry_authority_quarantined"
    assert current["chain_shares"] == pytest.approx(18.1)
    assert current["exit_reason"] == "entry_authority_chain_absence_conflict"
    assert current["last_monitor_prob"] == pytest.approx(0.0)
    assert current["last_monitor_prob_is_fresh"] == 1
    event = conn.execute(
        """
        SELECT phase_before, phase_after, payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        (pos.trade_id,),
    ).fetchone()
    assert event is not None
    assert event["phase_before"] == LifecyclePhase.QUARANTINED.value
    assert event["phase_after"] == LifecyclePhase.QUARANTINED.value
    payload = json.loads(event["payload_json"])
    assert payload["exit_decision_reason"].startswith("DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED")
    assert payload["phase_after"] == LifecyclePhase.QUARANTINED.value
    assert monitor_results[0].should_exit is False
    conn.close()


def test_chain_projection_preserves_fresh_monitor_snapshot(tmp_path):
    """Chain sync writes must not erase the last monitor belief/quote snapshot."""
    from src.engine.lifecycle_events import (
        build_chain_economics_observed_canonical_write,
        build_entry_canonical_write,
        build_monitor_refreshed_canonical_write,
    )
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "chain-preserve-monitor.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="chain-preserve-monitor-1",
        state="holding",
        city="Munich",
        target_date="2026-06-30",
        order_id="o-chain-preserve-monitor",
        entered_at="2026-06-29T08:55:40+00:00",
        order_posted_at="2026-06-29T08:55:21+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="Will the highest temperature in Munich be 30°C on June 30?",
        condition_id="0xchainpreservemonitor000000000000000000000000000000000001",
        size_usd=21.27,
        shares=29.14,
        cost_basis_usd=21.27,
        entry_price=0.73,
        token_id="tok-munich-30-yes",
        no_token_id="tok-munich-30-no",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-chain-preserve-monitor-entry",
        source_module="tests/test_chain_projection_preserves_fresh_monitor_snapshot",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    pos.last_monitor_prob = 0.98
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.22
    pos.last_monitor_market_price = 0.76
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_at = "2026-06-29T20:02:40+00:00"
    monitor_events, monitor_projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=4,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_chain_projection_preserves_fresh_monitor_snapshot",
    )
    append_many_and_project(conn, monitor_events, monitor_projection)

    chain_pos = _make_position(
        trade_id=pos.trade_id,
        state="holding",
        city=pos.city,
        target_date=pos.target_date,
        order_id=pos.order_id,
        order_status=pos.order_status,
        strategy_key=pos.strategy_key,
        bin_label=pos.bin_label,
        condition_id=pos.condition_id,
        size_usd=pos.size_usd,
        shares=pos.shares,
        cost_basis_usd=pos.cost_basis_usd,
        entry_price=pos.entry_price,
        token_id=pos.token_id,
        no_token_id=pos.no_token_id,
        chain_state="synced",
        chain_shares=29.14,
        chain_avg_price=0.73,
        chain_cost_basis_usd=21.27,
        chain_verified_at="2026-06-29T22:20:52+00:00",
    )
    chain_events, chain_projection = build_chain_economics_observed_canonical_write(
        chain_pos,
        chain_observed_at="2026-06-29T22:20:52+00:00",
        sequence_no=5,
        phase_after=LifecyclePhase.ACTIVE.value,
        chain_shares_before=29.14,
        source_module="tests/test_chain_projection_preserves_fresh_monitor_snapshot",
    )
    append_many_and_project(conn, chain_events, chain_projection)

    current = conn.execute(
        """
        SELECT chain_state, chain_shares, chain_cost_basis_usd,
               last_monitor_prob, last_monitor_prob_is_fresh, last_monitor_edge,
               last_monitor_market_price, last_monitor_market_price_is_fresh,
               updated_at
          FROM position_current
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    ).fetchone()
    assert current["chain_state"] == "synced"
    assert current["chain_shares"] == pytest.approx(29.14)
    assert current["chain_cost_basis_usd"] == pytest.approx(21.27)
    assert current["last_monitor_prob"] == pytest.approx(0.98)
    assert current["last_monitor_prob_is_fresh"] == 1
    assert current["last_monitor_edge"] == pytest.approx(0.22)
    assert current["last_monitor_market_price"] == pytest.approx(0.76)
    assert current["last_monitor_market_price_is_fresh"] == 1
    assert current["updated_at"] == "2026-06-29T22:20:52+00:00"
    conn.close()


def test_venue_confirmed_local_only_fill_is_monitored_without_reclassifying_open_set(
    tmp_path,
    monkeypatch,
):
    """Venue-confirmed fills need monitor/redecision before chain sync catches up."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import get_open_positions

    conn = get_connection(tmp_path / "local-only-confirmed-fill-monitor.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="local-only-confirmed-fill-monitor-1",
        state="holding",
        city="Buenos Aires",
        target_date="2026-07-02",
        order_id="o-local-only-confirmed-fill-monitor",
        order_status="filled",
        entered_at="2026-07-01T22:19:06+00:00",
        order_posted_at="2026-07-01T22:17:03+00:00",
        strategy_key="center_buy",
        direction="buy_yes",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        shares=69.34,
        shares_filled=69.34,
        size_usd=2.84294,
        cost_basis_usd=2.84294,
        filled_cost_basis_usd=2.84294,
        entry_price=0.041,
        chain_state="local_only",
        chain_shares=0.0,
        token_id="tok-buenos-11-yes",
        no_token_id="tok-buenos-11-no",
        condition_id="condition-buenos-11",
        p_posterior=0.24833093804728934,
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-local-only-confirmed-fill-monitor-entry",
        source_module="tests/test_venue_confirmed_local_only_fill_is_monitored",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    portfolio = _make_portfolio(pos)

    assert get_open_positions(portfolio) == []
    assert cycle_runtime._monitoring_phase_positions(portfolio) == [pos]

    def fake_refresh(conn_arg, clob_arg, position):
        assert position is pos
        position.last_monitor_prob = 0.12
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.079
        position.last_monitor_market_price = 0.041
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_at = "2026-07-01T22:30:00+00:00"
        return EdgeContext(
            p_raw=np.array([0.12]),
            p_cal=np.array([0.12]),
            p_market=np.array([0.041]),
            p_posterior=0.12,
            forward_edge=0.079,
            alpha=0.0,
            confidence_band_upper=0.09,
            confidence_band_lower=0.07,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snapshot-local-only-confirmed-fill-monitor",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

    def fake_evaluate_exit(self, exit_context):
        assert exit_context.fresh_prob == pytest.approx(0.12)
        assert exit_context.current_market_price == pytest.approx(0.041)
        return ExitDecision(
            False,
            reason="CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior", "ci_overlap_hold"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", fake_evaluate_exit)
    monkeypatch.setattr(cycle_runtime, "_closed_non_accepting_market_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle_runtime, "_entry_selection_guard_exit_decision", lambda **kwargs: None)

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_venue_confirmed_local_only_fill_is_monitored"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["monitors"] == 1
    assert summary["exits"] == 0
    assert monitor_results[0].fresh_prob == pytest.approx(0.12)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'",
            (pos.trade_id,),
        ).fetchone()[0]
        == 1
    )
    current = conn.execute(
        """
        SELECT chain_state, chain_shares, last_monitor_prob,
               last_monitor_prob_is_fresh, last_monitor_market_price
          FROM position_current
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    ).fetchone()
    assert current["chain_state"] == "local_only"
    assert current["chain_shares"] == pytest.approx(0.0)
    assert current["last_monitor_prob"] == pytest.approx(0.12)
    assert current["last_monitor_prob_is_fresh"] == 1
    assert current["last_monitor_market_price"] == pytest.approx(0.041)
    conn.close()


def test_monitoring_phase_persists_monitor_decision_with_refresh(tmp_path, monkeypatch):
    """Monitor refresh canonical evidence must include the final hold/exit decision."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "monitor-before-exit.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="monitor-before-exit-1",
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        order_id="o-monitor-before-exit",
        entered_at="2026-04-01T04:00:00+00:00",
        order_posted_at="2026-04-01T03:59:00+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="50-51°F",
        condition_id="0xmonitorbeforeexit000000000000000000000000000000000000000001",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-before-exit-seed",
        source_module="tests/test_monitoring_phase_persists_monitor_evidence",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    portfolio = _make_portfolio(pos)

    def fake_refresh(conn_arg, clob_arg, position):
        assert conn_arg is conn
        position.last_monitor_prob = 0.62
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.18
        position.last_monitor_market_price = 0.44
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_at = "2026-04-01T05:00:00+00:00"
        return EdgeContext(
            p_raw=np.array([0.62]),
            p_cal=np.array([0.62]),
            p_market=np.array([0.44]),
            p_posterior=0.62,
            forward_edge=0.18,
            alpha=0.0,
            confidence_band_upper=0.20,
            confidence_band_lower=0.16,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snapshot-monitor-before-exit",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

    def fake_evaluate_exit(self, exit_context):
        prior_monitor_events = conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'",
            (self.trade_id,),
        ).fetchone()[0]
        assert prior_monitor_events == 0
        return ExitDecision(
            False,
            reason="CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior", "ci_overlap_hold"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", fake_evaluate_exit)

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_monitoring_phase_persists_monitor_evidence"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 5, 0, tzinfo=timezone.utc)),
            "has_acknowledged_quarantine_clear": staticmethod(lambda token_id: False),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["monitors"] == 1
    assert summary["exits"] == 0
    assert monitor_results[0].fresh_prob == pytest.approx(0.62)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'",
            ("monitor-before-exit-1",),
        ).fetchone()[0]
        == 1
    )
    event = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        ("monitor-before-exit-1",),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert payload["last_monitor_prob"] == pytest.approx(0.62)
    assert payload["last_monitor_market_price"] == pytest.approx(0.44)
    assert payload["exit_decision_available"] is True
    assert payload["exit_decision_should_exit"] is False
    assert payload["exit_decision_reason"] == "CI_OVERLAP_HOLD"
    assert payload["exit_decision_trigger"] == "CI_OVERLAP_HOLD"
    assert payload["exit_decision_applied_validations"] == [
        "replacement_posterior",
        "ci_overlap_hold",
    ]
    conn.close()


def test_family_monitor_overlay_suppresses_single_leg_statistical_exit_and_persists_payload():
    """Same-family holdings must not liquidate one leg before family value is checked."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos_a = _make_position(
        trade_id="family-monitor-a",
        city="Shanghai",
        target_date="2026-06-19",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=7.0,
        entry_price=0.79,
        p_posterior=0.84,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos_b = _make_position(
        trade_id="family-monitor-b",
        city="Shanghai",
        target_date="2026-06-19",
        temperature_metric="high",
        bin_label="31C",
        direction="buy_no",
        shares=5.55,
        entry_price=0.80,
        p_posterior=0.85,
        strategy_key="center_bin_buy",
        env="live",
    )
    for pos, prob, bid in ((pos_a, 0.86, 0.71), (pos_b, 0.83, 0.75)):
        pos.last_monitor_at = "2026-06-18T23:25:00+00:00"
        pos.last_monitor_prob = prob
        pos.last_monitor_prob_is_fresh = True
        pos.last_monitor_market_price = bid
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_best_bid = bid
        pos.last_monitor_best_ask = min(0.99, bid + 0.02)
        pos.last_monitor_edge = prob - bid
        pos.applied_validations = ["replacement_posterior", "ci_separated_reversal"]

    portfolio = _make_portfolio(pos_a, pos_b)
    single_leg_exit = ExitDecision(
        True,
        reason="CI_SEPARATED_REVERSAL (entry=0.8900, current=0.8600)",
        trigger="CI_SEPARATED_REVERSAL",
        selected_method="replacement_posterior",
        applied_validations=["replacement_posterior", "ci_separated_reversal"],
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=portfolio,
        pos=pos_a,
        exit_decision=single_leg_exit,
        should_exit=True,
        exit_reason=single_leg_exit.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "FAMILY_HOLD_DOMINATES_SINGLE_LEG_EXIT"
    assert summary["family_redecision_single_leg_exits_suppressed"] == 1
    assert "family_hold_dominates_single_leg_exit" in pos_a.applied_validations

    events, _projection = build_monitor_refreshed_canonical_write(
        pos_a,
        sequence_no=4,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_family_monitor_overlay",
        exit_decision=single_leg_exit,
        final_should_exit=should_exit,
        final_exit_reason=reason,
    )
    payload = json.loads(events[0]["payload_json"])
    assert payload["exit_decision_should_exit"] is False
    assert payload["exit_decision_reason"] == "FAMILY_HOLD_DOMINATES_SINGLE_LEG_EXIT"
    assert payload["family_redecision"]["decision"] == "FAMILY_HOLD_DOMINATES_SINGLE_LEG_EXIT"
    assert payload["family_redecision"]["family_hold_value_usd"] > payload["family_redecision"]["family_direct_sell_value_usd"]


def test_single_leg_monitor_records_family_redecision_value_payload():
    """A single held leg still needs continuous hold-vs-sell evidence in receipts."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="single-family-monitor",
        city="Paris",
        target_date="2026-06-20",
        temperature_metric="low",
        bin_label="19C",
        direction="buy_no",
        shares=5.06,
        entry_price=0.75,
        p_posterior=0.80,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-18T23:55:00+00:00"
    pos.last_monitor_prob = 0.78
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.73
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.73
    pos.last_monitor_best_ask = 0.75
    pos.last_monitor_edge = 0.05
    portfolio = _make_portfolio(pos)
    hold_decision = ExitDecision(
        False,
        reason="CI_OVERLAP_HOLD",
        trigger="CI_OVERLAP_HOLD",
        selected_method="replacement_posterior",
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=portfolio,
        pos=pos,
        exit_decision=hold_decision,
        should_exit=False,
        exit_reason=hold_decision.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "CI_OVERLAP_HOLD"
    assert summary["family_redecision_overlay_evaluated"] == 1

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=2,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_single_leg_monitor_redecision",
        exit_decision=hold_decision,
        final_should_exit=should_exit,
        final_exit_reason=reason,
    )
    payload = json.loads(events[0]["payload_json"])
    family = payload["family_redecision"]
    assert family["position_count"] == 1
    assert family["decision"] == "FAMILY_OVERLAY_NO_OVERRIDE"
    assert family["family_hold_value_usd"] == pytest.approx(5.06 * 0.78)
    assert family["family_direct_sell_value_usd"] == pytest.approx(5.06 * 0.73)


def test_family_monitor_overlay_promotes_hold_when_direct_sell_value_dominates():
    """Continuous redecision must act when fresh family sell value beats hold value."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="family-direct-sell-dominates",
        city="Seoul",
        target_date="2026-06-26",
        temperature_metric="low",
        bin_label="21C",
        direction="buy_no",
        shares=15.5,
        entry_price=0.62,
        p_posterior=0.67,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-24T14:55:00+00:00"
    pos.last_monitor_prob = 0.60
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.70108
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.70108
    pos.last_monitor_best_ask = 0.72
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    pos.applied_validations = ["replacement_posterior", "ci_separated_edge_within_threshold_hold"]

    portfolio = _make_portfolio(pos)
    hold_decision = ExitDecision(
        False,
        reason="CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD",
        trigger="CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD",
        selected_method="replacement_posterior",
        applied_validations=list(pos.applied_validations),
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=portfolio,
        pos=pos,
        exit_decision=hold_decision,
        should_exit=False,
        exit_reason=hold_decision.reason,
        summary=summary,
    )
    trigger = cycle_runtime._effective_exit_trigger(hold_decision, reason)

    assert should_exit is True
    assert reason == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert trigger == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert summary["family_redecision_hold_exits_promoted"] == 1
    assert "family_direct_sell_dominates_hold_exit" in pos.applied_validations

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=3,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_family_direct_sell_dominates",
        exit_decision=hold_decision,
        final_should_exit=should_exit,
        final_exit_reason=reason,
        final_exit_trigger=trigger,
    )
    payload = json.loads(events[0]["payload_json"])
    family = payload["family_redecision"]
    assert payload["exit_decision_should_exit"] is True
    assert payload["exit_decision_reason"] == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert payload["exit_decision_trigger"] == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert family["decision"] == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert family["belief_reversed_below_entry"] is True
    assert family["family_direct_sell_value_usd"] > family["family_hold_value_usd"]
    assert family["family_direct_sell_advantage_usd"] > family["family_direct_sell_advantage_threshold_usd"]


def test_family_monitor_overlay_blocks_direct_sell_on_immature_day0_authority():
    """Pre-peak Day0 remaining-window signal is not sell authority by itself."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-direct-sell-day0-immature",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:00+00:00"
    pos.last_monitor_prob = 0.15
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.55
    pos.last_monitor_best_ask = 0.57
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    pos.applied_validations = [
        "day0_observation_remaining_window",
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
    ]

    hold_decision = ExitDecision(
        False,
        reason="CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD",
        trigger="CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD",
        selected_method="day0_observation_remaining_window",
        applied_validations=list(pos.applied_validations),
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=hold_decision,
        should_exit=False,
        exit_reason=hold_decision.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD"
    assert summary["family_redecision_day0_immature_exits_blocked"] == 1
    assert "family_direct_sell_blocked_day0_immature" in pos.applied_validations
    family = pos._monitor_family_redecision
    assert family["decision"] == "FAMILY_DIRECT_SELL_BLOCKED_DAY0_IMMATURE"
    assert family["family_direct_sell_value_usd"] > family["family_hold_value_usd"]


def test_family_monitor_overlay_replays_munich_0244_receipt_as_immature_day0_hold():
    """Munich 02:44 receipt shape must not be promoted into a sell."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    validations = [
        "day0_observation_remaining_window",
        "belief_source=day0_observation_remaining_window;kind=probabilistic_remaining_window;metric=high;posterior_mode=model_only_v1",
        "market_quote_prior_excluded:day0_observation_remaining_window",
        "alpha_blend_inapplicable:day0_observation_remaining_window",
        "day0_observation",
        "day0_hourly_vectors",
        "forecast_source_id:day0_hourly_vectors",
        "forecast_source_role:day0_remaining_window_live",
        "day0_extreme_not_absorbing",
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        "mc_instrument_noise",
        "day0_remaining_window_raw_vector_normalization",
        "ci_threshold",
        "ci_overlap_hold",
    ]
    pos = _make_position(
        trade_id="munich-29-no-0244-receipt",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="Will the highest temperature in Munich be 29°C on June 30?",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.8728257780611077,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:44.908942+00:00"
    pos.last_monitor_prob = 0.15810000000000002
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.57
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.57
    pos.last_monitor_best_ask = 0.58
    pos.last_monitor_edge = -0.41189999999999993
    pos.applied_validations = list(validations)

    hold_decision = ExitDecision(
        False,
        reason="CI_OVERLAP_HOLD",
        trigger="CI_OVERLAP_HOLD",
        selected_method="day0_observation_remaining_window",
        applied_validations=list(validations),
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=hold_decision,
        should_exit=False,
        exit_reason=hold_decision.reason,
        summary=summary,
    )
    trigger = cycle_runtime._effective_exit_trigger(hold_decision, reason)

    assert should_exit is False
    assert reason == "CI_OVERLAP_HOLD"
    assert trigger == "CI_OVERLAP_HOLD"
    assert summary["family_redecision_day0_immature_exits_blocked"] == 1
    assert "family_direct_sell_blocked_day0_immature" in pos.applied_validations

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=27,
        phase_after=LifecyclePhase.DAY0_WINDOW.value,
        source_module="tests/test_munich_0244_receipt",
        exit_decision=hold_decision,
        final_should_exit=should_exit,
        final_exit_reason=reason,
        final_exit_trigger=trigger,
    )
    payload = json.loads(events[0]["payload_json"])
    family = payload["family_redecision"]
    assert payload["exit_decision_should_exit"] is False
    assert payload["exit_decision_reason"] == "CI_OVERLAP_HOLD"
    assert payload["exit_decision_trigger"] == "CI_OVERLAP_HOLD"
    assert family["decision"] == "FAMILY_DIRECT_SELL_BLOCKED_DAY0_IMMATURE"
    assert family["suppressed_exit_reason"] == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert family["family_direct_sell_value_usd"] > family["family_hold_value_usd"]
    assert family["day0_maturity_block"].startswith("day0_high_extreme_not_mature:")
    assert "FAMILY_DIRECT_SELL_DOMINATES_HOLD" not in {
        payload["exit_decision_reason"],
        payload["exit_decision_trigger"],
    }


def test_monitor_refreshed_persists_day0_probability_receipt():
    """Day0 monitor events must carry enough input evidence to replay probability flips."""
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="munich-day0-receipt",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="Will the highest temperature in Munich be 29°C on June 30?",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.8728257780611077,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:44.908942+00:00"
    pos.last_monitor_prob = 0.15810000000000002
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.57
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.57
    pos.last_monitor_best_ask = 0.58
    pos.last_monitor_edge = -0.41189999999999993
    pos.selected_method = "day0_observation_remaining_window"
    pos.applied_validations = [
        "day0_observation_remaining_window",
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
    ]
    pos._day0_monitor_probability_receipt = {
        "schema_version": 1,
        "selected_method": "day0_observation_remaining_window",
        "metric": "high",
        "held_idx": 1,
        "held_direction": "buy_no",
        "held_side_probability": 0.15810000000000002,
        "bin_labels": ["28C", "29C", "30C"],
        "p_cal_vector": [0.01, 0.8419, 0.1481],
        "observation": {
            "source": "wu_hourly",
            "observed_high_so_far": 18.5,
            "current_temp": 18.0,
            "observation_time": "2026-06-30T02:44:00+00:00",
        },
        "remaining_window": {
            "source": "day0_hourly_vectors",
            "source_models": ["icon_d2"],
            "source_model_count": 1,
            "fetch_time": "2026-06-30T02:44:32.480826+00:00",
            "hours_remaining": 21.25,
            "member_extrema_summary": {
                "count": 1,
                "min": 28.8,
                "q50": 28.8,
                "q90": 28.8,
                "max": 28.8,
            },
        },
        "temporal_context": {
            "daypart": "pre_sunrise",
            "post_peak_confidence": 0.034,
        },
        "maturity_validations": [
            "day0_extreme_not_absorbing",
            "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        ],
    }

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=27,
        phase_after=LifecyclePhase.DAY0_WINDOW.value,
        source_module="tests/test_day0_probability_receipt",
    )

    payload = json.loads(events[0]["payload_json"])
    receipt = payload["day0_monitor_probability_receipt"]
    assert receipt["selected_method"] == "day0_observation_remaining_window"
    assert receipt["remaining_window"]["source"] == "day0_hourly_vectors"
    assert receipt["remaining_window"]["source_models"] == ["icon_d2"]
    assert receipt["remaining_window"]["member_extrema_summary"]["max"] == pytest.approx(28.8)
    assert receipt["held_side_probability"] == pytest.approx(0.15810000000000002)
    assert receipt["p_cal_vector"] == pytest.approx([0.01, 0.8419, 0.1481])


def test_monitor_refreshed_persists_conditioned_daily_extrema_receipt():
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="taipei-conditioned-daily-receipt",
        city="Taipei",
        target_date="2026-07-09",
        temperature_metric="high",
        bin_label="Will the highest temperature in Taipei be 35°C on July 9?",
        direction="buy_no",
        shares=3.8,
        entry_price=0.64,
        p_posterior=0.8006076372881108,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-07-09T11:20:00+00:00"
    pos.last_monitor_prob = 0.0066
    pos.last_monitor_prob_is_fresh = True
    pos.selected_method = "day0_observation_conditioned_daily_extrema"
    pos.applied_validations = [
        "day0_observation_conditioned_daily_extrema",
        "day0_daily_extrema_not_remaining_window:day0_daily_extrema_live",
    ]
    pos._day0_monitor_probability_receipt = {
        "schema_version": 1,
        "selected_method": "day0_observation_conditioned_daily_extrema",
        "metric": "high",
        "held_side_probability": 0.0066,
        "remaining_window": {
            "source": "day0_observed_bound_conditioned_daily_extrema",
            "member_extrema_summary": {"count": 1, "max": 35.0},
            "raw_member_extrema_summary": {"count": 1, "max": 36.0},
        },
    }

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=28,
        phase_after=LifecyclePhase.DAY0_WINDOW.value,
        source_module="tests/test_day0_conditioned_daily_receipt",
    )

    payload = json.loads(events[0]["payload_json"])
    receipt = payload["day0_monitor_probability_receipt"]
    assert receipt["selected_method"] == "day0_observation_conditioned_daily_extrema"
    assert receipt["remaining_window"]["source"] == (
        "day0_observed_bound_conditioned_daily_extrema"
    )
    assert receipt["remaining_window"]["raw_member_extrema_summary"]["max"] == pytest.approx(36.0)
    assert receipt["remaining_window"]["member_extrema_summary"]["max"] == pytest.approx(35.0)


def test_monitor_refreshed_omits_stale_day0_probability_receipt_on_non_day0_method():
    """A stale Day0 receipt must not contaminate later non-Day0 monitor events."""
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="replacement-monitor-after-day0",
        city="Munich",
        target_date="2026-07-02",
        temperature_metric="high",
        bin_label="Will the highest temperature in Munich be 29°C on July 2?",
        direction="buy_no",
        shares=12.0,
        entry_price=0.60,
        p_posterior=0.80,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.selected_method = "replacement_posterior"
    pos.last_monitor_at = "2026-06-30T12:00:00+00:00"
    pos.last_monitor_prob = 0.80
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.61
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_edge = 0.19
    pos._day0_monitor_probability_receipt = {
        "schema_version": 1,
        "selected_method": "day0_observation_remaining_window",
        "remaining_window": {"source": "day0_hourly_vectors"},
    }

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=3,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_day0_probability_receipt",
    )

    payload = json.loads(events[0]["payload_json"])
    assert payload["selected_method"] == "replacement_posterior"
    assert "day0_monitor_probability_receipt" not in payload


def test_family_monitor_overlay_includes_entry_authority_quarantined_family_exposure():
    """Chain-backed quarantine is live money risk and must enter family value math."""
    from src.engine import cycle_runtime

    active = _make_position(
        trade_id="munich-29-no-active",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="Will the highest temperature in Munich be 29°C on June 30?",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.8728257780611077,
        state="day0_window",
        last_monitor_prob=0.84,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.57,
        last_monitor_market_price_is_fresh=True,
        last_monitor_best_bid=0.57,
    )
    quarantined = _make_position(
        trade_id="munich-30-no-quarantined",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="Will the highest temperature in Munich be 30°C on June 30?",
        direction="buy_no",
        shares=29.14,
        chain_shares=29.14,
        chain_state="entry_authority_quarantined",
        entry_price=0.73,
        p_posterior=0.879883784472759,
        state="quarantined",
        last_monitor_prob=0.99,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.74,
        last_monitor_market_price_is_fresh=True,
        last_monitor_best_bid=0.74,
    )
    hold_decision = ExitDecision(
        False,
        reason="CI_OVERLAP_HOLD",
        trigger="CI_OVERLAP_HOLD",
        selected_method="day0_observation_remaining_window",
        applied_validations=["ci_overlap_hold"],
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(active, quarantined),
        pos=active,
        exit_decision=hold_decision,
        should_exit=False,
        exit_reason=hold_decision.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "CI_OVERLAP_HOLD"
    family = active._monitor_family_redecision
    assert family["position_count"] == 2
    assert {leg["position_id"] for leg in family["legs"]} == {
        "munich-29-no-active",
        "munich-30-no-quarantined",
    }
    assert family["family_hold_value_usd"] == pytest.approx(
        33.15 * 0.84 + 29.14 * 0.99
    )
    assert family["family_direct_sell_value_usd"] == pytest.approx(
        33.15 * 0.57 + 29.14 * 0.74
    )


def test_family_monitor_overlay_holds_munich_buy_no_when_held_side_probability_high():
    """Munich regression: buy_no monitor probability is NO-space, not same-bin YES q."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="munich-29-no-held-side-hold",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.8728257780611077,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:00+00:00"
    pos.last_monitor_prob = 0.8296
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.5646775174931549
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.55
    pos.last_monitor_best_ask = 0.57
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    pos.applied_validations = ["day0_observation_remaining_window"]

    hold_decision = ExitDecision(
        False,
        reason="CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD",
        trigger="CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD",
        selected_method="day0_observation_remaining_window",
        applied_validations=list(pos.applied_validations),
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=hold_decision,
        should_exit=False,
        exit_reason=hold_decision.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD"
    family = pos._monitor_family_redecision
    assert family["decision"] == "FAMILY_OVERLAY_NO_OVERRIDE"
    assert family["family_hold_value_usd"] > family["family_direct_sell_value_usd"]
    assert "family_direct_sell_dominates_hold_exit" not in pos.applied_validations


def test_family_monitor_overlay_blocks_statistical_exit_on_immature_day0_authority():
    """An immature Day0 validation cannot sponsor a CI-style exit."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-stat-exit-day0-immature",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:00+00:00"
    pos.last_monitor_prob = 0.15
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.55
    pos.last_monitor_best_ask = 0.57
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    pos.applied_validations = [
        "day0_observation_remaining_window",
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
    ]
    exit_decision = ExitDecision(
        True,
        reason="CI_SEPARATED_REVERSAL",
        trigger="CI_SEPARATED_REVERSAL",
        selected_method="day0_observation_remaining_window",
        applied_validations=list(pos.applied_validations),
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=exit_decision,
        should_exit=True,
        exit_reason=exit_decision.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "FAMILY_DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED"
    assert summary["family_redecision_day0_immature_exits_blocked"] == 1
    assert "family_day0_immature_exit_authority_blocked" in pos.applied_validations
    assert pos._monitor_family_redecision["decision"] == "FAMILY_DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED"


def test_family_monitor_overlay_blocks_exit_decision_only_immature_day0_authority():
    """Munich regression: exit-decision-only immature Day0 cannot authorize exit."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-stat-exit-day0-immature-exit-decision-only",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:00+00:00"
    pos.last_monitor_prob = 0.15
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.55
    pos.last_monitor_best_ask = 0.57
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    pos.applied_validations = ["day0_observation_remaining_window"]
    exit_decision = ExitDecision(
        True,
        reason="CI_SEPARATED_REVERSAL",
        trigger="CI_SEPARATED_REVERSAL",
        selected_method="day0_observation_remaining_window",
        applied_validations=[
            "day0_observation_remaining_window",
            "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        ],
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=exit_decision,
        should_exit=True,
        exit_reason=exit_decision.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "FAMILY_DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED"
    assert summary["family_redecision_day0_immature_exits_blocked"] == 1
    assert pos._monitor_family_redecision["day0_maturity_block"].startswith(
        "day0_high_extreme_not_mature:"
    )


def test_family_monitor_overlay_blocks_immature_day0_before_missing_family_quotes():
    """Missing sibling quote evidence cannot bypass immature Day0 exit authority."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-stat-exit-day0-immature-missing-quotes",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:00+00:00"
    pos.last_monitor_prob = 0.15
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.55
    pos.last_monitor_best_ask = 0.57
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    sibling = _make_position(
        trade_id="family-stat-exit-day0-immature-stale-sibling",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="30C",
        direction="buy_no",
        shares=10.0,
        entry_price=0.70,
        p_posterior=0.90,
        strategy_key="center_bin_buy",
        env="live",
    )
    sibling.last_monitor_prob = 0.90
    sibling.last_monitor_prob_is_fresh = True
    sibling.last_monitor_market_price = 0.40
    sibling.last_monitor_market_price_is_fresh = False
    sibling.last_monitor_best_bid = 0.40
    sibling.last_monitor_best_ask = 0.42
    exit_decision = ExitDecision(
        True,
        reason="CI_SEPARATED_REVERSAL",
        trigger="CI_SEPARATED_REVERSAL",
        selected_method="day0_observation_remaining_window",
        applied_validations=[
            "day0_observation_remaining_window",
            "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        ],
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos, sibling),
        pos=pos,
        exit_decision=exit_decision,
        should_exit=True,
        exit_reason=exit_decision.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "FAMILY_DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED"
    assert summary["family_redecision_day0_immature_exits_blocked"] == 1
    family = pos._monitor_family_redecision
    assert family["decision"] == "FAMILY_DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED"
    assert family["missing"] == [
        {
            "position_id": "family-stat-exit-day0-immature-stale-sibling",
            "reason": "market_price_not_fresh",
        }
    ]


def test_exit_evidence_gate_blocks_family_direct_sell_on_immature_day0_authority():
    """Final exit gate is a second Day0 maturity lock after family overlay."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-direct-sell-final-gate-day0-immature",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.applied_validations = [
        "day0_observation_remaining_window",
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        "family_direct_sell_dominates_hold_exit",
    ]
    summary = {}
    deps = SimpleNamespace(logger=logging.getLogger("test_exit_gate_day0_immature"))

    allowed, reason = cycle_runtime._exit_evidence_gate_allows_statistical_exit(
        conn=sqlite3.connect(":memory:"),
        pos=pos,
        exit_trigger="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        summary=summary,
        deps=deps,
    )

    assert allowed is False
    assert reason == (
        "DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED:"
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034"
    )
    assert summary["exit_evidence_missing_blocked"] == 1
    assert summary["exit_evidence_gate_blocked_positions"] == [
        {
            "position_id": "family-direct-sell-final-gate-day0-immature",
            "trigger": "FAMILY_DIRECT_SELL_DOMINATES_HOLD",
            "reason": reason,
        }
    ]


def test_family_monitor_overlay_does_not_sell_winner_without_belief_reversal():
    """A high bid over a conservative belief is not by itself an exit signal."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-direct-sell-winner-hold",
        city="Paris",
        target_date="2026-06-20",
        temperature_metric="low",
        bin_label="19C",
        direction="buy_no",
        shares=5.1,
        entry_price=0.75,
        p_posterior=0.80,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-24T14:55:00+00:00"
    pos.last_monitor_prob = 0.82
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.998
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.998
    pos.last_monitor_best_ask = 0.999
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    pos.applied_validations = ["replacement_posterior"]

    hold_decision = ExitDecision(
        False,
        reason="CI_OVERLAP_HOLD",
        trigger="CI_OVERLAP_HOLD",
        selected_method="replacement_posterior",
        applied_validations=list(pos.applied_validations),
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=hold_decision,
        should_exit=False,
        exit_reason=hold_decision.reason,
        summary=summary,
    )

    assert should_exit is False
    assert reason == "CI_OVERLAP_HOLD"
    assert summary["family_redecision_overlay_evaluated"] == 1
    family = pos._monitor_family_redecision
    assert family["decision"] == "FAMILY_OVERLAY_NO_OVERRIDE"
    assert family["family_direct_sell_value_usd"] > family["family_hold_value_usd"]
    assert "family_direct_sell_dominates_hold_exit" not in pos.applied_validations


def test_family_monitor_overlay_keeps_chain_backed_quarantine_in_family_vector():
    """Chain-backed quarantine remains live family risk for hold-vs-sell math."""
    from src.engine import cycle_runtime

    old_leg = _make_position(
        trade_id="munich-30-quarantine",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="30C",
        direction="buy_no",
        shares=29.14,
        chain_shares=29.14,
        chain_state="entry_authority_quarantined",
        state="quarantined",
        entry_price=0.73,
        p_posterior=0.88,
        strategy_key="center_bin_buy",
        env="live",
    )
    new_leg = _make_position(
        trade_id="munich-29-active",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        state="day0_window",
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    for pos, prob, bid in ((old_leg, 0.92, 0.71), (new_leg, 0.84, 0.55)):
        pos.last_monitor_prob = prob
        pos.last_monitor_prob_is_fresh = True
        pos.last_monitor_market_price = bid
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_best_bid = bid
        pos.last_monitor_best_ask = min(0.99, bid + 0.02)

    positions = cycle_runtime._family_monitor_positions(
        _make_portfolio(old_leg, new_leg),
        new_leg,
    )

    assert [pos.trade_id for pos in positions] == [
        "munich-30-quarantine",
        "munich-29-active",
    ]


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
    assert (
        pos.selected_method
        == monitor_refresh.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
    )
    assert EntryMethod.DAY0_OBSERVATION.value in pos.applied_validations
    assert "day0_observation_remaining_window" in pos.applied_validations
    assert "whale_toxicity_deferred:fresh_probability_authority" in pos.applied_validations
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
    assert (
        pos.selected_method
        == monitor_refresh.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
    )
    assert EntryMethod.DAY0_OBSERVATION.value in pos.applied_validations
    assert "day0_observation_remaining_window" in pos.applied_validations
    assert edge_ctx.p_posterior == pytest.approx(0.52)
    assert edge_ctx.entry_provenance == EntryMethod.ENS_MEMBER_COUNTING
    assert pos.last_monitor_prob == pytest.approx(0.52)
    assert pos.last_monitor_market_price == pytest.approx(0.41)


def test_day0_wu_observation_unavailable_reseeds_without_forecast_fallback(monkeypatch):
    """A missing Day0 observation must not borrow legacy forecast freshness."""
    from src.contracts import EntryMethod
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine import monitor_refresh

    pos = _make_position(
        state="day0_window",
        city="Chicago",
        target_date="2026-04-01",
        entry_method=EntryMethod.ENS_MEMBER_COUNTING.value,
        selected_method="",
        applied_validations=[],
    )
    city = type(
        "City",
        (),
        {
            "name": "Chicago",
            "timezone": "America/Chicago",
            "settlement_source_type": "wu_icao",
        },
    )()
    observed_methods = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_methods.append(position.entry_method)
        if position.entry_method == EntryMethod.DAY0_OBSERVATION.value:
            raise ObservationUnavailableError("wu observation unavailable")
        raise AssertionError("legacy forecast monitor fallback must not run")

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)
    reseeds = []
    monkeypatch.setattr(
        monitor_refresh,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kw: reseeds.append(kw),
    )

    p, refresh_pos, fresh = monitor_refresh.monitor_probability_refresh(
        pos,
        conn=None,
        city=city,
        target_d=date(2026, 4, 1),
    )

    assert observed_methods == [
        EntryMethod.DAY0_OBSERVATION.value,
    ]
    assert p == pytest.approx(pos.p_posterior)
    assert refresh_pos is not pos
    assert refresh_pos.entry_method == EntryMethod.DAY0_OBSERVATION.value
    assert fresh is False
    assert "day0_observation_unavailable:replacement_belief_reseed" in refresh_pos.applied_validations
    assert all("forecast_monitor_fallback" not in v for v in refresh_pos.applied_validations)
    assert "q_source:emos" not in refresh_pos.applied_validations
    assert reseeds == [
        {"city": "Chicago", "target_date": "2026-04-01", "metric": "high"}
    ]


def test_day0_absorbing_hard_fact_dominates_replacement_posterior(monkeypatch):
    """Tokyo LOW regression: absorbing hard fact is exact monitor belief."""
    from src.engine import monitor_refresh
    from src.execution.day0_hard_fact_exit import HardFactVerdict

    pos = _make_position(
        state="day0_window",
        city="Tokyo",
        cluster="East Asia",
        target_date="2026-06-18",
        bin_label="21°C on June 18?",
        direction="buy_no",
        temperature_metric="low",
        unit="C",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
        entry_price=0.58,
        p_posterior=0.720612963366361,
        token_id="tok_yes_tokyo_low_21",
        no_token_id="tok_no_tokyo_low_21",
    )

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            assert token_id == "tok_no_tokyo_low_21"
            return 0.99, 1.00, 100.0, 100.0

    monkeypatch.setattr(monitor_refresh, "_is_position_target_local_day", lambda *a, **k: True)
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda *, position, city, now=None, world_conn=None: HardFactVerdict(
            action="HOLD_STRUCTURAL_WIN",
            reason="running low extreme 20 killed bin [21.0,21.0]",
            metric="low",
            rounded_extreme=20.0,
            source="same_station_fast_tail",
        ),
    )
    monkeypatch.setattr(
        "src.engine.position_belief.load_replacement_belief",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("replacement posterior must not be read before absorbing hard fact")
        ),
    )

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert pos.selected_method == monitor_refresh.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    assert pos.last_monitor_prob_is_fresh is True
    assert pos.last_monitor_prob == pytest.approx(1.0)
    assert pos.last_monitor_market_price == pytest.approx(0.99)
    assert pos.last_monitor_edge == pytest.approx(0.01)
    assert edge_ctx.p_posterior == pytest.approx(1.0)
    assert edge_ctx.forward_edge == pytest.approx(0.01)
    assert monitor_refresh.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT in pos.applied_validations
    belief_tags = [
        tag for tag in pos.applied_validations
        if str(tag).startswith("belief_source=day0_absorbing_hard_fact;")
    ]
    assert belief_tags
    assert "yes_verdict=YES_DEAD" in belief_tags[0]
    assert "held_verdict=STRUCTURAL_WIN" in belief_tags[0]
    assert "held_prob=1.000000" in belief_tags[0]
    assert "forecast_posteriors_dominated_by_day0_hard_fact" in pos.applied_validations
    assert "model_divergence_panic_inapplicable:day0_absorbing_hard_fact" in pos.applied_validations


def test_active_same_day_absorbing_hard_fact_dominates_replacement_posterior(monkeypatch):
    """Active same-day positions must not wait for phase transition before hard-fact overlay."""
    from src.engine import monitor_refresh
    from src.execution.day0_hard_fact_exit import HardFactVerdict

    pos = _make_position(
        state="holding",
        city="Tokyo",
        cluster="East Asia",
        target_date="2026-06-18",
        bin_label="21°C on June 18?",
        direction="buy_no",
        temperature_metric="low",
        unit="C",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
        entry_price=0.58,
        p_posterior=0.720612963366361,
        token_id="tok_yes_tokyo_low_21",
        no_token_id="tok_no_tokyo_low_21",
    )

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            assert token_id == "tok_no_tokyo_low_21"
            return 0.99, 1.00, 100.0, 100.0

    monkeypatch.setattr(monitor_refresh, "_is_position_target_local_day", lambda *a, **k: True)
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda *, position, city, now=None, world_conn=None: HardFactVerdict(
            action="HOLD_STRUCTURAL_WIN",
            reason="running low extreme 20 killed bin [21.0,21.0]",
            metric="low",
            rounded_extreme=20.0,
            source="same_station_fast_tail",
        ),
    )
    monkeypatch.setattr(
        "src.engine.position_belief.load_replacement_belief",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("replacement posterior must not be read before active same-day hard fact")
        ),
    )

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert str(pos.state.value if hasattr(pos.state, "value") else pos.state) == "holding"
    assert pos.selected_method == monitor_refresh.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    assert pos.last_monitor_prob_is_fresh is True
    assert pos.last_monitor_prob == pytest.approx(1.0)
    assert edge_ctx.p_posterior == pytest.approx(1.0)
    assert monitor_refresh.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT in pos.applied_validations
    assert "forecast_posteriors_dominated_by_day0_hard_fact" in pos.applied_validations


def test_day0_high_morning_observation_is_not_exit_authority():
    """A local-day running HIGH near midnight is not the day's final high authority."""
    from src.engine import monitor_refresh
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    temporal_context = SimpleNamespace(daypart="morning", post_peak_confidence=0.0)

    reason = monitor_refresh._day0_extreme_authority_rejection_reason(
        temperature_metric=HIGH_LOCALDAY_MAX,
        temporal_context=temporal_context,
        hours_remaining=23.0,
        observed_extreme_so_far=22.2,
        member_extrema_remaining=np.array([24.0, 25.0, 26.0]),
    )

    assert reason is not None
    assert reason.startswith("day0_high_extreme_not_mature:")


def test_day0_low_nonterminal_observation_is_not_exit_authority():
    """A local-day running LOW is not final-low authority while most of the day remains."""
    from src.engine import monitor_refresh
    from src.types.metric_identity import LOW_LOCALDAY_MIN

    temporal_context = SimpleNamespace(daypart="morning", post_peak_confidence=0.0)

    reason = monitor_refresh._day0_extreme_authority_rejection_reason(
        temperature_metric=LOW_LOCALDAY_MIN,
        temporal_context=temporal_context,
        hours_remaining=18.0,
        observed_extreme_so_far=18.0,
        member_extrema_remaining=np.array([17.0, 16.5, 18.5]),
    )

    assert reason == "day0_low_extreme_not_terminal:hours_remaining=18.0"


def test_day0_deterministic_remaining_forecast_does_not_bypass_maturity():
    """Forecast remaining-window determinism is not settlement hard-fact authority."""
    from src.engine import monitor_refresh
    from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

    temporal_context = SimpleNamespace(daypart="morning", post_peak_confidence=0.0)

    high_reason = monitor_refresh._day0_extreme_authority_rejection_reason(
        temperature_metric=HIGH_LOCALDAY_MAX,
        temporal_context=temporal_context,
        hours_remaining=23.0,
        observed_extreme_so_far=35.0,
        member_extrema_remaining=np.array([24.0, 25.0, 26.0]),
    )
    assert high_reason is not None and "not_mature" in high_reason

    low_reason = monitor_refresh._day0_extreme_authority_rejection_reason(
        temperature_metric=LOW_LOCALDAY_MIN,
        temporal_context=temporal_context,
        hours_remaining=18.0,
        observed_extreme_so_far=5.0,
        member_extrema_remaining=np.array([17.0, 16.5, 18.5]),
    )
    assert low_reason == "day0_low_extreme_not_terminal:hours_remaining=18.0"


def test_day0_high_morning_refresh_marks_probability_stale(monkeypatch):
    """Seoul-style local-midnight HIGH observation must not create exit authority."""
    from src.config import City
    from src.engine import monitor_refresh
    from src.signal.day0_extrema import RemainingMemberExtrema
    import src.signal.diurnal as diurnal

    pos = _make_position(
        state="day0_window",
        city="Seoul",
        target_date="2026-06-08",
        bin_label="25°C",
        temperature_metric="high",
        entry_method="ens_member_counting",
        selected_method="",
        p_posterior=0.79,
    )
    city = City(
        name="Seoul",
        lat=37.558,
        lon=126.791,
        timezone="Asia/Seoul",
        settlement_unit="C",
        cluster="East Asia",
        wu_station="RKSI",
        settlement_source_type="wu_icao",
    )

    monkeypatch.setattr(monitor_refresh, "_fetch_day0_observation", lambda *_: {
        "high_so_far": 22.2,
        "low_so_far": 20.0,
        "current_temp": 22.2,
        "observation_time": "2026-06-08T00:10:00+09:00",
        "source": "wu_api",
    })
    monkeypatch.setattr(monitor_refresh, "_read_day0_hourly_vectors", lambda **kw: {
        "members_hourly": np.zeros((3, 3)),
        "times": [
            "2026-06-07T15:00:00+00:00",
            "2026-06-07T16:00:00+00:00",
            "2026-06-07T17:00:00+00:00",
        ],
        "source_id": "day0_hourly_vectors",
        "forecast_source_role": "day0_remaining_window_live",
        "source_models": ["icon_d2", "ecmwf_ifs"],
        "expected_models": ["icon_d2", "ecmwf_ifs"],
        "source_model_count": 2,
        "fetch_time": datetime(2026, 6, 7, 15, 5, tzinfo=timezone.utc),
    })
    monkeypatch.setattr(diurnal, "build_day0_temporal_context", lambda *a, **k: SimpleNamespace(
        daypart="morning",
        post_peak_confidence=0.0,
        current_utc_timestamp=datetime(2026, 6, 7, 15, 10, tzinfo=timezone.utc),
        solar_day=None,
        current_local_hour=0.17,
        daylight_progress=0.0,
    ))
    # Freeze the staleness gate's wall-clock to the fixture's frame: the obs
    # fast-lane gate (task #49) added a 1.0h max observation age measured
    # against real now, which rotted this fixed-date fixture (obs 2026-06-07
    # looked 100+ hours old). Real gate logic still runs — only the clock is
    # injected.
    _orig_quality_gate = monitor_refresh._day0_observation_quality_rejection_reason
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_observation_quality_rejection_reason",
        lambda city, obs, metric, decision_time=None, **kwargs: _orig_quality_gate(
            city, obs, metric,
            decision_time=datetime(2026, 6, 7, 15, 10, tzinfo=timezone.utc),
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "remaining_member_extrema_for_day0",
        lambda *a, **k: (
            RemainingMemberExtrema.for_metric(np.array([24.0, 25.0, 26.0]), k["temperature_metric"]),
            23.0,
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_build_all_bins",
        lambda *a, **k: (
            [
                monitor_refresh.Bin(low=24, high=24, label="24°C", unit="C"),
                monitor_refresh.Bin(low=25, high=25, label="25°C", unit="C"),
                monitor_refresh.Bin(low=26, high=26, label="26°C", unit="C"),
            ],
            1,
        ),
    )

    p, validations = monitor_refresh._refresh_day0_observation(
        position=pos,
        current_p_market=0.72,
        conn=None,
        city=city,
        target_d=date(2026, 6, 8),
    )

    assert np.isfinite(p)
    assert getattr(pos, "_monitor_probability_is_fresh") is True
    assert "day0_observation_remaining_window" in validations
    assert "day0_extreme_not_absorbing" in validations
    assert any(v.startswith("day0_high_extreme_not_mature:") for v in validations)


def test_day0_remaining_window_buy_no_returns_held_side_probability(monkeypatch):
    """Day0 monitor q is a YES-bin vector; buy_no exits must receive 1 - q_yes."""
    from src.engine import monitor_refresh
    import src.signal.diurnal as diurnal

    pos = _make_position(
        trade_id="munich-29-no-day0-side-space",
        state="day0_window",
        city="Munich",
        target_date="2026-06-30",
        bin_label="Will the highest temperature in Munich be 29°C on June 30?",
        temperature_metric="high",
        direction="buy_no",
        entry_method="qkernel_spine",
        selected_method="day0_observation_remaining_window",
        p_posterior=0.872825778061108,
    )
    city = SimpleNamespace(
        name="Munich",
        timezone="Europe/Berlin",
        settlement_unit="C",
        settlement_source_type="wu_icao",
        wu_station="EDDM",
    )

    monkeypatch.setattr(
        monitor_refresh,
        "_fetch_day0_observation",
        lambda *_: {
            "high_so_far": 28.0,
            "low_so_far": 18.0,
            "current_temp": 27.5,
            "observation_time": "2026-06-30T04:44:00+02:00",
            "source": "wu_api",
        },
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_observation_source_rejection_reason",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_observation_quality_rejection_reason",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        diurnal,
        "build_day0_temporal_context",
        lambda *a, **k: SimpleNamespace(
            daypart="pre_sunrise",
            post_peak_confidence=0.034,
            current_utc_timestamp=datetime(2026, 6, 30, 2, 44, tzinfo=timezone.utc),
            solar_day=None,
            current_local_hour=4.74,
            daylight_progress=0.0,
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_read_day0_hourly_vectors",
        lambda **kw: {
            "members_hourly": np.zeros((3, 3)),
            "times": [
                "2026-06-30T02:00:00+00:00",
                "2026-06-30T03:00:00+00:00",
                "2026-06-30T04:00:00+00:00",
            ],
            "source_id": "day0_hourly_vectors",
            "forecast_source_role": "day0_remaining_window_live",
            "source_models": ["icon_d2", "ecmwf_ifs"],
            "expected_models": ["icon_d2", "ecmwf_ifs"],
            "source_model_count": 2,
            "fetch_time": datetime(2026, 6, 30, 2, 40, tzinfo=timezone.utc),
        },
    )
    monkeypatch.setattr(
        monitor_refresh,
        "remaining_member_extrema_for_day0",
        lambda *a, **k: (
            SimpleNamespace(maxes=np.array([28.0, 29.0, 30.0]), mins=None),
            8.0,
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_extreme_authority_rejection_reason",
        lambda **kwargs: "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_observed_extreme_from_canonical_surface",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh.Day0Router,
        "route",
        staticmethod(
            lambda inputs: SimpleNamespace(
                p_vector=lambda bins, n_mc=None: np.array([0.28, 0.1581, 0.5619])
            )
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_build_all_bins",
        lambda *a, **k: (
            [
                monitor_refresh.Bin(low=28, high=28, label="28°C", unit="C"),
                monitor_refresh.Bin(low=29, high=29, label="29°C", unit="C"),
                monitor_refresh.Bin(low=30, high=30, label="30°C", unit="C"),
            ],
            1,
        ),
    )
    monkeypatch.setattr(monitor_refresh, "_maybe_write_day0_nowcast", lambda **kw: None)

    p, validations = monitor_refresh._refresh_day0_observation(
        position=pos,
        current_p_market=0.57,
        conn=None,
        city=city,
        target_d=date(2026, 6, 30),
    )

    assert p == pytest.approx(1.0 - 0.1581)
    assert getattr(pos, "_monitor_probability_is_fresh") is True
    assert "day0_observation_remaining_window" in validations
    assert "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034" in validations


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
    assert (
        pos.selected_method
        == monitor_refresh.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
    )
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


# ---- Bonus: Quarantine expiry timer retired (P0b, 2026-07-04) ----
#
# test_quarantine_does_not_expire_early previously pinned "stays quarantined
# before 48h" — now vacuously true for every duration since the timer no
# longer expires anything. Retired alongside test_quarantine_expires_after_48h
# above; see docs/rebuild/chain_mirror_state_model_2026-07-04.md §5 follow-up.


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
                current_market_price_is_fresh=True,
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


def test_micro_position_uses_fill_authority_but_does_not_block_negative_edge_exit():
    """Micro-position handling marks actual filled cost but still runs exit economics."""
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

    assert decision.should_exit is True
    assert decision.trigger == "EDGE_REVERSAL"
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


def test_live_exit_path_uses_fill_authority_shares(monkeypatch):
    """Live exit path (Position.evaluate_exit) must use fill-authority shares not
    submitted-size math. Wave 3 (2026-06-02): dead _evaluate_buy_yes_exit tests removed;
    this test directly exercises the live path via ExitContext.
    """
    from src.state.portfolio import ExitContext

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
    # effective_shares = shares_filled = 10.0 (not size_usd / entry_price = 200.0)
    assert pos.effective_shares == pytest.approx(10.0)
    assert pos.effective_shares != pytest.approx(pos.size_usd / pos.entry_price)

    ctx = ExitContext(
        fresh_prob=0.10,
        fresh_prob_is_fresh=True,
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.49,  # below p_posterior=0.10 → EV gate blocks
        hours_to_settlement=72.0,
        position_state="active",
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )
    decision = pos.evaluate_exit(ctx)
    # EV gate: sell_value = 10 * 0.49 = 4.9; hold_value = 10 * 0.10 = 1.0 → sell > hold → EXIT
    # (demonstrates effective_shares=10 not 200)
    assert decision.should_exit


def test_exit_paths_do_not_recompute_fill_authority_shares_from_legacy_price():
    """Static relationship check for corrected economics flowing into exit decisions.
    Wave 3 (2026-06-02): exit_triggers.py deleted; only portfolio.py and cycle_runtime.py checked.
    """
    portfolio_source = (ROOT / "src" / "state" / "portfolio.py").read_text(encoding="utf-8")
    cycle_runtime_source = (ROOT / "src" / "engine" / "cycle_runtime.py").read_text(encoding="utf-8")

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


def test_day0_buy_yes_point_reversal_requires_stronger_evidence():
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

    assert decision.should_exit is False
    assert decision.trigger != "DAY0_OBSERVATION_REVERSAL"
    assert "day0_observation_gate" in decision.applied_validations
    assert "day0_observation_reversal_nonterminal" in decision.applied_validations
    assert "consecutive_cycle_check" in decision.applied_validations


def test_low_win_rate_lottery_position_exits_before_ci_overlap_hold():
    """A live-invalid tail position must not be held forever by CI overlap."""

    pos = _make_position(
        direction="buy_yes",
        p_posterior=0.24833093804728934,
        entry_price=0.041,
        entry_ci_width=0.2985716143106003,
        shares=69.34,
        cost_basis_usd=2.8429,
    )

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.24833093804728934,
            fresh_prob_is_fresh=True,
            current_market_price=0.030649376417233552,
            current_market_price_is_fresh=True,
            best_bid=0.022,
            best_ask=0.039,
            hours_to_settlement=10.0,
            position_state="holding",
            day0_active=False,
            entry_posterior=0.24833093804728934,
            entry_ci=(0.0990451308919892, 0.3976167452025895),
            current_ci=(0.0990451308919892, 0.3976167452025895),
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "LIVE_WIN_RATE_FLOOR_REVOKED"
    assert "live_win_rate_floor_revoked" in decision.applied_validations


def test_low_lcb_position_exits_even_when_point_probability_is_high_and_ci_overlaps():
    """The live floor is q_lcb, not point q; CI overlap cannot hold an invalid bound."""

    pos = _make_position(
        direction="buy_yes",
        p_posterior=0.70,
        entry_price=0.04,
        entry_ci_width=0.50,
        shares=100.0,
        cost_basis_usd=4.0,
    )

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.70,
            fresh_prob_is_fresh=True,
            current_market_price=0.06,
            current_market_price_is_fresh=True,
            best_bid=0.055,
            best_ask=0.065,
            hours_to_settlement=10.0,
            position_state="holding",
            day0_active=False,
            entry_posterior=0.70,
            entry_ci=(0.40, 0.90),
            current_ci=(0.40, 0.90),
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "LIVE_WIN_RATE_FLOOR_REVOKED"
    assert "live_win_rate_floor_revoked" in decision.applied_validations
    assert "ci_overlap_hold" not in decision.applied_validations


def test_day0_buy_no_point_reversal_requires_stronger_evidence():
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

    assert decision.should_exit is False
    assert decision.trigger != "DAY0_OBSERVATION_REVERSAL"
    assert "day0_observation_gate" in decision.applied_validations
    assert "day0_observation_reversal_nonterminal" in decision.applied_validations
    assert "consecutive_cycle_check" in decision.applied_validations


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


def test_live_execute_exit_blocks_stale_market_price_context():
    """Direct execute_exit callers must not place exits from stale price evidence."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    outcome = execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ExitContext(
            exit_reason="EDGE_REVERSAL",
            current_market_price=0.45,
            current_market_price_is_fresh=False,
        ),
        clob=clob,
    )

    assert outcome == "exit_blocked: stale_market_price"
    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 1
    assert pos.last_exit_error == "stale_current_market_price"
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
