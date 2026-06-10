# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §14.2 (NativeSideCandidate per bin per side) +
#   §6 pseudocode (evaluate_family: a YES and a NO NativeSideCandidate per bin) +
#   §4 (native YES/NO separation: belief / executable / portfolio spaces;
#       p_exec(NO) != 1 - p_exec(YES)) + §9 Hidden #1 (FDR denominator includes
#       native NO) / Hidden #4 (native quote present but NO posterior missing) +
#   §11 Phase 1 (formal candidate object model) + §12.A native-side economics +
#   §12.D family selection (incomplete-family no-trade) + §13 no-trade gates +
#   operator directive 2026-06-08 (single primary-live path; NO flag, NO shadow).
"""S3 relationship tests — each priced proof materializes as a NativeSideCandidate.

RELATIONSHIP TESTS (cross-module invariants), written BEFORE the implementation
per the order relationship-tests -> implementation -> function-tests. They pin
the properties that survive the seam where a priced ``_CandidateProof`` (scalar
q_lcb + ExecutionPrice + missing_reason strings) flows into the unified
bin-selection candidate object ``NativeSideCandidate`` consumed by the
ranker/selector.

S3 REPLACES the split-shape per-proof materialization
(``_candidate_evaluation_from_proof`` building a ``CandidateEvaluation`` straight
from the proof) with: build the canonical ``NativeSideCandidate`` FIRST (the one
materialized truth — YES or NO, q_point, q_lcb, ProbabilityUncertainty from S2,
ExecutableCostCurve from S1, token/condition/snapshot ids), then derive the
legacy ``CandidateEvaluation`` receipt FROM that candidate. There is ONE
materialization path producing ONE candidate object. Missing native token/quote
downgrades to a NATIVE_TOKEN_MISSING / NATIVE_QUOTE_MISSING no-trade candidate —
NEVER a YES-derived complement price for the NO side.

The three named S3 invariants:

  test_two_native_candidates_per_executable_bin (§12.D / Hidden #1):
        a complete bin yields a YES and a NO NativeSideCandidate; the NO carries
        its OWN NO token, NO ExecutableCostCurve (side=='NO'), and its OWN NO
        q (q_lcb_no = 1 - q_ucb_yes, NOT 1 - q_lcb_yes).

  test_missing_native_no_quote_is_no_trade_not_complement (§12.A.2 / §4):
        a bin whose NO token has no executable ask yields a NATIVE_QUOTE_MISSING
        no-trade NativeSideCandidate; no path reads a YES-derived price into it.

  test_selected_token_identity_differs_by_side (§12.A.4):
        the same bin's YES vs NO candidates yield different
        selected_token_identity tuples (different token => different snapshot leg).

Plus the money-path iron-law invariants the S3 seam must preserve (DIRECTION LAW,
q_lcb authority, native executable separation, q_lcb <= q_point, NO-lcb
correctness).
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.contracts.executable_cost_curve import ExecutableCostCurve
from src.contracts.execution_price import ExecutionPrice
from src.contracts.native_side_candidate import (
    CandidateNoTradeReason,
    NativeSideCandidate,
)
from src.engine import event_reactor_adapter as era
from src.events.candidate_binding import MarketTopologyCandidate
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Snapshot-row fixture (same shape S1's tests use): the dict consumed by
# _native_quote_book_from_snapshot_row / _native_side_cost_curve_from_snapshot_row.
# ---------------------------------------------------------------------------
def _row(
    *,
    yes_asks=(("0.40", "1000"),),
    no_asks=(("0.55", "1000"),),
    yes_bids=(("0.39", "100"),),
    no_bids=(("0.19", "100"),),
    min_tick="0.01",
    min_order="5",
    fee_rate_fraction=0.0,
    snapshot_id="snap-s3",
):
    depth = {
        "YES": {
            "asks": [{"price": p, "size": s} for p, s in yes_asks],
            "bids": [{"price": p, "size": s} for p, s in yes_bids],
        },
        "NO": {
            "asks": [{"price": p, "size": s} for p, s in no_asks],
            "bids": [{"price": p, "size": s} for p, s in no_bids],
        },
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": "condition-1",
        "yes_token_id": "yes-1",
        "no_token_id": "no-1",
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": min_tick,
        "min_order_size": min_order,
        "fee_details_json": json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": "book-hash-s3",
    }


def _candidate(*, condition_id="condition-1", yes_token="yes-1", no_token="no-1"):
    return MarketTopologyCandidate(
        city="paris",
        target_date="2026-06-10",
        metric="tmax",
        condition_id=condition_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
        bin=Bin(low=60.0, high=61.0, unit="F", label="60-61°F"),
    )


def _proof(
    *,
    direction: str,
    row: dict,
    token_id: str,
    q_posterior: float,
    q_lcb_5pct: float,
    execution_price: ExecutionPrice | None,
    missing_reason: str | None = None,
    native_quote_available: bool | None = None,
    same_bin_yes_posterior: float | None = None,
) -> era._CandidateProof:
    """Build a priced _CandidateProof the way _generate_candidate_proofs does."""
    if native_quote_available is None:
        native_quote_available = execution_price is not None
    return era._CandidateProof(
        candidate=_candidate(condition_id=str(row.get("condition_id") or "")),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=execution_price,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=0.0,
        p_value=0.5,
        passed_prefilter=execution_price is not None,
        native_quote_available=native_quote_available,
        p_cal_vector_hash="cal-hash",
        p_live_vector_hash="live-hash",
        missing_reason=missing_reason,
        same_bin_yes_posterior=same_bin_yes_posterior,
    )


def _priced(row: dict, *, token_id: str, direction: str) -> ExecutionPrice:
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return ep


# ===========================================================================
# Named invariant 1 — two native candidates per executable bin (§12.D, Hidden #1)
# ===========================================================================
def test_two_native_candidates_per_executable_bin():
    """A complete bin yields a YES and a NO NativeSideCandidate; the NO carries
    its OWN NO token / curve / q (§12.D, Hidden #1).

    Both sides materialize as TRADEABLE NativeSideCandidate objects. The NO
    candidate's executable_cost_curve walks the NO ask book (side=='NO'), and its
    q is the native NO authority — never a YES point-complement.
    """
    row = _row(yes_asks=(("0.40", "1000"),), no_asks=(("0.55", "1000"),))

    # YES authority: q_yes point / lcb. NO authority (Hidden #3): q_lcb_no is the
    # lower tail of (1 - q_yes_samples) == 1 - q_ucb_yes, NOT 1 - q_lcb_yes.
    q_yes_point, q_lcb_yes = 0.62, 0.50
    q_no_point, q_lcb_no = 0.38, 0.20  # 1 - 0.62 = 0.38 ; q_lcb_no independent

    yes_proof = _proof(
        direction="buy_yes",
        row=row,
        token_id="yes-1",
        q_posterior=q_yes_point,
        q_lcb_5pct=q_lcb_yes,
        execution_price=_priced(row, token_id="yes-1", direction="buy_yes"),
    )
    no_proof = _proof(
        direction="buy_no",
        row=row,
        token_id="no-1",
        q_posterior=q_no_point,
        q_lcb_5pct=q_lcb_no,
        execution_price=_priced(row, token_id="no-1", direction="buy_no"),
        same_bin_yes_posterior=q_yes_point,
    )

    yes_cand = era._native_side_candidate_from_proof(family_key="family-1", proof=yes_proof)
    no_cand = era._native_side_candidate_from_proof(family_key="family-1", proof=no_proof)

    assert isinstance(yes_cand, NativeSideCandidate)
    assert isinstance(no_cand, NativeSideCandidate)

    # DIRECTION LAW mapping: buy_yes -> side YES, buy_no -> side NO.
    assert yes_cand.side == "YES"
    assert no_cand.side == "NO"
    assert yes_cand.is_tradeable
    assert no_cand.is_tradeable

    # Same bin, different native tokens.
    assert yes_cand.bin_id == no_cand.bin_id
    assert yes_cand.token_id == "yes-1"
    assert no_cand.token_id == "no-1"

    # The NO candidate carries its OWN NO executable cost curve (side=='NO'),
    # walking the NO ask book — never the YES book / a YES complement.
    assert isinstance(no_cand.executable_cost_curve, ExecutableCostCurve)
    assert no_cand.executable_cost_curve.side == "NO"
    assert isinstance(yes_cand.executable_cost_curve, ExecutableCostCurve)
    assert yes_cand.executable_cost_curve.side == "YES"
    no_prices = sorted(float(lvl.price) for lvl in no_cand.executable_cost_curve.levels)
    assert no_prices == [0.55]  # the NO ask book, not 1 - 0.40 = 0.60

    # The NO candidate carries its OWN native NO q authority (Hidden #1/#3).
    assert no_cand.q_point == pytest.approx(q_no_point)
    assert no_cand.q_lcb == pytest.approx(q_lcb_no)
    # NO-lcb correctness: q_lcb_no is NOT 1 - q_lcb_yes (the point-complement
    # intuition the spec forbids). 1 - q_lcb_yes = 0.50; q_lcb_no = 0.20.
    assert no_cand.q_lcb != pytest.approx(1.0 - q_lcb_yes)


# ===========================================================================
# Named invariant 2 — missing native NO quote is no-trade, not complement (§12.A.2, §4)
# ===========================================================================
def test_missing_native_no_quote_is_no_trade_not_complement():
    """A bin whose NO token has no executable ask yields a NATIVE_QUOTE_MISSING
    candidate; no path reads a YES-derived price into it (§12.A.2, §4).

    The NO side has an empty ask book. The proof path prices it as no-trade
    (execution_price=None, native_quote_available=False). The materialized
    NativeSideCandidate is a NATIVE_QUOTE_MISSING no-trade candidate carrying NO
    executable curve and NO probability authority — there is nothing to
    complement-substitute from.
    """
    row = _row(yes_asks=(("0.40", "1000"),), no_asks=())  # empty NO book

    # The proof path itself raises when pricing the NO side -> the proof carries
    # execution_price=None / native_quote_available=False (the upstream no-trade).
    with pytest.raises(ValueError):
        _priced(row, token_id="no-1", direction="buy_no")

    no_proof = _proof(
        direction="buy_no",
        row=row,
        token_id="no-1",
        q_posterior=0.38,
        q_lcb_5pct=0.20,
        execution_price=None,
        native_quote_available=False,
        missing_reason="EXECUTABLE_NATIVE_ASK_MISSING:no_asks_empty",
    )
    no_cand = era._native_side_candidate_from_proof(family_key="family-1", proof=no_proof)

    assert isinstance(no_cand, NativeSideCandidate)
    assert no_cand.side == "NO"
    assert not no_cand.is_tradeable
    assert no_cand.no_trade_reason == CandidateNoTradeReason.NATIVE_QUOTE_MISSING
    # No executable curve and NO probability authority — nothing to complement.
    assert no_cand.executable_cost_curve is None
    assert no_cand.q_point is None
    assert no_cand.q_lcb is None
    assert no_cand.probability_uncertainty is None

    # The YES side of the SAME bin is unaffected (prices from yes_asks, §4).
    yes_proof = _proof(
        direction="buy_yes",
        row=row,
        token_id="yes-1",
        q_posterior=0.62,
        q_lcb_5pct=0.50,
        execution_price=_priced(row, token_id="yes-1", direction="buy_yes"),
    )
    yes_cand = era._native_side_candidate_from_proof(family_key="family-1", proof=yes_proof)
    assert yes_cand.is_tradeable
    assert yes_cand.executable_cost_curve.side == "YES"


def test_missing_native_no_token_is_native_token_missing_no_trade():
    """A bin with no NO token id at all -> NATIVE_TOKEN_MISSING no-trade (§13)."""
    row = _row(yes_asks=(("0.40", "1000"),), no_asks=(("0.55", "1000"),))
    no_proof = _proof(
        direction="buy_no",
        row=row,
        token_id="",  # missing native token id
        q_posterior=0.38,
        q_lcb_5pct=0.20,
        execution_price=None,
        native_quote_available=False,
        missing_reason="NATIVE_TOKEN_MISSING",
    )
    no_cand = era._native_side_candidate_from_proof(family_key="family-1", proof=no_proof)
    assert not no_cand.is_tradeable
    assert no_cand.no_trade_reason == CandidateNoTradeReason.NATIVE_TOKEN_MISSING
    assert no_cand.executable_cost_curve is None


# ===========================================================================
# Named invariant 3 — selected_token_identity differs by side (§12.A.4)
# ===========================================================================
def test_selected_token_identity_differs_by_side():
    """Same bin YES vs NO yields different selected_token_identity tuples (§12.A.4).

    Different native token => different executable snapshot leg. The
    selected_token_identity (token_id, side, market_snapshot_id) must differ so a
    downstream executable-snapshot hash differs between the two sides and a cached
    score for one side can never authorize the other.
    """
    row = _row(yes_asks=(("0.40", "1000"),), no_asks=(("0.55", "1000"),))
    yes_cand = era._native_side_candidate_from_proof(
        family_key="family-1",
        proof=_proof(
            direction="buy_yes",
            row=row,
            token_id="yes-1",
            q_posterior=0.62,
            q_lcb_5pct=0.50,
            execution_price=_priced(row, token_id="yes-1", direction="buy_yes"),
        ),
    )
    no_cand = era._native_side_candidate_from_proof(
        family_key="family-1",
        proof=_proof(
            direction="buy_no",
            row=row,
            token_id="no-1",
            q_posterior=0.38,
            q_lcb_5pct=0.20,
            execution_price=_priced(row, token_id="no-1", direction="buy_no"),
            same_bin_yes_posterior=0.62,
        ),
    )
    yes_id = yes_cand.selected_token_identity()
    no_id = no_cand.selected_token_identity()
    assert yes_id != no_id
    # Differs specifically in token and side; same snapshot row.
    assert yes_id[0] != no_id[0]  # token_id
    assert yes_id[1] == "YES" and no_id[1] == "NO"  # side
    assert yes_id[2] == no_id[2]  # same market_snapshot_id (same row)


# ===========================================================================
# Money-path iron-law invariants the S3 seam must preserve
# ===========================================================================
def test_direction_law_side_agrees_with_direction_at_materialization():
    """DIRECTION LAW: side=='YES' iff buy_yes (own bin is WIN), side=='NO' iff
    buy_no (own bin is LOSE). The mapping is never inverted at materialization.
    """
    row = _row()
    yes_cand = era._native_side_candidate_from_proof(
        family_key="f",
        proof=_proof(
            direction="buy_yes",
            row=row,
            token_id="yes-1",
            q_posterior=0.62,
            q_lcb_5pct=0.50,
            execution_price=_priced(row, token_id="yes-1", direction="buy_yes"),
        ),
    )
    no_cand = era._native_side_candidate_from_proof(
        family_key="f",
        proof=_proof(
            direction="buy_no",
            row=row,
            token_id="no-1",
            q_posterior=0.38,
            q_lcb_5pct=0.20,
            execution_price=_priced(row, token_id="no-1", direction="buy_no"),
            same_bin_yes_posterior=0.62,
        ),
    )
    assert era._native_curve_side_for_direction("buy_yes") == "YES" == yes_cand.side
    assert era._native_curve_side_for_direction("buy_no") == "NO" == no_cand.side


def test_q_lcb_le_q_point_at_candidate_boundary():
    """ROBUST-LOWER-BOUND: at every tradeable candidate boundary q_lcb <= q_point.

    A lower-confidence bound that exceeds the point estimate is the edge_ci_lower-
    as-q_lcb confusion (Hidden #2). The NativeSideCandidate constructor enforces
    this; materialization must feed q_lcb (q_lcb_5pct), never q_point, as the
    lower bound — and never edge_ci_lower.
    """
    row = _row()
    cand = era._native_side_candidate_from_proof(
        family_key="f",
        proof=_proof(
            direction="buy_yes",
            row=row,
            token_id="yes-1",
            q_posterior=0.62,
            q_lcb_5pct=0.50,
            execution_price=_priced(row, token_id="yes-1", direction="buy_yes"),
        ),
    )
    assert cand.q_lcb <= cand.q_point
    # The candidate carries the proof's S2 q_lcb authority verbatim (not q_point).
    assert cand.q_lcb == pytest.approx(0.50)
    assert cand.q_point == pytest.approx(0.62)


def test_no_candidate_priced_only_from_its_own_no_ask_book():
    """NATIVE EXECUTABLE SEPARATION: a NO candidate's curve.side always matches
    side=='NO' and traces to no_asks; no path builds NO price as 1 - p_exec(YES).
    """
    # YES top ask 0.40 (=> 1-YES complement would be 0.60); NO top ask is 0.55.
    row = _row(yes_asks=(("0.40", "1000"),), no_asks=(("0.55", "1000"),))
    no_cand = era._native_side_candidate_from_proof(
        family_key="f",
        proof=_proof(
            direction="buy_no",
            row=row,
            token_id="no-1",
            q_posterior=0.38,
            q_lcb_5pct=0.20,
            execution_price=_priced(row, token_id="no-1", direction="buy_no"),
            same_bin_yes_posterior=0.62,
        ),
    )
    curve = no_cand.executable_cost_curve
    assert curve.side == "NO" == no_cand.side
    # The buy_no execution_price traces to no_asks (~0.55), not 1 - 0.40 = 0.60.
    ep = curve.avg_cost_for_shares(curve.min_order_size)
    assert float(ep) == pytest.approx(0.55, abs=1e-9)
    assert float(ep) != pytest.approx(0.60, abs=1e-6)  # NOT 1 - YES


def test_curve_side_mismatch_is_unconstructable():
    """A YES-tagged curve fed to a NO candidate raises (complement pricing is
    unconstructable). The materialization always tags the curve with the proof's
    own native side, so this can only happen on a routing bug — and it hard-fails.
    """
    row = _row()
    yes_curve = era._native_side_cost_curve_from_snapshot_row(
        row, side="YES", token_id="yes-1"
    )
    with pytest.raises(ValueError):
        NativeSideCandidate.tradeable(
            family_key="f",
            bin_id="b",
            side="NO",  # NO candidate
            token_id="no-1",
            condition_id="condition-1",
            q_point=0.38,
            q_lcb=0.20,
            probability_uncertainty=None,
            executable_cost_curve=yes_curve,  # YES curve -> mismatch
            forecast_snapshot_id="snap-s3",
            market_snapshot_id="snap-s3",
            hypothesis_id="h",
        )


def test_no_runtime_flag_routes_materialization():
    """SINGLE PATH: _native_side_candidate_from_proof has no env/settings toggle.

    The materialization is one path; no ZEUS_* env var or edli setting can
    route it to an alternate candidate shape. We assert the source carries no
    os.environ / settings lookup inside the helper body.
    """
    import inspect

    src = inspect.getsource(era._native_side_candidate_from_proof)
    assert "os.environ" not in src
    assert "settings[" not in src
    assert "_enabled(" not in src
