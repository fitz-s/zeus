# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K3 (q_lcb settlement-backward-coverage). K3 root: q_lcb
#   was never settlement-grounded (identity-Platt = 0 calibration rows) -> the live
#   LCB ran ~26pt overconfident. This module reads the REALIZED settlement win-rate
#   in the band a q_lcb claims, isotonic-calibrates it, and REFUSES to license an LCB
#   the settled record does not back. Truth comes ONLY from GradedReceipt verdicts
#   (src.contracts.graded_receipt) — never a fresh value-equality / startswith join
#   (which the D1 keystone proved structurally mis-grades temperature labels).
#   Direction Law lives inside grade_receipt: buy_yes WIN iff settled_bin==traded_bin;
#   buy_no WIN iff !=. The win/loss STREAM this module consumes is produced there.
#
#   K<<N FOLD: the coverage verdict is the EMOS k_cov INPUT (see emos_ci_license),
#   NOT a 7th coverage flag bolted atop the calibration layers.
#
#   K2 COUPLING (do NOT silently break): edli_bias_correction_enabled forces an
#   identity Platt on the BIAS-CORRECTED p_raw domain (config _edli_bias_correction_
#   enabled_note: "forces identity Platt for the corrected p_raw_domain — train/serve
#   lockstep"). Re-introducing a REAL settlement-isotonic calibration on q_lcb is
#   ALLOWED here only because it shrinks the LCB band (a one-sided honesty haircut),
#   NOT because it re-fits the corrected-domain Platt. Promoting this to a full
#   re-calibration of p_raw requires resolving that lockstep first — it is out of
#   scope for K3 and must not be done implicitly.
"""settlement_backward_coverage — license a q_lcb against the settled record.

For a (city, metric, season) cohort and a claimed ``q_lcb``, read the realized
settlement win-rate in that band (graded through ``grade_receipt``), isotonic-
calibrate it, and return one of:

  LICENSED          — realized >= claimed within tolerance: q_lcb unchanged.
  UNLICENSED        — realized < claimed beyond tolerance: shrink q_lcb to the
                      realized rate minus a 1pp honesty margin; source becomes
                      SETTLEMENT_ISOTONIC.
  INSUFFICIENT_DATA — fewer than ``min_n`` settled observations: q_lcb unchanged
                      + a WARN (we never shrink, nor license, on thin data).

The SHRINK is HIGH risk (it moves the live decision). It is applied to the live
LCB ONLY through ``apply_settlement_coverage(..., enabled=<flag>)`` with the flag
``edli.q_lcb_settlement_coverage_gate_enabled`` (default FALSE). With the flag
OFF the verdict is computed but the LCB is byte-identical to today. The ARM gate
(``arm_gate_coverage_blocks``) reads the verdict UNCONDITIONALLY — you cannot arm
on an LCB the settled record refuses, even while the live shrink is shadowed OFF.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Literal, Optional, Sequence

logger = logging.getLogger(__name__)

# Tolerance band: realized may sit this far BELOW claimed and still be LICENSED
# (sampling noise). A realized rate more than this below the claim is UNLICENSED.
_COVERAGE_TOL = 0.05
# Honesty margin: when we shrink, we go 1pp BELOW the realized rate, never to it
# exactly (a one-sided lower bound must under-claim, not match the point estimate).
_SHRINK_MARGIN = 0.01
# ARM-gate coverage-ratio tolerance: an armed decision requires the band to be
# calibrated within 10% of 1.0 (per the K3 brief).
_ARM_RATIO_TOL = 0.10

CoverageStatus = Literal["LICENSED", "UNLICENSED", "INSUFFICIENT_DATA"]


@dataclass(frozen=True)
class CoverageObservation:
    """One settled observation feeding the coverage check.

    ``q_lcb`` is the LCB the receipt CLAIMED at decision time; ``won`` is the
    realized outcome graded through ``grade_receipt`` (the Direction Law lives
    there — callers MUST build ``won`` via the spine, not a hand-set bool).
    """

    q_lcb: float
    won: bool


@dataclass(frozen=True)
class CoverageVerdict:
    """The settlement-backward-coverage verdict for one (city, metric, season).

    Frozen truth object. ``q_lcb_out`` is the shrink TARGET (only applied to the
    live LCB when the shadow flag is ON, via ``apply_settlement_coverage``).
    ``coverage_ratio`` = realized / claimed (None when INSUFFICIENT). The ARM gate
    reads ``status`` + ``coverage_ratio`` directly.
    """

    status: CoverageStatus
    q_lcb_in: float
    q_lcb_out: float
    n_settlement_observations: int
    coverage_ratio: Optional[float]
    realized_win_rate: Optional[float]
    calibration_source: Literal["SETTLEMENT_ISOTONIC"] = "SETTLEMENT_ISOTONIC"


def _isotonic_realized_rate(observations: Sequence[CoverageObservation], q_lcb: float) -> float:
    """Isotonic-calibrated realized win-rate at the claimed band ``q_lcb``.

    Fits a monotone (non-decreasing) map claimed-LCB -> realized-win-rate across
    the observed bands and reads it at ``q_lcb``. Monotone because a HIGHER claimed
    LCB should, if honest, correspond to a HIGHER realized win-rate; isotonic
    regression is the minimal-assumption estimator of that relationship and
    smooths small-sample non-monotonicity without imposing a parametric form.

    With observations clustered at a single claimed band (the common live case)
    this reduces to the pooled realized win-rate in that band — the right answer.
    """
    import numpy as np

    xs = np.asarray([float(o.q_lcb) for o in observations], dtype=float)
    ys = np.asarray([1.0 if o.won else 0.0 for o in observations], dtype=float)
    # Degenerate: a single distinct claimed band -> pooled mean (isotonic on a
    # single x is just the mean). Short-circuit to avoid a 1-point sklearn fit.
    if np.unique(xs).size <= 1:
        return float(np.mean(ys))
    try:
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
        iso.fit(xs, ys)
        return float(iso.predict([float(q_lcb)])[0])
    except Exception as exc:  # sklearn absent / fit failure -> pooled mean, honest fallback
        logger.warning("isotonic fit failed (%s); falling back to pooled band mean", exc)
        return float(np.mean(ys))


def settlement_backward_coverage_check(
    *,
    city: str,
    metric: str,
    season: str,
    q_lcb: float,
    observations: Iterable[CoverageObservation],
    min_n: int = 30,
) -> CoverageVerdict:
    """License ``q_lcb`` for (city, metric, season) against the settled record.

    Args:
        city, metric, season: cohort keys (for the WARN message + downstream keying).
        q_lcb: the claimed LCB band to license.
        observations: settled (claimed-LCB, won) pairs, ``won`` graded through
            grade_receipt. Build this stream via the spine truth fn — passing a
            hand-set ``won`` defeats the Direction-Law / BinKind / unit antibodies.
        min_n: minimum settled observations to act on (default 30).

    Returns:
        A ``CoverageVerdict``. INSUFFICIENT_DATA when n < min_n (q_lcb unchanged +
        WARN). UNLICENSED when realized < claimed - tol (shrink to realized-1pp).
        LICENSED otherwise (q_lcb unchanged).
    """
    obs = list(observations)
    n = len(obs)
    if n < min_n:
        logger.warning(
            "settlement coverage INSUFFICIENT_DATA city=%s metric=%s season=%s "
            "q_lcb=%.4f: n=%d < min_n=%d — q_lcb unchanged (MC LCB stands)",
            city, metric, season, float(q_lcb), n, min_n,
        )
        return CoverageVerdict(
            status="INSUFFICIENT_DATA",
            q_lcb_in=float(q_lcb),
            q_lcb_out=float(q_lcb),
            n_settlement_observations=n,
            coverage_ratio=None,
            realized_win_rate=None,
        )

    realized = _isotonic_realized_rate(obs, float(q_lcb))
    claimed = float(q_lcb)
    # coverage_ratio = realized / claimed (how well the settled record backs the
    # claimed band). 1.0 = perfectly calibrated; < 1.0 = overconfident claim.
    coverage_ratio = (realized / claimed) if claimed > 0.0 else None

    if realized < claimed - _COVERAGE_TOL:
        # Overconfident: the settled record does not back the claim. Shrink to the
        # realized rate minus the 1pp honesty margin (clamped to [0, 1]).
        shrunk = max(0.0, min(1.0, realized - _SHRINK_MARGIN))
        logger.warning(
            "settlement coverage UNLICENSED city=%s metric=%s season=%s: claimed "
            "q_lcb=%.4f but realized=%.4f over n=%d — shrink q_lcb -> %.4f",
            city, metric, season, claimed, realized, n, shrunk,
        )
        return CoverageVerdict(
            status="UNLICENSED",
            q_lcb_in=claimed,
            q_lcb_out=shrunk,
            n_settlement_observations=n,
            coverage_ratio=coverage_ratio,
            realized_win_rate=realized,
        )

    # LICENSED: the settled record backs the claim within tolerance.
    return CoverageVerdict(
        status="LICENSED",
        q_lcb_in=claimed,
        q_lcb_out=claimed,
        n_settlement_observations=n,
        coverage_ratio=coverage_ratio,
        realized_win_rate=realized,
    )


def apply_settlement_coverage(
    *,
    q_lcb: float,
    verdict: CoverageVerdict,
    enabled: bool,
) -> float:
    """Apply the coverage verdict to a live q_lcb — gated by the shadow flag.

    SHADOW SAFETY: when ``enabled`` is False the shrink is NOT applied; the input
    q_lcb is returned unchanged (byte-identical to legacy) even on an UNLICENSED
    verdict. The verdict is still computed and observable — it just does not move
    the live decision. Only ``enabled=True`` + ``status == "UNLICENSED"`` shrinks.

    The 1-arg verdict is trusted to be for THIS q_lcb (verdict.q_lcb_in == q_lcb);
    LICENSED / INSUFFICIENT_DATA are no-ops by construction (q_lcb_out == q_lcb_in).
    """
    if not enabled:
        return float(q_lcb)
    if verdict.status == "UNLICENSED":
        # Never widen: the shrink only ever LOWERS the LCB (one-sided honesty).
        return float(min(float(q_lcb), float(verdict.q_lcb_out)))
    return float(q_lcb)


def arm_gate_coverage_blocks(verdict: CoverageVerdict) -> tuple[bool, str]:
    """ARM-gate coverage predicate — read UNCONDITIONALLY (flag-independent).

    You cannot arm on an LCB the settled record refuses. The gate BLOCKS when:
      * status is UNLICENSED (the band is overconfident vs settled truth), or
      * coverage_ratio is None (no settled backing — INSUFFICIENT_DATA), or
      * |coverage_ratio - 1| >= 0.10 (band mis-calibrated beyond tolerance).

    Returns (blocked, reason). blocked=False + reason="" means the gate passes.
    """
    if verdict.status == "UNLICENSED":
        return (
            True,
            f"coverage UNLICENSED: realized={verdict.realized_win_rate} < "
            f"claimed={verdict.q_lcb_in} (n={verdict.n_settlement_observations})",
        )
    ratio = verdict.coverage_ratio
    if ratio is None:
        return (
            True,
            f"coverage_ratio is None (status={verdict.status}, "
            f"n={verdict.n_settlement_observations}) — no settled backing to arm on",
        )
    if abs(float(ratio) - 1.0) >= _ARM_RATIO_TOL:
        return (
            True,
            f"coverage_ratio={float(ratio):.4f} is more than {_ARM_RATIO_TOL:.2f} "
            f"from 1.0 — band mis-calibrated for arming",
        )
    return (False, "")
