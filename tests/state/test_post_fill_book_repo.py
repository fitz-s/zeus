# Created: 2026-09-08
# Last reused/audited: 2026-09-08
# Authority basis: docs/operations/current/plans/hourly_capital_gains_improvement_loop.md
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.ingest.post_fill_book_observer import classify_source_fact
from src.state import post_fill_book_repo as repo
from src.state.schema.post_fill_book_observations_schema import ensure_table

NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


def _row(
    fact_id: int, venue_timestamp: str | None, *, state: str = "MATCHED"
) -> dict[str, object]:
    return {
        "trade_fact_id": fact_id,
        "command_id": "cmd",
        "trade_id": "trade",
        "venue_order_id": "order",
        "condition_id": "condition",
        "token_id": "token",
        "side": "BUY",
        "envelope_token_id": "token",
        "envelope_side": "BUY",
        "state": state,
        "venue_timestamp": venue_timestamp,
        "observed_at": "2026-09-08T12:01:00+00:00",
        "filled_size": "1",
        "fill_price": "0.5",
        "fee_paid_micro": 0,
        "tx_hash": "tx",
        "source": "REST",
        "raw_payload_hash": "a" * 64,
    }


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE venue_trade_facts (trade_fact_id INTEGER PRIMARY KEY)")
    ensure_table(db)
    repo.register_protocol(
        db,
        protocol_id="p",
        caller="test",
        horizon_seconds=60,
        window_seconds=30,
        clock=lambda: NOW,
    )
    return db


def test_missing_clock_is_lineage_then_later_revision_drains_one_request(conn):
    first = _row(1, None)
    second = _row(2, "2026-09-08T12:00:00+00:00")
    assert (
        repo.observe_source_facts(
            conn,
            protocol_id="p",
            rows=[first, second],
            observed_at=NOW.isoformat(),
            classify=classify_source_fact,
        )
        == 2
    )
    events = conn.execute(
        "SELECT event_type,reason FROM post_fill_book_observation_events ORDER BY event_id"
    ).fetchall()
    assert [tuple(x) for x in events] == [
        ("SOURCE_OBSERVED", "VENUE_TIMESTAMP_INVALID"),
        ("SOURCE_OBSERVED", "SCHEDULED"),
    ]
    request = conn.execute(
        "SELECT source_trade_fact_id, due_at FROM post_fill_book_requests"
    ).fetchone()
    assert tuple(request) == (2, "2026-09-08T12:01:00+00:00")
    assert (
        conn.execute(
            "SELECT last_trade_fact_id FROM post_fill_book_cursors WHERE protocol_id='p'"
        ).fetchone()[0]
        == 2
    )


def test_source_event_cursor_and_request_roll_back_together(conn):
    conn.execute(
        "CREATE TRIGGER abort_cursor BEFORE UPDATE ON post_fill_book_cursors BEGIN SELECT RAISE(ABORT,'boom'); END"
    )
    with pytest.raises(sqlite3.DatabaseError, match="boom"):
        repo.observe_source_facts(
            conn,
            protocol_id="p",
            rows=[_row(1, "2026-09-08T12:00:00+00:00")],
            observed_at=NOW.isoformat(),
            classify=classify_source_fact,
        )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM post_fill_book_observation_events"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM post_fill_book_requests").fetchone()[0] == 0
    )


def test_requests_and_protocols_are_immutable_and_capture_is_idempotent(conn):
    repo.observe_source_facts(
        conn,
        protocol_id="p",
        rows=[_row(1, "2026-09-08T12:00:00+00:00")],
        observed_at=NOW.isoformat(),
        classify=classify_source_fact,
    )
    request = conn.execute("SELECT request_id FROM post_fill_book_requests").fetchone()[
        0
    ]
    fields = {
        "fetch_started_at": "2026-09-08T12:01:00+00:00",
        "fetch_finished_at": "2026-09-08T12:01:01+00:00",
        "endpoint": "https://clob.polymarket.com/book",
        "http_status": 200,
        "raw_body": b'{"asset_id":"token","market":"condition","bids":[],"asks":[]}',
        "provider_asset_id": "token",
        "provider_market": "condition",
    }
    import hashlib

    fields["raw_body_sha256"] = hashlib.sha256(fields["raw_body"]).hexdigest()
    repo.append_result(
        conn,
        request_id=request,
        protocol_id="p",
        event_type="CAPTURED",
        reason="ok",
        observed_at=fields["fetch_finished_at"],
        **fields,
    )
    repo.append_result(
        conn,
        request_id=request,
        protocol_id="p",
        event_type="CAPTURED",
        reason="again",
        observed_at=fields["fetch_finished_at"],
        **fields,
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM post_fill_book_observation_events WHERE event_type='CAPTURED'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute(
            "UPDATE post_fill_book_requests SET token_id='other' WHERE request_id=?",
            (request,),
        )


def test_schema_and_repo_savepoints_do_not_commit_an_outer_transaction(conn):
    conn.execute("CREATE TABLE outer_sentinel (value TEXT)")
    conn.execute("BEGIN")
    conn.execute("INSERT INTO outer_sentinel VALUES ('uncommitted')")
    ensure_table(conn)
    assert conn.in_transaction
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT COUNT(*) FROM outer_sentinel").fetchone()[0] == 0


def test_active_global_due_queue_preserves_active_slot_from_expired_backlog(conn):
    repo.register_protocol(
        conn,
        protocol_id="p2",
        caller="test",
        horizon_seconds=60,
        window_seconds=30,
        clock=lambda: NOW,
    )
    conn.executemany(
        """INSERT INTO post_fill_book_requests
        (protocol_id,command_id,trade_id,source_trade_fact_id,venue_order_id,
         condition_id,token_id,side,source_state,filled_size,fill_price,created_at,
         due_at,window_end_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                "p",
                "expired-command",
                "expired-trade",
                11,
                "expired-order",
                "condition",
                "token",
                "BUY",
                "CONFIRMED",
                "1",
                "0.5",
                NOW.isoformat(),
                "2026-09-08T12:00:00+00:00",
                "2026-09-08T12:00:30+00:00",
            ),
            (
                "p2",
                "active-command",
                "active-trade",
                12,
                "active-order",
                "condition",
                "token",
                "BUY",
                "CONFIRMED",
                "1",
                "0.5",
                NOW.isoformat(),
                "2026-09-08T12:00:00+00:00",
                "2026-09-08T12:02:00+00:00",
            ),
        ],
    )
    active = repo.active_due_requests(
        conn,
        now="2026-09-08T12:01:00+00:00",
        limit=1,
        protocol_ids=["p", "p2"],
    )
    expired = repo.expired_due_requests(
        conn,
        now="2026-09-08T12:01:00+00:00",
        limit=1,
        protocol_ids=["p", "p2"],
    )
    assert [row["protocol_id"] for row in active] == ["p2"]
    assert [row["protocol_id"] for row in expired] == ["p"]
