"""The terminal-no-fill recovery lookup must be seekable, or a cancel never terminalises.

`command_recovery._project_edli_terminal_no_fill_order_lifecycle` (command_recovery.py:24484)
matches an order lifecycle by `json_extract(payload_json,'$.command_id')` OR
`json_extract(payload_json,'$.venue_order_id')`. Without an index on those expressions the
OR resolves as a full table SCAN — measured 124-2374 ms on 11,646 live rows against the
recovery lane's own `_LIVE_TICK_DB_BUDGET_SECONDS = 0.1`. The read is therefore interrupted
on every attempt, in every lane, so a cancelled order stays ACKED forever.

That is not a cosmetic slow query. On 2026-09-18 command 57682424ec6740d4 held
`CANCEL_CONFIRMED` with `matched_size=0` at the venue while its command row still read
ACKED, and the live-trading restart gate counts nonterminal commands — so an unindexable
read was holding five landed fixes out of the trading daemon indefinitely.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.state.schema.edli_live_order_events_schema import ensure_tables

COMMAND_ID = "c0ffee0000000001"
VENUE_ORDER_ID = "0xabc0000000000000000000000000000000000000000000000000000000000001"

RECOVERY_SQL = """
SELECT aggregate_id, MAX(event_sequence) AS matched_sequence
  FROM edli_live_order_events
 WHERE json_extract(payload_json, '$.command_id') = ?
    OR json_extract(payload_json, '$.venue_order_id') = ?
 GROUP BY aggregate_id
"""


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    ensure_tables(connection)
    return connection


def _insert(connection: sqlite3.Connection, *, aggregate_id: str, payload_json: str) -> None:
    """Append one append-only lifecycle event with the table's real columns."""
    connection.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            event_hash, payload_json, payload_hash, source_authority,
            occurred_at, created_at, schema_version
        ) VALUES (
            :aggregate_event_id, :aggregate_id, 1, 'Reconciled',
            :event_hash, :payload_json, :payload_hash, 'explicit_reconcile',
            '2026-09-18T02:00:00+00:00', '2026-09-18T02:00:01+00:00', 1
        )
        """,
        {
            "aggregate_event_id": f"{aggregate_id}:1",
            "aggregate_id": aggregate_id,
            "event_hash": "e" * 64,
            "payload_json": payload_json,
            "payload_hash": "p" * 64,
        },
    )
    connection.commit()


def _index_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='edli_live_order_events'"
        )
    }


def test_both_recovery_predicates_are_indexed(conn):
    """Each side of the OR needs its own index for MULTI-INDEX OR to apply."""
    names = _index_names(conn)
    assert "idx_edli_live_order_events_command_id" in names
    assert "idx_edli_live_order_events_venue_order_id" in names


def test_the_recovery_lookup_plans_as_index_seeks_not_a_scan(conn):
    """The defect was the plan, not the row count: a SCAN cannot fit 100 ms."""
    plan = " ".join(
        str(row[-1])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN " + RECOVERY_SQL, (COMMAND_ID, VENUE_ORDER_ID)
        )
    )
    assert "MULTI-INDEX OR" in plan, plan
    assert "idx_edli_live_order_events_command_id" in plan, plan
    assert "idx_edli_live_order_events_venue_order_id" in plan, plan
    assert "SCAN edli_live_order_events" not in plan, plan


def test_the_lookup_still_finds_a_row_by_either_key(conn):
    """An index that changes the answer would be worse than a slow scan."""
    payload = json.dumps(
        {"command_id": COMMAND_ID, "venue_order_id": VENUE_ORDER_ID}
    )
    _insert(conn, aggregate_id="agg-1", payload_json=payload)
    by_both = conn.execute(RECOVERY_SQL, (COMMAND_ID, VENUE_ORDER_ID)).fetchall()
    assert by_both == [("agg-1", 1)]
    # Either key alone must still match, since the live predicate is an OR.
    assert conn.execute(RECOVERY_SQL, (COMMAND_ID, "absent")).fetchall() == [("agg-1", 1)]
    assert conn.execute(RECOVERY_SQL, ("absent", VENUE_ORDER_ID)).fetchall() == [
        ("agg-1", 1)
    ]
    assert conn.execute(RECOVERY_SQL, ("absent", "absent")).fetchall() == []


def test_rows_without_the_keys_are_left_out_of_the_partial_indexes(conn):
    """The indexes are partial, so payloads lacking the keys cost nothing."""
    _insert(conn, aggregate_id="agg-2", payload_json=json.dumps({"unrelated": 1}))
    assert conn.execute(RECOVERY_SQL, ("absent", "absent")).fetchall() == []


def test_ensure_tables_is_idempotent(conn):
    """Boot runs it every time; a second call must not raise or duplicate."""
    before = _index_names(conn)
    ensure_tables(conn)
    assert _index_names(conn) == before
