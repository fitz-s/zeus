# Created: 2026-06-03
# Last reused or audited: 2026-06-14
# Authority basis: K3 coverage measures whether settled history refutes a claimed q_lcb.
#   ``settlement_coverage_allows_arm`` is an ARM/shrink predicate, not a live-submit
#   license. Live entry layers may require stricter realized evidence.
#   docs/evidence/deadloop_2026-06-14/qlcb_suppression.md + operator
#   RULE 1 (every q_lcb<price rejection is OUR DEFECT until settlement proves otherwise).
#   2026-06-14 REBUILD: the prior check measured CLIMATOLOGY, not CALIBRATION. The live
#   observation stream stamped ONE constant claimed_q_lcb on every settled day and graded
#   a FIXED bin against the whole pooled history, so `_isotonic_realized_rate` degenerated
#   to `np.mean(ys)` = the UNCONDITIONAL base rate of the bin. It then declared any forecast
#   that CONCENTRATED above that base rate "UNLICENSED" and shrank it to base_rate-0.01 —
#   punishing forecast concentration itself, shrinking a PERFECTLY-calibrated forecast
#   identically (qlcb_suppression.md: Singapore high 31C, +0.060 ev/$ -> -0.730 ev/$).
#   This module now measures CALIBRATION: it consumes (per-day ACTUAL claimed q_lcb,
#   realized 0/1) pairs and asks "when the model claimed q_lcb~=X for THIS band, did the
#   outcome realize at >=X?". The honest direction-shrink is PRESERVED (one-sided, only
#   ever LOWERS — P3_architecture.md KEEP-list: "shrink-only coverage direction"). The
#   defect removed is the CLIMATOLOGY MEASUREMENT, not the shrink direction; no UP arm is
#   added (P3 KILL: N7 bidirectional rewrite). Truth comes ONLY from GradedReceipt verdicts
#   (src.contracts.graded_receipt) — never a fresh value-equality / startswith join
#   (which the D1 keystone proved structurally mis-grades temperature labels).
#   Direction Law lives inside grade_receipt: buy_yes WIN iff settled_bin==traded_bin;
#   buy_no WIN iff !=. The win/loss STREAM this module consumes is produced there.
#
#   RULE-1 INERT-DEFAULT: INSUFFICIENT_DATA (too few independent settled CLAIM days, OR
#   the per-day claimed-q_lcb history is unavailable) leaves q_lcb unchanged and the ARM
#   gate does not block because thin data is not proof of overconfidence. This does not
#   make thin data a live-money submit credential.
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
"""settlement_backward_coverage — CALIBRATION-license a q_lcb against the settled record.

For a (city, metric, season) cohort and a claimed ``q_lcb``, read the settlement
CALIBRATION CURVE — pairs of (per-day ACTUAL claimed q_lcb, realized 0/1) graded
through ``grade_receipt`` — isotonic-fit the monotone claimed->realized map, read it
at the claimed band, and return one of:

  LICENSED          — realized >= claimed - tolerance: the band is calibrated or
                      conservative (under-claimed). q_lcb unchanged.
  UNLICENSED        — PROVEN overconfident: n >= min_n independent settled CLAIM days
                      AND realized < claimed - tolerance. Shrink q_lcb to the realized
                      rate minus a 1pp honesty margin; source becomes SETTLEMENT_ISOTONIC.
  INSUFFICIENT_DATA — fewer than ``min_n`` independent settled CLAIM days (or the
                      per-day claimed-q_lcb history is unavailable): q_lcb UNCHANGED
                      + a WARN. The ARM gate does not block on thin data because it is
                      not proof of overconfidence; live entry may still require stronger
                      realized evidence.

WHAT CHANGED (2026-06-14): the observation stream now carries the per-day ACTUAL
claimed q_lcb (varying day to day), NOT one constant stamped on every day. With a
single constant claimed band the isotonic degenerated to ``np.mean(ys)`` = the
UNCONDITIONAL bin base rate (climatology), which shrank every concentrated forecast
to its bin's all-history frequency. Measuring the per-day claimed->realized curve is
a CALIBRATION test (P(realize | claimed X)), not a climatology test (P(bin | any day)).

The SHRINK is HIGH risk (it moves the live decision). It is applied to the live
LCB through ``apply_settlement_coverage``. The shrink is SHRINK-ONLY (one-sided,
only ever LOWERS): there is NO UP arm (P3_architecture.md KILL: N7 bidirectional rewrite).

The ARM gate (``arm_gate_coverage_blocks``) reads the verdict UNCONDITIONALLY — you
cannot arm on an LCB the settled record PROVES overconfident (UNLICENSED). But
INSUFFICIENT_DATA does NOT block arming: lack of per-day claim history is not proof of
overconfidence, and blocking on it suppresses every real concentrated edge before any
claim history can accumulate.
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


# ---------------------------------------------------------------------------
# SINGLE-VOCABULARY ARM/REFUTATION PREDICATES.
#
# The K3 design is explicit (module docstring + arm_gate_coverage_blocks): a settled-record
# verdict that is LICENSED (calibrated/conservative) or INSUFFICIENT_DATA (thin/absent
# claim history) is NON-BLOCKING; only UNLICENSED (PROVEN overconfident) refutes a claim.
#
# allows_arm: the status does NOT refute the claim → arming may proceed on the
#   (possibly shrunk) q_lcb. {LICENSED, INSUFFICIENT_DATA}. INSUFFICIENT_DATA means
#   lack of per-day claim history is NOT proof of overconfidence.
# refutes_claim: the settled record PROVED the claim overconfident → block. {UNLICENSED}.
#
# They are EXHAUSTIVE + MUTUALLY EXCLUSIVE over the three real statuses (one is True, the
# other False, for each of LICENSED / UNLICENSED / INSUFFICIENT_DATA). A None / unknown /
# unevaluated status satisfies NEITHER — it is not an admitting verdict, but it is also not
# a refutation; the caller decides how to treat "no verdict at all" (the credential treats
# it as UNEVALUATED → blocked, distinct from a real INSUFFICIENT_DATA verdict).
# ---------------------------------------------------------------------------
_ARM_ALLOWING_STATUSES = frozenset({"LICENSED", "INSUFFICIENT_DATA"})
_CLAIM_REFUTING_STATUSES = frozenset({"UNLICENSED"})


def settlement_coverage_allows_arm(status: str | None) -> bool:
    """True iff a settled-record VERDICT status permits arming (non-refuting).

    {LICENSED, INSUFFICIENT_DATA}. INSUFFICIENT_DATA means thin/absent claim history is
    not proof of overconfidence. None / UNEVALUATED / unknown → False, but that is NOT
    a refutation (see ``refutes_claim``).
    """
    return str(status or "").strip() in _ARM_ALLOWING_STATUSES


def settlement_coverage_refutes_claim(status: str | None) -> bool:
    """True iff a settled-record VERDICT status PROVES the claimed q_lcb overconfident.

    {UNLICENSED} only. The settled record evaluated the scope with n >= min_n and realized
    materially below the claim. None / INSUFFICIENT_DATA / LICENSED → False (no proof of
    overconfidence — thin data is not a refutation, RULE 1).
    """
    return str(status or "").strip() in _CLAIM_REFUTING_STATUSES


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

    Frozen truth object. ``q_lcb_out`` is the shrink TARGET applied to the live
    LCB by ``apply_settlement_coverage``.
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
    """CALIBRATION-license ``q_lcb`` for (city, metric, season) against the settled record.

    Args:
        city, metric, season: cohort keys (for the WARN message + downstream keying).
        q_lcb: the claimed LCB band to license.
        observations: settled (per-day ACTUAL claimed-LCB, won) pairs, ``won`` graded
            through grade_receipt. Build this stream via the spine truth fn — passing a
            hand-set ``won`` defeats the Direction-Law / BinKind / unit antibodies. Each
            observation MUST carry the q_lcb the model CLAIMED that day (varying day to
            day), NOT one constant — a constant claimed band collapses the isotonic to the
            unconditional bin base rate (climatology), the 2026-06-14 defect this rebuild
            removes.
        min_n: minimum INDEPENDENT settled claim-day observations to act on (default 30).

    Returns:
        A ``CoverageVerdict``. INSUFFICIENT_DATA when n < min_n (q_lcb unchanged + WARN,
        non-refuting for ARM/shrink only; not a live-submit credential). UNLICENSED ONLY on
        PROVEN overconfidence (n >= min_n AND realized < claimed - tol -> shrink to
        realized-1pp). LICENSED when realized >= claimed - tol (calibrated or conservative;
        q_lcb unchanged).
    """
    obs = list(observations)
    n = len(obs)
    if n < min_n:
        logger.warning(
            "settlement coverage INSUFFICIENT_DATA (non-refuting for ARM only; "
            "not a live-submit credential) city=%s metric=%s season=%s q_lcb=%.4f: "
            "n=%d < min_n=%d — MC LCB stands for shrink math only",
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
) -> float:
    """Apply the coverage verdict to a live q_lcb.

    The 1-arg verdict is trusted to be for THIS q_lcb (verdict.q_lcb_in == q_lcb);
    LICENSED / INSUFFICIENT_DATA are no-ops by construction (q_lcb_out == q_lcb_in).
    """
    if verdict.status == "UNLICENSED":
        # Never widen: the shrink only ever LOWERS the LCB (one-sided honesty).
        return float(min(float(q_lcb), float(verdict.q_lcb_out)))
    return float(q_lcb)


def arm_gate_coverage_blocks(verdict: CoverageVerdict) -> tuple[bool, str]:
    """ARM-gate coverage predicate — read UNCONDITIONALLY (flag-independent).

    REBUILD (2026-06-14, RULE 1): this gate is a PROVEN-OVERCONFIDENCE catch, not a
    default-deny. It BLOCKS arming ONLY on a verdict the settled record PROVES is
    overconfident:
      * status is UNLICENSED — n >= min_n independent settled claim-days AND the
        realized calibration rate fell materially BELOW the claimed band.

    It does NOT block on:
      * INSUFFICIENT_DATA / coverage_ratio is None — lack of per-day claim history is
        NOT proof of overconfidence. Blocking here is the default-deny suppression
        RULE 1 forbids (and it would perversely require corroboration we cannot yet
        compute, freezing every concentrated edge before any claim history accrues).
        The EMOS/MC q_lcb already carries its own conservative LCB floor + N_eff width
        correction + sigma-shape; this gate adds a check only WHEN the settled record
        can prove overconfidence.
      * coverage_ratio > 1 (realized ABOVE claimed) — that is a CONSERVATIVE / calibrated
        band (the model under-claimed), which is exactly what we WANT to arm on, never a
        reason to block. The prior ``|ratio-1| >= 0.10`` rule wrongly blocked conservative
        bands too; a one-sided lower bound is honest precisely when realized >= claimed.

    Note: when status is UNLICENSED the realized rate is materially below the claim, so a
    redundant ratio test is unnecessary — the status leg is the single source of truth.

    Returns (blocked, reason). blocked=False + reason="" means the gate passes.
    """
    # SINGLE-VOCABULARY dispatch (pr408 #1): the refutation rule lives in ONE predicate,
    # UNLICENSED is the only refuting status; LICENSED / INSUFFICIENT_DATA do not block
    # the ARM gate because thin data is not proof of overconfidence.
    if settlement_coverage_refutes_claim(verdict.status):
        return (
            True,
            f"coverage UNLICENSED (proven overconfident): realized="
            f"{verdict.realized_win_rate} < claimed={verdict.q_lcb_in} "
            f"(n={verdict.n_settlement_observations})",
        )
    # LICENSED (calibrated / conservative) and INSUFFICIENT_DATA (thin / absent history)
    # are BOTH non-blocking: only PROVEN overconfidence refuses an arm.
    return (False, "")
