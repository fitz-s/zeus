# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   A8/A9 settlement-axis split; consult ruling thread 6a42bc3d (read-only projection move #2);
#   live-owner audit scratchpad/a8_a9_settlement_representation_audit.md.

"""Read-only A8/A9 settlement-axis projections — derived views over SettlementOutcome.

The live SettlementOutcome IntEnum FUSES two orthogonal facts (audit §SUMMARY/§E1-E3):
A8 = which market side resolved (YES/NO), A9 = whether OUR position won money.
VENUE_RESOLVED_WIN is position-relative ("our position wins"), so the market side
cannot be recovered without the position direction — a buy_no WINNING position means
the market resolved NO. These pure projections make the two axes explicit and typed
WITHOUT touching any writer, DB column, or the SettlementOutcome integer values.
"""

from __future__ import annotations

import pytest

from src.contracts.settlement_axes import (
    MarketResolutionSide,
    PositionEconomicOutcome,
    market_resolution_side,
    position_economic_outcome,
)
from src.contracts.settlement_outcome import SettlementOutcome


def test_settlement_outcome_int_values_unchanged() -> None:
    # The split must NOT renumber the live IntEnum (DB-persisted integers).
    assert SettlementOutcome.UNRESOLVED == 0
    assert SettlementOutcome.PHYSICALLY_CONFIRMED == 1
    assert SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED == 2
    assert SettlementOutcome.VENUE_RESOLVED_WIN == 3
    assert SettlementOutcome.VENUE_RESOLVED_LOSE == 4
    assert SettlementOutcome.REDEEMED == 5
    assert SettlementOutcome.OBSERVATION_REVISED == 6
    assert SettlementOutcome.DISPUTED == 100
    assert SettlementOutcome.UMA_UNKNOWN_50_50 == 101
    assert SettlementOutcome.SOURCE_REVISION == 102


# --- A9 economic outcome (position-relative; direction-free) -------------------- #

@pytest.mark.parametrize("outcome,expected", [
    (SettlementOutcome.VENUE_RESOLVED_WIN, PositionEconomicOutcome.WIN),
    (SettlementOutcome.REDEEMED, PositionEconomicOutcome.WIN),
    (SettlementOutcome.VENUE_RESOLVED_LOSE, PositionEconomicOutcome.LOSE),
    (SettlementOutcome.UMA_UNKNOWN_50_50, PositionEconomicOutcome.VOID),
    (SettlementOutcome.DISPUTED, PositionEconomicOutcome.REVIEW),
    (SettlementOutcome.SOURCE_REVISION, PositionEconomicOutcome.REVIEW),
    (SettlementOutcome.OBSERVATION_REVISED, PositionEconomicOutcome.REVIEW),
    (SettlementOutcome.UNRESOLVED, PositionEconomicOutcome.UNRESOLVED),
    (SettlementOutcome.PHYSICALLY_CONFIRMED, PositionEconomicOutcome.UNRESOLVED),
    (SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED, PositionEconomicOutcome.UNRESOLVED),
])
def test_position_economic_outcome(outcome: SettlementOutcome, expected: PositionEconomicOutcome) -> None:
    assert position_economic_outcome(outcome) is expected


# --- A8 market side (un-fused via direction; the audit's 4-row table) ----------- #

@pytest.mark.parametrize("outcome,direction,expected", [
    (SettlementOutcome.VENUE_RESOLVED_WIN, "buy_yes", MarketResolutionSide.RESOLVED_YES),
    (SettlementOutcome.VENUE_RESOLVED_WIN, "buy_no", MarketResolutionSide.RESOLVED_NO),
    (SettlementOutcome.VENUE_RESOLVED_LOSE, "buy_yes", MarketResolutionSide.RESOLVED_NO),
    (SettlementOutcome.VENUE_RESOLVED_LOSE, "buy_no", MarketResolutionSide.RESOLVED_YES),
    (SettlementOutcome.REDEEMED, "buy_no", MarketResolutionSide.RESOLVED_NO),
    (SettlementOutcome.REDEEMED, "buy_yes", MarketResolutionSide.RESOLVED_YES),
])
def test_market_resolution_side_unfuses_by_direction(
    outcome: SettlementOutcome, direction: str, expected: MarketResolutionSide
) -> None:
    assert market_resolution_side(outcome, position_direction=direction) is expected


@pytest.mark.parametrize("outcome,expected", [
    (SettlementOutcome.UMA_UNKNOWN_50_50, MarketResolutionSide.VOID_50_50),
    (SettlementOutcome.DISPUTED, MarketResolutionSide.DISPUTED),
    (SettlementOutcome.SOURCE_REVISION, MarketResolutionSide.SOURCE_REVISION),
    (SettlementOutcome.UNRESOLVED, MarketResolutionSide.UNRESOLVED),
    (SettlementOutcome.PHYSICALLY_CONFIRMED, MarketResolutionSide.UNRESOLVED),
    (SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED, MarketResolutionSide.UNRESOLVED),
])
def test_market_resolution_side_direction_independent(
    outcome: SettlementOutcome, expected: MarketResolutionSide
) -> None:
    # These states don't depend on our side — any direction yields the same market side.
    assert market_resolution_side(outcome, position_direction="buy_yes") is expected
    assert market_resolution_side(outcome, position_direction="buy_no") is expected


def test_market_resolution_side_rejects_bad_direction() -> None:
    with pytest.raises(ValueError):
        market_resolution_side(SettlementOutcome.VENUE_RESOLVED_WIN, position_direction="hodl")


def test_buy_no_win_is_market_resolved_no_audit_invariant() -> None:
    # The audit's smoking gun: a buy_no WINNING position means the market resolved NO
    # (our NO token paid) — even though the fused enum label literally says "WIN".
    o = SettlementOutcome.VENUE_RESOLVED_WIN
    assert position_economic_outcome(o) is PositionEconomicOutcome.WIN
    assert market_resolution_side(o, position_direction="buy_no") is MarketResolutionSide.RESOLVED_NO


def test_axes_are_strenum() -> None:
    assert PositionEconomicOutcome.WIN == "WIN"
    assert MarketResolutionSide.RESOLVED_NO == "RESOLVED_NO"
    assert {s.value for s in PositionEconomicOutcome} == {
        "UNRESOLVED", "WIN", "LOSE", "VOID", "REVIEW",
    }
    assert {s.value for s in MarketResolutionSide} == {
        "UNRESOLVED", "RESOLVED_YES", "RESOLVED_NO", "VOID_50_50", "DISPUTED", "SOURCE_REVISION",
    }
