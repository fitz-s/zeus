# Lifecycle: created=2026-08-25; last_reviewed=2026-08-29; last_reused=2026-08-29
# Purpose: Prove decision-log inline retention bounds both scan work and deletes while preserving progress and anchors.
# Reuse: Run after changing decision_log inline retention, cursor progress, or artifact persistence.
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 13 (bounded-by-construction storage redesign) -- coverage for
#   src/state/decision_chain.py::_inline_expire_decision_log, piggybacked in
#   store_artifact and store_settlement_records.
"""Antibodies for the decision_log inline (write-path-piggybacked) retention:
per-mode window selection, tier0-anchor protection, the LIMIT bound, and that
store_artifact/store_settlement_records actually invoke it."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from src.state.decision_chain import (
    CycleArtifact,
    _inline_expire_decision_log,
    _INLINE_EXPIRE_LIMIT,
    store_artifact,
    store_settlement_records,
)

DECISION_LOG_DDL = """
CREATE TABLE decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    artifact_json TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    env TEXT NOT NULL DEFAULT 'live'
)
"""

TIER0_DDL = """
CREATE TABLE tier0_candidate_set_provenance (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    selection_epoch_identity TEXT NOT NULL
)
"""

ZEUS_META_DDL = """
CREATE TABLE zeus_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _decision_log_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(DECISION_LOG_DDL)
    conn.execute("CREATE INDEX idx_decision_log_ts ON decision_log(timestamp)")
    return conn


def _seed(conn: sqlite3.Connection, *, mode: str, ts: str, summary: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (mode, ts, ts, json.dumps({"summary": summary or {}}), ts),
    )


def _count(conn: sqlite3.Connection, mode: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM decision_log WHERE mode = ?", (mode,)).fetchone()[0]


def test_expires_old_rows_of_the_same_mode_only() -> None:
    conn = _decision_log_conn()
    old, recent = _iso(10), _iso(1)
    _seed(conn, mode="exit_monitor", ts=old)          # older than 7d floor -> deletable
    _seed(conn, mode="exit_monitor", ts=recent)        # recent -> survives
    _seed(conn, mode="settlement", ts=old)              # different mode, 30d window -> survives

    _inline_expire_decision_log(conn, "exit_monitor")

    assert _count(conn, "exit_monitor") == 1
    assert _count(conn, "settlement") == 1  # untouched -- different mode, different window


def test_per_mode_window_full_auction_gets_30_days_not_7() -> None:
    conn = _decision_log_conn()
    ten_days_ago = _iso(10)
    _seed(conn, mode="global_single_order_auction", ts=ten_days_ago)

    _inline_expire_decision_log(conn, "global_single_order_auction")

    # 10 days < the 30-day full-auction window -- must survive.
    assert _count(conn, "global_single_order_auction") == 1


def test_tier0_anchored_row_survives_even_when_old() -> None:
    conn = _decision_log_conn()
    conn.execute(TIER0_DDL)
    old = _iso(40)
    _seed(
        conn,
        mode="global_single_order_auction",
        ts=old,
        summary={"selection_epoch_identity": "epoch-anchored"},
    )
    _seed(conn, mode="global_single_order_auction", ts=old, summary={"selection_epoch_identity": "epoch-free"})
    conn.execute(
        "INSERT INTO tier0_candidate_set_provenance (selection_epoch_identity) VALUES ('epoch-anchored')"
    )

    _inline_expire_decision_log(conn, "global_single_order_auction")

    remaining = [
        json.loads(row[0])["summary"]["selection_epoch_identity"]
        for row in conn.execute("SELECT artifact_json FROM decision_log").fetchall()
    ]
    assert remaining == ["epoch-anchored"]


def test_expire_is_limit_bounded() -> None:
    conn = _decision_log_conn()
    old = _iso(10)
    for _ in range(_INLINE_EXPIRE_LIMIT + 20):
        _seed(conn, mode="exit_monitor", ts=old)

    _inline_expire_decision_log(conn, "exit_monitor")

    # Exactly one LIMIT-bounded chunk fires per call -- backlog drains over
    # subsequent inserts, never in one unbounded sweep.
    assert _count(conn, "exit_monitor") == 20


def test_expire_forces_timestamp_index_in_money_path_transaction() -> None:
    conn = _decision_log_conn()
    old = _iso(10)
    _seed(conn, mode="exit_monitor", ts=old)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    _inline_expire_decision_log(conn, "exit_monitor")

    assert any(
        "FROM decision_log INDEXED BY idx_decision_log_ts" in statement
        for statement in statements
    )


def test_expire_walks_backward_from_cutoff_not_oldest_history() -> None:
    conn = _decision_log_conn()
    expired = [_iso(8 + offset) for offset in range(_INLINE_EXPIRE_LIMIT + 1)]
    for ts in expired:
        _seed(conn, mode="exit_monitor", ts=ts)

    _inline_expire_decision_log(conn, "exit_monitor")

    # The cutoff-side 50 rows drain first, leaving the single oldest row. This
    # is the behavioral guard against walking unrelated ancient history while
    # a money-path writer transaction is open.
    remaining = conn.execute(
        "SELECT timestamp FROM decision_log WHERE mode = 'exit_monitor'"
    ).fetchall()
    assert remaining == [(expired[-1],)]


def test_sparse_mode_scan_is_bounded_and_cursor_reaches_older_target() -> None:
    conn = _decision_log_conn()
    conn.execute(ZEUS_META_DDL)
    target_ts = _iso(20)
    unrelated_ts = _iso(8)
    _seed(conn, mode="exit_monitor", ts=target_ts)
    for _ in range(2_000):
        _seed(conn, mode="other_mode", ts=unrelated_ts)

    _inline_expire_decision_log(conn, "exit_monitor")

    # A fixed scan budget must stop before the sparse target instead of
    # traversing every unrelated row while the money-path transaction is open.
    assert _count(conn, "exit_monitor") == 1
    cursor_row = conn.execute(
        "SELECT value FROM zeus_meta "
        "WHERE key = 'decision_log.inline_expire_cursor.v1:exit_monitor'"
    ).fetchone()
    assert cursor_row is not None

    # The durable per-mode cursor continues the bounded walk across calls.
    for _ in range(4):
        _inline_expire_decision_log(conn, "exit_monitor")
    assert _count(conn, "exit_monitor") == 0


def test_malformed_cursor_resets_without_blocking_expiry() -> None:
    conn = _decision_log_conn()
    conn.execute(ZEUS_META_DDL)
    conn.execute(
        "INSERT INTO zeus_meta(key, value) VALUES (?, ?)",
        ("decision_log.inline_expire_cursor.v1:exit_monitor", "[]"),
    )
    _seed(conn, mode="exit_monitor", ts=_iso(10))

    _inline_expire_decision_log(conn, "exit_monitor")

    assert _count(conn, "exit_monitor") == 0


def test_store_artifact_piggybacks_expiry() -> None:
    conn = _decision_log_conn()
    old = _iso(10)
    _seed(conn, mode="exit_monitor", ts=old)

    store_artifact(
        conn,
        CycleArtifact(mode="exit_monitor", started_at=_iso(0), completed_at=_iso(0)),
    )

    # The old row is gone; only the just-inserted fresh row remains.
    assert _count(conn, "exit_monitor") == 1
    fresh = conn.execute(
        "SELECT started_at FROM decision_log WHERE mode = 'exit_monitor'"
    ).fetchone()[0]
    assert fresh != old


def test_store_settlement_records_piggybacks_expiry() -> None:
    conn = _decision_log_conn()
    old = _iso(40)
    _seed(conn, mode="settlement", ts=old)

    store_settlement_records(conn, [{"trade_id": "t1", "city": "Denver"}])

    # settlement's 30-day window: the 40-day-old row is gone, only the fresh one remains.
    assert _count(conn, "settlement") == 1
