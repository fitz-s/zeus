# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D2 shift-bin "close-before-open". The same-token "never increase
#   exposure" invariant + the D1 fill-up RESIDUAL lift guard the 2026-06-16
#   double-rest defect; D2 adds the SIBLING case: when a fresh redecision selects a
#   DIFFERENT bin in a family that already holds a position, exposure is MOVED by
#   CLOSING the old leg FIRST and entering the new bin ONLY after the old leg is
#   proven closed (zero/dust). This is the inherently MULTI-CYCLE state machine
#   driven by the family-rebalance lease status + venue/fill truth.
"""ANTIBODY: shift-bin is CLOSE-BEFORE-OPEN. A sibling-different-bin redecision must
NEVER open the new bin while the old leg has live/partial/unknown exposure; it exits
the old leg first, and the counter-entry is admitted ONLY after the old residual is
proven zero/dust AND the fresh recompute still selects the sibling. Pure-predicate
half (the lease/orchestration half lives in test_shift_bin_wiring.py)."""
from __future__ import annotations

import pytest

from src.strategy.family_rebalance import decide_shift_bin


def _base(**over):
    kw = dict(
        is_redecision_event=True,
        selected_token_id="tok-B",
        selected_bin_id="bin-B",
        selected_direction="buy_yes",
        held_token_id="tok-A",
        held_bin_id="bin-A",
        held_position_id="p1",
        old_leg_residual_usd=4.0,          # old leg still live
        has_unowned_pending_or_unknown_entry=False,
        old_leg_dust_floor_usd=1.0,        # below this == proven closed (dust)
        # Default belief is already WEAKENED so tests unrelated to the belief gate
        # (residual/dust-floor/blocking) keep exercising EXIT_OLD_LEG unchanged.
        # Tests targeting the belief gate itself override these two explicitly.
        old_leg_q_current_lcb=0.20,
        old_leg_q_entry_lcb=0.80,
    )
    kw.update(over)
    return kw


def test_sibling_with_live_old_leg_exits_first():
    """Old leg still has live exposure → EXIT_OLD_LEG, no counter-entry yet."""
    d = decide_shift_bin(**_base(old_leg_residual_usd=4.0))
    assert d.phase == "EXIT_OLD_LEG"
    assert d.allow_entry is False


def test_sibling_old_leg_closed_to_zero_allows_entry():
    """Old leg residual proven ZERO → ENTER_NEW_BIN (close confirmed)."""
    d = decide_shift_bin(**_base(old_leg_residual_usd=0.0))
    assert d.phase == "ENTER_NEW_BIN"
    assert d.allow_entry is True


def test_sibling_old_leg_below_dust_allows_entry():
    """Old leg residual below the dust/min-order floor → ENTER_NEW_BIN."""
    d = decide_shift_bin(**_base(old_leg_residual_usd=0.5, old_leg_dust_floor_usd=1.0))
    assert d.phase == "ENTER_NEW_BIN"
    assert d.allow_entry is True


def test_sibling_old_leg_at_dust_floor_is_still_live():
    """Residual AT/above the dust floor is still live exposure → exit first."""
    d = decide_shift_bin(**_base(old_leg_residual_usd=1.0, old_leg_dust_floor_usd=1.0))
    assert d.phase == "EXIT_OLD_LEG"
    assert d.allow_entry is False


def test_blocking_unowned_exposure_aborts_no_exit_no_entry():
    """Any unowned pending/unknown/partial family command → ABORT, no exit, no entry
    (the 2026-06-16 double-rest hazard — never act over ambiguous family exposure)."""
    d = decide_shift_bin(**_base(has_unowned_pending_or_unknown_entry=True))
    assert d.phase == "BLOCKED"
    assert d.allow_entry is False
    assert "BLOCK" in d.reason.upper()


def test_same_token_is_not_shift_bin():
    """Selected token == held token is FILL-UP, not shift-bin — deny here."""
    d = decide_shift_bin(**_base(selected_token_id="tok-A", selected_bin_id="bin-A"))
    assert d.phase == "NOT_SHIFT_BIN"
    assert d.allow_entry is False


def test_same_bin_is_not_shift_bin():
    """Same bin id (the two sides of one bin) is not a shift to a DIFFERENT bin."""
    d = decide_shift_bin(**_base(selected_token_id="tok-B", selected_bin_id="bin-A"))
    assert d.phase == "NOT_SHIFT_BIN"
    assert d.allow_entry is False


def test_no_held_exposure_is_not_shift_bin():
    """No held position → fresh entry, not a shift-bin."""
    d = decide_shift_bin(**_base(held_token_id=None, held_bin_id=None, held_position_id=None))
    assert d.phase == "NOT_SHIFT_BIN"
    assert d.allow_entry is False


def test_non_redecision_event_is_not_shift_bin():
    d = decide_shift_bin(**_base(is_redecision_event=False))
    assert d.phase == "NOT_SHIFT_BIN"
    assert d.allow_entry is False


def test_blocking_takes_priority_over_entry_when_old_leg_closed():
    """Even with the old leg closed, an unowned blocking command still aborts —
    fail closed dominates the entry path."""
    d = decide_shift_bin(**_base(old_leg_residual_usd=0.0, has_unowned_pending_or_unknown_entry=True))
    assert d.phase == "BLOCKED"
    assert d.allow_entry is False


# ---------------------------------------------------------------------------
# VALUE/BELIEF GATE — mirrors decide_fill_up's inverse (belief WEAKENED, not
# strengthened). A live old leg must NOT be dumped just because a fresh
# redecision named a different sibling bin; only a genuinely weakened belief
# may exit it.
# ---------------------------------------------------------------------------
def test_shift_denied_when_old_leg_belief_not_weakened():
    """Old leg belief UNCHANGED/strong (current == entry) → HOLD, do not churn."""
    d = decide_shift_bin(**_base(
        old_leg_residual_usd=10.0,
        old_leg_q_current_lcb=0.83,
        old_leg_q_entry_lcb=0.83,
    ))
    assert d.phase == "NOT_SHIFT_BIN"
    assert d.allow_entry is False
    assert "BELIEF_NOT_WEAKENED" in d.reason.upper()


def test_shift_allowed_when_old_leg_belief_weakened():
    """Old leg belief genuinely WEAKENED relative to entry → EXIT_OLD_LEG preserved."""
    d = decide_shift_bin(**_base(
        old_leg_residual_usd=10.0,
        old_leg_q_current_lcb=0.23,
        old_leg_q_entry_lcb=0.87,
    ))
    assert d.phase == "EXIT_OLD_LEG"
    assert d.allow_entry is False


def test_shift_denied_when_old_leg_belief_unknown_fail_closed():
    """Missing current belief on a live old leg → fail closed, HOLD (do not churn)."""
    d = decide_shift_bin(**_base(
        old_leg_residual_usd=10.0,
        old_leg_q_current_lcb=None,
        old_leg_q_entry_lcb=0.83,
    ))
    assert d.phase == "NOT_SHIFT_BIN"
    assert d.allow_entry is False
    assert "BELIEF_UNKNOWN" in d.reason.upper()


def test_shift_denied_when_old_leg_entry_belief_unknown_fail_closed():
    """Missing entry belief on a live old leg → fail closed, HOLD (do not churn)."""
    d = decide_shift_bin(**_base(
        old_leg_residual_usd=10.0,
        old_leg_q_current_lcb=0.23,
        old_leg_q_entry_lcb=None,
    ))
    assert d.phase == "NOT_SHIFT_BIN"
    assert d.allow_entry is False
    assert "BELIEF_UNKNOWN" in d.reason.upper()


def test_shift_belief_weakening_floor_hysteresis():
    """With a hysteresis floor, a marginal weakening below the floor is denied."""
    d = decide_shift_bin(**_base(
        old_leg_residual_usd=10.0,
        old_leg_q_current_lcb=0.79,
        old_leg_q_entry_lcb=0.83,
        shift_belief_weakening_floor=0.10,
    ))
    assert d.phase == "NOT_SHIFT_BIN"  # 0.83 - 0.79 = 0.04 < 0.10 floor
    d2 = decide_shift_bin(**_base(
        old_leg_residual_usd=10.0,
        old_leg_q_current_lcb=0.70,
        old_leg_q_entry_lcb=0.83,
        shift_belief_weakening_floor=0.10,
    ))
    assert d2.phase == "EXIT_OLD_LEG"  # 0.83 - 0.70 = 0.13 >= 0.10 floor


def test_enter_new_bin_unchanged_when_residual_already_dust_regardless_of_belief():
    """ENTER_NEW_BIN (residual proven zero/dust) is not a churn sell — belief gate
    must not apply once the old leg is already closed."""
    d = decide_shift_bin(**_base(
        old_leg_residual_usd=0.0,
        old_leg_q_current_lcb=None,
        old_leg_q_entry_lcb=None,
    ))
    assert d.phase == "ENTER_NEW_BIN"
    assert d.allow_entry is True
