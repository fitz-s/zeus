# Created: 2026-06-10
# Last reused or audited: 2026-06-14
# Authority basis: FIX B antibody for incident 0b5c305e26524042 (Milan 24C first
#   fill); docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md.
#   pr408 review C1+C2 #2 CRITICAL (2026-06-14): the cheap-tail guard was a forbidden
#   ONE-SIDED fail-CLOSED gate (operator law 3) that killed +edge cheap trades. The PURE
#   PREDICATE (coverage_unlicensed_tail_rejection_reason) is retained as the single
#   definition of the tail concern and is now used ONLY for decision telemetry; it no longer
#   zeros score / fails prefilter / sets a no-trade reason / alters size on the live path.
"""Predicate + telemetry-only wiring for the unlicensed cheap-tail disagreement signal.

The PURE PREDICATE still classifies the category (market price < 0.05 while a
FORECAST_BOOTSTRAP settlement-unlicensed q_lcb claims > 2x the market; near-center and
licensed bands abstain). pr408 #2: the live proof seam records this ONLY as
``unlicensed_tail_coverage_telemetry`` — it never score-zeros, never fails prefilter,
never becomes a live reason. RED-on-revert: a candidate that would have been killed now trades.
"""
from __future__ import annotations

import pytest

from src.calibration.qlcb_provenance import CALIBRATION_SOURCES
from src.strategy.live_inference.live_admission import (
    COVERAGE_LICENSED_LCB_SOURCES,
    coverage_unlicensed_tail_rejection_reason,
)


def test_incident_shape_rejected():
    # Milan 24C: q_lcb 0.0927 vs fee-adjusted price 0.0168 (5.5x), FORECAST_BOOTSTRAP.
    reason = coverage_unlicensed_tail_rejection_reason(
        q_lcb=0.09267120287031377,
        execution_price=0.0167872,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
    )
    assert reason is not None and reason.startswith("COVERAGE_UNLICENSED_TAIL")


def test_licensed_sources_trade_again():
    for source in ("EMOS_ANALYTIC", "SETTLEMENT_ISOTONIC"):
        assert (
            coverage_unlicensed_tail_rejection_reason(
                q_lcb=0.0927,
                execution_price=0.0168,
                q_lcb_calibration_source=source,
            )
            is None
        )


def test_near_center_prices_untouched_by_construction():
    # price >= 0.05: guard abstains even at extreme disagreement.
    assert (
        coverage_unlicensed_tail_rejection_reason(
            q_lcb=0.50,
            execution_price=0.05,
            q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        )
        is None
    )
    assert (
        coverage_unlicensed_tail_rejection_reason(
            q_lcb=0.90,
            execution_price=0.30,
            q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        )
        is None
    )


def test_modest_disagreement_in_tail_allowed():
    # q_lcb <= 2x price: an honest small edge in a cheap bin still trades.
    assert (
        coverage_unlicensed_tail_rejection_reason(
            q_lcb=0.03,
            execution_price=0.02,
            q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        )
        is None
    )


def test_unpriced_candidate_is_not_this_guards_business():
    assert (
        coverage_unlicensed_tail_rejection_reason(
            q_lcb=0.5,
            execution_price=None,
            q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        )
        is None
    )


def test_missing_or_unknown_source_is_unlicensed():
    for source in (None, "", "YES_UCB_DERIVED", "made_up"):
        reason = coverage_unlicensed_tail_rejection_reason(
            q_lcb=0.10,
            execution_price=0.02,
            q_lcb_calibration_source=source,
        )
        assert reason is not None, f"source={source!r} must be unlicensed"


def test_nonfinite_inputs_fail_closed():
    reason = coverage_unlicensed_tail_rejection_reason(
        q_lcb=float("nan"),
        execution_price=0.02,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
    )
    assert reason is not None


def test_licensed_vocabulary_is_subset_of_carrier_vocabulary():
    """Relationship: every licensed source must be expressible by the
    QlcbProvenance carrier (mirrors the FIX-4 buy_no allow-list law)."""
    assert COVERAGE_LICENSED_LCB_SOURCES <= CALIBRATION_SOURCES
    assert "FORECAST_BOOTSTRAP" not in COVERAGE_LICENSED_LCB_SOURCES


# ---------------------------------------------------------------------------
# Wiring antibody (pr408 #2): the proof seam records the tail concern as SHADOW
# telemetry where the direction law passes (forecast-ADJACENT cheap bin with an
# unlicensed inflated q_lcb) — and does NOT kill the trade. The forbidden one-sided
# fail-closed gate is removed; the +edge cheap candidate stays eligible.
# ---------------------------------------------------------------------------
import json  # noqa: E402
import types  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest.mock import patch  # noqa: E402

from src.calibration.qlcb_provenance import QlcbByDirection, QlcbProvenance  # noqa: E402
from src.events.candidate_binding import MarketTopologyCandidate  # noqa: E402
from src.types.market import Bin  # noqa: E402


def _wired_proofs(*, calibration_source: str):
    """One forecast-adjacent bin (contains mu*) priced as a 3c longshot with an
    unlicensed q_lcb 4x the market: direction law passes, FIX B must decide."""
    bin_obj = Bin(low=26.0, high=26.0, unit="C", label="26°C")
    candidate = MarketTopologyCandidate(
        city="Milan", target_date="2026-06-11", metric="high",
        condition_id="cond-26", yes_token_id="cond-26-yes",
        no_token_id="cond-26-no", bin=bin_obj,
    )
    family = types.SimpleNamespace(candidates=(candidate,), city="Milan",
                                   target_date="2026-06-11", metric="high")
    depth = {
        "YES": {"asks": [{"price": "0.03", "size": "1000"}],
                "bids": [{"price": "0.02", "size": "100"}]},
        "NO": {"asks": [{"price": "0.98", "size": "1000"}],
               "bids": [{"price": "0.95", "size": "100"}]},
    }
    row = {
        "snapshot_id": "snap-26", "condition_id": "cond-26",
        "yes_token_id": "cond-26-yes", "no_token_id": "cond-26-no",
        "selected_outcome_token_id": "", "outcome_label": "",
        "min_tick_size": "0.001", "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": 0, "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}", "book_hash": "book-26",
    }
    lcb = QlcbByDirection()
    lcb[("cond-26", "buy_yes")] = QlcbProvenance(
        q_lcb=0.12, calibration_source=calibration_source
    )
    lcb[("cond-26", "buy_no")] = QlcbProvenance(
        q_lcb=0.0, calibration_source=calibration_source
    )
    mock_return = (
        {"cond-26": 0.12}, lcb,
        {("cond-26", "buy_yes"): 0.0, ("cond-26", "buy_no"): 1.0}, {},
        {"p_cal_vector_hash": "h", "p_live_vector_hash": "h",
         "forecast_mu_c": 26.42, "forecast_predictive_sigma_c": 1.26},
    )
    from src.engine.event_reactor_adapter import _generate_candidate_proofs

    sentinel = object()
    with patch(
        "src.engine.event_reactor_adapter._live_yes_probabilities",
        return_value=mock_return,
    ):
        proofs = _generate_candidate_proofs(
            event=types.SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload={}, family=family, snapshot_rows=[row],
            trade_conn=sentinel, forecast_conn=sentinel, calibration_conn=sentinel,
            decision_time=datetime(2026, 6, 10, 3, 0, tzinfo=timezone.utc),
        )
    return {p.direction: p for p in proofs}


def test_proof_seam_unlicensed_adjacent_tail_is_telemetry_only_not_killed():
    """pr408 #2 RED-on-revert: a cheap-tail unlicensed +edge candidate is FLAGGED in the
    telemetry but stays eligible — never score-zeroed, never a live reason, never prefilter-
    failed by the tail concern. Reverting (re-adding the live gate) makes score==0."""
    p = _wired_proofs(calibration_source="FORECAST_BOOTSTRAP")["buy_yes"]
    # the tail concern is recorded for settlement-graded study ...
    assert p.unlicensed_tail_coverage_telemetry is True
    # ... but it does NOT kill the trade: no COVERAGE_UNLICENSED_TAIL live reason, the
    # score is not zeroed BY THE TAIL GUARD, and the candidate is not prefilter-failed by it.
    assert p.missing_reason is None or not p.missing_reason.startswith(
        "COVERAGE_UNLICENSED_TAIL"
    )
    assert p.trade_score > 0.0
    assert p.passed_prefilter is True


def test_proof_seam_settlement_licensed_tail_has_no_telemetry_flag():
    p = _wired_proofs(calibration_source="SETTLEMENT_ISOTONIC")["buy_yes"]
    assert p.unlicensed_tail_coverage_telemetry is False
    assert p.missing_reason is None or not p.missing_reason.startswith(
        "COVERAGE_UNLICENSED_TAIL"
    )
