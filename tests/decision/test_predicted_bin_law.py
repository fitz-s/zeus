# Lifecycle: created=2026-07-23; last_reviewed=2026-07-23; last_reused=never
# Purpose: Exhaustive money-path unit coverage for src/decision/predicted_bin_law.py
#   — the single predicted-bin entry/exit law. Antibodies for every spec-mandated
#   trap: the native NO lower-bound flip, lock folding, strict entry break-even,
#   the exit precedence chain (RED > lock > evidence > value), partial-exit argmax,
#   hysteresis non-overlap, sunk-cost-free signatures, and Decimal purity.
# Authority basis: docs/operations/current/plans/ultimate_alpha_2026-07-23/
#   {COLLISION.md (C3), FINAL_SPEC.md (§入场律/§离场律), DERIVATION.md (axiom)}.
"""Every predicted-bin-law behavior the plan documents, as a falsifiable test."""
from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest

from src.decision import predicted_bin_law as law
from src.decision.predicted_bin_law import (
    ExitAction,
    ExitDecision,
    LockState,
    NativeBounds,
    admissible,
    apply_lock,
    entry_value,
    exit_decision,
    hysteresis_band,
    native_no_bounds,
    recycle_hurdle,
)


def D(x: str) -> Decimal:
    return Decimal(x)


# --------------------------------------------------------------------------- #
# native_no_bounds — the spec-mandated trap                                    #
# --------------------------------------------------------------------------- #

def test_native_no_lower_bound_uses_yes_upper_not_yes_lower():
    yes = NativeBounds(q_lcb=D("0.30"), q=D("0.40"), q_ucb=D("0.55"))
    no = native_no_bounds(yes)
    # q_lcb_NO must be 1 - q_ucb_YES.
    assert no.q_lcb == D("0.45")
    # The TRAP: 1 - q_lcb_YES = 0.70 would overstate the NO robust EV.
    assert no.q_lcb != (D("1") - yes.q_lcb)


def test_native_no_maps_all_three_bounds():
    yes = NativeBounds(q_lcb=D("0.30"), q=D("0.40"), q_ucb=D("0.55"))
    no = native_no_bounds(yes)
    assert (no.q_lcb, no.q, no.q_ucb) == (D("0.45"), D("0.60"), D("0.70"))


def test_native_no_preserves_bound_ordering():
    yes = NativeBounds(q_lcb=D("0.10"), q=D("0.42"), q_ucb=D("0.88"))
    no = native_no_bounds(yes)
    assert no.q_lcb <= no.q <= no.q_ucb


def test_native_no_is_its_own_involution():
    yes = NativeBounds(q_lcb=D("0.22"), q=D("0.37"), q_ucb=D("0.61"))
    assert native_no_bounds(native_no_bounds(yes)) == yes


# --------------------------------------------------------------------------- #
# apply_lock — folding                                                         #
# --------------------------------------------------------------------------- #

def test_apply_lock_impossible_collapses_to_zero():
    b = NativeBounds(D("0.4"), D("0.5"), D("0.6"))
    assert apply_lock(b, LockState.IMPOSSIBLE) == NativeBounds(D("0"), D("0"), D("0"))


def test_apply_lock_guaranteed_collapses_to_one():
    b = NativeBounds(D("0.4"), D("0.5"), D("0.6"))
    assert apply_lock(b, LockState.GUARANTEED) == NativeBounds(D("1"), D("1"), D("1"))


def test_apply_lock_none_is_identity():
    b = NativeBounds(D("0.4"), D("0.5"), D("0.6"))
    assert apply_lock(b, LockState.NONE) == b


# --------------------------------------------------------------------------- #
# entry_value / admissible — strict break-even, sunk-cost free                 #
# --------------------------------------------------------------------------- #

def test_entry_value_is_exact():
    # 100*0.90 - 85.00 - 0.01 = 4.99
    assert entry_value(D("100"), D("0.90"), D("85.00"), D("0.01")) == D("4.99")


def test_admissible_positive_ev():
    assert admissible(D("100"), D("0.90"), D("85.00"), D("0.01")) is True


def test_admissible_exact_zero_is_not_admissible():
    # 100*0.90 - 89.99 - 0.01 == 0 exactly -> strict > fails.
    assert entry_value(D("100"), D("0.90"), D("89.99"), D("0.01")) == D("0.00")
    assert admissible(D("100"), D("0.90"), D("89.99"), D("0.01")) is False


def test_admissible_negative_ev_rejected():
    assert admissible(D("100"), D("0.90"), D("90.50"), D("0.01")) is False


# --------------------------------------------------------------------------- #
# exit_decision — value comparison                                             #
# --------------------------------------------------------------------------- #

def test_exit_holds_when_bid_below_robust_lower():
    # q_lcb 0.90, best net bid 0.85 -> hold.
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.90"),
        bid_breakpoints=[(D("100"), D("85.00"))],
        exit_margin=D("0.01"),
        lock=LockState.NONE,
        evidence_ok=True,
        riskguard_red=False,
    )
    assert d.action is ExitAction.HOLD
    assert d.shares_to_sell == D("0")
    assert d.value_kept == D("90.00")
    assert d.value_sold == D("0")


def test_exit_sells_full_when_bid_dominates_hold():
    # q_lcb 0.50, net bid 0.80 -> clean liquidation dominates.
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.50"),
        bid_breakpoints=[(D("100"), D("80.00"))],
        exit_margin=D("0.01"),
        lock=LockState.NONE,
        evidence_ok=True,
        riskguard_red=False,
    )
    assert d.action is ExitAction.SELL_REVERSAL
    assert d.shares_to_sell == D("100")
    assert d.value_kept == D("0.00")
    assert d.value_sold == D("80.00")


def test_exit_partial_argmax_picks_interior_breakpoint_when_bid_decays():
    # Marginal per-share: 0..20 @0.97, 20..50 @0.92 (both > q_lcb 0.90),
    # 50..100 @0.80 (< q_lcb) -> optimal sells exactly 50, not 100.
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.90"),
        bid_breakpoints=[
            (D("20"), D("19.40")),
            (D("50"), D("47.00")),
            (D("100"), D("87.00")),
        ],
        exit_margin=D("0.01"),
        lock=LockState.NONE,
        evidence_ok=True,
        riskguard_red=False,
    )
    assert d.action is ExitAction.SELL_REVERSAL
    assert d.shares_to_sell == D("50")
    assert d.value_kept == D("45.00")  # (100-50)*0.90
    assert d.value_sold == D("47.00")


def test_exit_breakpoint_order_does_not_change_verdict():
    unordered = [
        (D("100"), D("87.00")),
        (D("20"), D("19.40")),
        (D("50"), D("47.00")),
    ]
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.90"),
        bid_breakpoints=unordered,
        exit_margin=D("0.01"),
        lock=LockState.NONE,
        evidence_ok=True,
        riskguard_red=False,
    )
    assert d.shares_to_sell == D("50")


def test_exit_no_bid_holds():
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.90"),
        bid_breakpoints=[],
        exit_margin=D("0.01"),
        lock=LockState.NONE,
        evidence_ok=True,
        riskguard_red=False,
    )
    assert d.action is ExitAction.HOLD
    assert d.shares_to_sell == D("0")


# --------------------------------------------------------------------------- #
# exit_decision — precedence chain: RED > lock > evidence > value              #
# --------------------------------------------------------------------------- #

def test_red_preempts_guaranteed_lock_and_good_evidence():
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.95"),
        bid_breakpoints=[(D("100"), D("50.00"))],
        exit_margin=D("0.01"),
        lock=LockState.GUARANTEED,
        evidence_ok=True,
        riskguard_red=True,
    )
    assert d.action is ExitAction.RED_FORCE_EXIT
    assert d.shares_to_sell == D("100")
    assert d.value_kept == D("0")
    assert d.value_sold == D("50.00")


def test_red_preempts_evidence_gate():
    # evidence not ok + lock NONE would be EVIDENCE_UNAVAILABLE, but RED wins.
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.90"),
        bid_breakpoints=[(D("100"), D("70.00"))],
        exit_margin=D("0.01"),
        lock=LockState.NONE,
        evidence_ok=False,
        riskguard_red=True,
    )
    assert d.action is ExitAction.RED_FORCE_EXIT
    assert d.shares_to_sell == D("100")


def test_evidence_unavailable_only_when_lock_none():
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.90"),
        bid_breakpoints=[(D("100"), D("95.00"))],
        exit_margin=D("0.01"),
        lock=LockState.NONE,
        evidence_ok=False,
        riskguard_red=False,
    )
    assert d.action is ExitAction.EVIDENCE_UNAVAILABLE
    assert d.shares_to_sell == D("0")


def test_guaranteed_lock_with_garbage_evidence_still_holds():
    # Lock is physical authority: folded q_lcb = 1, no bid can beat it -> HOLD,
    # and the stale-evidence gate does NOT fire because lock != NONE.
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.10"),  # garbage; overridden by GUARANTEED fold to 1
        bid_breakpoints=[(D("100"), D("99.50"))],
        exit_margin=D("0.01"),
        lock=LockState.GUARANTEED,
        evidence_ok=False,
        riskguard_red=False,
    )
    assert d.action is ExitAction.HOLD
    assert d.shares_to_sell == D("0")
    assert d.value_kept == D("100")  # 100 * folded 1


def test_impossible_lock_folds_qlcb_to_zero_and_sells_on_any_positive_bid():
    # Folded q_lcb = 0, tiny positive net proceeds beat the flat margin -> SELL,
    # even with stale evidence (lock authority bypasses the evidence gate).
    d = exit_decision(
        held_shares=D("100"),
        q_lcb=D("0.50"),  # ignored; IMPOSSIBLE folds to 0
        bid_breakpoints=[(D("100"), D("3.00"))],
        exit_margin=D("0.01"),
        lock=LockState.IMPOSSIBLE,
        evidence_ok=False,
        riskguard_red=False,
    )
    assert d.action is ExitAction.SELL_REVERSAL
    assert d.shares_to_sell == D("100")
    assert d.value_kept == D("0")  # (100-100)*0
    assert d.value_sold == D("3.00")


# --------------------------------------------------------------------------- #
# hysteresis — the band and non-overlap with entry/exit                        #
# --------------------------------------------------------------------------- #

def test_hysteresis_band_values():
    lower, upper = hysteresis_band(
        net_bid=D("0.85"), all_in_ask=D("0.88"),
        entry_margin=D("0.01"), exit_margin=D("0.01"),
    )
    assert lower == D("0.84")  # 0.85*1 - 0.01
    assert upper == D("0.89")  # 0.88 + 0.01


def test_hysteresis_recycle_return_lifts_lower_bound():
    base_lower, _ = hysteresis_band(D("0.90"), D("0.92"), D("0.01"), D("0.01"))
    recy_lower, _ = hysteresis_band(
        D("0.90"), D("0.92"), D("0.01"), D("0.01"), recycle_return=D("0.05")
    )
    assert recy_lower > base_lower
    # 0.90 * 1.05 - 0.01 = 0.935
    assert recy_lower == D("0.935")


def test_hysteresis_no_overlap_same_state_never_both_enter_and_exit():
    # A q_lcb strictly inside the band is neither admissible (entry) nor a SELL,
    # proving the two laws share one no-op region. Uses 1-share arithmetic so
    # entry all-in-cost == ask and exit proceeds == net bid.
    net_bid, ask, m = D("0.85"), D("0.88"), D("0.01")
    lower, upper = hysteresis_band(net_bid, ask, m, m)
    q_in_band = D("0.87")  # lower(0.84) < q < upper(0.89)
    assert lower < q_in_band < upper

    # Entry: q_lcb - ask - m_e = 0.87 - 0.88 - 0.01 < 0 -> not admissible.
    assert admissible(D("1"), q_in_band, ask, m) is False

    # Exit: net bid 0.85 vs q_lcb 0.87 + m_x -> HOLD.
    d = exit_decision(
        held_shares=D("1"),
        q_lcb=q_in_band,
        bid_breakpoints=[(D("1"), net_bid)],
        exit_margin=m,
        lock=LockState.NONE,
        evidence_ok=True,
        riskguard_red=False,
    )
    assert d.action is ExitAction.HOLD


def test_hysteresis_band_nonempty_when_spread_plus_margins_exceed_recycle():
    net_bid, ask, m = D("0.90"), D("0.92"), D("0.01")
    r = D("0.01")
    lower, upper = hysteresis_band(net_bid, ask, m, m, recycle_return=r)
    assert upper > lower  # (ask-bid) + 2m > bid*r  ->  0.02 + 0.02 > 0.009


# --------------------------------------------------------------------------- #
# recycle_hurdle                                                               #
# --------------------------------------------------------------------------- #

def test_recycle_hurdle_matches_spec_examples():
    h985 = recycle_hurdle(D("0.985"))
    h995 = recycle_hurdle(D("0.995"))
    # bid .985 -> 1.52%, bid .995 -> 0.50% (spec §离场律).
    assert h985.quantize(D("0.0001")) == D("0.0152")
    assert h995.quantize(D("0.0001")) == D("0.0050")


# --------------------------------------------------------------------------- #
# Structural invariants — sunk cost absent, Decimal purity                     #
# --------------------------------------------------------------------------- #

def test_no_sunk_entry_price_in_any_public_signature():
    forbidden = (
        "cost_basis", "entry_price", "purchase_price", "avg_cost",
        "sunk", "entry_cost", "paid", "basis_price",
    )
    public = [
        entry_value, admissible, exit_decision,
        hysteresis_band, recycle_hurdle, native_no_bounds, apply_lock,
    ]
    for fn in public:
        for name in inspect.signature(fn).parameters:
            lowered = name.lower()
            for bad in forbidden:
                assert bad not in lowered, f"{fn.__name__}({name}) leaks sunk cost {bad!r}"


def test_module_source_contains_no_float_literals():
    tree = ast.parse(inspect.getsource(law))
    floats = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert not floats, f"float literal(s) in pure Decimal law: {[f.value for f in floats]}"


def test_public_returns_are_decimal_or_frozen():
    assert isinstance(entry_value(D("1"), D("0.5"), D("0.4"), D("0.01")), Decimal)
    assert isinstance(recycle_hurdle(D("0.99")), Decimal)
    lo, hi = hysteresis_band(D("0.8"), D("0.85"), D("0.01"), D("0.01"))
    assert isinstance(lo, Decimal) and isinstance(hi, Decimal)
    nb = native_no_bounds(NativeBounds(D("0.3"), D("0.4"), D("0.5")))
    assert isinstance(nb, NativeBounds)
    d = exit_decision(
        D("1"), D("0.5"), [(D("1"), D("0.8"))], D("0.01"),
        LockState.NONE, True, False,
    )
    assert isinstance(d, ExitDecision)
    # frozen: attributes cannot be reassigned.
    with pytest.raises(Exception):
        d.action = ExitAction.HOLD  # type: ignore[misc]
