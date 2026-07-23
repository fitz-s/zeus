# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: Fitz #5 "database is locked" CATEGORY-kill, HOLDER side. Live
#   evidence (zeus-live.err / zeus-live.log 2026-06-08 09:43-09:52): the EDLI
#   market-substrate warm cycle, log_trade_exit ("Failed to log trade exit"), and
#   the CollateralLedger heartbeat ALL fail "database is locked" at the SAME
#   timestamps, for the FULL ~26s snapshot budget (attempted=11, inserted=0,
#   failed=11, budget_exhausted=1). The substrate writer's own per-row commit
#   (commit 8186444948) and a 2s busy_timeout cannot help because a DIFFERENT
#   trade-DB writer holds the single WAL write lock LONGER than any busy_timeout.
#
#   ROOT (this file's antibody target): the EDLI reactor opens ONE trade-DB
#   connection per cycle (main.py:5231 get_trade_connection_with_world_required)
#   and hands it to event_bound_live_adapter_from_trade_conn. The adapter's
#   _submit closure reads trade_conn (opening sqlite3 isolation_level="" implicit
#   DEFERRED txn) and the live-order build writes trade_conn rows inside a
#   SAVEPOINT that is RELEASED but NEVER committed. trade_conn is committed
#   NOWHERE in process_pending — only trade_conn.close() at main.py:5443, at
#   cycle end. So across the WHOLE multi-event reactor cycle (each event doing a
#   venue HTTP POST inside executor_submit) the trade-DB write lock / WAL
#   read-mark is held continuously, starving every other trade-DB writer.
"""Relationship antibody: the EDLI live submit adapter RELEASES the trade-DB
lock per event so concurrent trade-DB writers are never starved.

CROSS-MODULE INVARIANT (the relationship, not a function):
  When the EDLI reactor processes a batch of events through the live submit
  adapter (event_bound_live_adapter_from_trade_conn), each call to the adapter's
  _submit MUST leave NO open transaction on the shared trade connection when it
  returns — i.e. it MUST commit (or otherwise close) the trade_conn transaction
  per event. Otherwise the first event's implicit read/write transaction pins the
  trade-DB WAL write lock / read-mark for the ENTIRE cycle (including every later
  event's venue HTTP POST), and a concurrent trade-DB writer (the substrate warm
  cycle, log_trade_exit, the CollateralLedger heartbeat) blocks out its
  busy_timeout and records "database is locked".

  The boundary that loses semantics in the bug: the reactor (events/reactor.py)
  splits and commits its WORLD-DB write units per event around the network submit
  (Window A / Window B). But the TRADE connection handed into the injected
  self._submit is NOT managed by the reactor — the adapter must release it itself.
  The world DB got the full per-event commit discipline; the trade DB did not.

TEST: feed the adapter's _submit TWO sequential events on a real file-backed
trade DB. After EACH _submit returns, an INDEPENDENT trade-DB connection must be
able to take BEGIN IMMEDIATE (the WAL write lock) immediately — proving the
adapter left no open trade-DB transaction. On pre-fix code the first event's
implicit transaction is still open, so the independent writer blocks/raises and
the assertion FAILS.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

import src.engine.event_reactor_adapter as adapter_mod
from src.engine.event_reactor_adapter import event_bound_live_adapter_from_trade_conn


_NOW = datetime(2026, 6, 8, 14, 0, 0, tzinfo=timezone.utc)


def _create_trade_db(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS adapter_probe (id INTEGER PRIMARY KEY, v INTEGER);
        """
    )
    conn.commit()
    conn.close()


def _independent_writer_lock_is_free(db_path) -> bool:
    """True iff an INDEPENDENT trade-DB connection can take the WAL write lock NOW.

    Uses a short busy_timeout so a held lock surfaces as a fast, deterministic
    failure rather than a 30s hang. Acquires BEGIN IMMEDIATE then immediately
    ROLLBACKs — it writes nothing.
    """
    other = sqlite3.connect(str(db_path), timeout=30)
    try:
        other.execute("PRAGMA journal_mode=WAL")
        other.execute("PRAGMA busy_timeout = 300")
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            return True
        except sqlite3.OperationalError:
            return False
    finally:
        other.close()


class _FakeEvent:
    """Minimal OpportunityEvent stand-in for the adapter _submit signature."""

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        self.causal_snapshot_id = f"snap-{event_id}"
        self.event_type = "FORECAST_SNAPSHOT_READY"
