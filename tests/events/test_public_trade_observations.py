from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import nullcontext

import pytest

from src.events.public_trade_observations import (
    PublicTradeBuffer,
    write_public_trade_observations,
)
from src.events.triggers.market_channel_ingestor import (
    MarketChannelIngestor,
    MarketChannelOnlineService,
)
from src.state.schema.public_market_trade_observations_schema import ensure_table

NOW = "2026-09-12T02:00:00+00:00"
PRINT = {
    "event_type": "last_trade_price",
    "asset_id": "token",
    "market": "condition",
    "timestamp": "1789178400123",
    "price": "0.41",
    "size": "10",
    "side": "BUY",
    "transaction_hash": "0x" + "a" * 64,
}


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    conn.commit()
    yield conn
    conn.close()


def test_identical_prints_are_distinct_deliveries_and_retries_are_idempotent(conn):
    buffer = PublicTradeBuffer(max_pending=8)
    for _ in range(2):
        buffer.record("TRADE", PRINT, received_at=NOW)
    rows = buffer.peek(8)
    assert rows[0].observation_id != rows[1].observation_id
    assert rows[0].payload_hash == rows[1].payload_hash
    write_public_trade_observations(conn, rows)
    conn.commit()
    write_public_trade_observations(conn, rows)
    conn.commit()
    assert (
        conn.execute(
            "SELECT count(*) FROM public_market_trade_observations"
        ).fetchone()[0]
        == 2
    )
    assert conn.execute(
        "SELECT DISTINCT truth_status, coverage_status FROM public_market_trade_observations"
    ).fetchall() == [("PUBLIC_DELIVERY_ONLY", "SOURCE_UNSEQUENCED")]
    buffer.acknowledge(rows)
    assert not buffer.pending


@pytest.mark.parametrize("timestamp", [None, "bad", 1.5, True, "9" * 80, "-1"])
def test_invalid_source_time_is_retained_without_received_time_fallback(timestamp):
    buffer = PublicTradeBuffer(max_pending=8)
    row = buffer.record("TRADE", {**PRINT, "timestamp": timestamp}, received_at=NOW)
    assert row.source_observed_at is None
    assert json.loads(row.payload_json)["raw"]["timestamp"] == timestamp


def test_millisecond_time_and_input_are_frozen():
    buffer = PublicTradeBuffer(max_pending=8)
    payload = dict(PRINT)
    row = buffer.record("TRADE", payload, received_at=NOW)
    assert row.source_observed_at.endswith(".123000+00:00")
    payload["price"] = "0.9"
    assert json.loads(row.payload_json)["raw"]["price"] == "0.41"


def test_overflow_is_an_ordered_explicit_interval(conn):
    buffer = PublicTradeBuffer(max_pending=2)
    for _ in range(5):
        buffer.record("TRADE", PRINT, received_at=NOW)
    first = buffer.peek(8)
    assert [r.sequence for r in first] == [1, 2]
    write_public_trade_observations(conn, first)
    conn.commit()
    buffer.acknowledge(first)
    buffer.record("TRADE", PRINT, received_at=NOW)
    rest = buffer.peek(8)
    assert [(r.kind, r.sequence, r.end_sequence) for r in rest] == [
        ("GAP", 3, 5),
        ("TRADE", 6, 6),
    ]
    assert json.loads(rest[0].payload_json)["dropped_count"] == 3


def test_failed_batch_preserves_original_fifo_and_transaction(conn):
    buffer = PublicTradeBuffer(max_pending=2)
    for _ in range(2):
        buffer.record("TRADE", PRINT, received_at=NOW)
    rows = buffer.peek(8)
    write_public_trade_observations(conn, rows[:1])
    conn.rollback()
    assert buffer.peek(8) == rows
    assert (
        conn.execute(
            "SELECT count(*) FROM public_market_trade_observations"
        ).fetchone()[0]
        == 0
    )
    write_public_trade_observations(conn, rows)
    conn.commit()
    buffer.acknowledge(rows)


def test_empty_reconnect_does_not_inherit_flush_activation():
    buffer = PublicTradeBuffer(max_pending=8)
    buffer.record("STREAM_OPEN", {}, received_at=NOW)
    buffer.record("TRADE", PRINT, received_at=NOW)
    buffer.record("STREAM_CLOSED", {}, received_at=NOW)
    assert buffer.ready_to_flush
    buffer.acknowledge(buffer.peek(8))
    buffer.record("STREAM_OPEN", {}, received_at=NOW)
    assert buffer.pending
    assert not buffer.ready_to_flush
    buffer.record("TRADE", PRINT, received_at=NOW)
    assert buffer.ready_to_flush


def test_overflow_activation_survives_partial_ack_without_trades():
    buffer = PublicTradeBuffer(max_pending=2)
    for _ in range(5):
        buffer.record("STREAM_OPEN", {}, received_at=NOW)
    buffer.acknowledge(buffer.peek(1))
    assert buffer.ready_to_flush
    buffer.acknowledge(buffer.peek(1))
    assert buffer.ready_to_flush
    gap = buffer.peek(1)
    assert [(row.kind, row.sequence, row.end_sequence) for row in gap] == [("GAP", 3, 5)]
    buffer.acknowledge(gap)
    assert not buffer.ready_to_flush


def test_reconnect_never_claims_source_completeness(conn):
    buffer = PublicTradeBuffer(max_pending=8)
    first = buffer.record(
        "STREAM_OPEN", {"subscribed_token_ids": ["token"]}, received_at=NOW
    )
    buffer.record("STREAM_CLOSED", {}, received_at=NOW)
    second = buffer.record(
        "STREAM_OPEN", {"subscribed_token_ids": ["other"]}, received_at=NOW
    )
    assert first.connection_id != second.connection_id
    write_public_trade_observations(conn, buffer.peek(8))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE public_market_trade_observations SET coverage_status='COMPLETE'"
        )
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM public_market_trade_observations")


def test_existing_ingestor_and_service_drain_public_prints_without_quote_or_own_fill(
    conn,
):
    ingestor = MarketChannelIngestor(
        None, feasibility_conn=conn, active_token_ids={"token"}
    )
    assert ingestor.handle_message(PRINT, received_at=NOW) is None
    assert ingestor.handle_message(PRINT, received_at=NOW) is None
    assert ingestor.quote_cache.get("token") is None
    service = MarketChannelOnlineService(
        ingestor=ingestor, fetch_orderbook=lambda _: {}
    )

    async def drain():
        done = asyncio.Event()
        done.set()
        await service._flush_public_trades_forever(
            connection_done=done,
            write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
            logger=None,
        )

    asyncio.run(drain())
    assert not ingestor.public_trades.pending
    assert (
        conn.execute(
            "SELECT count(*) FROM public_market_trade_observations WHERE kind='TRADE'"
        ).fetchone()[0]
        == 2
    )
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall() == [("public_market_trade_observations",)]


@pytest.mark.parametrize("coalesced", [False, True])
def test_websocket_array_routes_every_public_print_to_durable_fifo(
    conn, monkeypatch, coalesced
):
    import websockets
    from src.events.event_coalescer import EventCoalescer
    from src.state.schema.market_channel_connectivity_schema import (
        ensure_table as ensure_connectivity,
    )

    ensure_connectivity(conn)
    conn.commit()
    stop = asyncio.Event()

    class Socket:
        sent = False

        async def send(self, _payload):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                stop.set()
                raise StopAsyncIteration
            self.sent = True
            return json.dumps([PRINT, PRINT])

    class Connect:
        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: Connect())
    ingestor = MarketChannelIngestor(
        None,
        feasibility_conn=conn,
        active_token_ids={"token"},
        coalescer=EventCoalescer() if coalesced else None,
    )
    service = MarketChannelOnlineService(ingestor=ingestor)

    async def seed(**_kwargs):
        return None

    monkeypatch.setattr(service, "_seed_subscribed_books", seed)
    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
        )
    )
    rows = conn.execute(
        "SELECT kind FROM public_market_trade_observations ORDER BY sequence"
    ).fetchall()
    assert rows == [("STREAM_OPEN",), ("TRADE",), ("TRADE",), ("STREAM_CLOSED",)]
    assert ingestor.quote_cache.get("token") is None
    assert not ingestor.public_trades.pending


def test_public_append_failure_retries_without_ack_or_quote_failure(conn):
    ingestor = MarketChannelIngestor(
        None, feasibility_conn=conn, active_token_ids={"token"}
    )
    ingestor.handle_message(PRINT, received_at=NOW)
    rows = ingestor.public_trades.peek(8)
    service = MarketChannelOnlineService(ingestor=ingestor)
    calls = 0

    def failed_commit():
        nonlocal calls
        calls += 1
        raise sqlite3.OperationalError("database is locked")

    async def drain(commit):
        done = asyncio.Event()
        done.set()
        await service._flush_public_trades_forever(
            connection_done=done,
            write_gate=nullcontext(),
            commit=commit,
            rollback=conn.rollback,
            logger=None,
        )

    asyncio.run(drain(failed_commit))
    assert calls == 2
    assert ingestor.public_trades.peek(8) == rows
    assert (
        conn.execute(
            "SELECT count(*) FROM public_market_trade_observations"
        ).fetchone()[0]
        == 0
    )
    asyncio.run(drain(conn.commit))
    assert not ingestor.public_trades.pending
