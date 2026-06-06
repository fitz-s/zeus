"""Order book projection contracts for event-driven redecision."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.decision_kernel.canonicalization import stable_hash


class BookContinuityStatus(StrEnum):
    REST_SNAPSHOT = "REST_SNAPSHOT"
    PROJECTED_CONTINUOUS = "PROJECTED_CONTINUOUS"
    GAP_REQUIRES_REST_REPAIR = "GAP_REQUIRES_REST_REPAIR"
    HASH_MISMATCH_REQUIRES_REST_REPAIR = "HASH_MISMATCH_REQUIRES_REST_REPAIR"
    EXECUTION_FACTS_MISSING_FAIL_CLOSED = "EXECUTION_FACTS_MISSING_FAIL_CLOSED"


@dataclass(frozen=True)
class ProjectedBook:
    condition_id: str | None
    market_id: str | None
    token_id: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    sequence: int | None
    venue_book_hash: str | None
    projection_hash: str
    captured_at: str | None
    status: BookContinuityStatus
    source: str
    visible_depth: float | None
    tick_size: float | None
    min_order_size: float | None
    neg_risk: bool | None
    venue_mode: str | None
    invalidation_reason: str | None = None

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def book_hash(self) -> str:
        return self.venue_book_hash or self.projection_hash

    @property
    def real_submit_blocked(self) -> bool:
        return self.status in {
            BookContinuityStatus.GAP_REQUIRES_REST_REPAIR,
            BookContinuityStatus.HASH_MISMATCH_REQUIRES_REST_REPAIR,
            BookContinuityStatus.EXECUTION_FACTS_MISSING_FAIL_CLOSED,
        }

    def to_receipt_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "market_id": self.market_id,
            "token_id": self.token_id,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "sequence": self.sequence,
            "venue_book_hash": self.venue_book_hash,
            "projection_hash": self.projection_hash,
            "book_hash": self.book_hash,
            "captured_at": self.captured_at,
            "status": self.status.value,
            "source": self.source,
            "visible_depth": self.visible_depth,
            "tick_size": self.tick_size,
            "min_order_size": self.min_order_size,
            "neg_risk": self.neg_risk,
            "venue_mode": self.venue_mode,
            "invalidation_reason": self.invalidation_reason,
            "real_submit_blocked": self.real_submit_blocked,
        }


def project_rest_snapshot(
    *,
    token_id: str,
    bids: tuple[tuple[float, float], ...] | list[tuple[float, float]],
    asks: tuple[tuple[float, float], ...] | list[tuple[float, float]],
    condition_id: str | None = None,
    market_id: str | None = None,
    sequence: int | None = None,
    captured_at: str | None = None,
    venue_book_hash: str | None = None,
    tick_size: float | None = None,
    min_order_size: float | None = None,
    neg_risk: bool | None = None,
    venue_mode: str | None = None,
) -> ProjectedBook:
    normalized_bids = tuple(sorted(((float(p), float(s)) for p, s in bids), reverse=True))
    normalized_asks = tuple(sorted((float(p), float(s)) for p, s in asks))
    projection_hash = stable_hash(
        {
            "condition_id": condition_id,
            "market_id": market_id,
            "token_id": token_id,
            "bids": normalized_bids,
            "asks": normalized_asks,
            "sequence": sequence,
            "venue_book_hash": venue_book_hash,
        }
    )
    missing_facts = []
    if tick_size is None:
        missing_facts.append("tick_size")
    if min_order_size is None:
        missing_facts.append("min_order_size")
    if neg_risk is None:
        missing_facts.append("neg_risk")
    if venue_mode is None:
        missing_facts.append("venue_mode")
    status = (
        BookContinuityStatus.EXECUTION_FACTS_MISSING_FAIL_CLOSED
        if missing_facts
        else BookContinuityStatus.REST_SNAPSHOT
    )
    return ProjectedBook(
        condition_id=condition_id,
        market_id=market_id,
        token_id=token_id,
        bids=normalized_bids,
        asks=normalized_asks,
        sequence=sequence,
        venue_book_hash=venue_book_hash,
        projection_hash=projection_hash,
        captured_at=captured_at,
        status=status,
        source="rest_snapshot",
        visible_depth=sum(size for _, size in normalized_bids) + sum(size for _, size in normalized_asks),
        tick_size=tick_size,
        min_order_size=min_order_size,
        neg_risk=neg_risk,
        venue_mode=venue_mode,
        invalidation_reason=(
            "EXECUTION_FACTS_MISSING:" + ",".join(missing_facts)
            if missing_facts
            else None
        ),
    )


def continuity_status_for_delta(
    *,
    previous_sequence: int | None,
    next_sequence: int | None,
    expected_venue_book_hash: str | None = None,
    actual_venue_book_hash: str | None = None,
) -> BookContinuityStatus:
    if (
        expected_venue_book_hash is not None
        and actual_venue_book_hash is not None
        and expected_venue_book_hash != actual_venue_book_hash
    ):
        return BookContinuityStatus.HASH_MISMATCH_REQUIRES_REST_REPAIR
    if previous_sequence is None or next_sequence is None:
        return BookContinuityStatus.GAP_REQUIRES_REST_REPAIR
    if next_sequence != previous_sequence + 1:
        return BookContinuityStatus.GAP_REQUIRES_REST_REPAIR
    return BookContinuityStatus.PROJECTED_CONTINUOUS
