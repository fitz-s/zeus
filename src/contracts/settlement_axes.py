# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   A8/A9 settlement-axis split (consult ruling thread 6a42bc3d, read-only move #2);
#   live-owner audit docs (scratchpad/a8_a9_settlement_representation_audit.md).

"""Read-only A8/A9 settlement-axis projections — derived views over SettlementOutcome.

The live ``SettlementOutcome`` IntEnum FUSES two orthogonal facts:
  A8 — MarketResolutionSide: which binary side the market resolved (YES / NO). This is
       a property of the contract, independent of what Zeus holds.
  A9 — PositionEconomicOutcome: whether OUR position won money (WIN / LOSE / VOID).

``SettlementOutcome.VENUE_RESOLVED_WIN`` is *position-relative* ("our position wins"),
so the market side cannot be recovered from it without the position ``direction``: a
``buy_no`` WINNING position means the market resolved NO (our NO token paid), even
though the fused enum label says "WIN".

These are PURE derived views. They add NO writer, NO DB column, and do NOT renumber the
SettlementOutcome integers — they exist so consumers can branch on one typed axis
instead of re-deriving WIN/LOSE-vs-YES/NO from the overloaded integer. The redemption
ACCOUNTING axis (A9 sub-axis over the SettlementState command machine) is intentionally
NOT modelled here yet: it needs its own live-owner audit before a typed projection.
"""

from __future__ import annotations

from enum import StrEnum

from src.contracts.settlement_outcome import SettlementOutcome

_VALID_DIRECTIONS = frozenset({"buy_yes", "buy_no"})


class PositionEconomicOutcome(StrEnum):
    """A9 — did OUR position win money. Direction-free: SettlementOutcome is already
    position-relative, so this reads off it directly."""

    UNRESOLVED = "UNRESOLVED"  # economics not realized yet (pre venue-resolution)
    WIN = "WIN"
    LOSE = "LOSE"
    VOID = "VOID"  # UMA 50/50 — stake returned, no economic win/loss
    REVIEW = "REVIEW"  # disputed / revised — needs re-grade before the outcome is trusted


class MarketResolutionSide(StrEnum):
    """A8 — which binary side the market resolved, independent of what Zeus held."""

    UNRESOLVED = "UNRESOLVED"  # venue has not resolved (covers obs-confirmed-but-unresolved)
    RESOLVED_YES = "RESOLVED_YES"  # YES token paid $1
    RESOLVED_NO = "RESOLVED_NO"  # NO token paid $1
    VOID_50_50 = "VOID_50_50"  # UMA returned 0.5 / unknown
    DISPUTED = "DISPUTED"  # UMA dispute filed — side not final
    SOURCE_REVISION = "SOURCE_REVISION"  # official source revised after settlement — side may change


# Position-relative SettlementOutcome states that mean "our position won".
_POSITION_WON = frozenset({SettlementOutcome.VENUE_RESOLVED_WIN, SettlementOutcome.REDEEMED})
# States that need a re-grade before the economic outcome is trusted.
_ECONOMIC_REVIEW = frozenset({
    SettlementOutcome.DISPUTED,
    SettlementOutcome.SOURCE_REVISION,
    SettlementOutcome.OBSERVATION_REVISED,
})


def position_economic_outcome(outcome: SettlementOutcome) -> PositionEconomicOutcome:
    """Project the A9 economic outcome (WIN/LOSE/VOID/REVIEW/UNRESOLVED) from the
    position-relative SettlementOutcome. Pure; no direction needed because the
    SettlementOutcome value already encodes our side's win/loss."""
    if outcome in _POSITION_WON:
        return PositionEconomicOutcome.WIN
    if outcome is SettlementOutcome.VENUE_RESOLVED_LOSE:
        return PositionEconomicOutcome.LOSE
    if outcome is SettlementOutcome.UMA_UNKNOWN_50_50:
        return PositionEconomicOutcome.VOID
    if outcome in _ECONOMIC_REVIEW:
        return PositionEconomicOutcome.REVIEW
    # UNRESOLVED, PHYSICALLY_CONFIRMED, SOURCE_PUBLISHED_VENUE_UNRESOLVED
    return PositionEconomicOutcome.UNRESOLVED


def market_resolution_side_from_position_relative_outcome(
    outcome: SettlementOutcome,
    *,
    position_direction: str,
) -> MarketResolutionSide:
    """Project the A8 market-resolution side by UN-FUSING a genuinely PER-POSITION
    SettlementOutcome with our held direction.

    The market resolved on OUR side iff our position won; on the OPPOSITE side iff it
    lost. The void/disputed/revision/unresolved states are side-independent.

    WARNING (consult 6a42bc3d): use ONLY where ``outcome`` is a true per-position fused
    outcome. Do NOT feed event-level ``settlement_outcomes.outcome_type`` here — that
    column is backfill-corrupt (every VERIFIED row stamped VENUE_RESOLVED_WIN), so this
    would falsely infer NO for every buy_no row. For event rows derive the side from
    ``winning_bin``/bin membership via ``market_resolution_side_for_bin``.
    """
    direction = str(position_direction or "").strip().lower()
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(
            f"position_direction must be one of {sorted(_VALID_DIRECTIONS)}, got "
            f"{position_direction!r}"
        )
    position_is_yes = direction == "buy_yes"
    if outcome in _POSITION_WON:
        # our position won → the market resolved on OUR side
        return MarketResolutionSide.RESOLVED_YES if position_is_yes else MarketResolutionSide.RESOLVED_NO
    if outcome is SettlementOutcome.VENUE_RESOLVED_LOSE:
        # our position lost → the market resolved on the OPPOSITE side
        return MarketResolutionSide.RESOLVED_NO if position_is_yes else MarketResolutionSide.RESOLVED_YES
    if outcome is SettlementOutcome.UMA_UNKNOWN_50_50:
        return MarketResolutionSide.VOID_50_50
    if outcome is SettlementOutcome.DISPUTED:
        return MarketResolutionSide.DISPUTED
    if outcome is SettlementOutcome.SOURCE_REVISION:
        return MarketResolutionSide.SOURCE_REVISION
    # UNRESOLVED, PHYSICALLY_CONFIRMED, SOURCE_PUBLISHED_VENUE_UNRESOLVED, OBSERVATION_REVISED
    return MarketResolutionSide.UNRESOLVED


# --------------------------------------------------------------------------- #
# A8 event-level resolution lifecycle (consult 6a42bc3d) — the canonical A8    #
# storage axis. NO win/lose/yes/no/redeemed: economics are A9 (per-position,   #
# derived via the Direction Law); redemption is A10 (settlement_commands).     #
# --------------------------------------------------------------------------- #

class SettlementResolutionState(StrEnum):
    """A8 event-level settlement lifecycle, keyed at the (city, target_date, metric)
    grain. Carries ONLY the resolution lifecycle — never position WIN/LOSE (incoherent
    at the event grain) nor redemption accounting."""

    UNRESOLVED = "UNRESOLVED"
    PHYSICALLY_CONFIRMED = "PHYSICALLY_CONFIRMED"
    SOURCE_PUBLISHED_VENUE_UNRESOLVED = "SOURCE_PUBLISHED_VENUE_UNRESOLVED"
    VENUE_RESOLVED = "VENUE_RESOLVED"
    OBSERVATION_REVISED = "OBSERVATION_REVISED"
    DISPUTED = "DISPUTED"
    VOID_50_50 = "VOID_50_50"
    SOURCE_REVISION = "SOURCE_REVISION"


# Legacy fused outcome_type int -> event lifecycle state. The position-relative
# WIN/LOSE/REDEEMED values (3/4/5) collapse to VENUE_RESOLVED: their economics are
# intentionally DISCARDED here (they are recomputed per-position via the Direction
# Law), preserving only the lifecycle/eligibility meaning.
_LEGACY_OUTCOME_TYPE_LIFECYCLE: dict[int, SettlementResolutionState] = {
    0: SettlementResolutionState.UNRESOLVED,
    1: SettlementResolutionState.PHYSICALLY_CONFIRMED,
    2: SettlementResolutionState.SOURCE_PUBLISHED_VENUE_UNRESOLVED,
    3: SettlementResolutionState.VENUE_RESOLVED,
    4: SettlementResolutionState.VENUE_RESOLVED,
    5: SettlementResolutionState.VENUE_RESOLVED,
    6: SettlementResolutionState.OBSERVATION_REVISED,
    100: SettlementResolutionState.DISPUTED,
    101: SettlementResolutionState.VOID_50_50,
    102: SettlementResolutionState.SOURCE_REVISION,
}


def legacy_outcome_type_to_resolution_state(
    outcome_type: int | None,
    authority: str = "",
    winning_bin: object = None,
) -> SettlementResolutionState:
    """Map a legacy settlement_outcomes row to its event lifecycle state — LIFECYCLE
    ONLY, never side/economics. An explicit outcome_type wins; a NULL/unknown one falls
    back to authority + winning_bin presence (the consult's safe historical backfill)."""
    if outcome_type is not None:
        mapped = _LEGACY_OUTCOME_TYPE_LIFECYCLE.get(int(outcome_type))
        if mapped is not None:
            return mapped
    auth = str(authority or "").strip().upper()
    if auth == "VERIFIED" and winning_bin:
        return SettlementResolutionState.VENUE_RESOLVED
    if auth == "QUARANTINED":
        return SettlementResolutionState.DISPUTED
    return SettlementResolutionState.UNRESOLVED


def settlement_resolution_state_from_row(row: dict) -> SettlementResolutionState:
    """Read the canonical A8 lifecycle from a settlement_outcomes row: the explicit
    ``resolution_state`` column if present, else the legacy outcome_type/authority
    fallback. Never returns YES/NO/WIN/LOSE."""
    explicit = row.get("resolution_state")
    if explicit:
        return SettlementResolutionState(str(explicit))
    return legacy_outcome_type_to_resolution_state(
        row.get("outcome_type"),
        authority=row.get("authority") or "",
        winning_bin=row.get("winning_bin"),
    )


# Calibration/promotion eligibility — the resolution-state equivalent of the legacy
# _PROMOTION_ELIGIBLE_OUTCOMES set. Proven zero-diff vs the legacy gate across every
# outcome_type value (tests/test_settlement_axes_a8_a9.py).
PROMOTION_ELIGIBLE_RESOLUTION_STATES: frozenset[SettlementResolutionState] = frozenset({
    SettlementResolutionState.PHYSICALLY_CONFIRMED,
    SettlementResolutionState.VENUE_RESOLVED,
    SettlementResolutionState.OBSERVATION_REVISED,
    SettlementResolutionState.SOURCE_REVISION,
})


def is_promotion_eligible_resolution_state(state: SettlementResolutionState) -> bool:
    """True iff this event lifecycle state is eligible to feed promotion-grade scoring
    / calibration learning (resolved-enough), independent of who won."""
    return state in PROMOTION_ELIGIBLE_RESOLUTION_STATES


def market_resolution_side_for_bin(
    *,
    settled_in_bin: bool | None,
    resolution_state: SettlementResolutionState,
) -> MarketResolutionSide:
    """A8 market side for a SPECIFIC bin's binary market, from event lifecycle + bin
    membership (``settled_in_bin`` = did the settlement land in this bin). This is the
    event-correct path (unlike the position-relative un-fuse): a bin's YES token paid
    iff the settlement landed in that bin. Side is defined only when VENUE_RESOLVED."""
    if resolution_state is SettlementResolutionState.VOID_50_50:
        return MarketResolutionSide.VOID_50_50
    if resolution_state is SettlementResolutionState.DISPUTED:
        return MarketResolutionSide.DISPUTED
    if resolution_state is SettlementResolutionState.SOURCE_REVISION:
        return MarketResolutionSide.SOURCE_REVISION
    if resolution_state is not SettlementResolutionState.VENUE_RESOLVED:
        return MarketResolutionSide.UNRESOLVED
    if settled_in_bin is None:
        return MarketResolutionSide.UNRESOLVED
    return MarketResolutionSide.RESOLVED_YES if settled_in_bin else MarketResolutionSide.RESOLVED_NO


def economic_outcome_for_position(
    *,
    settled_in_bin: bool | None,
    direction: str,
) -> PositionEconomicOutcome:
    """A9 economic outcome for a position via the Direction Law — the CANONICAL
    per-position grading path (matches grade_receipt). buy_yes wins iff settlement
    landed in its bin; buy_no wins iff it did NOT. Unresolved bin membership -> UNRESOLVED.
    """
    d = str(direction or "").strip().lower()
    if d not in _VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(_VALID_DIRECTIONS)}, got {direction!r}")
    if settled_in_bin is None:
        return PositionEconomicOutcome.UNRESOLVED
    won = settled_in_bin if d == "buy_yes" else (not settled_in_bin)
    return PositionEconomicOutcome.WIN if won else PositionEconomicOutcome.LOSE
