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


def market_resolution_side(
    outcome: SettlementOutcome,
    *,
    position_direction: str,
) -> MarketResolutionSide:
    """Project the A8 market-resolution side by UN-FUSING the position-relative
    SettlementOutcome with our held direction.

    The market resolved on OUR side iff our position won; on the OPPOSITE side iff it
    lost. The void/disputed/revision/unresolved states are side-independent.
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
