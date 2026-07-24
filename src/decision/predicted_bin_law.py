# Lifecycle: created=2026-07-23; last_reviewed=2026-07-23; last_reused=never
# Purpose: The single predicted-bin decision law — entry admissibility and
#   optimal-stopping exit — as pure Decimal functions. One law, one identity,
#   one stop, shared by the reactor (entry) and the exit monitor (later commit
#   groups). Encodes the operator axiom "buy our predicted bin, sell before the
#   probability reverses or hold to settlement" with no per-strategy branching.
# Reuse: import the specific function; never fork the arithmetic. Callers supply
#   the depth-walked all-in cost (entry) and the bid-depth net-proceeds
#   breakpoints (exit); this module never walks a book and never reads a DB.
#   PURE: no I/O, no engine/executor/portfolio imports, no float anywhere.
# Authority basis: docs/operations/current/plans/ultimate_alpha_2026-07-23/
#   COLLISION.md  — C3: PR-1 exit is the ΔJ≡0 special case, SELL_REVERSAL only,
#                    no allocator term (that couples in PR-2). Wins on conflict.
#   FINAL_SPEC.md — §入场律 (G⁻(x) = x·q⁻ − C⁺ − M_e; ENTER ⟺ positive robust EV),
#                    §离场律 (SELL ⟺ max_x[(h−x)q⁻ + L(x)] − M_x > h·q⁻), native
#                    NO bound q⁻_NO = 1−q⁺_YES, lock folding, hysteresis band.
#   DERIVATION.md — operator axiom 2026-07-23; entry price is sunk information.
"""Pure predicted-bin decision law: entry, exit, native NO bounds, hysteresis.

Design invariants (the shape carries the law):

* **Decimal only** — every value is a :class:`~decimal.Decimal`; there is no
  float literal in this module (a structural test enforces it). Currency and
  probability arithmetic must be exact.
* **No sunk cost** — the entry price / cost basis appears in NO signature. Both
  the entry gate and the exit stop are evaluated against forward robust value
  only. Depth-walked forward costs (``all_in_cost``, the bid breakpoints) are
  the caller's inputs; the paid price is never one of them.
* **Native NO bounds** — the NO lower bound is ``1 − q⁺_YES`` (widest YES upper
  maps to tightest NO lower), NEVER ``1 − q⁻_YES``. That flip is a spec-mandated
  trap: using ``1 − q⁻_YES`` would silently overstate the NO robust EV.
* **PR-1 exit (ΔJ≡0)** — the stop compares clean liquidation value to robust
  hold value with no joint-allocator shadow term; the single audit code is
  ``SELL_REVERSAL``. PR-2 injects ΔJ and opens ``SELL_REALLOCATE`` without
  touching this law's PR-1 branch.
* **Lock is physical authority** — an IMPOSSIBLE/GUARANTEED lock folds the
  bounds to (0,0,0)/(1,1,1) and decides regardless of evidence freshness.
  RiskGuard RED preempts everything and is phase-blind (no Day0 exemption).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

_ZERO = Decimal(0)
_ONE = Decimal(1)


class LockState(Enum):
    """Settlement-preimage lock derived by the caller from Day0 extrema/geometry.

    NONE — reversal physically possible; the value comparison decides.
    IMPOSSIBLE — the bin can no longer settle in our favor (folds bounds to 0).
    GUARANTEED — the bin is guaranteed to settle in our favor (folds bounds to 1).
    """

    NONE = "none"
    IMPOSSIBLE = "impossible"
    GUARANTEED = "guaranteed"


class ExitAction(Enum):
    """The four terminal exit verdicts. PR-1 emits only these; PR-2 adds
    SELL_REALLOCATE once the joint allocator's ΔJ term exists."""

    HOLD = "hold"
    SELL_REVERSAL = "sell_reversal"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    RED_FORCE_EXIT = "red_force_exit"


@dataclass(frozen=True)
class NativeBounds:
    """Native-side current-evidence probability bounds: robust lower ``q_lcb``,
    point ``q``, robust upper ``q_ucb``. "Native" = expressed on the side being
    priced (YES or NO), never derived by naively complementing the other side's
    same-name bound."""

    q_lcb: Decimal
    q: Decimal
    q_ucb: Decimal


@dataclass(frozen=True)
class ExitDecision:
    """Immutable exit verdict with its proof terms.

    ``value_kept`` = robust value of the shares retained ((h−x)·q⁻).
    ``value_sold`` = net liquidation proceeds L(x) realized on the sold shares.
    Their sum minus the flat exit margin is the sell-branch value that beat the
    hold value h·q⁻ when the action is SELL_REVERSAL."""

    action: ExitAction
    shares_to_sell: Decimal
    value_kept: Decimal
    value_sold: Decimal


def native_no_bounds(yes: NativeBounds) -> NativeBounds:
    """Map YES-side native bounds to NO-side native bounds.

    q⁻_NO = 1 − q⁺_YES,  q_NO = 1 − q_YES,  q⁺_NO = 1 − q⁻_YES.

    The lower NO bound comes from the UPPER YES bound. Using ``1 − q⁻_YES`` for
    the NO lower bound is the trap the spec forbids — it would make the NO robust
    EV look better than the evidence supports."""
    return NativeBounds(
        q_lcb=_ONE - yes.q_ucb,
        q=_ONE - yes.q,
        q_ucb=_ONE - yes.q_lcb,
    )


def apply_lock(bounds: NativeBounds, lock: LockState) -> NativeBounds:
    """Fold a settlement-preimage lock into the bounds.

    IMPOSSIBLE collapses to (0,0,0), GUARANTEED to (1,1,1); NONE is identity.
    A locked outcome is physical certainty, so all three bounds degenerate to
    the same value."""
    if lock is LockState.IMPOSSIBLE:
        return NativeBounds(_ZERO, _ZERO, _ZERO)
    if lock is LockState.GUARANTEED:
        return NativeBounds(_ONE, _ONE, _ONE)
    return bounds


def entry_value(
    shares: Decimal,
    q_lcb: Decimal,
    all_in_cost: Decimal,
    entry_margin: Decimal,
) -> Decimal:
    """Robust entry value G⁻ = shares·q⁻ − C⁺ − M_e.

    ``all_in_cost`` is the caller's depth-walked ask cost for ``shares`` shares,
    already including fees and cost uncertainty (this module walks no book).
    ``entry_margin`` is the global friction margin M_e (1 tick equivalent),
    scaled to the position by the caller. The entry price paid earlier is not an
    input — entry is judged only on forward robust value."""
    return shares * q_lcb - all_in_cost - entry_margin


def admissible(
    shares: Decimal,
    q_lcb: Decimal,
    all_in_cost: Decimal,
    entry_margin: Decimal,
) -> bool:
    """Entry is admissible iff robust value is strictly positive (G⁻ > 0).

    Strict: an exact break-even (G⁻ = 0) is NOT admissible. Positive robust EV
    is necessary; the joint-Kelly size x* > 0 (PR-2) is the sufficiency term."""
    return entry_value(shares, q_lcb, all_in_cost, entry_margin) > _ZERO


def _locked_q_lcb(q_lcb: Decimal, lock: LockState) -> Decimal:
    """Fold the lock into the scalar robust lower bound, reusing apply_lock so
    the lock semantics have exactly one definition."""
    return apply_lock(NativeBounds(q_lcb, q_lcb, q_lcb), lock).q_lcb


def _deepest_breakpoint(
    bid_breakpoints: Sequence[tuple[Decimal, Decimal]],
) -> tuple[Decimal, Decimal]:
    """Return the (shares, proceeds) breakpoint with the most sellable shares,
    or (0, 0) when the book is empty. Used for the RED force-exit proof terms."""
    deepest = (_ZERO, _ZERO)
    for shares, proceeds in bid_breakpoints:
        if shares > deepest[0]:
            deepest = (shares, proceeds)
    return deepest


def exit_decision(
    held_shares: Decimal,
    q_lcb: Decimal,
    bid_breakpoints: Sequence[tuple[Decimal, Decimal]],
    exit_margin: Decimal,
    lock: LockState,
    evidence_ok: bool,
    riskguard_red: bool,
) -> ExitDecision:
    """The unified PR-1 optimal-stopping exit law (ΔJ≡0 special case).

    ``bid_breakpoints`` is the caller-supplied bid-depth curve as a sequence of
    ``(shares_sold, net_liquidation_proceeds)`` points — L(x) already net of
    fees and exit uncertainty. The stop is evaluated ONLY at these breakpoints
    (partial exits fall out of the argmax for free); the module never
    interpolates or walks a book.

    Decision, in strict precedence:

    1. ``riskguard_red`` → RED_FORCE_EXIT. Phase-blind emergency exit; preempts
       lock and evidence (there is no Day0 exemption).
    2. evidence not ok AND lock is NONE → EVIDENCE_UNAVAILABLE (hold, cannot
       evaluate). A GUARANTEED/IMPOSSIBLE lock is independent physical authority
       and still decides even with stale/absent evidence.
    3. Otherwise compare, over the breakpoints, the sell-branch value
       ``max_x[(h−x)·q⁻ + L(x)] − M_x`` against the hold value ``h·q⁻`` using the
       lock-folded q⁻. SELL_REVERSAL at the argmax x iff it strictly wins;
       else HOLD. Equivalently SELL iff ``L(x) > x·q⁻ + M_x``.
    """
    q = _locked_q_lcb(q_lcb, lock)
    hold_value = held_shares * q

    if riskguard_red:
        _, proceeds = _deepest_breakpoint(bid_breakpoints)
        return ExitDecision(
            action=ExitAction.RED_FORCE_EXIT,
            shares_to_sell=held_shares,
            value_kept=_ZERO,
            value_sold=proceeds,
        )

    if not evidence_ok and lock is LockState.NONE:
        return ExitDecision(
            action=ExitAction.EVIDENCE_UNAVAILABLE,
            shares_to_sell=_ZERO,
            value_kept=hold_value,
            value_sold=_ZERO,
        )

    # Ascending by shares so ties resolve to the smallest sufficient sell.
    best_x = _ZERO
    best_proceeds = _ZERO
    best_value = hold_value  # the HOLD baseline
    for shares, proceeds in sorted(bid_breakpoints, key=lambda bp: bp[0]):
        candidate = (held_shares - shares) * q + proceeds - exit_margin
        if candidate > best_value:
            best_value = candidate
            best_x = shares
            best_proceeds = proceeds

    if best_x > _ZERO:
        return ExitDecision(
            action=ExitAction.SELL_REVERSAL,
            shares_to_sell=best_x,
            value_kept=(held_shares - best_x) * q,
            value_sold=best_proceeds,
        )
    return ExitDecision(
        action=ExitAction.HOLD,
        shares_to_sell=_ZERO,
        value_kept=hold_value,
        value_sold=_ZERO,
    )


def hysteresis_band(
    net_bid: Decimal,
    all_in_ask: Decimal,
    entry_margin: Decimal,
    exit_margin: Decimal,
    recycle_return: Decimal = _ZERO,
) -> tuple[Decimal, Decimal]:
    """The no-op hold band ``(lower, upper)`` in q⁻ space.

    lower = b⁻·(1 + r) − m_x,  upper = a⁺ + m_e.

    Entry fires when q⁻ > upper (see :func:`admissible` with all-in cost a⁺);
    exit fires when q⁻ < lower (see :func:`exit_decision`); a q⁻ strictly inside
    the band is neither enterable nor exitable, so the same (q, book) state can
    never trigger both a buy and a sell. The band is non-empty (upper > lower)
    exactly when ``(a⁺ − b⁻) + m_e + m_x > b⁻·r`` — i.e. spread plus margins
    exceed the recycle hurdle applied to the bid."""
    lower = net_bid * (_ONE + recycle_return) - exit_margin
    upper = all_in_ask + entry_margin
    return (lower, upper)


def recycle_hurdle(net_bid: Decimal) -> Decimal:
    """The locked-winner recycling threshold r* = 1/b⁻ − 1.

    A GUARANTEED winner (q⁻ = 1) is worth recycling into a new claim only when
    the causal opportunity-cost return r_t exceeds this hurdle (bid .985 → 1.52%,
    bid .995 → 0.50%). Consumed in PR-2; defined here so the law is complete."""
    return _ONE / net_bid - _ONE
