# Lifecycle: created=2026-07-25; last_reviewed=2026-07-25; last_reused=never
# Purpose: Law-level antibody — the selection-conditioned overconfidence debit
#   (src/calibration/selection_conditioned_debit.py) applied at the entry law's
#   existing margin slot (`penalty` in select_mode_consistent_ev) rejects a
#   mid-band candidate the undebited law would accept, while a low-disagreement
#   candidate (d_t=0, the "ordinary" state with no evidence) is untouched. Also
#   proves INV-40 non-stacking: the debit never widens q_lcb itself.
"""G- with the selection-conditioned debit vs G- without it."""

from __future__ import annotations

import pytest

from src.calibration.selection_conditioned_debit import (
    compute_selection_debit,
    decision_state,
)
from src.strategy.live_inference.mode_consistent_ev import select_mode_consistent_ev


def test_debit_rejects_mid_band_candidate_the_undebited_law_accepts():
    """A thin-edge mid-band candidate: undebited G- is positive (admissible);
    with the state's walk-forward debit applied as `penalty`, G- flips
    non-positive and the entry law naturally rejects it — no new gate."""
    kwargs = dict(
        q_lcb=0.55,
        taker_all_in_cost=0.50,
        p_fill_taker=1.0,
        best_bid=0.49,
        best_ask=0.50,
        tick_size=0.01,
        reservation=0.50,
    )

    undebited = select_mode_consistent_ev(**kwargs, penalty=0.0)
    assert undebited.chosen_ev == pytest.approx(0.05)  # 1.0 * (0.55 - 0.50 - 0.0) > 0

    # A settled cohort showing q_lcb overstated the realized win rate by 0.06 on
    # average (mean_residual=0.06, n=2000 so lam=2000/2020~0.99) -> d_t~0.0594,
    # comfortably larger than the raw 0.05 edge above.
    debit = compute_selection_debit([0.06] * 2000, state="high")
    assert debit.d_t > 0.05

    debited = select_mode_consistent_ev(**kwargs, penalty=debit.d_t)
    assert debited.chosen_ev <= 0.0  # G- <= 0: the law rejects it, not a hard gate


def test_low_disagreement_candidate_is_untouched_by_zero_debit():
    """A candidate whose own disagreement is small classifies 'ordinary'; with
    no settled evidence of overconfidence in that state, d_t=0.0 and the law's
    decision is byte-identical to the undebited case."""
    kwargs = dict(
        q_lcb=0.90,
        taker_all_in_cost=0.10,
        p_fill_taker=1.0,
        best_bid=0.09,
        best_ask=0.10,
        tick_size=0.01,
        reservation=0.10,
    )
    q_decision, executable_price = 0.92, 0.10  # |0.92 - 0.10| is a HIGH disagreement in
    # raw magnitude, but this test only needs an "ordinary" state with zero
    # evidence to prove the untouched-law property, so use a low-disagreement pair:
    q_decision, executable_price = 0.12, 0.10
    assert decision_state(q_decision, executable_price) == "ordinary"

    no_evidence_debit = compute_selection_debit([], state="ordinary")
    assert no_evidence_debit.d_t == 0.0

    undebited = select_mode_consistent_ev(**kwargs, penalty=0.0)
    debited = select_mode_consistent_ev(**kwargs, penalty=no_evidence_debit.d_t)
    assert debited.chosen_ev == undebited.chosen_ev
    assert debited.ev_taker == undebited.ev_taker
    assert debited.ev_maker == undebited.ev_maker


def test_debit_never_widens_q_lcb_inv40_non_stacking():
    """INV-40: the debit is a margin subtraction applied AFTER q_lcb is passed
    in; it must never appear as a widened/shrunk `q_lcb` value fed into the
    SAME law call — i.e. one candidate, one q_lcb, one margin application."""
    q_lcb = 0.60
    debit = compute_selection_debit([0.10] * 50, state="high")
    assert debit.d_t > 0.0

    result = select_mode_consistent_ev(
        q_lcb=q_lcb,
        taker_all_in_cost=0.50,
        p_fill_taker=1.0,
        best_bid=0.49,
        best_ask=0.50,
        tick_size=0.01,
        reservation=0.50,
        penalty=debit.d_t,
    )
    # The law was called with the RAW q_lcb (0.60), never q_lcb - debit. The
    # debit only ever subtracts inside the EV formula (q - cost - penalty),
    # never mutates the q_lcb input itself — single ownership of the margin
    # term, no double application through a widened q.
    expected_ev_taker = 1.0 * (q_lcb - 0.50 - debit.d_t)
    assert result.ev_taker == pytest.approx(expected_ev_taker)
