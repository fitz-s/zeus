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
    PROMOTION_ELIGIBLE_RESOLUTION_STATES,
    MarketResolutionSide,
    PositionEconomicOutcome,
    RedemptionAccountingPhase,
    SettlementResolutionState,
    economic_outcome_for_position,
    is_promotion_eligible_resolution_state,
    legacy_outcome_type_to_resolution_state,
    market_resolution_side_from_position_relative_outcome,
    market_resolution_side_for_bin,
    position_economic_outcome,
    redemption_accounting_phase,
    settlement_resolution_state_from_row,
)
from src.contracts.settlement_outcome import SettlementOutcome
from src.contracts.settlement_resolution import _PROMOTION_ELIGIBLE_OUTCOMES


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
    assert market_resolution_side_from_position_relative_outcome(outcome, position_direction=direction) is expected


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
    assert market_resolution_side_from_position_relative_outcome(outcome, position_direction="buy_yes") is expected
    assert market_resolution_side_from_position_relative_outcome(outcome, position_direction="buy_no") is expected


def test_market_resolution_side_rejects_bad_direction() -> None:
    with pytest.raises(ValueError):
        market_resolution_side_from_position_relative_outcome(SettlementOutcome.VENUE_RESOLVED_WIN, position_direction="hodl")


def test_buy_no_win_is_market_resolved_no_audit_invariant() -> None:
    # The audit's smoking gun: a buy_no WINNING position means the market resolved NO
    # (our NO token paid) — even though the fused enum label literally says "WIN".
    o = SettlementOutcome.VENUE_RESOLVED_WIN
    assert position_economic_outcome(o) is PositionEconomicOutcome.WIN
    assert market_resolution_side_from_position_relative_outcome(o, position_direction="buy_no") is MarketResolutionSide.RESOLVED_NO


def test_axes_are_strenum() -> None:
    assert PositionEconomicOutcome.WIN == "WIN"
    assert MarketResolutionSide.RESOLVED_NO == "RESOLVED_NO"
    assert {s.value for s in PositionEconomicOutcome} == {
        "UNRESOLVED", "WIN", "LOSE", "VOID", "REVIEW",
    }
    assert {s.value for s in MarketResolutionSide} == {
        "UNRESOLVED", "RESOLVED_YES", "RESOLVED_NO", "VOID_50_50", "DISPUTED", "SOURCE_REVISION",
    }


# --- A8 event-level resolution-state lifecycle + legacy bridge (consult 6a42bc3d) -- #

def test_resolution_state_membership() -> None:
    assert {s.value for s in SettlementResolutionState} == {
        "UNRESOLVED", "PHYSICALLY_CONFIRMED", "SOURCE_PUBLISHED_VENUE_UNRESOLVED",
        "VENUE_RESOLVED", "OBSERVATION_REVISED", "DISPUTED", "VOID_50_50", "SOURCE_REVISION",
    }


def test_resolution_state_carries_no_economics_or_redemption() -> None:
    # The event-level lifecycle axis must NOT embed position economics / market side /
    # redemption — those are A9 (per-position) and A10 (redemption) respectively.
    forbidden = {"WIN", "LOSE", "YES", "NO", "RESOLVED_YES", "RESOLVED_NO", "REDEEMED"}
    assert forbidden.isdisjoint({s.value for s in SettlementResolutionState})


@pytest.mark.parametrize("ot,expected", [
    (0, SettlementResolutionState.UNRESOLVED),
    (1, SettlementResolutionState.PHYSICALLY_CONFIRMED),
    (2, SettlementResolutionState.SOURCE_PUBLISHED_VENUE_UNRESOLVED),
    (3, SettlementResolutionState.VENUE_RESOLVED),   # was VENUE_RESOLVED_WIN — economics discarded
    (4, SettlementResolutionState.VENUE_RESOLVED),   # was VENUE_RESOLVED_LOSE
    (5, SettlementResolutionState.VENUE_RESOLVED),   # was REDEEMED (lifecycle only; redemption is A10)
    (6, SettlementResolutionState.OBSERVATION_REVISED),
    (100, SettlementResolutionState.DISPUTED),
    (101, SettlementResolutionState.VOID_50_50),
    (102, SettlementResolutionState.SOURCE_REVISION),
])
def test_legacy_outcome_type_maps_to_lifecycle_only(ot: int, expected: SettlementResolutionState) -> None:
    assert legacy_outcome_type_to_resolution_state(ot) is expected


def test_legacy_null_outcome_type_falls_back_to_authority() -> None:
    assert legacy_outcome_type_to_resolution_state(
        None, authority="VERIFIED", winning_bin="21-22C"
    ) is SettlementResolutionState.VENUE_RESOLVED
    assert legacy_outcome_type_to_resolution_state(
        None, authority="UNVERIFIED", winning_bin=None
    ) is SettlementResolutionState.UNRESOLVED
    assert legacy_outcome_type_to_resolution_state(
        None, authority="DISPUTED", winning_bin=None
    ) is SettlementResolutionState.DISPUTED


def test_resolution_state_accessor_prefers_explicit_column() -> None:
    # The new column wins; the legacy fallback fires only when resolution_state is absent.
    explicit = {"resolution_state": "DISPUTED", "outcome_type": 3, "authority": "VERIFIED", "winning_bin": "x"}
    assert settlement_resolution_state_from_row(explicit) is SettlementResolutionState.DISPUTED
    legacy = {"resolution_state": None, "outcome_type": 3, "authority": "VERIFIED", "winning_bin": "x"}
    assert settlement_resolution_state_from_row(legacy) is SettlementResolutionState.VENUE_RESOLVED


def test_promotion_eligibility_is_zero_diff_vs_legacy() -> None:
    # The lifecycle remap must preserve the calibration/promotion gate EXACTLY across
    # every legacy outcome_type value — the all-WIN backfill must NOT change eligibility.
    for ot in SettlementOutcome:
        new_state = legacy_outcome_type_to_resolution_state(int(ot))
        new_eligible = is_promotion_eligible_resolution_state(new_state)
        old_eligible = ot in _PROMOTION_ELIGIBLE_OUTCOMES
        assert new_eligible == old_eligible, (
            f"eligibility diff at outcome_type={ot.name}: old={old_eligible} new={new_eligible}"
        )


# --- A8 event-level market side (from bin membership, NOT the fused outcome) ----- #

@pytest.mark.parametrize("settled,expected", [
    (True, MarketResolutionSide.RESOLVED_YES),
    (False, MarketResolutionSide.RESOLVED_NO),
    (None, MarketResolutionSide.UNRESOLVED),
])
def test_market_side_for_bin_when_venue_resolved(settled, expected: MarketResolutionSide) -> None:
    assert market_resolution_side_for_bin(
        settled_in_bin=settled, resolution_state=SettlementResolutionState.VENUE_RESOLVED
    ) is expected


@pytest.mark.parametrize("state,expected", [
    (SettlementResolutionState.VOID_50_50, MarketResolutionSide.VOID_50_50),
    (SettlementResolutionState.DISPUTED, MarketResolutionSide.DISPUTED),
    (SettlementResolutionState.SOURCE_REVISION, MarketResolutionSide.SOURCE_REVISION),
    (SettlementResolutionState.UNRESOLVED, MarketResolutionSide.UNRESOLVED),
    (SettlementResolutionState.PHYSICALLY_CONFIRMED, MarketResolutionSide.UNRESOLVED),
])
def test_market_side_for_bin_non_resolved_states(state: SettlementResolutionState, expected: MarketResolutionSide) -> None:
    # The market side is undefined unless the event is VENUE_RESOLVED.
    assert market_resolution_side_for_bin(settled_in_bin=True, resolution_state=state) is expected


# --- A9 economic outcome via the Direction Law (the canonical per-position path) -- #

@pytest.mark.parametrize("settled,direction,expected", [
    (True, "buy_yes", PositionEconomicOutcome.WIN),    # in winning bin, held YES -> win
    (False, "buy_yes", PositionEconomicOutcome.LOSE),
    (True, "buy_no", PositionEconomicOutcome.LOSE),    # in winning bin, held NO -> lose
    (False, "buy_no", PositionEconomicOutcome.WIN),    # outside winning bin, held NO -> win
    (None, "buy_no", PositionEconomicOutcome.UNRESOLVED),
])
def test_economic_outcome_for_position_direction_law(settled, direction: str, expected: PositionEconomicOutcome) -> None:
    assert economic_outcome_for_position(settled_in_bin=settled, direction=direction) is expected


def test_economic_outcome_for_position_rejects_bad_direction() -> None:
    with pytest.raises(ValueError):
        economic_outcome_for_position(settled_in_bin=True, direction="hodl")


# --- step 4 (consult 6a42bc3d): the live Brier/calibration path is ALREADY -------- #
# --- corruption-free — lock it instead of "repairing" it ------------------------- #

def _live_exit_price_outcome(settled_in_bin: bool, direction: str) -> int:
    """The live A9 computation (harvester._settle_positions, audit §E5): exit_price
    applies the Direction Law; outcome_fact.outcome = 1 iff exit_price > 0."""
    if direction == "buy_yes":
        exit_price = 1.0 if settled_in_bin else 0.0
    else:  # buy_no
        exit_price = 1.0 if not settled_in_bin else 0.0
    return 1 if exit_price > 0 else 0


@pytest.mark.parametrize("settled,direction", [
    (True, "buy_yes"), (False, "buy_yes"), (True, "buy_no"), (False, "buy_no"),
])
def test_canonical_a9_matches_live_exit_price_outcome(settled: bool, direction: str) -> None:
    # The canonical Direction-Law A9 (economic_outcome_for_position) reproduces the live
    # outcome_fact.outcome EXACTLY. riskguard's Brier reads `outcome` (A9), so it is
    # already corruption-free: the settlement_outcomes.outcome_type backfill corruption
    # never reaches a scoring target — it was confined to the promotion-eligibility gate,
    # which step 3 cut to the typed accessor (zero-diff). No Brier "repair" is needed.
    canonical_win = economic_outcome_for_position(settled_in_bin=settled, direction=direction) is PositionEconomicOutcome.WIN
    live_win = _live_exit_price_outcome(settled, direction) == 1
    assert canonical_win == live_win


def test_event_outcome_type_confined_to_sanctioned_modules() -> None:
    # Broadened ratchet (consult 6a42bc3d): the corrupt event-level settlement_outcomes.
    # outcome_type may appear ONLY in the settlement-storage / legacy-bridge / schema /
    # migration / backfill modules — NEVER in a scoring / learning / ARM / calibration path
    # as a target (those grade A9 from outcome_fact.outcome / attribution.won / winning_bin).
    # Any new src/ or scripts/ file referencing it fails until explicitly classified here.
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[1]
    allow = {
        "src/contracts/settlement_outcome.py",
        "src/contracts/settlement_axes.py",
        "src/contracts/settlement_resolution.py",
        "src/state/db.py",
        "src/state/db_writer_lock.py",
        "scripts/backfill_settlement_outcome_type.py",
    }
    offenders = []
    for base in ("src", "scripts"):
        for py in (repo / base).rglob("*.py"):
            rel = py.relative_to(repo).as_posix()
            if rel in allow or "/schema/" in rel or "migration" in rel or "backfill" in rel:
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if "outcome_type" in line and "outcome_type_raw" not in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "settlement_outcomes.outcome_type referenced outside the sanctioned storage/legacy "
        "modules — it must never become a scoring/learning target (derive A9 from "
        "outcome_fact.outcome / attribution.won). Classify or remove: " + " | ".join(offenders[:10])
    )


# --- A10 redemption-accounting axis (consult 6a42bc3d: a distinct third axis) ---- #
# Redemption is ACCOUNTING-ONLY: Zeus never submits a redeem tx (operator law
# 2026-06-10); a third-party auto-redeem owns submission. This phase is an OBSERVED
# accounting lifecycle over settlement_commands.state, never a Zeus action and never
# market side (A8) or economic win/loss (A9).

def test_redemption_accounting_phase_membership() -> None:
    assert {s.value for s in RedemptionAccountingPhase} == {
        "NOT_RECORDED", "INTENT_RECORDED", "TX_OBSERVED", "CONFIRMED",
        "REVIEW_REQUIRED", "OPERATOR_REQUIRED", "FAILED",
    }


@pytest.mark.parametrize("state,expected", [
    (None, RedemptionAccountingPhase.NOT_RECORDED),
    ("", RedemptionAccountingPhase.NOT_RECORDED),
    ("REDEEM_INTENT_CREATED", RedemptionAccountingPhase.INTENT_RECORDED),
    ("REDEEM_RETRYING", RedemptionAccountingPhase.INTENT_RECORDED),
    ("REDEEM_SUBMITTED", RedemptionAccountingPhase.TX_OBSERVED),
    ("REDEEM_TX_HASHED", RedemptionAccountingPhase.TX_OBSERVED),
    ("REDEEM_CONFIRMED", RedemptionAccountingPhase.CONFIRMED),
    ("REDEEM_FAILED", RedemptionAccountingPhase.FAILED),
    ("REDEEM_REVIEW_REQUIRED", RedemptionAccountingPhase.REVIEW_REQUIRED),
    ("REDEEM_OPERATOR_REQUIRED", RedemptionAccountingPhase.OPERATOR_REQUIRED),
])
def test_redemption_accounting_phase_maps_settlement_state(state, expected: RedemptionAccountingPhase) -> None:
    assert redemption_accounting_phase(state) is expected


def test_redemption_accounting_phase_unknown_is_review() -> None:
    # An unmapped/unexpected state surfaces for operator review (fail-safe), not silent.
    assert redemption_accounting_phase("REDEEM_GARBAGE") is RedemptionAccountingPhase.REVIEW_REQUIRED


def test_redemption_phase_covers_every_live_settlement_state() -> None:
    # Antibody: every live SettlementState value must map to a real (non-NOT_RECORDED)
    # phase — a new redemption state cannot silently fall through. Importing the live
    # enum in the TEST keeps the contracts module free of an execution dependency.
    from src.execution.settlement_commands import SettlementState
    for st in SettlementState:
        phase = redemption_accounting_phase(st.value)
        assert phase is not RedemptionAccountingPhase.NOT_RECORDED, f"{st.value} unmapped"


# --- review hardening (consult 6a42bc3d [S2]): unknown non-null outcome_type fails closed --- #

def test_unknown_nonnull_outcome_type_fails_closed_to_unresolved() -> None:
    # A non-null UNKNOWN integer (writer/schema bug or a future enum value) must NOT be
    # promoted via the authority+winning_bin fallback; it fails closed to UNRESOLVED. The
    # authority fallback applies ONLY when outcome_type IS NULL.
    assert legacy_outcome_type_to_resolution_state(
        999, authority="VERIFIED", winning_bin="21-22C"
    ) is SettlementResolutionState.UNRESOLVED


def test_known_outcome_type_ignores_authority_fallback() -> None:
    # A known non-null value is authoritative regardless of authority/winning_bin.
    assert legacy_outcome_type_to_resolution_state(
        0, authority="VERIFIED", winning_bin="21-22C"
    ) is SettlementResolutionState.UNRESOLVED  # ot=0 -> UNRESOLVED, not VENUE_RESOLVED
