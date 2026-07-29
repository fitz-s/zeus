# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   center-evidence campaign data prerequisite (market-implied center vs our
#   posterior mu). Persists the FamilyBook FamilyDecisionEngine.decide()
#   already builds every cycle (src/execution/family_book.py) and otherwise
#   discards.
"""Capture a decided family's order-book ladder into family_book_snapshots.

This is telemetry-grade EVIDENCE, never decision authority --
``append_family_book_snapshot`` catches every exception and returns ``None``
rather than letting a persistence failure delay or fail the decision cycle
it is called from.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Optional

from src.state.schema.family_book_snapshots_schema import append_snapshot

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

    from src.decision.family_decision_engine import FamilyDecision
    from src.events.candidate_binding import EventBoundCandidateFamily
    from src.execution.family_book import FamilyBook

logger = logging.getLogger(__name__)

_MAX_LADDER_LEVELS = 5


def market_center_native(family_book: "FamilyBook") -> Optional[float]:
    """Price-weighted midpoint center implied by the captured book, in the
    family's native settlement unit (C or F -- see PLAN.md "Unit caveat").

    ``None`` when the book is incomplete (a partial ladder cannot honestly
    imply a family-wide center) or when no bin has both a two-sided best YES
    quote and parseable bounds (all weight would be zero).
    """
    if not family_book.complete_book:
        return None

    weighted_sum = 0.0
    total_weight = 0.0
    for outcome_bin in family_book.omega.bins:
        market = family_book.markets.get(outcome_bin.bin_id)
        if market is None:
            continue
        ask_levels = market.yes_asks.levels
        bid_levels = market.yes_bids.levels
        if not ask_levels or not bid_levels:
            continue
        yes_mid = (float(ask_levels[0].price) + float(bid_levels[0].price)) / 2.0

        lower, upper = outcome_bin.lower_native, outcome_bin.upper_native
        if lower is not None and upper is not None:
            rep_native = (lower + upper) / 2.0
        elif lower is not None:
            rep_native = lower  # open-high edge ("X or higher") -> boundary
        elif upper is not None:
            rep_native = upper  # open-low edge ("X or below") -> boundary
        else:
            continue  # unparseable bounds

        weighted_sum += yes_mid * rep_native
        total_weight += yes_mid

    if total_weight <= 0.0:
        return None
    return weighted_sum / total_weight


def _levels_json(levels) -> list[list[float]]:
    return [[float(level.price), float(level.size)] for level in levels[:_MAX_LADDER_LEVELS]]


def _ladder_json(family_book: "FamilyBook") -> str:
    """Compact per-bin ladder JSON, top-``_MAX_LADDER_LEVELS`` levels/side."""
    per_bin = {}
    for bin_id, market in family_book.markets.items():
        per_bin[bin_id] = {
            "condition_id": market.condition_id,
            "yes_token_id": market.yes_token_id,
            "no_token_id": market.no_token_id,
            "neg_risk": market.neg_risk,
            "yes_ask": _levels_json(market.yes_asks.levels),
            "yes_bid": _levels_json(market.yes_bids.levels),
            "no_ask": _levels_json(market.no_asks.levels),
            "no_bid": _levels_json(market.no_bids.levels),
        }
    return json.dumps(per_bin, sort_keys=True, separators=(",", ":"))


def _snapshot_id(family_id: str, book_hash: str, decision_time_iso: str) -> str:
    key = f"{family_id}|{book_hash}|{decision_time_iso}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def append_family_book_snapshot(
    conn: "sqlite3.Connection",
    *,
    decision: "Optional[FamilyDecision]",
    family: "EventBoundCandidateFamily",
    decision_time: "datetime",
    causal_snapshot_id: Optional[str],
) -> Optional[str]:
    """Fail-soft capture of ``decision.family_book`` into family_book_snapshots.

    Returns the new row's ``snapshot_id`` iff a row was actually inserted;
    ``None`` on a missing/ineligible decision, a dedup-ignored write (the
    book was already seen), or ANY exception -- logged as a warning, never
    re-raised. Telemetry-grade: must never block or delay the decision cycle.
    """
    try:
        if decision is None or decision.family_book is None:
            return None
        family_book = decision.family_book
        decision_time_iso = decision_time.isoformat()
        snapshot_id = _snapshot_id(family.family_id, family_book.book_hash, decision_time_iso)

        inserted = append_snapshot(
            conn,
            snapshot_id=snapshot_id,
            family_id=family.family_id,
            city=family.city,
            target_date=family.target_date,
            temperature_metric=family.metric,
            decision_time=decision_time_iso,
            captured_at_utc=family_book.captured_at_utc.isoformat(),
            book_hash=family_book.book_hash,
            complete_book=family_book.complete_book,
            ladder_json=_ladder_json(family_book),
            market_center_c=market_center_native(family_book),
            our_mu_c=decision.predictive.mu_native,
            our_sigma_c=decision.predictive.sigma_native,
            decision_snapshot_id=causal_snapshot_id,
        )
        return snapshot_id if inserted else None
    except Exception:
        logger.warning(
            "family_book_snapshot capture failed for family=%s; continuing without it",
            getattr(family, "family_id", "<unknown>"),
            exc_info=True,
        )
        return None
