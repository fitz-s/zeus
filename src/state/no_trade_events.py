# Lifecycle: created=2026-05-20; last_reviewed=2026-05-20; last_reused=never
# Purpose: Writer/reader for the no_trade_events instrumentation table (world DB, K1 split).
# Reuse: Verify NoTradeReason enum coverage, INV-37 conn contract, and allocate_decision_seq UNION logic.
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742)

"""
no_trade_events — writer / reader for the no_trade_events instrumentation table.

Schema lives in src/state/schema/no_trade_events_schema.py.
Table belongs to zeus-world.db (world DB, K1 split).

Two-tier model (§5.1):
  - reason: NoTradeReason (StrEnum CATEGORY, enforced by DB CHECK constraint)
  - reason_detail: free-form diagnostic string (original f-string / interpolated content)

INV-37 contract:
  writer requires caller-provided conn (INV-37); reader auto-opens if conn=None.
  write_no_trade_event takes natural_key: DecisionNaturalKey + reason + reason_detail +
  observed_at, with conn: sqlite3.Connection REQUIRED (no auto-open on write path).
  Passing a trades-DB conn raises AssertionError.

decision_seq is derived atomically under db_writer_lock(LIVE) via allocate_decision_seq
(UNION of decision_events + no_trade_events for the 4-tuple) so that sequences are
collision-free across both tables. Caller does not supply seq.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from src.contracts.decision_natural_key import DecisionNaturalKey, make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.state.decision_events import allocate_decision_seq


def write_no_trade_event(
    natural_key: DecisionNaturalKey,
    reason: NoTradeReason,
    reason_detail: Optional[str],
    observed_at: str,
    *,
    conn: sqlite3.Connection,
) -> DecisionNaturalKey:
    """Persist a no-trade decision event to the world DB.

    natural_key: DecisionNaturalKey 5-tuple (market_slug, temperature_metric,
        target_date, observation_time, decision_seq). The decision_seq field
        is IGNORED — a new seq is derived atomically from the UNION of
        decision_events + no_trade_events under db_writer_lock(LIVE).
    reason: NoTradeReason StrEnum — CATEGORY tier (enforced by DB CHECK).
    reason_detail: free-form diagnostic string — DETAIL tier (no constraint).
        For literal string callsites: pass the original string.
        For f-string callsites: pass the interpolated string.
        For dynamic callsites: pass str(exc) or similar.
    observed_at: ISO-8601 UTC timestamp of the write (caller provides for testability).

    conn: REQUIRED — caller must supply a zeus-world.db connection (INV-37).
        Use get_world_connection(write_class=WriteClass.LIVE) at the call frame.
        Passing a trades-DB conn raises AssertionError.

    decision_seq is derived atomically under db_writer_lock(LIVE) via
    allocate_decision_seq (UNION query across both tables). Caller does not supply seq.

    Returns the full DecisionNaturalKey (5-tuple with derived decision_seq).
    """
    from src.state.db import SCHEMA_VERSION, ZEUS_WORLD_DB_PATH
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    _db_list = conn.execute("PRAGMA database_list").fetchall()
    _actual_path = _db_list[0][2] if _db_list else ""
    if not _actual_path.endswith("zeus-world.db"):
        raise AssertionError(
            f"write_no_trade_event: conn must be a world DB connection "
            f"(lock path is ZEUS_WORLD_DB_PATH); got {_actual_path!r}. "
            "Caller must open a world connection via get_world_connection(write_class=WriteClass.LIVE)."
        )

    market_slug, temperature_metric, target_date, observation_time, _ = natural_key

    row_seq: int
    with db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.LIVE):
        # Derive decision_seq atomically under the LIVE lock.
        # allocate_decision_seq queries UNION of decision_events + no_trade_events
        # so sequences are collision-free across both tables for the same 4-tuple.
        row_seq = allocate_decision_seq(
            market_slug, temperature_metric, target_date, observation_time,
            conn=conn,
        )

        conn.execute(
            """
            INSERT INTO no_trade_events (
                market_slug, temperature_metric, target_date,
                observation_time, decision_seq,
                reason, reason_detail,
                observed_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_slug,
                temperature_metric,
                target_date,
                observation_time,
                row_seq,
                reason.value,
                reason_detail,
                observed_at,
                SCHEMA_VERSION,
            ),
        )
        conn.commit()

    return make_decision_natural_key(
        market_slug=market_slug,
        temperature_metric=temperature_metric,  # type: ignore[arg-type]
        target_date=target_date,
        observation_time=observation_time,
        decision_seq=row_seq,
    )


def read_no_trade_events_by_market(
    market_slug: str,
    since: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Read no-trade events for a market from the world DB since a given ISO-8601 timestamp.

    Returns rows as dicts ordered by (observed_at, decision_seq) ASC.
    conn=None -> opens a read-only world connection (auto-closed after query).
    """
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection_read_only
        conn = get_world_connection_read_only()

    prior_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT market_slug, temperature_metric, target_date,
                   observation_time, decision_seq,
                   reason, reason_detail,
                   observed_at, schema_version
            FROM no_trade_events
            WHERE market_slug = ? AND observed_at >= ?
            ORDER BY observed_at ASC, decision_seq ASC
            """,
            (market_slug, since),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.row_factory = prior_row_factory
        if own_conn:
            conn.close()
    return rows
