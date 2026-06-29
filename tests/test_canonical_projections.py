# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   §1 reducer contract / §4 governor read-only pilot (consult round-2).

"""Tests for the canonical A4 exposure projection predicates.

These centralize the lot-state -> exposure classification that risk_allocator/
governor.py currently inlines via local frozensets. The predicates must be
BEHAVIOR-IDENTICAL to governor's current logic (equivalence-preserving pilot):
ACTIVE = {OPTIMISTIC_EXPOSURE, CONFIRMED_EXPOSURE, EXIT_PENDING}; CLOSED =
{ECONOMICALLY_CLOSED_OPTIMISTIC, ECONOMICALLY_CLOSED_CONFIRMED, SETTLED};
QUARANTINED and anything else contribute zero exposure.
"""

from __future__ import annotations

import pytest

from src.contracts.canonical_lifecycle import (
    ExitProgress,
    ExposureState,
    LegacyOrderResultStatus,
    OrderProofClass,
    PositionPhase,
)
from src.state.canonical_projections import (
    OPEN_ORDER_FACT_STATES,
    counts_as_active_exposure,
    derive_exit_progress,
    derive_order_result_status,
    derive_position_phase,
    is_closed_exposure,
    is_open_order_fact,
    is_optimistic_exposure,
    weighted_lot_exposure_micro,
)


# --------------------------------------------------------------------------- #
# A5 — PositionPhase derived (10-rule precedence over truth facts)             #
# --------------------------------------------------------------------------- #

def test_position_phase_membership() -> None:
    assert {s.value for s in PositionPhase} == {
        "pending_entry", "active", "day0_window", "pending_exit",
        "economically_closed", "settled", "voided", "quarantined",
        "admin_closed", "unknown",
    }


def test_phase_nothing_is_unknown() -> None:
    assert derive_position_phase() is PositionPhase.UNKNOWN


def test_phase_entry_intent_only_is_pending_entry() -> None:
    assert derive_position_phase(has_entry_intent=True) is PositionPhase.PENDING_ENTRY


def test_phase_positive_exposure_is_active() -> None:
    assert derive_position_phase(has_positive_exposure=True, has_entry_intent=True) is PositionPhase.ACTIVE


def test_phase_exposure_in_day0_is_day0() -> None:
    assert derive_position_phase(has_positive_exposure=True, in_day0_window=True) is PositionPhase.DAY0_WINDOW


def test_phase_open_exit_is_pending_exit() -> None:
    assert derive_position_phase(has_positive_exposure=True, has_open_exit=True) is PositionPhase.PENDING_EXIT


def test_phase_economic_close_beats_exposure() -> None:
    assert derive_position_phase(has_positive_exposure=True, has_open_exit=True, has_economic_close=True) is PositionPhase.ECONOMICALLY_CLOSED


def test_phase_quarantined() -> None:
    assert derive_position_phase(is_quarantined=True, has_positive_exposure=True) is PositionPhase.QUARANTINED


def test_phase_voided_beats_quarantine() -> None:
    assert derive_position_phase(is_voided=True, is_quarantined=True) is PositionPhase.VOIDED


def test_phase_settled_beats_voided() -> None:
    assert derive_position_phase(has_settlement=True, is_voided=True, has_economic_close=True) is PositionPhase.SETTLED


def test_phase_admin_close_is_highest() -> None:
    assert derive_position_phase(has_admin_close=True, has_settlement=True, is_voided=True) is PositionPhase.ADMIN_CLOSED


# --------------------------------------------------------------------------- #
# A4 authority — RecoveryAuthority derived from FillAuthority + CausalityStatus #
# --------------------------------------------------------------------------- #

from src.contracts.position_truth import CausalityStatus, FillAuthority, RecoveryAuthority
from src.state.canonical_projections import derive_recovery_class


@pytest.mark.parametrize("fa", [
    FillAuthority.VENUE_CONFIRMED_PARTIAL, FillAuthority.VENUE_CONFIRMED_FULL,
    FillAuthority.CANCELLED_REMAINDER, FillAuthority.SETTLED,
])
def test_trade_verified_when_confirmed_and_causality_ok(fa) -> None:
    assert derive_recovery_class(fill_authority=fa, causality_status=CausalityStatus.OK) is RecoveryAuthority.TRADE_VERIFIED


def test_balance_only_when_venue_position_observed() -> None:
    # Shared-wallet reality: chain balance, tradable, but no linked trade fact.
    assert derive_recovery_class(fill_authority=FillAuthority.VENUE_POSITION_OBSERVED, causality_status=CausalityStatus.OK) is RecoveryAuthority.BALANCE_ONLY


def test_balance_only_when_causality_not_ok() -> None:
    assert derive_recovery_class(fill_authority=FillAuthority.VENUE_CONFIRMED_FULL, causality_status=CausalityStatus.UNVERIFIED) is RecoveryAuthority.BALANCE_ONLY


@pytest.mark.parametrize("fa", [FillAuthority.NONE, FillAuthority.OPTIMISTIC_SUBMITTED])
def test_balance_only_for_weak_authority(fa) -> None:
    assert derive_recovery_class(fill_authority=fa, causality_status=CausalityStatus.OK) is RecoveryAuthority.BALANCE_ONLY


# --------------------------------------------------------------------------- #
# A1 — OrderResult.status derived from command + order truth                    #
# --------------------------------------------------------------------------- #

def test_order_result_status_membership() -> None:
    assert {s.value for s in LegacyOrderResultStatus} == {
        "filled", "pending", "rejected", "unknown_side_effect",
    }


def test_rejected_command_is_rejected() -> None:
    assert derive_order_result_status(command_rejected=True, order_proof_class=OrderProofClass.LIVE_RESTING) is LegacyOrderResultStatus.REJECTED


def test_terminal_filled_is_filled() -> None:
    assert derive_order_result_status(command_rejected=False, order_proof_class=OrderProofClass.TERMINAL_FILLED) is LegacyOrderResultStatus.FILLED


def test_terminal_partial_with_fill_is_filled() -> None:
    assert derive_order_result_status(command_rejected=False, order_proof_class=OrderProofClass.TERMINAL_PARTIAL, matched_size=3) is LegacyOrderResultStatus.FILLED


def test_order_result_accepts_decimal_matched_size() -> None:
    # Live execution/reducer sizes are Decimal; the derive must accept them.
    from decimal import Decimal
    assert derive_order_result_status(command_rejected=False, order_proof_class=OrderProofClass.TERMINAL_PARTIAL, matched_size=Decimal("0.1")) is LegacyOrderResultStatus.FILLED


@pytest.mark.parametrize("pc", [OrderProofClass.LIVE_RESTING, OrderProofClass.PARTIAL_WITH_REMAINDER])
def test_open_proof_is_pending(pc) -> None:
    assert derive_order_result_status(command_rejected=False, order_proof_class=pc) is LegacyOrderResultStatus.PENDING


def test_terminal_no_fill_is_rejected() -> None:
    assert derive_order_result_status(command_rejected=False, order_proof_class=OrderProofClass.TERMINAL_NO_FILL) is LegacyOrderResultStatus.REJECTED


@pytest.mark.parametrize("pc", [OrderProofClass.UNKNOWN_SIDE_EFFECT, OrderProofClass.REVIEW_REQUIRED])
def test_unknown_or_review_is_unknown_side_effect(pc) -> None:
    assert derive_order_result_status(command_rejected=False, order_proof_class=pc) is LegacyOrderResultStatus.UNKNOWN_SIDE_EFFECT


# --------------------------------------------------------------------------- #
# A6 — ExitProgress derived as a pure view over the sell command's order truth #
# --------------------------------------------------------------------------- #

def test_exit_progress_committed_membership() -> None:
    assert {s.value for s in ExitProgress} == {
        "", "exit_intent", "sell_open", "sell_partially_filled",
        "sell_filled", "retry_pending", "backoff_exhausted", "review_required",
    }


def test_no_exit_command_is_none() -> None:
    assert derive_exit_progress(has_exit_command=False, has_venue_fact=False, order_proof_class=None) is ExitProgress.NONE


def test_exit_command_without_venue_fact_is_intent() -> None:
    assert derive_exit_progress(has_exit_command=True, has_venue_fact=False, order_proof_class=None) is ExitProgress.EXIT_INTENT


def test_live_resting_sell_is_sell_open() -> None:
    assert derive_exit_progress(has_exit_command=True, has_venue_fact=True, order_proof_class=OrderProofClass.LIVE_RESTING) is ExitProgress.SELL_OPEN


def test_partial_with_remainder_sell_is_partially_filled() -> None:
    assert derive_exit_progress(has_exit_command=True, has_venue_fact=True, order_proof_class=OrderProofClass.PARTIAL_WITH_REMAINDER) is ExitProgress.SELL_PARTIALLY_FILLED


@pytest.mark.parametrize("pc", [OrderProofClass.TERMINAL_FILLED, OrderProofClass.TERMINAL_PARTIAL])
def test_terminal_fill_sell_is_sell_filled(pc) -> None:
    assert derive_exit_progress(has_exit_command=True, has_venue_fact=True, order_proof_class=pc) is ExitProgress.SELL_FILLED


@pytest.mark.parametrize("pc", [OrderProofClass.UNKNOWN_SIDE_EFFECT, OrderProofClass.REVIEW_REQUIRED])
def test_unknown_or_review_sell_is_review_required(pc) -> None:
    assert derive_exit_progress(has_exit_command=True, has_venue_fact=True, order_proof_class=pc) is ExitProgress.REVIEW_REQUIRED


def test_retry_and_backoff_take_precedence_over_intent() -> None:
    assert derive_exit_progress(has_exit_command=True, has_venue_fact=False, order_proof_class=None, retry_pending=True) is ExitProgress.RETRY_PENDING
    assert derive_exit_progress(has_exit_command=True, has_venue_fact=False, order_proof_class=None, backoff_exhausted=True) is ExitProgress.BACKOFF_EXHAUSTED


def test_open_order_fact_states_match_legacy_set() -> None:
    # Exactly the {LIVE, RESTING, PARTIALLY_MATCHED} set the consumers used
    # (order_truth_reducer._OPEN_STATES, maker_rest_escalation, venue_command_repo).
    assert set(OPEN_ORDER_FACT_STATES) == {"LIVE", "RESTING", "PARTIALLY_MATCHED"}


@pytest.mark.parametrize("state", ["LIVE", "RESTING", "PARTIALLY_MATCHED"])
def test_open_order_fact_true_for_open_states(state: str) -> None:
    assert is_open_order_fact(state) is True


@pytest.mark.parametrize("state", ["MATCHED", "CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED", "", "UNKNOWN"])
def test_open_order_fact_false_for_non_open(state: str) -> None:
    assert is_open_order_fact(state) is False


@pytest.mark.parametrize("state", ["OPTIMISTIC_EXPOSURE", "CONFIRMED_EXPOSURE", "EXIT_PENDING"])
def test_active_states_count_as_active_exposure(state: str) -> None:
    assert counts_as_active_exposure(state) is True


@pytest.mark.parametrize("state", ["ECONOMICALLY_CLOSED_OPTIMISTIC", "ECONOMICALLY_CLOSED_CONFIRMED", "SETTLED", "QUARANTINED"])
def test_non_active_states_do_not_count_as_active(state: str) -> None:
    assert counts_as_active_exposure(state) is False


@pytest.mark.parametrize("state", ["ECONOMICALLY_CLOSED_OPTIMISTIC", "ECONOMICALLY_CLOSED_CONFIRMED", "SETTLED"])
def test_closed_states_are_closed(state: str) -> None:
    assert is_closed_exposure(state) is True


@pytest.mark.parametrize("state", ["OPTIMISTIC_EXPOSURE", "CONFIRMED_EXPOSURE", "EXIT_PENDING", "QUARANTINED"])
def test_non_closed_states_are_not_closed(state: str) -> None:
    assert is_closed_exposure(state) is False


def test_is_optimistic_exposure_ties_to_canonical_enum() -> None:
    assert is_optimistic_exposure("OPTIMISTIC_EXPOSURE") is True
    assert is_optimistic_exposure(ExposureState.OPTIMISTIC_EXPOSURE) is True
    assert is_optimistic_exposure("CONFIRMED_EXPOSURE") is False


def test_weighted_optimistic_uses_optimistic_weight() -> None:
    # Matches governor: int(round(exposure_micro * optimistic_weight)).
    assert weighted_lot_exposure_micro("OPTIMISTIC_EXPOSURE", 1_000_000, 0.5) == 500_000
    assert weighted_lot_exposure_micro("OPTIMISTIC_EXPOSURE", 1_000_001, 0.5) == 500_000  # round-half-to-even


@pytest.mark.parametrize("state", ["CONFIRMED_EXPOSURE", "EXIT_PENDING"])
def test_weighted_active_nonoptimistic_is_full_micro(state: str) -> None:
    assert weighted_lot_exposure_micro(state, 1_000_000, 0.5) == 1_000_000


@pytest.mark.parametrize("state", ["ECONOMICALLY_CLOSED_OPTIMISTIC", "SETTLED", "QUARANTINED", "NONSENSE"])
def test_weighted_closed_or_unknown_is_zero(state: str) -> None:
    assert weighted_lot_exposure_micro(state, 1_000_000, 0.5) == 0
