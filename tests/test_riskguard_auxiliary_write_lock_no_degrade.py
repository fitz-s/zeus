# Created: 2026-06-13
# Last reused/audited: 2026-06-13
# Lifecycle: created=2026-06-13; last_reviewed=2026-06-13; last_reused=2026-06-13
# Authority basis: docs/evidence/no_order_root_2026-06-13/diagnosis.md (Blocker 1 —
#   RiskGuard persistent DATA_DEGRADED / dependency_db_locked). RELATIONSHIP test of
#   the cross-module invariant: a concurrent writer holding the zeus_trades WAL write
#   lock must NOT flip RiskGuard's computed risk LEVEL to DATA_DEGRADED when the
#   risk-level READS themselves succeed. The level is computed purely from reads; the
#   risk_actions / strategy_health writes are AUXILIARY bookkeeping. The live storm
#   (2026-06-13 01:00-06:57Z, 2113 RISK_GUARD_BLOCKED receipts / 17h) degraded the
#   GREEN-only entry gate because a bookkeeping write lost the WAL write lock. This is
#   the no-conn-across-IO / writer-contention class (9f70e9c581). Written BEFORE the
#   implementation was trusted; goes RED on revert of
#   _refresh_riskguard_auxiliary_bookkeeping.
"""Relationship tests: RiskGuard auxiliary bookkeeping write-lock MUST NOT degrade
the risk level.

The invariant under test is a CROSS-MODULE / CROSS-PROCESS property, not a single
function's output: when module A (a concurrent writer — the EDLI reactor or an
ingest job, in another process) holds the single zeus_trades WAL *write* lock, and
module B (RiskGuard's tick) goes to run its AUXILIARY bookkeeping writes
(``risk_actions`` / ``strategy_health``), the write loses the lock and raises
``"database is locked"``. The property that must hold across that boundary: the risk
LEVEL — computed entirely from the metric READS already gathered — must NOT degrade.
Only a genuine truth-READ failure may degrade (fail-closed, preserved elsewhere).
"""
import sqlite3

import pytest

from src.riskguard import riskguard


def _wal_db_with_risk_actions(db_path) -> None:
    """Create a real WAL-mode DB with a minimal canonical ``risk_actions`` table.

    Mirrors the architecture-kernel DDL (architecture/2026_04_02_architecture_kernel.sql)
    closely enough that ``_sync_riskguard_strategy_gate_actions`` performs a real
    INSERT — so the held WAL write lock is genuinely contended (not a stub).
    """
    setup = sqlite3.connect(str(db_path))
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_actions (
            action_id TEXT PRIMARY KEY,
            strategy_key TEXT NOT NULL,
            action_type TEXT NOT NULL,
            value TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            effective_until TEXT,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            precedence INTEGER NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    setup.commit()
    setup.close()


def _open_short_timeout_conn(db_path) -> sqlite3.Connection:
    """A WAL connection whose busy wait is short so a contended write FAILS FAST
    (matches the live tick's short per-attempt busy_timeout)."""
    conn = sqlite3.connect(str(db_path), timeout=0.2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 200")
    return conn


def test_auxiliary_write_lock_is_absorbed_not_degraded(tmp_path):
    """RELATIONSHIP (concurrent writer -> RiskGuard tick): with the zeus_trades WAL
    write lock held by another connection, the auxiliary bookkeeping refresh must
    NOT raise — it absorbs the lock and returns a ``skipped_dependency_lock`` status
    so the tick proceeds to compute/persist the level from the reads it already has.

    This is the exact live failure mode (2026-06-13): the bookkeeping write lost the
    WAL write lock and the old code let it bubble to DATA_DEGRADED. On revert of
    ``_refresh_riskguard_auxiliary_bookkeeping`` the unwrapped INSERT raises and this
    assertion goes RED (see the companion RED-on-revert control test below)."""
    db_path = tmp_path / "zeus_trades.db"
    _wal_db_with_risk_actions(db_path)

    # Module A: a concurrent writer holds the single WAL write lock.
    holder = sqlite3.connect(str(db_path))
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO risk_actions (action_id, strategy_key, action_type, value, "
        "issued_at, effective_until, reason, source, precedence, status) VALUES "
        "('holder', 'center_buy', 'gate', 'true', 'now', NULL, 'holding', 'system', 1, 'active')"
    )  # write lock now acquired and HELD (no commit)

    try:
        # Module B: RiskGuard's auxiliary bookkeeping over a separate short-wait conn.
        conn = _open_short_timeout_conn(db_path)
        try:
            durable, refresh, snapshot = riskguard._refresh_riskguard_auxiliary_bookkeeping(
                conn,
                recommended_strategy_gate_reasons={"center_buy": ["edge_compression"]},
                now="2026-06-13T06:00:00+00:00",
            )
        finally:
            conn.close()
    finally:
        holder.rollback()
        holder.close()

    # The bookkeeping lock was ABSORBED — the helper did not raise, and signals the
    # skip so the level computation (from the reads) proceeds undegraded.
    assert durable["status"] == "skipped_dependency_lock"
    assert refresh["status"] == "skipped_dependency_lock"
    assert snapshot["status"] == "skipped_dependency_lock"
    # The skip MUST NOT manufacture a degraded settlement-authority signal — that
    # would feed realized_degraded and is reserved for genuine truth gaps.
    assert refresh["settlement_authority_missing_tables"] == []


def test_unwrapped_auxiliary_write_raises_database_is_locked(tmp_path):
    """RED-ON-REVERT CONTROL: proves the lock the wrapper absorbs is REAL.

    Calling the auxiliary write directly (as the pre-fix _tick_once did inline) under
    the held WAL write lock raises ``OperationalError('database is locked')`` — the
    exact exception that propagated to the tick handler and produced the
    DATA_DEGRADED storm. If a future refactor removes the lock-tolerant wrapper, the
    INSERT once again raises here and the no-degrade invariant above breaks. This
    test pins that the contention is genuine, not a test artifact."""
    db_path = tmp_path / "zeus_trades.db"
    _wal_db_with_risk_actions(db_path)

    holder = sqlite3.connect(str(db_path))
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO risk_actions (action_id, strategy_key, action_type, value, "
        "issued_at, effective_until, reason, source, precedence, status) VALUES "
        "('holder', 'center_buy', 'gate', 'true', 'now', NULL, 'holding', 'system', 1, 'active')"
    )

    try:
        conn = _open_short_timeout_conn(db_path)
        try:
            with pytest.raises(sqlite3.OperationalError) as excinfo:
                riskguard._sync_riskguard_strategy_gate_actions(
                    conn,
                    {"center_buy": ["edge_compression"]},
                    issued_at="2026-06-13T06:00:00+00:00",
                )
            assert riskguard._is_sqlite_database_locked(excinfo.value)
        finally:
            conn.close()
    finally:
        holder.rollback()
        holder.close()


def test_non_lock_operationalerror_in_auxiliary_write_propagates(tmp_path):
    """A NON-lock OperationalError in the auxiliary bookkeeping (e.g. a genuine
    schema fault) must NOT be swallowed as a lock-skip — it propagates loudly so a
    real fault is never masked by the lock-tolerance path. Fail-loud is preserved."""
    db_path = tmp_path / "zeus_trades.db"
    _wal_db_with_risk_actions(db_path)
    conn = _open_short_timeout_conn(db_path)
    # The table EXISTS (so _table_exists passes and the INSERT is attempted) but is
    # missing a column the INSERT references → a genuine NON-lock OperationalError
    # ("no column named precedence"), NOT "database is locked".
    conn.execute("ALTER TABLE risk_actions DROP COLUMN precedence")
    conn.commit()
    try:
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            riskguard._refresh_riskguard_auxiliary_bookkeeping(
                conn,
                recommended_strategy_gate_reasons={"center_buy": ["edge_compression"]},
                now="2026-06-13T06:00:00+00:00",
            )
        # It must be a NON-lock error (proves the discriminator did not classify it
        # as a lock and swallow it).
        assert not riskguard._is_sqlite_database_locked(excinfo.value)
    finally:
        conn.close()
