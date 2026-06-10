# Created: 2026-06-10
# Last reused/audited: 2026-06-10
# Authority basis: operator single-Kelly directive 2026-06-10, /tmp/kelly_stack_audit.md
"""Relationship tests for the single-application Kelly restructure (operator option "a").

These pin the operator's NEW sizing law (2026-06-10), supersing the implicit
"size on spendable_cash" law:

  1. The ΔU family optimizer provides ONLY the family-internal allocation SHAPE.
     The FAMILY TOTAL is scaled to the fractional-Kelly total. Fractional Kelly is
     applied EXACTLY ONCE.
  2. Sizing basis = TOTAL portfolio equity (free cash + open position equity),
     applied once: stake_family_total = equity × kelly_multiplier × f*_family.
     CASH is a separate ONE-TIME BOUND (stake ≤ free available cash), NOT another
     multiplicative haircut.
  3. Acceptance envelope: typical certified edges (trade_score 0.03–0.15) on
     ~1000 USD equity produce per-order stakes ~5–15 USD, clearing the ~3.5–4 USD
     venue 5-share min order.

These are RELATIONSHIP tests (cross-module invariants), not function tests: they
assert properties that hold as edge/equity/cash flow THROUGH the sizing kernel.
"""
from __future__ import annotations

import json

import pytest

from src.engine import event_reactor_adapter as era
from src.events.candidate_binding import MarketTopologyCandidate
from src.types.market import Bin

# Operator proving-phase truth.
EQUITY = 1000.0  # total portfolio equity basis (~1043 live)
LIVE_KELLY_MULT = 0.125  # the live fractional-Kelly multiplier (1/8 Kelly)
VENUE_MIN_ORDER_FLOOR_USD = 3.5  # ~5-share min order on a mid-price NO bin (audit Part 1)


# ---------------------------------------------------------------------------
# Fixtures — same shape the S4/S5 kernel tests use.
# ---------------------------------------------------------------------------
def _row(
    *,
    condition_id="condition-1",
    yes_token="yes-1",
    no_token="no-1",
    yes_asks=(("0.40", "1000000"),),
    no_asks=(("0.55", "1000000"),),
    yes_bids=(("0.39", "100"),),
    no_bids=(("0.19", "100"),),
    min_tick="0.01",
    min_order="5",
    fee_rate_fraction=0.0,
    snapshot_id="snap-sk",
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
        "condition_id": condition_id,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": min_tick,
        "min_order_size": min_order,
        "fee_details_json": json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": "book-hash-sk",
    }


def _proof(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj, trade_score=1.0):
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=MarketTopologyCandidate(
            city="paris",
            target_date="2026-06-12",
            metric="tmax",
            condition_id=str(row.get("condition_id") or ""),
            yes_token_id=str(row.get("yes_token_id") or ""),
            no_token_id=str(row.get("no_token_id") or ""),
            bin=bin_obj,
        ),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=ep,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=trade_score,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="cal-hash",
        p_live_vector_hash="live-hash",
        missing_reason=None,
    )


def _stake_and_price(proof, all_proofs=None, *, equity=EQUITY, mult=LIVE_KELLY_MULT,
                     exposure=None, free_cash_usd=None, floor_out=None):
    """The live S5 kernel stake (USD) + chosen-stake price for ``proof``."""
    return era._robust_marginal_utility_stake_and_price(
        family_key="fam",
        selected_proof=proof,
        all_proofs=tuple(all_proofs) if all_proofs is not None else (proof,),
        extra_exposure_by_bin_id=exposure or {},
        bankroll_usd=equity,
        kelly_multiplier=mult,
        stake_floor_out=floor_out,
        free_cash_usd=free_cash_usd,
    )


def _stake(proof, all_proofs=None, **kw):
    s, _p = _stake_and_price(proof, all_proofs, **kw)
    return s


def _single_yes_bin(low=60.0):
    return Bin(low=low, high=low + 1.0, unit="F", label=f"{int(low)}-{int(low)+1}F")


def _binary_fractional_kelly_proxy(q_lcb, cost, *, equity=EQUITY, mult=LIVE_KELLY_MULT):
    """f* × mult × equity for a 2-outcome (single-bin) family (the audit's binary proxy)."""
    if q_lcb <= cost:
        return 0.0
    f_star = (q_lcb - cost) / (1.0 - cost)
    return f_star * mult * equity


# ===========================================================================
# T1 — ENVELOPE: typical edges on ~1000 USD equity land in the 5–15 USD band.
# ===========================================================================
@pytest.mark.parametrize(
    "q_lcb,yes_ask,expected_stake",
    [
        # edge = q_lcb − cost. Single-bin family ⇒ ΔU stake == f*·mult·equity exactly.
        (0.20, "0.15", 7.35),   # ts ~0.05 → ~7.4 USD
        (0.30, "0.20", 15.63),  # ts ~0.10 → ~15.6 USD (top of band)
    ],
)
def test_T1_envelope_typical_edge_sizes_in_5_to_15_band(q_lcb, yes_ask, expected_stake):
    """A single-candidate family, equity=1000, mult=0.125: a certified edge in the
    operator's trade_score 0.03–0.15 range produces a per-order stake in the
    ~5–15 USD band (assert within ±20% of the exact f* math). This is the
    acceptance envelope the live system was MISSING because it sized on free cash
    (~241) instead of total equity (~1000)."""
    proof = _proof(direction="buy_yes", row=_row(yes_asks=((yes_ask, "1000000"),)),
                   token_id="yes-1", q_posterior=q_lcb, q_lcb_5pct=q_lcb,
                   bin_obj=_single_yes_bin())
    stake = _stake(proof)
    assert stake == pytest.approx(expected_stake, rel=0.20), (
        f"stake {stake:.4f} not within ±20% of expected {expected_stake:.4f} "
        f"(q_lcb={q_lcb}, ask={yes_ask})"
    )
    # And it clears the venue 5-share min order for a mid-price bin.
    assert stake >= VENUE_MIN_ORDER_FLOOR_USD, (
        f"stake {stake:.4f} below the ~{VENUE_MIN_ORDER_FLOOR_USD} USD venue min order "
        f"— would 100% abort BELOW_MIN_ORDER (the live bug)"
    )


def test_T1_envelope_strongest_edge_in_or_above_band():
    """ts ~0.15 sizes ~25 USD — at/above the band top, which is correct (a stronger
    edge deserves more), and emphatically clears the venue floor."""
    proof = _proof(direction="buy_yes", row=_row(yes_asks=(("0.25", "1000000"),)),
                   token_id="yes-1", q_posterior=0.40, q_lcb_5pct=0.40,
                   bin_obj=_single_yes_bin())
    stake = _stake(proof)
    assert stake == pytest.approx(25.0, rel=0.20)
    assert stake >= VENUE_MIN_ORDER_FLOOR_USD


# ===========================================================================
# T2 — SINGLE APPLICATION: end-to-end deployed fraction ≈ mult × f* (no hidden
# second fractional layer). ratio ≥ 0.7.
# ===========================================================================
@pytest.mark.parametrize(
    "q_lcb,yes_ask",
    [(0.20, "0.15"), (0.30, "0.20"), (0.40, "0.25"), (0.50, "0.30")],
)
def test_T2_single_fractional_application(q_lcb, yes_ask):
    """For a single-candidate family the deployed stake equals exactly ONE fractional
    Kelly: stake ≈ kelly_multiplier × f* × equity. If a hidden second fractional layer
    existed, stake/proxy would collapse well below 1.0. Assert ratio ≥ 0.7."""
    proof = _proof(direction="buy_yes", row=_row(yes_asks=((yes_ask, "1000000"),)),
                   token_id="yes-1", q_posterior=q_lcb, q_lcb_5pct=q_lcb,
                   bin_obj=_single_yes_bin())
    stake = _stake(proof)
    proxy = _binary_fractional_kelly_proxy(q_lcb, float(yes_ask))
    assert proxy > 0.0
    ratio = stake / proxy
    assert ratio >= 0.7, (
        f"deployed/fractional-Kelly ratio {ratio:.3f} < 0.7 — a hidden SECOND "
        f"fractional layer is shrinking the single-application stake "
        f"(stake={stake:.4f}, proxy={proxy:.4f}, q_lcb={q_lcb}, ask={yes_ask})"
    )
    # No DOUBLE-count: the ratio must also not exceed ~1.05 (a single application,
    # not a super-Kelly amplification).
    assert ratio <= 1.05, (
        f"ratio {ratio:.3f} > 1.05 — stake exceeds a single fractional Kelly (amplification)"
    )


# ===========================================================================
# T3 — SHAPE PRESERVATION: multi-bin family — relative allocation follows the
# ΔU shape; correlation legs still attenuate RELATIVE weights.
# ===========================================================================
def _family_two_bins():
    """A 2-bin family: bin A is a confident left-tail YES, bin B a thinner YES.
    Returns (proof_a, proof_b, all_proofs)."""
    row_a = _row(condition_id="cond-A", yes_token="yesA", no_token="noA",
                 yes_asks=(("0.15", "1000000"),), snapshot_id="snap-A")
    row_b = _row(condition_id="cond-B", yes_token="yesB", no_token="noB",
                 yes_asks=(("0.20", "1000000"),), snapshot_id="snap-B")
    bin_a = _single_yes_bin(60.0)
    bin_b = _single_yes_bin(61.0)
    pa = _proof(direction="buy_yes", row=row_a, token_id="yesA",
                q_posterior=0.30, q_lcb_5pct=0.30, bin_obj=bin_a)
    pb = _proof(direction="buy_yes", row=row_b, token_id="yesB",
                q_posterior=0.25, q_lcb_5pct=0.25, bin_obj=bin_b)
    return pa, pb, (pa, pb)


def test_T3_family_shape_preserved_and_total_scaled_to_fractional_kelly():
    """In a multi-bin family the per-leg stakes preserve the ΔU optimizer's relative
    SHAPE (the stronger-edge bin sizes >= the weaker one), AND each leg's stake stays
    within the fractional-Kelly budget mult·equity (the family total is scaled to
    fractional Kelly, applied once)."""
    pa, pb, allp = _family_two_bins()
    sa = _stake(pa, allp)
    sb = _stake(pb, allp)
    budget = EQUITY * LIVE_KELLY_MULT
    assert sa > 0.0 and sb > 0.0
    # Shape: the confident bin (lower cost, higher q_lcb edge) sizes at least as large.
    assert sa >= sb, f"family shape inverted: strong bin {sa:.4f} < weak bin {sb:.4f}"
    # Each leg within the single fractional-Kelly budget.
    assert sa <= budget + 1e-6 and sb <= budget + 1e-6


def test_T3_family_total_invariant_to_within_family_exposure():
    """Under the new single-Kelly law, the FAMILY TOTAL (equity × mult × f*_binary) is
    invariant to within-family exposure for a single-leg selection — the ΔU concavity
    affects the SHAPE (how simultaneous multi-leg families distribute across bins), but
    the total of the selected leg does not shrink due to existing correlated exposure.
    This tests spec point 1 (operator single-Kelly directive 2026-06-10): ΔU = SHAPE only.

    Note: this is the UPDATED K4/K5 expectation for the new law. The PRE-FIX law had
    ΔU shrink the stake via concavity on existing exposure — that was the bug.
    """
    pa, pb, allp = _family_two_bins()
    base = _stake(pa, allp)
    # Heavy existing exposure on bin A's outcome must NOT shrink the total for a
    # single-leg selection — the binary-Kelly total is exposure-invariant.
    bin_a_id = era._candidate_bin_id(pa)
    with_exposure = _stake(pa, allp, exposure={bin_a_id: 500.0})
    assert with_exposure == pytest.approx(base, rel=1e-6), (
        f"single-leg family total must be exposure-invariant (ΔU = SHAPE only): "
        f"with-exposure {with_exposure:.4f} vs flat {base:.4f} (new law: binary-Kelly total)"
    )


# ===========================================================================
# T4 — CASH BOUND: free cash < computed stake ⇒ stake = free cash (a one-time
# bound with loud provenance), never a silent multiplicative shrink.
# ===========================================================================
def test_T4_free_cash_bounds_stake_with_provenance():
    """When free available cash is below the equity-scaled fractional-Kelly stake, the
    kernel clamps the stake to free cash AS A ONE-TIME BOUND and records the provenance
    (stake_floor='FREE_CASH_BOUND'). The equity basis still scales the size; cash is the
    final min(), never another multiplicative haircut."""
    # Strong edge on equity=1000 → ~25 USD unbounded.
    proof = _proof(direction="buy_yes", row=_row(yes_asks=(("0.25", "1000000"),)),
                   token_id="yes-1", q_posterior=0.40, q_lcb_5pct=0.40,
                   bin_obj=_single_yes_bin())
    unbounded = _stake(proof, equity=EQUITY, free_cash_usd=None)
    assert unbounded == pytest.approx(25.0, rel=0.20)
    # Now free cash is only 10 USD — the stake must clamp to 10, with provenance.
    floor_out: dict = {}
    bounded = _stake(proof, equity=EQUITY, free_cash_usd=10.0, floor_out=floor_out)
    assert bounded == pytest.approx(10.0, abs=1e-6), (
        f"stake {bounded:.4f} not clamped to free cash 10.0"
    )
    assert floor_out.get("stake_floor") == "FREE_CASH_BOUND", (
        f"free-cash clamp must record provenance; got {floor_out!r}"
    )
    assert floor_out.get("stake_floor_free_cash_usd") == pytest.approx(10.0)


def test_T4_free_cash_above_stake_is_a_noop():
    """When free cash exceeds the computed stake the bound never fires — the stake is the
    full equity-scaled fractional Kelly and no FREE_CASH_BOUND provenance is recorded."""
    proof = _proof(direction="buy_yes", row=_row(yes_asks=(("0.20", "1000000"),)),
                   token_id="yes-1", q_posterior=0.30, q_lcb_5pct=0.30,
                   bin_obj=_single_yes_bin())
    floor_out: dict = {}
    bounded = _stake(proof, equity=EQUITY, free_cash_usd=900.0, floor_out=floor_out)
    unbounded = _stake(proof, equity=EQUITY, free_cash_usd=None)
    assert bounded == pytest.approx(unbounded, rel=1e-6)
    assert "stake_floor" not in floor_out or floor_out.get("stake_floor") != "FREE_CASH_BOUND"


def test_T4_cash_bound_never_silently_shrinks_below_basis_size():
    """The cash bound is a min(), not a multiplicative shrink: with ample cash the
    equity-basis size is fully deployed (the 4.3x lift from spendable→equity is real)."""
    proof = _proof(direction="buy_yes", row=_row(yes_asks=(("0.15", "1000000"),)),
                   token_id="yes-1", q_posterior=0.20, q_lcb_5pct=0.20,
                   bin_obj=_single_yes_bin())
    # Equity 1000 with plenty of cash sizes ~7.4 USD; the OLD spendable-cash basis (241)
    # would have sized ~1.8 USD (below the venue floor). Pin the lift.
    on_equity = _stake(proof, equity=1000.0, free_cash_usd=900.0)
    on_spendable_only = _stake(proof, equity=241.0, free_cash_usd=241.0)
    assert on_equity > on_spendable_only * 3.0, (
        f"equity basis must lift the stake materially vs the old spendable-cash basis: "
        f"equity {on_equity:.4f} vs spendable {on_spendable_only:.4f}"
    )


# ===========================================================================
# T5 — MULTI-BIN FAMILY (22-hypothesis regime): deployed fraction ≥ 0.7 × intent.
#
# This is the live failure mode: a NO candidate on bin_i inside a 22-bin weather
# family. The ΔU log-utility optimizer, when run unconstrained over the 22-outcome
# payoff matrix on a flat baseline A_y=equity, returns optimal_stake_usd near-zero
# because the NO-on-bin_i leg "wins" on 21/22 outcomes — marginal utility per dollar
# is near-zero (the optimizer already "has" a lot of winning wealth). The result is
# a ~10x collapse in the deployed stake relative to the fractional-Kelly intent.
#
# The operator spec: ΔU provides SHAPE only; the TOTAL = equity × mult × f*_scalar
# where f*_scalar is the binary Kelly derived from the selected candidate's certified
# q_lcb and cost (not from the 22-outcome ΔU argmax). For single-leg selection
# (the live case), shape = 1.0, so stake = family_total.
# ===========================================================================

def _family_n_bins(n=22, *, selected_no_cost=0.70, no_q_lcb=0.74, equity=EQUITY):
    """Create a NO candidate for bin_0 inside a family with n bins.

    All bins have YES asks at 1/n price (equal-probability scenario) so the
    22-bin family approximates the live weather market. The selected candidate is
    buy_no on bin_0 (cost=selected_no_cost, q_lcb_no=no_q_lcb).

    The key: with 22 bins + OUTSIDE outcome, the NO leg wins on 22/23 outcomes.
    The ΔU log-utility argmax on baseline A_y=equity should return near-zero s*
    for this geometry — which is WRONG per operator spec. The fix must recover
    the binary-Kelly total regardless of family width.
    """
    # build the selected bin's proof
    row_selected = _row(
        condition_id="cond-sel",
        yes_token="yes-sel",
        no_token="no-sel",
        no_asks=((str(round(selected_no_cost, 2)), "1000000"),),
        yes_asks=(("0.30", "1000000"),),
        snapshot_id="snap-sel",
    )
    bin_selected = _single_yes_bin(60.0)
    proof_selected = _proof(
        direction="buy_no",
        row=row_selected,
        token_id="no-sel",
        q_posterior=no_q_lcb,
        q_lcb_5pct=no_q_lcb,
        bin_obj=bin_selected,
    )

    # Build sibling NO proofs for the remaining n-1 bins (family context for ΔU)
    siblings = []
    for i in range(1, n):
        row_sib = _row(
            condition_id=f"cond-sib{i}",
            yes_token=f"yes-sib{i}",
            no_token=f"no-sib{i}",
            no_asks=(("0.96", "1000000"),),
            yes_asks=(("0.04", "1000000"),),
            snapshot_id=f"snap-sib{i}",
        )
        bin_sib = _single_yes_bin(61.0 + i)
        siblings.append(_proof(
            direction="buy_no",
            row=row_sib,
            token_id=f"no-sib{i}",
            q_posterior=0.97,
            q_lcb_5pct=0.97,
            bin_obj=bin_sib,
        ))

    all_proofs = (proof_selected, *siblings)
    return proof_selected, all_proofs


@pytest.mark.parametrize("n_bins,no_cost,no_q_lcb", [
    # Live case: ts=0.04, NO at 0.70, 22-bin family
    (22, 0.70, 0.74),
    # Simpler: ts=0.08, NO at 0.65, 10-bin family
    (10, 0.65, 0.73),
])
def test_T5_multi_bin_family_no_candidate_deployed_fraction_geq_0_7(n_bins, no_cost, no_q_lcb):
    """For a buy_no candidate in a multi-bin family, the deployed stake must be ≥ 0.7 ×
    the binary fractional-Kelly proxy (equity × mult × f*_binary).

    RELATIONSHIP: operator spec point 1 — ΔU provides SHAPE only; the FAMILY TOTAL =
    equity × mult × f*_binary. For single-leg selection (the live case), shape = 1.0,
    so deployed ≥ 0.7 × intended regardless of how wide the family is.

    Before the fix: ΔU on a 22-bin payoff matrix returned optimal_stake_usd near-zero
    (the NO-wins-on-21-outcomes concavity collapse), giving ratio ~ 0.1 — FAIL.
    After the fix: the kernel uses f*_binary directly for the family total — PASS.
    """
    proof, all_proofs = _family_n_bins(n=n_bins, selected_no_cost=no_cost, no_q_lcb=no_q_lcb)

    stake = _stake(proof, list(all_proofs), equity=EQUITY, mult=LIVE_KELLY_MULT)
    proxy = _binary_fractional_kelly_proxy(no_q_lcb, no_cost)

    assert proxy > 0.0, "proxy must be positive (test setup error)"
    assert stake > 0.0, f"kernel returned 0 stake for {n_bins}-bin family (no-trade? ts too low?)"

    ratio = stake / proxy
    assert ratio >= 0.7, (
        f"T5 FAIL: {n_bins}-bin family deployed/fractional-Kelly ratio={ratio:.4f} < 0.7 "
        f"(stake={stake:.4f}, proxy={proxy:.4f}, no_cost={no_cost}, no_q_lcb={no_q_lcb}). "
        f"The ΔU multi-bin concavity collapse is still applying its own risk-aversion "
        f"on top of fractional Kelly — violates operator spec point 1 (ΔU = SHAPE only)."
    )
    assert ratio <= 1.05, (
        f"ratio {ratio:.4f} > 1.05 — exceeds a single fractional Kelly (amplification)"
    )
