# Created: 2026-06-04
# Last reused/audited: 2026-06-05
# Authority basis: operator directive 2026-06-04 — prune superseded channel
#                  events (BEST_BID_ASK_CHANGED / BOOK_SNAPSHOT) from the active
#                  working set. Companion to tests/events/test_archive_expired_sweep.py
#                  (FSR per-city-tz sweep). Same append-only provenance contract:
#                  only opportunity_event_processing.processing_status is mutated.
"""RED→GREEN relationship antibody for the superseded-channel-event sweep.

The defect: fetch_pending JOINs ~1.7M pending channel-event rows every cycle.
1743 distinct token_ids each have ~990 pending BEST_BID_ASK_CHANGED events;
only the LATEST per (event_type, token_id) is actionable — every older one is
superseded state. The reactor rejects them all as NO_DIRECT_STALE_TRADE but still
pays the full JOIN/ORDER BY cost for every one.

Structural fix: mark processing row 'expired' for all but the latest
available_at per (event_type, token_id). Same append-only contract as the FSR
sweep — immutable opportunity_events rows never deleted.

These tests pin the cross-module invariant:

  (a) N price events for one token → only the latest survives 'pending'; N-1 → 'expired'.
  (b) The latest event for a token is NEVER archived (even if it has the same
      available_at as another event for a different token that IS archived).
  (c) Pending channel-event count drops; fetch_pending JOIN sees fewer rows.
  (d) Idempotent — second sweep at same state archives nothing new.
  (e) Fail-closed — unparseable/missing token_id keeps row 'pending'.
  (f) Events already in a terminal state (processed/dead_letter) are not touched.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.event_store import EventStore
from src.events.opportunity_event import MarketBookEventPayload, make_opportunity_event
from src.state.db import init_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class CaptureConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_sql: list[tuple[str, tuple]] = []

    def execute(self, sql, parameters=(), /):  # type: ignore[override]
        params = tuple(parameters) if isinstance(parameters, (list, tuple)) else parameters
        self.executed_sql.append((sql, params))
        return super().execute(sql, parameters)


def _world_conn(*, factory=sqlite3.Connection) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", factory=factory)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _plan_text(conn: sqlite3.Connection, sql: str, params: tuple) -> str:
    plan_rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return " ".join(
        (r["detail"] if isinstance(r, sqlite3.Row) else r[-1]) for r in plan_rows
    ).upper()


def _channel_event(
    event_type: str,
    token_id: str,
    condition_id: str,
    available_at: str,
    *,
    seq: int = 0,
):
    """Build a BEST_BID_ASK_CHANGED or BOOK_SNAPSHOT event for a specific token."""
    payload = MarketBookEventPayload(
        condition_id=condition_id,
        token_id=token_id,
        outcome_label="YES",
        event_type=event_type,
        quote_seen_at=available_at,
        best_bid=0.45 + seq * 0.001,
        best_ask=0.55 + seq * 0.001,
    )
    return make_opportunity_event(
        event_type=event_type,
        entity_key=f"{condition_id}:{token_id}:{seq}",
        source="market_channel",
        observed_at=available_at,
        available_at=available_at,
        received_at=available_at,
        payload=payload,
        priority=0,
    )


def _pending_count(conn: sqlite3.Connection, consumer: str = "edli_reactor_v1") -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM opportunity_event_processing "
        "WHERE consumer_name = ? AND processing_status = 'pending'",
        (consumer,),
    ).fetchone()[0]


def _status_of(conn: sqlite3.Connection, event_id: str, consumer: str = "edli_reactor_v1") -> str:
    row = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing "
        "WHERE consumer_name = ? AND event_id = ?",
        (consumer, event_id),
    ).fetchone()
    return row[0] if row else "MISSING"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_superseded_older_events_archived_only_latest_kept():
    """(a) For one token with N price events, only the latest available_at survives
    as 'pending'; all N-1 older ones are marked 'expired' (superseded)."""
    conn = _world_conn()
    store = EventStore(conn)

    token_id = "tok-AAA"
    cond_id = "0xcond1"

    # 5 events in chronological order — only the last should survive.
    events = [
        _channel_event(
            "BEST_BID_ASK_CHANGED", token_id, cond_id,
            f"2026-06-04T{10+i:02d}:00:00+00:00", seq=i,
        )
        for i in range(5)
    ]
    for ev in events:
        store.insert_or_ignore(ev)

    archived = store.archive_superseded_channel_events()

    assert archived == 4, f"4 superseded events should be archived; got {archived}"

    # The latest event (index 4) must remain pending.
    assert _status_of(conn, events[4].event_id) == "pending", (
        "latest event must stay pending"
    )
    # All older ones must be expired.
    for i in range(4):
        assert _status_of(conn, events[i].event_id) == "expired", (
            f"event {i} (older) must be 'expired' (superseded)"
        )


def test_latest_event_never_archived():
    """(b) The single latest event for a token is NEVER archived regardless of
    how many older events exist."""
    conn = _world_conn()
    store = EventStore(conn)

    token_id = "tok-BBB"
    latest = _channel_event(
        "BEST_BID_ASK_CHANGED", token_id, "0xcond2",
        "2026-06-04T20:00:00+00:00",
    )
    store.insert_or_ignore(latest)

    archived = store.archive_superseded_channel_events()
    assert archived == 0, "a single event (the latest by definition) must not be archived"
    assert _status_of(conn, latest.event_id) == "pending"


def test_multiple_tokens_independent_survival():
    """Each token's latest is preserved independently; older events across
    multiple tokens are each archived."""
    conn = _world_conn()
    store = EventStore(conn)

    tokens = ["tok-X", "tok-Y", "tok-Z"]
    latest_ids = []
    for tok in tokens:
        evs = [
            _channel_event(
                "BEST_BID_ASK_CHANGED", tok, f"0xcond-{tok}",
                f"2026-06-04T{10+j:02d}:00:00+00:00", seq=j,
            )
            for j in range(3)
        ]
        for ev in evs:
            store.insert_or_ignore(ev)
        latest_ids.append(evs[-1].event_id)

    archived = store.archive_superseded_channel_events()
    assert archived == 6, "2 older events × 3 tokens = 6 archived"

    for eid in latest_ids:
        assert _status_of(conn, eid) == "pending", "each token's latest must survive"


def test_scan_volume_drops_after_channel_sweep():
    """(c) After the sweep the pending-event count drops sharply; fetch_pending
    returns only the surviving events (latest per token)."""
    conn = _world_conn()
    store = EventStore(conn)

    # 4 tokens × 10 events each = 40 pending; sweep → 4 survivors.
    for t in range(4):
        for j in range(10):
            ev = _channel_event(
                "BEST_BID_ASK_CHANGED", f"tok-{t}", f"0xcond-{t}",
                f"2026-06-04T{j:02d}:00:00+00:00", seq=t * 10 + j,
            )
            store.insert_or_ignore(ev)

    before = _pending_count(conn)
    assert before == 40

    archived = store.archive_superseded_channel_events()
    assert archived == 36

    after = _pending_count(conn)
    assert after == 4

    # fetch_pending's WHERE gates on processing_status=pending so the JOIN is small.
    # We can't inspect the plan here, but the count is the proxy metric.
    assert after < before


def test_channel_sweep_idempotent():
    """(d) Running the sweep twice at the same state archives nothing on the second
    pass — the sweep is idempotent."""
    conn = _world_conn()
    store = EventStore(conn)

    for j in range(5):
        store.insert_or_ignore(
            _channel_event(
                "BEST_BID_ASK_CHANGED", "tok-idem", "0xcidem",
                f"2026-06-04T{j:02d}:00:00+00:00", seq=j,
            )
        )

    first = store.archive_superseded_channel_events()
    second = store.archive_superseded_channel_events()

    assert first == 4
    assert second == 0, "second pass must be a no-op (idempotent)"


def test_batch_limited_sweep_preserves_keeper_outside_batch():
    """The sweep may examine only a small oldest-row batch, but the keeper lookup
    must still consider the full active stream for each token. Otherwise the
    newest event outside the batch could be archived in a later pass or an older
    in-batch row could be incorrectly kept forever."""
    conn = _world_conn()
    store = EventStore(conn)

    events = []
    for j in range(8):
        event = _channel_event(
            "BEST_BID_ASK_CHANGED",
            "tok-batch",
            "0xcbatch",
            f"2026-06-04T{j:02d}:00:00+00:00",
            seq=j,
        )
        store.insert_or_ignore(event)
        events.append(event)

    first = store.archive_superseded_channel_events(batch_limit=3)

    assert first == 3
    assert _status_of(conn, events[-1].event_id) == "pending", (
        "latest keeper must be preserved even when it is outside the candidate batch"
    )
    for event in events[:3]:
        assert _status_of(conn, event.event_id) == "expired"

    second = store.archive_superseded_channel_events(batch_limit=3)

    assert second == 3
    assert _status_of(conn, events[-1].event_id) == "pending"


def test_missing_token_id_kept_active_failclosed():
    """(e) Fail-closed: an event whose payload has no parseable token_id is kept
    'pending' — archiving an unverifiable row would silently drop an active event."""
    conn = _world_conn()
    store = EventStore(conn)

    # Insert with a raw broken payload (no token_id key)
    from src.events.opportunity_event import OpportunityEvent, SCHEMA_VERSION
    import json
    from src.events.idempotency import stable_event_id, stable_idempotency_key, payload_hash, canonical_json

    broken_payload = {"event_type": "BEST_BID_ASK_CHANGED", "condition_id": "0xbroken"}
    payload_json = canonical_json(broken_payload)
    digest = payload_hash(broken_payload)
    idem = stable_idempotency_key("BEST_BID_ASK_CHANGED", "no-key", "test", "2026-06-04T00:00:00+00:00", digest)
    event_id = stable_event_id(idem)

    conn.execute(
        """
        INSERT OR IGNORE INTO opportunity_events
          (event_id, event_type, entity_key, source,
           observed_at, available_at, received_at,
           causal_snapshot_id, payload_hash, idempotency_key,
           priority, expires_at, payload_json, schema_version, created_at)
        VALUES (?,?,?,?,?,?,?,NULL,?,?,0,NULL,?,1,?)
        """,
        (event_id, "BEST_BID_ASK_CHANGED", "no-key", "test",
         "2026-06-04T00:00:00+00:00", "2026-06-04T00:00:00+00:00",
         "2026-06-04T00:00:00+00:00",
         digest, idem, payload_json,
         "2026-06-04T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO opportunity_event_processing "
        "(consumer_name, event_id, processing_status, attempt_count, updated_at) "
        "VALUES (?, ?, 'pending', 0, ?)",
        ("edli_reactor_v1", event_id, "2026-06-04T00:00:00+00:00"),
    )

    store.archive_superseded_channel_events()

    assert _status_of(conn, event_id) == "pending", (
        "an event with no parseable token_id must be kept active (fail-closed)"
    )


def test_already_terminal_events_not_touched():
    """(f) Events already in terminal states (processed / dead_letter) are not
    affected by the sweep — it only touches pending/processing rows."""
    conn = _world_conn()
    store = EventStore(conn)

    tok = "tok-terminal"
    old = _channel_event("BEST_BID_ASK_CHANGED", tok, "0xcterm", "2026-06-04T10:00:00+00:00")
    new = _channel_event("BEST_BID_ASK_CHANGED", tok, "0xcterm", "2026-06-04T11:00:00+00:00", seq=1)
    store.insert_or_ignore(old)
    store.insert_or_ignore(new)

    # Manually mark old as already processed (terminal).
    store.mark_processed(old.event_id)

    archived = store.archive_superseded_channel_events()
    assert archived == 0, "no row should be archived when older event is already terminal"
    assert _status_of(conn, new.event_id) == "pending"


def test_book_snapshot_events_also_swept():
    """BOOK_SNAPSHOT events obey the same superseded-keep-latest rule as
    BEST_BID_ASK_CHANGED — separate per (event_type, token_id) keys."""
    conn = _world_conn()
    store = EventStore(conn)

    tok = "tok-snap"
    for j in range(4):
        store.insert_or_ignore(
            _channel_event(
                "BOOK_SNAPSHOT", tok, "0xcsnap",
                f"2026-06-04T{j:02d}:00:00+00:00", seq=j,
            )
        )

    archived = store.archive_superseded_channel_events()
    assert archived == 3, "3 of 4 BOOK_SNAPSHOT events should be superseded"
    assert _pending_count(conn) == 1


def test_keeper_query_uses_channel_token_index():
    """RED->GREEN performance antibody: the keeper subquery in
    archive_superseded_channel_events must use idx_opportunity_events_channel_token
    (the expression index on event_type + json_extract(payload_json, '$.token_id')
    + available_at) rather than a full table SCAN.

    Without the index the GROUP BY over json_extract hits every row in
    opportunity_events -- confirmed 85.6 s at 1.78 M rows on live DB 2026-06-04.
    With the index the plan shows USING INDEX / SEARCH, collapsing to sub-second.

    Two assertions:
    (1) Structural: idx_opportunity_events_channel_token exists in sqlite_master.
        RED before the index DDL is added to ensure_table, GREEN after.
    (2) Planner: with realistic volume (20 tokens x 50 events = 1000 rows) +
        ANALYZE the planner uses that index not a full SCAN.  This proves the
        expression text is byte-identical to the query GROUP BY / WHERE terms.
    """
    conn = _world_conn()
    store = EventStore(conn)

    # --- assertion (1): structural existence ---
    index_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='opportunity_events'"
        ).fetchall()
    }
    assert "idx_opportunity_events_channel_token" in index_names, (
        "idx_opportunity_events_channel_token must be declared in "
        "opportunity_events_schema.ensure_table(); index is absent from sqlite_master."
    )

    # --- populate enough rows for ANALYZE statistics to tip the planner ---
    # 20 tokens x 50 events = 1000 rows; with ANALYZE the planner drives from
    # opportunity_events via the expression index rather than the PK-join path
    # that a tiny table would choose.
    for tok in range(20):
        for j in range(50):
            store.insert_or_ignore(
                _channel_event(
                    "BEST_BID_ASK_CHANGED",
                    f"tok-plan-{tok}",
                    f"0xcplan-{tok}",
                    f"2026-06-04T{j % 24:02d}:{j // 24:02d}:00+00:00",
                    seq=tok * 50 + j,
                )
            )
    conn.execute("ANALYZE")

    consumer = "edli_reactor_v1"
    channel_types = ("BEST_BID_ASK_CHANGED", "BOOK_SNAPSHOT", "NEW_MARKET_DISCOVERED")
    type_placeholders = ",".join("?" * len(channel_types))

    # --- assertion (2): planner uses the expression index ---
    plan_rows = conn.execute(
        f"""
        EXPLAIN QUERY PLAN
        SELECT e2.event_type,
               json_extract(e2.payload_json, '$.token_id') AS token_id,
               MAX(e2.available_at) AS max_available_at
        FROM opportunity_events e2
        JOIN opportunity_event_processing p2
          ON p2.event_id = e2.event_id
         AND p2.consumer_name = ?
        WHERE e2.event_type IN ({type_placeholders})
          AND p2.processing_status IN ('pending', 'processing')
          AND json_extract(e2.payload_json, '$.token_id') IS NOT NULL
        GROUP BY e2.event_type, json_extract(e2.payload_json, '$.token_id')
        """,
        (consumer, *channel_types),
    ).fetchall()

    # EXPLAIN QUERY PLAN columns: id, parent, notused, detail
    plan_text = " ".join(
        (r["detail"] if isinstance(r, sqlite3.Row) else r[-1]) for r in plan_rows
    ).upper()

    assert "IDX_OPPORTUNITY_EVENTS_CHANNEL_TOKEN" in plan_text, (
        f"keeper query must use idx_opportunity_events_channel_token after ANALYZE "
        f"(got: {plan_text!r}). The expression text in the DDL must be byte-identical "
        "to json_extract(payload_json, '$.token_id') in the GROUP BY / WHERE."
    )
    assert "SCAN E2" not in plan_text and "SCAN OPPORTUNITY_EVENTS" not in plan_text, (
        f"keeper query must not full-scan opportunity_events (got: {plan_text!r})"
    )


def test_candidate_query_uses_processing_status_index():
    """RED->GREEN performance antibody: the candidate-batch query must not full-scan
    opportunity_event_processing.

    Live regression 2026-06-05: the keeper lookup was index-backed, but the
    candidate CTE still planned as SCAN p on a ~2.7M-row processing table, pinning
    the EDLI reactor worker before process_pending could emit no-submit receipts.
    """
    conn = _world_conn(factory=CaptureConnection)
    store = EventStore(conn)
    for tok in range(20):
        for j in range(50):
            store.insert_or_ignore(
                _channel_event(
                    "BEST_BID_ASK_CHANGED",
                    f"tok-candidate-plan-{tok}",
                    f"0xcandidate-plan-{tok}",
                    f"2026-06-04T{j % 24:02d}:{j // 24:02d}:00+00:00",
                    seq=tok * 50 + j,
                )
            )
    conn.execute("ANALYZE")

    conn.executed_sql.clear()
    store.archive_superseded_channel_events()
    candidate_sql, candidate_params = next(
        (sql, params)
        for sql, params in conn.executed_sql
        if "SELECT e.event_id" in sql
        and "json_extract(e.payload_json, '$.token_id') AS token_id" in sql
        and "WITH candidate_rows" not in sql
    )

    plan_text = _plan_text(conn, candidate_sql, candidate_params)

    assert "IDX_OPPORTUNITY_EVENT_PROCESSING_STATUS" in plan_text, (
        f"candidate query must use active-status index, got: {plan_text!r}"
    )
    assert "SCAN P" not in plan_text, (
        f"candidate query must not full-scan opportunity_event_processing, got: {plan_text!r}"
    )


def test_fetch_pending_query_uses_processing_status_index():
    """The reactor's main fetch_pending query must also avoid SCAN p.

    The channel sweep can be fast while process_pending still starves if the
    final fetch query scans every historical processing row before ordering the
    active working set.
    """
    conn = _world_conn(factory=CaptureConnection)
    store = EventStore(conn)
    for tok in range(20):
        for j in range(50):
            store.insert_or_ignore(
                _channel_event(
                    "BEST_BID_ASK_CHANGED",
                    f"tok-fetch-plan-{tok}",
                    f"0xfetch-plan-{tok}",
                    f"2026-06-04T{j % 24:02d}:{j // 24:02d}:00+00:00",
                    seq=tok * 50 + j,
                )
            )
    conn.execute("ANALYZE")

    decision_time = "2026-06-05T00:00:00+00:00"

    conn.executed_sql.clear()
    store.fetch_pending(decision_time=decision_time, limit=90)
    fetch_sql, fetch_params = next(
        (sql, params)
        for sql, params in conn.executed_sql
        if "SELECT e.*" in sql and "FROM opportunity_event_processing" in sql
    )

    plan_text = _plan_text(conn, fetch_sql, fetch_params)

    assert "IDX_OPPORTUNITY_EVENT_PROCESSING_STATUS" in plan_text, (
        f"fetch_pending must use active-status index, got: {plan_text!r}"
    )
    assert "SCAN P" not in plan_text, (
        f"fetch_pending must not full-scan opportunity_event_processing, got: {plan_text!r}"
    )
