"""Low entry price is a sunk cost, never a categorical SELL veto."""

import inspect

from src.engine import global_batch_runtime
from tests.solve.test_solver_properties import _global_select, _global_sell_candidate


def test_low_entry_price_cannot_remove_a_positive_statistical_sell():
    sell = _global_sell_candidate(
        candidate_id="cheap-position-reversal",
        family="Moscow|2026-08-28|high",
        side="YES",
        held_q=0.0274,
        bids=(("0.10", "5"), ("0.07", "9.37"), ("0.06", "12"), ("0.05", "35.89")),
        shares="28",
        probability_functional="POSTERIOR_PREDICTIVE_MEAN",
        exit_authority_status="immature",
        exit_authority_reason="day0_high_extreme_not_mature",
    )

    decision = _global_select((sell,))

    assert decision.candidate is sell
    assert decision.shares == sell.held_shares
    assert decision.expected_terminal_wealth is not None
    assert decision.expected_terminal_wealth.expected_ev_usd > 0.0


def test_runtime_sell_policy_does_not_read_entry_price():
    source = inspect.getsource(global_batch_runtime.process_current_global_batch)

    assert "GLOBAL_SELL_CONVEX_HOLD_EXEMPT" not in source
    assert "convex_hold_exempt_position_ids" not in source
    assert not hasattr(global_batch_runtime, "CONVEX_HOLD_PRICE_THRESHOLD")
