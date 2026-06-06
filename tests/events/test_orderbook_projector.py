from __future__ import annotations

# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Authority basis: Operator request — price redecision must carry orderbook continuity and execution-critical venue facts.

from src.events.orderbook_projector import (
    BookContinuityStatus,
    continuity_status_for_delta,
    project_rest_snapshot,
)


def test_project_rest_snapshot_sorts_book_and_marks_snapshot_source():
    book = project_rest_snapshot(
        token_id="token-1",
        bids=[(0.41, 10), (0.43, 2)],
        asks=[(0.45, 3), (0.44, 5)],
        condition_id="condition-1",
        market_id="market-1",
        sequence=7,
        captured_at="2026-06-06T00:00:00+00:00",
        venue_book_hash="venue-hash-1",
        tick_size=0.01,
        min_order_size=5.0,
        neg_risk=False,
        venue_mode="orderbook",
    )

    assert book.best_bid == 0.43
    assert book.best_ask == 0.44
    assert book.status is BookContinuityStatus.REST_SNAPSHOT
    receipt = book.to_receipt_dict()
    assert receipt["source"] == "rest_snapshot"
    assert receipt["venue_book_hash"] == "venue-hash-1"
    assert receipt["projection_hash"] != "venue-hash-1"
    assert receipt["real_submit_blocked"] is False


def test_project_rest_snapshot_fails_closed_when_execution_facts_unknown():
    book = project_rest_snapshot(
        token_id="token-1",
        bids=[(0.41, 10)],
        asks=[(0.44, 5)],
    )

    assert book.status is BookContinuityStatus.EXECUTION_FACTS_MISSING_FAIL_CLOSED
    assert book.real_submit_blocked is True
    assert "venue_mode" in (book.invalidation_reason or "")


def test_continuity_status_requires_rest_repair_on_gap_or_hash_mismatch():
    assert continuity_status_for_delta(previous_sequence=7, next_sequence=8) is BookContinuityStatus.PROJECTED_CONTINUOUS
    assert continuity_status_for_delta(previous_sequence=7, next_sequence=9) is BookContinuityStatus.GAP_REQUIRES_REST_REPAIR
    assert (
        continuity_status_for_delta(
            previous_sequence=7,
            next_sequence=8,
            expected_venue_book_hash="a",
            actual_venue_book_hash="b",
        )
        is BookContinuityStatus.HASH_MISMATCH_REQUIRES_REST_REPAIR
    )


def test_repair_required_statuses_block_real_submit_in_receipt_dict():
    base = project_rest_snapshot(
        token_id="token-1",
        bids=[(0.41, 10)],
        asks=[(0.44, 5)],
        tick_size=0.01,
        min_order_size=5.0,
        neg_risk=False,
        venue_mode="orderbook",
    )

    gap = type(base)(**{**base.__dict__, "status": BookContinuityStatus.GAP_REQUIRES_REST_REPAIR})
    mismatch = type(base)(**{**base.__dict__, "status": BookContinuityStatus.HASH_MISMATCH_REQUIRES_REST_REPAIR})

    assert gap.to_receipt_dict()["real_submit_blocked"] is True
    assert mismatch.to_receipt_dict()["real_submit_blocked"] is True
