# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §13 event reactor no-bypass contract.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.events.event_store import EventStore
from src.events.opportunity_event import Day0ExtremeUpdatedPayload, MarketBookEventPayload, make_day0_extreme_updated_event, make_opportunity_event
from src.events.reactor import OpportunityEventReactor, ReactorConfig
from src.state.db import init_schema


def _store() -> tuple[sqlite3.Connection, EventStore]:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventStore(conn)


def _day0_event(key_suffix: str = "a"):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        settlement_source="WU",
        station_id="KMDW",
        observation_time="2026-05-24T18:00:00+00:00",
        observation_available_at="2026-05-24T18:07:00+00:00",
        raw_value=74.2,
        rounded_value=74,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
    )
    return make_day0_extreme_updated_event(
        entity_key=f"Chicago|2026-05-24|high|{key_suffix}",
        source="day0_observation",
        observed_at=payload.observation_time,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
    )


def _market_event():
    payload = MarketBookEventPayload(
        condition_id="0xcondition",
        token_id="token-1",
        outcome_label="YES",
        event_type="BOOK_SNAPSHOT",
        quote_seen_at="2026-05-24T18:07:00+00:00",
        book_hash="hash-1",
    )
    return make_opportunity_event(
        event_type="BOOK_SNAPSHOT",
        entity_key="0xcondition|token-1",
        source="polymarket_market_channel",
        observed_at=payload.quote_seen_at,
        available_at=payload.quote_seen_at,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
        causal_snapshot_id="hash-1",
    )


def _reactor(store, *, gates=True, config=None):
    rejected = []
    submitted = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: gates,
        executable_snapshot_gate=lambda _event: gates,
        fdr_gate=lambda _event: gates,
        kelly_gate=lambda _event: gates,
        riskguard_gate=lambda _event: gates,
        final_intent_submit=lambda event: submitted.append(event.event_id),
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=config or ReactorConfig(),
    )
    return reactor, rejected, submitted


def test_event_cannot_bypass_source_truth():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    reactor, rejected, submitted = _reactor(store, gates=False)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.rejected == 1
    assert rejected[0][1] == "SOURCE_TRUTH"
    assert submitted == []


def test_market_channel_event_no_direct_stale_trade():
    _conn, store = _store()
    store.insert_or_ignore(_market_event())
    reactor, rejected, submitted = _reactor(store)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.rejection_reasons == ["MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE"]
    assert submitted == []


def test_duplicate_event_not_double_counted():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    store.insert_or_ignore(event)
    reactor, _rejected, submitted = _reactor(store)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.processed == 1
    assert len(submitted) == 1


def test_sibling_family_logged_once():
    _conn, store = _store()
    store.insert_or_ignore(_day0_event("bin-a"))
    store.insert_or_ignore(_day0_event("bin-b"))
    reactor, _rejected, _submitted = _reactor(store)
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert reactor.family_log_count() == 1


def test_live_day0_requires_tiny_cap():
    _conn, store = _store()
    store.insert_or_ignore(_day0_event("bin-a"))
    store.insert_or_ignore(_day0_event("bin-b"))
    reactor, rejected, submitted = _reactor(
        store,
        config=ReactorConfig(tiny_live_max_orders_per_day=1),
    )
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert len(submitted) == 1
    assert result.rejected == 1
    assert rejected[-1][2] == "DAY0_TINY_ORDER_CAP_BLOCKED"


def test_live_day0_tiny_cap_persists_across_reactor_instances():
    conn, store = _store()
    first = _day0_event("bin-a")
    second = _day0_event("bin-b")
    store.insert_or_ignore(first)
    reactor, _rejected, submitted = _reactor(
        store,
        config=ReactorConfig(tiny_live_max_orders_per_day=1),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert submitted == [first.event_id]

    store.insert_or_ignore(second)
    second_reactor, rejected, second_submitted = _reactor(
        store,
        config=ReactorConfig(tiny_live_max_orders_per_day=1),
    )
    result = second_reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc))

    assert second_submitted == []
    assert result.rejected == 1
    assert rejected[-1][2] == "DAY0_TINY_ORDER_CAP_BLOCKED"
    assert conn.execute("SELECT COUNT(*) FROM edli_live_cap_usage").fetchone()[0] == 1


def test_live_day0_tiny_notional_cap_persists_across_reactor_instances():
    conn, store = _store()
    first = _day0_event("bin-a")
    second = _day0_event("bin-b")
    store.insert_or_ignore(first)
    reactor, _rejected, submitted = _reactor(
        store,
        config=ReactorConfig(tiny_live_max_orders_per_day=2, tiny_live_max_notional_usd=5.0),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert submitted == [first.event_id]

    store.insert_or_ignore(second)
    second_reactor, rejected, second_submitted = _reactor(
        store,
        config=ReactorConfig(tiny_live_max_orders_per_day=2, tiny_live_max_notional_usd=5.0),
    )
    result = second_reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc))

    assert second_submitted == []
    assert result.rejected == 1
    assert rejected[-1][2] == "DAY0_TINY_NOTIONAL_CAP_BLOCKED"
    assert conn.execute("SELECT SUM(notional_usd) FROM edli_live_cap_usage").fetchone()[0] == 5.0


def test_day0_source_mismatch_blocks_before_trade_score_path():
    _conn, store = _store()
    event = _day0_event()
    import json
    from dataclasses import replace

    payload = json.loads(event.payload_json)
    payload["source_match_status"] = "MISMATCH"
    mismatched = replace(
        event,
        event_id="event-source-mismatch",
        idempotency_key="idem-source-mismatch",
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    store.insert_or_ignore(mismatched)
    reactor, rejected, submitted = _reactor(store)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    assert rejected[0][1] == "SOURCE_TRUTH"
    assert rejected[0][2] == "DAY0_HARD_FACT_AUTHORITY_BLOCKED"
    assert submitted == []


def test_reactor_rejections_write_no_trade_regret_events():
    conn, store = _store()
    store.insert_or_ignore(_market_event())
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

    rejected = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event: True,
        fdr_gate=lambda _event: True,
        kelly_gate=lambda _event: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event: None,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert conn.execute("SELECT rejection_reason FROM no_trade_regret_events").fetchone()[0] == "MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE"


def test_reactor_exception_dead_letters_event():
    conn, store = _store()
    store.insert_or_ignore(_day0_event())
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: (_ for _ in ()).throw(RuntimeError("boom")),
        executable_snapshot_gate=lambda _event: True,
        fdr_gate=lambda _event: True,
        kelly_gate=lambda _event: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event: None,
        reject=lambda _event, _stage, _reason: None,
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM event_dead_letters").fetchone()[0] == 1
