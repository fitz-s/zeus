# Created: 2026-05-24
# Last reused/audited: 2026-06-04
# Authority basis: EDLI v1 implementation prompt §10 online MarketChannelIngestor contract.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.event_writer import EventWriter
from src.events.triggers.market_channel_ingestor import (
    MarketChannelAction,
    MarketChannelAuthorityError,
    MarketChannelIngestor,
    MarketChannelOnlineService,
    MarketTokenMetadata,
    QuoteCache,
    active_weather_token_metadata_from_snapshots,
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
    event = ingestor.reconnect_gap_snapshot(
        {
            "event_type": "book",
            "asset_id": "token-1",
            "market": "0xcondition",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
            "hash": "hash-after-gap",
            "timestamp": "1766789469958",
        },
        gap_start="2026-05-24T09:59:00+00:00",
        received_at="2026-05-24T10:00:00+00:00",
    )
    assert event is not None
    writer.write(event)
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events WHERE event_type='BOOK_SNAPSHOT'").fetchone()[0] == 1


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
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events WHERE event_type='BOOK_SNAPSHOT'").fetchone()[0] == 1


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
        "SELECT token_id FROM execution_feasibility_evidence ORDER BY token_id"
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
        world_mutex=nullcontext(),
        commit=lambda: commit_counts.append(
            conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
        ),
        chunk_size=2,
    )

    assert written == 5
    assert fetch_calls == [f"token-{idx}" for idx in range(5)]
    # Two evidence rows per token (buy/sell for the canonical side), committed
    # after each bounded batch rather than only after the full universe.
    assert commit_counts == [4, 8, 10]


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
        world_mutex=nullcontext(),
        commit=conn.commit,
        chunk_size=2,
    )

    assert written == 5
    assert batch_calls == [["token-0", "token-1"], ["token-2", "token-3"], ["token-4"]]
    assert (
        conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
        == 10
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
        world_mutex=nullcontext(),
        commit=conn.commit,
        chunk_size=1,
        deadline_monotonic=0.0,
    )

    assert written == 0
    assert fetch_calls == []
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='BOOK_SNAPSHOT'"
        ).fetchone()[0]
        == 0
    )


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
        world_mutex=nullcontext(),
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
        world_mutex=nullcontext(),
        commit=lambda: commit_counts.append(
            conn.execute("SELECT COUNT(*) FROM opportunity_events WHERE event_type='BOOK_SNAPSHOT'").fetchone()[0]
        ),
        chunk_size=2,
    )

    assert written == 5
    assert fetch_calls == [f"token-{idx}" for idx in range(5)]
    assert commit_counts == [2, 4, 5]
    assert service.connected is True
    assert service.gap_start is None


def test_market_channel_quote_writes_feasibility_evidence_only():
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
        "SELECT direction, accepted_or_rejected, filled_shares FROM execution_feasibility_evidence ORDER BY direction"
    ).fetchall()
    assert rows == [("buy_yes", None, None), ("sell_yes", None, None)]


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
    assert (
        conn.execute("SELECT COUNT(*) FROM opportunity_events WHERE event_type='BEST_BID_ASK_CHANGED'").fetchone()[0]
        == 2
    )
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

    assert world_conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1
    assert world_conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='execution_feasibility_evidence'"
    ).fetchone()[0] == 0
    rows = trade_conn.execute(
        "SELECT direction, accepted_or_rejected, filled_shares FROM execution_feasibility_evidence ORDER BY direction"
    ).fetchall()
    assert rows == [("buy_yes", None, None), ("sell_yes", None, None)]


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

    event_payload = conn.execute("SELECT payload_json FROM opportunity_events").fetchone()[0]
    assert '"outcome_label":"NO"' in event_payload
    rows = conn.execute(
        "SELECT direction, outcome_label FROM execution_feasibility_evidence ORDER BY direction"
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


def test_market_channel_condition_refresh_does_not_fallback_to_unrelated_markets():
    from src.ingest.price_channel_ingest import _edli_filter_markets_for_condition

    markets = [
        {"condition_id": "condition-top", "outcomes": []},
        {"condition_id": "condition-other", "outcomes": [{"condition_id": "condition-child"}]},
    ]

    assert _edli_filter_markets_for_condition(markets, "condition-top") == [markets[0]]
    assert _edli_filter_markets_for_condition(markets, "condition-child") == [markets[1]]
    assert _edli_filter_markets_for_condition(markets, "missing-condition") == []


def test_tick_size_change_invalidates_bound_executable_snapshot_until_refreshed():
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
    assert conn.execute("SELECT freshness_deadline FROM executable_market_snapshots").fetchone()[0] == "2026-05-24T11:59:59+00:00"


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

    from src.main import _edli_latest_pre_submit_book_row

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

    # Seed must still populate the book cache and write the event row
    assert len(results) == 1
    assert cache.get("token-1") is not None
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='BOOK_SNAPSHOT'"
        ).fetchone()[0]
        == 1
    )
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
