# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   §1 reducer semantic contract (consult round-2); INV-CL-1 single-ingress-normalizer invariant.

"""Tests for the canonical lifecycle vocabulary + single ingress normalizers.

The normalizers are the ONLY sanctioned conversion from raw venue/API/DB status
text into typed domain status (INV-CL-1). They fold the synonym soup
(LIVE/RESTING/OPEN/ACCEPTED, CANCELLED/CANCELED, PARTIAL*) to one canonical value.
"""

from __future__ import annotations

import pytest

from src.contracts.canonical_lifecycle import (
    CommandTruthState,
    ExposureState,
    OrderProofClass,
    PositionPhase,
    VenueOrderStatus,
    VenueStatusIngress,
    VenueTradeStatus,
    normalize_command_truth_state,
    normalize_venue_order_status,
    normalize_venue_trade_status,
)


# --------------------------------------------------------------------------- #
# Enum closure — the committed canonical value-sets (live-DB grounded)         #
# --------------------------------------------------------------------------- #

def test_venue_order_status_is_the_six_db_canonical_values() -> None:
    assert {s.value for s in VenueOrderStatus} == {
        "LIVE", "PARTIALLY_MATCHED", "MATCHED", "CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED",
    }


def test_venue_trade_status_is_the_five_trade_chain_values() -> None:
    assert {s.value for s in VenueTradeStatus} == {
        "MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED",
    }


def test_strenum_members_interchangeable_with_raw_strings() -> None:
    # The byte-identical reducer/fill_tracker cutovers rely on StrEnum members being
    # equal to + hash-equal to + set-interchangeable with their raw string values.
    assert VenueOrderStatus.MATCHED == "MATCHED"
    assert hash(VenueOrderStatus.MATCHED) == hash("MATCHED")
    assert "MATCHED" in {VenueOrderStatus.MATCHED}
    assert "CONFIRMED" in {VenueTradeStatus.CONFIRMED}
    assert "active" in {PositionPhase.ACTIVE}


def test_exposure_state_is_only_the_two_live_values() -> None:
    # Committed simplification: position_lots.state live DB uses ONLY these two.
    assert {s.value for s in ExposureState} == {"OPTIMISTIC_EXPOSURE", "CONFIRMED_EXPOSURE"}


def test_order_proof_class_committed_membership() -> None:
    assert {s.value for s in OrderProofClass} == {
        "TERMINAL_NO_FILL", "TERMINAL_FILLED", "TERMINAL_PARTIAL",
        "PARTIAL_WITH_REMAINDER", "LIVE_RESTING", "UNKNOWN_SIDE_EFFECT", "REVIEW_REQUIRED",
    }


# --------------------------------------------------------------------------- #
# normalize_venue_order_status — the synonym-fold (the actual bug fix)         #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", ["LIVE", "live", " RESTING ", "OPEN", "ACCEPTED", "unmatched"])
def test_resting_synonyms_fold_to_LIVE(raw: str) -> None:
    assert normalize_venue_order_status(raw, ingress=VenueStatusIngress.REST) is VenueOrderStatus.LIVE


@pytest.mark.parametrize("raw", ["PARTIAL", "PARTIALLY_MATCHED", "PARTIALLY_FILLED"])
def test_partial_synonyms_fold_to_PARTIALLY_MATCHED(raw: str) -> None:
    assert normalize_venue_order_status(raw, ingress=VenueStatusIngress.WS) is VenueOrderStatus.PARTIALLY_MATCHED


@pytest.mark.parametrize("raw", ["CANCELLED", "CANCELED", "CANCEL_CONFIRMED"])
def test_cancel_synonyms_fold_to_CANCEL_CONFIRMED(raw: str) -> None:
    # Committed: persisted canonical stays CANCEL_CONFIRMED (no rename to CANCELLED).
    assert normalize_venue_order_status(raw, ingress=VenueStatusIngress.WS) is VenueOrderStatus.CANCEL_CONFIRMED


def test_MATCHED_folds_to_MATCHED() -> None:
    assert normalize_venue_order_status("MATCHED", ingress=VenueStatusIngress.REST) is VenueOrderStatus.MATCHED


def test_FILLED_with_zero_remainder_is_MATCHED() -> None:
    from decimal import Decimal
    assert (
        normalize_venue_order_status("FILLED", ingress=VenueStatusIngress.REST, remaining_size=Decimal("0"))
        is VenueOrderStatus.MATCHED
    )


def test_FILLED_with_unknown_remainder_raises_ambiguous() -> None:
    with pytest.raises(ValueError):
        normalize_venue_order_status("FILLED", ingress=VenueStatusIngress.REST)


@pytest.mark.parametrize("raw,expected", [("EXPIRED", VenueOrderStatus.EXPIRED), ("VENUE_WIPED", VenueOrderStatus.VENUE_WIPED)])
def test_terminal_passthrough(raw: str, expected: VenueOrderStatus) -> None:
    assert normalize_venue_order_status(raw, ingress=VenueStatusIngress.DB) is expected


@pytest.mark.parametrize("raw", [None, "", "NONSENSE_STATUS"])
def test_unmapped_order_status_raises(raw) -> None:
    with pytest.raises(ValueError):
        normalize_venue_order_status(raw, ingress=VenueStatusIngress.REST)


# --------------------------------------------------------------------------- #
# normalize_venue_trade_status — trade chain only, FILLED forbidden            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", ["MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED", "confirmed"])
def test_trade_status_passthrough(raw: str) -> None:
    assert normalize_venue_trade_status(raw) is VenueTradeStatus[raw.strip().upper()]


def test_trade_status_rejects_FILLED() -> None:
    # FILLED is order-level only unless caller supplies explicit trade-finality source.
    with pytest.raises(ValueError):
        normalize_venue_trade_status("FILLED")


# --------------------------------------------------------------------------- #
# normalize_command_truth_state — fold redundant rejects/unknowns              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", ["REJECTED", "SUBMIT_REJECTED"])
def test_reject_synonyms_fold_to_REJECTED(raw: str) -> None:
    assert normalize_command_truth_state(raw) is CommandTruthState.REJECTED


@pytest.mark.parametrize("raw", ["UNKNOWN", "SUBMIT_UNKNOWN_SIDE_EFFECT"])
def test_unknown_synonyms_fold_to_UNKNOWN_SIDE_EFFECT(raw: str) -> None:
    assert normalize_command_truth_state(raw) is CommandTruthState.UNKNOWN_SIDE_EFFECT


@pytest.mark.parametrize("raw", ["FILLED", "CANCELLED", "EXPIRED"])
def test_legacy_venue_outcomes_are_not_command_truth(raw: str) -> None:
    # These are venue/order projections persisted on venue_commands.state for
    # compatibility; they must NOT be read as command-side truth (use
    # project_legacy_command_display()). The normalizer refuses them.
    with pytest.raises(ValueError):
        normalize_command_truth_state(raw)


def test_command_truth_state_committed_membership() -> None:
    assert {s.value for s in CommandTruthState} == {
        "INTENT_CREATED", "SNAPSHOT_BOUND", "SUBMITTING", "SIGNED_PERSISTED",
        "POSTING", "POST_ACKED", "ACKED", "CANCEL_PENDING",
        "REJECTED", "UNKNOWN_SIDE_EFFECT", "REVIEW_REQUIRED",
    }


@pytest.mark.parametrize("raw", ["INTENT_CREATED", "ACKED", "CANCEL_PENDING", "REVIEW_REQUIRED", "posting"])
def test_command_truth_passthrough_for_local_states(raw: str) -> None:
    assert normalize_command_truth_state(raw) is CommandTruthState[raw.strip().upper()]
