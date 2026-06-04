# Created: 2026-06-01
# Last reused or audited: 2026-06-04
# Authority basis: ingestor seed per-token isolation, live-feed revival (GATE #84 PRE_SUBMIT_BOOK_AUTHORITY_MISSING)
from __future__ import annotations

import logging
import sqlite3

import pytest

from src.events.event_writer import EventWriter
from src.events.triggers.market_channel_ingestor import (
    MarketChannelIngestor,
    MarketChannelOnlineService,
    MarketTokenMetadata,
    QuoteCache,
)
from src.state.db import init_schema


def _conn_writer():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventWriter(conn)


def _metadata_multi(*token_ids: str) -> dict[str, MarketTokenMetadata]:
    return {
        tid: MarketTokenMetadata(
            condition_id="0xcondition",
            token_id=tid,
            outcome_label="YES",
            min_tick_size="0.01",
            min_order_size="5",
            neg_risk=False,
            executable_snapshot_id="snap-1",
        )
        for tid in token_ids
    }


def _valid_book(token_id: str) -> dict:
    return {
        "asset_id": token_id,
        "market": "0xcondition",
        "bids": [{"price": "0.48", "size": "10"}],
        "asks": [{"price": "0.52", "size": "10"}],
        "hash": f"hash-{token_id}",
    }


class _FakeHTTPStatusError(Exception):
    """Stand-in for httpx.HTTPStatusError (404 Not Found)."""


def _make_fetch_orderbook(bad_token: str):
    """Returns a fetch_orderbook callable that raises for bad_token, succeeds for others."""

    def fetch(token_id: str) -> dict:
        if token_id == bad_token:
            raise _FakeHTTPStatusError(f"Client error '404 Not Found' for url '.../book?token_id={token_id}'")
        return _valid_book(token_id)

    return fetch


# ---------------------------------------------------------------------------
# RED: pre-fix — exception from one token propagates, killing the whole seed
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason="Pre-fix documentation: fix applied — exception no longer propagates, token-C is now seeded",
    strict=True,
)
def test_seed_from_rest_one_404_aborts_all_pre_fix():
    """PRE-FIX regression anchor: confirms the fix changed the behaviour.

    Before the fix: seed_from_rest propagated the fetch exception and token-C was never seeded.
    After the fix (strict xfail): this test fails as expected — proving the fix is active.
    """
    TOKENS = ["token-A", "token-BAD", "token-C"]
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(TOKENS),
        token_metadata=_metadata_multi(*TOKENS),
        quote_cache=QuoteCache(),
    )

    fetch = _make_fetch_orderbook(bad_token="token-BAD")

    # Pre-fix: the exception propagates out of seed_from_rest
    with pytest.raises(_FakeHTTPStatusError):
        ingestor.seed_from_rest(fetch, received_at="2026-06-01T00:00:00+00:00")

    # Consequence: the loop aborts mid-way — token-C (last in sorted order) never reached.
    # Sorted order: A → BAD → C; A seeds before the exception, so evidence_count < 2*len(TOKENS).
    # The key assertion is that the exception propagated (tested by pytest.raises above)
    # and that token-C was NOT seeded (only A got through before BAD raised).
    evidence_count = conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
    # A gets through (2 rows: buy+sell), BAD raises, C never reached → strictly < 6 (full set)
    assert evidence_count < 6, (
        f"Expected fewer than 6 feasibility rows (seed aborted mid-loop) but got {evidence_count}. "
        "If this assertion fails, the per-token isolation fix is already in place."
    )
    # Critically: token-C was NOT seeded because the exception aborted before reaching it
    seeded_tokens = {row[0] for row in conn.execute(
        "SELECT DISTINCT token_id FROM execution_feasibility_evidence"
    ).fetchall()}
    assert "token-C" not in seeded_tokens, (
        "token-C should NOT have been seeded (loop aborted at token-BAD) but it was. "
        "If this assertion fails, the per-token isolation fix is already in place."
    )


@pytest.mark.xfail(
    reason="Pre-fix documentation: fix applied — exception no longer propagates in on_reconnect",
    strict=True,
)
def test_on_reconnect_one_404_aborts_all_pre_fix():
    """PRE-FIX regression anchor for on_reconnect — confirms fix is active."""
    TOKENS = ["token-A", "token-BAD", "token-C"]
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(TOKENS),
        token_metadata=_metadata_multi(*TOKENS),
        quote_cache=QuoteCache(),
    )
    fetch = _make_fetch_orderbook(bad_token="token-BAD")
    service = MarketChannelOnlineService(ingestor, fetch_orderbook=fetch)
    service.connected = False
    service.gap_start = "2026-06-01T00:00:00+00:00"

    with pytest.raises(_FakeHTTPStatusError):
        service.on_reconnect(received_at="2026-06-01T00:01:00+00:00")

    evidence_count = conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
    assert evidence_count < 6, (
        f"Expected fewer than 6 feasibility rows (seed aborted mid-loop) but got {evidence_count}. "
        "If this assertion fails, the per-token isolation fix is already in place."
    )
    seeded_tokens = {row[0] for row in conn.execute(
        "SELECT DISTINCT token_id FROM execution_feasibility_evidence"
    ).fetchall()}
    assert "token-C" not in seeded_tokens, (
        "token-C should NOT have been seeded (loop aborted at token-BAD) but it was."
    )


# ---------------------------------------------------------------------------
# GREEN: post-fix — valid tokens seeded, bad token skipped with warning logged
# ---------------------------------------------------------------------------

def test_seed_from_rest_bad_token_skipped_valid_seeded(caplog):
    """POST-FIX: seed_from_rest must complete all valid tokens even when one raises.

    Asserts:
    - No exception propagates
    - results list contains entries for the 2 valid tokens
    - execution_feasibility_evidence has rows for the 2 valid tokens (4 rows: buy+sell each)
    - A WARNING log entry references the bad token_id
    """
    TOKENS = ["token-A", "token-BAD", "token-C"]
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(TOKENS),
        token_metadata=_metadata_multi(*TOKENS),
        quote_cache=QuoteCache(),
    )
    fetch = _make_fetch_orderbook(bad_token="token-BAD")

    with caplog.at_level(logging.WARNING, logger="src.events.triggers.market_channel_ingestor"):
        results = ingestor.seed_from_rest(fetch, received_at="2026-06-01T00:00:00+00:00")

    # 2 valid tokens seeded → 2 write results
    assert len(results) == 2, f"Expected 2 results (valid tokens), got {len(results)}"

    # feasibility evidence: buy+sell per valid token = 4 rows
    evidence_count = conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
    assert evidence_count == 4, f"Expected 4 feasibility rows, got {evidence_count}"

    # warning logged for the bad token
    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("token-BAD" in m for m in warning_msgs), (
        f"Expected a WARNING mentioning 'token-BAD'; got: {warning_msgs}"
    )


def test_on_reconnect_bad_token_skipped_valid_seeded(caplog):
    """POST-FIX: MarketChannelOnlineService.on_reconnect same per-token isolation."""
    TOKENS = ["token-A", "token-BAD", "token-C"]
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(TOKENS),
        token_metadata=_metadata_multi(*TOKENS),
        quote_cache=QuoteCache(),
    )
    fetch = _make_fetch_orderbook(bad_token="token-BAD")
    service = MarketChannelOnlineService(ingestor, fetch_orderbook=fetch)
    service.connected = False
    service.gap_start = "2026-06-01T00:00:00+00:00"

    with caplog.at_level(logging.WARNING, logger="src.events.triggers.market_channel_ingestor"):
        results = service.on_reconnect(received_at="2026-06-01T00:01:00+00:00")

    assert len(results) == 2, f"Expected 2 results (valid tokens), got {len(results)}"

    evidence_count = conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
    assert evidence_count == 4, f"Expected 4 feasibility rows, got {evidence_count}"

    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("token-BAD" in m for m in warning_msgs), (
        f"Expected a WARNING mentioning 'token-BAD'; got: {warning_msgs}"
    )


def test_seed_from_rest_all_valid_tokens_seeded_no_exceptions():
    """Regression: when no token raises, all 3 are seeded normally."""
    TOKENS = ["token-A", "token-B", "token-C"]
    conn, writer = _conn_writer()
    ingestor = MarketChannelIngestor(
        writer,
        active_token_ids=set(TOKENS),
        token_metadata=_metadata_multi(*TOKENS),
        quote_cache=QuoteCache(),
    )
    fetch = lambda tid: _valid_book(tid)

    results = ingestor.seed_from_rest(fetch, received_at="2026-06-01T00:00:00+00:00")

    assert len(results) == 3
    evidence_count = conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
    assert evidence_count == 6  # buy+sell per 3 tokens
