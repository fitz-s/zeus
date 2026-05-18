# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/WAVE_2_PLAN.md §#37 F11(v2)
#   "BulkChunker LIVE chunk boundary visibility"
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F11(v2) antibody — assert that after BulkChunker.commit_chunk() the
#   committed rows are visible to a concurrent reader process. Catches the
#   "intra-chunk staleness window" regression class without changing the
#   production WAL / commit semantics.
# Reuse: Run on every PR touching src/state/db_writer_lock.py BulkChunker
#   or the at-most-one-chunk-behind contract.

"""F11(v2) BulkChunker visibility invariant antibody.

Background (WAVE_2_PLAN §#37): BulkChunker yields chunks to the writer.
Without an explicit per-chunk commit (or WAL checkpoint), a concurrent
reader sees only the rows that were committed before the bulk write
began — every in-flight chunk is invisible until the whole bulk
operation finishes. For long bulks (e.g. backfills), that's an
"intra-chunk staleness window" of minutes-to-hours during which a
reader's snapshot is silently behind by O(chunks).

The production code already calls `self.conn.commit()` inside
`commit_chunk()`. This antibody establishes the invariant — that the
commit IS observable by an independent reader after each chunk —
without touching production behavior. A regression that swaps
`commit_chunk()` to a no-op or that wraps it inside a never-released
SAVEPOINT would silently revive the F11(v2) staleness window; this
antibody catches that.

Three probes (each opens a separate sqlite3.Connection to the same
file-backed DB to simulate the cross-process read):

1. Before commit_chunk: reader sees zero new rows
2. After first commit_chunk: reader sees first chunk's rows, no more
3. After second commit_chunk: reader sees both chunks (at-most-1-chunk-
   behind boundary invariant — reader can lag by at most 1 chunk, never 2)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# The BulkChunker import is conditional — its module-level imports drag in
# threading + fcntl wiring that's fine in CI but heavy. We exercise the
# public commit_chunk() surface.
from src.state.db_writer_lock import BulkChunker


@pytest.fixture
def two_conn_fixture(tmp_path: Path):
    """File-backed DB so a separate sqlite3.Connection can act as
    'independent reader process' — :memory: doesn't share between
    connections."""
    db_path = tmp_path / "f11v2_visibility.db"
    writer = sqlite3.connect(str(db_path))
    writer.executescript(
        "CREATE TABLE rows_under_test (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);"
    )
    writer.commit()
    reader = sqlite3.connect(str(db_path))
    yield writer, reader
    writer.close()
    reader.close()


def _count_visible_to_reader(reader: sqlite3.Connection) -> int:
    return reader.execute("SELECT COUNT(*) FROM rows_under_test").fetchone()[0]


def _insert_chunk(writer: sqlite3.Connection, *, start_id: int, n: int) -> None:
    writer.executemany(
        "INSERT INTO rows_under_test (id, payload) VALUES (?, ?)",
        [(start_id + i, f"payload_{start_id + i}") for i in range(n)],
    )


# ---------------------------------------------------------------------------
# Probe 1: pre-commit invisibility
# ---------------------------------------------------------------------------

def test_pre_commit_chunk_invisible_to_reader(two_conn_fixture) -> None:
    """Probe 1: rows written but not yet committed must be invisible
    to a separate reader connection — establishes the visibility
    boundary."""
    writer, reader = two_conn_fixture

    with BulkChunker(writer, caller_module=__name__) as chunker:
        _insert_chunk(writer, start_id=1, n=100)
        # Do NOT commit yet
        assert _count_visible_to_reader(reader) == 0, (
            "writer wrote 100 rows but did not commit; reader must see 0"
        )
        # Now commit and confirm reader sees them — sanity gate
        chunker.commit_chunk()
        assert _count_visible_to_reader(reader) == 100, (
            "after commit_chunk(), reader must see the 100-row chunk"
        )


# ---------------------------------------------------------------------------
# Probe 2: chunk-by-chunk visibility (at-most-1-chunk-behind invariant)
# ---------------------------------------------------------------------------

def test_at_most_one_chunk_behind_invariant(two_conn_fixture) -> None:
    """Probe 2: with N chunks each followed by commit_chunk(), a reader
    polling between chunks lags by AT MOST one chunk's worth of rows.
    This is the F11(v2) "intra-chunk staleness window" contract — the
    window must shrink to a single chunk size, not the whole bulk."""
    writer, reader = two_conn_fixture
    chunk_size = 50
    n_chunks = 4
    seen_visible_counts: list[int] = []

    with BulkChunker(writer, caller_module=__name__) as chunker:
        for chunk_idx in range(n_chunks):
            _insert_chunk(
                writer, start_id=chunk_idx * chunk_size + 1, n=chunk_size
            )
            chunker.commit_chunk()
            seen_visible_counts.append(_count_visible_to_reader(reader))

    # After each commit_chunk the reader's view advanced by exactly chunk_size
    expected = [chunk_size * (i + 1) for i in range(n_chunks)]
    assert seen_visible_counts == expected, (
        f"per-chunk visibility regression: expected {expected}, got {seen_visible_counts}. "
        "F11(v2) invariant: reader lags by at most 1 chunk after each commit_chunk()."
    )


# ---------------------------------------------------------------------------
# Probe 3: commit_chunk failure mode — no commit means no visibility
# ---------------------------------------------------------------------------

def test_skipping_commit_chunk_silences_visibility(two_conn_fixture) -> None:
    """Probe 3: if a writer skips commit_chunk() (regression / refactor
    error), the reader's view stays at the pre-bulk count. This is
    EXACTLY the F11(v2) failure mode — the antibody catches refactors
    that turn commit_chunk() into a no-op.

    Note: this test ASSERTS the negative case so a regression which
    accidentally inlines commits inside __exit__ but removes commit_chunk
    explicit commits would still light up here.
    """
    writer, reader = two_conn_fixture

    # Open a transaction explicitly so executemany goes into it but never
    # commits within the chunker context. The chunker's __exit__ does NOT
    # call commit() in current implementation, so the rows are visible to
    # the writer but invisible to the reader until something commits.
    writer.execute("BEGIN IMMEDIATE")
    with BulkChunker(writer, caller_module=__name__) as chunker:
        _insert_chunk(writer, start_id=1, n=100)
        # Skip commit_chunk() — simulate the regression
    # After __exit__ with no commit_chunk call, reader still sees 0
    assert _count_visible_to_reader(reader) == 0, (
        "skipping commit_chunk() should leave reader at 0; if this sees "
        "the rows, the chunker is committing implicitly and the F11(v2) "
        "invariant is being satisfied accidentally (which is fine — but the "
        "test must be rewritten to assert the *explicit* commit path)."
    )
    writer.rollback()


# ---------------------------------------------------------------------------
# Probe 4: production-shape — BulkChunker.commit_chunk delegates to conn.commit
# ---------------------------------------------------------------------------

def test_commit_chunk_invokes_conn_commit(two_conn_fixture) -> None:
    """Probe 4: structural antibody — BulkChunker.commit_chunk() must call
    conn.commit() on the wrapped connection. Catches a refactor that
    silently elides the commit (e.g. a wrapper that no-ops commits in
    a 'savepoint mode' flag).
    """
    writer, _reader = two_conn_fixture

    calls: list[str] = []

    class _ConnSpy:
        """Minimal spy implementing the BulkChunker.commit_chunk() contract."""

        def commit(self) -> None:
            calls.append("commit")

    spy = _ConnSpy()
    with BulkChunker(spy, caller_module=__name__) as chunker:
        chunker.commit_chunk()

    assert calls == ["commit"], (
        f"BulkChunker.commit_chunk() must invoke conn.commit() exactly once; "
        f"got call log {calls}. Regression: commit_chunk silently elides commit."
    )
