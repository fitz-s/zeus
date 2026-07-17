# Created: 2026-05-24
# Last reused/audited: 2026-07-17
# Authority basis: EDLI v1 implementation prompt §7 EventStore acceptance A01-A04.
from __future__ import annotations

import dataclasses
import inspect
import json
import sqlite3

import pytest

from src.events.event_store import EventStore, EventStoreSchemaError
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    MarketBookEventPayload,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)
from src.state.db import init_schema


def _payload(
    snapshot_id: str = "snap-1",
    *,
    city: str = "Chicago",
    target_date: str = "2026-05-24",
    metric: str = "high",
) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city=city,
        target_date=target_date,
        metric=metric,
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )


def _event(snapshot_id: str, priority: int, available_at: str, received_at: str):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-05-24|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=_payload(snapshot_id),
        priority=priority,
    )


def _day0_event(
    *,
    city: str = "Chicago",
    target_date: str = "2026-05-24",
    metric: str = "high",
    available_at: str = "2026-05-24T04:16:00+00:00",
):
    payload = Day0ExtremeUpdatedPayload(
        city=city,
        target_date=target_date,
        metric=metric,
        settlement_source="aviationweather_metar",
        station_id="KORD",
        observation_time="2026-05-24T04:15:00+00:00",
        observation_available_at=available_at,
        raw_value=28.0,
        rounded_value=28,
        high_so_far=28.0 if metric == "high" else None,
        low_so_far=28.0 if metric == "low" else None,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
    )
    return make_day0_extreme_updated_event(
        entity_key=f"{city}|{target_date}|{metric}|KORD",
        source="day0_extreme_updated_trigger",
        observed_at=payload.observation_time,
        received_at=available_at,
        payload=payload,
        causal_snapshot_id="day0-authority-1",
        priority=100,
    )


def _insert_no_trade_regret(
    conn: sqlite3.Connection,
    event,
    *,
    regret_event_id: str | None = None,
    created_at: str = "2026-05-24T04:20:00+00:00",
    rejection_reason: str,
) -> None:
    payload = json.loads(event.payload_json)
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            executable_snapshot_id, created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', ?, 'NO_EDGE',
                  ?, ?, ?, ?, ?, ?, NULL, ?, 1)
        """,
        (
            regret_event_id or ("regret-" + event.event_id),
            event.event_id,
            rejection_reason,
            created_at,
            str(payload.get("city") or ""),
            str(payload.get("target_date") or ""),
            str(payload.get("metric") or ""),
            "|".join(
                (
                    str(payload.get("city") or ""),
                    str(payload.get("target_date") or ""),
                    str(payload.get("metric") or ""),
                )
            ),
            event.causal_snapshot_id,
            created_at,
        ),
    )


def _channel_event(event_type: str = "BEST_BID_ASK_CHANGED"):
    available_at = "2026-05-24T04:15:00+00:00"
    payload = MarketBookEventPayload(
        condition_id="0xcondition",
        token_id="token-yes",
        outcome_label="YES",
        event_type=event_type,
        quote_seen_at=available_at,
        best_bid=0.44,
        best_ask=0.56,
    )
    return make_opportunity_event(
        event_type=event_type,
        entity_key=f"0xcondition:token-yes:{event_type}",
        source="market_channel",
        observed_at=available_at,
        available_at=available_at,
        received_at=available_at,
        payload=payload,
        priority=0,
    )


def _fsr_entity_event(
    entity_key: str,
    snapshot_id: str,
    available_at: str,
    received_at: str,
    *,
    city: str = "Chicago",
    target_date: str = "2026-05-24",
    metric: str = "high",
    source_run_completeness_status: str = "COMPLETE",
    coverage_completeness_status: str | None = None,
    coverage_readiness_status: str | None = None,
    member_count: int = 51,
    expected_members: int = 51,
):
    payload = _payload(
        snapshot_id,
        city=city,
        target_date=target_date,
        metric=metric,
    )
    payload = dataclasses.replace(
        payload,
        member_count=member_count,
        expected_members=expected_members,
    )
    if source_run_completeness_status != "COMPLETE":
        payload = dataclasses.replace(
            payload,
            completeness_status=coverage_completeness_status or "PARTIAL_ALLOWED",
            source_run_completeness_status=source_run_completeness_status,
            coverage_completeness_status=coverage_completeness_status or source_run_completeness_status,
            coverage_readiness_status=coverage_readiness_status or "NOT_ELIGIBLE",
        )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=entity_key,
        source="cycle",
        observed_at=available_at,
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=50,
    )


def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _insert_no_value_regret(
    conn: sqlite3.Connection,
    event,
    *,
    created_at: str = "2026-05-24T04:18:00+00:00",
    rejection_reason: str = "TRADE_SCORE_NON_POSITIVE",
    executable_snapshot_id: str | None = None,
) -> None:
    payload = json.loads(event.payload_json)
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            executable_snapshot_id, created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', ?, 'NO_EDGE',
                  ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            "regret-" + event.event_id,
            event.event_id,
            rejection_reason,
            created_at,
            str(payload.get("city") or ""),
            str(payload.get("target_date") or ""),
            str(payload.get("metric") or ""),
            "|".join(
                (
                    str(payload.get("city") or ""),
                    str(payload.get("target_date") or ""),
                    str(payload.get("metric") or ""),
                )
            ),
            event.causal_snapshot_id,
            executable_snapshot_id,
            created_at,
        ),
    )


def test_insert_or_ignore_duplicate():
    conn = _world_conn()
    store = EventStore(conn)
    event = _event("snap-1", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    assert store.insert_or_ignore(event) is True
    assert store.insert_or_ignore(event) is False
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM opportunity_event_processing").fetchone()[0] == 1


def test_insert_or_ignore_repairs_missing_processing_row_for_existing_event():
    conn = _world_conn()
    store = EventStore(conn)
    event = _event("snap-1", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    assert store.insert_or_ignore(event) is True
    conn.execute(
        "DELETE FROM opportunity_event_processing WHERE consumer_name = ? AND event_id = ?",
        (store.consumer_name, event.event_id),
    )
    conn.commit()

    assert store.insert_or_ignore(event) is False

    row = conn.execute(
        """
        SELECT processing_status, attempt_count, processed_at, last_error
          FROM opportunity_event_processing
         WHERE consumer_name = ? AND event_id = ?
        """,
        (store.consumer_name, event.event_id),
    ).fetchone()
    assert dict(row) == {
        "processing_status": "pending",
        "attempt_count": 0,
        "processed_at": None,
        "last_error": None,
    }


def test_repair_missing_processing_rows_backfills_decision_events_only():
    conn = _world_conn()
    store = EventStore(conn)
    decision = _event(
        "snap-1",
        0,
        "2026-05-24T04:15:00+00:00",
        "2026-05-24T04:16:00+00:00",
    )
    channel = _channel_event("BOOK_SNAPSHOT")
    assert store.insert_or_ignore(decision) is True
    assert store.insert_or_ignore(channel) is True
    conn.execute("DELETE FROM opportunity_event_processing")
    conn.commit()

    repaired = store.repair_missing_processing_rows(
        decision_time="2026-05-24T05:00:00+00:00",
        batch_limit=10,
    )

    assert repaired == 1
    rows = {
        row["event_id"]: row["processing_status"]
        for row in conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        )
    }
    assert rows == {decision.event_id: "pending"}


def test_repair_missing_processing_rows_skips_historical_rows():
    conn = _world_conn()
    store = EventStore(conn)
    recent = _event(
        "snap-recent",
        0,
        "2026-05-26T04:15:00+00:00",
        "2026-05-26T04:16:00+00:00",
    )
    historical = _event(
        "snap-historical",
        0,
        "2026-05-24T04:15:00+00:00",
        "2026-05-24T04:16:00+00:00",
    )
    assert store.insert_or_ignore(recent) is True
    assert store.insert_or_ignore(historical) is True
    conn.execute("DELETE FROM opportunity_event_processing")
    conn.commit()

    repaired = store.repair_missing_processing_rows(
        decision_time="2026-05-27T05:00:00+00:00",
        batch_limit=10,
    )

    assert repaired == 1
    rows = {
        row["event_id"]: row["processing_status"]
        for row in conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        )
    }
    assert rows == {recent.event_id: "pending"}


def test_requeue_processed_day0_entries_paused_when_pause_cleared():
    conn = _world_conn()
    store = EventStore(conn)
    event = _day0_event()
    store.insert_or_ignore(event)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = 'processed',
               processed_at = ?,
               updated_at = ?
         WHERE consumer_name = ? AND event_id = ?
        """,
        (
            "2026-05-24T04:20:00+00:00",
            "2026-05-24T04:20:00+00:00",
            store.consumer_name,
            event.event_id,
        ),
    )
    _insert_no_trade_regret(
        conn,
        event,
        created_at="2026-05-24T04:20:00+00:00",
        rejection_reason=(
            "EVENT_BOUND_ALL_CANDIDATES_REJECTED:strategy_policy:"
            "STRATEGY_POLICY_GATED:settlement_capture:sources=hard_safety:pause_entries"
        ),
    )

    assert store.requeue_processed_day0_entries_paused(
        decision_time="2026-05-24T10:00:00+00:00",
        batch_limit=10,
    ) == 1
    row = conn.execute(
        """
        SELECT processing_status, processed_at, last_error
          FROM opportunity_event_processing
         WHERE consumer_name = ? AND event_id = ?
        """,
        (store.consumer_name, event.event_id),
    ).fetchone()
    assert row["processing_status"] == "pending"
    assert row["processed_at"] is None
    assert row["last_error"] == "RECOVERED_DAY0_ENTRIES_PAUSED"


def test_requeue_processed_day0_entries_paused_requires_latest_pause_and_open_local_day():
    conn = _world_conn()
    store = EventStore(conn)
    latest_non_pause_event = _day0_event(available_at="2026-05-24T04:16:00+00:00")
    past_event = _day0_event(available_at="2026-05-24T04:17:00+00:00")
    store.insert_or_ignore(latest_non_pause_event)
    store.insert_or_ignore(past_event)
    for event in (latest_non_pause_event, past_event):
        conn.execute(
            """
            UPDATE opportunity_event_processing
               SET processing_status = 'processed',
                   processed_at = ?,
                   updated_at = ?
             WHERE consumer_name = ? AND event_id = ?
            """,
            (
                "2026-05-24T04:20:00+00:00",
                "2026-05-24T04:20:00+00:00",
                store.consumer_name,
                event.event_id,
            ),
        )
    _insert_no_trade_regret(
        conn,
        latest_non_pause_event,
        created_at="2026-05-24T04:20:00+00:00",
        rejection_reason="EVENT_BOUND_ALL_CANDIDATES_REJECTED:entries_paused:operator",
    )
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            executable_snapshot_id, created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', ?, 'NO_EDGE',
                  ?, 'Chicago', '2026-05-24', 'high', 'Chicago|2026-05-24|high',
                  ?, NULL, ?, 1)
        """,
        (
            "regret-later-" + latest_non_pause_event.event_id,
            latest_non_pause_event.event_id,
            "EVENT_BOUND_ALL_CANDIDATES_REJECTED:capital_efficiency_lcb_ev",
            "2026-05-24T04:25:00+00:00",
            latest_non_pause_event.causal_snapshot_id,
            "2026-05-24T04:25:00+00:00",
        ),
    )
    _insert_no_trade_regret(
        conn,
        past_event,
        created_at="2026-05-24T04:20:00+00:00",
        rejection_reason="EVENT_BOUND_ALL_CANDIDATES_REJECTED:entries_paused:operator",
    )

    assert store.requeue_processed_day0_entries_paused(
        decision_time="2026-05-26T10:00:00+00:00",
        batch_limit=10,
    ) == 0
    rows = conn.execute(
        """
        SELECT event_id, processing_status
          FROM opportunity_event_processing
         WHERE consumer_name = ?
        """,
        (store.consumer_name,),
    ).fetchall()
    assert {row["event_id"]: row["processing_status"] for row in rows} == {
        latest_non_pause_event.event_id: "processed",
        past_event.event_id: "processed",
    }


@pytest.mark.parametrize(
    "event_type",
    ["BEST_BID_ASK_CHANGED", "BOOK_SNAPSHOT", "NEW_MARKET_DISCOVERED"],
)
def test_channel_cache_events_are_immutable_inputs_not_pending_reactor_work(event_type: str):
    conn = _world_conn()
    store = EventStore(conn)
    event = _channel_event(event_type)

    assert store.insert_or_ignore(event) is True

    event_row = conn.execute(
        "SELECT event_type, payload_json FROM opportunity_events WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    processing_row_count = conn.execute(
        """
        SELECT COUNT(*)
          FROM opportunity_event_processing
         WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()[0]

    assert event_row["event_type"] == event_type
    assert json.loads(event_row["payload_json"])["token_id"] == "token-yes"
    assert processing_row_count == 0
    assert store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00") == []


def test_processing_state_separate_from_event_row():
    conn = _world_conn()
    store = EventStore(conn)
    event = _event("snap-1", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    store.insert_or_ignore(event)
    assert store.claim(event.event_id, claimed_at="2026-05-24T04:17:00+00:00") is True
    row = conn.execute(
        "SELECT processing_status, attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert dict(row) == {"processing_status": "processing", "attempt_count": 1}
    assert conn.execute("SELECT payload_hash FROM opportunity_events WHERE event_id = ?", (event.event_id,)).fetchone()[0] == event.payload_hash


def test_archive_orphan_processing_rows_expires_only_rows_without_event_provenance():
    conn = _world_conn()
    store = EventStore(conn)
    live_event = _event(
        "snap-live",
        0,
        "2026-05-24T04:15:00+00:00",
        "2026-05-24T04:16:00+00:00",
    )
    store.insert_or_ignore(live_event)
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count, updated_at
        ) VALUES (?, ?, 'pending', 2, ?)
        """,
        ("edli_reactor_v1", "missing-event-row", "2026-05-24T04:16:30+00:00"),
    )
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count, claimed_at, updated_at
        ) VALUES (?, ?, 'processing', 1, ?, ?)
        """,
        (
            "edli_reactor_v1",
            "missing-processing-row",
            "2026-05-24T04:16:30+00:00",
            "2026-05-24T04:16:30+00:00",
        ),
    )

    assert store.archive_orphan_processing_rows(batch_limit=10) == 2

    rows = dict(
        conn.execute(
            """
            SELECT event_id, processing_status || ':' || COALESCE(last_error, '')
              FROM opportunity_event_processing
             WHERE event_id IN (?, ?, ?)
            """,
            (live_event.event_id, "missing-event-row", "missing-processing-row"),
        ).fetchall()
    )
    assert rows[live_event.event_id] == "pending:"
    assert rows["missing-event-row"] == "expired:ORPHAN_EVENT_ROW_MISSING"
    assert rows["missing-processing-row"] == "expired:ORPHAN_EVENT_ROW_MISSING"


def test_archive_orphan_processing_rows_avoids_live_antijoin_scan():
    src = inspect.getsource(EventStore.archive_orphan_processing_rows)
    assert "LEFT JOIN opportunity_events" not in src
    assert "WHERE event_id IN" in src


def test_world_conn_required_for_event_tables():
    conn = _world_conn()
    store = EventStore(conn)
    assert store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00") == []


def test_trade_conn_wrong_db_fails_loud():
    conn = sqlite3.connect(":memory:")
    store = EventStore(conn)
    event = _event("snap-1", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    with pytest.raises(EventStoreSchemaError, match="world DB|EDLI event tables"):
        store.insert_or_ignore(event)


def test_replay_order_deterministic():
    conn = _world_conn()
    store = EventStore(conn)
    events = [
        _event("snap-low-priority", 0, "2026-05-24T04:00:00+00:00", "2026-05-24T04:01:00+00:00"),
        _event("snap-newer", 5, "2026-05-24T04:10:00+00:00", "2026-05-24T04:11:00+00:00"),
        _event("snap-older", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00"),
    ]
    for event in events:
        store.insert_or_ignore(event)
    ordered = store.replay_events()
    assert [event.causal_snapshot_id for event in ordered] == [
        "snap-older",
        "snap-newer",
        "snap-low-priority",
    ]
    from src.events.replay import replay_all_events

    assert replay_all_events(store).event_ids == tuple(event.event_id for event in ordered)


def test_pending_fetch_excludes_future_available_at():
    conn = _world_conn()
    store = EventStore(conn)
    future = _event("snap-future", 5, "2026-05-24T06:00:00+00:00", "2026-05-24T04:11:00+00:00")
    ready = _event("snap-ready", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00")
    store.insert_or_ignore(future)
    store.insert_or_ignore(ready)
    ordered = store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00")
    assert [event.causal_snapshot_id for event in ordered] == ["snap-ready"]


def test_pending_fetch_excludes_future_received_at():
    conn = _world_conn()
    store = EventStore(conn)
    future_received = _event("snap-received-future", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T05:30:00+00:00")
    ready = _event("snap-ready", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00")
    store.insert_or_ignore(future_received)
    store.insert_or_ignore(ready)
    ordered = store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00")
    assert [event.causal_snapshot_id for event in ordered] == ["snap-ready"]


def test_fetch_pending_prioritizes_fresh_fsr_before_retry_debt_same_target():
    conn = _world_conn()
    store = EventStore(conn)
    old_retry = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-old",
        "snap-old-retry",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
    )
    fresh_redecision = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-new",
        "snap-fresh-redecision",
        "2026-05-24T04:20:00+00:00",
        "2026-05-24T04:21:00+00:00",
    )
    store.insert_or_ignore(old_retry)
    store.insert_or_ignore(fresh_redecision)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET attempt_count = 3
         WHERE event_id = ?
        """,
        (old_retry.event_id,),
    )

    ordered = store.fetch_pending(
        decision_time="2026-05-24T05:00:00+00:00",
        limit=2,
    )

    assert [event.event_id for event in ordered] == [
        fresh_redecision.event_id,
        old_retry.event_id,
    ]


def test_archive_superseded_forecast_snapshot_events_keeps_latest_per_family():
    conn = _world_conn()
    store = EventStore(conn)
    entity_key = "Chicago|2026-05-24|high|source-run-1"
    older = _fsr_entity_event(
        entity_key, "snap-old", "2026-05-24T04:00:00+00:00", "2026-05-24T04:01:00+00:00"
    )
    newer = _fsr_entity_event(
        entity_key, "snap-new", "2026-05-24T04:10:00+00:00", "2026-05-24T04:11:00+00:00"
    )
    other = _fsr_entity_event(
        "Denver|2026-05-24|high|source-run-1",
        "snap-other",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
        city="Denver",
    )
    for event in (older, newer, other):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[older.event_id] == "expired"
    assert rows[newer.event_id] == "pending"
    assert rows[other.event_id] == "pending"


def test_archive_superseded_forecast_snapshot_events_crosses_source_runs():
    conn = _world_conn()
    store = EventStore(conn)
    older = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-1",
        "snap-run-1",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
    )
    newer = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-2",
        "snap-run-2",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
    )
    for event in (older, newer):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[older.event_id] == "expired"
    assert rows[newer.event_id] == "pending"


def test_archive_superseded_forecast_snapshot_events_capped_batch_checks_newer_tail():
    conn = _world_conn()
    store = EventStore(conn)
    events = [
        _fsr_entity_event(
            f"Chicago|2026-05-24|high|source-run-{index}",
            f"snap-{index}",
            f"2026-05-24T04:{index:02d}:00+00:00",
            f"2026-05-24T04:{index:02d}:30+00:00",
        )
        for index in range(3)
    ]
    for event in events:
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events(batch_limit=2)

    assert archived == 2
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[events[0].event_id] == "expired"
    assert rows[events[1].event_id] == "expired"
    assert rows[events[2].event_id] == "pending"


def test_archive_superseded_forecast_snapshot_events_fallback_keeps_entity_keeper():
    conn = _world_conn()
    store = EventStore(conn)
    entity_key = "Chicago|2026-05-24|high|source-run-1"
    older = _fsr_entity_event(
        entity_key,
        "snap-missing-family-old",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
        city="",
    )
    newer = _fsr_entity_event(
        entity_key,
        "snap-missing-family-new",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        city="",
    )
    for event in (older, newer):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[older.event_id] == "expired"
    assert rows[newer.event_id] == "pending"


def test_archive_superseded_forecast_snapshot_events_keeps_complete_over_newer_partial():
    conn = _world_conn()
    store = EventStore(conn)
    entity_key = "Chicago|2026-05-24|high|source-run-1"
    complete = _fsr_entity_event(
        entity_key, "snap-complete", "2026-05-24T04:00:00+00:00", "2026-05-24T04:01:00+00:00"
    )
    partial = _fsr_entity_event(
        entity_key,
        "snap-partial",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        source_run_completeness_status="PARTIAL_ALLOWED",
    )
    for event in (complete, partial):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[complete.event_id] == "pending"
    assert rows[partial.event_id] == "expired"


def test_archive_superseded_forecast_snapshot_events_keeps_window_complete_source_partial():
    conn = _world_conn()
    store = EventStore(conn)
    entity_key = "Chicago|2026-05-24|high|source-run-1"
    older_incomplete = _fsr_entity_event(
        entity_key,
        "snap-coverage-partial",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
        source_run_completeness_status="PARTIAL",
        coverage_completeness_status="PARTIAL",
        coverage_readiness_status="NOT_ELIGIBLE",
    )
    window_complete = _fsr_entity_event(
        entity_key,
        "snap-window-complete",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        source_run_completeness_status="PARTIAL",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    for event in (older_incomplete, window_complete):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[older_incomplete.event_id] == "expired"
    assert rows[window_complete.event_id] == "pending"


def test_archive_invalid_forecast_snapshot_events_expires_live_carrier_count_mismatch():
    conn = _world_conn()
    store = EventStore(conn)
    invalid = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-bad",
        "snap-invalid-carrier",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        member_count=0,
        expected_members=51,
    )
    valid = _fsr_entity_event(
        "Denver|2026-05-24|high|source-run-good",
        "snap-valid-carrier",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        city="Denver",
        member_count=3,
        expected_members=3,
    )
    for event in (invalid, valid):
        store.insert_or_ignore(event)

    archived = store.archive_invalid_forecast_snapshot_events()

    assert archived == 1
    rows = {
        event_id: (status, last_error)
        for event_id, status, last_error in conn.execute(
            "SELECT event_id, processing_status, last_error FROM opportunity_event_processing"
        ).fetchall()
    }
    assert rows[invalid.event_id] == ("expired", "INVALID_FORECAST_SNAPSHOT_CARRIER_COUNTS")
    assert rows[valid.event_id] == ("pending", None)


def test_archive_superseded_forecast_snapshot_events_keeps_valid_carrier_over_newer_invalid():
    conn = _world_conn()
    store = EventStore(conn)
    valid_older = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-good",
        "snap-valid-older",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
        member_count=3,
        expected_members=3,
    )
    invalid_newer = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-bad",
        "snap-invalid-newer",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        member_count=0,
        expected_members=51,
    )
    for event in (valid_older, invalid_newer):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[valid_older.event_id] == "pending"
    assert rows[invalid_newer.event_id] == "expired"


def test_archive_recent_no_value_refuted_events_expires_queued_fsr_from_redecision_refutation():
    conn = _world_conn()
    store = EventStore(conn)
    prior_redecision = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Chicago|2026-05-24|high|snap-same",
        source="edli_redecision:screen",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:10:00+00:00",
        received_at="2026-05-24T04:11:00+00:00",
        causal_snapshot_id="snap-same",
        payload=_payload("snap-same"),
        priority=50,
    )
    queued_fsr = _fsr_entity_event(
        "Chicago|2026-05-24|high|snap-same",
        "snap-same",
        "2026-05-24T04:12:00+00:00",
        "2026-05-24T04:12:30+00:00",
    )
    for event in (prior_redecision, queued_fsr):
        store.insert_or_ignore(event)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = 'processed'
         WHERE event_id = ?
        """,
        (prior_redecision.event_id,),
    )
    _insert_no_value_regret(conn, prior_redecision)

    archived = store.archive_recent_no_value_refuted_events(
        decision_time="2026-05-24T05:20:00+00:00"
    )

    assert archived == 1
    rows = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            """
            SELECT event_id, processing_status, last_error
              FROM opportunity_event_processing
            """
        ).fetchall()
    }
    assert rows[prior_redecision.event_id][0] == "processed"
    assert rows[queued_fsr.event_id][0] == "expired"
    assert rows[queued_fsr.event_id][1].startswith(
        "RECENT_NO_VALUE_REFUTATION:payload_hash:"
    )


def test_archive_recent_no_value_refuted_events_keeps_queued_redecision_live():
    conn = _world_conn()
    store = EventStore(conn)
    prior_redecision = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Chicago|2026-05-24|high|snap-same",
        source="edli_redecision:screen",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:10:00+00:00",
        received_at="2026-05-24T04:11:00+00:00",
        causal_snapshot_id="snap-same",
        payload=_payload("snap-same"),
        priority=50,
    )
    queued_redecision = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Chicago|2026-05-24|high|snap-same-refresh",
        source="edli_redecision:screen",
        observed_at="2026-05-24T04:12:00+00:00",
        available_at="2026-05-24T04:12:00+00:00",
        received_at="2026-05-24T04:12:30+00:00",
        causal_snapshot_id="snap-same",
        payload=_payload("snap-same"),
        priority=50,
    )
    for event in (prior_redecision, queued_redecision):
        store.insert_or_ignore(event)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = 'processed'
         WHERE event_id = ?
        """,
        (prior_redecision.event_id,),
    )
    _insert_no_value_regret(conn, prior_redecision)

    archived = store.archive_recent_no_value_refuted_events(
        decision_time="2026-05-24T05:20:00+00:00"
    )

    assert archived == 0
    assert (
        conn.execute(
            """
            SELECT processing_status
              FROM opportunity_event_processing
             WHERE event_id = ?
            """,
            (queued_redecision.event_id,),
        ).fetchone()[0]
        == "pending"
    )


def test_archive_recent_no_value_refuted_events_keeps_price_conditioned_regret_live():
    conn = _world_conn()
    store = EventStore(conn)
    prior_redecision = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Chicago|2026-05-24|high|snap-priced",
        source="edli_redecision:screen",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:10:00+00:00",
        received_at="2026-05-24T04:11:00+00:00",
        causal_snapshot_id="snap-priced",
        payload=_payload("snap-priced"),
        priority=50,
    )
    queued_fsr = _fsr_entity_event(
        "Chicago|2026-05-24|high|snap-priced",
        "snap-priced",
        "2026-05-24T04:12:00+00:00",
        "2026-05-24T04:12:30+00:00",
    )
    for event in (prior_redecision, queued_fsr):
        store.insert_or_ignore(event)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = 'processed'
         WHERE event_id = ?
        """,
        (prior_redecision.event_id,),
    )
    _insert_no_value_regret(
        conn,
        prior_redecision,
        executable_snapshot_id="ems2-old-price",
    )

    archived = store.archive_recent_no_value_refuted_events(
        decision_time="2026-05-24T05:20:00+00:00"
    )

    assert archived == 0
    assert (
        conn.execute(
            """
            SELECT processing_status
              FROM opportunity_event_processing
             WHERE event_id = ?
            """,
            (queued_fsr.event_id,),
        ).fetchone()[0]
        == "pending"
    )


def test_archive_recent_no_value_refuted_events_ignores_future_refutation():
    conn = _world_conn()
    store = EventStore(conn)
    prior_redecision = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Chicago|2026-05-24|high|snap-future",
        source="edli_redecision:screen",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:10:00+00:00",
        received_at="2026-05-24T04:11:00+00:00",
        causal_snapshot_id="snap-future",
        payload=_payload("snap-future"),
        priority=50,
    )
    queued_fsr = _fsr_entity_event(
        "Chicago|2026-05-24|high|snap-future",
        "snap-future",
        "2026-05-24T04:12:00+00:00",
        "2026-05-24T04:12:30+00:00",
    )
    for event in (prior_redecision, queued_fsr):
        store.insert_or_ignore(event)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = 'processed'
         WHERE event_id = ?
        """,
        (prior_redecision.event_id,),
    )
    _insert_no_value_regret(
        conn,
        prior_redecision,
        created_at="2026-05-24T06:00:00+00:00",
    )

    archived = store.archive_recent_no_value_refuted_events(
        decision_time="2026-05-24T05:20:00+00:00"
    )

    assert archived == 0
    assert (
        conn.execute(
            """
            SELECT processing_status
              FROM opportunity_event_processing
             WHERE event_id = ?
            """,
            (queued_fsr.event_id,),
        ).fetchone()[0]
        == "pending"
    )


def test_archive_recent_no_value_refuted_events_keeps_day0_separate_from_forecast_refutation():
    from src.events.opportunity_event import Day0ExtremeUpdatedPayload

    conn = _world_conn()
    store = EventStore(conn)
    prior_forecast = _fsr_entity_event(
        "Chicago|2026-05-24|high|snap-day0",
        "snap-day0",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:10:30+00:00",
    )
    day0 = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Chicago|2026-05-24|high|82",
        source="day0",
        observed_at="2026-05-24T04:12:00+00:00",
        available_at="2026-05-24T04:12:00+00:00",
        received_at="2026-05-24T04:12:30+00:00",
        causal_snapshot_id="snap-day0",
        payload=Day0ExtremeUpdatedPayload(
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            settlement_source="wu_icao_history",
            station_id="KORD",
            observation_time="2026-05-24T04:00:00+00:00",
            observation_available_at="2026-05-24T04:12:00+00:00",
            raw_value=82.0,
            rounded_value=82,
            high_so_far=82.0,
        ),
        priority=20,
    )
    for event in (prior_forecast, day0):
        store.insert_or_ignore(event)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = 'processed'
         WHERE event_id = ?
        """,
        (prior_forecast.event_id,),
    )
    _insert_no_value_regret(conn, prior_forecast)

    archived = store.archive_recent_no_value_refuted_events(
        decision_time="2026-05-24T04:20:00+00:00"
    )

    assert archived == 0
    assert (
        conn.execute(
            """
            SELECT processing_status
              FROM opportunity_event_processing
             WHERE event_id = ?
            """,
            (day0.event_id,),
        ).fetchone()[0]
        == "pending"
    )


def test_archive_terminal_last_error_events_expires_qkernel_quality_retry_debt():
    conn = _world_conn()
    store = EventStore(conn)
    event = _fsr_entity_event(
        "Chicago|2026-05-24|high|snap-terminal-quality",
        "snap-terminal-quality",
        "2026-05-24T04:12:00+00:00",
        "2026-05-24T04:12:30+00:00",
    )
    store.insert_or_ignore(event)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET attempt_count = 7,
               last_error = ?
         WHERE event_id = ?
        """,
        (
            "QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR:"
            "actual_profit_below_strategy_floor:strategy=forecast_qkernel_entry:"
            "profit_lcb_usd=0.467748:floor=1.000000:stake_usd=11.703848:cost=0.640000",
            event.event_id,
        ),
    )

    archived = store.archive_terminal_last_error_events()

    assert archived == 1
    row = conn.execute(
        """
        SELECT processing_status, processed_at, last_error
          FROM opportunity_event_processing
         WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert row["processing_status"] == "expired"
    assert row["processed_at"]
    assert row["last_error"].startswith(
        "TERMINAL_LAST_ERROR_ARCHIVED:QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR:"
    )


def test_archive_terminal_last_error_events_keeps_unknown_retry_debt_active():
    conn = _world_conn()
    store = EventStore(conn)
    event = _fsr_entity_event(
        "Chicago|2026-05-24|high|snap-transient-quality",
        "snap-transient-quality",
        "2026-05-24T04:12:00+00:00",
        "2026-05-24T04:12:30+00:00",
    )
    store.insert_or_ignore(event)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET attempt_count = 7,
               last_error = 'EXECUTABLE_SNAPSHOT_STALE:selection_deadline=2026-05-24T04:18:00+00:00'
         WHERE event_id = ?
        """,
        (event.event_id,),
    )

    archived = store.archive_terminal_last_error_events()

    assert archived == 0
    row = conn.execute(
        """
        SELECT processing_status, last_error
          FROM opportunity_event_processing
         WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert row["processing_status"] == "pending"
    assert row["last_error"].startswith("EXECUTABLE_SNAPSHOT_STALE:")


def test_fetch_pending_interleaves_forecast_before_day0_backlog():
    from src.events.opportunity_event import Day0ExtremeUpdatedPayload

    conn = _world_conn()
    store = EventStore(conn)
    fsr = _event("snap-ready", 50, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00")
    day0 = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Chicago|2026-05-24|high|82",
        source="day0",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:10:00+00:00",
        received_at="2026-05-24T04:10:00+00:00",
        causal_snapshot_id=None,
        payload=Day0ExtremeUpdatedPayload(
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            settlement_source="wu_icao_history",
            station_id="KORD",
            observation_time="2026-05-24T04:00:00+00:00",
            observation_available_at="2026-05-24T04:10:00+00:00",
            raw_value=82.0,
            rounded_value=82,
            high_so_far=82.0,
        ),
        priority=20,
    )
    store.insert_or_ignore(fsr)
    store.insert_or_ignore(day0)

    ordered = store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00", limit=2)

    assert [event.event_type for event in ordered] == ["FORECAST_SNAPSHOT_READY", "DAY0_EXTREME_UPDATED"]


def test_stale_processing_claim_is_reclaimed_after_lease():
    conn = _world_conn()
    store = EventStore(conn, processing_lease_seconds=60)
    event = _event("snap-stale", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00")
    store.insert_or_ignore(event)
    assert store.claim(event.event_id, claimed_at="2026-05-24T04:10:00+00:00") is True

    pending = store.fetch_pending(decision_time="2026-05-24T04:12:00+00:00")
    assert [row.event_id for row in pending] == [event.event_id]
    assert store.claim(event.event_id, claimed_at="2026-05-24T04:12:00+00:00") is True
    assert conn.execute(
        "SELECT attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0] == 2


def test_stale_processing_claim_is_not_buried_behind_pending_backlog():
    conn = _world_conn()
    store = EventStore(conn, processing_lease_seconds=60)
    stale = _event(
        "snap-stale-buried",
        5,
        "2026-05-24T04:05:00+00:00",
        "2026-05-24T04:06:00+00:00",
    )
    store.insert_or_ignore(stale)
    assert store.claim(stale.event_id, claimed_at="2026-05-24T04:10:00+00:00") is True
    for i in range(250):
        pending = _event(
            f"snap-pending-{i}",
            100,
            "2026-05-24T04:11:00+00:00",
            "2026-05-24T04:11:00+00:00",
        )
        store.insert_or_ignore(pending)

    fetched = store.fetch_pending(
        decision_time="2026-05-24T04:12:00+00:00",
        limit=20,
    )

    assert fetched
    assert fetched[0].event_id == stale.event_id


def test_dead_letter_writes_separate_evidence_and_terminal_status():
    conn = _world_conn()
    store = EventStore(conn)
    event = _event("snap-bad", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    store.insert_or_ignore(event)
    store.claim(event.event_id, claimed_at="2026-05-24T04:17:00+00:00")

    from src.events.dead_letter import dead_letter_event

    record = dead_letter_event(
        store,
        event,
        failure_stage="SOURCE_TRUTH",
        error_message="source mismatch",
        created_at="2026-05-24T04:18:00+00:00",
    )

    assert record.event_id == event.event_id
    assert conn.execute("SELECT COUNT(*) FROM event_dead_letters").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]
        == "dead_letter"
    )
