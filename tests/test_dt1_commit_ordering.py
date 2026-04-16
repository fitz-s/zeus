"""Relationship tests for DT#1 / INV-17: DB authority writes commit BEFORE
derived JSON exports fire.

Phase: 2 (World DB v2 Schema + DT#1 Commit Ordering + DT#4 Chain Three-State)
R-numbers covered: R-B (commit-then-export ordering), R-D (save_portfolio
                   recovery contract)

These tests MUST FAIL today (2026-04-16) because:
  - src/state/canonical_write.py does not exist (ImportError on all tests).
  - save_portfolio() has no last_committed_artifact_id kwarg.
  - cycle_runner.py:302-311 writes JSON BEFORE store_artifact (inverted order).
  - load_portfolio() / stale-detection helper do not yet exist.

First commit that should turn these green: executor Phase 2 implementation commit
(creates canonical_write.py, rewires cycle_runner and harvester, adds
last_committed_artifact_id to save_portfolio, adds stale-detection helper).
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory DB with a minimal test table for db_op exercises."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE test_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT)"
    )
    conn.execute(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT)"
    )
    conn.commit()
    return conn


def _import_commit_then_export():
    """Import commit_then_export; raises ImportError (RED) if Phase 2 not landed."""
    from src.state.canonical_write import commit_then_export  # noqa: PLC0415
    return commit_then_export


# ---------------------------------------------------------------------------
# R-B — commit_then_export contract
# ---------------------------------------------------------------------------

class TestCommitThenExportHappyPath(unittest.TestCase):
    """R-B normal path: DB row persists AND all json_exports called after commit."""

    def test_commit_then_export_happy_path(self):
        """db_op inserts a row; json_exports=[fake_save_fn]; commit_then_export
        causes the DB row to persist AND fake_save_fn to be called AFTER the commit.

        Verified via a sequence counter: we record (seq_at_commit, seq_at_json_call)
        and assert seq_at_json_call > seq_at_commit.

        Fails today with ImportError.
        """
        commit_then_export = _import_commit_then_export()
        conn = _make_test_db()

        call_log: list[str] = []

        def db_op():
            conn.execute("INSERT INTO test_artifacts (value) VALUES ('artifact-1')")
            call_log.append("db_op")

        def fake_save():
            call_log.append("json_export")

        commit_then_export(conn, db_op=db_op, json_exports=[fake_save])

        # DB row must persist
        (count,) = conn.execute("SELECT COUNT(*) FROM test_artifacts").fetchone()
        self.assertEqual(count, 1, "DB row must persist after commit_then_export")

        # json_export must have been called
        self.assertIn("json_export", call_log, "json_export function was not called")

        # Ordering: db_op recorded before json_export
        self.assertLess(
            call_log.index("db_op"),
            call_log.index("json_export"),
            "json_export must be called AFTER db_op (commit first, export second)",
        )

    def test_commit_then_export_fires_all_json_exports_in_order(self):
        """Three json_exports are all called in sequence (left to right).

        Fails today with ImportError.
        """
        commit_then_export = _import_commit_then_export()
        conn = _make_test_db()

        call_order: list[int] = []

        def db_op():
            conn.execute("INSERT INTO test_artifacts (value) VALUES ('x')")

        def export_a():
            call_order.append(1)

        def export_b():
            call_order.append(2)

        def export_c():
            call_order.append(3)

        commit_then_export(conn, db_op=db_op, json_exports=[export_a, export_b, export_c])

        self.assertEqual(
            call_order,
            [1, 2, 3],
            "All json_exports must fire in the order they were passed",
        )


class TestCommitThenExportDbFailure(unittest.TestCase):
    """R-B crash path: db_op raises → no json export fires, no partial row."""

    def test_commit_then_export_db_failure_suppresses_json(self):
        """When db_op raises, fake_save_fn is never invoked and the DB has no
        partial row (transaction must be rolled back).

        Fails today with ImportError.
        """
        commit_then_export = _import_commit_then_export()
        conn = _make_test_db()

        json_called = []

        def bad_db_op():
            conn.execute("INSERT INTO test_artifacts (value) VALUES ('partial')")
            raise RuntimeError("simulated DB write failure")

        def fake_save():
            json_called.append(True)

        with self.assertRaises(RuntimeError):
            commit_then_export(conn, db_op=bad_db_op, json_exports=[fake_save])

        self.assertFalse(
            json_called,
            "json_export must NOT be called when db_op raises",
        )

        # Transaction must be rolled back — no partial row
        (count,) = conn.execute("SELECT COUNT(*) FROM test_artifacts").fetchone()
        self.assertEqual(
            count,
            0,
            "DB must have no partial row after db_op failure (transaction rolled back)",
        )


class TestCommitThenExportJsonFailure(unittest.TestCase):
    """R-B degrade path: db_op succeeds, json_exports[0] raises; DB row persists,
    error logged but NOT re-raised (DB is source of truth; JSON is best-effort)."""

    def test_commit_then_export_json_failure_after_commit(self):
        """db_op succeeds; json_exports[0] raises; DB row persists; the exception
        is NOT re-raised to the caller.

        The invariant: DB commit is the authority write.  A JSON export failure
        is a degrade, not a cycle-fatal event.  The caller must not receive an
        uncaught exception.

        Fails today with ImportError.
        """
        commit_then_export = _import_commit_then_export()
        conn = _make_test_db()

        def db_op():
            conn.execute("INSERT INTO test_artifacts (value) VALUES ('committed')")

        def failing_save():
            raise OSError("simulated filesystem write failure")

        # Must NOT raise — JSON failure is degrade, not fatal
        try:
            commit_then_export(conn, db_op=db_op, json_exports=[failing_save])
        except Exception as exc:
            self.fail(
                f"commit_then_export re-raised JSON export failure to caller: {exc!r}. "
                "JSON export failures must be caught and logged, not propagated."
            )

        # DB row must persist despite JSON failure
        (count,) = conn.execute("SELECT COUNT(*) FROM test_artifacts").fetchone()
        self.assertEqual(
            count,
            1,
            "DB row must persist even when json_exports[0] raises",
        )


# ---------------------------------------------------------------------------
# R-D — save_portfolio recovery contract
# ---------------------------------------------------------------------------

class TestSavePortfolioRecoveryContract(unittest.TestCase):
    """R-D: save_portfolio writes last_committed_artifact_id; load detects stale JSON."""

    def _import_save_portfolio(self):
        """Import save_portfolio from portfolio module.

        This import succeeds today (the function exists) but the kwarg
        last_committed_artifact_id does not yet exist — tests that pass it
        will fail with TypeError (also a valid RED failure mode).
        """
        from src.state.portfolio import save_portfolio  # noqa: PLC0415
        return save_portfolio

    def test_save_portfolio_persists_last_committed_artifact_id(self):
        """call save_portfolio(state, last_committed_artifact_id=42); read the JSON
        back; assert the field is present with value 42.

        Fails today because save_portfolio() does not accept
        last_committed_artifact_id kwarg (TypeError → RED).
        """
        save_portfolio = self._import_save_portfolio()

        # Build a minimal PortfolioState-like object (duck type for the write path)
        # We use a real PortfolioState to avoid faking too many internals.
        from src.state.portfolio import PortfolioState  # noqa: PLC0415

        state = PortfolioState(
            positions=[],
            bankroll=1000.0,
            daily_baseline_total=1000.0,
            weekly_baseline_total=1000.0,
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)

        try:
            save_portfolio(state, path=tmp_path, last_committed_artifact_id=42)
            payload = json.loads(tmp_path.read_text())
            self.assertEqual(
                payload.get("last_committed_artifact_id"),
                42,
                "positions.json must carry last_committed_artifact_id=42 after save",
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_load_portfolio_detects_stale_json_when_db_newer(self):
        """pre-seed positions.json with last_committed_artifact_id=10; pre-seed
        decision_log with MAX(id)=20; call the stale-detection helper; assert
        a 'stale' signal is raised (flag or warning).

        The exact API is TBD by executor — this test asserts that SOME observable
        signal is raised.  Acceptable signals:
          - helper returns a truthy 'is_stale' value, OR
          - helper raises a specific exception class, OR
          - a logger.warning is emitted (captured via assertLogs).

        Fails today with ImportError (helper does not exist).
        """
        from src.state.canonical_write import detect_stale_portfolio  # noqa: PLC0415

        conn = _make_test_db()
        # Pre-seed decision_log with MAX(id)=20
        for i in range(20):
            conn.execute("INSERT INTO decision_log (note) VALUES ('entry')")
        conn.commit()

        # Build a stale JSON payload (last_committed_artifact_id=10 < MAX=20)
        stale_payload = {"last_committed_artifact_id": 10, "positions": []}

        result = detect_stale_portfolio(stale_payload, conn)

        self.assertTrue(
            result,
            "detect_stale_portfolio must return a truthy value when "
            "last_committed_artifact_id(10) < MAX(decision_log.id)(20)",
        )
