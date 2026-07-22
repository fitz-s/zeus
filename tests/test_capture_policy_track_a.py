# Created: 2026-07-21
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/implementation/capture_policy_spec.md
"""capture_policy_spec.md Track A antibodies.

Track A is additive-only / log-only: the capture_trigger column and the
compact table exist but nothing routes to compact yet, and the hydration
check only ever logs. These tests are fixture-only (in-memory sqlite) and
never touch a live DB.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
from src.state.db import init_schema, init_schema_trade_only
from src.state.snapshot_repo import (
    _track_a_capture_trigger_check,
    get_snapshot,
    init_snapshot_schema,
    insert_snapshot,
)

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    init_schema_trade_only(c)
    yield c
    c.close()


def _snapshot(snapshot_id: str = "snap-cp1", **overrides) -> ExecutableMarketSnapshot:
    payload = dict(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-1",
        event_id="event-1",
        event_slug="weather-nyc-high",
        condition_id="condition-1",
        question_id="question-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
        selected_outcome_token_id="yes-token",
        outcome_label="YES",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=NOW + timedelta(hours=1),
        market_end_at=NOW + timedelta(days=1),
        market_close_at=NOW + timedelta(days=1, hours=1),
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        fee_details={"bps": 0, "source": "test"},
        token_map_raw={"YES": "yes-token", "NO": "no-token"},
        rfqe=None,
        neg_risk=False,
        orderbook_top_bid=Decimal("0.49"),
        orderbook_top_ask=Decimal("0.51"),
        orderbook_depth_jsonb='{"asks":[["0.51","100"]],"bids":[["0.49","100"]]}',
        raw_gamma_payload_hash=HASH_A,
        raw_clob_market_info_hash=HASH_B,
        raw_orderbook_hash=HASH_C,
        authority_tier="CLOB",
        captured_at=NOW,
        freshness_deadline=NOW + timedelta(seconds=30),
    )
    payload.update(overrides)
    return ExecutableMarketSnapshot(**payload)


def _insert_compact_row(conn, compact_id: str = "emc2-test", trigger: str = "DISCOVERY_SWEEP") -> None:
    conn.execute(
        """
        INSERT INTO executable_market_snapshot_compact (
          compact_id, condition_id, selected_outcome_token_id, captured_at,
          raw_orderbook_hash, capture_trigger, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (compact_id, "condition-1", "yes-token", NOW.isoformat(), HASH_C, trigger, 1),
    )


# --- (a) idempotent ALTER is safe to run twice ------------------------------


def test_capture_trigger_migration_idempotent(conn):
    """init_snapshot_schema (the capture_trigger ALTER's home) can run again
    on an already-migrated connection without raising 'duplicate column'."""
    init_snapshot_schema(conn)  # fixture already ran it once via init_schema_trade_only
    init_snapshot_schema(conn)  # third time total — still must not raise

    columns = {row[1] for row in conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()}
    assert "capture_trigger" in columns

    # Column still behaves correctly post-re-migration, not left in a broken state.
    insert_snapshot(conn, _snapshot(snapshot_id="snap-post-migration"), capture_trigger="KEYFRAME")
    row = conn.execute(
        "SELECT capture_trigger FROM executable_market_snapshots WHERE snapshot_id = ?",
        ("snap-post-migration",),
    ).fetchone()
    assert row["capture_trigger"] == "KEYFRAME"


# --- (b) each trigger class stamps the correct capture_trigger -------------


@pytest.mark.parametrize(
    "trigger",
    [
        "PRIORITY_HELD_POSITION",
        "PRIORITY_OPEN_ORDER",
        "PRIORITY_MARKER",
        "NEAR_THRESHOLD_MATCH",
        "KEYFRAME",
        "JIT_SUBMIT",
        "DISCOVERY_SWEEP",
    ],
)
def test_insert_snapshot_stamps_capture_trigger(conn, trigger):
    insert_snapshot(conn, _snapshot(snapshot_id=f"snap-{trigger}"), capture_trigger=trigger)
    row = conn.execute(
        "SELECT capture_trigger FROM executable_market_snapshots WHERE snapshot_id = ?",
        (f"snap-{trigger}",),
    ).fetchone()
    assert row["capture_trigger"] == trigger


def test_insert_snapshot_capture_trigger_defaults_null(conn):
    """Omitting capture_trigger (a caller not yet updated) writes NULL, not an error."""
    insert_snapshot(conn, _snapshot(snapshot_id="snap-no-trigger"))
    row = conn.execute(
        "SELECT capture_trigger FROM executable_market_snapshots WHERE snapshot_id = ?",
        ("snap-no-trigger",),
    ).fetchone()
    assert row["capture_trigger"] is None


def test_insert_snapshot_rejects_unrecognized_capture_trigger(conn):
    with pytest.raises(sqlite3.IntegrityError):
        insert_snapshot(
            conn,
            _snapshot(snapshot_id="snap-bad-trigger"),
            capture_trigger="NOT_A_REAL_TRIGGER",
        )


# --- (c) compact table created + append-only --------------------------------


def test_compact_table_created_with_indexes(conn):
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'executable_market_snapshot_compact'"
    ).fetchone()
    assert table is not None
    indexes = {
        row[1]
        for row in conn.execute("PRAGMA index_list(executable_market_snapshot_compact)").fetchall()
    }
    assert {
        "idx_snapshot_compact_condition_captured",
        "idx_snapshot_compact_selected_token_captured",
    }.issubset(indexes)


def test_compact_table_unused_by_default(conn):
    """This increment routes zero rows to compact — table exists but is empty."""
    count = conn.execute("SELECT COUNT(*) FROM executable_market_snapshot_compact").fetchone()[0]
    assert count == 0


def test_compact_table_rejects_update(conn):
    _insert_compact_row(conn)
    with pytest.raises(sqlite3.IntegrityError, match="APPEND-ONLY"):
        conn.execute(
            "UPDATE executable_market_snapshot_compact SET orderbook_top_bid = '0.50' WHERE compact_id = ?",
            ("emc2-test",),
        )


def test_compact_table_rejects_delete(conn):
    _insert_compact_row(conn)
    with pytest.raises(sqlite3.IntegrityError, match="APPEND-ONLY"):
        conn.execute(
            "DELETE FROM executable_market_snapshot_compact WHERE compact_id = ?",
            ("emc2-test",),
        )


def test_compact_table_capture_trigger_check_is_compact_specific(conn):
    """Compact's CHECK is the §3 2-value set, distinct from the full table's
    7-value set — a value valid on the full table must be rejected here."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_compact_row(conn, trigger="JIT_SUBMIT")


# --- (d) hydration check logs but never raises ------------------------------


def test_hydration_check_logs_on_compact_eligible_trigger(conn, caplog):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-sweep"), capture_trigger="DISCOVERY_SWEEP")
    with caplog.at_level(logging.WARNING, logger="src.state.snapshot_repo"):
        loaded = get_snapshot(conn, "snap-sweep")
    assert loaded is not None  # log-only: never raises, never blocks the read
    assert "capture_policy_track_a" in caplog.text
    assert "condition-1" in caplog.text
    assert "DISCOVERY_SWEEP" in caplog.text


@pytest.mark.parametrize(
    "trigger",
    ["PRIORITY_HELD_POSITION", "PRIORITY_OPEN_ORDER", "PRIORITY_MARKER", "NEAR_THRESHOLD_MATCH", "KEYFRAME", "JIT_SUBMIT"],
)
def test_hydration_check_silent_on_full_eligible_trigger(conn, caplog, trigger):
    insert_snapshot(conn, _snapshot(snapshot_id=f"snap-full-{trigger}"), capture_trigger=trigger)
    with caplog.at_level(logging.WARNING, logger="src.state.snapshot_repo"):
        loaded = get_snapshot(conn, f"snap-full-{trigger}")
    assert loaded is not None
    assert "capture_policy_track_a" not in caplog.text


def test_hydration_check_silent_on_null_trigger(conn, caplog):
    """Pre-migration rows / not-yet-updated callers: NULL trigger, nothing to
    validate, no warning (would be 100% log noise across the entire existing
    history, none of which is a taxonomy violation)."""
    insert_snapshot(conn, _snapshot(snapshot_id="snap-legacy"))
    with caplog.at_level(logging.WARNING, logger="src.state.snapshot_repo"):
        loaded = get_snapshot(conn, "snap-legacy")
    assert loaded is not None
    assert "capture_policy_track_a" not in caplog.text


def test_hydration_check_never_raises_when_column_missing():
    """Defensive: an unmigrated row shape (no capture_trigger column at all,
    e.g. a DB read before this migration has run) must not raise."""
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    try:
        raw.execute("CREATE TABLE t (snapshot_id TEXT, condition_id TEXT)")
        raw.execute("INSERT INTO t VALUES ('s1', 'c1')")
        row = raw.execute("SELECT * FROM t").fetchone()
        _track_a_capture_trigger_check(row)  # must not raise
    finally:
        raw.close()


def test_hydration_check_never_raises_on_unrecognized_value():
    """Defensive: even a value outside every known enum (a future bug, a
    manually-edited row) must log, not raise."""
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    try:
        raw.execute("CREATE TABLE t (snapshot_id TEXT, condition_id TEXT, capture_trigger TEXT)")
        raw.execute("INSERT INTO t VALUES ('s1', 'c1', 'SOMETHING_UNEXPECTED')")
        row = raw.execute("SELECT * FROM t").fetchone()
        _track_a_capture_trigger_check(row)  # must not raise
    finally:
        raw.close()
