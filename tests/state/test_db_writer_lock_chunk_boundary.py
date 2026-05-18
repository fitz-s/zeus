# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/WAVE_2_PLAN.md §#37 F11
#   "BulkChunker LIVE chunk boundary observability"
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F11 antibody — assert that BulkChunker emits DB_CHUNK_BOUNDARY event
#   rows via event_writer when _yield_to_live() fires, and when the watchdog
#   fires. Also verifies the migration creates the table idempotently.
# Reuse: Run on every PR touching src/state/db_writer_lock.py BulkChunker
#   event_writer wiring or src/state/chunk_boundary_events.py.

"""F11 antibody: BulkChunker DB_CHUNK_BOUNDARY event emission.

Background (WAVE_2_PLAN §#37): BulkChunker LIVE-yield events were counter-only.
No queryable record existed. F11 adds event_writer callback to BulkChunker so
LIVE_CONTENDED yields and WATCHDOG fires emit rows into db_chunk_boundary_events.

Six probes:
1. LIVE_CONTENDED path: mock _is_live_contended=True, verify event_writer called
   with split_reason='LIVE_CONTENDED', duration_ms >= 0, and rows_processed == 123.
2. WATCHDOG path: configure short watchdog_s, sleep past it, verify event_writer
   called with split_reason='WATCHDOG' and rows_processed == 456.
3. emit_event integration: call emit_event() directly against a tmp DB, verify
   row visible to independent reader connection (confirms ensure_table + insert).
4. Migration idempotency: up() runs twice without error.
5. E2E: bulk_lock_with_chunker wires event_writer so a real db row appears after
   a LIVE_CONTENDED yield (rows_processed == 789).
6. emit_event WAL mode: journal_mode=WAL persists in the DB header after close.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.state.db_writer_lock import BulkChunker, bulk_lock_with_chunker
from src.state.chunk_boundary_events import emit_event, ensure_table


# ---------------------------------------------------------------------------
# Probe 1: LIVE_CONTENDED event emitted via event_writer
# ---------------------------------------------------------------------------

def test_live_contended_calls_event_writer(tmp_path: Path) -> None:
    """When _is_live_contended() returns True, event_writer is called with
    split_reason='LIVE_CONTENDED' and a non-negative duration_ms.

    The test mocks _is_live_contended to avoid needing a real fcntl fd.
    """
    db_path = tmp_path / "world.db"
    writer_conn = sqlite3.connect(str(db_path))
    writer_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    writer_conn.commit()

    calls: list[dict] = []

    def mock_event_writer(*, caller_module: str, split_reason: str,
                          duration_ms: int = 0, rows_processed: int = 0, **kw) -> None:
        calls.append({"caller_module": caller_module,
                      "split_reason": split_reason,
                      "duration_ms": duration_ms,
                      "rows_processed": rows_processed})

    chunker = BulkChunker(
        writer_conn,
        caller_module="test.f11.probe1",
        db_path=db_path,
        event_writer=mock_event_writer,
    )

    with patch.object(chunker, "_is_live_contended", return_value=True):
        with chunker:
            chunker.increment_rows(123)
            chunker.yield_if_live_contended()

    writer_conn.close()

    assert len(calls) == 1, (
        f"F11: expected 1 event_writer call for LIVE_CONTENDED yield; got {calls}"
    )
    assert calls[0]["split_reason"] == "LIVE_CONTENDED", (
        f"F11: split_reason must be LIVE_CONTENDED; got {calls[0]['split_reason']!r}"
    )
    assert calls[0]["rows_processed"] == 123, (
        f"F11: rows_processed must be 123; got {calls[0].get('rows_processed')}"
    )
    assert calls[0]["caller_module"] == "test.f11.probe1"
    assert calls[0]["duration_ms"] >= 0, (
        f"F11: duration_ms must be >= 0; got {calls[0]['duration_ms']}"
    )


# ---------------------------------------------------------------------------
# Probe 2: WATCHDOG event emitted via event_writer
# ---------------------------------------------------------------------------

def test_watchdog_calls_event_writer(tmp_path: Path) -> None:
    """When the watchdog fires, event_writer is called with split_reason='WATCHDOG'.

    The watchdog fires _thread.interrupt_main() which raises KeyboardInterrupt
    in the main (pytest) thread. We catch it inside the test to prevent it
    from aborting the suite while still asserting the event was emitted.
    """
    db_path = tmp_path / "world.db"
    writer_conn = sqlite3.connect(str(db_path))
    writer_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    writer_conn.commit()

    calls: list[dict] = []
    fired = threading.Event()

    def mock_event_writer(*, caller_module: str, split_reason: str,
                          duration_ms: int = 0, rows_processed: int = 0, **kw) -> None:
        calls.append({"caller_module": caller_module,
                      "split_reason": split_reason,
                      "duration_ms": duration_ms,
                      "rows_processed": rows_processed})
        fired.set()

    chunker = BulkChunker(
        writer_conn,
        caller_module="test.f11.probe2",
        watchdog_s=0,  # fires immediately on first poll
        watchdog_poll_s=0.02,
        event_writer=mock_event_writer,
    )

    try:
        with chunker:
            chunker.increment_rows(456)
            # Wait for event_writer to be called (fired event), or timeout.
            # The watchdog will also fire interrupt_main() shortly after.
            fired.wait(timeout=2.0)
            # Clear the abort so __exit__ does not re-raise.
            chunker._abort_requested.clear()
    except (KeyboardInterrupt, Exception):
        # interrupt_main() raises KeyboardInterrupt here — absorb it.
        # The event_writer call (fired.set()) happens BEFORE interrupt_main().
        pass
    finally:
        writer_conn.close()

    assert any(c["split_reason"] == "WATCHDOG" for c in calls), (
        f"F11: expected event_writer called with WATCHDOG; got {calls}"
    )
    watchdog_calls = [c for c in calls if c["split_reason"] == "WATCHDOG"]
    assert watchdog_calls[0]["caller_module"] == "test.f11.probe2"
    assert watchdog_calls[0]["rows_processed"] == 456, (
        f"F11: watchdog rows_processed must be 456; got {watchdog_calls[0].get('rows_processed')}"
    )
    assert watchdog_calls[0]["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Probe 3: emit_event integration — row visible to independent reader
# ---------------------------------------------------------------------------

def test_emit_event_row_visible_to_reader(tmp_path: Path) -> None:
    """emit_event() writes a row that is immediately visible to an
    independent reader connection (separate sqlite3.connect call).

    Verifies: ensure_table creates the table, insert succeeds, split_reason
    CHECK passes for all three valid values.
    """
    db_path = tmp_path / "world.db"

    # Emit one row for each valid split_reason.
    for reason in ("LIVE_CONTENDED", "WATCHDOG", "MANUAL"):
        emit_event(
            db_path,
            caller_module="test.f11.probe3",
            split_reason=reason,
            rows_processed=10,
            duration_ms=42,
        )

    # Independent reader verifies all 3 rows landed.
    reader = sqlite3.connect(str(db_path))
    try:
        rows = reader.execute(
            "SELECT split_reason, caller_module, rows_processed, duration_ms "
            "FROM db_chunk_boundary_events ORDER BY rowid"
        ).fetchall()
    finally:
        reader.close()

    assert len(rows) == 3, (
        f"F11: expected 3 rows from emit_event; got {len(rows)}: {rows}"
    )
    reasons = [r[0] for r in rows]
    assert reasons == ["LIVE_CONTENDED", "WATCHDOG", "MANUAL"], (
        f"F11: split_reason ordering wrong; got {reasons}"
    )
    for r in rows:
        assert r[1] == "test.f11.probe3"
        assert r[2] == 10
        assert r[3] == 42


# ---------------------------------------------------------------------------
# Probe 4: migration idempotency — up() runs twice without error
# ---------------------------------------------------------------------------

def test_migration_idempotent(tmp_path: Path) -> None:
    """202605_db_chunk_boundary_events.up() is safe to call twice.

    First call creates the table; second call detects it already exists and
    skips without error (the CREATE TABLE IF NOT EXISTS + _is_already_applied
    guard).
    """
    import importlib.util, sys
    from pathlib import Path as _Path
    _mig_path = _Path(__file__).parent.parent.parent / "scripts" / "migrations" / "202605_db_chunk_boundary_events.py"
    _spec = importlib.util.spec_from_file_location("_mig_chunk_boundary", str(_mig_path))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    up = _mod.up

    db_path = tmp_path / "world_idempotent.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # First call: creates the table.
        up(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='db_chunk_boundary_events'"
        ).fetchone()
        assert row is not None, "F11 migration: table should exist after first up()"

        # Second call: must not raise.
        up(conn)  # idempotent
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Probe 5 (E2E): bulk_lock_with_chunker → event_writer → real db row
# Antibody for CRITICAL Fix 1: exercises the wiring gap the critic flagged.
# Exercises bulk_lock_with_chunker (NOT BulkChunker directly) so the
# stage-1 antibody mask is broken.
# ---------------------------------------------------------------------------

def test_bulk_lock_with_chunker_emits_real_row(tmp_path: Path) -> None:
    """bulk_lock_with_chunker wires event_writer into BulkChunker so a real
    db_chunk_boundary_events row appears after a LIVE_CONTENDED yield.

    This probe is the critical antibody the critic flagged as missing: all prior
    probes injected event_writer=mock into BulkChunker directly; this one goes
    through bulk_lock_with_chunker, which is the sole production entry point.
    """
    write_db_path = tmp_path / "write.db"
    obs_db_path = tmp_path / "events.db"

    write_conn = sqlite3.connect(str(write_db_path))
    write_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    write_conn.commit()

    # event_writer closure: emit into obs_db_path (mirrors production closure
    # pattern of emit_event(db_path=ZEUS_WORLD_DB_PATH, **kw)).
    def _event_writer(**kw):
        emit_event(db_path=obs_db_path, **kw)

    with bulk_lock_with_chunker(
        write_db_path,
        write_conn,
        caller_module="test.f11.probe5.e2e",
        event_writer=_event_writer,
    ) as chunker:
        # Trigger LIVE_CONTENDED yield via mock on the returned chunker instance.
        with patch.object(chunker, "_is_live_contended", return_value=True):
            chunker.increment_rows(789)
            chunker.yield_if_live_contended()

    write_conn.close()

    # Verify row landed in obs_db_path via independent reader.
    reader = sqlite3.connect(str(obs_db_path))
    try:
        rows = reader.execute(
            "SELECT split_reason, caller_module, rows_processed FROM db_chunk_boundary_events"
        ).fetchall()
    finally:
        reader.close()

    assert len(rows) >= 1, (
        f"F11 E2E: expected ≥1 row in db_chunk_boundary_events via "
        f"bulk_lock_with_chunker; got {rows}"
    )
    assert rows[0][0] == "LIVE_CONTENDED", (
        f"F11 E2E: expected split_reason LIVE_CONTENDED; got {rows[0][0]!r}"
    )
    assert rows[0][1] == "test.f11.probe5.e2e", (
        f"F11 E2E: expected caller_module test.f11.probe5.e2e; got {rows[0][1]!r}"
    )
    assert rows[0][2] == 789, (
        f"F11 E2E: expected rows_processed 789; got {rows[0][2]}"
    )


# ---------------------------------------------------------------------------
# Probe 6: emit_event connection hygiene — WAL mode + timeout from env
# Antibody for Fix 3.
# ---------------------------------------------------------------------------

def test_emit_event_wal_mode_persists(tmp_path: Path) -> None:
    """emit_event applies WAL journal mode which persists in the DB header.

    After emit_event closes its connection, an independent reader sees
    journal_mode=wal — confirming the PRAGMA was applied.
    """
    db_path = tmp_path / "wal_check.db"
    emit_event(db_path, caller_module="test.f11.probe6.wal",
               split_reason="MANUAL", rows_processed=0, duration_ms=0)

    reader = sqlite3.connect(str(db_path))
    try:
        mode = reader.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        reader.close()

    assert mode == "wal", (
        f"F11 hygiene: emit_event must leave journal_mode=wal; got {mode!r}"
    )


def test_emit_event_timeout_from_env(tmp_path: Path, monkeypatch) -> None:
    """emit_event reads ZEUS_DB_BUSY_TIMEOUT_MS to set sqlite3.connect(timeout=).

    Monkey-patches sqlite3.connect to capture the timeout kwarg and asserts it
    matches the env var value converted to seconds.
    """
    import sqlite3 as _sqlite3

    db_path = tmp_path / "timeout_check.db"
    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "12345")

    captured_timeouts: list[float] = []
    _real_connect = _sqlite3.connect

    def _mock_connect(path, timeout=5.0, **kw):
        captured_timeouts.append(timeout)
        return _real_connect(path, timeout=timeout, **kw)

    import src.state.chunk_boundary_events as _cbe
    monkeypatch.setattr(_cbe.sqlite3, "connect", _mock_connect)

    emit_event(db_path, caller_module="test.f11.probe6.timeout",
               split_reason="MANUAL")

    assert len(captured_timeouts) >= 1, "F11 hygiene: sqlite3.connect never called"
    assert captured_timeouts[0] == pytest.approx(12.345, abs=0.001), (
        f"F11 hygiene: timeout should be 12.345s (ZEUS_DB_BUSY_TIMEOUT_MS=12345); "
        f"got {captured_timeouts[0]}"
    )
