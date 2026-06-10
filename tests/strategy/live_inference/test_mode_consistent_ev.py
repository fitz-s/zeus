# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: FIX C antibody for incident 0b5c305e26524042 (Milan 24C first
#   fill) + operator directive 2026-06-10 (mode-consistent evaluation);
#   docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md §3.
"""Antibody: evaluation prices the mode it executes; crossing a wide spread is
unconstructable; maker placement improves the bid and cannot cross.

Categories killed:
  1. taker crossing on a wide relative spread (canary / governor / EV override
     included — the LANE is guarded, not the callers);
  2. maker entries evaluated at taker cost with a ~1.0 visible-depth p_fill;
  3. a maker limit that hugs (or lifts) the ask.
"""
from __future__ import annotations

import pytest

from src.strategy.live_inference.mode_consistent_ev import (
    MAKER_FILL_PROBABILITY_PRIOR,
    PLACEMENT_MAKER,
    PLACEMENT_TAKER,
    TAKER_MAX_RELATIVE_SPREAD,
    maker_adverse_selection_haircut,
    maker_limit_price,
    relative_spread,
    select_mode_consistent_ev,
    taker_spread_guard_reason,
)

# Incident book (24C YES): bid 0.009 / ask 0.016, tick 0.001; q_lcb 0.0927 (corrupt),
# fee-adjusted cost 0.0168 / c95 0.0178, visible-depth p_fill 0.9997.
INCIDENT = dict(
    q_lcb=0.09267120287031377,
    taker_all_in_cost=0.0177872,
    p_fill_taker=0.99972,
    best_bid=0.009,
    best_ask=0.016,
    tick_size=0.001,
    reservation=0.0167872,
    penalty=0.01,
)


class TestSpreadGuard:
    def test_incident_spread_forbids_taker(self):
        # (0.016-0.009)/0.0125 = 56% > 25%.
        reason = taker_spread_guard_reason(0.009, 0.016)
        assert reason is not None and reason.startswith("TAKER_FORBIDDEN_RELATIVE_SPREAD")

    def test_tight_spread_allows_taker(self):
        assert taker_spread_guard_reason(0.48, 0.50) is None

    def test_unmeasurable_book_forbids_taker(self):
        # No bid (extreme illiquidity) -> crossing forbidden, fail-closed.
        assert taker_spread_guard_reason(None, 0.016) is not None
        assert taker_spread_guard_reason(0.009, None) is not None

    def test_relative_spread_value(self):
        assert relative_spread(0.009, 0.016) == pytest.approx(0.56)


class TestMakerPlacement:
    def test_maker_limit_improves_bid_never_hugs_ask(self):
        limit = maker_limit_price(best_bid=0.009, best_ask=0.016, tick_size=0.001, reservation=0.0167872)
        assert limit == pytest.approx(0.010)  # bid + 1 tick
        assert limit > 0.009 and limit < 0.016

    def test_one_tick_spread_joins_bid_instead_of_crossing(self):
        # bid 0.49 / ask 0.50, tick 0.01: bid+tick == ask would CROSS; the
        # ask - tick cap makes the order join the bid. Crossing unconstructable.
        limit = maker_limit_price(best_bid=0.49, best_ask=0.50, tick_size=0.01, reservation=0.60)
        assert limit == pytest.approx(0.49)

    def test_reservation_caps_the_improve(self):
        limit = maker_limit_price(best_bid=0.40, best_ask=0.50, tick_size=0.01, reservation=0.35)
        assert limit == pytest.approx(0.35)

    def test_no_bid_rests_inside_ask(self):
        limit = maker_limit_price(best_bid=None, best_ask=0.016, tick_size=0.001, reservation=0.0167872)
        assert limit == pytest.approx(0.015)  # ask - tick

    def test_sub_tick_book_has_no_maker_placement(self):
        assert maker_limit_price(best_bid=None, best_ask=0.001, tick_size=0.001, reservation=0.001) is None

    def test_matches_cert_builder_maker_branch(self):
        """RELATIONSHIP: the evaluation-seam maker limit and the final-intent
        builder's maker branch are the same law (one placement truth)."""
        from src.decision_kernel.certificates.execution import _branch_limit_price

        for bid, ask, tick, reservation in (
            (0.009, 0.016, 0.001, 0.0167872),
            (0.49, 0.50, 0.01, 0.60),
            (0.40, 0.50, 0.01, 0.35),
            (None, 0.016, 0.001, 0.0167872),
        ):
            expected = maker_limit_price(
                best_bid=bid, best_ask=ask, tick_size=tick, reservation=reservation
            )
            actual = _branch_limit_price(
                side="BUY", order_mode="MAKER", reservation=reservation,
                best_bid=bid, best_ask=ask, tick_size=tick, passive_maker_context=None,
            )
            assert actual == pytest.approx(expected), (bid, ask, tick, reservation)

    def test_cert_maker_tuple_is_post_only(self):
        from src.decision_kernel.certificates.execution import _order_spec_for_mode

        spec = _order_spec_for_mode(order_mode="MAKER", order_type=None, time_in_force=None)
        assert spec.post_only is True
        assert spec.maker_intent is True
        assert spec.time_in_force in {"GTC", "GTD"}
        taker = _order_spec_for_mode(order_mode="TAKER", order_type=None, time_in_force=None)
        assert taker.post_only is False


class TestModeSelection:
    def test_milan_shape_maker_ev_is_tiny_vs_hybrid(self):
        """Incident shape under mode-consistent semantics: taker forbidden (56%
        spread); maker EV with fill prior + adverse haircut is ~10x smaller than
        the 0.0649 hybrid score that sized the wrong order."""
        ev = select_mode_consistent_ev(**INCIDENT)
        assert ev.taker_forbidden_reason is not None
        assert ev.chosen_mode == "MAKER"
        assert ev.placement == PLACEMENT_MAKER
        assert ev.maker_limit_price == pytest.approx(0.010)
        assert ev.ev_maker is not None and ev.ev_maker < 0.01
        # The hybrid would have said 0.0649; mode-consistent says < 1c of EV.
        assert ev.chosen_ev < 0.01

    def test_tight_spread_favorite_chooses_taker(self):
        ev = select_mode_consistent_ev(
            q_lcb=0.56, taker_all_in_cost=0.51, p_fill_taker=0.999,
            best_bid=0.48, best_ask=0.50, tick_size=0.01, reservation=0.51,
            penalty=0.01,
        )
        assert ev.taker_forbidden_reason is None
        assert ev.ev_taker == pytest.approx(0.999 * (0.56 - 0.51 - 0.01))
        assert ev.ev_taker > ev.ev_maker
        assert ev.chosen_mode == "TAKER" and ev.placement == PLACEMENT_TAKER

    def test_wide_spread_chooses_maker_with_bid_improve(self):
        ev = select_mode_consistent_ev(
            q_lcb=0.56, taker_all_in_cost=0.51, p_fill_taker=0.999,
            best_bid=0.30, best_ask=0.50, tick_size=0.01, reservation=0.51,
            penalty=0.01,
        )
        # rel spread = 0.20/0.40 = 50% > 25% -> taker forbidden despite 4c edge.
        assert ev.taker_forbidden_reason is not None
        assert ev.chosen_mode == "MAKER"
        assert ev.maker_limit_price == pytest.approx(0.31)  # bid + tick

    def test_adverse_selection_haircut_is_half_spread(self):
        haircut = maker_adverse_selection_haircut(
            best_bid=0.30, best_ask=0.50, maker_limit=0.31
        )
        assert haircut == pytest.approx(0.10)

    def test_taker_formula_matches_legacy_kernel_q_lcb_leg(self):
        """RELATIONSHIP: TAKER-chosen score is byte-identical to the legacy
        robust kernel (c_stress == c95 made the legacy min() always the q_lcb
        leg) — mode-consistency changes maker semantics only."""
        from src.contracts.execution_price import ExecutionPrice
        from src.strategy.live_inference.trade_score import robust_trade_score

        q_lcb, q_post, c95, p_fill = 0.56, 0.60, 0.51, 0.999
        legacy = robust_trade_score(
            trade_score_id="parity",
            q_posterior=q_post, q_5pct=q_lcb,
            c_95pct=ExecutionPrice(c95, "ask", fee_deducted=True, currency="probability_units"),
            c_stress=ExecutionPrice(c95, "ask", fee_deducted=True, currency="probability_units"),
            p_fill_lcb=p_fill, penalty=0.01, stress_penalty=0.01,
        ).score
        ev = select_mode_consistent_ev(
            q_lcb=q_lcb, taker_all_in_cost=c95, p_fill_taker=p_fill,
            best_bid=0.48, best_ask=0.50, tick_size=0.01, reservation=0.51,
            penalty=0.01,
        )
        assert ev.chosen_mode == "TAKER"
        assert ev.chosen_ev == pytest.approx(float(legacy))

    def test_both_evs_always_recorded(self):
        ev = select_mode_consistent_ev(**INCIDENT)
        assert ev.ev_maker is not None
        assert ev.ev_taker is not None  # recorded even though forbidden
        assert ev.relative_spread == pytest.approx(0.56)
        assert ev.maker_fill_probability == MAKER_FILL_PROBABILITY_PRIOR


# ---------------------------------------------------------------------------
# Submit-seam antibody: the spread guard dominates the canary force-taker and
# the governor — the LANE is guarded, not the callers.
# ---------------------------------------------------------------------------
def _order_mode(*, bid, ask, canary=True):
    import types

    from src.engine.event_reactor_adapter import _select_edli_order_mode

    actionable_payload = {
        "direction": "buy_yes",
        "q_live": 0.0927,
        "c_fee_adjusted": ask + 0.0008 if ask else 0.02,
        "p_fill_lcb": 0.999,
        "trade_score": 0.06,
    }
    return _select_edli_order_mode(
        actionable_payload=actionable_payload,
        quote_payload={},
        best_bid=bid,
        best_ask=ask,
        executable_snapshot=types.SimpleNamespace(payload={}),
        canary_force_taker=canary,
        fresh_best_bid=bid,
        fresh_best_ask=ask,
    )


def test_submit_seam_wide_spread_forbids_taker_even_under_canary_force():
    """THE incident lane: canary_force_taker with a 7.5c post-cross edge on a
    56% relative spread MUST rest as maker, never cross."""
    assert _order_mode(bid=0.009, ask=0.016, canary=True) == "MAKER"


def test_submit_seam_unmeasurable_book_forbids_taker():
    assert _order_mode(bid=None, ask=0.016, canary=True) == "MAKER"


def test_submit_seam_tight_spread_canary_can_still_cross():
    """Maker-vs-taker stays a numbers decision where the spread is healthy: the
    canary's 5c post-cross edge floor still routes taker on a tight book."""
    import types

    from src.engine.event_reactor_adapter import _select_edli_order_mode

    actionable_payload = {
        "direction": "buy_yes",
        "q_live": 0.56,
        "c_fee_adjusted": 0.51,
        "p_fill_lcb": 0.999,
        "trade_score": 0.06,
    }
    mode = _select_edli_order_mode(
        actionable_payload=actionable_payload,
        quote_payload={},
        best_bid=0.48,
        best_ask=0.50,
        executable_snapshot=types.SimpleNamespace(payload={}),
        canary_force_taker=True,
        fresh_best_bid=0.48,
        fresh_best_ask=0.50,
    )
    # post_cross_edge = 0.56 - 0.50 = 0.06 >= 0.05 floor, spread 4% < 25%.
    assert mode == "TAKER"


# ---------------------------------------------------------------------------
# Receipt antibody: the proof seam threads chosen mode + both EVs to the
# CandidateEvaluation receipt dict.
# ---------------------------------------------------------------------------
def test_receipts_carry_mode_and_both_evs():
    import json
    import types
    from datetime import datetime, timezone
    from unittest.mock import patch

    from src.calibration.qlcb_provenance import QlcbByDirection, QlcbProvenance
    from src.engine.event_reactor_adapter import (
        _candidate_evaluation_from_proof,
        _generate_candidate_proofs,
    )
    from src.events.candidate_binding import MarketTopologyCandidate
    from src.types.market import Bin

    depth = {
        "YES": {"asks": [{"price": "0.03", "size": "1000"}],
                "bids": [{"price": "0.02", "size": "100"}]},
        "NO": {"asks": [{"price": "0.98", "size": "1000"}],
               "bids": [{"price": "0.95", "size": "100"}]},
    }
    row = {
        "snapshot_id": "snap-1", "condition_id": "cond-1",
        "yes_token_id": "cond-1-yes", "no_token_id": "cond-1-no",
        "selected_outcome_token_id": "", "outcome_label": "",
        "min_tick_size": "0.001", "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": 0, "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}", "book_hash": "book-1",
    }
    candidate = MarketTopologyCandidate(
        city="Milan", target_date="2026-06-11", metric="high",
        condition_id="cond-1", yes_token_id="cond-1-yes",
        no_token_id="cond-1-no", bin=Bin(low=26.0, high=26.0, unit="C", label="26°C"),
    )
    family = types.SimpleNamespace(candidates=(candidate,), city="Milan",
                                   target_date="2026-06-11", metric="high")
    lcb = QlcbByDirection()
    lcb[("cond-1", "buy_yes")] = QlcbProvenance(q_lcb=0.12, calibration_source="SETTLEMENT_ISOTONIC")
    lcb[("cond-1", "buy_no")] = QlcbProvenance(q_lcb=0.0, calibration_source="SETTLEMENT_ISOTONIC")
    mock_return = (
        {"cond-1": 0.13}, lcb,
        {("cond-1", "buy_yes"): 0.0, ("cond-1", "buy_no"): 1.0}, {},
        {"p_cal_vector_hash": "h", "p_live_vector_hash": "h",
         "forecast_mu_c": 26.42, "forecast_predictive_sigma_c": 1.26},
    )
    sentinel = object()
    with patch("src.engine.event_reactor_adapter._live_yes_probabilities",
               return_value=mock_return):
        proofs = _generate_candidate_proofs(
            event=types.SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload={}, family=family, snapshot_rows=[row],
            trade_conn=sentinel, forecast_conn=sentinel, calibration_conn=sentinel,
            decision_time=datetime(2026, 6, 10, 3, 0, tzinfo=timezone.utc),
        )
    yes_proof = next(p for p in proofs if p.direction == "buy_yes")
    assert yes_proof.execution_mode_intent in {"MAKER", "TAKER"}
    assert yes_proof.ev_taker is not None and yes_proof.ev_maker is not None
    receipt = _candidate_evaluation_from_proof(
        family_id="fam-1", proof=yes_proof
    ).to_receipt_dict()
    for key in ("execution_mode_intent", "ev_taker", "ev_maker",
                "maker_limit_price", "relative_spread_at_eval",
                "maker_fill_probability", "maker_fill_probability_source"):
        assert key in receipt, f"receipt missing {key}"
    assert receipt["execution_mode_intent"] == yes_proof.execution_mode_intent
