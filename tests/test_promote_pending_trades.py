# Created: 2026-05-17
# Last reused or audited: 2026-05-18
# Authority basis: STRUCTURAL_PLAN.md v3 §2 PR-S2
"""Antibody tests for promote_pending_trades (Bug #2, PR-S2).

Tests cover: age-gate, idempotency, 404 skip, 429 batch abort, economic-close
relationship, and concurrent-write atomicity (CRITIC_FLAG-2).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

from src.execution.exit_lifecycle import (
    promote_pending_trades,
    FILL_STATUSES,
)
from src.state.db import init_schema
from src.state.venue_command_repo import append_trade_fact

# ---------------------------------------------------------------------------
# Time helpers — relative to real utcnow() so abandon-window filter
# (_PROMOTE_MAX_AGE_SECONDS=3600) doesn't exclude fixtures
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _old() -> datetime:
    """2 minutes old — above min-age gate (60s), inside abandon window (3600s)."""
    return _now() - timedelta(seconds=120)


def _young() -> datetime:
    """10 seconds old — below min-age gate (60s)."""
    return _now() - timedelta(seconds=10)


HASH_A = "a" * 64
HASH_B = "b" * 64


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    return c


def _seed_command(conn: sqlite3.Connection, command_id: str, venue_order_id: str) -> None:
    """Insert a minimal venue_commands row to satisfy venue_trade_facts FK."""
    conn.execute(
        """
        INSERT OR IGNORE INTO venue_commands
          (command_id, snapshot_id, envelope_id, position_id, decision_id,
           idempotency_key, intent_kind, market_id, token_id, side, size, price,
           venue_order_id, state, created_at, updated_at)
        VALUES (?, 'snap-test', 'env-test', 'pos-test', 'dec-test',
                ?, 'EXIT', 'market-test', 'tok-test', 'SELL', 6.0, 0.50,
                ?, 'SUBMITTED', ?, ?)
        """,
        (
            command_id,
            command_id + "-idem",
            venue_order_id,
            _old().isoformat(),
            _old().isoformat(),
        ),
    )
    conn.commit()


def _seed_matched_fact(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    command_id: str,
    venue_order_id: str,
    observed_at: datetime,
) -> int:
    return append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state="MATCHED",
        filled_size="6",
        fill_price="0.50",
        source="WS_USER",
        observed_at=observed_at,
        raw_payload_hash=HASH_A,
        raw_payload_json={"state": "MATCHED"},
    )


def _clob_returning(status: str, tx_hash: str = "0xabc") -> MagicMock:
    clob = MagicMock()
    clob.get_order.return_value = {
        "status": status,
        "transaction_hash": tx_hash,
        "last_update": _now().isoformat(),
        "size": "6",
        "price": "0.50",
    }
    return clob


def _count_facts(conn: sqlite3.Connection, trade_id: str, state: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id=? AND state=?",
        (trade_id, state),
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# Test 1: matched row aged >60s is promoted to CONFIRMED
# ---------------------------------------------------------------------------

def test_matched_row_aged_over_60s_promoted_to_confirmed():
    conn = _conn()
    _seed_command(conn, "cmd-1", "ord-1")
    _seed_matched_fact(
        conn,
        trade_id="trade-1",
        command_id="cmd-1",
        venue_order_id="ord-1",
        observed_at=_old(),
    )

    clob = _clob_returning("CONFIRMED", tx_hash="0xdeadbeef")
    stats = promote_pending_trades(conn, clob, max_age_seconds=60)

    assert stats["promoted"] == 1
    assert stats["polled"] == 1
    assert _count_facts(conn, "trade-1", "CONFIRMED") == 1, (
        "Expected exactly one CONFIRMED fact after promotion"
    )

    row = conn.execute(
        "SELECT tx_hash, source FROM venue_trade_facts WHERE trade_id='trade-1' AND state='CONFIRMED'"
    ).fetchone()
    assert row["tx_hash"] == "0xdeadbeef"
    assert row["source"] == "REST"


def test_matched_row_promotes_from_order_state_payload():
    conn = _conn()
    _seed_command(conn, "cmd-state", "ord-state")
    _seed_matched_fact(
        conn,
        trade_id="trade-state",
        command_id="cmd-state",
        venue_order_id="ord-state",
        observed_at=_old(),
    )
    clob = MagicMock()
    clob.get_order.return_value = SimpleNamespace(
        order_id="ord-state",
        status="CONFIRMED",
        raw={
            "orderID": "ord-state",
            "status": "CONFIRMED",
            "transaction_hash": "0xorderstate",
            "last_update": _now().isoformat(),
            "size": "6",
            "price": "0.50",
        },
    )

    stats = promote_pending_trades(conn, clob, max_age_seconds=60)

    assert stats["promoted"] == 1
    row = conn.execute(
        "SELECT tx_hash, filled_size, fill_price FROM venue_trade_facts "
        "WHERE trade_id='trade-state' AND state='CONFIRMED'"
    ).fetchone()
    assert dict(row) == {
        "tx_hash": "0xorderstate",
        "filled_size": "6",
        "fill_price": "0.50",
    }


# ---------------------------------------------------------------------------
# Test 2: young row (below age gate) is NOT polled
# ---------------------------------------------------------------------------

def test_young_matched_row_not_polled():
    conn = _conn()
    _seed_command(conn, "cmd-2", "ord-2")
    _seed_matched_fact(
        conn,
        trade_id="trade-2",
        command_id="cmd-2",
        venue_order_id="ord-2",
        observed_at=_young(),
    )

    clob = _clob_returning("CONFIRMED")
    stats = promote_pending_trades(conn, clob, max_age_seconds=60)

    assert stats["polled"] == 0, "Young row should not be polled"
    clob.get_order.assert_not_called()
    assert _count_facts(conn, "trade-2", "CONFIRMED") == 0


# ---------------------------------------------------------------------------
# Test 3: already-CONFIRMED command is not re-polled (idempotency)
# ---------------------------------------------------------------------------

def test_already_confirmed_row_not_repolled():
    conn = _conn()
    old_ts = _old()
    _seed_command(conn, "cmd-3", "ord-3")
    _seed_matched_fact(
        conn,
        trade_id="trade-3",
        command_id="cmd-3",
        venue_order_id="ord-3",
        observed_at=old_ts,
    )
    # Seed already-CONFIRMED sibling for the same command
    append_trade_fact(
        conn,
        trade_id="trade-3",
        venue_order_id="ord-3",
        command_id="cmd-3",
        state="CONFIRMED",
        filled_size="6",
        fill_price="0.50",
        tx_hash="0xalreadydone",
        source="WS_USER",
        observed_at=old_ts + timedelta(seconds=5),
        raw_payload_hash=HASH_B,
        raw_payload_json={"state": "CONFIRMED"},
    )

    clob = _clob_returning("CONFIRMED")
    stats = promote_pending_trades(conn, clob, max_age_seconds=60)

    assert stats["polled"] == 0, "Already-CONFIRMED command must not be polled"
    clob.get_order.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: 404 (get_order returns None) → no phantom CONFIRMED row written
# ---------------------------------------------------------------------------

def test_promoter_handles_404_without_writing_phantom():
    conn = _conn()
    _seed_command(conn, "cmd-4", "ord-4")
    _seed_matched_fact(
        conn,
        trade_id="trade-4",
        command_id="cmd-4",
        venue_order_id="ord-4",
        observed_at=_old(),
    )

    clob = MagicMock()
    clob.get_order.return_value = None  # 404

    stats = promote_pending_trades(conn, clob, max_age_seconds=60)

    assert stats["polled"] == 1
    assert stats["skipped"] == 1
    assert stats["promoted"] == 0
    assert _count_facts(conn, "trade-4", "CONFIRMED") == 0, "No phantom row on 404"


# ---------------------------------------------------------------------------
# Test 5: 429 aborts batch — remaining candidates unpolled
# ---------------------------------------------------------------------------

def test_429_aborts_batch_remaining_unpolled():
    conn = _conn()
    old_ts = _old()
    for i in ("5a", "5b"):
        _seed_command(conn, f"cmd-{i}", f"ord-{i}")
        _seed_matched_fact(
            conn,
            trade_id=f"trade-{i}",
            command_id=f"cmd-{i}",
            venue_order_id=f"ord-{i}",
            observed_at=old_ts,
        )

    # Use real httpx.HTTPStatusError shape — status code is on exc.response.status_code
    rate_exc = httpx.HTTPStatusError(
        "429 Too Many Requests",
        request=httpx.Request("GET", "https://clob.example.com/order/ord-5a"),
        response=httpx.Response(429),
    )
    clob = MagicMock()
    clob.get_order.side_effect = rate_exc

    stats = promote_pending_trades(conn, clob, max_age_seconds=60)

    assert stats["errors"] == 1
    assert stats["promoted"] == 0
    # Batch aborted after first 429
    assert clob.get_order.call_count == 1
    assert _count_facts(conn, "trade-5a", "CONFIRMED") == 0
    assert _count_facts(conn, "trade-5b", "CONFIRMED") == 0


# ---------------------------------------------------------------------------
# Test 6: promotion triggers economic close (relationship test)
# promote_pending_trades writes CONFIRMED fact → FILL_STATUSES gate can see it
# ---------------------------------------------------------------------------

def test_promotion_triggers_economic_close():
    """Relationship: promote_pending_trades writes a CONFIRMED fact that satisfies
    FILL_STATUSES, making the downstream exit-lifecycle economic-close path reachable."""
    conn = _conn()
    _seed_command(conn, "cmd-6", "ord-6")
    _seed_matched_fact(
        conn,
        trade_id="trade-6",
        command_id="cmd-6",
        venue_order_id="ord-6",
        observed_at=_old(),
    )

    clob = _clob_returning("CONFIRMED", tx_hash="0xclose")
    stats = promote_pending_trades(conn, clob, max_age_seconds=60)
    assert stats["promoted"] == 1

    row = conn.execute(
        "SELECT state, source FROM venue_trade_facts WHERE trade_id='trade-6' AND state='CONFIRMED'"
    ).fetchone()
    assert row is not None, "CONFIRMED row must exist after promotion"
    assert row["source"] == "REST"

    # Invariant: FILL_STATUSES contains CONFIRMED — this is the downstream gate
    assert "CONFIRMED" in FILL_STATUSES, (
        "FILL_STATUSES must contain CONFIRMED — drives economic close path"
    )


# ---------------------------------------------------------------------------
# Test 7: concurrent WS_USER ingest does not collide (CRITIC_FLAG-2 antibody)
#
# Two threads open separate connections to the same file DB and both try to
# promote the same MATCHED row. BEGIN IMMEDIATE + re-check guard must ensure
# exactly one CONFIRMED row is written.
# ---------------------------------------------------------------------------

def test_concurrent_ws_ingest_does_not_collide():
    """CRITIC_FLAG-2: SAVEPOINT re-check guard ensures only one CONFIRMED row per command."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        # Set up schema + seed data on file DB
        setup = sqlite3.connect(db_path)
        setup.row_factory = sqlite3.Row
        setup.execute("PRAGMA foreign_keys=ON")
        setup.execute("PRAGMA journal_mode=WAL")
        init_schema(setup)
        old_ts = _old()
        setup.execute(
            """
            INSERT OR IGNORE INTO venue_commands
              (command_id, snapshot_id, envelope_id, position_id, decision_id,
               idempotency_key, intent_kind, market_id, token_id, side, size, price,
               venue_order_id, state, created_at, updated_at)
            VALUES ('cmd-7', 'snap-test', 'env-test', 'pos-test', 'dec-test',
                    'idem-7', 'EXIT', 'market-test', 'tok-test', 'SELL', 6.0, 0.50,
                    'ord-7', 'SUBMITTED', ?, ?)
            """,
            (old_ts.isoformat(), old_ts.isoformat()),
        )
        setup.commit()
        append_trade_fact(
            setup,
            trade_id="trade-7",
            venue_order_id="ord-7",
            command_id="cmd-7",
            state="MATCHED",
            filled_size="6",
            fill_price="0.50",
            source="WS_USER",
            observed_at=old_ts,
            raw_payload_hash=HASH_A,
            raw_payload_json={"state": "MATCHED"},
        )
        setup.close()

        results: list[dict] = []
        errors: list[Exception] = []

        def _worker() -> None:
            c = sqlite3.connect(db_path, timeout=10.0)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA journal_mode=WAL")
            try:
                clob = _clob_returning("CONFIRMED", tx_hash="0xconcurrent")
                stats = promote_pending_trades(c, clob, max_age_seconds=60)
                results.append(stats)
            except Exception as exc:
                errors.append(exc)
            finally:
                c.close()

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not errors, f"Unexpected thread errors: {errors}"

        check = sqlite3.connect(db_path)
        count = check.execute(
            "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id='trade-7' AND state='CONFIRMED'"
        ).fetchone()[0]
        check.close()

        assert count == 1, (
            f"Concurrent promoters must produce exactly 1 CONFIRMED row, got {count}. "
            "BEGIN IMMEDIATE + re-check guard must prevent duplicate writes."
        )

        total_promoted = sum(r.get("promoted", 0) for r in results)
        assert total_promoted == 1, (
            f"Exactly one thread should report promoted=1, got total={total_promoted}"
        )

    finally:
        try:
            os.unlink(db_path)
        except Exception:
            pass


def test_sqlite_writer_lock_returns_skip_not_thread_error():
    """Relationship: external writer contention degrades to retry/skip, not exception."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    locker = None
    contender = None
    try:
        setup = sqlite3.connect(db_path)
        setup.row_factory = sqlite3.Row
        setup.execute("PRAGMA foreign_keys=ON")
        setup.execute("PRAGMA journal_mode=WAL")
        init_schema(setup)
        _seed_command(setup, "cmd-lock", "ord-lock")
        _seed_matched_fact(
            setup,
            trade_id="trade-lock",
            command_id="cmd-lock",
            venue_order_id="ord-lock",
            observed_at=_old(),
        )
        setup.close()

        locker = sqlite3.connect(db_path)
        locker.execute("PRAGMA journal_mode=WAL")
        locker.execute("BEGIN IMMEDIATE")
        locker.execute("CREATE TABLE IF NOT EXISTS _lock_holder (id INTEGER)")
        locker.execute("INSERT INTO _lock_holder VALUES (1)")

        contender = sqlite3.connect(db_path, timeout=0.01)
        contender.row_factory = sqlite3.Row
        contender.execute("PRAGMA foreign_keys=ON")
        contender.execute("PRAGMA journal_mode=WAL")

        stats = promote_pending_trades(
            contender,
            _clob_returning("CONFIRMED", tx_hash="0xlocked"),
            max_age_seconds=60,
        )

        assert stats["promoted"] == 0
        assert stats["skipped"] == 1
        assert _count_facts(contender, "trade-lock", "CONFIRMED") == 0
    finally:
        if locker is not None:
            try:
                locker.rollback()
            finally:
                locker.close()
        if contender is not None:
            contender.close()
        try:
            os.unlink(db_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 8: production-pattern antibody — promoter runs inside outer transaction
# (mirrors cycle_runner conn lifecycle: prior DML leaves conn.in_transaction=True)
#
# Meta-verify: revert _savepoint_atomic swap back to BEGIN IMMEDIATE and this
# test MUST fail with OperationalError "cannot start a transaction within a
# transaction". Restore → passes.
# ---------------------------------------------------------------------------

def test_promoter_runs_inside_outer_transaction_without_raising():
    """Critical fix R1: SAVEPOINT composes with outer implicit TX; BEGIN IMMEDIATE did not.

    Simulates cycle_runner pattern: prior DML (chain_sync, allocator) left the
    shared conn in_transaction=True before the promoter is called.
    """
    conn = _conn()
    _seed_command(conn, "cmd-8", "ord-8")
    _seed_matched_fact(
        conn,
        trade_id="trade-8",
        command_id="cmd-8",
        venue_order_id="ord-8",
        observed_at=_old(),
    )

    # Simulate cycle_runner prior DML: update a venue_commands row without committing.
    # This opens an implicit transaction and leaves conn.in_transaction=True.
    conn.execute(
        "UPDATE venue_commands SET updated_at=? WHERE command_id='cmd-8'",
        (_now().isoformat(),),
    )
    assert conn.in_transaction, (
        "Precondition: conn must be in_transaction=True before calling promote_pending_trades"
    )

    clob = _clob_returning("CONFIRMED", tx_hash="0xouter-tx")

    # This must NOT raise — SAVEPOINT composes with outer transaction.
    stats = promote_pending_trades(conn, clob, max_age_seconds=60)

    assert stats["promoted"] == 1, (
        f"Promoter must succeed inside outer transaction, got stats={stats}. "
        "If this fails with OperationalError, the SAVEPOINT fix was not applied."
    )
    assert _count_facts(conn, "trade-8", "CONFIRMED") == 1


# ---------------------------------------------------------------------------
# Test 9: recovery_mode=True bypasses abandon-window cutoff
# ---------------------------------------------------------------------------

def test_aged_out_trades_recoverable_via_recovery_mode():
    """recovery_mode=True must allow promotion of rows older than _PROMOTE_MAX_AGE_SECONDS.

    Antibody for bot review finding #3: without recovery_mode, aged-out rows are
    permanently excluded by the abandon-window cutoff. With recovery_mode=True,
    they must be eligible.
    """
    conn = _conn()
    _seed_command(conn, "cmd-9", "ord-9")
    # Seed a fact that is far older than the 3600s abandon window
    ancient_ts = _now() - timedelta(seconds=7200)  # 2 hours old
    _seed_matched_fact(
        conn,
        trade_id="trade-9",
        command_id="cmd-9",
        venue_order_id="ord-9",
        observed_at=ancient_ts,
    )

    clob = _clob_returning("CONFIRMED", tx_hash="0xrecovered")

    # Without recovery_mode: should be excluded (observed_at > cutoff_abandon fails)
    stats_normal = promote_pending_trades(conn, clob, max_age_seconds=60, recovery_mode=False)
    assert stats_normal["promoted"] == 0, (
        "Ancient row must NOT be promoted in normal mode (abandon-window enforced)"
    )

    # With recovery_mode=True: abandon-window bypassed, should be promoted
    stats_recovery = promote_pending_trades(conn, clob, max_age_seconds=60, recovery_mode=True)
    assert stats_recovery["promoted"] == 1, (
        "Ancient row MUST be promoted in recovery_mode=True (abandon-window bypassed)"
    )
    assert _count_facts(conn, "trade-9", "CONFIRMED") == 1


# ---------------------------------------------------------------------------
# Test 10: ENTRY-intent commands are excluded from promotion (EXIT-only scope)
# ---------------------------------------------------------------------------

def _seed_entry_command(conn: sqlite3.Connection, command_id: str, venue_order_id: str) -> None:
    """Seed a venue_commands row with intent_kind='ENTRY' to test exclusion."""
    conn.execute(
        """
        INSERT OR IGNORE INTO venue_commands
          (command_id, snapshot_id, envelope_id, position_id, decision_id,
           idempotency_key, intent_kind, market_id, token_id, side, size, price,
           venue_order_id, state, created_at, updated_at)
        VALUES (?, 'snap-test', 'env-test', 'pos-test', 'dec-test',
                ?, 'ENTRY', 'market-test', 'tok-test', 'BUY', 6.0, 0.50,
                ?, 'SUBMITTED', ?, ?)
        """,
        (
            command_id,
            command_id + "-idem",
            venue_order_id,
            _old().isoformat(),
            _old().isoformat(),
        ),
    )
    conn.commit()


def test_promoter_skips_entry_trade_facts():
    """Antibody for bot review finding #4: promoter must only touch EXIT-intent commands.

    An ENTRY-intent command with a MATCHED fact (aged >60s) must NOT be promoted.
    This prevents early promotion of live entry orders that haven't settled yet.
    """
    conn = _conn()
    _seed_entry_command(conn, "cmd-10", "ord-10")
    _seed_matched_fact(
        conn,
        trade_id="trade-10",
        command_id="cmd-10",
        venue_order_id="ord-10",
        observed_at=_old(),
    )

    clob = _clob_returning("CONFIRMED", tx_hash="0xentry-tx")
    stats = promote_pending_trades(conn, clob, max_age_seconds=60)

    assert stats["polled"] == 0, (
        "ENTRY-intent commands must NOT be polled by promote_pending_trades"
    )
    assert stats["promoted"] == 0
    clob.get_order.assert_not_called()
    assert _count_facts(conn, "trade-10", "CONFIRMED") == 0, (
        "No CONFIRMED row must be written for an ENTRY-intent command"
    )
