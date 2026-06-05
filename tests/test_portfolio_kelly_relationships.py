# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: K1–K8 cross-module relationship tests for portfolio-aware (multi) Kelly sizing (#107)
# Reuse: verify sizing_context + evaluate_kelly + correlated_committed_usd are all at HEAD;
#   confirm effective_bankroll / effective_bankroll_raw formulas unchanged before relying on results
"""Relationship tests for portfolio-aware (multi) Kelly sizing — Task #107.

These are CROSS-MODULE relationship tests (Fitz methodology: test the property
that holds when one module's output flows into another), written RED-first.

The single structural decision under test: "committed capital =
correlation-weighted open+in-flight exposure", computed in
``portfolio.correlated_committed_usd`` and consumed by ``SizingContext`` /
``evaluate_kelly`` so the live EDLI reactor sizes each bet against a bankroll
NET of already-committed (correlation-weighted) capital.

Let ``size(new, held, B)`` be the portfolio-aware sized stake in USD. The
invariants:

- INV-K1  budget:        Σ simultaneous stakes ≤ B·f_cap.
- INV-K2  MECE:          same (city,date,metric) bins size with corr=1.0 —
                          the 2nd bin is strictly smaller than independent.
- INV-K3  single cap:    a single bet ≤ max_single_position_pct·B (0.10).
                          THIS IS THE HEADLINE RED→GREEN (single-Kelly = 25-27%).
- INV-K4  monotone:      adding committed capital never increases next size;
                          correlated capital strictly decreases it.
- INV-K5  corr-weighting: a correlated held position reduces size MORE than an
                          uncorrelated one of equal committed capital.
- INV-K6  fail-closed:   corr_committed ≥ B  ⇒  size 0 (never negative/NaN).
- INV-K7  in-flight:     a just-emitted (unfilled, cost_basis=0) sibling within
                          the same cycle is counted via the reservation accumulator.
- INV-K8  no-amplify:    portfolio-aware size ≤ single-Kelly size, everywhere.
"""

from __future__ import annotations

import math

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.events.money_path_adapters import evaluate_kelly
from src.sizing.sizing_context import SizingContext, effective_bankroll, effective_bankroll_raw
from src.state.portfolio import (
    PortfolioState,
    Position,
    correlated_committed_usd,
    total_exposure_usd,
)

# ── Config truth (read from settings, not hardcoded) ─────────────────────────

F_CAP = 0.25  # fractional-Kelly cap = kelly_multiplier (config/settings.json)
MAX_SINGLE_POSITION_PCT = 0.10  # max_single_position_pct (config)
BANKROLL = 170.0  # representative live bankroll (matches the ~$43=25% receipt)

# Two cities chosen for KNOWN correlation behaviour via correlation.get_correlation:
#   - same city  → corr 1.0 (MECE / self)
#   - far-apart  → haversine floor 0.10
NEAR_CITY = "New York City"
FAR_CITY = "Singapore"  # ~15000 km from NYC → haversine floor 0.10


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _kelly_safe_price(value: float = 0.50) -> ExecutionPrice:
    """A fee-adjusted execution price that passes assert_kelly_safe()."""
    return ExecutionPrice(
        value=value,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )


def _held_position(*, city: str, committed_usd: float, tid: str) -> Position:
    """A runtime-open position carrying ``committed_usd`` effective cost basis.

    state="holding" (default, NOT in INACTIVE_RUNTIME_STATES) and NOT
    pending-without-fill, so effective_cost_basis_usd returns cost_basis_usd.
    """
    p = Position(
        trade_id=tid,
        market_id=f"m_{tid}",
        city=city,
        cluster=city,  # K3 revision: cluster == city.name
        target_date="2026-06-10",
        bin_label=f"bin_{tid}",
        direction="buy_yes",
        cost_basis_usd=float(committed_usd),
        size_usd=float(committed_usd),
        state="holding",
    )
    assert p.effective_cost_basis_usd == pytest.approx(committed_usd)
    assert not p.is_pending_entry_without_fill_authority
    return p


def _pending_unfilled_position(*, city: str, committed_usd: float, tid: str) -> Position:
    """A just-emitted PENDING_TRACKED position WITHOUT fill authority.

    Models the INV-K7 hazard: effective_cost_basis_usd == 0.0 → invisible to
    load_portfolio() exposure summation until reconciled.
    """
    p = Position(
        trade_id=tid,
        market_id=f"m_{tid}",
        city=city,
        cluster=city,
        target_date="2026-06-10",
        bin_label=f"bin_{tid}",
        direction="buy_yes",
        cost_basis_usd=float(committed_usd),
        size_usd=float(committed_usd),
        state="pending_tracked",
    )
    # Confirm the hazard the reservation must compensate for:
    assert p.is_pending_entry_without_fill_authority
    assert p.effective_cost_basis_usd == 0.0
    return p


def _size(
    *,
    new_city: str,
    held: list[Position],
    bankroll: float = BANKROLL,
    p_posterior: float = 0.85,
    price: float = 0.50,
    f_cap: float = F_CAP,
    extra_reserved: list[tuple[str, float]] | None = None,
) -> float:
    """Portfolio-aware sized stake (USD) for a new bet in ``new_city``.

    Mirrors the live reactor sizing build:
      corr_committed = correlated_committed_usd(state, new_city, extra_reserved)
      raw_committed  = total_exposure_usd(state) + Σ reservation_usd
      ctx = SizingContext.from_candidate_proof_with_portfolio(...)
      kelly = evaluate_kelly(..., sizing_context=ctx)  # sizes vs effective bankroll
    """
    state = PortfolioState(positions=list(held))
    corr_committed = correlated_committed_usd(
        state, new_city=new_city, extra_reserved=extra_reserved
    )
    # INV-K1b: absolute raw deployed (no correlation discount) + same-cycle reservation.
    raw_committed = total_exposure_usd(state) + sum(
        float(usd) for _, usd in (extra_reserved or [])
    )
    ctx = SizingContext.from_candidate_proof_with_portfolio(
        q_posterior=p_posterior,
        q_lcb_5pct=p_posterior - 0.01,  # tight CI so the CI haircut isn't what drives the test
        lead_days=1.0,
        bankroll_usd=bankroll,
        corr_committed_usd=corr_committed,
        raw_committed_usd=raw_committed,
    )
    proof = evaluate_kelly(
        kelly_decision_id="k_test",
        p_posterior=p_posterior,
        execution_price=_kelly_safe_price(price),
        bankroll_usd=bankroll,
        sizing_context=ctx,
        kelly_multiplier=f_cap,
    )
    return proof.size_usd


def _single_kelly_size(
    *,
    bankroll: float = BANKROLL,
    p_posterior: float = 0.85,
    price: float = 0.50,
    f_cap: float = F_CAP,
) -> float:
    """The OLD full-bankroll single-asset Kelly size (no portfolio context)."""
    ctx = SizingContext.from_candidate_proof(
        q_posterior=p_posterior, q_lcb_5pct=p_posterior - 0.01, lead_days=1.0
    )
    proof = evaluate_kelly(
        kelly_decision_id="k_single",
        p_posterior=p_posterior,
        execution_price=_kelly_safe_price(price),
        bankroll_usd=bankroll,
        sizing_context=ctx,
        kelly_multiplier=f_cap,
    )
    return proof.size_usd


# ── INV-K1: budget — Σ simultaneous stakes ≤ B·f_cap ─────────────────────────

def test_K1_simultaneous_stakes_respect_budget():
    """Five same-cycle bets across the same family, sized in sequence against a
    running reservation, must sum to ≤ B·f_cap (the budget is never breached)."""
    reserved: list[tuple[str, float]] = []
    sizes: list[float] = []
    for i in range(5):
        s = _size(
            new_city=NEAR_CITY,
            held=[],
            extra_reserved=list(reserved),
            p_posterior=0.85,
        )
        sizes.append(s)
        reserved.append((NEAR_CITY, s))
    total = sum(sizes)
    # Budget: Σ stakes ≤ B·f_cap (+ float epsilon).
    assert total <= BANKROLL * F_CAP + 1e-6, (
        f"budget breached: Σ={total:.4f} > B·f_cap={BANKROLL * F_CAP:.4f}"
    )


def test_K1b_distant_city_absolute_raw_floor():
    """INV-K1b (verifier defect): 15 geographically distant cities at the 0.10
    correlation floor must collectively sum to ≤ B·max_portfolio_heat_pct (the
    absolute cash ceiling), AND ≤ B (never exceed the bankroll).

    This is the HEADLINE VERIFIER DEFECT: before the fix each of 15 sequential
    distant-city bets saw only 0.10 fraction of prior committed as ``corr_committed``
    → the corr-weighted effective bankroll barely shrank → each bet sized near the
    K3 single-bet cap ($17) → Σ = $253 against B=$170.

    This test was RED under the original impl (no raw-dollar floor) and GREEN after
    the ``effective_bankroll_raw`` fix. It exercises the SECOND structural belt:
    absolute raw-committed constraint (INV-K1b).

    Uses ``_size`` (which mirrors the live reactor by passing both
    ``corr_committed_usd`` AND ``raw_committed_usd``) with sequentially growing held
    books of distant-city positions.
    """
    # 15 distinct far-apart cities: use Singapore as new_city (gets 0.10 corr floor
    # vs all others) and build up the held book sequentially.
    NEW_CITY = "Singapore"
    # Any city geographically far from Singapore to guarantee 0.10 corr floor.
    # The correlation module gives floor=0.10 for haversine distance > threshold.
    far_cities = [
        "New York City", "Chicago", "Los Angeles", "Miami", "London",
        "Paris", "Berlin", "Warsaw", "Moscow", "Toronto",
        "Seattle", "Boston", "Atlanta", "Denver", "Houston",
    ]
    assert len(far_cities) == 15

    held: list[Position] = []
    sizes: list[float] = []
    reserved: list[tuple[str, float]] = []
    for i, city in enumerate(far_cities):
        s = _size(
            new_city=NEW_CITY,
            held=list(held),
            extra_reserved=list(reserved),
            p_posterior=0.92,
            bankroll=BANKROLL,
        )
        sizes.append(s)
        # Each prior bet is a "held" position (fully committed) for the next.
        held.append(_held_position(city=city, committed_usd=s, tid=f"far_{i}"))
        reserved.append((city, s))

    total_raw = sum(sizes)
    max_heat_cap = BANKROLL * 0.5  # max_portfolio_heat_pct=0.5 from config
    assert total_raw <= max_heat_cap + 1e-6, (
        f"absolute cash ceiling breached: Σ raw stakes={total_raw:.4f} "
        f"> max_heat_cap={max_heat_cap:.4f} (B·0.5). "
        f"Individual sizes: {[f'{s:.2f}' for s in sizes]}"
    )
    assert total_raw <= BANKROLL + 1e-6, (
        f"total exceeds full bankroll: Σ={total_raw:.4f} > B={BANKROLL:.4f}"
    )


# ── INV-K2: MECE — same-family bins sized with corr=1.0 (strictly smaller) ───

def test_K2_same_family_bins_sized_non_independent():
    """A 2nd bin in the SAME (city,date) family is sized with corr=1.0 to the
    first → strictly smaller than if it had been sized independently.

    CONDITIONALITY NOTE: the ``s2 < s2_indep`` strict-smaller assertion holds
    ONLY when the committed capital is sufficient to move s2 out of the K3
    single-bet cap floor. If both bets are already capped at max_single_position_pct·B
    (and the MECE reduction would move s2 below that cap), s2 still equals the cap
    and the assertion would fail as ``s2 == s2_indep``. The economic MECE guarantee
    IS intact (corr=1.0 is verified via ``corr_committed == 20.0`` below); the
    strict-smaller signal just requires a committed amount large enough to visibly
    move s2 below the cap. This test uses committed_usd=20.0 which does produce
    a visible s2=$15.75 < cap=$17.00 (K3 clamp ≠ K2 territory here).
    """
    first = _held_position(city=NEAR_CITY, committed_usd=20.0, tid="binA")
    s2 = _size(new_city=NEAR_CITY, held=[first])  # binB after binA committed
    s2_indep = _size(new_city=NEAR_CITY, held=[])  # binB sized alone
    assert s2 < s2_indep, (
        f"same-family bin not reduced: s2={s2:.4f} !< s2_indep={s2_indep:.4f}"
    )
    # And the reduction is at FULL weight (corr=1.0): effective bankroll dropped
    # by the full committed capital of the first bin.
    state = PortfolioState(positions=[first])
    corr_committed = correlated_committed_usd(state, new_city=NEAR_CITY)
    assert corr_committed == pytest.approx(20.0), (
        f"same-family corr-weight not 1.0: corr_committed={corr_committed:.4f}"
    )


# ── INV-K3: single-bet cap ≤ max_single_position_pct·B — HEADLINE RED→GREEN ──

def test_K3_single_bet_respects_max_single_position_pct():
    """The headline defect: a single strong-edge bet sized against the FULL
    bankroll exceeds max_single_position_pct (25-27% observed live). The
    portfolio-aware path must keep ANY single bet ≤ 0.10·B.

    NOTE: single-Kelly here breaches the cap; this asserts the FIX holds.
    """
    # Strong edge: p=0.95, price=0.50 → f*≈0.90 → single-Kelly ≈ 0.90·0.25·B = 22.5%·B.
    s = _size(new_city=NEAR_CITY, held=[], p_posterior=0.95, price=0.50)
    cap = BANKROLL * MAX_SINGLE_POSITION_PCT
    assert s <= cap + 1e-6, (
        f"single bet {s:.4f} ({s / BANKROLL * 100:.1f}% of B) exceeds "
        f"max_single_position_pct cap {cap:.4f} (10% of B)"
    )


# ── INV-K4: monotone in committed capital ────────────────────────────────────

def test_K4_size_monotone_decreasing_in_committed():
    """Adding an open position never increases the next bet's size; a correlated
    add strictly decreases it."""
    base = _size(new_city=NEAR_CITY, held=[])
    # Far (uncorrelated) add → non-increasing.
    far = _held_position(city=FAR_CITY, committed_usd=30.0, tid="far1")
    with_far = _size(new_city=NEAR_CITY, held=[far])
    assert with_far <= base + 1e-9
    # Correlated (same-city) add → strictly decreasing.
    near = _held_position(city=NEAR_CITY, committed_usd=30.0, tid="near1")
    with_near = _size(new_city=NEAR_CITY, held=[near])
    assert with_near < base, f"correlated add did not reduce: {with_near:.4f} !< {base:.4f}"


# ── INV-K5: correlation weighting ────────────────────────────────────────────

def test_K5_correlated_position_reduces_more_than_uncorrelated():
    """For equal committed capital, a held position in a highly-correlated city
    reduces the new bet MORE than one in an uncorrelated city."""
    committed = 40.0
    near = _held_position(city=NEAR_CITY, committed_usd=committed, tid="n1")
    far = _held_position(city=FAR_CITY, committed_usd=committed, tid="f1")
    size_with_far = _size(new_city=NEAR_CITY, held=[far])
    size_with_near = _size(new_city=NEAR_CITY, held=[near])
    assert size_with_far > size_with_near, (
        f"correlation weighting absent: far={size_with_far:.4f} "
        f"!> near={size_with_near:.4f}"
    )


# ── INV-K6: full-exposure fail-closed ────────────────────────────────────────

def test_K6_full_exposure_sizes_to_zero():
    """When correlation-weighted committed ≥ B, effective bankroll is 0 → size 0
    (never negative, never NaN)."""
    # Same-city committed capital ≥ bankroll (corr=1.0 → full weight).
    over = _held_position(city=NEAR_CITY, committed_usd=BANKROLL + 50.0, tid="over1")
    s = _size(new_city=NEAR_CITY, held=[over])
    assert s == 0.0, f"full-exposure did not fail-closed to 0: {s!r}"
    assert not math.isnan(s)
    assert s >= 0.0
    # effective_bankroll helper itself clamps at 0.
    assert effective_bankroll(BANKROLL, BANKROLL + 50.0) == 0.0


# ── INV-K7: in-flight reservation (intra-cycle) ──────────────────────────────

def test_K7_in_flight_reservation_counted_intra_cycle():
    """Two same-cycle bets: the second must see the first's just-emitted stake via
    the reservation accumulator even though the first is PENDING_TRACKED with
    effective_cost_basis_usd == 0.0 (invisible to load_portfolio)."""
    # event1: sized alone, emitted but unfilled.
    s1 = _size(new_city=NEAR_CITY, held=[], extra_reserved=[])
    assert s1 > 0.0
    # The pending sibling exists in portfolio but reports 0.0 cost basis.
    pending = _pending_unfilled_position(city=NEAR_CITY, committed_usd=s1, tid="ev1")
    state = PortfolioState(positions=[pending])

    # WITHOUT the reservation, committed seen by event2 is 0 (pending invisible).
    committed_no_reservation = correlated_committed_usd(state, new_city=NEAR_CITY)
    assert committed_no_reservation == pytest.approx(0.0)

    # WITH the reservation, committed seen by event2 includes event1's stake.
    committed_with_reservation = correlated_committed_usd(
        state, new_city=NEAR_CITY, extra_reserved=[(NEAR_CITY, s1)]
    )
    assert committed_with_reservation >= s1 - 1e-9, (
        f"in-flight reservation not counted: {committed_with_reservation:.4f} < {s1:.4f}"
    )

    # And event2's size is strictly smaller WITH the reservation than without.
    # Use a MODEST edge (p=0.55) so neither bet is bound by the K3 single-bet
    # cap — otherwise both clip to max_single_position_pct·B and the
    # reservation's marginal reduction is masked by the cap (the reservation
    # is independently proven above via committed_with_reservation).
    s1_modest = _size(new_city=NEAR_CITY, held=[], extra_reserved=[], p_posterior=0.55)
    pending_modest = _pending_unfilled_position(
        city=NEAR_CITY, committed_usd=s1_modest, tid="ev1m"
    )
    s2_no_res = _size(
        new_city=NEAR_CITY, held=[pending_modest], extra_reserved=[], p_posterior=0.55
    )
    s2_res = _size(
        new_city=NEAR_CITY,
        held=[pending_modest],
        extra_reserved=[(NEAR_CITY, s1_modest)],
        p_posterior=0.55,
    )
    assert s2_res < s2_no_res, (
        f"reservation did not shrink event2: {s2_res:.4f} !< {s2_no_res:.4f}"
    )


# ── INV-K8: no amplification vs single-Kelly ─────────────────────────────────

@pytest.mark.parametrize("p_post", [0.55, 0.70, 0.85, 0.95])
@pytest.mark.parametrize("held_committed", [0.0, 25.0, 60.0])
def test_K8_never_amplifies_vs_single_kelly(p_post, held_committed):
    """For all inputs, portfolio-aware size ≤ single-Kelly size (never amplifies).
    With zero committed capital it reduces to EQUALITY (no regression for the
    unwired/empty-portfolio case)."""
    held = (
        [_held_position(city=NEAR_CITY, committed_usd=held_committed, tid="h1")]
        if held_committed > 0
        else []
    )
    s_portfolio = _size(new_city=NEAR_CITY, held=held, p_posterior=p_post)
    s_single = _single_kelly_size(p_posterior=p_post)
    assert s_portfolio <= s_single + 1e-9, (
        f"amplified: portfolio={s_portfolio:.4f} > single={s_single:.4f} "
        f"(p={p_post}, committed={held_committed})"
    )
    if held_committed == 0.0:
        # Empty portfolio ⇒ effective bankroll == bankroll ⇒ the ONLY reduction
        # vs single-Kelly is the K3 single-bet cap (max_single_position_pct·B).
        # So the portfolio-aware size equals min(single-Kelly, cap): identical
        # when the edge is small, clipped to the cap when single-Kelly exceeds
        # 10%·B (which is itself the headline defect being fixed). This is NOT a
        # regression — it is K3 holding even with an empty book.
        cap = BANKROLL * MAX_SINGLE_POSITION_PCT
        assert s_portfolio == pytest.approx(min(s_single, cap)), (
            f"empty-portfolio not equal to min(single-Kelly, cap): "
            f"{s_portfolio:.4f} vs min({s_single:.4f}, {cap:.4f})"
        )


# ── FIX A (P1 zero-submit): budget ceiling ≠ variance haircut ────────────────
#
# ROOT CAUSE the next two tests pin: ``evaluate_kelly`` passed
# ``effective_bankroll(..., f_cap=effective_multiplier)`` — the Kelly VARIANCE
# HAIRCUT (~0.04–0.18 live) — where ``effective_bankroll``'s contract requires
# ``f_cap`` to be the CORRELATED-RISK CEILING ``max_correlated_pct`` (~0.25).
# The corr budget is ``f_cap·B``; with ``f_cap`` set to the haircut the budget
# collapses to ``mult·B`` (≈ $8.9 at mult=0.0525, B=170) instead of
# ``max_correlated_pct·B`` ($42.5). So the first same-cycle candidate's stake
# exhausts the tiny budget and every later positive-edge candidate gets
# ``corr_budget:size=0.0000`` — the zero-submit P1 defect.
#
# The existing K1–K8 suite did NOT catch this because its ``_size`` helper uses
# a TIGHT CI (q_lcb = q − 0.01) and lead_days=1.0, which produce
# ``effective_multiplier == 0.25 == max_correlated_pct`` — the bug is masked
# only when the haircut happens to equal the ceiling. These tests use a WIDE CI
# + LONG lead so the haircut is strictly below the ceiling, exposing it.

# max_correlated_pct from config (the corr-budget ceiling). Distinct concept
# from F_CAP=kelly_multiplier even though both are 0.25 in current config.
MAX_CORRELATED_PCT = 0.25


def _size_haircut(
    *,
    new_city: str,
    held: list[Position],
    bankroll: float = BANKROLL,
    p_posterior: float = 0.92,
    price: float = 0.50,
    extra_reserved: list[tuple[str, float]] | None = None,
) -> float:
    """Like ``_size`` but with a WIDE CI + LONG lead so the dynamic Kelly
    multiplier is HAIRCUT strictly below ``max_correlated_pct``.

    q_lcb = q − 0.20 → ci_width = 0.40 → ci haircut ×0.7×0.5; lead_days=5 →
    ×0.6.  effective_multiplier = 0.25·0.7·0.5·0.6 = 0.0525 (≪ 0.25). This is
    the live regime that collapsed the corr budget to ~$8.9 and zeroed every
    later same-cycle candidate.
    """
    state = PortfolioState(positions=list(held))
    corr_committed = correlated_committed_usd(
        state, new_city=new_city, extra_reserved=extra_reserved
    )
    raw_committed = total_exposure_usd(state) + sum(
        float(usd) for _, usd in (extra_reserved or [])
    )
    ctx = SizingContext.from_candidate_proof_with_portfolio(
        q_posterior=p_posterior,
        q_lcb_5pct=p_posterior - 0.20,  # WIDE CI → ci_width 0.40 → haircut
        lead_days=5.0,  # LONG lead → ×0.6 haircut
        bankroll_usd=bankroll,
        corr_committed_usd=corr_committed,
        raw_committed_usd=raw_committed,
    )
    proof = evaluate_kelly(
        kelly_decision_id="k_haircut",
        p_posterior=p_posterior,
        execution_price=_kelly_safe_price(price),
        bankroll_usd=bankroll,
        sizing_context=ctx,
        kelly_multiplier=F_CAP,
    )
    return proof.size_usd


def test_FIXA_haircut_does_not_collapse_corr_budget_to_zero():
    """RED→GREEN (Fix A): with a healthy bankroll, the config ceiling, a wide-CI
    / long-lead candidate (heavy variance haircut), and a single small open
    correlated position (~the live Shanghai $1.34 open), the candidate STILL
    sizes > 0.

    BEFORE the fix the corr budget was ``mult·B`` (≈ $8.9), so a same-cycle
    committed capital of ~$9 (well below the live exposure floor) zeroed every
    later candidate → ``corr_budget:size=0.0000``.  AFTER the fix the budget is
    ``max_correlated_pct·B`` ($42.5) and the candidate sizes positively.
    """
    # One small correlated open position, plus a same-cycle reservation that
    # together commit ~$10 (corr-weighted) — under the OLD mult·B≈$8.9 budget
    # this exhausts it and zeroes the bet; under the CORRECT max_correlated_pct·B
    # budget ($42.5) the bet still sizes.
    open_pos = _held_position(city=NEAR_CITY, committed_usd=1.34, tid="shanghai_open")
    s = _size_haircut(
        new_city=NEAR_CITY,
        held=[open_pos],
        extra_reserved=[(NEAR_CITY, 9.0)],  # earlier same-cycle accepted bet
        p_posterior=0.92,
    )
    assert s > 0.0, (
        f"FIX A regression: positive-edge candidate zeroed by corr_budget "
        f"(size={s:.4f}); budget collapsed to mult·B instead of "
        f"max_correlated_pct·B"
    )


def test_FIXA_haircut_path_still_respects_INV_K1_budget():
    """Fix A must NOT breach INV-K1 (over-sizing = ruin): even under the
    haircut path, Σ correlation-weighted simultaneous stakes ≤
    ``max_correlated_pct·B``.

    Two maximally-correlated (same-city) wide-CI strong-edge candidates sized in
    sequence against a running reservation must still sum below the ceiling. The
    fix restores would-submit sizing WITHOUT loosening the ceiling.
    """
    reserved: list[tuple[str, float]] = []
    sizes: list[float] = []
    for _ in range(6):  # several same-family same-cycle bets
        s = _size_haircut(
            new_city=NEAR_CITY,
            held=[],
            extra_reserved=list(reserved),
            p_posterior=0.95,  # strong edge to stress the budget
        )
        sizes.append(s)
        reserved.append((NEAR_CITY, s))
    total = sum(sizes)
    budget = BANKROLL * MAX_CORRELATED_PCT
    assert total <= budget + 1e-6, (
        f"INV-K1 BREACHED by Fix A: Σ stakes={total:.4f} > "
        f"max_correlated_pct·B={budget:.4f} (over-sizing = ruin)"
    )


# ── Input validation — finite check covers NaN AND inf ───────────────────────

@pytest.mark.parametrize("bad_field,bad_value", [
    # NaN inputs — existing guard
    ("ci_width",          float("nan")),
    ("bankroll_usd",      float("nan")),
    # +inf inputs — the Copilot BUG-1: NaN-only check (x == x) passes inf
    ("ci_width",          float("inf")),
    ("lead_days",         float("inf")),
    ("bankroll_usd",      float("inf")),
    ("corr_committed_usd", float("inf")),
    ("raw_committed_usd", float("inf")),
    # -inf inputs
    ("ci_width",          float("-inf")),
    ("bankroll_usd",      float("-inf")),
    ("corr_committed_usd", float("-inf")),
])
def test_sizing_context_rejects_non_finite_inputs(bad_field, bad_value):
    """SizingContext.__post_init__ must reject NaN AND inf/-inf for every
    numeric field.  Before the BUG-1 fix the check was ``x == x`` (NaN-only);
    ``float('inf') == float('inf')`` is True so inf flowed silently into
    kelly_size and produced nonsensical output.  After the fix (math.isfinite)
    all three — NaN, +inf, -inf — raise ValueError."""
    base = dict(ci_width=0.05, lead_days=1.0, bankroll_usd=170.0,
                corr_committed_usd=0.0, raw_committed_usd=0.0)
    base[bad_field] = bad_value
    # sign / range checks can also fire for negative bankroll etc.; the key
    # requirement is that a ValueError is raised (not a silent pass).
    with pytest.raises((ValueError, OverflowError)):
        SizingContext(**base)
