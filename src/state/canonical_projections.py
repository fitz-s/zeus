# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   §1 reducer contract / §4 read-only-gate pilot (consult round-2).

"""Pure projection functions over canonical truth — A4 exposure classification.

These centralize the lot-state -> exposure classification so consumers
(risk_allocator/governor.py, strategy/family_exclusive_dedup.py) branch on ONE
typed predicate set instead of each re-encoding local frozensets (the value-vs-
type coupling that drifts). Pure functions, no I/O, no DB.

Migration note: live position_lots.state uses only OPTIMISTIC_EXPOSURE and
CONFIRMED_EXPOSURE (canonical ExposureState). The other legacy values
(EXIT_PENDING, ECONOMICALLY_CLOSED_*, SETTLED, QUARANTINED) are 0-row in live DB
and are removed in the lot-state-narrowing migration step; they are recognized
here ONLY to keep this pilot behavior-identical to governor's current logic.
"""

from __future__ import annotations

from decimal import Decimal

from src.contracts.canonical_lifecycle import (
    ExitProgress,
    ExposureState,
    LegacyOrderResultStatus,
    OrderProofClass,
    PositionPhase,
    VenueOrderStatus,
)
from src.contracts.position_truth import CausalityStatus, FillAuthority, RecoveryAuthority

# A2 — "open order fact" classification, unified single source for the consumers
# that each re-encoded {LIVE, RESTING, PARTIALLY_MATCHED} (order_truth_reducer,
# maker_rest_escalation, venue_command_repo, event_reactor_adapter). LIVE +
# PARTIALLY_MATCHED are canonical VenueOrderStatus; "RESTING" is a raw venue
# synonym for LIVE retained until the ingress normalizer is wired in.
OPEN_ORDER_FACT_STATES = frozenset({
    VenueOrderStatus.LIVE.value,
    VenueOrderStatus.PARTIALLY_MATCHED.value,
    "RESTING",
})


def is_open_order_fact(state: str) -> bool:
    """True iff a venue order-fact state means the order/remainder is open on book."""
    return state in OPEN_ORDER_FACT_STATES


# --------------------------------------------------------------------------- #
# Proof-class coercion — the INV-CL-1 boundary for projection inputs           #
# --------------------------------------------------------------------------- #

def coerce_order_proof_class(value: OrderProofClass | str | None) -> OrderProofClass | None:
    """Coerce a proof-class input to the typed enum before projection branching.

    The reducer now emits ``OrderProofClass``, but future wiring may hand a
    persisted raw proof string. The projections branch with identity (``is``)
    checks, which are NOT raw-string compatible (StrEnum ``==``/membership works,
    ``is`` does not). Coercing at projection entry keeps every downstream ``is``
    check correct. ``None`` passes through; an unmapped string raises (fail loud).
    """
    if value is None:
        return None
    if isinstance(value, OrderProofClass):
        return value
    return OrderProofClass(str(value))


# --------------------------------------------------------------------------- #
# A6 — ExitProgress derived purely from the sell command's order truth         #
# --------------------------------------------------------------------------- #

def derive_exit_progress(
    *,
    has_exit_command: bool,
    has_venue_fact: bool,
    order_proof_class: OrderProofClass | str | None,
    retry_pending: bool = False,
    backoff_exhausted: bool = False,
) -> ExitProgress:
    """Pure projection of the exit/sell progression — never a stored source.

    The sell command's order-truth proof class is AUTHORITATIVE: it already
    encodes whether a venue fact exists. UNKNOWN_SIDE_EFFECT / REVIEW_REQUIRED
    mean "submitted, result not cleanly known" — there is deliberately NO clean
    venue fact, so they must be honored WITHOUT a ``has_venue_fact`` gate (the
    prior gated form mis-projected them as EXIT_INTENT, the exact condition the
    review state exists for). ``has_venue_fact`` is retained for call-site
    ergonomics but is not the classifier.

    Precedence: a confirmed sell fill is terminal and wins; otherwise an explicit
    backoff/retry event; otherwise classify the unknown/review/partial/resting
    sell order from its proof class; otherwise the intent has not reached a known
    venue state.
    """
    if not has_exit_command:
        return ExitProgress.NONE
    pc = coerce_order_proof_class(order_proof_class)
    # Terminal sell fill wins — the position economics are closed.
    if pc in (OrderProofClass.TERMINAL_FILLED, OrderProofClass.TERMINAL_PARTIAL):
        return ExitProgress.SELL_FILLED
    if backoff_exhausted:
        return ExitProgress.BACKOFF_EXHAUSTED
    if retry_pending:
        return ExitProgress.RETRY_PENDING
    if pc in (OrderProofClass.UNKNOWN_SIDE_EFFECT, OrderProofClass.REVIEW_REQUIRED):
        return ExitProgress.REVIEW_REQUIRED
    if pc is OrderProofClass.PARTIAL_WITH_REMAINDER:
        return ExitProgress.SELL_PARTIALLY_FILLED
    if pc is OrderProofClass.LIVE_RESTING:
        return ExitProgress.SELL_OPEN
    return ExitProgress.EXIT_INTENT


# --------------------------------------------------------------------------- #
# A1 — OrderResult.status derived from command + order truth                    #
# --------------------------------------------------------------------------- #

def derive_order_result_status(
    *,
    command_rejected: bool,
    order_proof_class: OrderProofClass | str | None,
    matched_size: Decimal | int = 0,
) -> LegacyOrderResultStatus:
    """Coarse executor-return status from command + order truth. Fill size and
    proof class carry the partial-vs-full detail; 'partial' is NOT a status.

    Conflict invariant: a no-side-effect reject (``command_rejected``) cannot
    coexist with a positive venue fill proof — venue truth would be silently
    lost. That contradiction is raised loudly (fail loud) so executor wiring
    surfaces inconsistent historical data instead of mis-reporting REJECTED."""
    pc = coerce_order_proof_class(order_proof_class)
    if (
        command_rejected
        and pc in (OrderProofClass.TERMINAL_FILLED, OrderProofClass.TERMINAL_PARTIAL)
        and matched_size > 0
    ):
        raise ValueError(
            "invalid order truth: command_rejected with positive venue fill proof "
            f"(proof_class={pc}, matched_size={matched_size})"
        )
    if command_rejected:
        return LegacyOrderResultStatus.REJECTED
    if pc is OrderProofClass.TERMINAL_FILLED:
        return LegacyOrderResultStatus.FILLED
    if pc is OrderProofClass.TERMINAL_PARTIAL and matched_size > 0:
        return LegacyOrderResultStatus.FILLED
    if pc in (
        OrderProofClass.LIVE_RESTING,
        OrderProofClass.PARTIAL_WITH_REMAINDER,
    ):
        return LegacyOrderResultStatus.PENDING
    if pc is OrderProofClass.TERMINAL_NO_FILL:
        return LegacyOrderResultStatus.REJECTED
    if pc in (
        OrderProofClass.UNKNOWN_SIDE_EFFECT,
        OrderProofClass.REVIEW_REQUIRED,
    ):
        return LegacyOrderResultStatus.UNKNOWN_SIDE_EFFECT
    return LegacyOrderResultStatus.UNKNOWN_SIDE_EFFECT


# --------------------------------------------------------------------------- #
# A5 — PositionPhase derived (10-rule monotonic precedence over truth facts)   #
# --------------------------------------------------------------------------- #

def derive_position_phase(
    *,
    has_admin_close: bool = False,
    has_settlement: bool = False,
    is_voided: bool = False,
    is_quarantined: bool = False,
    has_economic_close: bool = False,
    has_open_exit: bool = False,
    has_positive_exposure: bool = False,
    in_day0_window: bool = False,
    has_entry_intent: bool = False,
) -> PositionPhase:
    """Derive the coarse position phase from decision-relevant truth facts.

    The booleans are themselves projections of the underlying facts (admin/manual
    override events; settlement/P&L recorded; entry voided/terminal-no-fill/phantom-
    zero; chain-only or review-bucket chain visibility; exit terminal fill; any open
    exit order; positive exposure from active lot / chain-observed / entry fill; day0
    window; local entry intent). Precedence is monotonic terminal-first so the phase
    never regresses. `has_admin_close` is an EXPLICIT operator terminal override and
    intentionally outranks settlement — it is not ordinary post-settlement
    bookkeeping; if a report consumer needs economic resolution to dominate the
    operator phase, split that out rather than re-ranking here. The fact->boolean
    computation is wired at the call sites (lifecycle_manager / projection) in the
    A5 cutover; this is the pure decision."""
    if has_admin_close:
        return PositionPhase.ADMIN_CLOSED
    if has_settlement:
        return PositionPhase.SETTLED
    if is_voided:
        return PositionPhase.VOIDED
    if is_quarantined:
        return PositionPhase.QUARANTINED
    if has_economic_close:
        return PositionPhase.ECONOMICALLY_CLOSED
    if has_open_exit:
        return PositionPhase.PENDING_EXIT
    if has_positive_exposure and in_day0_window:
        return PositionPhase.DAY0_WINDOW
    if has_positive_exposure:
        return PositionPhase.ACTIVE
    if has_entry_intent:
        return PositionPhase.PENDING_ENTRY
    return PositionPhase.UNKNOWN


# --------------------------------------------------------------------------- #
# A4 authority — RecoveryAuthority as a DERIVED facet (not a 3rd stored enum)  #
# --------------------------------------------------------------------------- #

_TRADE_VERIFIED_FILL_AUTHORITIES = frozenset({
    FillAuthority.VENUE_CONFIRMED_PARTIAL,
    FillAuthority.VENUE_CONFIRMED_FULL,
    FillAuthority.CANCELLED_REMAINDER,
    FillAuthority.SETTLED,
})


def derive_recovery_class(
    *,
    fill_authority: FillAuthority,
    causality_status: CausalityStatus,
) -> RecoveryAuthority:
    """Recovery strength derived from the two surviving authority facets.

    TRADE_VERIFIED only when an exact venue trade fact links the fill economics
    (fill_authority is venue-confirmed/cancelled-remainder/settled) AND causality
    is OK (eligible for training/P&L). Otherwise BALANCE_ONLY — tradable exposure
    (incl. the load-bearing shared-wallet VENUE_POSITION_OBSERVED) but never
    fill-verified / training-eligible."""
    if fill_authority in _TRADE_VERIFIED_FILL_AUTHORITIES and causality_status is CausalityStatus.OK:
        return RecoveryAuthority.TRADE_VERIFIED
    return RecoveryAuthority.BALANCE_ONLY

# Behavior-identical to governor's current _ACTIVE_EXPOSURE_STATES /
# _CLOSED_EXPOSURE_STATES (preserved exactly for the equivalence pilot).
_ACTIVE_EXPOSURE_LOT_STATES = frozenset({
    ExposureState.OPTIMISTIC_EXPOSURE.value,
    ExposureState.CONFIRMED_EXPOSURE.value,
    "EXIT_PENDING",  # legacy/transitional — 0-row live, removed in lot-narrowing step
})
_CLOSED_EXPOSURE_LOT_STATES = frozenset({
    "ECONOMICALLY_CLOSED_OPTIMISTIC",
    "ECONOMICALLY_CLOSED_CONFIRMED",
    "SETTLED",
})  # all legacy/transitional — 0-row live


def is_optimistic_exposure(lot_state: str) -> bool:
    """True for the optimistic (risk-weighted) exposure lot state."""
    return lot_state == ExposureState.OPTIMISTIC_EXPOSURE


def counts_as_active_exposure(lot_state: str) -> bool:
    """True iff the lot contributes live exposure (not closed, not review)."""
    return lot_state in _ACTIVE_EXPOSURE_LOT_STATES


def is_closed_exposure(lot_state: str) -> bool:
    """True iff the lot is economically closed / settled (skipped for exposure)."""
    return lot_state in _CLOSED_EXPOSURE_LOT_STATES


def weighted_lot_exposure_micro(
    lot_state: str,
    exposure_micro: int,
    optimistic_weight: float,
) -> int:
    """Capacity-weighted exposure of a single lot, in micro-USD.

    Behavior-identical to governor._weighted_lot_exposure: optimistic lots are
    risk-weighted by ``optimistic_weight``; other active lots count at full
    notional; closed / quarantined / unknown lots contribute zero.
    """
    if is_optimistic_exposure(lot_state):
        return int(round(exposure_micro * optimistic_weight))
    if counts_as_active_exposure(lot_state):
        return int(exposure_micro)
    return 0
