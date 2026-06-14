# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   (Stage 4 block lines 1091-1107; "Create src/forecast/day0_conditioner.py"
#   block lines 273-342: Day0ObservationState 277-288, Day0Conditioning 289-298,
#   the high/low center clamp 299-318, and the probability_high_day0_bin /
#   probability_low_day0_bin support transforms 320-340) reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD ONLY
#   — no live-file edits; the day0 extreme is an INPUT to the estimator/integrator,
#   not a downstream clamp; preimage wraps settlement_preimage_offsets:57).
"""Day0 conditioner — the observed running extreme is ground truth.

This is Stage 4 of the q-kernel rebuild (consult_build_spec.md). On an active
trading day, the settlement value of a HIGH market is
``Y = max(observed_high_so_far, X_remaining)`` and of a LOW market is
``Y = min(observed_low_so_far, X_remaining)``, where ``X_remaining ~ N(mu, sigma)``
is the predictive distribution over the *remaining* (not-yet-observed) part of the
day. Because the maximum (resp. minimum) of the day cannot fall below (resp. above)
what has *already been observed*, every settlement bin entirely below the observed
running high — or entirely above the observed running low — is **impossible** and
must carry q = 0.

The defect this replaces: the live day0 lane integrated the bare predictive Normal
``X ~ N(mu, sigma)`` over the bins, so a Tokyo family with an observed-so-far high
of ~21 could still place probability on a 26 bin (or on bins *below* 21) — both
physically impossible once 21 has been observed. A center near 26 cannot coexist
with an observed running high of 21 unless the fresh remaining distribution
actually supports that move upward.

Structural guarantee (operator law — make the bad value impossible, NOT a
downstream cap/gate/clamp that catches it):

  The impossible-bin q = 0 is produced by the SETTLEMENT-CONDITIONED probability
  transform itself, not by a sanity check applied to a bare-Normal output. The
  integrator for an active day computes the probability of the settlement random
  variable ``Y = max(obs_high, X)`` (resp. ``min(obs_low, X)``) directly. For a
  HIGH market and a bin whose settlement PREIMAGE interval is ``[lo, hi)``:

    * ``hi <= obs_high``  → bin entirely below the observed high → ``Y >= obs_high
      >= hi`` can never land in ``[lo, hi)`` → probability is **exactly 0.0** by
      the definition of the transform. There is no bare-Normal mass here to be
      "zeroed out" — the mass was never computed, because ``Y`` cannot reach it.
    * ``lo <= obs_high < hi`` → the bin straddles the observed high. ``Y`` lands
      here whenever ``X < hi`` (either ``X < obs_high`` so ``Y = obs_high`` lands
      in the bin, or ``obs_high <= X < hi`` so ``Y = X`` lands in the bin), i.e.
      ``P(Y in bin) = P(X < hi) = normal_cdf(hi)``. All remaining-distribution
      mass below ``hi`` collapses into the current observed bin.
    * otherwise (``lo > obs_high``) → ``Y = X`` on this bin → the ordinary Normal
      interval ``normal_cdf(hi) - normal_cdf(lo)``.

  Symmetric for LOW (``min``): ``lo >= obs_low`` → 0.0; ``lo < obs_low <= hi`` →
  ``1 - normal_cdf(lo)``; otherwise the Normal interval.

  The center clamp ``mu_after = max(mu_before, obs_high)`` (resp.
  ``min(mu_before, obs_low)``) is the **support transform's center** — the mean of
  the conditioned settlement variable ``Y``, which can never be below the observed
  high — it is NOT a cap on a separately-computed mu. The conditioned probabilities
  above are exact regardless of where ``mu_before`` sits relative to the observed
  extreme; the clamp records the support-corrected center on the receipt so the
  decoupling (mu* far from the observed extreme) is visible.

``probability_high_day0_bin`` / ``probability_low_day0_bin`` take a ``normal_cdf``
callable that is the predictive Normal CDF already folded with mu and sigma —
``normal_cdf(x) = Phi((x - mu) / sigma)`` — so the bin-mass formulae match the
build spec verbatim and the caller supplies whichever (mu, sigma) the
estimator/integrator decided (typically the support-clamped ``center_after_native``).
The bin bounds ``lo`` / ``hi`` are the SETTLEMENT PREIMAGE bounds, derived from the
bin label set via :func:`day0_bin_preimage_native`, which wraps the single live
contract ``src.contracts.settlement_semantics.settlement_preimage_offsets`` — there
is no bare ``settlement_preimage`` to import (drift ledger MINOR row).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Optional

from src.contracts.settlement_semantics import settlement_preimage_offsets


@dataclass(frozen=True)
class Day0ObservationState:
    """The observed running state for a (city, date, metric) on an active day.

    Field names are verbatim from consult_build_spec.md (lines 277-288). This is
    the observed-so-far ground truth the conditioner treats as a hard support
    bound: ``observed_high_native`` / ``observed_low_native`` are the running
    extremes already realized today (in the settlement unit), and the provenance
    fields (``station_id``, ``source``, ``samples_count``, ``latest_observed_at_utc``,
    ``raw_observation_hash``) let a receipt reconstruct exactly which observation
    set produced the support bound.

    ``observed`` is the fail-closed gate: when ``False`` (no day0 observation set
    resolved), the conditioner refuses to assert a support bound rather than
    silently treating "no observation" as "observed extreme of 0".
    """

    observed: bool
    station_id: str
    source: str
    samples_count: int
    latest_observed_at_utc: Optional[datetime]
    observed_high_native: Optional[float]
    observed_low_native: Optional[float]
    observed_extreme_native: Optional[float]
    raw_observation_hash: Optional[str]


@dataclass(frozen=True)
class Day0Conditioning:
    """The support-transform result for one active-day family.

    Field names are verbatim from consult_build_spec.md (lines 289-298).

    * ``active`` — whether a day0 support transform is in force (an observed
      extreme on the relevant side was resolved).
    * ``observed_extreme_native`` — the observed running extreme used as the
      support bound (the high for a high market, the low for a low market).
    * ``support_lower_native`` / ``support_upper_native`` — the conditioned
      settlement support. For a HIGH market the settlement value cannot fall
      below the observed high, so ``support_lower_native = observed_high``
      (``support_upper_native`` stays open / ``None``). For a LOW market the
      settlement value cannot rise above the observed low, so
      ``support_upper_native = observed_low`` (``support_lower_native`` open).
    * ``center_before_native`` — the predictive center mu* before the support
      transform (the estimator's remaining-distribution center).
    * ``center_after_native`` — the support-clamped center of the conditioned
      settlement variable: ``max(mu_before, observed_high)`` for a high market,
      ``min(mu_before, observed_low)`` for a low market. This is the support
      transform's center, NOT a cap on a separately-computed mu.
    * ``status`` — the conditioning verdict (see ``Day0Status``).
    """

    active: bool
    observed_extreme_native: Optional[float]
    support_lower_native: Optional[float]
    support_upper_native: Optional[float]
    center_before_native: float
    center_after_native: float
    status: Literal[
        "NO_DAY0", "HIGH_CLAMPED", "LOW_CLAMPED", "OBS_SOURCE_MISSING_REFUSED"
    ]


# Status literal alias (matches the Day0Conditioning.status field domain verbatim).
Day0Status = Literal[
    "NO_DAY0", "HIGH_CLAMPED", "LOW_CLAMPED", "OBS_SOURCE_MISSING_REFUSED"
]


def probability_high_day0_bin(
    obs_high: float,
    lo: float,
    hi: float,
    normal_cdf: Callable[[float], float],
) -> float:
    """Settlement-conditioned probability mass for a HIGH-market bin given obs_high.

    ``Y = max(obs_high, X_remaining)``; ``normal_cdf(x) = Phi((x - mu) / sigma)``
    is the predictive Normal CDF of the remaining distribution ``X``. ``lo`` / ``hi``
    are the bin's settlement PREIMAGE bounds (native unit).

    Verbatim from consult_build_spec.md lines 324-329:

        if hi <= obs_high:
            return 0.0
        if lo <= obs_high < hi:
            return normal_cdf(hi)   # all remaining values below hi settle into
                                    # the current observed bin
        return normal_cdf(hi) - normal_cdf(lo)

    The first branch is the impossible-bin q = 0: a bin entirely below the observed
    high can never be reached by ``Y = max(obs_high, X)``, so its mass is 0.0 by the
    definition of the transform (not by a downstream clamp on a bare-Normal value).
    """
    if hi <= obs_high:
        return 0.0
    if lo <= obs_high < hi:
        # All remaining-distribution mass below hi (whether X < obs_high, giving
        # Y = obs_high, or obs_high <= X < hi, giving Y = X) settles into this
        # observed bin: P(Y in [lo, hi)) = P(X < hi) = normal_cdf(hi).
        return normal_cdf(hi)
    return normal_cdf(hi) - normal_cdf(lo)


def probability_low_day0_bin(
    obs_low: float,
    lo: float,
    hi: float,
    normal_cdf: Callable[[float], float],
) -> float:
    """Settlement-conditioned probability mass for a LOW-market bin given obs_low.

    ``Y = min(obs_low, X_remaining)``; ``normal_cdf(x) = Phi((x - mu) / sigma)``
    is the predictive Normal CDF of the remaining distribution ``X``. ``lo`` / ``hi``
    are the bin's settlement PREIMAGE bounds (native unit).

    Verbatim from consult_build_spec.md lines 335-340:

        if lo >= obs_low:
            return 0.0
        if lo < obs_low <= hi:
            return 1.0 - normal_cdf(lo)
        return normal_cdf(hi) - normal_cdf(lo)

    The first branch is the impossible-bin q = 0: a bin entirely above the observed
    low can never be reached by ``Y = min(obs_low, X)``, so its mass is 0.0 by the
    definition of the transform.
    """
    if lo >= obs_low:
        return 0.0
    if lo < obs_low <= hi:
        # All remaining-distribution mass above lo (whether X > obs_low, giving
        # Y = obs_low, or lo < X <= obs_low, giving Y = X) settles into this
        # observed bin: P(Y in [lo, hi)) = P(X >= lo) = 1 - normal_cdf(lo).
        return 1.0 - normal_cdf(lo)
    return normal_cdf(hi) - normal_cdf(lo)


def day0_bin_preimage_native(
    bin_low: Optional[float],
    bin_high: Optional[float],
    *,
    rounding_rule: str,
    half_step: float = 0.5,
) -> tuple[float, float]:
    """Expand a bin label set to its settlement PREIMAGE bounds (native unit).

    Wraps the single live contract
    ``src.contracts.settlement_semantics.settlement_preimage_offsets`` so the day0
    conditioner integrates over the SAME preimage interval every other q consumer
    declares (drift ledger: there is no bare ``settlement_preimage``; wrap/call the
    offsets fn). For a bin whose integer label set is ``{bin_low, ..., bin_high}``
    and offsets ``(low_offset, high_offset)``, the preimage interval is
    ``[bin_low + low_offset, bin_high + high_offset)``.

    Open shoulders: ``bin_low is None`` -> lower bound ``-inf``; ``bin_high is
    None`` -> upper bound ``+inf``. The day0 probability functions take finite
    ``lo`` / ``hi``; callers integrating an open shoulder pass ``-math.inf`` /
    ``math.inf`` and rely on a ``normal_cdf`` that returns 0.0 / 1.0 at the
    infinities (standard Normal CDF behavior).
    """
    low_offset, high_offset = settlement_preimage_offsets(
        rounding_rule, half_step=half_step
    )
    lo = -math.inf if bin_low is None else float(bin_low) + low_offset
    hi = math.inf if bin_high is None else float(bin_high) + high_offset
    return (lo, hi)


def condition_day0(
    *,
    metric: Literal["high", "low"],
    obs: Day0ObservationState,
    center_before_native: float,
) -> Day0Conditioning:
    """Build the Day0Conditioning support transform for one active-day family.

    Applies the spec's center clamp (lines 299-318) — the support transform's
    center, NOT a cap:

        For high markets: mu_after = max(mu_before, observed_high);
                          support_lower = observed_high.
        For low markets:  mu_after = min(mu_before, observed_low);
                          support_upper = observed_low.

    Fail-closed: when ``obs.observed`` is False, or the relevant-side observed
    extreme is missing, the conditioner refuses to assert a support bound. It
    returns an inactive conditioning (``status="NO_DAY0"`` when there is simply no
    day0 observation, ``status="OBS_SOURCE_MISSING_REFUSED"`` when an observation
    set was claimed but the relevant-side extreme is absent), leaving
    ``center_after_native == center_before_native`` and the bare predictive Normal
    integration in force.
    """
    if not obs.observed:
        return Day0Conditioning(
            active=False,
            observed_extreme_native=None,
            support_lower_native=None,
            support_upper_native=None,
            center_before_native=center_before_native,
            center_after_native=center_before_native,
            status="NO_DAY0",
        )

    if metric == "high":
        observed_extreme = obs.observed_high_native
        if observed_extreme is None:
            return Day0Conditioning(
                active=False,
                observed_extreme_native=None,
                support_lower_native=None,
                support_upper_native=None,
                center_before_native=center_before_native,
                center_after_native=center_before_native,
                status="OBS_SOURCE_MISSING_REFUSED",
            )
        # Support transform: the day high cannot fall below the observed high, so
        # the conditioned settlement support is [observed_high, +inf) and the
        # conditioned center is pulled up to at least the observed high.
        mu_after = max(center_before_native, observed_extreme)
        return Day0Conditioning(
            active=True,
            observed_extreme_native=observed_extreme,
            support_lower_native=observed_extreme,
            support_upper_native=None,
            center_before_native=center_before_native,
            center_after_native=mu_after,
            status="HIGH_CLAMPED",
        )

    # metric == "low"
    observed_extreme = obs.observed_low_native
    if observed_extreme is None:
        return Day0Conditioning(
            active=False,
            observed_extreme_native=None,
            support_lower_native=None,
            support_upper_native=None,
            center_before_native=center_before_native,
            center_after_native=center_before_native,
            status="OBS_SOURCE_MISSING_REFUSED",
        )
    # Support transform: the day low cannot rise above the observed low, so the
    # conditioned settlement support is (-inf, observed_low] and the conditioned
    # center is pulled down to at most the observed low.
    mu_after = min(center_before_native, observed_extreme)
    return Day0Conditioning(
        active=True,
        observed_extreme_native=observed_extreme,
        support_lower_native=None,
        support_upper_native=observed_extreme,
        center_before_native=center_before_native,
        center_after_native=mu_after,
        status="LOW_CLAMPED",
    )
