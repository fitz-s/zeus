# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K3 fix (adversarial-verify finding #1, CRITICAL). The
#   QlcbProvenance carrier raised ValueError on q_lcb outside [0,1], but the legacy
#   (origin/main) lcb_by_direction was a plain dict[tuple,float] that TOLERATED a
#   negative deep-tail q_lcb: the FORECAST_BOOTSTRAP restore is
#   `float(hyp.ci_lower) + cost`, and for a deep-OTM bin (p_posterior~0) the edge
#   CI lower bound is negative, so the restored q_lcb is negative. Legacy let that
#   bin simply lose selection while the FAMILY still formed a receipt. The K3 type
#   (introduced UNCONDITIONALLY, not flag-gated) turned that legitimate out-of-range
#   tail into a ValueError that propagates to the family catch (event_reactor_adapter
#   :732) -> LIVE_INFERENCE_INPUTS_MISSING, collapsing the WHOLE family even with the
#   K3 shadow flag OFF. That is a flag-OFF production regression — it violates the
#   merge safety contract (flag-OFF == legacy, byte-identical family formation).
#
#   FIX: clamp q_lcb into [0.0, 1.0] at construction and record a provenance flag
#   `clamped=True` when the clamp fired, so the TYPE never raises on a legitimate
#   out-of-range tail. The error CATEGORY (a deep-tail bin kills the whole family)
#   becomes unconstructable; the bin just loses selection as it did in legacy.
"""K3 clamp relationship tests — flag-OFF legacy family formation restored.

RELATIONSHIP under test: the q_lcb PRODUCER (event_reactor_adapter building
lcb_by_direction via the FORECAST_BOOTSTRAP restore `ci_lower + cost`) hands a
value across the QlcbProvenance boundary to the CONSUMER (trade_score / family
proof generation). For a deep-OTM bin the produced value is NEGATIVE. Legacy
tolerated it (the bin lost selection, the family formed). The fix must keep that
property: the type clamps into [0,1] and records `clamped=True` instead of raising.

Written RED-first against the pre-fix branch state (the carrier raises).
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Carrier-level: a deep-tail negative q_lcb clamps to 0.0, never raises.
# ---------------------------------------------------------------------------
def test_qlcb_provenance_clamps_negative_tail_instead_of_raising():
    """A deep-OTM bin produces `ci_lower + cost` < 0. Legacy stored it as a plain
    float and the bin merely lost selection. The carrier must NOT raise — it clamps
    to 0.0 and records that the clamp fired, so the family still forms."""
    from src.calibration.qlcb_provenance import QlcbProvenance

    p = QlcbProvenance(
        q_lcb=-0.05,  # deep-tail: ci_lower(-0.07) + cost(0.02)
        calibration_source="FORECAST_BOOTSTRAP",
        n_settlement_observations=None,
        coverage_ratio=None,
    )
    assert p.q_lcb == pytest.approx(0.0)  # clamped into [0,1], not raised
    assert p.clamped is True  # provenance records the clamp fired


def test_qlcb_provenance_clamps_above_one_instead_of_raising():
    """Symmetric upper-bound clamp: a q_lcb above 1.0 clamps to 1.0 and records
    clamped=True. The carrier must never raise on a numeric out-of-range value —
    raising is what collapsed the whole family flag-OFF."""
    from src.calibration.qlcb_provenance import QlcbProvenance

    p = QlcbProvenance(
        q_lcb=1.4,
        calibration_source="FORECAST_BOOTSTRAP",
        n_settlement_observations=None,
        coverage_ratio=None,
    )
    assert p.q_lcb == pytest.approx(1.0)
    assert p.clamped is True


def test_qlcb_provenance_in_range_value_is_not_flagged_clamped():
    """An in-range q_lcb is stored verbatim with clamped=False — the clamp flag is
    only set when the clamp actually fired (provenance honesty)."""
    from src.calibration.qlcb_provenance import QlcbProvenance

    p = QlcbProvenance(
        q_lcb=0.62,
        calibration_source="FORECAST_BOOTSTRAP",
        n_settlement_observations=None,
        coverage_ratio=None,
    )
    assert p.q_lcb == pytest.approx(0.62)
    assert p.clamped is False


def test_qlcb_provenance_still_rejects_non_numeric():
    """The clamp is for out-of-RANGE numbers only. A non-numeric q_lcb is still a
    hard construction error — clamping cannot rescue a NaN/None/scale bug."""
    from src.calibration.qlcb_provenance import QlcbProvenance

    with pytest.raises((ValueError, TypeError)):
        QlcbProvenance(
            q_lcb=None,  # type: ignore[arg-type]
            calibration_source="FORECAST_BOOTSTRAP",
        )


def test_clamp_to_zero_is_decision_equivalent_to_legacy_raw_negative():
    """SAFETY CONTRACT (K3 unconditional clamp). The clamp is decision-equivalent to
    legacy's raw-negative q_lcb: the robust trade score is
    p_fill·min(q_5pct - c_95pct - penalty, q_posterior - c_stress - penalty). For a
    deep-OTM bin BOTH q_5pct=0.0 (clamped) and q_5pct<0 (legacy) yield a NEGATIVE
    robust_edge (any real cost c_95pct > 0), so the bin loses selection IDENTICALLY.
    The clamp therefore changes no live decision — it only stops the type raising."""
    from src.strategy.live_inference.trade_score import TradeScoreInputs, robust_trade_score

    c_95pct = 0.30  # a real cost (the deep-OTM bin's market price)
    common = dict(
        q_posterior=0.0,  # deep-OTM: in-bin mass ~0
        c_95pct=c_95pct,
        c_stress=c_95pct,
        lambda_edge=0.01,
        lambda_stress=0.01,
        p_fill_lcb=0.5,
    )
    score_legacy = robust_trade_score(TradeScoreInputs(q_5pct=-0.05, **common))
    score_clamped = robust_trade_score(TradeScoreInputs(q_5pct=0.0, **common))

    # Both lose selection (strictly negative score) — the decision is identical.
    assert score_legacy < 0.0
    assert score_clamped < 0.0
