# Created: 2026-06-13
# Last reused or audited: 2026-07-11
# Authority basis: docs/evidence/plans/2026-06-13_fill_bridge_retry_storm.md —
#   live incident 2026-06-12: legacy NOT NULL disposition column made the
#   accumulating-row insert fail forever, freezing attempt_count at 1 and
#   defeating retry-cadence evidence (infinite retry storm); the rest-filled
#   orphan bridge re-selected terminal-RECONCILED aggregates its own ledger guard
#   rejects, looping every scan, and one raising row aborted the whole batch.
#   Quarantine excision (docs/rebuild/quarantine_excision_2026-07-11.md T1,
#   2026-07-11): the permanent QUARANTINED_BRIDGE_FAILURE terminal disposition
#   is retired (CHECK literal dropped via table rebuild); a live DB carrying
#   the retired value must be drained back to an accumulating row (disposition
#   NULL) so the fixed scanner re-drives it under decaying retry, never
#   excluding it.
"""Antibodies for the fill-bridge retry-storm pair (schema drift + orphan re-selection)
and the quarantine-literal drop migration."""
from __future__ import annotations

import sqlite3

import pytest

from src.events.edli_position_bridge import (
    _increment_failure_count,
    get_fill_bridge_disposition,
    is_retry_eligible,
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

# Nullable-disposition legacy DDL: the shape live DBs carried between the
# 2026-06-12 nullability fix and the 2026-07-11 quarantine excision — CHECK
# still allows the retired QUARANTINED_BRIDGE_FAILURE literal.
LEGACY_QUARANTINE_CHECK_DDL = """
CREATE TABLE edli_fill_bridge_dispositions (
    aggregate_id  TEXT PRIMARY KEY,
    disposition   TEXT
        CHECK (disposition IS NULL OR disposition IN ('SETTLED_MARKET_FILL_BOOKED', 'QUARANTINED_BRIDGE_FAILURE')),
    reason        TEXT,
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


def test_failure_count_accumulates_on_migrated_table(legacy_conn):
    """Relationship test: failure-count increments must actually accumulate (not
    freeze) on the MIGRATED legacy table — retry-cadence evidence, never a path
    to exclusion (no terminal quarantine disposition exists any more)."""
    ensure_table(legacy_conn)
    counts = [
        _increment_failure_count(legacy_conn, "agg_fail", "boom", f"t{i}")
        for i in range(1, 4)
    ]
    assert counts == [1, 2, 3], "attempt_count frozen — retry-cadence evidence unreachable (live bug)"
    # No terminal disposition — the aggregate is still accumulating and
    # eligible for retry forever (subject only to backoff cadence).
    assert get_fill_bridge_disposition(legacy_conn, "agg_fail") is None


# ---------------------------------------------------------------------------
# Quarantine-literal drop migration (T1 excision, 2026-07-11)
# ---------------------------------------------------------------------------


@pytest.fixture()
def legacy_quarantine_conn():
    """A live-shaped DB carrying a QUARANTINED_BRIDGE_FAILURE row under the
    retired CHECK — the exact shape of the 8 live rows found 2026-07-11."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(LEGACY_QUARANTINE_CHECK_DDL)
    conn.execute(
        "INSERT INTO edli_fill_bridge_dispositions "
        "(aggregate_id, disposition, reason, attempt_count, last_error, created_at, updated_at) "
        "VALUES ('agg_quarantined', 'QUARANTINED_BRIDGE_FAILURE', "
        "'quarantined after 10 consecutive failures', 10, 'boom', 't0', "
        "'2026-06-12T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO edli_fill_bridge_dispositions "
        "(aggregate_id, disposition, reason, attempt_count, created_at, updated_at) "
        "VALUES ('agg_settled', 'SETTLED_MARKET_FILL_BOOKED', 'settled', 0, 't0', 't0')"
    )
    yield conn
    conn.close()


def _check_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='edli_fill_bridge_dispositions'"
    ).fetchone()
    return str(row["sql"])


def test_ensure_table_drops_quarantine_check_literal(legacy_quarantine_conn):
    assert "QUARANTINED_BRIDGE_FAILURE" in _check_sql(legacy_quarantine_conn)
    ensure_table(legacy_quarantine_conn)
    assert "QUARANTINED_BRIDGE_FAILURE" not in _check_sql(legacy_quarantine_conn)
    # A fresh write attempting the retired literal is now structurally impossible.
    with pytest.raises(sqlite3.IntegrityError):
        legacy_quarantine_conn.execute(
            "INSERT INTO edli_fill_bridge_dispositions "
            "(aggregate_id, disposition, reason, attempt_count, created_at, updated_at) "
            "VALUES ('agg_new_bad', 'QUARANTINED_BRIDGE_FAILURE', 'x', 0, 't', 't')"
        )


def test_ensure_table_drains_quarantined_rows_to_accumulating(legacy_quarantine_conn):
    """The drain: existing QUARANTINED_BRIDGE_FAILURE rows lose their terminal
    disposition (NULL) but keep attempt_count/last_error as retry-cadence
    evidence, so the fixed scanner re-drives them under backoff instead of
    skipping them forever."""
    ensure_table(legacy_quarantine_conn)

    disp = get_fill_bridge_disposition(legacy_quarantine_conn, "agg_quarantined")
    assert disp is None, f"drained row must be non-terminal (accumulating); got {disp!r}"

    row = legacy_quarantine_conn.execute(
        "SELECT attempt_count, last_error FROM edli_fill_bridge_dispositions WHERE aggregate_id = ?",
        ("agg_quarantined",),
    ).fetchone()
    assert row["attempt_count"] == 10, "attempt_count (retry evidence) must survive the drain"
    assert row["last_error"] == "boom"

    # Unrelated SETTLED row is untouched by the drain.
    assert get_fill_bridge_disposition(legacy_quarantine_conn, "agg_settled") == "SETTLED_MARKET_FILL_BOOKED"


def test_drained_row_retried_once_backoff_window_elapses(legacy_quarantine_conn):
    """A drained (formerly QUARANTINED_BRIDGE_FAILURE) row must become retry
    eligible once wall-clock time has elapsed past its backoff window — it is
    not readmitted instantly on every scan tick, but it is NEVER permanently
    excluded either."""
    from datetime import datetime, timedelta, timezone

    ensure_table(legacy_quarantine_conn)

    updated_at = datetime.fromisoformat("2026-06-12T00:00:00+00:00")
    # Immediately after migration (no wall-clock time elapsed): still backed off.
    assert is_retry_eligible(legacy_quarantine_conn, "agg_quarantined", updated_at) is False

    # attempt_count=10 -> capped backoff (256 cycles * 60s = 15360s ~= 4.27h).
    just_short = updated_at + timedelta(seconds=15359)
    assert is_retry_eligible(legacy_quarantine_conn, "agg_quarantined", just_short) is False

    past_window = updated_at + timedelta(seconds=15360)
    assert is_retry_eligible(legacy_quarantine_conn, "agg_quarantined", past_window) is True

    # In production this row was quarantined 2026-06-12; a scan running any
    # time after that window is well past the backoff cap, so the drained
    # aggregate is re-driven on the very next live scan (the packet's
    # "each either materializes a position or fails loudly" verification bar).
    far_future = datetime(2026, 7, 11, tzinfo=timezone.utc)
    assert is_retry_eligible(legacy_quarantine_conn, "agg_quarantined", far_future) is True


def test_ensure_table_admits_unrecoverable_manual_review_literal(legacy_quarantine_conn):
    """CHECK amendment (critic M-3, 2026-07-11): a second, deliberately
    NON-AUTOMATIC terminal (UNRECOVERABLE_MANUAL_REVIEW) is admitted by the
    current CHECK on a fully-migrated table, alongside SETTLED_MARKET_FILL_BOOKED."""
    from src.events.edli_position_bridge import mark_unrecoverable_manual_review

    ensure_table(legacy_quarantine_conn)
    mark_unrecoverable_manual_review(
        legacy_quarantine_conn, "agg_quarantined", "diagnosed dead", "2026-07-11T00:00:00+00:00"
    )
    assert get_fill_bridge_disposition(legacy_quarantine_conn, "agg_quarantined") == "UNRECOVERABLE_MANUAL_REVIEW"

    # The retired QUARANTINED_BRIDGE_FAILURE literal is still rejected.
    with pytest.raises(sqlite3.IntegrityError):
        legacy_quarantine_conn.execute(
            "INSERT INTO edli_fill_bridge_dispositions "
            "(aggregate_id, disposition, reason, attempt_count, created_at, updated_at) "
            "VALUES ('agg_new_bad2', 'QUARANTINED_BRIDGE_FAILURE', 'x', 0, 't', 't')"
        )


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
