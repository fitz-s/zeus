# Created: 2026-05-24
# Last reused/audited: 2026-07-19
# Authority basis: EDLI v1 implementation prompt §10 online MarketChannelIngestor contract.
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest

from src.events.event_coalescer import EventCoalescer
from src.events.event_writer import EventWriter
from src.events.triggers.market_channel_ingestor import (
    MarketChannelAction,
    MarketChannelAuthorityError,
    MarketChannelIngestor,
    MarketChannelOnlineService,
    MarketTokenMetadata,
    QuoteCache,
    active_weather_token_metadata_from_snapshots,
    active_weather_token_metadata_for_tokens,
    active_weather_token_ids_from_snapshots,
    assert_market_channel_not_fill_authority,
    assert_user_channel_fill_authority,
    feasibility_evidence_from_quote,
    invalidate_executable_snapshots_for_market_channel_action,
    insert_execution_feasibility_evidence,
)
from src.state.db import init_schema, init_schema_trade_only
from src.strategy.live_inference.executable_cost import ExecutableCostError, quote_book_from_depth_json, executable_cost


def _conn_writer():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    # Most ingestor unit tests use a single in-memory connection to exercise
    # parsing/coalescing behavior. Live wiring is covered separately by
    # test_market_channel_can_write_feasibility_to_trade_connection.
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table
    ensure_table(conn)
    from src.state.schema.market_channel_connectivity_schema import (
        ensure_table as ensure_connectivity_table,
    )
    ensure_connectivity_table(conn)
    return conn, EventWriter(conn)


def _metadata(token_id: str = "token-1", *, outcome_label: str = "YES") -> dict[str, MarketTokenMetadata]:
    return {
        token_id: MarketTokenMetadata(
            condition_id="0xcondition",
            token_id=token_id,
            outcome_label=outcome_label,
            min_tick_size="0.01",
            min_order_size="5",
            neg_risk=False,
            executable_snapshot_id="snap-1",
        )
    }


def test_execution_feasibility_schema_indexes_token_created_at():
    conn = sqlite3.connect(":memory:")
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    ensure_table(conn)

    indexes = {
        row[1]
        for row in conn.execute("PRAGMA index_list('execution_feasibility_evidence')").fetchall()
    }
    assert "idx_execution_feasibility_evidence_token_created" in indexes
    latest_indexes = {
        row[1]
        for row in conn.execute("PRAGMA index_list('execution_feasibility_latest')").fetchall()
    }
    assert "idx_execution_feasibility_latest_token_created" in latest_indexes
    columns = [
        row[2]
        for row in conn.execute(
            "PRAGMA index_info('idx_execution_feasibility_evidence_token_created')"
        ).fetchall()
    ]
    assert columns == ["token_id", "created_at"]


def test_bounded_token_metadata_prefers_latest_projection_and_falls_back_per_missing_token():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            neg_risk INTEGER NOT NULL,
            active INTEGER,
            closed INTEGER,
            event_slug TEXT,
            market_end_at TEXT,
            captured_at TEXT NOT NULL
        );
        CREATE INDEX idx_snapshots_yes_token_captured
            ON executable_market_snapshots (yes_token_id, captured_at DESC);
        CREATE INDEX idx_snapshots_no_token_captured
            ON executable_market_snapshots (no_token_id, captured_at DESC);
        CREATE TABLE executable_market_snapshot_latest (
            snapshot_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE INDEX idx_snapshot_latest_yes_token_captured
            ON executable_market_snapshot_latest (yes_token_id, captured_at DESC);
        CREATE INDEX idx_snapshot_latest_no_token_captured
            ON executable_market_snapshot_latest (no_token_id, captured_at DESC);

        INSERT INTO executable_market_snapshots VALUES
            ('snap-latest', 'cond-latest', 'token-latest', 'no-latest', '0.01', '5', 0,
             1, 0, 'weather-latest', '2026-07-11T00:00:00+00:00', '2026-07-09T10:00:00+00:00'),
            ('snap-fallback', 'cond-fallback', 'token-fallback', 'no-fallback', '0.01', '5', 0,
             1, 0, 'weather-fallback', '2026-07-11T00:00:00+00:00', '2026-07-09T09:00:00+00:00'),
            ('snap-closed-old', 'cond-closed', 'token-closed', 'no-closed', '0.01', '5', 0,
             1, 0, 'weather-closed', '2026-07-11T00:00:00+00:00', '2026-07-09T08:00:00+00:00'),
            ('snap-closed-new', 'cond-closed', 'token-closed', 'no-closed', '0.01', '5', 0,
             0, 1, 'weather-closed', '2026-07-11T00:00:00+00:00', '2026-07-09T11:00:00+00:00');
        INSERT INTO executable_market_snapshot_latest VALUES
            ('snap-latest', 'token-latest', 'no-latest', '2026-07-09T10:00:00+00:00'),
            ('snap-closed-new', 'token-closed', 'no-closed', '2026-07-09T11:00:00+00:00');
        """
    )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    metadata = active_weather_token_metadata_for_tokens(
        conn,
        token_ids=("token-latest", "token-fallback", "token-closed"),
        now=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
    )

    assert set(metadata) == {"token-latest", "token-fallback"}
    assert metadata["token-latest"].executable_snapshot_id == "snap-latest"
    assert metadata["token-fallback"].executable_snapshot_id == "snap-fallback"
    assert any("FROM executable_market_snapshot_latest AS l" in sql for sql in statements)
    direct_history_seeks = [
        sql
        for sql in statements
        if "FROM executable_market_snapshots" in sql
        and "JOIN (" not in sql
        and ("WHERE yes_token_id =" in sql or "WHERE no_token_id =" in sql)
    ]
    assert len(direct_history_seeks) == 2


def test_bounded_token_metadata_keeps_past_end_only_for_still_executable_exit():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            neg_risk INTEGER NOT NULL,
            enable_orderbook INTEGER NOT NULL,
            accepting_orders INTEGER,
            active INTEGER,
            closed INTEGER,
            event_slug TEXT,
            market_end_at TEXT,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            snapshot_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );

        INSERT INTO executable_market_snapshots VALUES
            ('snap-exit-open', 'cond-exit-open', 'token-exit-open', 'no-exit-open',
             '0.01', '5', 0, 1, 1, 1, 0, 'weather-open',
             '2026-07-16T12:00:00+00:00', '2026-07-16T12:01:00+00:00'),
            ('snap-exit-stopped', 'cond-exit-stopped', 'token-exit-stopped', 'no-exit-stopped',
             '0.01', '5', 0, 1, 0, 1, 0, 'weather-stopped',
             '2026-07-16T12:00:00+00:00', '2026-07-16T12:01:00+00:00');
        INSERT INTO executable_market_snapshot_latest VALUES
            ('snap-exit-open', 'token-exit-open', 'no-exit-open',
             '2026-07-16T12:01:00+00:00'),
            ('snap-exit-stopped', 'token-exit-stopped', 'no-exit-stopped',
             '2026-07-16T12:01:00+00:00');
        """
    )
    now = datetime(2026, 7, 16, 12, 2, tzinfo=timezone.utc)
    token_ids = ("token-exit-open", "token-exit-stopped")

    entry = active_weather_token_metadata_for_tokens(
        conn,
        token_ids=token_ids,
        now=now,
        purpose="entry",
    )
    exit_metadata = active_weather_token_metadata_for_tokens(
        conn,
        token_ids=token_ids,
        now=now,
        purpose="exit",
    )

    assert entry == {}
    assert set(exit_metadata) == {"token-exit-open"}


def test_book_buy_uses_best_ask():
    book = quote_book_from_depth_json(
        yes_depth_json='{"asks":[{"price":"0.52","size":"10"}],"bids":[{"price":"0.48","size":"10"}]}',
        no_depth_json='{"asks":[{"price":"0.51","size":"10"}],"bids":[{"price":"0.49","size":"10"}]}',
        min_tick_size="0.01",
        min_order_size="5",
        fee_rate=0.05,
        neg_risk=False,
    )
    assert executable_cost(book, direction="buy_yes", shares=__import__("decimal").Decimal("5")).value > 0.52


def test_book_sell_uses_best_bid():
    book = quote_book_from_depth_json(
        yes_depth_json='{"asks":[{"price":"0.52","size":"10"}],"bids":[{"price":"0.48","size":"10"}]}',
        no_depth_json='{"asks":[{"price":"0.51","size":"10"}],"bids":[{"price":"0.49","size":"10"}]}',
        min_tick_size="0.01",
        min_order_size="5",
        fee_rate=0.05,
        neg_risk=False,
    )
    assert executable_cost(book, direction="sell_yes", shares=__import__("decimal").Decimal("5")).value < 0.48


def test_tick_size_change_forces_snapshot_refresh():
    _conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata())
    result = ingestor.handle_message(
        {"event_type": "tick_size_change", "asset_id": "token-1", "timestamp": "1766789469958"},
        received_at="2026-05-24T10:00:00+00:00",
    )
    assert isinstance(result, MarketChannelAction)
    assert result.refresh_snapshot is True


def test_market_channel_cannot_write_fill_truth():
    with pytest.raises(MarketChannelAuthorityError, match="cannot write fill truth"):
        assert_market_channel_not_fill_authority(source="polymarket_market_channel")


def test_user_channel_is_only_fill_authority():
    assert_user_channel_fill_authority(source="polymarket_user_channel")
    assert_user_channel_fill_authority(source="venue_reconcile")
    with pytest.raises(MarketChannelAuthorityError, match="user channel"):
        assert_user_channel_fill_authority(source="polymarket_market_channel")


def test_reconnect_gap_online_ingestor_no_stale_trade():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata())
    service = MarketChannelOnlineService(ingestor, fetch_orderbook=lambda _token_id: {})
    service.connected = False
    service.gap_start = "2026-05-24T09:59:00+00:00"
    results = service.on_reconnect(
        pre_captured_books={
            "token-1": {
                "event_type": "book",
                "asset_id": "token-1",
                "market": "0xcondition",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": "hash-after-gap",
                "timestamp": "1766789469958",
            }
        },
        token_ids={"token-1"},
        gap_start="2026-05-24T09:59:00+00:00",
        received_at="2026-05-24T10:00:00+00:00",
    )
    assert len(results) == 1
    assert results[0].inserted is True
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0] == 2


def test_market_message_ignores_inactive_token():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata())
    result = ingestor.handle_message(
        {"event_type": "book", "asset_id": "token-2", "timestamp": "1766789469958"},
        received_at="2026-05-24T10:00:00+00:00",
    )
    assert result is None
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0


def test_insert_execution_feasibility_evidence():
    conn, _writer = _conn_writer()
    insert_execution_feasibility_evidence(
        conn,
        {
            "event_id": "event-1",
            "condition_id": "0xcondition",
            "token_id": "token-1",
            "outcome_label": "YES",
            "direction": "buy_yes",
            "quote_seen_at": "2026-05-24T10:00:00+00:00",
            "book_hash_before": "hash-1",
            "best_bid_before": 0.48,
            "best_ask_before": 0.52,
            "depth_before_json": "{}",
            "order_intent_time": None,
            "submit_time": None,
            "accepted_or_rejected": None,
            "venue_order_id": None,
            "fok_full_fill": None,
            "fak_partial_fill": None,
            "filled_shares": None,
            "fill_price": None,
            "cancel_remainder_status": None,
            "book_hash_after": None,
            "latency_ms": None,
            "maker_cancel_before_submit": None,
            "would_have_edge_after_fee": 1,
            "fill_truth_source": "evidence_only",
        },
    )
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0] == 1
    latest = conn.execute(
        """
        SELECT token_id, direction, event_id, book_hash_before
          FROM execution_feasibility_latest
        """
    ).fetchone()
    assert latest == ("token-1", "buy_yes", "event-1", "hash-1")


def test_execution_feasibility_duplicate_quote_refreshes_observation_time():
    conn, _writer = _conn_writer()
    row = {
        "event_id": "event-static-book",
        "condition_id": "0xcondition",
        "token_id": "token-1",
        "outcome_label": "NO",
        "direction": "buy_no",
        "quote_seen_at": "2026-05-24T10:00:00+00:00",
        "book_hash_before": "hash-1",
        "best_bid_before": 0.74,
        "best_ask_before": 0.75,
        "depth_before_json": '{"bids":[],"asks":[]}',
        "order_intent_time": None,
        "submit_time": None,
        "accepted_or_rejected": None,
        "venue_order_id": None,
        "fok_full_fill": None,
        "fak_partial_fill": None,
        "filled_shares": None,
        "fill_price": None,
        "cancel_remainder_status": None,
        "book_hash_after": None,
        "latency_ms": None,
        "maker_cancel_before_submit": None,
        "would_have_edge_after_fee": 1,
        "fill_truth_source": "evidence_only",
        "created_at": "2026-05-24T10:00:01+00:00",
    }
    insert_execution_feasibility_evidence(conn, row)
    refreshed = {
        **row,
        "book_hash_before": "hash-2",
        "best_bid_before": 0.73,
        "best_ask_before": 0.76,
        "created_at": "2026-05-24T10:02:01+00:00",
    }
    insert_execution_feasibility_evidence(conn, refreshed)

    rows = conn.execute(
        """
        SELECT quote_seen_at, created_at, book_hash_before, best_bid_before, best_ask_before
          FROM execution_feasibility_evidence
        """
    ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "2026-05-24T10:00:00+00:00"
    assert rows[0][1] == "2026-05-24T10:02:01+00:00"
    assert rows[0][2] == "hash-2"
    assert rows[0][3] == pytest.approx(0.73)
    assert rows[0][4] == pytest.approx(0.76)
    latest = conn.execute(
        """
        SELECT created_at, book_hash_before, best_bid_before, best_ask_before
          FROM execution_feasibility_latest
         WHERE token_id = 'token-1' AND direction = 'buy_no'
        """
    ).fetchone()
    assert latest[0] == "2026-05-24T10:02:01+00:00"
    assert latest[1] == "hash-2"
    assert latest[2] == pytest.approx(0.73)
    assert latest[3] == pytest.approx(0.76)


def test_execution_feasibility_latest_never_regresses_event_time():
    conn, _writer = _conn_writer()
    base = {
        "condition_id": "0xcondition",
        "token_id": "token-1",
        "outcome_label": "NO",
        "direction": "buy_no",
        "best_bid_before": 0.74,
        "best_ask_before": 0.75,
        "depth_before_json": '{"bids":[],"asks":[]}',
        "order_intent_time": None,
        "submit_time": None,
        "accepted_or_rejected": None,
        "venue_order_id": None,
        "fok_full_fill": None,
        "fak_partial_fill": None,
        "filled_shares": None,
        "fill_price": None,
        "cancel_remainder_status": None,
        "book_hash_after": None,
        "latency_ms": None,
        "maker_cancel_before_submit": None,
        "would_have_edge_after_fee": None,
        "fill_truth_source": "evidence_only",
    }
    insert_execution_feasibility_evidence(
        conn,
        {
            **base,
            "event_id": "newer",
            "quote_seen_at": "2026-05-24T10:00:02+00:00",
            "book_hash_before": "newer-hash",
        },
    )
    insert_execution_feasibility_evidence(
        conn,
        {
            **base,
            "event_id": "older",
            "quote_seen_at": "2026-05-24T10:00:01+00:00",
            "book_hash_before": "older-hash",
        },
    )

    latest = conn.execute(
        "SELECT event_id, quote_seen_at, book_hash_before "
        "FROM execution_feasibility_latest WHERE token_id='token-1' AND direction='buy_no'"
    ).fetchone()
    assert latest == (
        "newer",
        "2026-05-24T10:00:02+00:00",
        "newer-hash",
    )


def test_execution_feasibility_latest_attached_schema_never_regresses_event_time():
    from src.state.schema.execution_feasibility_evidence_schema import (
        CREATE_LATEST_TABLE_SQL,
        CREATE_TABLE_SQL,
    )

    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS trades")
    conn.execute(
        CREATE_TABLE_SQL.replace(
            "execution_feasibility_evidence",
            "trades.execution_feasibility_evidence",
            1,
        )
    )
    conn.execute(
        CREATE_LATEST_TABLE_SQL.replace(
            "execution_feasibility_latest",
            "trades.execution_feasibility_latest",
            1,
        )
    )
    base = {
        "condition_id": "0xcondition",
        "token_id": "token-1",
        "outcome_label": "NO",
        "direction": "buy_no",
        "best_bid_before": 0.74,
        "best_ask_before": 0.75,
        "depth_before_json": '{"bids":[],"asks":[]}',
        "order_intent_time": None,
        "submit_time": None,
        "accepted_or_rejected": None,
        "venue_order_id": None,
        "fok_full_fill": None,
        "fak_partial_fill": None,
        "filled_shares": None,
        "fill_price": None,
        "cancel_remainder_status": None,
        "book_hash_after": None,
        "latency_ms": None,
        "maker_cancel_before_submit": None,
        "would_have_edge_after_fee": None,
        "fill_truth_source": "evidence_only",
    }
    insert_execution_feasibility_evidence(
        conn,
        {
            **base,
            "event_id": "newer",
            "quote_seen_at": "2026-05-24T10:00:02+00:00",
            "book_hash_before": "newer-hash",
        },
        schema="trades",
    )
    insert_execution_feasibility_evidence(
        conn,
        {
            **base,
            "event_id": "older",
            "quote_seen_at": "2026-05-24T10:00:01+00:00",
            "book_hash_before": "older-hash",
        },
        schema="trades",
    )

    latest = conn.execute(
        "SELECT event_id, quote_seen_at, book_hash_before "
        "FROM trades.execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_no'"
    ).fetchone()
    assert latest == (
        "newer",
        "2026-05-24T10:00:02+00:00",
        "newer-hash",
    )


def test_quote_cache_seeded_from_rest_on_connect():
    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda token_id: {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "hash-1",
        },
    )

    results = service.on_connect(received_at="2026-05-24T10:00:00+00:00")

    assert len(results) == 1
    assert cache.get("token-1") is not None
    assert results[0].inserted is True
    assert results[0].evidence_written is True
    assert results[0].opportunity_event_persisted is False
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0] == 0
    rows = conn.execute(
        """
        SELECT direction, depth_before_json
          FROM execution_feasibility_latest
         ORDER BY direction
        """
    ).fetchall()
    assert rows[0][0] == "buy_yes"
    assert rows[0][1]
    assert rows[1] == ("sell_yes", None)
    latest_rows = conn.execute(
        """
        SELECT direction, best_bid_before, best_ask_before, depth_before_json
          FROM execution_feasibility_latest
         ORDER BY direction
        """
    ).fetchall()
    assert latest_rows[0] == ("buy_yes", 0.48, 0.52, rows[0][1])
    assert latest_rows[1] == ("sell_yes", 0.48, 0.52, None)


def test_buffered_older_delta_cannot_regress_seeded_quote():
    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )
    ingestor.seed_from_rest(
        lambda _token: {},
        received_at="2026-05-24T10:00:02+00:00",
        pre_cached={
            "token-1": {
                "asset_id": "token-1",
                "market": "0xcondition",
                "timestamp": "2026-05-24T10:00:02+00:00",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": "seed-hash",
            }
        },
    )
    evidence_before = conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_evidence"
    ).fetchone()[0]

    result = ingestor.handle_message(
        {
            "event_type": "best_bid_ask",
            "asset_id": "token-1",
            "market": "0xcondition",
            "timestamp": "2026-05-24T10:00:01+00:00",
            "best_bid": "0.10",
            "best_ask": "0.90",
            "hash": "older-hash",
        },
        received_at="2026-05-24T10:00:03+00:00",
    )

    assert result is None
    assert cache.get("token-1").book_hash == "seed-hash"
    assert conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_evidence"
    ).fetchone()[0] == evidence_before
    latest = conn.execute(
        "SELECT quote_seen_at, book_hash_before FROM execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_yes'"
    ).fetchone()
    assert latest == ("2026-05-24T10:00:02+00:00", "seed-hash")


def test_price_change_updates_full_depth_even_when_touch_is_unchanged():
    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )
    ingestor.seed_from_rest(
        lambda _token: {},
        received_at="2026-05-24T10:00:00+00:00",
        pre_cached={
            "token-1": {
                "asset_id": "token-1",
                "market": "0xcondition",
                "timestamp": "2026-05-24T10:00:00+00:00",
                "bids": [
                    {"price": "0.48", "size": "10"},
                    {"price": "0.47", "size": "5"},
                ],
                "asks": [
                    {"price": "0.52", "size": "10"},
                    {"price": "0.53", "size": "20"},
                ],
                "hash": "seed-hash",
            }
        },
    )

    removed = ingestor.handle_message(
        {
            "event_type": "price_change",
            "market": "0xcondition",
            "timestamp": "2026-05-24T10:00:01+00:00",
            "price_changes": [
                {
                    "asset_id": "token-1",
                    "price": "0.48",
                    "size": "0",
                    "side": "BUY",
                    "hash": "hash-2",
                    "best_bid": "0.47",
                    "best_ask": "0.52",
                }
            ],
        },
        received_at="2026-05-24T10:00:01.010000+00:00",
    )
    assert removed is not None
    depth = json.loads(cache.get("token-1").depth_json)
    assert depth["bids"] == [{"price": "0.47", "size": "5"}]

    changed = ingestor.handle_message(
        {
            "event_type": "price_change",
            "market": "0xcondition",
            "timestamp": "2026-05-24T10:00:02+00:00",
            "price_changes": [
                {
                    "asset_id": "token-1",
                    "price": "0.53",
                    "size": "25",
                    "side": "SELL",
                    "hash": "hash-3",
                    "best_bid": "0.47",
                    "best_ask": "0.52",
                }
            ],
        },
        received_at="2026-05-24T10:00:02.010000+00:00",
    )
    assert changed is not None
    depth = json.loads(cache.get("token-1").depth_json)
    assert depth["asks"] == [
        {"price": "0.52", "size": "10"},
        {"price": "0.53", "size": "25"},
    ]
    latest = conn.execute(
        "SELECT quote_seen_at, book_hash_before, depth_before_json "
        "FROM execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_yes'"
    ).fetchone()
    assert latest[:2] == ("2026-05-24T10:00:02+00:00", "hash-3")
    assert json.loads(latest[2]) == depth
    assert conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_evidence"
    ).fetchone()[0] == 0


def test_price_change_packet_groups_each_tokens_deltas():
    from src.events.triggers.market_channel_ingestor import _parse_channel_messages

    messages = _parse_channel_messages(
        json.dumps(
            {
                "event_type": "price_change",
                "market": "0xcondition",
                "timestamp": "2026-05-24T10:00:00+00:00",
                "price_changes": [
                    {
                        "asset_id": "token-yes",
                        "price": "0.50",
                        "size": "20",
                        "side": "BUY",
                    },
                    {
                        "asset_id": "token-yes",
                        "price": "0.49",
                        "size": "10",
                        "side": "BUY",
                    },
                    {
                        "asset_id": "token-no",
                        "price": "0.50",
                        "size": "20",
                        "side": "SELL",
                    },
                ],
            }
        )
    )

    assert [message["asset_id"] for message in messages] == ["token-yes", "token-no"]
    assert [len(message["price_changes"]) for message in messages] == [2, 1]
    assert messages[0]["price"] == "0.49"
    assert all(message["timestamp"] == "2026-05-24T10:00:00+00:00" for message in messages)


def test_price_change_packet_applies_one_tokens_deltas_in_order():
    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )
    ingestor.seed_from_rest(
        lambda _token: {},
        received_at="2026-05-24T10:00:00+00:00",
        pre_cached={
            "token-1": {
                "asset_id": "token-1",
                "market": "0xcondition",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
            }
        },
    )

    ingestor.handle_message(
        {
            "event_type": "price_change",
            "market": "0xcondition",
            "timestamp": "2026-05-24T10:00:01+00:00",
            "price_changes": [
                {
                    "asset_id": "token-1",
                    "price": "0.48",
                    "size": "0",
                    "side": "BUY",
                },
                {
                    "asset_id": "token-1",
                    "price": "0.47",
                    "size": "12",
                    "side": "BUY",
                    "best_bid": "0.47",
                    "best_ask": "0.52",
                },
            ],
        },
        received_at="2026-05-24T10:00:01.001000+00:00",
    )

    assert json.loads(cache.get("token-1").depth_json)["bids"] == [
        {"price": "0.47", "size": "12"}
    ]


def test_seed_from_rest_can_seed_priority_subset_before_full_universe():
    conn, writer = _conn_writer()
    cache = QuoteCache()
    metadata = {
        "token-1": _metadata("token-1")["token-1"],
        "token-2": _metadata("token-2")["token-2"],
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1", "token-2"},
        token_metadata=metadata,
        quote_cache=cache,
    )
    fetch_calls: list[str] = []

    def fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        return {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        }

    results = ingestor.seed_from_rest(
        fetch,
        received_at="2026-05-24T10:00:00+00:00",
        token_ids={"token-2"},
    )

    assert len(results) == 1
    assert fetch_calls == ["token-2"]
    assert cache.get("token-2") is not None
    assert cache.get("token-1") is None
    rows = conn.execute(
        "SELECT token_id FROM execution_feasibility_latest ORDER BY token_id"
    ).fetchall()
    assert rows == [("token-2",), ("token-2",)]


def test_rest_seed_chunks_commit_progressively_before_full_universe_finishes():
    from contextlib import nullcontext

    conn, writer = _conn_writer()
    cache = QuoteCache()
    metadata = {
        f"token-{idx}": _metadata(f"token-{idx}")[
            f"token-{idx}"
        ]
        for idx in range(5)
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
        quote_cache=cache,
    )
    fetch_calls: list[str] = []
    commit_counts: list[int] = []

    def fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        return {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        }

    service = MarketChannelOnlineService(ingestor, fetch_orderbook=fetch)

    written = service.seed_rest_books_in_chunks(
        token_ids=set(metadata),
        received_at="2026-05-24T10:00:00+00:00",
        write_gate=nullcontext(),
        commit=lambda: commit_counts.append(
            conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
        ),
        chunk_size=2,
    )

    assert written == 5
    assert fetch_calls == [f"token-{idx}" for idx in range(5)]
    # Two evidence rows per token (buy/sell for the canonical side), committed
    # after each bounded batch rather than only after the full universe.
    assert commit_counts == [4, 8, 10]


def test_subscribed_seed_fetches_off_event_loop_thread():
    conn, writer = _conn_writer()
    caller_thread = threading.get_ident()
    fetch_threads: list[int] = []
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
        ),
        fetch_orderbook=lambda token_id: (
            fetch_threads.append(threading.get_ident())
            or {
                "asset_id": token_id,
                "market": "0xcondition",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": "seed-hash",
            }
        ),
    )
    written = asyncio.run(
        service.seed_rest_books_after_subscribe(
            token_ids={"token-1"},
            write_gate=nullcontext(),
            commit=conn.commit,
        )
    )

    assert written == 1
    assert fetch_threads and fetch_threads[0] != caller_thread


def test_subscribed_seed_separates_wide_network_fetch_from_small_db_commits():
    conn, writer = _conn_writer()
    metadata = {
        f"token-{idx}": _metadata(f"token-{idx}")[f"token-{idx}"]
        for idx in range(5)
    }
    fetch_batches: list[list[str]] = []
    commit_counts: list[int] = []

    def fetch_many(token_ids: list[str]) -> dict[str, dict]:
        fetch_batches.append(list(token_ids))
        return {
            token_id: {
                "asset_id": token_id,
                "market": "0xcondition",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": f"hash-{token_id}",
            }
            for token_id in token_ids
        }

    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids=set(metadata),
            token_metadata=metadata,
        ),
        fetch_orderbook=lambda _token_id: {},
        fetch_orderbooks=fetch_many,
    )

    written = asyncio.run(
        service.seed_rest_books_after_subscribe(
            token_ids=tuple(metadata),
            write_gate=nullcontext(),
            commit=lambda: commit_counts.append(
                conn.execute(
                    "SELECT COUNT(*) FROM execution_feasibility_latest"
                ).fetchone()[0]
            ),
            chunk_size=2,
            fetch_batch_size=5,
        )
    )

    assert written == 5
    assert fetch_batches == [[f"token-{idx}" for idx in range(5)]]
    assert commit_counts == [4, 8, 10]


def test_rest_seed_uses_request_start_not_last_venue_mutation_as_capture_time():
    conn, writer = _conn_writer()
    old_venue_time = "2026-01-01T00:00:00+00:00"
    before = datetime.now(timezone.utc)
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
        ),
        fetch_orderbook=lambda _token_id: {},
        fetch_orderbooks=lambda _token_ids: {
            "token-1": {
                "asset_id": "token-1",
                "timestamp": old_venue_time,
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": "seed-hash",
            }
        },
    )

    captured = service._fetch_rest_seed_books(["token-1"])["token-1"]
    after = datetime.now(timezone.utc)

    capture_time = datetime.fromisoformat(str(captured["timestamp"]))
    assert before <= capture_time <= after
    assert captured["venue_timestamp"] == old_venue_time
    assert capture_time > datetime.fromisoformat(old_venue_time)


def test_quote_projection_pump_commits_one_available_stream_batch():
    conn, writer = _conn_writer()
    metadata = {
        f"token-{idx}": _metadata(f"token-{idx}")[f"token-{idx}"]
        for idx in range(100)
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
        coalescer=EventCoalescer(max_market_keys=1000),
    )
    for token_id in metadata:
        ingestor.handle_message(
            {
                "event_type": "book",
                "asset_id": token_id,
                "market": "0xcondition",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": f"hash-{token_id}",
            },
            received_at="2026-07-17T12:00:00+00:00",
        )
    service = MarketChannelOnlineService(ingestor)
    wake = asyncio.Event()
    connection_done = asyncio.Event()
    initial_seed_done = asyncio.Event()
    wake.set()
    connection_done.set()
    initial_seed_done.set()
    commits: list[int] = []

    asyncio.run(
        service._flush_quote_projection_forever(
            wake=wake,
            connection_done=connection_done,
            initial_seed_done=initial_seed_done,
            active_token_ids=set(metadata),
            write_gate=nullcontext(),
            commit=lambda: commits.append(
                conn.execute(
                    "SELECT COUNT(*) FROM execution_feasibility_latest"
                ).fetchone()[0]
            ),
            rollback=conn.rollback,
            logger=None,
        )
    )

    assert commits == [200]
    assert ingestor._coalescer.pending_counts() == {"lossless": 0, "market": 0}


def test_quote_projection_pump_rate_limits_only_burst_commits(monkeypatch):
    from src.events.triggers import market_channel_ingestor as market_channel

    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        coalescer=EventCoalescer(max_market_keys=8),
    )
    ingestor.handle_message(
        {
            "event_type": "best_bid_ask",
            "asset_id": "token-1",
            "market": "0xcondition",
            "best_bid": "0.48",
            "best_ask": "0.52",
            "timestamp": "1766789469958",
        },
        received_at="2026-07-17T12:00:00+00:00",
    )
    service = MarketChannelOnlineService(ingestor)
    wake = asyncio.Event()
    connection_done = asyncio.Event()
    initial_seed_done = asyncio.Event()
    wake.set()
    initial_seed_done.set()
    clock = [10.0]
    sleeps: list[float] = []
    commits = 0

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    def commit() -> None:
        nonlocal commits
        conn.commit()
        commits += 1
        if commits == 1:
            ingestor.handle_message(
                {
                    "event_type": "best_bid_ask",
                    "asset_id": "token-1",
                    "market": "0xcondition",
                    "best_bid": "0.49",
                    "best_ask": "0.53",
                    "timestamp": "1766789469959",
                },
                received_at="2026-07-17T12:00:00.001000+00:00",
            )
            wake.set()
        else:
            connection_done.set()

    monkeypatch.setattr(market_channel.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(market_channel.asyncio, "sleep", fake_sleep)

    asyncio.run(
        service._flush_quote_projection_forever(
            wake=wake,
            connection_done=connection_done,
            initial_seed_done=initial_seed_done,
            active_token_ids={"token-1"},
            write_gate=nullcontext(),
            commit=commit,
            rollback=conn.rollback,
            logger=None,
        )
    )

    assert commits == 2
    assert sleeps == pytest.approx(
        [market_channel.MARKET_CHANNEL_QUOTE_MIN_COMMIT_INTERVAL_SECONDS]
    )
    assert conn.execute(
        "SELECT best_bid_before, best_ask_before "
        "FROM execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_yes'"
    ).fetchone() == (0.49, 0.53)


def test_websocket_subscribes_before_rest_seed(monkeypatch):
    import websockets

    conn, writer = _conn_writer()
    order: list[str] = []
    stop = asyncio.Event()
    connect_kwargs: dict[str, object] = {}

    class FakeWebSocket:
        async def send(self, _payload):  # noqa: ANN001
            order.append("subscribe")

        def __aiter__(self):
            return self

        async def __anext__(self):
            stop.set()
            raise StopAsyncIteration

    class FakeConnect:
        async def __aenter__(self):
            order.append("connect")
            return FakeWebSocket()

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    def connect(_endpoint, **kwargs):  # noqa: ANN001
        connect_kwargs.update(kwargs)
        return FakeConnect()

    monkeypatch.setattr(websockets, "connect", connect)
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
        ),
        fetch_orderbook=lambda token_id: (
            order.append("seed")
            or {
                "asset_id": token_id,
                "market": "0xcondition",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": "seed-hash",
            }
        ),
    )

    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
        )
    )

    assert order == ["connect", "subscribe", "seed"]
    assert connect_kwargs["max_queue"] == 1024
    transitions = conn.execute(
        "SELECT transition FROM market_channel_connectivity_events ORDER BY occurred_at"
    ).fetchall()
    assert transitions == [("connected",)]


def test_subscription_universe_sync_sends_only_delta_off_event_loop():
    conn, writer = _conn_writer()
    sent: list[dict] = []
    loader_thread_ids: list[int] = []
    loop_thread_id = threading.get_ident()

    class FakeWebSocket:
        async def send(self, payload):  # noqa: ANN001
            sent.append(json.loads(payload))

    next_metadata = _metadata("token-2", outcome_label="NO")

    def reload_token_metadata():
        loader_thread_ids.append(threading.get_ident())
        return next_metadata

    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
    )
    service = MarketChannelOnlineService(
        ingestor,
        reload_token_metadata=reload_token_metadata,
    )
    subscribed = {"token-1"}

    asyncio.run(
        service._sync_subscription_universe(
            FakeWebSocket(),
            subscribed_token_ids=subscribed,
            write_gate=nullcontext(),
            commit=conn.commit,
            logger=None,
        )
    )

    assert loader_thread_ids and loader_thread_ids[0] != loop_thread_id
    assert sent == [
        {"operation": "subscribe", "assets_ids": ["token-2"]},
        {"operation": "unsubscribe", "assets_ids": ["token-1"]},
    ]
    assert subscribed == {"token-2"}
    assert ingestor.active_token_ids_open_at() == {"token-2"}
    assert service.subscription_add_count == 1
    assert service.subscription_remove_count == 1


def test_subscription_reload_atomically_refreshes_money_path_depth_repair_priority():
    from src.events.triggers.market_channel_ingestor import MarketTokenUniverse

    conn, writer = _conn_writer()
    sent: list[dict] = []

    class FakeWebSocket:
        async def send(self, payload):  # noqa: ANN001
            sent.append(json.loads(payload))

    priority_tokens = {
        "new-held",
        "new-candidate",
        "new-rest",
        "new-day0",
    }
    metadata = {
        token_id: next(iter(_metadata(token_id).values()))
        for token_id in priority_tokens
    }
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"old"},
            token_metadata=_metadata("old"),
        ),
        reload_token_metadata=lambda: MarketTokenUniverse(
            token_metadata=metadata,
            seed_first_token_ids=tuple(sorted(priority_tokens)),
            depth_repair_token_ids=tuple(
                sorted(priority_tokens - {"new-day0"})
            ),
        ),
        seed_first_token_ids={"old"},
    )
    subscribed = {"old"}

    asyncio.run(
        service._sync_subscription_universe(
            FakeWebSocket(),
            subscribed_token_ids=subscribed,
            write_gate=nullcontext(),
            commit=conn.commit,
            logger=None,
        )
    )
    for token_id in priority_tokens:
        service._record_current_generation_depth(
            {
                "event_type": "best_bid_ask",
                "asset_id": token_id,
                "market": "0xcondition",
                "best_bid": "0.49",
                "best_ask": "0.51",
            }
        )

    assert subscribed == priority_tokens
    assert set(service.seed_first_token_ids) == priority_tokens
    assert service._missing_depth_tokens == priority_tokens - {"new-day0"}
    assert sent == [
        {"operation": "subscribe", "assets_ids": sorted(priority_tokens)},
        {"operation": "unsubscribe", "assets_ids": ["old"]},
    ]


def test_websocket_adds_new_token_without_reconnect(monkeypatch):
    import websockets

    conn, writer = _conn_writer()
    stop = asyncio.Event()
    sent: list[dict] = []
    connect_count = 0

    class FakeWebSocket:
        async def send(self, payload):  # noqa: ANN001
            sent.append(json.loads(payload))

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(0.05)
            stop.set()
            raise StopAsyncIteration

    class FakeConnect:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    def connect(_endpoint, **_kwargs):  # noqa: ANN001
        nonlocal connect_count
        connect_count += 1
        return FakeConnect()

    monkeypatch.setattr(websockets, "connect", connect)
    metadata = _metadata()
    metadata.update(_metadata("token-2", outcome_label="NO"))
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
        ),
        reload_token_metadata=lambda: metadata,
        universe_refresh_interval_seconds=0.01,
    )

    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
        )
    )

    assert connect_count == 1
    assert sent[0] == {
        "assets_ids": ["token-1"],
        "type": "market",
        "custom_feature_enabled": True,
    }
    assert {"operation": "subscribe", "assets_ids": ["token-2"]} in sent[1:]
    assert not any(message.get("operation") == "unsubscribe" for message in sent)


def test_websocket_delta_is_consumed_while_rest_seed_is_in_flight(monkeypatch):
    import websockets

    conn, writer = _conn_writer()
    order = []
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    stop = asyncio.Event()
    proofs = []

    def fetch(token_id):  # noqa: ANN001
        order.append("seed_started")
        fetch_started.set()
        assert release_fetch.wait(timeout=2.0)
        order.append("seed_finished")
        return {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "seed-hash",
        }

    class FakeWebSocket:
        emitted = False

        async def send(self, _payload):  # noqa: ANN001
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted:
                stop.set()
                raise StopAsyncIteration
            await asyncio.to_thread(fetch_started.wait, 2.0)
            self.emitted = True
            order.append("ws_delta")
            release_fetch.set()
            return json.dumps(
                {
                    "event_type": "best_bid_ask",
                    "asset_id": "token-1",
                    "market": "0xcondition",
                    "best_bid": "0.49",
                    "best_ask": "0.51",
                    "hash": "delta-hash",
                }
            )

    class FakeConnect:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: FakeConnect())
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
        ),
        fetch_orderbook=fetch,
        continuity_sink=proofs.append,
        continuity_publish_interval_seconds=0.0,
    )

    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
        )
    )

    assert order.index("ws_delta") < order.index("seed_finished")
    assert len(proofs) == 2
    assert proofs[-1]["connected"] is True
    assert proofs[-1]["observed_at"] >= proofs[-1]["connected_at"]


def test_websocket_initial_book_eliminates_redundant_rest_seed(monkeypatch):
    import websockets

    conn, writer = _conn_writer()
    stop = asyncio.Event()
    fetch_calls: list[str] = []

    class FakeWebSocket:
        emitted = False

        async def send(self, _payload):  # noqa: ANN001
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted:
                stop.set()
                raise StopAsyncIteration
            self.emitted = True
            return json.dumps(
                {
                    "event_type": "book",
                    "asset_id": "token-1",
                    "market": "0xcondition",
                    "bids": [{"price": "0.48", "size": "10"}],
                    "asks": [{"price": "0.52", "size": "10"}],
                    "hash": "initial-ws-book",
                    "timestamp": "1766789469958",
                }
            )

    class FakeConnect:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: FakeConnect())
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
            coalescer=EventCoalescer(max_market_keys=8),
        ),
        fetch_orderbook=lambda token_id: fetch_calls.append(token_id) or {},
        initial_book_grace_seconds=0.05,
    )

    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
        )
    )

    assert fetch_calls == []
    assert service._current_generation_depth_tokens == {"token-1"}
    assert conn.execute(
        "SELECT book_hash_before FROM execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_yes'"
    ).fetchone()[0] == "initial-ws-book"


def test_priority_rest_seed_precedes_ws_covered_broad_fallback():
    conn, writer = _conn_writer()
    metadata = {
        "token-priority": _metadata("token-priority")["token-priority"],
        "token-ws": _metadata("token-ws")["token-ws"],
    }
    fetch_calls: list[str] = []

    def fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        return {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"rest-{token_id}",
        }

    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids=set(metadata),
            token_metadata=metadata,
        ),
        fetch_orderbook=fetch,
        seed_first_token_ids={"token-priority"},
        initial_book_grace_seconds=0,
    )
    service._current_generation_depth_tokens.add("token-ws")

    written = asyncio.run(
        service._seed_subscribed_books(
            active_token_ids=set(metadata),
            write_gate=nullcontext(),
            commit=conn.commit,
            logger=None,
        )
    )

    assert written == 1
    assert fetch_calls == ["token-priority"]


def test_bba_only_quote_queues_and_repairs_missing_depth(tmp_path):
    db_path = tmp_path / "market-channel-repair.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    ensure_table(conn)
    writer = EventWriter(conn)
    coalescer = EventCoalescer(max_market_keys=8)
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        coalescer=coalescer,
    )
    fetch_batches: list[list[str]] = []
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda _token_id: {},
        fetch_orderbooks=lambda token_ids: (
            fetch_batches.append(list(token_ids))
            or {
                token_id: {
                    "asset_id": token_id,
                    "market": "0xcondition",
                    "bids": [{"price": "0.49", "size": "10"}],
                    "asks": [{"price": "0.51", "size": "10"}],
                    "hash": f"repair-{token_id}",
                }
                for token_id in token_ids
            }
        ),
        seed_first_token_ids={"token-1"},
    )
    book = {
        "event_type": "book",
        "asset_id": "token-1",
        "market": "0xcondition",
        "bids": [{"price": "0.48", "size": "10"}],
        "asks": [{"price": "0.52", "size": "10"}],
        "hash": "initial-book",
    }
    ingestor.handle_message(book, received_at="2026-07-19T09:00:00+00:00")
    service._record_current_generation_depth(book)
    ingestor.flush_coalesced(commit=conn.commit, rollback=conn.rollback)
    bba = {
        "event_type": "best_bid_ask",
        "asset_id": "token-1",
        "market": "0xcondition",
        "best_bid": "0.49",
        "best_ask": "0.51",
    }
    ingestor.handle_message(bba, received_at="2026-07-19T09:00:01+00:00")
    service._record_current_generation_depth(bba)
    ingestor.flush_coalesced(commit=conn.commit, rollback=conn.rollback)
    service._quote_projection_pump_active = True
    assert service._missing_depth_tokens == {"token-1"}
    assert service._current_generation_depth_tokens == set()

    quote_flush_wake = asyncio.Event()
    written = asyncio.run(
        service._repair_missing_depth_once(
            active_token_ids={"token-1"},
            write_gate=nullcontext(),
            commit=conn.commit,
            quote_flush_wake=quote_flush_wake,
            logger=None,
        )
    )

    assert written == 1
    assert fetch_batches == [["token-1"]]
    assert service._missing_depth_tokens == {"token-1"}
    assert service._depth_repair_inflight_tokens == {"token-1"}
    assert ingestor.quote_cache.get("token-1").depth_json is not None
    assert quote_flush_wake.is_set()
    assert conn.in_transaction is False

    independent = sqlite3.connect(db_path)
    assert independent.execute(
        "SELECT depth_before_json FROM execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_yes'"
    ).fetchone()[0] is None

    def fail_commit() -> None:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        ingestor.flush_coalesced(commit=fail_commit, rollback=conn.rollback)
    assert coalescer.pending_counts() == {"lossless": 0, "market": 1}
    assert service._missing_depth_tokens == {"token-1"}
    assert service._depth_repair_inflight_tokens == {"token-1"}
    assert independent.execute(
        "SELECT depth_before_json FROM execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_yes'"
    ).fetchone()[0] is None

    connection_done = asyncio.Event()
    connection_done.set()
    initial_seed_done = asyncio.Event()
    initial_seed_done.set()
    asyncio.run(
        service._flush_quote_projection_forever(
            wake=quote_flush_wake,
            connection_done=connection_done,
            initial_seed_done=initial_seed_done,
            active_token_ids={"token-1"},
            write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
            logger=None,
            depth_repair_wake=asyncio.Event(),
        )
    )
    assert service._missing_depth_tokens == set()
    assert service._depth_repair_inflight_tokens == set()
    assert independent.execute(
        "SELECT depth_before_json FROM execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_yes'"
    ).fetchone()[0] is not None
    independent.close()


def test_unchanged_bba_old_durable_depth_does_not_satisfy_repair():
    conn, writer = _conn_writer()
    coalescer = EventCoalescer(max_market_keys=8)
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        coalescer=coalescer,
    )
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda _token_id: {},
        seed_first_token_ids={"token-1"},
    )
    book = {
        "event_type": "book",
        "asset_id": "token-1",
        "market": "0xcondition",
        "bids": [{"price": "0.49", "size": "10"}],
        "asks": [{"price": "0.51", "size": "10"}],
        "hash": "old-full-book",
    }
    ingestor.handle_message(book, received_at="2026-07-19T09:00:00+00:00")
    ingestor.flush_coalesced(commit=conn.commit, rollback=conn.rollback)

    unchanged_bba = {
        "event_type": "best_bid_ask",
        "asset_id": "token-1",
        "market": "0xcondition",
        "best_bid": "0.49",
        "best_ask": "0.51",
    }
    assert (
        ingestor.handle_message(
            unchanged_bba,
            received_at="2026-07-19T09:00:01+00:00",
        )
        is None
    )
    service._record_current_generation_depth(unchanged_bba)

    assert ingestor.quote_cache.get("token-1").depth_json is None
    assert service._missing_depth_tokens == {"token-1"}
    assert service._clear_durable_missing_depth_tokens(logger=None) == 0
    assert service._missing_depth_tokens == {"token-1"}


def test_bba_only_quote_does_not_repair_nonpriority_depth():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
    )
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda _token_id: {},
    )
    bba = {
        "event_type": "best_bid_ask",
        "asset_id": "token-1",
        "market": "0xcondition",
        "best_bid": "0.49",
        "best_ask": "0.51",
    }

    ingestor.handle_message(bba, received_at="2026-07-19T09:00:01+00:00")
    service._record_current_generation_depth(bba)

    assert service._missing_depth_tokens == set()


def test_depth_repair_return_cannot_resurrect_demoted_inflight_marker():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
    )
    service = MarketChannelOnlineService(
        ingestor,
        depth_repair_token_ids={"token-1"},
    )
    service._missing_depth_tokens.add("token-1")

    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_seed(**_kwargs):  # noqa: ANN003
            started.set()
            await release.wait()
            ingestor.handle_message(
                {
                    "event_type": "book",
                    "asset_id": "token-1",
                    "market": "0xcondition",
                    "bids": [{"price": "0.49", "size": "10"}],
                    "asks": [{"price": "0.51", "size": "10"}],
                    "hash": "late-repair",
                },
                received_at="2026-07-19T09:00:02+00:00",
            )
            return 1

        service.seed_rest_books_after_subscribe = delayed_seed
        repair = asyncio.create_task(
            service._repair_missing_depth_once(
                active_token_ids={"token-1"},
                write_gate=nullcontext(),
                commit=conn.commit,
                quote_flush_wake=asyncio.Event(),
                logger=None,
            )
        )
        await started.wait()
        service._replace_depth_repair_token_ids(())
        release.set()
        await repair

        assert service._missing_depth_tokens == set()
        assert service._depth_repair_inflight_tokens == set()
        assert service._depth_repair_quote_seen_at == {}

        service._replace_depth_repair_token_ids(("token-1",))
        bba = {
            "event_type": "best_bid_ask",
            "asset_id": "token-1",
            "market": "0xcondition",
            "best_bid": "0.49",
            "best_ask": "0.51",
        }
        ingestor.handle_message(bba, received_at="2026-07-19T09:00:03+00:00")
        service._record_current_generation_depth(bba)

        assert service._missing_depth_tokens == {"token-1"}
        assert service._depth_repair_inflight_tokens == set()
        assert (
            service._missing_depth_tokens - service._depth_repair_inflight_tokens
        ) == {"token-1"}

    asyncio.run(scenario())


def test_depth_repair_failure_does_not_escape_background_lane(monkeypatch):
    from src.events.triggers import market_channel_ingestor as market_channel

    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
    )
    ingestor.handle_message(
        {
            "event_type": "best_bid_ask",
            "asset_id": "token-1",
            "market": "0xcondition",
            "best_bid": "0.49",
            "best_ask": "0.51",
        },
        received_at="2026-07-19T09:00:01+00:00",
    )
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda _token_id: {},
        fetch_orderbooks=lambda _token_ids: (_ for _ in ()).throw(
            TimeoutError("depth repair timeout")
        ),
    )
    service._missing_depth_tokens.add("token-1")
    wake = asyncio.Event()
    wake.set()
    connection_done = asyncio.Event()
    initial_seed_done = asyncio.Event()
    initial_seed_done.set()

    async def bounded_sleep(delay: float) -> None:
        if delay == market_channel.MARKET_CHANNEL_DEPTH_REPAIR_RETRY_SECONDS:
            connection_done.set()

    monkeypatch.setattr(market_channel.asyncio, "sleep", bounded_sleep)
    asyncio.run(
        service._repair_missing_depth_forever(
            wake=wake,
            connection_done=connection_done,
            initial_seed_done=initial_seed_done,
            active_token_ids={"token-1"},
            write_gate=nullcontext(),
            commit=conn.commit,
            quote_flush_wake=asyncio.Event(),
            logger=None,
        )
    )

    assert service.depth_repair_fetch_count == 1
    assert service.depth_repair_failure_count == 1
    assert service._missing_depth_tokens == {"token-1"}


def test_depth_durability_probe_waits_for_market_backlog_to_drain(monkeypatch):
    conn, writer = _conn_writer()
    token_ids = {f"token-{index}" for index in range(129)}
    metadata = {
        token_id: _metadata(token_id)[token_id]
        for token_id in token_ids
    }
    coalescer = EventCoalescer(max_market_keys=256)
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=token_ids,
        token_metadata=metadata,
        coalescer=coalescer,
    )
    service = MarketChannelOnlineService(ingestor)
    for token_id in token_ids:
        ingestor.handle_message(
            {
                "event_type": "book",
                "asset_id": token_id,
                "market": "0xcondition",
                "bids": [{"price": "0.49", "size": "10"}],
                "asks": [{"price": "0.51", "size": "10"}],
            },
            received_at="2026-07-19T09:00:00+00:00",
        )

    pending_at_probe: list[dict[str, int]] = []

    def record_probe(*, logger):  # noqa: ANN001
        pending_at_probe.append(coalescer.pending_counts())
        return 0

    monkeypatch.setattr(service, "_clear_durable_missing_depth_tokens", record_probe)
    wake = asyncio.Event()
    wake.set()
    connection_done = asyncio.Event()
    connection_done.set()
    initial_seed_done = asyncio.Event()
    initial_seed_done.set()
    asyncio.run(
        service._flush_quote_projection_forever(
            wake=wake,
            connection_done=connection_done,
            initial_seed_done=initial_seed_done,
            active_token_ids=token_ids,
            write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
            logger=None,
        )
    )

    assert pending_at_probe == [{"lossless": 0, "market": 0}]


def test_websocket_routes_quote_and_world_events_to_independent_write_lanes(
    monkeypatch,
):
    import websockets

    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trade_conn = sqlite3.connect(":memory:")
    init_schema_trade_only(trade_conn)
    stop = asyncio.Event()
    entered: list[str] = []

    class Gate:
        def __init__(self, name: str) -> None:
            self.name = name

        def __enter__(self):
            entered.append(f"enter:{self.name}")
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            entered.append(f"exit:{self.name}")
            return False

    class FakeWebSocket:
        emitted = False

        async def send(self, _payload):  # noqa: ANN001
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted:
                stop.set()
                raise StopAsyncIteration
            self.emitted = True
            return json.dumps(
                [
                    {
                        "event_type": "book",
                        "asset_id": "token-1",
                        "market": "0xcondition",
                        "bids": [{"price": "0.48", "size": "10"}],
                        "asks": [{"price": "0.52", "size": "10"}],
                        "hash": "quote-hash",
                    },
                    {
                        "event_type": "new_market",
                        "condition_id": "0xcondition",
                        "clob_token_ids": ["token-1"],
                    },
                ]
            )

    class FakeConnect:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: FakeConnect())
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            EventWriter(world_conn),
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
            feasibility_conn=trade_conn,
        )
    )

    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=Gate("trade"),
            world_event_write_gate=Gate("world"),
            commit=trade_conn.commit,
            rollback=trade_conn.rollback,
            world_event_commit=world_conn.commit,
            world_event_rollback=world_conn.rollback,
        )
    )

    assert entered == [
        "enter:trade",
        "exit:trade",
        "enter:trade",
        "exit:trade",
        "enter:world",
        "exit:world",
    ]
    assert trade_conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_latest"
    ).fetchone()[0] == 2
    assert trade_conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_evidence"
    ).fetchone()[0] == 0
    assert world_conn.execute(
        "SELECT COUNT(*) FROM opportunity_events "
        "WHERE event_type='NEW_MARKET_DISCOVERED'"
    ).fetchone()[0] == 1


def test_coalesced_quote_commit_failure_requeues_without_false_dedupe():
    conn, writer = _conn_writer()
    seen: list[str] = []
    coalescer = EventCoalescer(max_market_keys=8)
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        coalescer=coalescer,
        market_event_sink=lambda events: seen.extend(event.event_id for event in events),
        market_event_sink_independently_coordinated=True,
    )
    message = {
        "event_type": "book",
        "asset_id": "token-1",
        "market": "0xcondition",
        "bids": [{"price": "0.48", "size": "10"}],
        "asks": [{"price": "0.52", "size": "10"}],
        "hash": "retry-hash",
        "timestamp": "1766789469958",
    }
    assert ingestor.handle_message(
        message,
        received_at="2026-05-24T10:00:00+00:00",
    ) is None

    def fail_commit() -> None:
        raise sqlite3.OperationalError("database is locked")

    with ingestor.defer_market_event_sink():
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            ingestor.flush_coalesced(
                market_budget=1,
                commit=fail_commit,
                rollback=conn.rollback,
            )

    assert coalescer.pending_counts() == {"lossless": 0, "market": 1}
    assert conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_latest"
    ).fetchone()[0] == 0
    assert seen == []

    with ingestor.defer_market_event_sink():
        results = ingestor.flush_coalesced(
            market_budget=1,
            commit=conn.commit,
            rollback=conn.rollback,
        )
    ingestor.flush_deferred_market_event_sink()

    assert len(results) == 1
    assert results[0].inserted is True
    assert coalescer.pending_counts() == {"lossless": 0, "market": 0}
    assert conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_latest"
    ).fetchone()[0] == 2
    assert seen == [results[0].event_id]


def test_quote_write_backpressure_retains_websocket_and_latest_event(monkeypatch):
    import websockets

    conn, writer = _conn_writer()
    stop = asyncio.Event()
    proofs: list[dict[str, object]] = []

    class FlakyQuoteGate:
        def __init__(self) -> None:
            self.enters = 0

        def __enter__(self):
            self.enters += 1
            if self.enters == 2:
                raise TimeoutError("trade quote writer busy")
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    class FakeWebSocket:
        emitted = 0

        async def send(self, _payload):  # noqa: ANN001
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted == 2:
                stop.set()
                raise StopAsyncIteration
            self.emitted += 1
            return json.dumps(
                {
                    "event_type": "book",
                    "asset_id": "token-1",
                    "market": "0xcondition",
                    "bids": [{"price": "0.48", "size": "10"}],
                    "asks": [{"price": "0.52", "size": "10"}],
                    "hash": f"quote-hash-{self.emitted}",
                    "timestamp": str(1766789469958 + self.emitted * 1000),
                }
            )

    class FakeConnect:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: FakeConnect())
    gate = FlakyQuoteGate()
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
            coalescer=EventCoalescer(max_market_keys=8),
        ),
        continuity_sink=proofs.append,
        continuity_publish_interval_seconds=0.0,
    )

    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=gate,
            commit=conn.commit,
            rollback=conn.rollback,
        )
    )

    assert gate.enters == 3
    assert conn.execute(
        "SELECT book_hash_before FROM execution_feasibility_latest "
        "WHERE token_id='token-1' AND direction='buy_yes'"
    ).fetchone()[0] == "quote-hash-2"
    assert conn.execute(
        "SELECT transition FROM market_channel_connectivity_events "
        "ORDER BY occurred_at"
    ).fetchall() == [("connected",)]
    assert len(proofs) == 2
    assert all(proof["connected"] is True for proof in proofs)


def test_quote_burst_coalesces_before_bounded_write_retry(monkeypatch):
    import websockets

    conn, writer = _conn_writer()
    stop = asyncio.Event()

    class BusyQuoteGate:
        def __init__(self) -> None:
            self.enters = 0

        def __enter__(self):
            self.enters += 1
            if self.enters > 1:
                raise TimeoutError("trade quote writer busy")
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    class FakeWebSocket:
        emitted = 0

        async def send(self, _payload):  # noqa: ANN001
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted == 100:
                stop.set()
                raise StopAsyncIteration
            self.emitted += 1
            await asyncio.sleep(0)
            return json.dumps(
                {
                    "event_type": "book",
                    "asset_id": "token-1",
                    "market": "0xcondition",
                    "bids": [{"price": "0.48", "size": "10"}],
                    "asks": [{"price": "0.52", "size": "10"}],
                    "hash": f"burst-{self.emitted}",
                    "timestamp": str(1766789469958 + self.emitted),
                }
            )

    class FakeConnect:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: FakeConnect())
    gate = BusyQuoteGate()
    coalescer = EventCoalescer(max_market_keys=8)
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        coalescer=coalescer,
    )
    service = MarketChannelOnlineService(ingestor)

    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=gate,
            commit=conn.commit,
            rollback=conn.rollback,
        )
    )

    assert gate.enters <= 4
    assert service.quote_projection_backpressure_count == gate.enters - 1
    assert coalescer.pending_counts() == {"lossless": 0, "market": 1}
    assert ingestor.quote_cache.get("token-1").book_hash == "burst-100"


def test_quote_projection_contention_retries_with_bounded_backoff(monkeypatch):
    from src.events.triggers import market_channel_ingestor as market_channel

    conn, writer = _conn_writer()
    coalescer = EventCoalescer(max_market_keys=8)
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        coalescer=coalescer,
    )
    ingestor.handle_message(
        {
            "event_type": "book",
            "asset_id": "token-1",
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "backoff-quote",
            "timestamp": "1766789469958",
        },
        received_at="2026-07-17T12:00:00+00:00",
    )
    service = MarketChannelOnlineService(ingestor)
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(market_channel.asyncio, "sleep", fake_sleep)

    async def run():
        wake = asyncio.Event()
        wake.set()
        connection_done = asyncio.Event()
        initial_seed_done = asyncio.Event()
        initial_seed_done.set()

        class Gate:
            enters = 0

            def __enter__(self):
                self.enters += 1
                if self.enters <= 3:
                    raise TimeoutError("trade quote writer busy")
                connection_done.set()
                return self

            def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        await service._flush_quote_projection_forever(
            wake=wake,
            connection_done=connection_done,
            initial_seed_done=initial_seed_done,
            active_token_ids={"token-1"},
            write_gate=Gate(),
            commit=conn.commit,
            rollback=conn.rollback,
            logger=None,
        )

    asyncio.run(run())

    assert delays == [0.05, 0.1, 0.2]
    assert service.quote_projection_backpressure_count == 3
    assert coalescer.pending_counts() == {"lossless": 0, "market": 0}


def test_continuity_publication_coalesces_quotes_but_not_state_changes(monkeypatch):
    proofs: list[dict] = []
    monotonic = iter((10.0, 10.1, 10.26, 10.27))
    monkeypatch.setattr(
        "src.events.triggers.market_channel_ingestor.time.monotonic",
        lambda: next(monotonic),
    )
    service = MarketChannelOnlineService(
        object(),
        continuity_sink=proofs.append,
        continuity_publish_interval_seconds=0.25,
    )

    for connected, observed_at in (
        (True, "2026-07-17T10:00:00+00:00"),
        (True, "2026-07-17T10:00:00.100000+00:00"),
        (True, "2026-07-17T10:00:00.260000+00:00"),
        (False, "2026-07-17T10:00:00.270000+00:00"),
    ):
        service._publish_continuity(
            connected=connected,
            observed_at=observed_at,
            active_token_count=2,
        )

    assert [proof["observed_at"] for proof in proofs] == [
        "2026-07-17T10:00:00+00:00",
        "2026-07-17T10:00:00.260000+00:00",
        "2026-07-17T10:00:00.270000+00:00",
    ]
    assert [proof["connected"] for proof in proofs] == [True, True, False]


def test_disconnect_transition_commits_after_rollback(monkeypatch):
    import websockets

    conn, writer = _conn_writer()
    stop = asyncio.Event()

    class BrokenWebSocket:
        async def send(self, _payload):  # noqa: ANN001
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            stop.set()
            raise ConnectionError("socket closed")

    class FakeConnect:
        async def __aenter__(self):
            return BrokenWebSocket()

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: FakeConnect())
    service = MarketChannelOnlineService(
        MarketChannelIngestor(
            writer,
            active_token_ids={"token-1"},
            token_metadata=_metadata(),
        )
    )

    asyncio.run(
        service.run_websocket_forever(
            stop_event=stop,
            reconnect_delay_seconds=0,
            quote_write_gate=nullcontext(),
            commit=conn.commit,
            rollback=conn.rollback,
        )
    )

    transitions = conn.execute(
        "SELECT transition FROM market_channel_connectivity_events ORDER BY occurred_at"
    ).fetchall()
    assert transitions == [("connected",), ("disconnected",)]


def test_rest_seed_commits_deferred_sink_inside_world_writer_gate():
    conn, writer = _conn_writer()
    conn.execute("CREATE TABLE derived_sink_writes (event_id TEXT PRIMARY KEY)")
    order: list[str] = []
    held = False

    class RecordingWorldMutex:
        def __enter__(self):
            nonlocal held
            held = True
            order.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            nonlocal held
            order.append("exit")
            held = False
            return False

    def sink(events) -> None:  # noqa: ANN001
        assert held is True
        order.append("sink")
        conn.executemany(
            "INSERT INTO derived_sink_writes(event_id) VALUES (?)",
            [(event.event_id,) for event in events],
        )

    def commit() -> None:
        conn.commit()
        order.append("commit")

    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        market_event_sink=sink,
    )
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda token_id: {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        },
    )

    written = service.seed_rest_books_in_chunks(
        token_ids=["token-1"],
        received_at="2026-07-13T12:00:00+00:00",
        write_gate=RecordingWorldMutex(),
        commit=commit,
        chunk_size=1,
    )

    assert written == 1
    assert order == ["enter", "sink", "commit", "exit"]
    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) FROM derived_sink_writes").fetchone()[0] == 1


def test_rest_seed_independent_sink_flushes_after_world_writer_gate():
    conn, writer = _conn_writer()
    order: list[str] = []
    held = False

    class RecordingWorldMutex:
        def __enter__(self):
            nonlocal held
            held = True
            order.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            nonlocal held
            order.append("exit")
            held = False
            return False

    def sink(events) -> None:  # noqa: ANN001
        assert events
        assert held is False
        assert conn.in_transaction is False
        order.append("sink")

    def commit() -> None:
        conn.commit()
        order.append("commit")

    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        market_event_sink=sink,
        market_event_sink_independently_coordinated=True,
    )
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda token_id: {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        },
    )

    written = service.seed_rest_books_in_chunks(
        token_ids=["token-1"],
        received_at="2026-07-14T09:00:00+00:00",
        write_gate=RecordingWorldMutex(),
        commit=commit,
        chunk_size=1,
    )

    assert written == 1
    assert order == ["enter", "commit", "exit", "sink"]
    assert conn.in_transaction is False


def test_independent_sink_failure_retains_events_for_retry():
    conn, writer = _conn_writer()
    calls: list[list[str]] = []

    def sink(events) -> None:  # noqa: ANN001
        calls.append([event.event_id for event in events])
        if len(calls) == 1:
            raise TimeoutError("world writer busy")

    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        market_event_sink=sink,
        market_event_sink_independently_coordinated=True,
    )
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda token_id: {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        },
    )

    assert service.seed_rest_books_in_chunks(
        token_ids=["token-1"],
        received_at="2026-07-14T09:00:00+00:00",
        write_gate=nullcontext(),
        commit=conn.commit,
        chunk_size=1,
    ) == 1
    assert len(ingestor._deferred_market_event_sink_events) == 1

    ingestor._deferred_market_event_sink_retry_not_before = 0.0
    ingestor.flush_deferred_market_event_sink()

    assert calls[0] == calls[1]
    assert ingestor._deferred_market_event_sink_events == []


def test_independent_sink_sustained_failure_is_bounded_and_backed_off():
    import types

    conn, writer = _conn_writer()
    attempts = 0

    def sink(_events) -> None:  # noqa: ANN001
        nonlocal attempts
        attempts += 1
        raise TimeoutError("world writer busy")

    tokens = {"token-1", "token-2", "token-3"}
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=tokens,
        token_metadata={token: _metadata(token)[token] for token in tokens},
        market_event_sink=sink,
        market_event_sink_independently_coordinated=True,
    )

    def event(token: str, serial: int):
        return types.SimpleNamespace(
            event_id=f"event-{token}-{serial}",
            event_type="BOOK_SNAPSHOT",
            entity_key=f"book:{token}",
            payload_json=f'{{"token_id":"{token}"}}',
        )

    for serial in range(12):
        token = f"token-{serial % 3 + 1}"
        with ingestor.defer_market_event_sink():
            ingestor._notify_market_event_sink([event(token, serial)])
            ingestor._deferred_market_event_sink_retry_not_before = 0.0
            ingestor.flush_deferred_market_event_sink()

    assert attempts == 12
    assert len(ingestor._deferred_market_event_sink_events) == len(tokens)
    assert ingestor.deferred_market_event_sink_coalesced_count == 9
    assert ingestor.deferred_market_event_sink_overflow_count == 0
    assert ingestor.deferred_market_event_sink_retry_count == 12
    assert ingestor._deferred_market_event_sink_retry_not_before > 0.0
    ingestor.flush_deferred_market_event_sink()
    assert attempts == 12


def test_independent_sink_overflow_preserves_every_active_token():
    import json
    import types

    conn, writer = _conn_writer()
    active_tokens = {f"active-{index}" for index in range(130)}
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=active_tokens,
        token_metadata={},
        market_event_sink=lambda _events: None,
        market_event_sink_independently_coordinated=True,
    )

    def event(token: str):
        return types.SimpleNamespace(
            event_id=f"event-{token}",
            event_type="BOOK_SNAPSHOT",
            entity_key=f"book:{token}",
            payload_json=json.dumps({"token_id": token}),
        )

    with ingestor.defer_market_event_sink():
        for token in sorted(active_tokens):
            ingestor._notify_market_event_sink([event(token)])
        for index in range(100):
            ingestor._notify_market_event_sink([event(f"nonactive-{index}")])

    retained = {
        json.loads(item.payload_json)["token_id"]
        for item in ingestor._deferred_market_event_sink_events
    }
    assert active_tokens <= retained
    assert len(retained) == ingestor._deferred_market_event_sink_limit
    assert ingestor.deferred_market_event_sink_overflow_count == 68


def test_rest_seed_write_backpressure_keeps_committed_chunks_and_stops():
    conn, writer = _conn_writer()
    cache = QuoteCache()
    metadata = {
        f"token-{idx}": _metadata(f"token-{idx}")[f"token-{idx}"]
        for idx in range(3)
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
        quote_cache=cache,
    )
    fetch_calls: list[str] = []
    commit_counts: list[int] = []

    def fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        return {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        }

    class FlakyWorldMutex:
        def __init__(self) -> None:
            self.enters = 0

        def __enter__(self):
            self.enters += 1
            if self.enters == 2:
                raise TimeoutError("world write busy")
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    service = MarketChannelOnlineService(ingestor, fetch_orderbook=fetch)
    written = service.seed_rest_books_in_chunks(
        token_ids=[f"token-{idx}" for idx in range(3)],
        received_at="2026-05-24T10:00:00+00:00",
        write_gate=FlakyWorldMutex(),
        commit=lambda: commit_counts.append(
            conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
        ),
        chunk_size=1,
    )

    assert written == 1
    assert fetch_calls == ["token-0", "token-1"]
    assert commit_counts == [2]
    assert service.rest_seed_backpressure_count == 1
    assert service.rest_seed_backpressure_reason == "world write busy"
    assert (
        conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
        == 2
    )


def test_rest_seed_uses_batch_orderbook_fetch_when_available():
    from contextlib import nullcontext

    conn, writer = _conn_writer()
    metadata = {
        f"token-{idx}": _metadata(f"token-{idx}")[
            f"token-{idx}"
        ]
        for idx in range(5)
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
        quote_cache=QuoteCache(),
    )
    batch_calls: list[list[str]] = []

    def fetch_one(token_id: str) -> dict:
        raise AssertionError(f"single-token fetch should not run for {token_id}")

    def fetch_many(token_ids: list[str]) -> dict[str, dict]:
        call = list(token_ids)
        batch_calls.append(call)
        return {
            token_id: {
                "asset_id": token_id,
                "market": "0xcondition",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": f"hash-{token_id}",
            }
            for token_id in call
        }

    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=fetch_one,
        fetch_orderbooks=fetch_many,
    )

    written = service.seed_rest_books_in_chunks(
        token_ids=set(metadata),
        received_at="2026-05-24T10:00:00+00:00",
        write_gate=nullcontext(),
        commit=conn.commit,
        chunk_size=2,
    )

    assert written == 5
    assert batch_calls == [["token-0", "token-1"], ["token-2", "token-3"], ["token-4"]]
    assert (
        conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
        == 10
    )


def test_rest_seed_falls_back_for_partial_batch_orderbook_response():
    from contextlib import nullcontext

    conn, writer = _conn_writer()
    metadata = {
        "token-0": _metadata("token-0")["token-0"],
        "token-1": _metadata("token-1")["token-1"],
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
        quote_cache=QuoteCache(),
    )
    batch_calls: list[list[str]] = []
    single_calls: list[str] = []

    def _book(token_id: str) -> dict:
        return {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        }

    def fetch_one(token_id: str) -> dict:
        single_calls.append(token_id)
        return _book(token_id)

    def fetch_many(token_ids: list[str]) -> dict[str, dict]:
        batch_calls.append(list(token_ids))
        return {"token-0": _book("token-0")}

    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=fetch_one,
        fetch_orderbooks=fetch_many,
    )

    written = service.seed_rest_books_in_chunks(
        token_ids=["token-0", "token-1"],
        received_at="2026-05-24T10:00:00+00:00",
        write_gate=nullcontext(),
        commit=conn.commit,
        chunk_size=2,
    )

    assert written == 2
    assert batch_calls == [["token-0", "token-1"]]
    assert single_calls == ["token-1"]
    assert (
        conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
        == 4
    )


def test_rest_seed_deadline_stops_before_fetching_more_tokens():
    from contextlib import nullcontext

    conn, writer = _conn_writer()
    metadata = {
        "token-1": _metadata("token-1")["token-1"],
        "token-2": _metadata("token-2")["token-2"],
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
    )
    fetch_calls: list[str] = []
    service = MarketChannelOnlineService(
        ingestor,
        fetch_orderbook=lambda token_id: fetch_calls.append(token_id) or {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        },
    )

    written = service.seed_rest_books_in_chunks(
        token_ids=set(metadata),
        received_at="2026-05-24T10:00:00+00:00",
        write_gate=nullcontext(),
        commit=conn.commit,
        chunk_size=1,
        deadline_monotonic=0.0,
    )

    assert written == 0
    assert fetch_calls == []
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0] == 0


def test_rest_seed_preserves_ordered_priority_tokens():
    from contextlib import nullcontext

    conn, writer = _conn_writer()
    metadata = {
        "newer-token": _metadata("newer-token")["newer-token"],
        "stale-token": _metadata("stale-token")["stale-token"],
        "missing-token": _metadata("missing-token")["missing-token"],
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
    )
    fetch_calls: list[str] = []

    def fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        return {
            "asset_id": token_id,
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        }

    service = MarketChannelOnlineService(ingestor, fetch_orderbook=fetch)

    written = service.seed_rest_books_in_chunks(
        token_ids=["missing-token", "stale-token", "newer-token"],
        received_at="2026-05-24T10:00:00+00:00",
        write_gate=nullcontext(),
        commit=conn.commit,
        chunk_size=1,
    )

    assert written == 3
    assert fetch_calls == ["missing-token", "stale-token", "newer-token"]


def test_reconnect_rest_seed_chunks_preserve_gap_snapshot_and_commit_progressively():
    from contextlib import nullcontext

    conn, writer = _conn_writer()
    metadata = {
        f"token-{idx}": _metadata(f"token-{idx}")[
            f"token-{idx}"
        ]
        for idx in range(5)
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
        quote_cache=QuoteCache(),
    )
    fetch_calls: list[str] = []
    commit_counts: list[int] = []

    def fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        return {
            "asset_id": token_id,
            "event_type": "book",
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": f"hash-{token_id}",
        }

    service = MarketChannelOnlineService(ingestor, fetch_orderbook=fetch)
    service.connected = False
    service.gap_start = "2026-05-24T09:58:00+00:00"

    written = service.reconnect_rest_books_in_chunks(
        token_ids=set(metadata),
        received_at="2026-05-24T10:00:00+00:00",
        write_gate=nullcontext(),
        commit=lambda: commit_counts.append(
            conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
        ),
        chunk_size=2,
    )

    assert written == 5
    assert fetch_calls == [f"token-{idx}" for idx in range(5)]
    assert commit_counts == [4, 8, 10]
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    assert service.connected is True
    assert service.gap_start is None


def test_market_channel_quote_updates_latest_without_appending_history():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata())

    ingestor.handle_message(
        {
            "event_type": "book",
            "asset_id": "token-1",
            "market": "0xcondition",
            "outcome_label": "YES",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "hash-1",
            "timestamp": "1766789469958",
        },
        received_at="2026-05-24T10:00:00+00:00",
    )

    rows = conn.execute(
        "SELECT direction FROM execution_feasibility_latest ORDER BY direction"
    ).fetchall()
    assert rows == [("buy_yes",), ("sell_yes",)]
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0] == 0


def test_market_channel_quote_notifies_inserted_event_sink_once():
    conn, writer = _conn_writer()
    seen: list[list[str]] = []
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        market_event_sink=lambda events: seen.append([event.event_id for event in events]),
    )
    message = {
        "event_type": "book",
        "asset_id": "token-1",
        "market": "0xcondition",
        "outcome_label": "YES",
        "bids": [{"price": "0.48", "size": "10"}],
        "asks": [{"price": "0.52", "size": "10"}],
        "hash": "hash-1",
        "timestamp": "1766789469958",
    }

    first = ingestor.handle_message(message, received_at="2026-05-24T10:00:00+00:00")
    second = ingestor.handle_message(message, received_at="2026-05-24T10:00:00+00:00")

    assert first.inserted is True
    assert second.inserted is False
    assert len(seen) == 1
    assert len(seen[0]) == 1


def test_market_channel_same_top_of_book_bba_does_not_append_ignored_events():
    conn, writer = _conn_writer()
    seen: list[list[str]] = []
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        market_event_sink=lambda events: seen.append([event.event_id for event in events]),
    )

    base = {
        "event_type": "best_bid_ask",
        "asset_id": "token-1",
        "market": "0xcondition",
        "outcome_label": "YES",
        "best_bid": "0.48",
        "best_ask": "0.52",
        "hash": "hash-1",
        "timestamp": "1766789469958",
    }
    first = ingestor.handle_message(base, received_at="2026-05-24T10:00:00+00:00")
    same_touch = ingestor.handle_message(
        {**base, "hash": "hash-2"},
        received_at="2026-05-24T10:00:01+00:00",
    )
    moved = ingestor.handle_message(
        {**base, "best_ask": "0.53", "hash": "hash-3"},
        received_at="2026-05-24T10:00:02+00:00",
    )

    assert first.inserted is True
    assert same_touch is None
    assert moved.inserted is True
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0] == 2
    assert len(seen) == 2


def test_market_channel_can_write_feasibility_to_trade_connection():
    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    writer = EventWriter(world_conn)
    trade_conn = sqlite3.connect(":memory:")
    init_schema_trade_only(trade_conn)
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        feasibility_conn=trade_conn,
    )

    ingestor.handle_message(
        {
            "event_type": "book",
            "asset_id": "token-1",
            "market": "0xcondition",
            "outcome_label": "YES",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "hash-1",
            "timestamp": "1766789469958",
        },
        received_at="2026-05-24T10:00:00+00:00",
    )

    assert world_conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    assert world_conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='execution_feasibility_evidence'"
    ).fetchone()[0] == 0
    rows = trade_conn.execute(
        "SELECT direction FROM execution_feasibility_latest ORDER BY direction"
    ).fetchall()
    assert rows == [("buy_yes",), ("sell_yes",)]
    assert trade_conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_evidence"
    ).fetchone()[0] == 0


def test_market_channel_no_default_yes_for_no_token():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"no-token"},
        token_metadata=_metadata("no-token", outcome_label="NO"),
    )

    ingestor.handle_message(
        {
            "event_type": "book",
            "asset_id": "no-token",
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "hash-no",
            "timestamp": "1766789469958",
        },
        received_at="2026-05-24T10:00:00+00:00",
    )

    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    rows = conn.execute(
        "SELECT direction, outcome_label FROM execution_feasibility_latest ORDER BY direction"
    ).fetchall()
    assert rows == [("buy_no", "NO"), ("sell_no", "NO")]


def test_market_channel_rejects_unmapped_token_without_outcome_label():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(writer, active_token_ids={"token-1"})

    result = ingestor.handle_message(
        {
            "event_type": "book",
            "asset_id": "token-1",
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "hash-1",
            "timestamp": "1766789469958",
        },
        received_at="2026-05-24T10:00:00+00:00",
    )

    assert result is None
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0


def test_tick_size_change_invokes_refresh_callback():
    _conn, writer = _conn_writer()
    actions = []
    service = MarketChannelOnlineService(
        MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata()),
        refresh_snapshot=actions.append,
    )

    action = service.ingestor.handle_message(
        {"event_type": "tick_size_change", "asset_id": "token-1", "market": "0xcondition"},
        received_at="2026-05-24T10:00:00+00:00",
    )
    assert isinstance(action, MarketChannelAction)
    service._handle_action(action)

    assert service.refresh_action_count == 1
    assert actions == [action]


def test_market_channel_refresh_action_dedupes_within_window():
    _conn, writer = _conn_writer()
    actions = []
    service = MarketChannelOnlineService(
        MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata()),
        refresh_snapshot=actions.append,
    )
    action = MarketChannelAction(
        refresh_snapshot=True,
        reason="tick_size_change",
        token_id="token-1",
        condition_id="0xcondition",
    )

    service._handle_action(action)
    service._handle_action(action)

    assert service.refresh_action_count == 1
    assert service.refresh_action_dropped_count == 1
    assert actions == [action]


def test_market_channel_refresh_action_budget_limits_work():
    _conn, writer = _conn_writer()
    actions = []
    service = MarketChannelOnlineService(
        MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata()),
        refresh_snapshot=actions.append,
        max_refresh_actions_per_window=1,
    )
    first = MarketChannelAction(
        refresh_snapshot=True,
        reason="tick_size_change",
        token_id="token-1",
        condition_id="0xcondition-1",
    )
    second = MarketChannelAction(
        refresh_snapshot=True,
        reason="market_resolved",
        token_id="token-2",
        condition_id="0xcondition-2",
    )

    service._handle_action(first)
    service._handle_action(second)

    assert service.refresh_action_count == 1
    assert service.refresh_action_dropped_count == 1
    assert actions == [first]


def test_market_channel_refresh_budget_still_invalidates_dropped_actions():
    _conn, writer = _conn_writer()
    invalidated = []
    refreshed = []
    service = MarketChannelOnlineService(
        MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata()),
        invalidate_snapshot=invalidated.append,
        refresh_snapshot=refreshed.append,
        max_refresh_actions_per_window=1,
    )
    first = MarketChannelAction(
        refresh_snapshot=True,
        reason="tick_size_change",
        token_id="token-1",
        condition_id="0xcondition-1",
    )
    second = MarketChannelAction(
        refresh_snapshot=True,
        reason="market_resolved",
        token_id="token-2",
        condition_id="0xcondition-2",
    )

    service._handle_action(first)
    service._handle_action(second)

    assert invalidated == [first, second]
    assert refreshed == [first]
    assert service.refresh_action_dropped_count == 1


def test_market_channel_refresh_queue_keeps_slow_work_off_caller_thread():
    _conn, writer = _conn_writer()
    caller_thread = threading.get_ident()
    started = threading.Event()
    release = threading.Event()
    refresh_threads: list[int] = []

    def refresh(_action: MarketChannelAction) -> None:
        refresh_threads.append(threading.get_ident())
        started.set()
        assert release.wait(timeout=2.0)

    service = MarketChannelOnlineService(
        MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata()),
        refresh_snapshot=refresh,
    )
    action = MarketChannelAction(
        refresh_snapshot=True,
        reason="tick_size_change",
        token_id="token-1",
        condition_id="0xcondition",
    )

    service._enqueue_refresh_action(action)

    assert started.wait(timeout=1.0)
    assert len(refresh_threads) == 1
    assert refresh_threads[0] != caller_thread
    release.set()
    assert service.wait_refresh_idle(timeout=1.0)
    assert service.refresh_action_count == 1


def test_market_channel_refresh_queue_coalesces_identical_pending_work():
    _conn, writer = _conn_writer()
    started = threading.Event()
    release = threading.Event()
    refreshed: list[MarketChannelAction] = []

    def refresh(action: MarketChannelAction) -> None:
        refreshed.append(action)
        started.set()
        assert release.wait(timeout=2.0)

    service = MarketChannelOnlineService(
        MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata()),
        refresh_snapshot=refresh,
    )
    action = MarketChannelAction(
        refresh_snapshot=True,
        reason="tick_size_change",
        token_id="token-1",
        condition_id="0xcondition",
    )

    service._enqueue_refresh_action(action)
    assert started.wait(timeout=1.0)
    service._enqueue_refresh_action(action)
    service._enqueue_refresh_action(action)
    release.set()

    assert service.wait_refresh_idle(timeout=1.0)
    assert refreshed == [action]
    assert service.refresh_action_count == 1
    assert service.refresh_action_dropped_count == 1
    assert service.refresh_action_coalesced_count == 1


def test_market_channel_condition_refresh_does_not_fallback_to_unrelated_markets():
    from src.ingest.price_channel_ingest import _edli_filter_markets_for_condition

    markets = [
        {"condition_id": "condition-top", "outcomes": []},
        {"condition_id": "condition-other", "outcomes": [{"condition_id": "condition-child"}]},
    ]

    assert _edli_filter_markets_for_condition(markets, "condition-top") == [markets[0]]
    assert _edli_filter_markets_for_condition(markets, "condition-child") == [markets[1]]
    assert _edli_filter_markets_for_condition(markets, "missing-condition") == []


def test_tick_size_change_records_append_only_snapshot_invalidation_until_refreshed():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            freshness_deadline TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?)",
        ("snapshot-1", "condition-1", "yes-1", "no-1", "2026-05-24T12:05:00+00:00"),
    )

    count = invalidate_executable_snapshots_for_market_channel_action(
        conn,
        MarketChannelAction(
            refresh_snapshot=True,
            reason="tick_size_change",
            condition_id="condition-1",
            token_id="yes-1",
        ),
        invalidated_at=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )

    assert count == 1
    assert conn.execute("SELECT freshness_deadline FROM executable_market_snapshots").fetchone()[0] == "2026-05-24T12:05:00+00:00"
    row = conn.execute(
        """
        SELECT condition_id, token_id, reason, invalidated_at
          FROM executable_market_snapshot_invalidations
        """
    ).fetchone()
    assert row == (
        "condition-1",
        "yes-1",
        "tick_size_change",
        "2026-05-24T12:00:00+00:00",
    )


def test_new_market_message_emits_discovery_event_without_shadow_module():
    _conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata())

    event = ingestor.event_from_message(
        {
            "event_type": "new_market",
            "condition_id": "0xnew",
            "clob_token_ids": ["token-1", "token-2"],
            "timestamp": "1766789469958",
        },
        received_at="2026-05-24T10:00:00+00:00",
    )

    assert event is not None
    assert event.event_type == "NEW_MARKET_DISCOVERED"
    assert event.source == "polymarket_market_channel"


def test_new_market_message_does_not_default_unmapped_token_to_yes():
    _conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(writer, active_token_ids={"token-1"})

    event = ingestor.event_from_message(
        {
            "event_type": "new_market",
            "condition_id": "0xnew",
            "clob_token_ids": ["token-1", "token-2"],
            "timestamp": "1766789469958",
        },
        received_at="2026-05-24T10:00:00+00:00",
    )

    assert event is None


def test_quote_only_ingestor_writes_trade_evidence_and_rejects_world_events():
    conn, _writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        None,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        feasibility_conn=conn,
    )

    results = ingestor.seed_from_rest(
        lambda _token_id: pytest.fail("pre-captured quote must not fetch"),
        received_at="2026-07-18T20:00:00+00:00",
        pre_cached={
            "token-1": {
                "asset_id": "token-1",
                "market": "0xcondition",
                "bids": [{"price": "0.48", "size": "10"}],
                "asks": [{"price": "0.52", "size": "10"}],
                "hash": "quote-only-hash",
            }
        },
    )
    assert len(results) == 1
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0] == 2

    world_event = ingestor.event_from_message(
        {
            "event_type": "new_market",
            "condition_id": "0xnew",
            "clob_token_ids": ["token-1"],
            "timestamp": "1784404800000",
        },
        received_at="2026-07-18T20:00:00+00:00",
    )
    assert world_event is not None
    with pytest.raises(MarketChannelAuthorityError, match="quote-only"):
        ingestor._commit_market_event(world_event)


def test_feasibility_evidence_from_quote_is_evidence_only():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(writer, active_token_ids={"token-1"}, token_metadata=_metadata())
    event = ingestor.event_from_message(
        {
            "event_type": "book",
            "asset_id": "token-1",
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "hash-1",
        },
        received_at="2026-05-24T10:00:00+00:00",
    )
    assert event is not None
    insert_execution_feasibility_evidence(conn, feasibility_evidence_from_quote(event, direction="buy_yes"))
    assert conn.execute("SELECT accepted_or_rejected FROM execution_feasibility_evidence").fetchone()[0] is None


def test_active_weather_token_ids_from_executable_snapshots():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            event_slug TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            min_tick_size TEXT,
            min_order_size TEXT,
            neg_risk INTEGER,
            active INTEGER,
            closed INTEGER,
            captured_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap-1','0xcondition','chicago-weather','yes-1','no-1','0.01','5',0,1,0,'2026-05-24T10:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap-2','0xpolitics','politics','yes-2','no-2','0.01','5',0,1,0,'2026-05-24T10:00:00+00:00')"
    )
    assert active_weather_token_ids_from_snapshots(conn) == {"yes-1", "no-1"}
    metadata = active_weather_token_metadata_from_snapshots(conn)
    assert metadata["yes-1"].outcome_label == "YES"
    assert metadata["no-1"].outcome_label == "NO"
    assert metadata["no-1"].min_order_size == "5"


def test_active_weather_metadata_reads_latest_projection_not_snapshot_history():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT,
            event_slug TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            min_tick_size TEXT,
            min_order_size TEXT,
            neg_risk INTEGER,
            active INTEGER,
            closed INTEGER,
            captured_at TEXT
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            snapshot_id TEXT,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        """
    )
    conn.executemany(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            (
                "old",
                "condition",
                "weather-old",
                "yes-old",
                "no-old",
                "0.01",
                "5",
                0,
                1,
                0,
                "2026-07-17T00:00:00+00:00",
            ),
            (
                "current",
                "condition",
                "weather-current",
                "yes-current",
                "no-current",
                "0.001",
                "10",
                1,
                1,
                0,
                "2026-07-17T01:00:00+00:00",
            ),
        ),
    )
    conn.executemany(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?)",
        (
            ("condition", "yes-current", "current"),
            ("condition", "no-current", "current"),
        ),
    )

    metadata = active_weather_token_metadata_from_snapshots(conn)

    assert set(metadata) == {"yes-current", "no-current"}
    assert metadata["yes-current"].min_tick_size == "0.001"
    assert metadata["no-current"].min_order_size == "10"


def test_min_order_size_enforced():
    book = quote_book_from_depth_json(
        yes_depth_json='{"asks":[{"price":"0.52","size":"10"}],"bids":[]}',
        no_depth_json='{"asks":[],"bids":[]}',
        min_tick_size="0.01",
        min_order_size="5",
        fee_rate=0.05,
        neg_risk=False,
    )
    with pytest.raises(ExecutableCostError, match="min order"):
        executable_cost(book, direction="buy_yes", shares=__import__("decimal").Decimal("4"))


# ---------------------------------------------------------------------------
# Blocker #52 — universe coverage relationship test (EDLI live canary)
# Created: 2026-05-31
# Last reused/audited: 2026-05-31
# Authority basis: EDLI live canary Blocker #52 — pre-submit authority witness
#   (_edli_latest_pre_submit_book_row) needs a fresh execution_feasibility_evidence
#   row per candidate token. Universe query must cover EVERY active candidate token,
#   not the N most-recent snapshot ROWS.
# ---------------------------------------------------------------------------


def _snapshot_universe_table_with_candidate_and_fillers(n_fillers: int):
    """A candidate market with an OLDER snapshot + many fillers with FRESHER rows.

    Under the legacy ``ORDER BY captured_at DESC LIMIT n`` (on ROWS) the candidate
    is pushed out by the fresher filler rows. The fix (latest-per-market) keeps the
    candidate because it ranks per condition, not globally by row recency.
    """

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            event_slug TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            min_tick_size TEXT,
            min_order_size TEXT,
            neg_risk INTEGER,
            active INTEGER,
            closed INTEGER,
            captured_at TEXT
        )
        """
    )
    # Candidate market — snapshot captured EARLIER than all fillers.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES "
        "('snap-cand','0xcand','chicago-weather-high','yes-cand','no-cand','0.01','5',0,1,0,'2026-05-31T00:00:00+00:00')"
    )
    # Fillers — each FRESHER than the candidate, so a row-LIMIT prefers them.
    for i in range(n_fillers):
        ts = f"2026-05-31T10:{i % 60:02d}:{(i // 60) % 60:02d}+00:00"
        conn.execute(
            "INSERT INTO executable_market_snapshots VALUES "
            f"('snap-f{i}','0xf{i}','tokyo-temperature-{i}','yes-f{i}','no-f{i}','0.01','5',0,1,0,'{ts}')"
        )
    return conn


def test_candidate_token_excluded_under_row_limit_then_covered_by_latest_per_market():
    """RED→GREEN: candidate token excluded by row-LIMIT, included by the fix.

    Relationship: snapshot universe -> ingestor capture set. The candidate token
    MUST be in the capture set even though its snapshot is older than the cap-many
    filler rows. This is the precondition for it ever getting an evidence row.
    """

    n_fillers = 600  # > any reasonable per-market cap; rows, not markets
    conn = _snapshot_universe_table_with_candidate_and_fillers(n_fillers)

    # --- RED baseline: reproduce the legacy row-LIMIT behavior directly. ---
    legacy_rows = conn.execute(
        """
        SELECT yes_token_id, no_token_id
        FROM executable_market_snapshots
        WHERE COALESCE(active,0)=1 AND COALESCE(closed,0)=0
          AND (LOWER(COALESCE(event_slug,'')) LIKE '%weather%'
               OR LOWER(COALESCE(event_slug,'')) LIKE '%temperature%')
        ORDER BY captured_at DESC
        LIMIT 500
        """
    ).fetchall()
    legacy_tokens = {str(t) for row in legacy_rows for t in row if t}
    assert "yes-cand" not in legacy_tokens, "RED expectation: row-LIMIT excludes the candidate"
    assert "no-cand" not in legacy_tokens

    # --- GREEN: the fixed universe covers the candidate at any sane cap. ---
    md = active_weather_token_metadata_from_snapshots(conn, limit=500)
    assert "yes-cand" in md, "candidate YES token must be in capture set after fix"
    assert "no-cand" in md, "candidate NO token must be in capture set after fix"
    assert md["yes-cand"].outcome_label == "YES"
    assert md["no-cand"].outcome_label == "NO"


def test_priority_pinning_keeps_candidate_even_below_cap():
    """Even with a tight cap that excludes most markets, pinned candidates survive."""

    conn = _snapshot_universe_table_with_candidate_and_fillers(50)
    # Tight cap = 5 markets. Candidate's older snapshot would lose the newest-first
    # bounded slice — but priority pinning forces it in.
    md = active_weather_token_ids_from_snapshots(
        conn, limit=5, priority_token_ids={"yes-cand", "no-cand"}
    )
    assert "yes-cand" in md
    assert "no-cand" in md


def test_universe_to_witness_relationship_candidate_gets_fresh_evidence_row():
    """End-to-end relationship: universe -> ingestor REST seed -> evidence row -> witness.

    Boundary under test: the candidate token's snapshot flows through the ingestor
    capture path and produces an execution_feasibility_evidence row whose
    quote_seen_at is fresh and <= decision_time, so the pre-submit witness query
    (_edli_latest_pre_submit_book_row) returns a usable bid/ask/book_hash row.

    Pre-fix the candidate is absent from the universe -> no evidence row -> witness
    returns None -> EDLI_LIVE_CERTIFICATE_BUILD_FAILED:QUOTE_FEASIBILITY_BID_ASK_REQUIRED.
    """

    from src.events.reactor import _edli_latest_pre_submit_book_row

    snap_conn = _snapshot_universe_table_with_candidate_and_fillers(600)
    # Universe with candidate pinned (mirrors the live wiring in main.py).
    token_metadata = active_weather_token_metadata_from_snapshots(
        snap_conn, limit=500, priority_token_ids={"yes-cand", "no-cand"}
    )
    assert "yes-cand" in token_metadata

    # World DB owns opportunity events; trade DB owns execution_feasibility_evidence
    # and is the same book-evidence connection the pre-submit witness reads in live.
    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trade_conn = sqlite3.connect(":memory:")
    init_schema_trade_only(trade_conn)
    ingestor = MarketChannelIngestor(
        EventWriter(world_conn),
        active_token_ids=set(token_metadata),
        token_metadata=token_metadata,
        feasibility_conn=trade_conn,
    )

    seed_time = "2026-05-31T12:00:00+00:00"

    def _fetch_orderbook(token_id: str) -> dict:
        # Minimal valid book with bid/ask/hash for the candidate; empty otherwise.
        # ISO timestamp passes through _timestamp_ms_to_iso unchanged (avoids ms math).
        if token_id in {"yes-cand", "no-cand"}:
            return {
                "event_type": "book",
                "asset_id": token_id,
                "market": "0xcand",
                "timestamp": seed_time,
                "hash": f"bookhash-{token_id}",
                "bids": [{"price": "0.48", "size": "100"}],
                "asks": [{"price": "0.52", "size": "100"}],
            }
        return {
            "event_type": "book",
            "asset_id": token_id,
            "timestamp": seed_time,
            "hash": "",
            "bids": [],
            "asks": [],
        }

    ingestor.seed_from_rest(_fetch_orderbook, received_at=seed_time)
    world_conn.commit()
    trade_conn.commit()

    # Decision time strictly AFTER the seed quote — causal witness must accept it.
    decision_time = datetime(2026, 5, 31, 12, 0, 30, tzinfo=timezone.utc)
    row = _edli_latest_pre_submit_book_row(
        trade_conn, token_id="yes-cand", decision_time=decision_time
    )
    assert row is not None, "witness must find a fresh evidence row for the candidate token"
    quote_seen_at, book_hash_before, best_bid_before, best_ask_before = row
    assert book_hash_before == "bookhash-yes-cand"
    assert best_bid_before is not None and best_ask_before is not None
    # Causal guard preserved: the captured quote is <= decision_time.
    assert datetime.fromisoformat(quote_seen_at) <= decision_time

    # Safety NOT relaxed: a future-dated decision boundary still excludes the quote.
    past_decision = datetime(2026, 5, 31, 11, 59, 0, tzinfo=timezone.utc)
    assert (
        _edli_latest_pre_submit_book_row(
            trade_conn, token_id="yes-cand", decision_time=past_decision
        )
        is None
    ), "causal guard must still reject quotes seen after the decision time"


# ---------------------------------------------------------------------------
# 2026-06-04 — Channel universe must EXCLUDE settled markets (market_end_at <= now)
# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: live candidate-flow stall root (this session). EMS active/closed
#   lifecycle flags are never maintained (all live rows show active=1/closed=0), so
#   the channel universe selector admitted 3,468 SETTLED weather conditions (June-4
#   back to May) alongside only 540 live ones — ~8,000 tokens, ~7,000 dead-404. The
#   persistent market-channel thread drowned its REST reseed / WS subscription in 404
#   dead tokens (~1 tok/sec, ~2h/pass) → BEST_BID_ASK_CHANGED emission died →
#   opportunity_events/candidates/receipts went to zero.
#
#   RELATIONSHIP INVARIANT (the boundary where EMS lifecycle-flag staleness corrupts
#   the live subscription universe): a market whose market_end_at is in the PAST
#   cannot be a tradeable candidate and MUST NOT enter the channel universe,
#   regardless of the stale active/closed flags. Excluding past-ending markets cannot
#   drop a live candidate (a settled market is untradeable), so the Blocker #52
#   coverage invariant — every CANDIDATE token is covered — is preserved.
# ---------------------------------------------------------------------------


def _ems_table_with_end(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            event_slug TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            min_tick_size TEXT,
            min_order_size TEXT,
            neg_risk INTEGER,
            active INTEGER,
            closed INTEGER,
            captured_at TEXT,
            market_end_at TEXT
        )
        """
    )


def test_universe_excludes_settled_markets_by_market_end_at():
    """Settled (past-ending) weather markets must not leak into the channel universe.

    Both rows carry the STALE live-state flags (active=1, closed=0) that EMS never
    maintains; the only honest signal of tradeability is market_end_at vs now.
    """

    conn = sqlite3.connect(":memory:")
    _ems_table_with_end(conn)
    now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    # LIVE: future-ending weather market.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES "
        "('snap-live','0xlive','chicago-weather','yes-live','no-live','0.01','5',0,1,0,"
        "'2026-06-04T11:00:00+00:00','2026-06-05T12:00:00+00:00')"
    )
    # SETTLED: past-ending weather market with STALE active=1/closed=0 flags.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES "
        "('snap-dead','0xdead','dallas-weather','yes-dead','no-dead','0.01','5',0,1,0,"
        "'2026-06-04T10:00:00+00:00','2026-06-04T11:30:00+00:00')"
    )

    md = active_weather_token_metadata_from_snapshots(conn, now=now)

    assert "yes-live" in md and "no-live" in md, "live (future-ending) market must be covered"
    assert md["yes-live"].market_end_at == "2026-06-05T12:00:00+00:00"
    assert "yes-dead" not in md, "settled market (market_end_at<=now) leaked into channel universe"
    assert "no-dead" not in md


def test_universe_filter_agrees_with_canonical_market_open_predicate():
    """STEP 5 relationship test: the bulk SQL `market_end_at > now` universe filter
    gives the SAME keep/drop verdict as the ONE canonical POST_TRADING-boundary
    authority ``market_phase.market_open_at_decision`` for every (market_end_at,
    now) pair — so the universe filter and the phase axis cannot diverge on the
    end-boundary. (NULL end-time is the coverage-safe exception, covered
    separately; this pins the explicit-end-time agreement.)"""
    from src.strategy.market_phase import market_open_at_decision

    now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    cases = [
        ("yes-future", "2026-06-05T12:00:00+00:00"),  # open → kept
        ("yes-boundary", "2026-06-04T12:00:00+00:00"),  # exactly now → POST_TRADING → dropped
        ("yes-past", "2026-06-04T11:30:00+00:00"),  # closed → dropped
    ]
    conn = sqlite3.connect(":memory:")
    _ems_table_with_end(conn)
    for i, (tok, end_at) in enumerate(cases):
        conn.execute(
            "INSERT INTO executable_market_snapshots VALUES "
            f"('snap-{i}','0xc{i}','x-weather','{tok}','no-{i}','0.01','5',0,1,0,"
            f"'2026-06-04T11:00:00+00:00','{end_at}')"
        )
    md = active_weather_token_metadata_from_snapshots(conn, now=now)

    for tok, end_at in cases:
        from datetime import datetime as _dt
        end_utc = _dt.fromisoformat(end_at)
        predicate_open = market_open_at_decision(polymarket_end_utc=end_utc, as_of_utc=now)
        sql_kept = tok in md
        assert sql_kept == predicate_open, (
            f"SQL universe filter and market_open_at_decision disagree for "
            f"end_at={end_at}: sql_kept={sql_kept} predicate_open={predicate_open}"
        )


def test_universe_includes_market_with_null_end_at():
    """Defensive: a NULL market_end_at must NOT silently drop the token (coverage-safe).

    Preserves the Blocker #52 invariant when end-time provenance is missing: when we
    cannot prove a market is settled, we keep it (it may be a live candidate). Only
    a definitively PAST market_end_at excludes a token.
    """

    conn = sqlite3.connect(":memory:")
    _ems_table_with_end(conn)
    now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES "
        "('snap-null','0xnull','miami-weather','yes-null','no-null','0.01','5',0,1,0,"
        "'2026-06-04T11:00:00+00:00',NULL)"
    )

    md = active_weather_token_metadata_from_snapshots(conn, now=now)
    assert "yes-null" in md, "NULL market_end_at must be kept (cannot prove settled)"


def test_long_lived_seed_prunes_tokens_that_expired_after_thread_start():
    """A running market-channel thread must not keep yesterday's token universe forever."""

    conn, writer = _conn_writer()
    metadata = {
        "token-live": MarketTokenMetadata(
            condition_id="0xcondition",
            token_id="token-live",
            outcome_label="YES",
            min_tick_size="0.01",
            min_order_size="5",
            neg_risk=False,
            executable_snapshot_id="snap-live",
            market_end_at="2999-01-01T00:00:00+00:00",
        ),
        "token-expired": MarketTokenMetadata(
            condition_id="0xcondition",
            token_id="token-expired",
            outcome_label="YES",
            min_tick_size="0.01",
            min_order_size="5",
            neg_risk=False,
            executable_snapshot_id="snap-expired",
            market_end_at="2000-01-01T00:00:00+00:00",
        ),
    }
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(metadata),
        token_metadata=metadata,
    )
    service = MarketChannelOnlineService(ingestor, fetch_orderbook=_fake_book)

    fetch_calls: list[str] = []

    def recording_fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        if token_id == "token-expired":
            raise AssertionError("expired token must not be REST fetched")
        return _fake_book(token_id)

    service.fetch_orderbook = recording_fetch
    written = service.seed_rest_books_in_chunks(
        token_ids=["token-expired", "token-live"],
        received_at="2026-06-28T06:45:00+00:00",
        write_gate=nullcontext(),
        commit=conn.commit,
    )

    assert written == 1
    assert fetch_calls == ["token-live"]
    assert ingestor.active_token_ids_open_at() == {"token-live"}


def test_universe_filter_absent_market_end_at_column_is_noop():
    """Back-compat: EMS schema without a market_end_at column behaves as before."""

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT, condition_id TEXT, event_slug TEXT,
            yes_token_id TEXT, no_token_id TEXT,
            min_tick_size TEXT, min_order_size TEXT, neg_risk INTEGER,
            active INTEGER, closed INTEGER, captured_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES "
        "('snap-1','0xc','chicago-weather','yes-1','no-1','0.01','5',0,1,0,'2026-06-04T11:00:00+00:00')"
    )
    md = active_weather_token_metadata_from_snapshots(
        conn, now=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    )
    assert "yes-1" in md and "no-1" in md


# ---------------------------------------------------------------------------
# 5th-instance fix (2026-06-04): pre-capture pattern + world-mutex guard
# ---------------------------------------------------------------------------


def _fake_book(token_id: str) -> dict:
    return {
        "asset_id": token_id,
        "market": "0xcondition",
        "bids": [{"price": "0.48", "size": "10"}],
        "asks": [{"price": "0.52", "size": "10"}],
        "hash": "hash-x",
    }


def test_on_connect_with_pre_captured_books_seeds_cache_without_fetch_call():
    """RELATIONSHIP TEST (5th-instance fix): when pre_captured_books is passed to
    on_connect, seed_from_rest uses the cached data and the fetch_orderbook callable
    is NOT invoked (guard never tripped by I/O under the mutex).

    Relationship invariant: Module A (MarketChannelOnlineService.on_connect called
    inside with _world_mutex) → Module B (seed_from_rest) must NOT call the REST
    fetch callable when pre-cached data is available.
    """
    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )

    fetch_calls: list[str] = []

    def recording_fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        return _fake_book(token_id)

    service = MarketChannelOnlineService(ingestor, fetch_orderbook=recording_fetch)

    pre_captured = {"token-1": _fake_book("token-1")}
    results = service.on_connect(
        received_at="2026-06-04T10:00:00+00:00",
        pre_captured_books=pre_captured,
    )

    # Seed must still populate the book cache and write executable quote evidence.
    assert len(results) == 1
    assert cache.get("token-1") is not None
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0] == 2
    # The REST callable must NOT have been invoked — I/O happened before the lock
    assert fetch_calls == [], (
        "fetch_orderbook was called inside on_connect with pre_captured_books — "
        "this means I/O under the world mutex was NOT eliminated"
    )


def test_on_connect_pre_capture_failure_skips_seed_gracefully():
    """Fail-closed per-token: when pre-capture fails for a token (pre_captured_books
    contains no entry), seed_from_rest falls back to fetch_orderbook for that token.
    If that also fails, the token is skipped gracefully — no crash, no exception
    propagation, no WorldMutexIOViolation."""
    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )

    def always_failing_fetch(token_id: str) -> dict:
        raise ConnectionError("simulated pre-fetch failure")

    service = MarketChannelOnlineService(ingestor, fetch_orderbook=always_failing_fetch)

    # Pass an empty pre_captured_books (token absent → fallback fetch → also fails)
    results = service.on_connect(
        received_at="2026-06-04T10:00:00+00:00",
        pre_captured_books={},  # token-1 absent → fallback to fetch_orderbook
    )

    # Graceful: no exception, no crash, empty results (seed skipped for all tokens)
    assert results == []
    assert cache.get("token-1") is None
    # No rows written — seed was skipped
    assert (
        conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    )


def test_on_connect_pre_captured_books_none_uses_legacy_direct_fetch():
    """Backwards-compatibility: when pre_captured_books is None (legacy callers),
    seed_from_rest calls fetch_orderbook directly — the original behaviour is
    preserved for callers that haven't adopted the pre-capture pattern."""
    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )

    fetch_calls: list[str] = []

    def recording_fetch(token_id: str) -> dict:
        fetch_calls.append(token_id)
        return _fake_book(token_id)

    service = MarketChannelOnlineService(ingestor, fetch_orderbook=recording_fetch)

    results = service.on_connect(received_at="2026-06-04T10:00:00+00:00")  # pre_captured_books=None

    assert len(results) == 1
    assert cache.get("token-1") is not None
    assert fetch_calls == ["token-1"], "legacy direct fetch must still fire when pre_captured_books=None"


def test_seed_from_rest_empty_pre_cached_under_world_mutex_raises_not_fetches():
    """RED→GREEN (production fix 2026-06-04): the production bug was
    seed_from_rest with empty pre_cached under the world mutex falling through
    to the fallback-fetch branch → 283× WorldMutexIOViolation per 2min +
    481 MB WAL re-bloat.

    AFTER the fix: the fallback-fetch branch has its own
    assert_no_world_mutex_held_for_io guard, so it raises WorldMutexIOViolation
    BEFORE calling fetch_orderbook — zero I/O under the mutex.

    This test verifies:
    1. No fetch_orderbook call is made (guard fires before reaching I/O).
    2. WorldMutexIOViolation is raised (not silently skipped).
    3. The token is caught by the except block → WARNING logged, results=[].
    """
    from src.state.db import world_write_mutex

    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )

    fetch_reached: list[str] = []

    def should_not_be_called(token_id: str) -> dict:
        fetch_reached.append(token_id)
        return _fake_book(token_id)

    mutex = world_write_mutex()
    mutex.acquire()
    try:
        # Empty pre_cached → fallback-fetch branch → guard fires → exception caught
        # by seed_from_rest's per-token try/except → token skipped → results = []
        results = ingestor.seed_from_rest(
            should_not_be_called,
            received_at="2026-06-04T10:00:00+00:00",
            pre_cached={},  # empty — token-1 absent → fallback path
        )
    finally:
        mutex.release()

    # Guard fires BEFORE the fetch callable → fetch must never be reached
    assert fetch_reached == [], (
        "fetch_orderbook was called under the world mutex — the under-mutex "
        "fetch fallback was NOT eliminated"
    )
    # Token skipped → empty results (fail-closed per token)
    assert results == []
    # Nothing written — seed was skipped
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0


def test_on_reconnect_empty_pre_captured_under_world_mutex_raises_not_fetches():
    """Same structural guarantee for on_reconnect: empty pre_captured_books under
    the world mutex must NOT reach fetch_orderbook."""
    from src.state.db import world_write_mutex

    conn, writer = _conn_writer()
    cache = QuoteCache()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
        quote_cache=cache,
    )

    fetch_reached: list[str] = []

    def should_not_be_called(token_id: str) -> dict:
        fetch_reached.append(token_id)
        return _fake_book(token_id)

    service = MarketChannelOnlineService(ingestor, fetch_orderbook=should_not_be_called)
    service.connected = False
    service.gap_start = "2026-06-04T09:00:00+00:00"

    mutex = world_write_mutex()
    mutex.acquire()
    try:
        results = service.on_reconnect(
            received_at="2026-06-04T10:00:00+00:00",
            pre_captured_books={},  # empty — token-1 absent → fallback path → guard
        )
    finally:
        mutex.release()

    assert fetch_reached == [], (
        "fetch_orderbook was called under the world mutex in on_reconnect"
    )
    assert results == []


# ---------------------------------------------------------------------------
# W0.2 blind-window metric: on_connect/on_disconnect/on_reconnect must persist
# a durable connectivity transition (the in-memory connected/gap_start fields
# alone do not survive a daemon restart — see
# src/state/schema/market_channel_connectivity_schema.py for the query that
# derives blind-window intervals from these rows).
# ---------------------------------------------------------------------------


def test_on_disconnect_persists_durable_transition_row():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
    )
    service = MarketChannelOnlineService(ingestor)

    service.on_disconnect(gap_start="2026-07-02T10:00:00+00:00")

    rows = conn.execute(
        "SELECT channel, transition, occurred_at FROM market_channel_connectivity_events"
    ).fetchall()
    assert rows == [("market_channel", "disconnected", "2026-07-02T10:00:00+00:00")]


def test_on_connect_persists_durable_transition_row():
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
    )
    service = MarketChannelOnlineService(ingestor)

    service.on_connect(received_at="2026-07-02T10:00:00+00:00")

    rows = conn.execute(
        "SELECT channel, transition, occurred_at FROM market_channel_connectivity_events"
    ).fetchall()
    assert rows == [("market_channel", "connected", "2026-07-02T10:00:00+00:00")]


def test_simulated_disconnect_reconnect_produces_queryable_blind_window():
    """(b) from the W0.2 TDD acceptance: a simulated WS disconnect/reconnect
    produces a blind-window interval, read back via BLIND_WINDOW_QUERY."""
    from src.state.schema.market_channel_connectivity_schema import BLIND_WINDOW_QUERY

    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
    )
    service = MarketChannelOnlineService(ingestor)

    service.on_connect(received_at="2026-07-02T10:00:00+00:00")
    service.on_disconnect(gap_start="2026-07-02T10:05:00+00:00")
    service.on_reconnect(received_at="2026-07-02T10:05:45+00:00", token_ids=[])

    rows = conn.execute(BLIND_WINDOW_QUERY).fetchall()

    assert len(rows) == 1
    channel, blind_window_start, blind_window_end, blind_window_seconds = rows[0]
    assert channel == "market_channel"
    assert blind_window_start == "2026-07-02T10:05:00+00:00"
    assert blind_window_end == "2026-07-02T10:05:45+00:00"
    assert blind_window_seconds == pytest.approx(45.0)


def test_disconnect_reconnect_idempotent_on_repeated_calls():
    """Re-emitting the same transition at the same timestamp (e.g. a retried
    call) must not fabricate extra blind-window rows."""
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids={"token-1"},
        token_metadata=_metadata(),
    )
    service = MarketChannelOnlineService(ingestor)

    service.on_disconnect(gap_start="2026-07-02T10:05:00+00:00")
    service.on_disconnect(gap_start="2026-07-02T10:05:00+00:00")

    count = conn.execute(
        "SELECT COUNT(*) FROM market_channel_connectivity_events"
    ).fetchone()[0]
    assert count == 1
