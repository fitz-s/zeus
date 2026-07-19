# Created: 2026-07-19
# Authority basis: docs/evidence/capital_efficiency_2026_07_19/fill_funnel.md §3/§7-2
#   (walk-forward measurement: 439/897 = 49% of live ENTRY decisions carried a
#   decision-time p_fill_lcb >= 0.999 near-certainty claim but realized only 25.5%
#   fill; re-bucketed by CHOSEN execution mode, MAKER-mode p_fill_lcb does not
#   separate realized fill outcome AT ALL (flat ~13-14% across every decile) because
#   it is the TAKER visible-depth-coverage bound, persisted unconditionally even
#   when the certificate's own chosen mode is MAKER).
"""Antibody: the certified p_fill_lcb must track the CHOSEN execution mode.

Root cause: _generate_candidate_proofs computed a single p_fill_lcb (a TAKER
depth-coverage Wilson bound, ~1.0 whenever the book covers a min-size order) and
persisted it onto _CandidateProof/the certificate UNCONDITIONALLY -- including on
every REST_DEFAULT/MAKER decision (94% of live ENTRY decisions). mode_ev already
carries the measured maker-fill prior (mode_ev.maker_fill_probability, basis =
MEASURED Kaplan-Meier curve, the sole maker-EV fill source per W4.4 2026-07-03),
but it was never routed to the field every downstream consumer (portfolio_rotation.
candidate_future_value, no_trade_regret events, the settlement-loop join) actually
reads.

_certified_p_fill_lcb_for_proof (src/engine/event_reactor_adapter.py) is the fix:
route mode_ev.maker_fill_probability to the certified field whenever the
CERTIFICATE'S OWN reported execution mode (proof_execution_mode_intent -- the
Day0-override-aware final value, not the raw mode_ev.chosen_mode) is MAKER;
otherwise keep the taker depth-coverage bound (honest for a TAKER cross, and
confirmed by the same walk-forward measurement to track ~90%+ realized fills).

This does NOT reintroduce a learned per-decision recalibration model -- that was
tried and deleted as the order-engine-rebuild anti-pattern §3.4
(docs/rebuild/order_engine_implementation_architecture_2026-07-02.md:55). It only
routes the EXISTING, already-measured, already-frozen maker_fill_probability
constant to the field consumers read.
"""
from __future__ import annotations

import pytest

from src.engine.event_reactor_adapter import _certified_p_fill_lcb_for_proof
from src.strategy.live_inference.mode_consistent_ev import (
    MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE,
    MAKER_FILL_PROBABILITY_DEADLINE_SOURCE,
    ModeConsistentEv,
)


def _mode_ev(*, chosen_mode: str, maker_fill_probability: float = MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE) -> ModeConsistentEv:
    return ModeConsistentEv(
        chosen_mode=chosen_mode,
        chosen_ev=0.01,
        ev_taker=0.005 if chosen_mode == "TAKER" else -0.01,
        ev_maker=0.01 if chosen_mode == "MAKER" else -0.01,
        maker_limit_price=0.5,
        relative_spread=0.02,
        taker_forbidden_reason=None,
        maker_fill_probability=maker_fill_probability,
        maker_fill_probability_source=MAKER_FILL_PROBABILITY_DEADLINE_SOURCE,
        placement="maker_bid_improve" if chosen_mode == "MAKER" else "taker_cross",
        policy="REST_DEFAULT" if chosen_mode == "MAKER" else "TAKER_EDGE_CLEARS_BOUND",
    )


class TestCertifiedPFillLcbTracksChosenMode:
    def test_maker_mode_uses_measured_maker_fill_probability_not_taker_depth_bound(self):
        """The core antibody: a REST_DEFAULT/MAKER decision must NOT persist the
        near-1.0 taker depth-coverage bound as its certified p_fill_lcb."""
        mode_ev = _mode_ev(chosen_mode="MAKER", maker_fill_probability=0.19)
        certified = _certified_p_fill_lcb_for_proof(
            mode_ev=mode_ev,
            proof_execution_mode_intent="MAKER",
            taker_p_fill_lcb=0.9992574820528436,  # observed live saturating value
        )
        assert certified == 0.19
        assert certified != 0.9992574820528436

    def test_taker_mode_keeps_the_depth_coverage_bound(self):
        """TAKER-mode p_fill_lcb IS the right quantity (walk-forward measurement:
        TAKER-mode buckets realize ~90%+ fills, tracking the depth-coverage bound)."""
        mode_ev = _mode_ev(chosen_mode="TAKER")
        certified = _certified_p_fill_lcb_for_proof(
            mode_ev=mode_ev,
            proof_execution_mode_intent="TAKER",
            taker_p_fill_lcb=0.97,
        )
        assert certified == 0.97

    def test_day0_maker_only_override_is_honored(self):
        """proof_execution_mode_intent (the Day0-override-aware FINAL mode), not the
        raw mode_ev.chosen_mode, is the gate -- a Day0 forced-maker decision must
        certify the maker probability even if the internal EV comparison chose TAKER."""
        mode_ev = _mode_ev(chosen_mode="TAKER", maker_fill_probability=0.19)
        certified = _certified_p_fill_lcb_for_proof(
            mode_ev=mode_ev,
            proof_execution_mode_intent="MAKER",  # Day0 override forced this
            taker_p_fill_lcb=0.999,
        )
        assert certified == 0.19

    def test_no_mode_ev_falls_back_to_taker_value(self):
        """Unpriced proof (execution_price/row/c_cost_95pct missing) -> mode_ev is
        None; no chosen-mode distinction exists, so the taker value is kept as the
        conservative pre-existing default (no submit occurs on this path either
        way, so this is immaterial to fill-probability consumers)."""
        certified = _certified_p_fill_lcb_for_proof(
            mode_ev=None,
            proof_execution_mode_intent=None,
            taker_p_fill_lcb=0.0,
        )
        assert certified == 0.0

    def test_none_mode_intent_falls_back_to_taker_value(self):
        """mode_ev present but proof_execution_mode_intent is None/unset (should not
        happen given proof_execution_mode_intent derives from mode_ev.chosen_mode
        whenever mode_ev is not None, but the gate must fail toward the honest
        taker value rather than assume MAKER)."""
        mode_ev = _mode_ev(chosen_mode="MAKER", maker_fill_probability=0.19)
        certified = _certified_p_fill_lcb_for_proof(
            mode_ev=mode_ev,
            proof_execution_mode_intent=None,
            taker_p_fill_lcb=0.85,
        )
        assert certified == 0.85


class TestConsumerSeamRotationMathUsesCorrectedValue:
    """Regression: portfolio_rotation.candidate_future_value's redeploy math must
    move with the corrected p_fill_lcb -- this is the actual point of the fix
    (fewer rotations INTO a maker-rest candidate whose true fill odds are ~13-19%,
    not the ~99.9% the taker depth-coverage bound previously implied)."""

    def test_corrected_maker_p_fill_lcb_reduces_candidate_future_value(self):
        from src.strategy.portfolio_rotation import (
            RotationCandidate,
            candidate_future_value,
        )

        base_kwargs = dict(
            event_id="evt1",
            city="Seoul",
            target_date="2026-07-20",
            metric="high",
            bin_label="88-89F",
            direction="buy_yes",
            q_lcb=0.4,
            fee_adjusted_cost=0.2,
            trade_score=0.05,
        )
        stale_candidate = RotationCandidate(**base_kwargs, p_fill_lcb=0.9992574820528436)
        corrected_candidate = RotationCandidate(**base_kwargs, p_fill_lcb=0.19)

        stale_future, stale_fill_lcb = candidate_future_value(
            stale_candidate, released_cash_usd=10.0
        )
        corrected_future, corrected_fill_lcb = candidate_future_value(
            corrected_candidate, released_cash_usd=10.0
        )

        assert stale_fill_lcb == 0.9992574820528436
        assert corrected_fill_lcb == 0.19
        # Same released cash, same q_lcb/cost -> the corrected (honest) fill
        # probability must yield a materially LOWER expected future value: this
        # is the fix doing its job, not a side effect to explain away.
        # deployed_shares = 10/0.2 = 50; filled_future = 50*0.4 = 20.
        # stale:     0.9992574820528436*20 + 0.0007425179471564*10 ~= 19.9926
        # corrected: 0.19*20 + 0.81*10 = 11.9
        assert stale_future == pytest.approx(19.9926, abs=1e-3)
        assert corrected_future == pytest.approx(11.9, abs=1e-9)
        assert corrected_future < stale_future
        assert (stale_future - corrected_future) > 8.0
