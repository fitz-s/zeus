# Created: 2026-05-24
# Last reused/audited: 2026-05-24
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
from src.state.db import init_schema
from src.strategy.live_inference.executable_cost import ExecutableCostError, quote_book_from_depth_json, executable_cost


def _conn_writer():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
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
    from src.main import _edli_filter_markets_for_condition

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

    # World DB with the REAL execution_feasibility_evidence schema + witness reads.
    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    ingestor = MarketChannelIngestor(
        EventWriter(world_conn),
        active_token_ids=set(token_metadata),
        token_metadata=token_metadata,
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

    # Decision time strictly AFTER the seed quote — causal witness must accept it.
    decision_time = datetime(2026, 5, 31, 12, 0, 30, tzinfo=timezone.utc)
    row = _edli_latest_pre_submit_book_row(
        world_conn, token_id="yes-cand", decision_time=decision_time
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
            world_conn, token_id="yes-cand", decision_time=past_decision
        )
        is None
    ), "causal guard must still reject quotes seen after the decision time"
