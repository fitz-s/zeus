# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/08_PHASE_7_SETTLEMENT_TYPE_GATE.md
#                  + docs/operations/task_2026-05-21_strategy_vnext_phase7_settlement_type_gate/PHASE_7_PLAN.md §T1-T2
"""Settlement outcome enum + typed classifier.

SettlementOutcome(IntEnum) — 10-member lifecycle for a Zeus weather position.
Values are fixed per authority 08_PHASE_7_SETTLEMENT_TYPE_GATE.md; do NOT
renumber.

Transitions are monotonic-forward. VALID_FORWARD_TRANSITIONS encodes the DAG;
apply_transition raises InvalidSettlementTransition on any backward or invalid
move.

OBSERVATION_REVISED (=6) routes forward-only to SOURCE_REVISION (102) or
DISPUTED (100). It never reverts to PHYSICALLY_CONFIRMED, SOURCE_PUBLISHED_VENUE_UNRESOLVED,
or UNRESOLVED.

classify_settlement_outcome(market_json) → SettlementOutcome maps raw Gamma
JSON to a typed value. Fail-closed: resolved + missing/malformed/non-binary
outcomePrices → SOURCE_PUBLISHED_VENUE_UNRESOLVED. Never assumes WIN on
ambiguous data.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Optional


# ---------------------------------------------------------------------------
# Enum — 10 members; exact values fixed by authority
# ---------------------------------------------------------------------------

class SettlementOutcome(IntEnum):
    """10-state settlement lifecycle per authority §08_PHASE_7."""

    # Pre-event
    UNRESOLVED = 0                          # event hasn't happened yet

    # Post-event, source-pending
    PHYSICALLY_CONFIRMED = 1                # observed temp is final; source page hasn't published
    SOURCE_PUBLISHED_VENUE_UNRESOLVED = 2   # NOAA/WU published; Polymarket/UMA hasn't resolved

    # Resolved
    VENUE_RESOLVED_WIN = 3                  # market resolved; position wins
    VENUE_RESOLVED_LOSE = 4                 # market resolved; position loses

    # Post-resolution
    REDEEMED = 5                            # winning token redeemed for collateral

    # Revised
    OBSERVATION_REVISED = 6                 # official obs revised after PHYSICALLY_CONFIRMED;
                                            # routes forward-only to SOURCE_REVISION or DISPUTED

    # Edge cases
    DISPUTED = 100                          # UMA dispute filed
    UMA_UNKNOWN_50_50 = 101                 # UMA returned 0.5 / unknown
    SOURCE_REVISION = 102                   # official source revised after settlement


# ---------------------------------------------------------------------------
# Transition DAG — monotonic forward only
# ---------------------------------------------------------------------------

# Terminal states (no outgoing edges): REDEEMED, DISPUTED, UMA_UNKNOWN_50_50, SOURCE_REVISION
VALID_FORWARD_TRANSITIONS: dict[SettlementOutcome, frozenset[SettlementOutcome]] = {
    SettlementOutcome.UNRESOLVED: frozenset({
        SettlementOutcome.PHYSICALLY_CONFIRMED,
        SettlementOutcome.DISPUTED,
    }),
    SettlementOutcome.PHYSICALLY_CONFIRMED: frozenset({
        SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED,
        SettlementOutcome.OBSERVATION_REVISED,
        SettlementOutcome.DISPUTED,
    }),
    SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED: frozenset({
        SettlementOutcome.VENUE_RESOLVED_WIN,
        SettlementOutcome.VENUE_RESOLVED_LOSE,
        SettlementOutcome.UMA_UNKNOWN_50_50,
        SettlementOutcome.DISPUTED,
    }),
    SettlementOutcome.VENUE_RESOLVED_WIN: frozenset({
        SettlementOutcome.REDEEMED,
        SettlementOutcome.DISPUTED,
    }),
    SettlementOutcome.VENUE_RESOLVED_LOSE: frozenset({
        SettlementOutcome.REDEEMED,
        SettlementOutcome.DISPUTED,
    }),
    # REDEEMED → terminal
    SettlementOutcome.OBSERVATION_REVISED: frozenset({
        SettlementOutcome.SOURCE_REVISION,
        SettlementOutcome.DISPUTED,
        # NOT: PHYSICALLY_CONFIRMED, SOURCE_PUBLISHED_VENUE_UNRESOLVED, UNRESOLVED
    }),
    # DISPUTED → terminal
    # UMA_UNKNOWN_50_50 → terminal
    # SOURCE_REVISION → terminal
}


class InvalidSettlementTransition(Exception):
    """Raised when apply_transition receives a disallowed (from, to) pair."""


def apply_transition(
    current: SettlementOutcome,
    target: SettlementOutcome,
) -> SettlementOutcome:
    """Attempt a forward transition; raise on invalid move.

    Args:
        current: Current lifecycle state.
        target: Desired next state.

    Returns:
        target on success.

    Raises:
        InvalidSettlementTransition: if the transition is not in VALID_FORWARD_TRANSITIONS.
    """
    allowed = VALID_FORWARD_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidSettlementTransition(
            f"Invalid transition {current.name} → {target.name}. "
            f"Allowed from {current.name}: "
            f"{sorted(s.name for s in allowed) if allowed else '[]'}"
        )
    return target


# ---------------------------------------------------------------------------
# Classifier — social JSON → typed enum (fail-closed)
# ---------------------------------------------------------------------------

def _resolution_price_is_one(value: object) -> bool:
    try:
        return float(value) == 1.0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _resolution_price_is_zero(value: object) -> bool:
    try:
        return float(value) == 0.0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _parse_outcome_prices(raw: object) -> Optional[list]:
    """Return a list from raw outcomePrices, or None if malformed."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        import json as _json
        try:
            parsed = _json.loads(raw)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def classify_settlement_outcome(market_json: dict) -> SettlementOutcome:
    """Map a raw Gamma market dict to a typed SettlementOutcome.

    Fail-closed: returns SOURCE_PUBLISHED_VENUE_UNRESOLVED when
    umaResolutionStatus is 'resolved' but direction cannot be inferred
    (missing, malformed, or non-binary outcomePrices). Never assumes
    WIN on missing data.

    Decision table:
      umaResolutionStatus != 'resolved'             → UNRESOLVED
      resolved + prices=[1,0]                       → VENUE_RESOLVED_WIN
      resolved + prices=[0,1]                       → VENUE_RESOLVED_LOSE
      resolved + prices missing/malformed/non-binary → SOURCE_PUBLISHED_VENUE_UNRESOLVED
      resolved + prices=[0.5,0.5]                   → SOURCE_PUBLISHED_VENUE_UNRESOLVED (UMA unknown)
    """
    if market_json.get("umaResolutionStatus") != "resolved":
        return SettlementOutcome.UNRESOLVED

    # Resolved case — must infer direction from outcomePrices
    raw_prices = market_json.get("outcomePrices")
    prices = _parse_outcome_prices(raw_prices)

    if prices is None or len(prices) != 2:
        # Missing, malformed, or non-binary length — fail-closed.
        # Require exactly 2 elements; 3+ element lists (e.g. [1,0,0]) are rejected
        # rather than silently picking the first two.
        return SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED

    p0, p1 = prices[0], prices[1]

    # Strictly binary: exactly [1,0] or [0,1]
    if _resolution_price_is_one(p0) and _resolution_price_is_zero(p1):
        return SettlementOutcome.VENUE_RESOLVED_WIN
    if _resolution_price_is_zero(p0) and _resolution_price_is_one(p1):
        return SettlementOutcome.VENUE_RESOLVED_LOSE

    # Non-binary (0.5/0.5, partial, malformed numeric) — fail-closed
    return SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED
