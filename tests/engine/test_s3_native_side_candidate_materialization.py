# Lifecycle: created=2026-06-08; last_reviewed=2026-07-17; last_reused=2026-07-17
# Purpose: Prove one native YES/NO candidate shape and its callback contracts.
# Reuse: Re-audit identity, full-depth, and call-shape seams before auction changes.
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

import ast
import inspect
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.contracts.executable_cost_curve import (
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
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


def _execution_conn(row: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE executable_market_snapshots ("
        "snapshot_id TEXT PRIMARY KEY, condition_id TEXT, "
        "selected_outcome_token_id TEXT, yes_token_id TEXT, no_token_id TEXT, "
        "captured_at TEXT, freshness_deadline TEXT, enable_orderbook INTEGER, "
        "active INTEGER, closed INTEGER, accepting_orders INTEGER, "
        "min_tick_size TEXT, min_order_size TEXT, fee_details_json TEXT, "
        "neg_risk INTEGER, orderbook_depth_json TEXT, "
        "tradeability_status_json TEXT, book_hash TEXT)"
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES "
        "(:snapshot_id, :condition_id, :selected_outcome_token_id, "
        ":yes_token_id, :no_token_id, :captured_at, :freshness_deadline, "
        ":enable_orderbook, :active, :closed, :accepting_orders, "
        ":min_tick_size, :min_order_size, :fee_details_json, :neg_risk, "
        ":orderbook_depth_json, :tradeability_status_json, :book_hash)",
        row,
    )
    return conn


def test_current_global_execution_authority_uses_latest_full_native_ladder():
    row = {
        **_row(
            yes_asks=(("0.400", "5"), ("0.410", "20")),
            snapshot_id="snap-current",
        ),
        "selected_outcome_token_id": "yes-1",
        "captured_at": "2026-07-10T09:59:59+00:00",
        "freshness_deadline": "2026-07-10T10:01:00+00:00",
        "enable_orderbook": 1,
        "active": 1,
        "closed": 0,
        "accepting_orders": 1,
    }
    conn = _execution_conn(row)
    candidate = SimpleNamespace(
        condition_id="condition-1",
        token_id="yes-1",
        side="YES",
    )
    decision_time = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)

    current = era.current_global_execution_authority(
        conn,
        candidate,
        decision_time=decision_time,
    )
    expected_curve = era._native_side_cost_curve_from_snapshot_row(
        row,
        side="YES",
        token_id="yes-1",
    )
    from src.solve.solver import executable_curve_identity

    assert current is not None
    assert current.book_snapshot_id == "snap-current"
    assert current.execution_curve_identity == executable_curve_identity(expected_curve)

    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "snap-closed",
            "condition-1",
            "yes-1",
            "yes-1",
            "no-1",
            "2026-07-10T10:00:00+00:00",
            "2026-07-10T10:01:00+00:00",
            1,
            1,
            1,
            0,
            row["min_tick_size"],
            row["min_order_size"],
            row["fee_details_json"],
            row["neg_risk"],
            row["orderbook_depth_json"],
            row["tradeability_status_json"],
            row["book_hash"],
        ),
    )
    assert era.current_global_execution_authority(
        conn,
        candidate,
        decision_time=decision_time,
    ) is None
    conn.close()


def test_native_side_curve_preserves_gamma_fee_provenance_for_submit_recapture():
    row = _row(fee_rate_fraction=0.05)
    fee_details = {
        "fee_rate_fraction": 0.05,
        "fee_rate_bps": 500.0,
        "fee_rate_source_field": "fee_rate_fraction",
        "feeSchedule_taker_only": True,
        "source": "gamma_fee_schedule_family_cache",
        "token_id": "yes-1",
    }
    row["fee_details_json"] = json.dumps(fee_details)

    curve = era._native_side_cost_curve_from_snapshot_row(
        row,
        side="YES",
        token_id="yes-1",
    )

    assert curve.fee_model.fee_rate == Decimal("0.05")
    assert curve.fee_details == fee_details


def _jit_curve(
    *,
    side: str,
    token_id: str,
    snapshot_id: str,
    book_hash: str,
    levels: tuple[tuple[str, str], ...],
    fee_rate: str = "0",
    min_tick: str = "0.01",
    min_order_size: str = "5",
) -> ExecutableCostCurve:
    return ExecutableCostCurve(
        token_id=token_id,
        side=side,
        snapshot_id=snapshot_id,
        book_hash=book_hash,
        levels=tuple(
            BookLevel(price=Decimal(price), size=Decimal(size))
            for price, size in levels
        ),
        fee_model=FeeModel(fee_rate=Decimal(fee_rate)),
        min_tick=Decimal(min_tick),
        min_order_size=Decimal(min_order_size),
        quote_ttl=timedelta(seconds=30),
    )


def _jit_decision(curve: ExecutableCostCurve, shares: str) -> SimpleNamespace:
    size = Decimal(shares)
    remaining = size
    raw_cost = Decimal("0")
    all_in_cost = Decimal("0")
    limit = Decimal("0")
    for level in curve.levels:
        take = min(level.size, remaining)
        if take > 0:
            limit = level.price
            raw_cost += take * level.price
            all_in_cost += take * curve.fee_model.all_in_price(level.price)
            remaining -= take
        if remaining <= Decimal("1e-18"):
            break
    assert remaining <= Decimal("1e-18")
    return SimpleNamespace(
        candidate=SimpleNamespace(executable_cost_curve=curve),
        shares=size,
        cost_usd=all_in_cost,
        limit_price=limit,
        expected_fill_price_before_fee=raw_cost / size,
        max_spend_usd=size * curve.fee_model.all_in_price(limit),
    )


def test_opportunity_book_projects_exact_global_selected_proof():
    row = _row()
    local = _proof(
        direction="buy_yes",
        row=row,
        token_id="yes-1",
        q_posterior=0.70,
        q_lcb_5pct=0.60,
        execution_price=_priced(row, token_id="yes-1", direction="buy_yes"),
    )
    global_selected = replace(
        local,
        qkernel_execution_economics={"global_actuation_identity": "global-1"},
        selection_authority_applied="qkernel_spine",
    )

    assert era._opportunity_book_proofs_with_global_selected_authority(
        (local,),
        global_selected,
    ) == (global_selected,)
    assert era._opportunity_book_proofs_with_global_selected_authority(
        (local,),
        local,
    ) == (local,)


@pytest.mark.parametrize(
    ("side", "token_id", "touch"),
    (("YES", "yes-1", "0.40"), ("NO", "no-1", "0.55")),
)
def test_global_jit_curve_allows_evidence_carrier_churn_only(
    side: str,
    token_id: str,
    touch: str,
):
    selected = _jit_curve(
        side=side,
        token_id=token_id,
        snapshot_id="selected",
        book_hash="selected-book",
        levels=((touch, "5"), ("0.70", "100")),
        fee_rate="0.02",
    )
    current = _jit_curve(
        side=side,
        token_id=token_id,
        snapshot_id="jit",
        book_hash="jit-book",
        levels=((touch, "5"), ("0.70", "100")),
        fee_rate="0.02",
    )

    assert era._global_selected_order_economics_preserved(
        decision=_jit_decision(selected, "5"),
        current_candidate=SimpleNamespace(executable_cost_curve=current),
    )


@pytest.mark.parametrize(
    ("side", "token_id", "touch"),
    (("YES", "yes-1", "0.40"), ("NO", "no-1", "0.55")),
)
def test_global_jit_curve_rejects_unconsumed_tail_drift(
    side: str,
    token_id: str,
    touch: str,
):
    selected = _jit_curve(
        side=side,
        token_id=token_id,
        snapshot_id="selected",
        book_hash="selected-book",
        levels=((touch, "5"), ("0.70", "100")),
        fee_rate="0.02",
    )
    current = _jit_curve(
        side=side,
        token_id=token_id,
        snapshot_id="jit",
        book_hash="jit-book",
        levels=((touch, "5"), ("0.80", "99")),
        fee_rate="0.02",
    )

    drift = era._global_selected_order_economics_drift(
        decision=_jit_decision(selected, "5"),
        current_candidate=SimpleNamespace(executable_cost_curve=current),
    )

    assert drift == "fields=levels"


@pytest.mark.parametrize(
    ("side", "token_id", "touch"),
    (("YES", "yes-1", "0.40"), ("NO", "no-1", "0.55")),
)
@pytest.mark.parametrize(
    "drift",
    (
        "selected_price",
        "selected_size",
        "insufficient_depth",
        "fee",
        "tick",
        "min_order",
    ),
)
def test_global_jit_curve_rejects_selected_order_economic_drift(
    side: str,
    token_id: str,
    touch: str,
    drift: str,
):
    selected = _jit_curve(
        side=side,
        token_id=token_id,
        snapshot_id="selected",
        book_hash="selected-book",
        levels=((touch, "5"), ("0.70", "100")),
    )
    current_kwargs = {
        "side": side,
        "token_id": token_id,
        "snapshot_id": "jit",
        "book_hash": f"jit-{drift}",
        "levels": ((touch, "5"), ("0.70", "100")),
        "fee_rate": "0",
        "min_tick": "0.01",
        "min_order_size": "5",
    }
    if drift == "selected_price":
        current_kwargs["levels"] = (
            (str(Decimal(touch) + Decimal("0.01")), "5"),
            ("0.70", "100"),
        )
    elif drift == "selected_size":
        current_kwargs["levels"] = ((touch, "4"), ("0.70", "100"))
    elif drift == "insufficient_depth":
        current_kwargs["levels"] = ((touch, "4"),)
    elif drift == "fee":
        current_kwargs["fee_rate"] = "0.02"
    elif drift == "tick":
        current_kwargs["min_tick"] = "0.05"
    elif drift == "min_order":
        current_kwargs["min_order_size"] = "6"
    current = _jit_curve(**current_kwargs)

    decision = _jit_decision(selected, "5")
    current_candidate = SimpleNamespace(executable_cost_curve=current)
    assert not era._global_selected_order_economics_preserved(
        decision=decision,
        current_candidate=current_candidate,
    )
    assert era._global_selected_order_economics_drift(
        decision=decision,
        current_candidate=current_candidate,
    )


def test_global_jit_curve_rejects_cheaper_selected_prefix_for_reoptimization():
    selected = _jit_curve(
        side="YES",
        token_id="yes-1",
        snapshot_id="selected",
        book_hash="selected-book",
        levels=(("0.40", "10"), ("0.90", "100")),
    )
    current = _jit_curve(
        side="YES",
        token_id="yes-1",
        snapshot_id="jit",
        book_hash="jit-book",
        levels=(("0.39", "5"), ("0.40", "5"), ("0.99", "100")),
    )

    assert not era._global_selected_order_economics_preserved(
        decision=_jit_decision(selected, "10"),
        current_candidate=SimpleNamespace(executable_cost_curve=current),
    )


@pytest.mark.parametrize(
    ("current", "worsened"),
    (("0.39", False), ("0.40", False), ("0.4000000000005", False), ("0.41", True)),
)
def test_global_actuation_sweep_accepts_equal_or_cheaper_buy_cost(current, worsened):
    assert era._global_actuation_sweep_cost_worsened(
        selected="0.40",
        current=current,
    ) is worsened


def test_global_candidate_snapshot_row_targets_winner_sibling():
    rows = (
        {
            "snapshot_id": "trigger-snapshot",
            "condition_id": "trigger-condition",
            "selected_outcome_token_id": "trigger-yes",
            "yes_token_id": "trigger-yes",
            "no_token_id": "trigger-no",
            "captured_at": "2026-07-11T01:00:02+00:00",
        },
        {
            "snapshot_id": "winner-snapshot",
            "condition_id": "winner-condition",
            "selected_outcome_token_id": "winner-no",
            "yes_token_id": "winner-yes",
            "no_token_id": "winner-no",
            "captured_at": "2026-07-11T01:00:01+00:00",
        },
    )
    winner = SimpleNamespace(
        condition_id="winner-condition",
        token_id="winner-no",
        side="NO",
    )

    row = era._global_candidate_snapshot_row(rows, winner)
    assert row is not None
    assert row["snapshot_id"] == "winner-snapshot"


def test_decision_refresh_targets_selected_row_only():
    conditions, token = era._decision_snapshot_refresh_target(
        row={"condition_id": "selected-condition"},
        payload={"token_id": "selected-no"},
        family_condition_ids=("trigger-condition", "selected-condition"),
    )

    assert conditions == ("selected-condition",)
    assert token == "selected-no"


def test_global_submit_requires_complete_candidate_bound_jit_identity():
    candidate = SimpleNamespace(candidate_id="global-candidate")
    payload = {
        "qkernel_execution_economics": {
            "global_candidate_id": "global-candidate",
            "global_jit_book_hash": "jit-book",
            "global_jit_venue_book_hash": "jit-venue-book",
            "global_jit_book_snapshot_id": "jit-snapshot",
            "global_jit_execution_curve_identity": "jit-curve",
        }
    }

    assert era._global_jit_book_hash_for_submit(
        actionable_payload=payload,
        global_candidate=candidate,
    ) == "jit-venue-book"


@pytest.mark.parametrize(
    "economics",
    (
        {},
        {"global_candidate_id": "wrong"},
        {
            "global_candidate_id": "global-candidate",
            "global_jit_book_hash": "jit-book",
            "global_jit_venue_book_hash": "jit-venue-book",
            "global_jit_book_snapshot_id": "jit-snapshot",
        },
    ),
)
def test_global_submit_rebind_requires_complete_candidate_bound_jit_identity(economics):
    with pytest.raises(ValueError, match="GLOBAL_ACTUATION_JIT_BOOK"):
        era._global_jit_book_hash_for_submit(
            actionable_payload={"qkernel_execution_economics": economics},
            global_candidate=SimpleNamespace(candidate_id="global-candidate"),
        )


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

    The NO side has an empty ask book AND no complementary YES bid to quote
    behind (no maker-quote lane). The proof path prices it as no-trade
    (execution_price=None, native_quote_available=False). The materialized
    NativeSideCandidate is a NATIVE_QUOTE_MISSING no-trade candidate carrying NO
    executable curve and NO probability authority — there is nothing to
    complement-substitute from.

    NOTE (2026-06-10 maker-quote lane): empty NO ask is no longer terminal when a
    live complementary YES bid exists — that case becomes a MAKER quote
    (tests/engine/test_maker_quote_empty_no_ask.py). This test pins the FAIL-CLOSED
    end: empty NO ask AND empty YES bid -> still NATIVE_QUOTE_MISSING. The YES bid
    is therefore explicitly removed here.
    """
    row = _row(
        yes_asks=(("0.40", "1000"),), no_asks=(), yes_bids=(), no_bids=()
    )  # empty NO book AND no complementary bid -> genuine no-trade

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


def test_global_candidate_rejects_scalar_curve_fallback_without_full_depth():
    """The cross-family auction never ranks a million-share scalar fallback."""

    source_row = _row()
    price = _priced(source_row, token_id="yes-1", direction="buy_yes")
    scalar_only_row = dict(source_row)
    scalar_only_row.pop("orderbook_depth_json")
    proof = _proof(
        direction="buy_yes",
        row=scalar_only_row,
        token_id="yes-1",
        q_posterior=0.62,
        q_lcb_5pct=0.50,
        execution_price=price,
    )

    legacy = era._native_side_candidate_from_proof(family_key="f", proof=proof)
    global_candidate = era._full_depth_native_side_candidate_from_proof(
        family_key="f", proof=proof
    )

    assert legacy.no_trade_reason is None
    assert global_candidate.no_trade_reason is CandidateNoTradeReason.NATIVE_QUOTE_MISSING
    assert global_candidate.executable_cost_curve is None


def test_full_depth_builder_is_wired_only_into_global_preparation() -> None:
    """Build full depth for the auction once; actuation reuses that candidate."""

    tree = ast.parse(inspect.getsource(era))
    direct_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_full_depth_native_side_candidate_from_proof"
    ]
    assert direct_calls == []
    spine_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "decide_family_via_spine"
    ]
    callback_values = [
        keyword.value
        for call in spine_calls
        for keyword in call.keywords
        if keyword.arg == "global_native_side_candidate_from_proof"
    ]
    assert len(callback_values) == 1
    callback = callback_values[0]
    assert isinstance(callback, ast.IfExp)
    assert isinstance(callback.body, ast.Name)
    assert callback.body.id == "_full_depth_native_side_candidate_from_proof"


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
