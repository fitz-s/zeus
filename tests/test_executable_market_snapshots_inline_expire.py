# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 13 (bounded-by-construction storage redesign) -- coverage for
#   src/state/snapshot_repo.py::_inline_expire_executable_market_snapshots,
#   piggybacked in insert_snapshot.
"""Antibodies for the executable_market_snapshots inline retention: the
rowid-modulo throttle gate, the SAVEPOINT-nested trigger drop/verify/recreate
dance (append-only trigger intact both before and after every firing, even a
non-firing call), the venue_commands/position_events anchor exception, and
the LIMIT bound."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.state.snapshot_repo import (
    _inline_expire_executable_market_snapshots,
    _SNAPSHOT_INLINE_EXPIRE_LIMIT,
    _SNAPSHOT_INLINE_EXPIRE_THROTTLE,
)

SNAPSHOTS_DDL = """
CREATE TABLE executable_market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE TRIGGER no_update_executable_market_snapshots
BEFORE UPDATE ON executable_market_snapshots
BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
CREATE TRIGGER no_delete_executable_market_snapshots
BEFORE DELETE ON executable_market_snapshots
BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
"""

ANCHOR_DDL = """
CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, snapshot_id TEXT);
CREATE TABLE position_events (event_id TEXT PRIMARY KEY, snapshot_id TEXT);
"""


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _trigger_blocks_delete(conn: sqlite3.Connection) -> bool:
    """Probe against an EXISTING row -- a BEFORE DELETE trigger fires once per
    matching row, so a WHERE clause matching zero rows never invokes it."""
    probe_id = conn.execute(
        "SELECT snapshot_id FROM executable_market_snapshots LIMIT 1"
    ).fetchone()
    assert probe_id is not None, "fixture must seed at least one row before probing"
    try:
        conn.execute(
            "DELETE FROM executable_market_snapshots WHERE snapshot_id = ?", probe_id
        )
        return False
    except sqlite3.IntegrityError as exc:
        return "APPEND-ONLY" in str(exc)


def _seed_old_row_at_rowid(conn: sqlite3.Connection, n: int, *, with_anchors: bool) -> None:
    """Insert rows up to the Nth so last_insert_rowid() == n after the final insert."""
    old = _iso(40)
    for i in range(1, n + 1):
        conn.execute(
            "INSERT INTO executable_market_snapshots VALUES (?, ?, ?)",
            (f"snap-{i}", f"cond-{i}", old),
        )
    if with_anchors:
        conn.execute(ANCHOR_DDL)


def test_does_not_fire_below_the_throttle_count() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SNAPSHOTS_DDL)
    _seed_old_row_at_rowid(conn, _SNAPSHOT_INLINE_EXPIRE_THROTTLE - 1, with_anchors=False)

    _inline_expire_executable_market_snapshots(conn)

    count = conn.execute("SELECT COUNT(*) FROM executable_market_snapshots").fetchone()[0]
    assert count == _SNAPSHOT_INLINE_EXPIRE_THROTTLE - 1  # no-op: rowid % throttle != 0


def test_fires_at_the_throttle_count_and_deletes_old_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SNAPSHOTS_DDL)
    _seed_old_row_at_rowid(conn, _SNAPSHOT_INLINE_EXPIRE_THROTTLE, with_anchors=False)

    _inline_expire_executable_market_snapshots(conn)

    count = conn.execute("SELECT COUNT(*) FROM executable_market_snapshots").fetchone()[0]
    # All seeded rows are 40 days old (past the 30-day window) and unanchored;
    # one firing deletes up to _SNAPSHOT_INLINE_EXPIRE_LIMIT of them.
    assert count == _SNAPSHOT_INLINE_EXPIRE_THROTTLE - _SNAPSHOT_INLINE_EXPIRE_LIMIT


def test_append_only_trigger_intact_after_a_firing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SNAPSHOTS_DDL)
    _seed_old_row_at_rowid(conn, _SNAPSHOT_INLINE_EXPIRE_THROTTLE, with_anchors=False)

    assert _trigger_blocks_delete(conn)  # before
    _inline_expire_executable_market_snapshots(conn)
    assert _trigger_blocks_delete(conn)  # after a real firing


def test_append_only_trigger_intact_after_a_non_firing_call() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SNAPSHOTS_DDL)
    _seed_old_row_at_rowid(conn, 3, with_anchors=False)

    assert _trigger_blocks_delete(conn)
    _inline_expire_executable_market_snapshots(conn)  # rowid=3, throttle not reached
    assert _trigger_blocks_delete(conn)


def test_anchored_row_survives_a_firing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SNAPSHOTS_DDL)
    conn.executescript(ANCHOR_DDL)
    old = _iso(40)
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap-anchored', 'cond-1', ?)", (old,)
    )
    conn.execute("INSERT INTO venue_commands VALUES ('cmd-1', 'snap-anchored')")
    # Pad up to the throttle count so the firing condition is met.
    for i in range(2, _SNAPSHOT_INLINE_EXPIRE_THROTTLE + 1):
        conn.execute(
            "INSERT INTO executable_market_snapshots VALUES (?, ?, ?)",
            (f"snap-{i}", f"cond-{i}", old),
        )

    _inline_expire_executable_market_snapshots(conn)

    remaining = {
        row[0] for row in conn.execute("SELECT snapshot_id FROM executable_market_snapshots").fetchall()
    }
    assert "snap-anchored" in remaining


def test_never_raises_on_missing_trigger() -> None:
    conn = sqlite3.connect(":memory:")
    # No trigger at all -- simulates a legacy/isolated fixture missing NC-NEW-B.
    conn.execute(
        "CREATE TABLE executable_market_snapshots (snapshot_id TEXT PRIMARY KEY, "
        "condition_id TEXT NOT NULL, captured_at TEXT NOT NULL)"
    )
    _seed_old_row_at_rowid(conn, _SNAPSHOT_INLINE_EXPIRE_THROTTLE, with_anchors=False)

    _inline_expire_executable_market_snapshots(conn)  # must not raise

    count = conn.execute("SELECT COUNT(*) FROM executable_market_snapshots").fetchone()[0]
    assert count == _SNAPSHOT_INLINE_EXPIRE_THROTTLE  # skipped -- nothing deleted, nothing broken
