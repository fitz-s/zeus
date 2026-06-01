# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: ELEVATION S3 (task #103/#111) — variance-required Kelly.
#
# STRICT TDD relationship test (Fitz methodology): this is a CROSS-MODULE
# relationship test, not a function test. The invariant under test is a
# property that must hold across the SizingContext -> evaluate_kelly ->
# dynamic_kelly_mult -> kelly_size boundary:
#
#     For two candidates IDENTICAL in every sizing input EXCEPT posterior
#     CI width, size is NON-INCREASING in CI width (strictly smaller across
#     a haircut threshold).
#
# The dynamic_kelly_mult ci_width haircut is STEPWISE (>0.10 -> x0.7,
# >0.15 -> x0.5), so two widths both under 0.10 size identically; the
# strict inequality only holds across a haircut threshold. The chosen
# widths (0.02 vs 0.20) straddle both thresholds, so the strict GREEN
# holds for THIS test.
#
# Pre-S3 the EDLI money-path adapter sized on a FLAT kelly_multiplier
# scalar, so variance was UNCARRIED and the two candidates sized
# IDENTICALLY. The first test below pins that defect (flat path: equal
# sizes) as a permanent witness; the relationship test proves the fix.
# NOTE: S3's strict relationship is only provable POST-signature-change
# (the variance input ci_width only reaches Kelly once evaluate_kelly
# accepts a SizingContext); on the pre-S3 flat path the relationship test
# fails as a genuine value-RED (tight.size_usd == wide.size_usd), not a
# signature/TypeError.

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.events.money_path_adapters import evaluate_kelly
from src.sizing.sizing_context import SizingContext
from src.strategy.kelly import kelly_size


def _safe_price() -> ExecutionPrice:
    return ExecutionPrice(
        0.40, "ask", fee_deducted=False, currency="probability_units"
    ).with_taker_fee()


# Two candidates IDENTICAL except CI width.
#   tight: q_posterior=0.64, q_lcb_5pct=0.63 -> ci_width = 2*(0.64-0.63) = 0.02
#   wide:  q_posterior=0.64, q_lcb_5pct=0.54 -> ci_width = 2*(0.64-0.54) = 0.20
# Same lead_days=6, same execution price, same bankroll, same p_posterior.
_Q_POSTERIOR = 0.64
_Q_LCB_TIGHT = 0.63  # ci_width 0.02
_Q_LCB_WIDE = 0.54   # ci_width 0.20
_LEAD_DAYS = 6.0
_BANKROLL = 1000.0


def test_flat_multiplier_path_sizes_identically_DEFECT_WITNESS():
    """Witness the pre-S3 defect: a FLAT multiplier ignores CI width.

    This is the RED baseline made permanent. With a flat kelly_multiplier
    the tight-CI and wide-CI candidates size to the SAME USD amount — the
    exact variance-blind behaviour S3 exists to remove. If a future change
    re-introduces a flat-scalar sizing path that this equality stops
    holding for the WRONG reason, that is a separate regression; the
    relationship test below is the forward guard.
    """
    ep = _safe_price()
    size_tight_flat = kelly_size(_Q_POSTERIOR, ep, _BANKROLL, kelly_mult=0.25)
    size_wide_flat = kelly_size(_Q_POSTERIOR, ep, _BANKROLL, kelly_mult=0.25)
    # Flat path: CI width has NO effect — identical sizes (the defect).
    assert size_tight_flat == size_wide_flat
    assert size_tight_flat == pytest.approx(96.9388, abs=1e-3)


def test_wider_ci_sizes_strictly_smaller_RELATIONSHIP():
    """S3 invariant: size is non-increasing in CI width (strictly smaller
    across a haircut threshold).

    Both candidates pass through evaluate_kelly with a SizingContext built
    from the proof's (q_posterior, q_lcb_5pct, lead_days). The ONLY
    difference is q_lcb_5pct (hence ci_width). The dynamic_kelly_mult
    ci_width haircut is STEPWISE (>0.10 -> x0.7, >0.15 -> x0.5), so the
    strict `>` only holds when the two widths straddle a threshold — the
    chosen widths (0.02 vs 0.20) do, so the wide-CI candidate sizes DOWN.
    On pre-S3 code (flat scalar) the two sizes are EQUAL, so the strict `>`
    fails as a genuine defect-VALUE RED (tight.size_usd == wide.size_usd),
    not an import / signature / TypeError.
    """
    ep = _safe_price()

    ctx_tight = SizingContext.from_candidate_proof(
        q_posterior=_Q_POSTERIOR, q_lcb_5pct=_Q_LCB_TIGHT, lead_days=_LEAD_DAYS
    )
    ctx_wide = SizingContext.from_candidate_proof(
        q_posterior=_Q_POSTERIOR, q_lcb_5pct=_Q_LCB_WIDE, lead_days=_LEAD_DAYS
    )
    assert ctx_tight.ci_width == pytest.approx(0.02)
    assert ctx_wide.ci_width == pytest.approx(0.20)

    proof_tight = evaluate_kelly(
        kelly_decision_id="kelly-tight",
        p_posterior=_Q_POSTERIOR,
        execution_price=ep,
        bankroll_usd=_BANKROLL,
        sizing_context=ctx_tight,
    )
    proof_wide = evaluate_kelly(
        kelly_decision_id="kelly-wide",
        p_posterior=_Q_POSTERIOR,
        execution_price=ep,
        bankroll_usd=_BANKROLL,
        sizing_context=ctx_wide,
    )

    # STRICT inequality — the variance-required core of S3.
    assert proof_tight.size_usd > proof_wide.size_usd

    # Concrete GREEN targets: dynamic_kelly_mult(base=0.25, ci, lead=6)
    #   tight -> 0.25 * 0.6              = 0.15    -> size 58.1633
    #   wide  -> 0.25 * 0.7 * 0.5 * 0.6  = 0.0525  -> size 20.3571
    assert proof_tight.size_usd == pytest.approx(58.1633, abs=1e-3)
    assert proof_wide.size_usd == pytest.approx(20.3571, abs=1e-3)
    assert proof_tight.passed is True
    assert proof_wide.passed is True


def test_kelly_proof_passed_false_when_size_collapses():
    """KellyProof.passed = size_usd > 0 routes True->False on collapse.

    A candidate with NO edge (p_posterior <= execution price) sizes to
    0.0 regardless of context, so passed must be False — proving the
    passed flag tracks size, not a hard-coded True.
    """
    ep = _safe_price()  # fee-adjusted value ~0.412
    ctx = SizingContext.from_candidate_proof(
        q_posterior=0.41, q_lcb_5pct=0.40, lead_days=2.0
    )
    proof = evaluate_kelly(
        kelly_decision_id="kelly-no-edge",
        p_posterior=0.30,  # below execution price -> zero edge -> size 0
        execution_price=ep,
        bankroll_usd=_BANKROLL,
        sizing_context=ctx,
    )
    assert proof.size_usd == 0.0
    assert proof.passed is False
