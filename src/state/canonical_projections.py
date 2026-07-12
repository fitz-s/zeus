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
(EXIT_PENDING, ECONOMICALLY_CLOSED_*, SETTLED) are 0-row in live DB and are
removed in the lot-state-narrowing migration step; they are recognized here
ONLY to keep this pilot behavior-identical to governor's current logic. (T5,
docs/rebuild/quarantine_excision_2026-07-11.md: QUARANTINED retired — no
writer mints it; rollback_optimistic_lot_for_failed_trade now appends
ECONOMICALLY_CLOSED_OPTIMISTIC.)
"""

from __future__ import annotations

from dataclasses import dataclass
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
# A5 — PositionPhase derived (authority-aware precedence over truth facts)      #
# --------------------------------------------------------------------------- #

def _derive_phase_and_authority(
    *,
    has_admin_close: bool,
    is_voided: bool,
    has_settlement: bool,
    has_economic_close: bool,
    has_explicit_pending_exit: bool,
    has_exit_fallback: bool,
    has_positive_exposure: bool,
    in_day0_window: bool,
    has_entry_intent: bool,
) -> tuple[PositionPhase, str]:
    """The authority-aware A5 precedence (consult 6a42bc3d ruling). Returns the phase
    and the authority tier that decided it. EXPLICIT A5-owned state/event facts win;
    exit-state is a FALLBACK ranked below economic-close / explicit-pending-exit, so
    an economically_closed / pending_exit position keeps its phase.

    T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW): there
    is no quarantine phase target — a confirmed-fill/chain-absence dispute keeps its
    TRUE phase (reached via has_positive_exposure / has_exit_fallback below) and the
    dispute lives in a ReviewWorkItem, never in the phase itself (see
    project_position_phase's has_open_review_fact overlay for the review-visibility
    signal, which no longer participates in phase precedence at all)."""
    if has_admin_close:
        return PositionPhase.ADMIN_CLOSED, "primary_state"
    if is_voided:
        return PositionPhase.VOIDED, "primary_state"
    if has_settlement:
        return PositionPhase.SETTLED, "primary_state"
    if has_economic_close:
        return PositionPhase.ECONOMICALLY_CLOSED, "primary_state"
    if has_explicit_pending_exit:
        return PositionPhase.PENDING_EXIT, "primary_state"
    if has_exit_fallback:
        return PositionPhase.PENDING_EXIT, "exit_fallback"
    if has_positive_exposure and in_day0_window:
        return PositionPhase.DAY0_WINDOW, "exposure_fallback"
    if has_positive_exposure:
        return PositionPhase.ACTIVE, "exposure_fallback"
    if has_entry_intent:
        return PositionPhase.PENDING_ENTRY, "primary_state"
    return PositionPhase.UNKNOWN, "unknown"


def derive_position_phase(
    *,
    # A5-owned explicit lifecycle facts / trusted primary state (authority tier).
    has_admin_close: bool = False,
    is_voided: bool = False,
    has_settlement: bool = False,
    has_economic_close: bool = False,
    has_explicit_pending_exit: bool = False,
    # A6 exit FALLBACK fact (project only when no A5 authority is above).
    has_exit_fallback: bool = False,
    # Exposure / intent facts.
    has_positive_exposure: bool = False,
    in_day0_window: bool = False,
    has_entry_intent: bool = False,
    strict_terminal_conflict: bool = False,
) -> PositionPhase:
    """Derive the coarse position phase from decision-relevant truth facts.

    Authority-aware precedence (consult 6a42bc3d): explicit A5 state/event facts
    (admin / void / settlement / economic-close / explicit-pending-exit) win;
    exit-state is a FALLBACK ranked below economic-close and explicit-pending-exit,
    so a closed/exiting position keeps its phase. This makes the function an exact
    decomposition of the live owner phase_for_runtime_position over the runtime
    domain.

    `has_admin_close` is an EXPLICIT operator terminal override and outranks settlement.
    The default defensive ordering prefers VOIDED over SETTLED (a wrongly-settled
    phantom is worse than a voided row in review). For INDEPENDENT reconstruction where
    mutually-exclusive terminal facts may conflict (the market-resolved-vs-position-
    settled bug class), pass strict_terminal_conflict=True to fail loud instead of
    silently ranking. No writer is wired to this yet; it is the pure decision."""
    if strict_terminal_conflict:
        n_terminal = sum((has_admin_close, is_voided, has_settlement, has_economic_close))
        if n_terminal > 1:
            raise ValueError(
                "conflicting terminal/economic facts in independent reconstruction "
                f"(admin_close={has_admin_close}, voided={is_voided}, "
                f"settlement={has_settlement}, economic_close={has_economic_close}) — "
                "resolve via the trusted primary state / fold event stream, do not rank"
            )
    phase, _authority = _derive_phase_and_authority(
        has_admin_close=has_admin_close,
        is_voided=is_voided,
        has_settlement=has_settlement,
        has_economic_close=has_economic_close,
        has_explicit_pending_exit=has_explicit_pending_exit,
        has_exit_fallback=has_exit_fallback,
        has_positive_exposure=has_positive_exposure,
        in_day0_window=in_day0_window,
        has_entry_intent=has_entry_intent,
    )
    return phase


@dataclass(frozen=True)
class PositionPhaseProjection:
    """A5 phase plus an A7 review-visibility overlay (consult 6a42bc3d [S2]).

    T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW):
    chain_review_required is now driven by has_open_review_fact — an orthogonal
    fact (e.g. an open ReviewWorkItem) that NEVER overrides the phase itself,
    keeping a real chain/local discrepancy visible to operator/reconcile
    reports instead of silently dropped, without stranding the position in a
    quarantine scar phase."""

    phase: PositionPhase
    chain_review_required: bool
    chain_review_reason: str | None
    phase_authority: str


def project_position_phase(
    *,
    has_admin_close: bool = False,
    is_voided: bool = False,
    has_settlement: bool = False,
    has_economic_close: bool = False,
    has_explicit_pending_exit: bool = False,
    has_exit_fallback: bool = False,
    has_positive_exposure: bool = False,
    in_day0_window: bool = False,
    has_entry_intent: bool = False,
    has_open_review_fact: bool = False,
    chain_review_reason: str | None = None,
) -> PositionPhaseProjection:
    """Project the A5 phase together with the A7 review-visibility overlay. The
    phase is derive_position_phase(...); chain_review_required is True whenever
    the caller reports an open review fact (e.g. an open ReviewWorkItem) —
    purely informational, never a phase input."""
    phase, authority = _derive_phase_and_authority(
        has_admin_close=has_admin_close,
        is_voided=is_voided,
        has_settlement=has_settlement,
        has_economic_close=has_economic_close,
        has_explicit_pending_exit=has_explicit_pending_exit,
        has_exit_fallback=has_exit_fallback,
        has_positive_exposure=has_positive_exposure,
        in_day0_window=in_day0_window,
        has_entry_intent=has_entry_intent,
    )
    return PositionPhaseProjection(
        phase=phase,
        chain_review_required=bool(has_open_review_fact),
        chain_review_reason=chain_review_reason if has_open_review_fact else None,
        phase_authority=authority,
    )


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
