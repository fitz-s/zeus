# Lifecycle: created=2026-07-21; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: prove capture-policy value tiers, registered compact schema, and full-evidence isolation.
# Reuse: run on any change to snapshot_repo capture_trigger plumbing or init_snapshot_schema.
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/implementation/capture_policy_spec.md
"""capture_policy_spec.md value-tier antibodies.

The full journal keeps priority/JIT/keyframe executable evidence. Ordinary
discovery goes to a registered compact append journal that no existing money
path reader queries. The full table's capture_trigger remains unconstrained at
the SQLite layer to avoid an O(rows) boot migration, with value tiers enforced
at the write API.

* a CHECK-constrained ``ADD COLUMN`` forces SQLite (>=3.37) to full-scan every
  existing row (~0.9s / 3M rows measured; O(rows) with cold I/O on the ~43GB
  live trade table), whereas a plain nullable ``ADD COLUMN`` is O(1);
* the log-only hydration check warned on ``DISCOVERY_SWEEP`` rows — a value the
  scanner intentionally writes — on every money-path read (log amplification).

The taxonomy is an application invariant enforced at write; its distribution is
measured off the hot path by an audit query
(``SELECT capture_trigger, COUNT(*) FROM executable_market_snapshots GROUP BY 1``).
These tests are fixture-only (in-memory SQLite) and never touch a live DB.
"""

from __future__ import annotations

import json
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
    insert_compact_snapshot,
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


def test_capture_trigger_column_unconstrained_but_api_validates(conn):
    """The ADD COLUMN is deliberately unconstrained TEXT (no DB CHECK — a CHECK on
    ADD COLUMN full-scans every existing row of the ~43GB live table at boot). The
    taxonomy is enforced at the write API boundary (insert_snapshot) instead, so the
    ALTER stays O(1) metadata-only while out-of-taxonomy values are still rejected
    (consult re-review 2026-07-22)."""
    # (1) The DB column carries NO CHECK clause (sqlite_master.sql is rewritten by
    #     ADD COLUMN) — the column is unconstrained at the storage layer.
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='executable_market_snapshots'"
    ).fetchone()[0]
    assert "capture_trigger" in ddl
    assert not re.search(r"CHECK\s*\([^)]*capture_trigger", ddl, re.IGNORECASE)

    # (2) The write API enforces the §2 taxonomy: a value outside it raises ValueError.
    with pytest.raises(ValueError, match="taxonomy"):
        insert_snapshot(
            conn,
            _snapshot(snapshot_id="snap-bad-trigger"),
            capture_trigger="NOT_IN_TAXONOMY",
        )

    # A valid taxonomy value still inserts fine.
    insert_snapshot(conn, _snapshot(snapshot_id="snap-ok-trigger"), capture_trigger="KEYFRAME")
    row = conn.execute(
        "SELECT capture_trigger FROM executable_market_snapshots WHERE snapshot_id='snap-ok-trigger'"
    ).fetchone()
    assert row["capture_trigger"] == "KEYFRAME"


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
        "DAY0_EXTREME_EVENT",
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
    insert_snapshot(conn, _snapshot(snapshot_id="snap-read"), capture_trigger="KEYFRAME")
    loaded = get_snapshot(conn, "snap-read")
    assert loaded is not None
    assert loaded.snapshot_id == "snap-read"


# --- (c) compact value tier and boot registry --------------------------------


def test_fresh_world_init_matches_registry_no_compact_table():
    """The discovery-only table belongs to TRADE and must stay absent on WORLD."""
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


def test_fresh_trade_init_matches_registry_with_compact_table():
    """TRADE init creates the registered compact journal and still boots clean."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        init_schema_trade_only(c)
        assert (
            c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='executable_market_snapshot_compact'"
            ).fetchone()
            is not None
        )
        assert_db_matches_registry(c, DBIdentity.TRADE)  # must not raise
    finally:
        c.close()


def test_discovery_cannot_enter_full_executable_journal(conn):
    with pytest.raises(ValueError, match="full-eligible"):
        insert_snapshot(
            conn,
            _snapshot(snapshot_id="snap-discovery-full"),
            capture_trigger="DISCOVERY_SWEEP",
        )


def test_day0_extreme_event_cannot_enter_compact_journal(conn):
    """Crossing-instrumentation increment: DAY0_EXTREME_EVENT is full-eligible
    only. The compact table's capture_trigger CHECK enumerates exactly two
    values and SQLite has no ALTER-CHECK, so this trigger stays off that table
    by construction rather than requiring a live-table rebuild."""
    with pytest.raises(ValueError, match="compact-eligible"):
        insert_compact_snapshot(
            conn,
            _snapshot(snapshot_id="snap-day0-extreme-compact"),
            capture_trigger="DAY0_EXTREME_EVENT",
        )


def test_compact_snapshot_preserves_scalar_hash_and_top_k_without_full_body(conn):
    compact_id = insert_compact_snapshot(
        conn,
        _snapshot(snapshot_id="snap-compact"),
        capture_trigger="DISCOVERY_SWEEP",
        prev_hash="d" * 64,
        hash_delta_ms=250,
    )
    row = conn.execute(
        "SELECT * FROM executable_market_snapshot_compact WHERE compact_id = ?",
        (compact_id,),
    ).fetchone()

    assert row["condition_id"] == "condition-1"
    assert row["selected_outcome_token_id"] == "yes-token"
    assert row["raw_orderbook_hash"] == HASH_C
    assert json.loads(row["top_k_bids_json"]) == [["0.49", "100"]]
    assert json.loads(row["top_k_asks_json"]) == [["0.51", "100"]]
    assert row["prev_hash"] == "d" * 64
    assert row["hash_delta_ms"] == 250
    assert "orderbook_depth_json" not in {
        column[1]
        for column in conn.execute(
            "PRAGMA table_info(executable_market_snapshot_compact)"
        ).fetchall()
    }
    assert conn.execute(
        "SELECT 1 FROM executable_market_snapshots WHERE snapshot_id = 'snap-compact'"
    ).fetchone() is None
