# Created: 2026-05-12
# Last reused or audited: 2026-05-12
# Authority basis: architect K=3 structural decisions / AGENTS.md money path
#                  K3 — BulkChunker yields writer-lock to LIVE work at chunk
#                  boundary so LIVE trades do not wait for the entire bulk
#                  cycle.
"""K3 relationship test for BulkChunker LIVE-yield at chunk boundaries.

The cross-module invariant under test is:

  BulkChunker's bulk-fcntl + open-SQLite-transaction MUST NOT starve a
  LIVE writer for the duration of a multi-chunk bulk operation. A LIVE
  writer arriving mid-cycle must acquire its lock + complete its
  transaction within bounded latency (here: <= 2 s), not the full bulk
  duration.

This is a relationship test (Fitz Constraint #1): function-level tests
of `yield_if_live_contended` cannot see whether the chunker actually
makes room for LIVE work crossing the module boundary. Only a
two-process test that watches the LIVE process latency does.
"""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from src.state.db_writer_lock import (
    BulkChunker,
    WriteClass,
    bulk_lock_with_chunker,
    db_writer_lock,
)


# --------------------------------------------------------------------------
# Subprocess helpers
# --------------------------------------------------------------------------


def _live_writer_subprocess(
    db_path_str: str,
    start_event: Any,
    ready_event: Any,
    latency_q: Any,
) -> None:
    """LIVE-class writer: wait for start, then acquire LIVE flock + write.

    Records (acquire_latency_s, total_latency_s) on ``latency_q``.
    """
    from pathlib import Path as _P

    from src.state.db_writer_lock import WriteClass as _WC
    from src.state.db_writer_lock import db_writer_lock as _dwl

    db_path = _P(db_path_str)
    ready_event.set()
    start_event.wait(timeout=10.0)
    t0 = time.monotonic()
    with _dwl(db_path, _WC.LIVE):
        acquire_latency_s = time.monotonic() - t0
        # Simulate a tiny LIVE transaction against the same SQLite DB.
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO live_marker(ts) VALUES (?)", (time.time(),))
            conn.execute("COMMIT")
        finally:
            conn.close()
    total_latency_s = time.monotonic() - t0
    latency_q.put((acquire_latency_s, total_latency_s))


def _live_lock_holder_subprocess(
    db_path_str: str, ready_e: Any, release_e: Any
) -> None:
    """Hold LIVE fcntl until ``release_e`` fires (top-level for spawn pickling)."""
    from pathlib import Path as _P

    from src.state.db_writer_lock import WriteClass as _WC
    from src.state.db_writer_lock import db_writer_lock as _dwl

    with _dwl(_P(db_path_str), _WC.LIVE):
        ready_e.set()
        release_e.wait(timeout=5.0)


def _populate_schema(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bulk_rows ("
            "id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS live_marker ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# K3 relationship test — LIVE acquire latency bounded under BULK chunk loop
# --------------------------------------------------------------------------


def test_live_acquires_within_2s_during_bulk_chunk_loop(tmp_path: Path) -> None:
    """K3: LIVE writer slots in within <= 2s of a chunk-boundary yield.

    Topology:
      Main process: holds BULK fcntl, runs a long simulated chunk loop
        with ``bulk_lock_with_chunker`` (the K3 wiring). Each chunk takes
        ~200 ms of "work" then calls ``yield_if_live_contended()`` +
        ``commit_chunk()``.
      Subprocess: LIVE-class writer that acquires the LIVE fcntl AND
        opens a SQLite ``BEGIN IMMEDIATE`` transaction against the same
        DB.

    Without K3, the BULK side would hold the open SQLite write
    transaction for the entire chunk loop (~5 s here), so the LIVE
    BEGIN IMMEDIATE would block until commit. With K3, the chunker's
    commit + bulk-fcntl yield at each chunk boundary lets the LIVE
    writer in within 1 chunk-duration + jitter.

    Assertion: total LIVE latency <= 2.0 s (the bounded-latency promise).
    """
    db_path = tmp_path / "zeus-world.db"
    db_path.touch()
    _populate_schema(db_path)

    # The BULK side uses a real SQLite connection so commit_chunk()
    # genuinely releases SQLite's engine-level write lock.
    bulk_conn = sqlite3.connect(str(db_path), timeout=10.0)
    # autocommit-style: BEGIN/COMMIT explicit so commit_chunk semantics match.
    bulk_conn.isolation_level = None

    ctx = mp.get_context("spawn")
    start_event = ctx.Event()
    ready_event = ctx.Event()
    latency_q: Any = ctx.Queue()
    live_proc = ctx.Process(
        target=_live_writer_subprocess,
        args=(str(db_path), start_event, ready_event, latency_q),
    )
    live_proc.start()
    try:
        assert ready_event.wait(timeout=5.0), "LIVE writer failed to start"

        # Run the bulk chunk loop. Total work budget ~5 s (25 chunks × 200 ms).
        # Without K3, the LIVE writer would wait ~5 s for the BULK
        # transaction to commit at the end of the loop.
        with bulk_lock_with_chunker(
            db_path,
            bulk_conn,
            caller_module="test_k3_live_yield",
            watchdog_s=30,
            watchdog_poll_s=0.5,
            live_yield_sleep_s=0.05,
        ) as chunker:
            # Open a long-running BULK transaction.
            bulk_conn.execute("BEGIN IMMEDIATE")
            for i in range(25):
                # On the first chunk, signal the LIVE subprocess to GO.
                if i == 2:
                    start_event.set()
                # Simulate ~200 ms of bulk work (1k row inserts).
                bulk_conn.executemany(
                    "INSERT INTO bulk_rows(payload) VALUES (?)",
                    [(f"chunk{i}-row{j}",) for j in range(1_000)],
                )
                time.sleep(0.05)  # extra work to ensure each chunk is ~200ms
                # Chunk boundary: yield to LIVE if waiting, then commit.
                chunker.yield_if_live_contended()
                chunker.commit_chunk()
                # Start a fresh tx for the next chunk's work.
                bulk_conn.execute("BEGIN IMMEDIATE")
            # Final commit before exit.
            bulk_conn.execute("COMMIT")

        # Collect LIVE latency.
        try:
            acquire_latency_s, total_latency_s = latency_q.get(timeout=10.0)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"LIVE writer did not return latency: {e!r}")

        # Bounded-latency assertion: LIVE total < 2s.
        assert total_latency_s <= 2.0, (
            f"K3 broken: LIVE total latency {total_latency_s:.2f}s "
            f"exceeds 2.0s bound; chunker did not yield within a chunk "
            f"boundary (acquire_latency={acquire_latency_s:.2f}s)."
        )

        # Verify both LIVE and BULK actually wrote.
        verify_conn = sqlite3.connect(str(db_path))
        try:
            live_count = verify_conn.execute(
                "SELECT COUNT(*) FROM live_marker"
            ).fetchone()[0]
            bulk_count = verify_conn.execute(
                "SELECT COUNT(*) FROM bulk_rows"
            ).fetchone()[0]
        finally:
            verify_conn.close()
        assert live_count == 1, f"LIVE writer did not commit (count={live_count})"
        assert bulk_count == 25_000, (
            f"BULK writer did not commit all rows (count={bulk_count})"
        )
    finally:
        bulk_conn.close()
        live_proc.join(timeout=10.0)
        if live_proc.is_alive():
            live_proc.terminate()
            live_proc.join(timeout=2.0)


# --------------------------------------------------------------------------
# K3 unit-level: yield_if_live_contended is a no-op when LIVE is idle
# --------------------------------------------------------------------------


class _StubConn:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_yield_is_noop_when_live_idle(tmp_path: Path) -> None:
    """Without LIVE contention, yield_if_live_contended does NOT commit."""
    db_path = tmp_path / "zeus-world.db"
    db_path.touch()
    conn = _StubConn()
    with BulkChunker(
        conn,
        caller_module="test_yield_noop",
        watchdog_s=10,
        watchdog_poll_s=0.5,
        db_path=db_path,
    ) as chunker:
        # No LIVE caller holds .live → no commit.
        chunker.yield_if_live_contended()
        assert conn.commits == 0, (
            f"Idle yield committed unexpectedly (commits={conn.commits})"
        )


def test_yield_commits_when_live_held(tmp_path: Path) -> None:
    """When LIVE is held, yield_if_live_contended commits the current chunk."""
    db_path = tmp_path / "zeus-world.db"
    db_path.touch()
    conn = _StubConn()

    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()

    proc = ctx.Process(
        target=_live_lock_holder_subprocess,
        args=(str(db_path), ready, release),
    )
    proc.start()
    try:
        assert ready.wait(timeout=5.0), "LIVE holder did not start"
        with BulkChunker(
            conn,
            caller_module="test_yield_commits",
            watchdog_s=10,
            watchdog_poll_s=0.5,
            db_path=db_path,
            live_yield_sleep_s=0.01,
        ) as chunker:
            # LIVE is held → yield should commit the chunk.
            chunker.yield_if_live_contended()
        assert conn.commits >= 1, (
            f"Contended yield did not commit (commits={conn.commits})"
        )
    finally:
        release.set()
        proc.join(timeout=5.0)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2.0)


def test_yield_fence_suppresses_live_yield(tmp_path: Path) -> None:
    """Inside chunker.fence(...) atomicity wins — no LIVE yield."""
    db_path = tmp_path / "zeus-world.db"
    db_path.touch()
    conn = _StubConn()

    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()

    proc = ctx.Process(
        target=_live_lock_holder_subprocess,
        args=(str(db_path), ready, release),
    )
    proc.start()
    try:
        assert ready.wait(timeout=5.0), "LIVE holder did not start"
        with BulkChunker(
            conn,
            caller_module="test_fence_blocks_yield",
            watchdog_s=10,
            watchdog_poll_s=0.5,
            db_path=db_path,
            live_yield_sleep_s=0.01,
        ) as chunker:
            with chunker.fence("atomic_block"):
                # Even though LIVE is held, fence forbids yielding mid-block.
                chunker.yield_if_live_contended()
        assert conn.commits == 0, (
            f"Fence did not suppress LIVE yield (commits={conn.commits})"
        )
    finally:
        release.set()
        proc.join(timeout=5.0)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2.0)
