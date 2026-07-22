# Lifecycle: created=2026-07-21; last_reviewed=2026-07-22; last_reused=never
# Purpose: prove capture-policy Track A is additive (nullable capture_trigger column + stamping) — no compact table, no CHECK, registry-clean.
# Reuse: run on any change to snapshot_repo capture_trigger plumbing or init_snapshot_schema.
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/implementation/capture_policy_spec.md
"""capture_policy_spec.md Track A antibodies.

Track A is additive-only: ``init_snapshot_schema`` adds a single nullable,
UNCONSTRAINED ``capture_trigger`` column to ``executable_market_snapshots`` and
every writer stamps a fixed taxonomy constant. This increment has NO compact
table and NO hot-path hydration check (both were removed after the PR review):

* the compact table was unregistered in ``db_table_ownership.yaml`` — a fresh
  init created it, and the boot-time ``assert_db_matches_registry`` would then
  abort the daemon with ``extra_on_disk=executable_market_snapshot_compact``;
* a CHECK-constrained ``ADD COLUMN`` forces SQLite (>=3.37) to full-scan every
  existing row (~0.9s / 3M rows measured; O(rows) with cold I/O on the ~43GB
  live trade table), whereas a plain nullable ``ADD COLUMN`` is O(1);
* the log-only hydration check warned on ``DISCOVERY_SWEEP`` rows — a value the
  scanner intentionally writes — on every money-path read (log amplification).

The taxonomy is an application invariant enforced at write; its distribution is
measured off the hot path by an audit query
(``SELECT capture_trigger, COUNT(*) FROM executable_market_snapshots GROUP BY 1``).
These tests are fixture-only (in-memory sqlite) and never touch a live DB.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
from src.state.db import init_schema, init_schema_trade_only
from src.state.snapshot_repo import (
    get_snapshot,
    init_snapshot_schema,
    insert_snapshot,
)
from src.state.table_registry import DBIdentity, assert_db_matches_registry

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


# --- (a) idempotent, O(1) additive ALTER ------------------------------------


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


def test_capture_trigger_column_is_unconstrained_no_db_check(conn):
    """The ADD COLUMN is deliberately unconstrained TEXT (no CHECK): a CHECK on
    ADD COLUMN forces SQLite to full-scan every existing row of the ~43GB live
    trade table at boot. The taxonomy is enforced by the application at write,
    so the DB accepts any TEXT and the ALTER stays O(1) metadata-only."""
    # A value outside the taxonomy inserts without a DB-level IntegrityError.
    insert_snapshot(
        conn,
        _snapshot(snapshot_id="snap-arbitrary"),
        capture_trigger="NOT_IN_TAXONOMY",
    )
    row = conn.execute(
        "SELECT capture_trigger FROM executable_market_snapshots WHERE snapshot_id = ?",
        ("snap-arbitrary",),
    ).fetchone()
    assert row["capture_trigger"] == "NOT_IN_TAXONOMY"

    # The table DDL (sqlite_master.sql is rewritten by ADD COLUMN) carries the
    # column but NO CHECK clause naming it.
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='executable_market_snapshots'"
    ).fetchone()[0]
    assert "capture_trigger" in ddl
    assert not re.search(r"CHECK\s*\([^)]*capture_trigger", ddl, re.IGNORECASE)


# --- (b) each trigger class stamps the correct capture_trigger --------------


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


def test_get_snapshot_hydrates_row_with_capture_trigger(conn):
    """The read/hydration path returns a snapshot unaffected by the column: no
    hot-path check remains, so a stamped row hydrates like any other."""
    insert_snapshot(conn, _snapshot(snapshot_id="snap-read"), capture_trigger="DISCOVERY_SWEEP")
    loaded = get_snapshot(conn, "snap-read")
    assert loaded is not None
    assert loaded.snapshot_id == "snap-read"


# --- (c) BLOCKER-1 regression: no unregistered table; boot registry passes ---


def test_fresh_world_init_matches_registry_no_compact_table():
    """Track A must not create an ownership-unregistered table. A fresh WORLD
    init followed by the boot-time registry assertion must PASS, and the
    (removed) compact table must be absent. Before the PR-review fix this
    aborted the daemon at boot (extra_on_disk=executable_market_snapshot_compact)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        init_schema(c)
        assert (
            c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='executable_market_snapshot_compact'"
            ).fetchone()
            is None
        )
        assert_db_matches_registry(c, DBIdentity.WORLD)  # must not raise
    finally:
        c.close()


def test_fresh_trade_init_matches_registry_no_compact_table():
    """Same guard for the TRADE DB — the live snapshot table's real home."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        init_schema_trade_only(c)
        assert (
            c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='executable_market_snapshot_compact'"
            ).fetchone()
            is None
        )
        assert_db_matches_registry(c, DBIdentity.TRADE)  # must not raise
    finally:
        c.close()
