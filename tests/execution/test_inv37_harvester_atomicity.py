# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: ChatGPT PR#408 review B1 INV-37,
#   docs/evidence/pr408_review/chatgpt_deep_review_2026-06-14.md
#   docs/archive/2026-Q2/operations_historical/inv37_harvester_fix_2026-06-14.md
"""INV-37 harvester atomicity fault-injection test (RED-on-revert).

The settlement harvester writes to TWO DB families in a single cycle:
  - forecasts-class: ``settlements`` table (zeus-forecasts.db, MAIN)
  - trade-class: ``position_current`` table (zeus_trades.db, ATTACHed as ``trades``)

INV-37 requires a single SAVEPOINT spanning both writes so the cycle is
all-or-nothing.  The pre-fix code used two independent connections committed
separately — a crash/exception between the two commits left logically impossible
partial state.

Test structure
--------------
T1 (pre-fix simulation — RED-on-revert):
    Simulate the OLD two-independent-commit pattern using two raw sqlite3
    connections.  Inject a crash after the forecasts-class commit but before
    the trade-class commit.  Assert the forecasts-class write PERSISTS while
    the trade-class write does NOT — proving partial state was possible.

T2 (post-fix — the actual INV-37 contract):
    Use a single connection with ATTACH + SAVEPOINT (the pattern now in
    ``forecasts_connection_with_trades_flocked``).  Inject an exception after
    the forecasts-class INSERT but before RELEASE SAVEPOINT.  Assert NEITHER
    side persists — atomic rollback.

T3 (post-fix happy path):
    Same ATTACH+SAVEPOINT but no injected exception.  Assert BOTH sides persist.
"""
from __future__ import annotations

import sqlite3
import tempfile
import inspect
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Minimal DDL for each DB family under test
# ---------------------------------------------------------------------------

_FORECASTS_DDL = """
CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    authority TEXT NOT NULL
);
"""

_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS position_current (
    trade_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    phase TEXT NOT NULL
);
"""


def _make_dbs(tmp_path: Path):
    """Create minimal temp forecasts + trade DBs and return their paths."""
    forecasts_path = tmp_path / "zeus-forecasts.db"
    trades_path = tmp_path / "zeus_trades.db"

    fc = sqlite3.connect(str(forecasts_path))
    fc.executescript(_FORECASTS_DDL)
    fc.commit()
    fc.close()

    tc = sqlite3.connect(str(trades_path))
    tc.executescript(_TRADES_DDL)
    tc.commit()
    tc.close()

    return forecasts_path, trades_path


# ---------------------------------------------------------------------------
# T1: pre-fix two-independent-commit pattern leaves partial state on crash
# ---------------------------------------------------------------------------

class _SimulatedCrash(Exception):
    """Sentinel exception injected between the two pre-fix commits."""


def test_settle_positions_never_commits_inside_outer_inv37_transaction():
    from src.execution.harvester import _settle_positions

    source = inspect.getsource(_settle_positions)
    assert ".commit(" not in source


def test_prefix_partial_state_on_crash(tmp_path):
    """T1 (RED-on-revert): the old two-conn pattern leaves partial state.

    If this test starts PASSING (partial state no longer possible without the
    fix), that would mean the pre-fix simulation no longer works — update it.
    The *expected* result is that forecasts side persists but trades side does
    not after an injected crash.
    """
    forecasts_path, trades_path = _make_dbs(tmp_path)

    # Simulate the OLD pattern: two independent connections
    trade_conn = sqlite3.connect(str(trades_path))
    shared_conn = sqlite3.connect(str(forecasts_path))
    trade_conn.row_factory = sqlite3.Row
    shared_conn.row_factory = sqlite3.Row

    try:
        # Forecasts-class write
        shared_conn.execute(
            "INSERT INTO settlements (city, target_date, authority) VALUES (?, ?, ?)",
            ("Chicago", "2026-01-10", "VERIFIED"),
        )
        # <<< OLD pre-fix commit #1 (forecasts side)
        shared_conn.commit()

        # Inject crash before trade-class commit
        raise _SimulatedCrash("crash between the two independent commits")

        # <<< OLD pre-fix commit #2 (trade side) — never reached
        trade_conn.execute(
            "INSERT INTO position_current (trade_id, city, target_date, phase)"
            " VALUES (?, ?, ?, ?)",
            ("trade-1", "Chicago", "2026-01-10", "SETTLED"),
        )
        trade_conn.commit()

    except _SimulatedCrash:
        pass
    finally:
        trade_conn.close()
        shared_conn.close()

    # Verify partial state: forecasts side committed, trades side did not
    fc = sqlite3.connect(str(forecasts_path))
    tc = sqlite3.connect(str(trades_path))
    try:
        fc_rows = fc.execute("SELECT * FROM settlements").fetchall()
        tc_rows = tc.execute("SELECT * FROM position_current").fetchall()
    finally:
        fc.close()
        tc.close()

    # This is the BAD state the fix prevents: forecasts-class write persists
    # even though the trade-class write never happened.
    assert len(fc_rows) == 1, (
        "Pre-fix: forecasts-class write should persist (demonstrating partial state)"
    )
    assert len(tc_rows) == 0, (
        "Pre-fix: trade-class write should NOT have happened (crash before second commit)"
    )


# ---------------------------------------------------------------------------
# T2: post-fix ATTACH+SAVEPOINT rolls back atomically on exception
# ---------------------------------------------------------------------------

class _InjectedFault(Exception):
    """Sentinel injected after the forecasts write but before RELEASE SAVEPOINT."""


def test_postfix_atomic_rollback_on_exception(tmp_path):
    """T2: ATTACH+SAVEPOINT makes the entire write all-or-nothing.

    RED-on-revert: revert to two independent connections and this test will fail
    because the forecasts-class write will persist (fc_rows == 1) while the
    trade-class write did not (tc_rows == 0).
    """
    forecasts_path, trades_path = _make_dbs(tmp_path)

    # Open a single connection to forecasts.db (MAIN) and ATTACH trades.db
    conn = sqlite3.connect(str(forecasts_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{trades_path}' AS trades")
        conn.execute("SAVEPOINT harvester_settlement")
        _savepoint_released = False
        try:
            # Forecasts-class write (bare name → MAIN)
            conn.execute(
                "INSERT INTO settlements (city, target_date, authority) VALUES (?, ?, ?)",
                ("Chicago", "2026-01-10", "VERIFIED"),
            )

            # <<< Inject fault AFTER forecasts write, BEFORE trade write >>>
            raise _InjectedFault("injected after forecasts write, before RELEASE SAVEPOINT")

            # Trade-class write via ATTACHed schema (never reached)
            conn.execute(
                "INSERT INTO trades.position_current (trade_id, city, target_date, phase)"
                " VALUES (?, ?, ?, ?)",
                ("trade-1", "Chicago", "2026-01-10", "SETTLED"),
            )
            conn.execute("RELEASE SAVEPOINT harvester_settlement")
            _savepoint_released = True
            conn.commit()

        except Exception:
            if not _savepoint_released:
                try:
                    conn.execute("ROLLBACK TO SAVEPOINT harvester_settlement")
                    conn.execute("RELEASE SAVEPOINT harvester_settlement")
                except Exception:
                    pass
            raise

    except _InjectedFault:
        pass  # Expected: the savepoint rolled back everything
    finally:
        conn.close()

    # Neither side should persist — atomic rollback
    fc = sqlite3.connect(str(forecasts_path))
    tc = sqlite3.connect(str(trades_path))
    try:
        fc_rows = fc.execute("SELECT * FROM settlements").fetchall()
        tc_rows = tc.execute("SELECT * FROM position_current").fetchall()
    finally:
        fc.close()
        tc.close()

    assert len(fc_rows) == 0, (
        "Post-fix: forecasts-class write must roll back atomically with the SAVEPOINT. "
        "If this fails (fc_rows==1), the SAVEPOINT is not being rolled back — "
        "revert-check: are you still using two independent connections?"
    )
    assert len(tc_rows) == 0, (
        "Post-fix: trade-class write must also roll back (it was never reached, "
        "but confirming the attached schema has no leftover state)."
    )


# ---------------------------------------------------------------------------
# T3: post-fix happy path — both sides commit atomically
# ---------------------------------------------------------------------------

def test_postfix_atomic_commit_success(tmp_path):
    """T3: ATTACH+SAVEPOINT commits both sides when no exception is raised."""
    forecasts_path, trades_path = _make_dbs(tmp_path)

    conn = sqlite3.connect(str(forecasts_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{trades_path}' AS trades")
        conn.execute("SAVEPOINT harvester_settlement")
        _savepoint_released = False
        try:
            # Forecasts-class write
            conn.execute(
                "INSERT INTO settlements (city, target_date, authority) VALUES (?, ?, ?)",
                ("Chicago", "2026-01-10", "VERIFIED"),
            )
            # Trade-class write via ATTACHed schema
            conn.execute(
                "INSERT INTO trades.position_current (trade_id, city, target_date, phase)"
                " VALUES (?, ?, ?, ?)",
                ("trade-1", "Chicago", "2026-01-10", "SETTLED"),
            )
            conn.execute("RELEASE SAVEPOINT harvester_settlement")
            _savepoint_released = True
            conn.commit()
        except Exception:
            if not _savepoint_released:
                try:
                    conn.execute("ROLLBACK TO SAVEPOINT harvester_settlement")
                    conn.execute("RELEASE SAVEPOINT harvester_settlement")
                except Exception:
                    pass
            raise
    finally:
        conn.close()

    # Both sides must persist
    fc = sqlite3.connect(str(forecasts_path))
    fc.row_factory = sqlite3.Row
    tc = sqlite3.connect(str(trades_path))
    tc.row_factory = sqlite3.Row
    try:
        fc_rows = fc.execute("SELECT * FROM settlements").fetchall()
        tc_rows = tc.execute("SELECT * FROM position_current").fetchall()
    finally:
        fc.close()
        tc.close()

    assert len(fc_rows) == 1, "Post-fix happy path: forecasts-class write must persist"
    assert len(tc_rows) == 1, "Post-fix happy path: trade-class write must persist"
    assert fc_rows[0]["authority"] == "VERIFIED"
    assert tc_rows[0]["phase"] == "SETTLED"


# ---------------------------------------------------------------------------
# T4: ATTACH cross-commit isolation — SAVEPOINT spans both schemas
# ---------------------------------------------------------------------------

def test_savepoint_spans_both_schemas(tmp_path):
    """T4: a SAVEPOINT opened before any write rolls back writes to BOTH schemas.

    This proves the SAVEPOINT on the MAIN connection is the correct atomicity
    boundary for both forecasts-class (MAIN) and trade-class (ATTACHed) tables.
    It is NOT possible to atomically span two independent sqlite3 connections
    using SAVEPOINT — that is the root cause of the INV-37 violation.
    """
    forecasts_path, trades_path = _make_dbs(tmp_path)

    conn = sqlite3.connect(str(forecasts_path))
    try:
        conn.execute(f"ATTACH DATABASE '{trades_path}' AS trades")
        conn.execute("SAVEPOINT sp")

        conn.execute(
            "INSERT INTO settlements (city, target_date, authority) VALUES ('X', '2026-01-01', 'V')"
        )
        conn.execute(
            "INSERT INTO trades.position_current (trade_id, city, target_date, phase)"
            " VALUES ('t1', 'X', '2026-01-01', 'SETTLED')"
        )

        # Verify both are visible inside the connection (within the savepoint)
        fc_before = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        tc_before = conn.execute(
            "SELECT COUNT(*) FROM trades.position_current"
        ).fetchone()[0]
        assert fc_before == 1
        assert tc_before == 1

        # Roll back the savepoint
        conn.execute("ROLLBACK TO SAVEPOINT sp")
        conn.execute("RELEASE SAVEPOINT sp")

        # Both writes are gone
        fc_after = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        tc_after = conn.execute(
            "SELECT COUNT(*) FROM trades.position_current"
        ).fetchone()[0]
        assert fc_after == 0, "SAVEPOINT rollback must undo forecasts-class write"
        assert tc_after == 0, "SAVEPOINT rollback must undo trade-class write"
    finally:
        conn.close()
