# Created: 2026-05-31
# Last reused or audited: 2026-10-01
# Authority basis: EDLI_EXECUTION_STRATEGY_DESIGN_2026_05_31.md §4 items 5+6 (Dimension 3 + 6) +
#   PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md SD-6/SD-7. RED-first relationship tests for the
#   EXIT + re-price-cadence half of the execution strategy. SHADOW semantics; no real orders.
"""Relationship tests for the EXIT + re-price cadence (src.events.continuous_redecision).

These pin the ANTI-TWITCH invariant as a cross-module property, not a single function's return:
  - A bare price wiggle (belief unchanged) must NEVER trigger a cancel, a re-price, OR an exit.
  - Only an EVIDENCE-backed belief move (new snapshot_id / CI-separated reversal) acts.
  - A quote priced off a dead book (stale) is pulled regardless of belief (it's not a belief move,
    it's a "this order's price is meaningless now" cancel — re-decide next cycle on fresh price).

§4.6 6b (select_exit_order_mode) is LIVE and covered below. The §4.5 resting-order re-price
(screen_reprice) was deleted 2026-10-01: an open ENTRY rest is valued by the C3 standing
valuation (tests/execution/test_standing_entry_value.py), never pulled on belief-identity drift. §4.6's CI-separation / EVIDENCE_UNAVAILABLE / screen_exit_cancel exit-discriminator
hardening never landed — screen_exit/screen_exit_cancel were deleted as dead code in W3 (#133)
before those tests could go green; that permanently-skipped coverage was removed in the
gate-stack simplification (Phase 1, 2026-07-06) rather than carried forward indefinitely.
"""
from __future__ import annotations

import pytest

cr = pytest.importorskip(
    "src.events.continuous_redecision",
    reason="continuous_redecision module not yet authored — relationship contract is RED",
)


# ===========================================================================
# §4.6 6b — exit order mode routes through the entry order-mode machinery
# ===========================================================================


def test_exit_order_mode_reuses_entry_select_edli_order_mode():
    """6b: an exit is an entry into the OPPOSITE side. select_exit_order_mode must route through the
    SAME governor maker/taker + EV machinery the entry spine uses (reuse _select_edli_order_mode),
    NOT a duplicated exit-only selector. We prove reuse by patching the entry selector and asserting
    the exit path delegates to it (and flips the side / caps at the exit reservation)."""
    import src.engine.event_reactor_adapter as era

    captured: dict = {}

    def _fake_select(*, actionable_payload, **kwargs):
        captured["direction"] = actionable_payload.get("direction")
        captured["c_fee_adjusted"] = actionable_payload.get("c_fee_adjusted")
        return "TAKER"

    orig = era._select_edli_order_mode
    era._select_edli_order_mode = _fake_select
    try:
        mode = cr.select_exit_order_mode(
            held_side="buy_no",                 # we HELD buy_no -> exit enters buy_yes
            exit_reservation=0.42,
            actionable_payload={"direction": "buy_no", "c_fee_adjusted": 0.30},
            quote_payload={},
            best_bid=0.40,
            best_ask=0.45,
            executable_snapshot=None,
        )
    finally:
        era._select_edli_order_mode = orig

    assert mode == "TAKER", "exit order mode must come from the reused entry selector"
    assert captured["direction"] == "buy_yes", "an exit of buy_no enters the OPPOSITE side (buy_yes)"
    assert captured["c_fee_adjusted"] == 0.42, "exit must be capped at the exit reservation (no panic-dump)"
