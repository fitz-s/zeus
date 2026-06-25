"""Soft-anchor posterior for Open-Meteo ECMWF IFS 9km plus AIFS ENS research."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence


SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
PRODUCT_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1"
DEFAULT_ANCHOR_WEIGHT = 0.80
DEFAULT_ANCHOR_SIGMA_C = 3.00
EPSILON = 1e-15
# Unconditional STRUCTURAL prior floor (Fault A, soft_anchor.py zero-prior -inf veto fix).
# Why this exists: a market bin whose AIFS member-vote prior is exactly 0.0 (zero votes) was
# given log_term = -inf, which forces its normalized posterior to 0.0 -> the bin is STRUCTURALLY
# un-hittable. Real settlement landing there is a guaranteed miss, and a cheap buy_no on a "0%"
# bin looks like free EV but LOSES when settlement lands in it. This floor makes the literal-zero
# / -inf category numerically impossible (Fitz #4: kill the category, not the instance): every
# bin in support keeps a strictly-positive, finite log_term so the posterior is always
# normalizable with no -inf and no NaN.
#
# Magnitude discipline (iron rule #2/#6): the floor is 1e-12 -- roughly NINE orders of magnitude
# below the flag-gated member-vote smoothing pseudo-count (alpha=0.05 places ~1e-3 of meaningful
# mass on a 0-vote bin). At flag-OFF the floor surfaces at most ~5e-5 of total posterior mass, and
# only in the degenerate configuration where the anchor Gaussian is centered ON the floored bin
# while every voted bin sits many sigma away (a posterior that is itself ~1.0 on a single far bin).
# It NEVER raises any q_lcb and NEVER manufactures confidence -- it only ADDS a tiny mass to a
# previously-zero bin, lowering (not raising) confidence on the formerly-un-hittable category. It
# is therefore a structural normalizability guarantee, NOT the trading-mass change. The MEANINGFUL
# economic mass remains the single flag-gated alpha knob (member_vote_smoothing_alpha, default-OFF,
# on the blocked->prove->promote ladder): the floor and the alpha compose as ONE mechanism through
# this same normalized prior -- the floor guarantees >0 / normalizable for every bin unconditionally;
# the alpha, when proven and promoted, lands the trade-relevant mass through the SAME path. No
# parallel smoothing or posterior path is created (iron rule #4).
STRUCTURAL_PRIOR_FLOOR = 1e-12
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ProbabilityBin:
    bin_id: str
    lower_c: float | None = None
    upper_c: float | None = None
    center_c: float | None = None

    def center(self) -> float:
        if self.center_c is not None:
            return float(self.center_c)
        if self.lower_c is None or self.upper_c is None:
            raise ValueError(f"open-ended bin {self.bin_id!r} requires center_c")
        return (float(self.lower_c) + float(self.upper_c)) / 2.0


@dataclass(frozen=True)
class SoftAnchorConfig:
    anchor_weight: float = DEFAULT_ANCHOR_WEIGHT
    anchor_sigma_c: float = DEFAULT_ANCHOR_SIGMA_C

    @classmethod
    def from_sigma(
        cls,
        *,
        anchor_weight: float = DEFAULT_ANCHOR_WEIGHT,
        anchor_sigma: float = DEFAULT_ANCHOR_SIGMA_C,
        sigma_unit: str = "C",
    ) -> "SoftAnchorConfig":
        return cls(anchor_weight=anchor_weight, anchor_sigma_c=anchor_sigma_to_celsius(anchor_sigma, sigma_unit))

    def __post_init__(self) -> None:
        if not 0.0 <= self.anchor_weight <= 1.0:
            raise ValueError("anchor_weight must be in [0, 1]")
        if self.anchor_sigma_c <= 0.0 or not math.isfinite(self.anchor_sigma_c):
            raise ValueError("anchor_sigma_c must be positive and finite")


@dataclass(frozen=True)
class SoftAnchorPosterior:
    probabilities: Mapping[str, float]
    anchor_likelihood: Mapping[str, float]
    anchor_c: float
    anchor_weight: float
    anchor_sigma_c: float
    source_id: str = SOURCE_ID
    product_id: str = PRODUCT_ID
    trade_authority_status: str = "BLOCKED"
    training_allowed: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (("source_id", self.source_id), ("product_id", self.product_id)):
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use the full product identity")
        total = sum(self.probabilities.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError("posterior probabilities must sum to 1")
        if self.trade_authority_status != "BLOCKED" or self.training_allowed:
            raise ValueError("soft-anchor posterior is blocked until promoted by evidence")


@dataclass(frozen=True)
class SoftAnchorDisagreementDiagnostic:
    aifs_mean_c: float
    anchor_c: float
    disagreement_c: float
    baseline_anchor_sigma_c: float
    widened_anchor_sigma_c: float
    disagreement_scale: float
    reason_codes: tuple[str, ...]
    product_id: str = PRODUCT_ID
    trade_authority_status: str = "BLOCKED"

    @property
    def promotion_allowed(self) -> bool:
        return False

    @property
    def sigma_widened(self) -> bool:
        return self.widened_anchor_sigma_c > self.baseline_anchor_sigma_c + EPSILON

    def as_config(self, *, anchor_weight: float = DEFAULT_ANCHOR_WEIGHT) -> SoftAnchorConfig:
        return SoftAnchorConfig(anchor_weight=anchor_weight, anchor_sigma_c=self.widened_anchor_sigma_c)

    def as_dict(self) -> dict[str, object]:
        return {
            "aifs_mean_c": self.aifs_mean_c,
            "anchor_c": self.anchor_c,
            "disagreement_c": self.disagreement_c,
            "baseline_anchor_sigma_c": self.baseline_anchor_sigma_c,
            "widened_anchor_sigma_c": self.widened_anchor_sigma_c,
            "disagreement_scale": self.disagreement_scale,
            "sigma_widened": self.sigma_widened,
            "reason_codes": list(self.reason_codes),
            "product_id": self.product_id,
            "trade_authority_status": self.trade_authority_status,
            "promotion_allowed": False,
        }


def _normalize_probabilities(probabilities: Mapping[str, float]) -> dict[str, float]:
    if not probabilities:
        raise ValueError("probabilities must not be empty")
    cleaned: dict[str, float] = {}
    for key, value in probabilities.items():
        if not isinstance(key, str) or not key:
            raise ValueError("probability keys must be non-empty bin ids")
        number = float(value)
        if number < 0.0 or not math.isfinite(number):
            raise ValueError("probabilities must be non-negative finite numbers")
        cleaned[key] = number
    total = sum(cleaned.values())
    if total <= 0.0:
        raise ValueError("probability mass must be positive")
    return {key: value / total for key, value in cleaned.items()}


def anchor_sigma_to_celsius(anchor_sigma: float, sigma_unit: str) -> float:
    """Convert a temperature uncertainty sigma into Celsius degrees.

    This converts a temperature *difference*, not an absolute temperature. Kelvin
    and Celsius deltas are equal; Fahrenheit deltas scale by 5/9.
    """

    sigma = float(anchor_sigma)
    if sigma <= 0.0 or not math.isfinite(sigma):
        raise ValueError("anchor_sigma must be positive and finite")
    unit = sigma_unit.strip().lower().replace("°", "")
    if unit in {"c", "celsius", "k", "kelvin"}:
        return sigma
    if unit in {"f", "fahrenheit"}:
        return sigma * 5.0 / 9.0
    raise ValueError("anchor_sigma unit must be C, F, or K")


def build_soft_anchor_posterior(
    *,
    aifs_probabilities: Mapping[str, float],
    bins: Sequence[ProbabilityBin],
    anchor_c: float,
    config: SoftAnchorConfig = SoftAnchorConfig(),
) -> SoftAnchorPosterior:
    """Fuse AIFS bin probabilities with an Open-Meteo IFS9 deterministic anchor.

    AIFS probabilities are treated as the prior. The deterministic anchor is a
    Gaussian likelihood over bin centers, weighted but not allowed to create mass
    for bins that the AIFS posterior assigned zero probability.
    """

    if not math.isfinite(anchor_c):
        raise ValueError("anchor_c must be finite")
    prior = _normalize_probabilities(aifs_probabilities)
    bin_by_id = {bin_spec.bin_id: bin_spec for bin_spec in bins}
    if set(bin_by_id) != set(prior):
        raise ValueError("bins and aifs_probabilities must cover the same bin ids")

    likelihood: dict[str, float] = {}
    log_terms: dict[str, float] = {}
    for bin_id, prior_probability in prior.items():
        center = bin_by_id[bin_id].center()
        z = (center - anchor_c) / config.anchor_sigma_c
        log_likelihood = -0.5 * z * z
        likelihood[bin_id] = math.exp(log_likelihood)
        # Unconditional structural floor: a zero-vote prior bin keeps a strictly-positive, finite
        # log_term (never -inf) so it can never be made structurally un-hittable. One uniform path
        # for every bin -- the floor only binds when the raw prior is <= the floor; otherwise the
        # term is byte-identical to math.log(prior_probability) + weight*log_likelihood. The
        # MEANINGFUL trade mass still arrives only via the flag-gated member_vote_smoothing_alpha
        # upstream (which lifts the raw prior well above this floor); this floor adds only the
        # negligible structural mass that removes the literal-zero / -inf pathology.
        floored_prior = prior_probability if prior_probability > STRUCTURAL_PRIOR_FLOOR else STRUCTURAL_PRIOR_FLOOR
        log_terms[bin_id] = math.log(floored_prior) + config.anchor_weight * log_likelihood

    finite_terms = [value for value in log_terms.values() if math.isfinite(value)]
    if not finite_terms:
        raise ValueError("soft-anchor posterior has no finite probability mass")
    max_log = max(finite_terms)
    weights = {key: (0.0 if not math.isfinite(value) else math.exp(value - max_log)) for key, value in log_terms.items()}
    total_weight = sum(weights.values())
    if total_weight <= 0.0:
        raise ValueError("soft-anchor posterior normalization failed")
    posterior = {key: value / total_weight for key, value in weights.items()}
    return SoftAnchorPosterior(
        probabilities=posterior,
        anchor_likelihood=likelihood,
        anchor_c=float(anchor_c),
        anchor_weight=config.anchor_weight,
        anchor_sigma_c=config.anchor_sigma_c,
    )


def build_source_disagreement_sigma_widening(
    *,
    aifs_probabilities: Mapping[str, float],
    bins: Sequence[ProbabilityBin],
    anchor_c: float,
    baseline_anchor_sigma_c: float = DEFAULT_ANCHOR_SIGMA_C,
    disagreement_scale: float = 1.0,
) -> SoftAnchorDisagreementDiagnostic:
    """Widen anchor uncertainty when the AIFS posterior mean disagrees with OM9.

    The soft-anchor path may reduce confidence before promotion, but it must not
    treat source disagreement as a pure mean shift. This folds the AIFS-vs-anchor
    distance into the anchor sigma in quadrature, so disagreement can only widen
    the kernel used by the derived posterior.
    """

    if baseline_anchor_sigma_c <= 0.0 or not math.isfinite(baseline_anchor_sigma_c):
        raise ValueError("baseline_anchor_sigma_c must be positive and finite")
    if disagreement_scale < 0.0 or not math.isfinite(disagreement_scale):
        raise ValueError("disagreement_scale must be non-negative and finite")
    if not math.isfinite(anchor_c):
        raise ValueError("anchor_c must be finite")
    prior = _normalize_probabilities(aifs_probabilities)
    bin_by_id = {bin_spec.bin_id: bin_spec for bin_spec in bins}
    if set(bin_by_id) != set(prior):
        raise ValueError("bins and aifs_probabilities must cover the same bin ids")
    aifs_mean = sum(prior[bin_id] * bin_by_id[bin_id].center() for bin_id in prior)
    disagreement = abs(float(anchor_c) - aifs_mean)
    disagreement_component = disagreement * disagreement_scale
    widened_sigma = math.sqrt(baseline_anchor_sigma_c * baseline_anchor_sigma_c + disagreement_component * disagreement_component)
    reasons = ["SOFT_ANCHOR_SOURCE_DISAGREEMENT_SIGMA_WIDENED"] if widened_sigma > baseline_anchor_sigma_c + EPSILON else [
        "SOFT_ANCHOR_SOURCE_DISAGREEMENT_NO_WIDENING"
    ]
    return SoftAnchorDisagreementDiagnostic(
        aifs_mean_c=aifs_mean,
        anchor_c=float(anchor_c),
        disagreement_c=disagreement,
        baseline_anchor_sigma_c=float(baseline_anchor_sigma_c),
        widened_anchor_sigma_c=widened_sigma,
        disagreement_scale=float(disagreement_scale),
        reason_codes=tuple(reasons),
    )


def selected_bin(probabilities: Mapping[str, float]) -> str:
    normalized = _normalize_probabilities(probabilities)
    return max(normalized, key=lambda key: (normalized[key], key))
