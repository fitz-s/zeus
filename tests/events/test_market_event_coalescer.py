# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §7 market coalescing and lossless source events.
from __future__ import annotations

import sqlite3

from src.events.event_coalescer import EventCoalescer
from src.events.event_writer import EventWriter
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    MarketBookEventPayload,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)
from src.state.db import init_schema


def _market_event(entity: str, book_hash: str):
    payload = MarketBookEventPayload(
        condition_id="0xcondition",
        token_id=entity,
        outcome_label="YES",
        event_type="BOOK_SNAPSHOT",
        quote_seen_at="2026-05-24T10:00:00+00:00",
        book_hash=book_hash,
        best_bid=0.42,
        best_ask=0.44,
        depth_json='{"asks":[["0.44","10"]]}',
    )
    return make_opportunity_event(
        event_type="BOOK_SNAPSHOT",
        entity_key=entity,
        source="polymarket_market_channel",
        observed_at="2026-05-24T10:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        received_at=f"2026-05-24T10:00:0{book_hash[-1]}+00:00",
        payload=payload,
    )


def _day0_event(raw_value: float):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        settlement_source="WU",
        station_id="KMDW",
        observation_time="2026-05-24T18:00:00+00:00",
        observation_available_at="2026-05-24T18:07:00+00:00",
        raw_value=raw_value,
        rounded_value=round(raw_value),
    )
    return make_day0_extreme_updated_event(
        entity_key=f"Chicago|2026-05-24|high|{raw_value}",
        source="day0_observation",
        observed_at=payload.observation_time,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
    )


def _writer() -> tuple[sqlite3.Connection, EventWriter]:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventWriter(conn)


def test_market_coalescer_db_write_budget():
    conn, writer = _writer()
    coalescer = EventCoalescer()
    coalescer.enqueue(_market_event("token-1", "hash-1"))
    coalescer.enqueue(_market_event("token-1", "hash-2"))
    coalescer.enqueue(_market_event("token-2", "hash-3"))

    written = coalescer.flush(writer, market_budget=1)

    assert len(written) == 1
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1
    assert coalescer.pending_counts() == {"lossless": 0, "market": 1}
    assert coalescer.stats.coalesced_market_events == 1


def test_forecast_day0_not_dropped_on_backpressure():
    conn, writer = _writer()
    coalescer = EventCoalescer(max_market_keys=1)
    coalescer.enqueue(_day0_event(74.2))
    coalescer.enqueue(_day0_event(75.1))
    coalescer.enqueue(_market_event("token-1", "hash-1"))
    coalescer.enqueue(_market_event("token-2", "hash-2"))

    coalescer.flush(writer, market_budget=0)

    rows = conn.execute("SELECT event_type FROM opportunity_events ORDER BY created_at").fetchall()
    assert [row[0] for row in rows] == ["DAY0_EXTREME_UPDATED", "DAY0_EXTREME_UPDATED"]
    assert coalescer.pending_counts() == {"lossless": 0, "market": 1}
    assert coalescer.stats.backpressure_drops == 1


def test_market_online_ingestor_backpressure_counter():
    coalescer = EventCoalescer(max_market_keys=1)
    coalescer.enqueue(_market_event("token-1", "hash-1"))
    coalescer.enqueue(_market_event("token-2", "hash-2"))
    coalescer.enqueue(_market_event("token-2", "hash-3"))

    assert coalescer.stats.backpressure_drops == 1
    assert coalescer.stats.coalesced_market_events == 1
    assert coalescer.pending_counts() == {"lossless": 0, "market": 1}
