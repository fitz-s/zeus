# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/evidence/plans/2026-06-13_fill_bridge_retry_storm.md —
#   live incident 2026-06-12: legacy NOT NULL disposition column made the
#   accumulating-row insert fail forever, freezing attempt_count at 1 so the
#   quarantine threshold was unreachable (infinite retry storm); the rest-filled
#   orphan bridge re-selected terminal-RECONCILED aggregates its own ledger guard
#   rejects, looping every scan, and one raising row aborted the whole batch.
"""Antibodies for the fill-bridge retry-storm pair (schema drift + orphan re-selection)."""
from __future__ import annotations

import sqlite3

import pytest

from src.events.edli_position_bridge import (
    _increment_failure_count,
    _quarantine_aggregate,
    get_fill_bridge_disposition,
)
from src.state.schema.edli_fill_bridge_dispositions_schema import ensure_table

LEGACY_NOT_NULL_DDL = """
CREATE TABLE edli_fill_bridge_dispositions (
    aggregate_id  TEXT PRIMARY KEY,
    disposition   TEXT NOT NULL
        CHECK (disposition IN ('SETTLED_MARKET_FILL_BOOKED', 'QUARANTINED_BRIDGE_FAILURE')),
    reason        TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""


@pytest.fixture()
def legacy_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(LEGACY_NOT_NULL_DDL)
    conn.execute(
        "INSERT INTO edli_fill_bridge_dispositions VALUES "
        "('agg_settled', 'SETTLED_MARKET_FILL_BOOKED', 'settled', 0, NULL, 't0', 't0')"
    )
    yield conn
    conn.close()


def _disposition_notnull(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(edli_fill_bridge_dispositions)").fetchall()
    return any(r["name"] == "disposition" and bool(r["notnull"]) for r in rows)


def test_ensure_table_relaxes_legacy_not_null_and_preserves_rows(legacy_conn):
    """CREATE TABLE IF NOT EXISTS cannot relax constraints — ensure_table must
    detect the drift and rebuild (the exact category that froze quarantine live)."""
    assert _disposition_notnull(legacy_conn)
    ensure_table(legacy_conn)
    assert not _disposition_notnull(legacy_conn)
    # pre-existing terminal row preserved
    assert get_fill_bridge_disposition(legacy_conn, "agg_settled") == "SETTLED_MARKET_FILL_BOOKED"
    # NULL-disposition accumulating insert now constructable
    legacy_conn.execute(
        "INSERT INTO edli_fill_bridge_dispositions "
        "(aggregate_id, disposition, reason, attempt_count, created_at, updated_at) "
        "VALUES ('agg_acc', NULL, 'bridge_failure_accumulating', 1, 't1', 't1')"
    )


def test_ensure_table_idempotent_on_migrated_table(legacy_conn):
    ensure_table(legacy_conn)
    ensure_table(legacy_conn)  # second run must be a no-op, not a data loss
    assert get_fill_bridge_disposition(legacy_conn, "agg_settled") == "SETTLED_MARKET_FILL_BOOKED"


def test_quarantine_reachable_after_migration(legacy_conn):
    """Relationship test: failure-count increments must actually accumulate so the
    caller's quarantine threshold fires — on the MIGRATED legacy table."""
    ensure_table(legacy_conn)
    counts = [
        _increment_failure_count(legacy_conn, "agg_fail", "boom", f"t{i}")
        for i in range(1, 4)
    ]
    assert counts == [1, 2, 3], "attempt_count frozen — quarantine unreachable (live bug)"
    _quarantine_aggregate(legacy_conn, "agg_fail", "boom", 3, "t4")
    assert get_fill_bridge_disposition(legacy_conn, "agg_fail") == "QUARANTINED_BRIDGE_FAILURE"


def test_orphan_bridge_excludes_terminal_reconciled_aggregates():
    """The candidate query must mirror the ledger guard: terminal RECONCILED
    projections are unselectable (not selected-then-rejected every scan)."""
    import inspect

    from src.events import edli_trade_fact_bridge as mod

    src = inspect.getsource(mod.append_rest_filled_orphan_trade_facts_to_edli)
    assert "edli_live_order_projection" in src
    assert "RECONCILED" in src
    assert "pending_reconcile" in src


def test_orphan_bridge_poison_row_does_not_abort_batch():
    """One ledger-rejected row must not starve the remaining recoverable orphans."""
    import inspect

    from src.events import edli_trade_fact_bridge as mod

    src = inspect.getsource(mod.append_rest_filled_orphan_trade_facts_to_edli)
    assert "except LiveOrderAggregateError" in src
    assert "continue" in src
