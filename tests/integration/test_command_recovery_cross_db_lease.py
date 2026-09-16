# Created: 2026-09-16
# Purpose: Lock the live-tick capital pass's cross-DB conn_factory to the raw
#   WORLD+TRADE factory, not the already-lease-taking conn_factory rebound at
#   reconcile_unresolved_commands's live-tick apply_deadline.
# Reuse: Run when command_recovery's live-tick capital fast lane or its
#   coordinator lease wiring changes.
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.test_command_recovery import (
    _advance_to_cancel_pending,
    _append_order_fact,
    _insert,
    _insert_decision_log_trade_case_for_recovery,
)


def test_live_tick_cross_db_capital_pass_takes_one_lease(tmp_path, monkeypatch):
    """terminal_order_facts_fast must not nest a second RECOVERY_CRITICAL lease.

    The pass is the only cross_db=True caller of _capital_apply_conn_factory.
    Its base connection factory must be the raw WORLD+TRADE factory captured
    before reconcile_unresolved_commands rebinds ``conn_factory`` to a
    lease-taking wrapper for the live-tick apply deadline. If it instead
    wraps that already-wrapped factory, the pass's own priority lease
    self-conflicts on the coordinator's turnstile/file locks (real flocks,
    same thread) and the pass defers as "DB contention" every tick.
    """
    from src.execution import command_recovery, venue_sync_contract
    from src.state import write_coordinator
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema, init_schema_trade_only
    from src.state.venue_command_repo import append_event
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    db_path = tmp_path / "live-tick-cross-db-lease.db"
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    init_schema(seed)
    init_schema_trade_only(seed)
    init_collateral_schema(seed)
    _insert(seed)
    _advance_to_cancel_pending(seed, venue_order_id="ord-001")
    append_event(
        seed,
        command_id="cmd-001",
        event_type="CANCEL_ACKED",
        occurred_at="2026-04-26T00:05:00Z",
        payload={"venue_order_id": "ord-001", "venue_status": "CANCELED"},
    )
    _append_order_fact(
        seed,
        order_id="ord-001",
        state="CANCEL_CONFIRMED",
        matched_size="0",
        remaining_size="10",
        source="REST",
    )
    _insert_decision_log_trade_case_for_recovery(seed)
    seed.commit()
    seed.close()

    def _conn_factory(*, blocking: bool = True, busy_timeout_ms: int | None = None):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        if busy_timeout_ms is not None:
            conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        return conn

    # Mirrors default_trade_conn_factory's real protocol: it is the ATTACH
    # WORLD+TRADE factory, so it requires the coordinator's writer flocks.
    _conn_factory.requires_writer_flocks = True
    _conn_factory.supports_nonblocking_flocks = True

    def _trade_only_factory(*, blocking: bool = True, busy_timeout_ms: int | None = None):
        return _conn_factory(blocking=blocking, busy_timeout_ms=busy_timeout_ms)

    _trade_only_factory.requires_writer_flocks = True
    _trade_only_factory.supports_nonblocking_flocks = True
    _conn_factory.trade_only_factory = _trade_only_factory

    def _read_factory():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
    monkeypatch.setattr(
        venue_sync_contract, "default_trade_read_conn_factory", _read_factory
    )

    # A REAL WriteCoordinator bound to a temp path: the decisive property.
    # Its turnstile/file locks are real flock()s on real fds, so a nested
    # lease attempt on the same DB from the same thread genuinely conflicts
    # instead of a stub silently granting both.
    coordinator = WriteCoordinator({DBIdentity.TRADE: db_path})
    monkeypatch.setattr(
        write_coordinator, "default_runtime_write_coordinator", lambda: coordinator
    )

    client = MagicMock(
        spec_set=["get_account_truth", "get_order", "get_open_orders", "get_trades"]
    )
    client.get_account_truth.return_value = SimpleNamespace(open_orders=[], trades=[])
    client.get_order.side_effect = pytest.fail
    client.get_open_orders.return_value = []
    client.get_trades.return_value = []

    summary = command_recovery.reconcile_unresolved_commands(
        client=client, scope="live_tick"
    )

    assert not summary.get("db_lock_deferred"), summary
    assert summary.get("db_lock_deferred_at") != "terminal_order_facts_fast", summary
    assert summary["terminal_order_facts"]["advanced"] >= 1, summary

    verified = _conn_factory()
    try:
        row = verified.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'cmd-001'"
        ).fetchone()
    finally:
        verified.close()
    assert row["state"] != "CANCEL_PENDING", dict(row)
