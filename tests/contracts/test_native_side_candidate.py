# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §12.A (native side economics tests) + §14.2
#   (NativeSideCandidate dataclass) + §4 (native YES/NO separation) + Hidden #1/#4
#   + operator directive 2026-06-08. Relationship antibody for
#   src/contracts/native_side_candidate.py.
"""NativeSideCandidate type-level RELATIONSHIP tests (spec §12.A).

These test the TYPE in isolation (no DB, no scanner, no live decision path).
They pin the structural invariants that make the spec's §4 native YES/NO
separation laws UNCONSTRUCTABLE at the candidate boundary:

  - §12.A.1 test_yes_no_native_quotes_are_independent:
        YES ask 0.42, NO ask 0.61 — nothing assumes NO = 1 - YES in
        EXECUTABLE space. The two sides are separate tokens with separate
        executable cost curves.
  - §12.A.2 test_missing_no_quote_blocks_buy_no:
        native NO token exists but no executable ask -> a NO-TRADE candidate
        carrying a CandidateNoTradeReason, NOT a YES-complement price.
  - §12.A.4 test_selected_token_hash_changes_with_side:
        same bin, YES vs NO selected token -> different snapshot identity.

The candidate is a DEFAULT-OFF / shadow contract object: importing it and
constructing it changes no live trading behavior. The peer objects
ProbabilityUncertainty and ExecutableCostCurve are referenced only via
forward refs / TYPE_CHECKING, so this test (and the module) import cleanly
even if those peers land slightly later.
"""
from __future__ import annotations

import pytest

from src.contracts.native_side_candidate import (
    CandidateNoTradeReason,
    NativeSideCandidate,
    SideProbability,
)


# ---------------------------------------------------------------------------
# Lightweight stand-ins for the peer objects being built in parallel.
# The candidate stores them opaquely; it must not assume YES/NO complement
# relationships across them. A simple stub with the attributes the candidate
# reads (token identity, executable ask presence) is enough to pin the
# relationship invariants WITHOUT importing the real peers (which may not
# exist yet at test time).
# ---------------------------------------------------------------------------
class _StubCostCurve:
    """Minimal ExecutableCostCurve stand-in.

    ``has_executable_ask`` is the only structural fact the candidate
    constructor needs in order to decide tradeable vs no-trade. A real
    ExecutableCostCurve exposes the same boolean once the peer lands.
    """

    def __init__(self, *, token_id: str, side: str, top_ask: float | None) -> None:
        self.token_id = token_id
        self.side = side
        self.top_ask = top_ask

    @property
    def has_executable_ask(self) -> bool:
        return self.top_ask is not None


def _make_side_probability(point: float, lcb: float) -> SideProbability:
    return SideProbability(side="YES", q_point=point, q_lcb=lcb)


# ===========================================================================
# §12.A.1 — YES and NO native quotes are independent (NO != 1 - YES in
#           executable space).
# ===========================================================================
def test_yes_no_native_quotes_are_independent() -> None:
    yes_curve = _StubCostCurve(token_id="tok-yes-bin3", side="YES", top_ask=0.42)
    no_curve = _StubCostCurve(token_id="tok-no-bin3", side="NO", top_ask=0.61)

    yes = NativeSideCandidate.tradeable(
        family_key="NYC|2026-06-10|tmax",
        bin_id="bin3",
        side="YES",
        token_id="tok-yes-bin3",
        condition_id="cond-bin3",
        q_point=0.50,
        q_lcb=0.44,
        probability_uncertainty=None,
        executable_cost_curve=yes_curve,
        forecast_snapshot_id="fc-snap-1",
        market_snapshot_id="mkt-snap-yes-bin3",
        hypothesis_id="hyp-yes-bin3",
    )
    no = NativeSideCandidate.tradeable(
        family_key="NYC|2026-06-10|tmax",
        bin_id="bin3",
        side="NO",
        token_id="tok-no-bin3",
        condition_id="cond-bin3",
        q_point=0.50,
        q_lcb=0.41,
        probability_uncertainty=None,
        executable_cost_curve=no_curve,
        forecast_snapshot_id="fc-snap-1",
        market_snapshot_id="mkt-snap-no-bin3",
        hypothesis_id="hyp-no-bin3",
    )

    # Both sides are tradeable, distinct, native candidates.
    assert yes.is_tradeable and no.is_tradeable
    assert yes.no_trade_reason is None and no.no_trade_reason is None

    # The candidate stores each side's OWN executable curve verbatim; it does
    # NOT synthesize the NO ask as 1 - YES ask. The asks are independent.
    assert yes.executable_cost_curve.top_ask == 0.42
    assert no.executable_cost_curve.top_ask == 0.61
    # The relationship that MUST NOT hold: NO ask == 1 - YES ask.
    assert no.executable_cost_curve.top_ask != pytest.approx(
        1.0 - yes.executable_cost_curve.top_ask
    )

    # Native token identity differs per side — neither borrows the other's token.
    assert yes.token_id != no.token_id


# ===========================================================================
# §12.A.2 / Hidden #4 — missing native NO executable quote blocks buy-NO with
#           a no-trade candidate, NEVER a YES-complement price.
# ===========================================================================
def test_missing_no_quote_blocks_buy_no() -> None:
    # Native NO token EXISTS, but its executable ask is missing (top_ask=None).
    no_curve = _StubCostCurve(token_id="tok-no-bin3", side="NO", top_ask=None)

    cand = NativeSideCandidate.tradeable(
        family_key="NYC|2026-06-10|tmax",
        bin_id="bin3",
        side="NO",
        token_id="tok-no-bin3",
        condition_id="cond-bin3",
        q_point=0.50,
        q_lcb=0.41,
        probability_uncertainty=None,
        executable_cost_curve=no_curve,
        forecast_snapshot_id="fc-snap-1",
        market_snapshot_id="mkt-snap-no-bin3",
        hypothesis_id="hyp-no-bin3",
    )

    # The result is a NO-TRADE candidate, not a complement-priced tradeable one.
    assert not cand.is_tradeable
    assert cand.no_trade_reason is CandidateNoTradeReason.NATIVE_QUOTE_MISSING
    # No executable curve is attached when the native ask is missing — there is
    # nothing to complement-substitute from.
    assert cand.executable_cost_curve is None


def test_missing_no_token_blocks_buy_no() -> None:
    # Native NO token identity ITSELF is absent (empty token_id). This is a
    # distinct no-trade reason from "token present, quote missing".
    cand = NativeSideCandidate.no_trade(
        family_key="NYC|2026-06-10|tmax",
        bin_id="bin3",
        side="NO",
        token_id="",
        condition_id="cond-bin3",
        forecast_snapshot_id="fc-snap-1",
        market_snapshot_id="mkt-snap-no-bin3",
        reason=CandidateNoTradeReason.NATIVE_TOKEN_MISSING,
    )

    assert not cand.is_tradeable
    assert cand.no_trade_reason is CandidateNoTradeReason.NATIVE_TOKEN_MISSING
    assert cand.executable_cost_curve is None
    # A no-trade candidate carries no probability authority — there is no
    # native belief to size on.
    assert cand.q_point is None
    assert cand.q_lcb is None


def test_complement_price_cannot_construct_a_no_candidate() -> None:
    """A NO candidate must NOT be buildable from a YES-derived complement price.

    The factory refuses to accept a YES-token curve for a NO-side candidate:
    the curve's side must match the candidate's side. This makes "borrow the
    YES book to price NO" UNCONSTRUCTABLE at the type boundary (§4 executable
    space law: p_exec(NO) != 1 - p_exec(YES)).
    """
    yes_curve = _StubCostCurve(token_id="tok-yes-bin3", side="YES", top_ask=0.42)
    with pytest.raises(ValueError):
        NativeSideCandidate.tradeable(
            family_key="NYC|2026-06-10|tmax",
            bin_id="bin3",
            side="NO",  # NO candidate...
            token_id="tok-no-bin3",
            condition_id="cond-bin3",
            q_point=0.50,
            q_lcb=0.41,
            probability_uncertainty=None,
            executable_cost_curve=yes_curve,  # ...fed a YES-side curve.
            forecast_snapshot_id="fc-snap-1",
            market_snapshot_id="mkt-snap-no-bin3",
            hypothesis_id="hyp-no-bin3",
        )


# ===========================================================================
# §12.A.4 — the selected-token snapshot identity differs between YES and NO of
#           the same bin.
# ===========================================================================
def test_selected_token_hash_changes_with_side() -> None:
    yes_curve = _StubCostCurve(token_id="tok-yes-bin3", side="YES", top_ask=0.42)
    no_curve = _StubCostCurve(token_id="tok-no-bin3", side="NO", top_ask=0.61)

    common = dict(
        family_key="NYC|2026-06-10|tmax",
        bin_id="bin3",
        condition_id="cond-bin3",
        q_point=0.50,
        q_lcb=0.44,
        probability_uncertainty=None,
        forecast_snapshot_id="fc-snap-1",
    )

    yes = NativeSideCandidate.tradeable(
        side="YES",
        token_id="tok-yes-bin3",
        executable_cost_curve=yes_curve,
        market_snapshot_id="mkt-snap-yes-bin3",
        hypothesis_id="hyp-yes-bin3",
        **common,
    )
    no = NativeSideCandidate.tradeable(
        side="NO",
        token_id="tok-no-bin3",
        executable_cost_curve=no_curve,
        market_snapshot_id="mkt-snap-no-bin3",
        hypothesis_id="hyp-no-bin3",
        **common,
    )

    # Same bin, same family — but the SELECTED-token snapshot identity must
    # differ between the YES and NO side (different token => different snapshot).
    assert yes.bin_id == no.bin_id
    assert yes.family_key == no.family_key
    assert yes.selected_token_identity() != no.selected_token_identity()
    # The identity is derived from (token_id, side, market_snapshot_id) — change
    # any of them and the identity changes; this is what a downstream snapshot
    # hash keys on.
    assert yes.selected_token_identity() == NativeSideCandidate.tradeable(
        side="YES",
        token_id="tok-yes-bin3",
        executable_cost_curve=yes_curve,
        market_snapshot_id="mkt-snap-yes-bin3",
        hypothesis_id="hyp-yes-bin3",
        **common,
    ).selected_token_identity()


# ===========================================================================
# Structural hygiene — frozenness, side validation, q ordering.
# ===========================================================================
def test_candidate_is_frozen() -> None:
    curve = _StubCostCurve(token_id="tok-yes-bin3", side="YES", top_ask=0.42)
    cand = NativeSideCandidate.tradeable(
        family_key="NYC|2026-06-10|tmax",
        bin_id="bin3",
        side="YES",
        token_id="tok-yes-bin3",
        condition_id="cond-bin3",
        q_point=0.50,
        q_lcb=0.44,
        probability_uncertainty=None,
        executable_cost_curve=curve,
        forecast_snapshot_id="fc-snap-1",
        market_snapshot_id="mkt-snap-yes-bin3",
        hypothesis_id="hyp-yes-bin3",
    )
    with pytest.raises(Exception):
        cand.q_point = 0.99  # type: ignore[misc]


def test_side_must_be_yes_or_no() -> None:
    curve = _StubCostCurve(token_id="tok-x", side="MAYBE", top_ask=0.42)
    with pytest.raises(ValueError):
        NativeSideCandidate.tradeable(
            family_key="NYC|2026-06-10|tmax",
            bin_id="bin3",
            side="MAYBE",  # not YES/NO
            token_id="tok-x",
            condition_id="cond-bin3",
            q_point=0.50,
            q_lcb=0.44,
            probability_uncertainty=None,
            executable_cost_curve=curve,
            forecast_snapshot_id="fc-snap-1",
            market_snapshot_id="mkt-snap-x",
            hypothesis_id="hyp-x",
        )


def test_q_lcb_cannot_exceed_q_point() -> None:
    """q_lcb is a LOWER confidence bound; it must be <= q_point (Hidden #2).

    This pins the §5.6 / Hidden #2 estimator invariant at the candidate
    boundary: a lower-confidence probability that exceeds the point estimate
    is a sign that edge_ci_lower has masqueraded as q_lcb.
    """
    curve = _StubCostCurve(token_id="tok-yes-bin3", side="YES", top_ask=0.42)
    with pytest.raises(ValueError):
        NativeSideCandidate.tradeable(
            family_key="NYC|2026-06-10|tmax",
            bin_id="bin3",
            side="YES",
            token_id="tok-yes-bin3",
            condition_id="cond-bin3",
            q_point=0.40,
            q_lcb=0.55,  # > q_point — forbidden
            probability_uncertainty=None,
            executable_cost_curve=curve,
            forecast_snapshot_id="fc-snap-1",
            market_snapshot_id="mkt-snap-yes-bin3",
            hypothesis_id="hyp-yes-bin3",
        )


# ===========================================================================
# SideProbability carrier — native side belief, not a cross-space float.
# ===========================================================================
def test_side_probability_carries_native_side() -> None:
    sp = _make_side_probability(point=0.50, lcb=0.44)
    assert sp.side == "YES"
    assert sp.q_point == 0.50
    assert sp.q_lcb == 0.44


def test_side_probability_no_lcb_is_not_complement_of_yes_lcb() -> None:
    """SideProbability for NO is NOT 1 - YES lcb (§4 / Hidden #3).

    The carrier stores an independently-supplied NO lcb. Constructing a NO
    SideProbability from YES samples by point-complementing the lcb is the
    error this carrier exists to prevent — the lower tail of (1 - q_yes) is
    the UPPER tail of q_yes, not 1 - lower_tail(q_yes).
    """
    yes = SideProbability(side="YES", q_point=0.70, q_lcb=0.60)
    # An honest NO carrier: q_no point = 1 - 0.70 = 0.30, but its lcb comes
    # from the complement SAMPLES (here 0.18), NOT 1 - yes.q_lcb (= 0.40).
    no = SideProbability(side="NO", q_point=0.30, q_lcb=0.18)
    assert no.q_lcb != pytest.approx(1.0 - yes.q_lcb)
    assert no.q_lcb <= no.q_point


def test_side_probability_lcb_cannot_exceed_point() -> None:
    with pytest.raises(ValueError):
        SideProbability(side="NO", q_point=0.30, q_lcb=0.45)


def test_tradeable_from_real_executable_cost_curve_is_tradeable():
    """Integration relationship test (Phase-1↔Phase-3 boundary): a candidate
    built from the REAL ExecutableCostCurve peer (which exposes executability via
    a non-empty ``levels`` ladder, not has_executable_ask/top_ask) must be
    tradeable. Antibody for the parallel-build mismatch where _curve_has_executable_ask
    recognized only the stub surface and silently downgraded every real,
    fully-executable curve to NATIVE_QUOTE_MISSING."""
    from decimal import Decimal
    from datetime import timedelta
    from src.contracts.executable_cost_curve import (
        ExecutableCostCurve,
        BookLevel,
        FeeModel,
    )

    real_no_curve = ExecutableCostCurve(
        token_id="tok-no-bin3",
        side="NO",
        snapshot_id="snap-no",
        book_hash="hash-no",
        levels=(
            BookLevel(price=Decimal("0.61"), size=Decimal("500")),
            BookLevel(price=Decimal("0.62"), size=Decimal("500")),
        ),
        fee_model=FeeModel(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )

    cand = NativeSideCandidate.tradeable(
        family_key="NYC|2026-06-10|tmax",
        bin_id="bin3",
        side="NO",
        token_id="tok-no-bin3",
        condition_id="cond-bin3",
        q_point=0.50,
        q_lcb=0.41,
        probability_uncertainty=None,
        executable_cost_curve=real_no_curve,
        forecast_snapshot_id="fc-snap-1",
        market_snapshot_id="mkt-snap-no-bin3",
        hypothesis_id="hyp-no-bin3",
    )

    assert cand.is_tradeable, f"real executable curve wrongly downgraded: {cand.no_trade_reason}"
    assert cand.no_trade_reason is None
    assert cand.executable_cost_curve is real_no_curve
