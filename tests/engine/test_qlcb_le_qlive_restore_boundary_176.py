# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: docs/operations/CONSOLIDATED_AUDIT_AND_PLAN_2026-06-04.md
#   R5/#176 — lcb>point inversion at the cross-module restore boundary.
#   The recorded point q_value = 1 - yes_q comes from the inference_engine's
#   NORMALIZED posterior (evaluate_live_bins().normalized()); the recorded raw
#   q_lcb comes from market_analysis's bootstrap, clamped only against
#   market_analysis's OWN (un-normalized) p_posterior (BUG #129 within-module
#   clamp). When sum(P)<1 the two modules' normalizations diverge and the raw
#   q_lcb can land ABOVE the recorded q_value (26,017/60,411 = 43% of live buy_no
#   receipts on 2026-06-03, worst gap 0.79). A "lower bound" above its own point
#   is definitionally impossible. The decision path is contained by trade_score's
#   internal min(); this pins the RECORDED receipt value at the single boundary
#   where both legs are present, regardless of which estimator's domain drifts.
"""Relationship test for #176 — recorded q_lcb_5pct <= recorded q_posterior.

Drives the REAL _generate_candidate_proofs restore path (the cross-module
boundary). Reuses the controlled-QlcbByDirection harness from
test_k3_selection_byte_identity_integration. An inversion is injected by setting
the raw q_lcb above the achievable point; the antibody clamps it at the boundary.
"""
from __future__ import annotations

import types
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.calibration.qlcb_provenance import QlcbByDirection, QlcbProvenance
from src.events.candidate_binding import MarketTopologyCandidate
from src.types.market import Bin


def _inverted_family_and_mock():
    """Two bins whose RAW q_lcb (both legs) is pinned near the ceiling (0.99),
    far ABOVE the achievable normalized point — guaranteeing q_lcb > q_value
    pre-clamp on every recorded proof."""
    b_A = Bin(low=None, high=32, label="32 or below", unit="F")
    b_B = Bin(low=33, high=34, label="33-34", unit="F")
    c_A = MarketTopologyCandidate(
        city="Chicago", target_date="2026-06-04", metric="high",
        condition_id="cond-A", yes_token_id="tok-A-yes", no_token_id="tok-A-no",
        bin=b_A,
    )
    c_B = MarketTopologyCandidate(
        city="Chicago", target_date="2026-06-04", metric="high",
        condition_id="cond-B", yes_token_id="tok-B-yes", no_token_id="tok-B-no",
        bin=b_B,
    )
    family = types.SimpleNamespace(candidates=(c_A, c_B))

    lcb = QlcbByDirection()
    # Raw q_lcb pinned at 0.99 on BOTH directions of BOTH bins → above any
    # 2-bin normalized point (~0.5), an unambiguous inversion pre-clamp.
    for cond in ("cond-A", "cond-B"):
        for d in ("buy_yes", "buy_no"):
            lcb[(cond, d)] = QlcbProvenance(q_lcb=0.99, calibration_source="FORECAST_BOOTSTRAP")

    mock_return = (
        {"cond-A": 0.5, "cond-B": 0.5},   # q_by_condition → normalized yes_q ≈ 0.5/bin
        lcb,
        {("cond-A", "buy_yes"): 0.9, ("cond-A", "buy_no"): 0.9,
         ("cond-B", "buy_yes"): 0.9, ("cond-B", "buy_no"): 0.9},
        {},
        {"p_cal_vector_hash": "h_cal", "p_live_vector_hash": "h_live"},
    )
    return family, mock_return


def _run_proofs(family, mock_return):
    from src.engine.event_reactor_adapter import _generate_candidate_proofs

    event = types.SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY")
    decision_time = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    sentinel = object()
    with patch("src.engine.event_reactor_adapter._live_yes_probabilities",
               return_value=mock_return):
        with patch("src.engine.event_reactor_adapter._native_costs_by_candidate_direction",
                   return_value={}):
            return _generate_candidate_proofs(
                event=event,
                payload={},
                family=family,
                snapshot_rows=[],
                trade_conn=sentinel,        # type: ignore[arg-type]
                forecast_conn=sentinel,     # type: ignore[arg-type]
                calibration_conn=sentinel,  # type: ignore[arg-type]
                decision_time=decision_time,
            )


def test_recorded_q_lcb_never_exceeds_recorded_q_posterior():
    """ANTIBODY: for EVERY recorded proof, q_lcb_5pct <= q_posterior.

    PRE-FIX this FAILS — the raw 0.99 q_lcb is recorded above the ~0.5 point on
    every leg (the cross-module inversion). POST-FIX the boundary clamp pins the
    recorded q_lcb to its own point, making the inversion unconstructable.
    """
    family, mock_return = _inverted_family_and_mock()
    proofs = _run_proofs(family, mock_return)

    assert proofs, "no proofs generated — fixture broken"
    for p in proofs:
        assert p.q_lcb_5pct <= p.q_posterior + 1e-9, (
            f"{p.token_id} ({p.direction}): q_lcb_5pct={p.q_lcb_5pct:.6f} > "
            f"q_posterior={p.q_posterior:.6f} (delta={p.q_lcb_5pct - p.q_posterior:+.6f}) "
            "— a recorded lower bound above its own point (R5/#176 inversion)."
        )
