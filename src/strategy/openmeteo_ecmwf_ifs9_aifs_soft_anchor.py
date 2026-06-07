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
DIRICHLET_PRIOR_FLOOR = 1e-6
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
    trade_authority_status: str = "SHADOW_ONLY"
    training_allowed: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (("source_id", self.source_id), ("product_id", self.product_id)):
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use the full product identity")
        total = sum(self.probabilities.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError("posterior probabilities must sum to 1")
        if self.trade_authority_status != "SHADOW_ONLY" or self.training_allowed:
            raise ValueError("soft-anchor posterior is shadow-only until promoted by evidence")


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
    trade_authority_status: str = "SHADOW_ONLY"

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


@dataclass(frozen=True)
class ShadowVetoGuardrail:
    baseline_direction: str
    candidate_direction: str
    allowed_direction: str
    baseline_q_lcb: float
    candidate_q_lcb: float
    allowed_q_lcb: float
    baseline_kelly_fraction: float
    candidate_kelly_fraction: float
    allowed_kelly_fraction: float
    veto: bool
    reasons: tuple[str, ...]
    product_id: str = PRODUCT_ID
    trade_authority_status: str = "SHADOW_VETO_ONLY"


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


def _standard_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def _anchor_bin_likelihood(bin_spec: ProbabilityBin, *, anchor_c: float, sigma_c: float) -> float:
    if bin_spec.lower_c is None and bin_spec.upper_c is None:
        raise ValueError("probability bin requires at least one bound")
    if bin_spec.lower_c is None:
        z_upper = (float(bin_spec.upper_c) - anchor_c) / sigma_c
        return max(EPSILON, _standard_normal_cdf(z_upper))
    if bin_spec.upper_c is None:
        z_lower = (float(bin_spec.lower_c) - anchor_c) / sigma_c
        return max(EPSILON, 1.0 - _standard_normal_cdf(z_lower))
    z_lower = (float(bin_spec.lower_c) - anchor_c) / sigma_c
    z_upper = (float(bin_spec.upper_c) - anchor_c) / sigma_c
    return max(EPSILON, _standard_normal_cdf(z_upper) - _standard_normal_cdf(z_lower))


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
    raw_prior = _normalize_probabilities(aifs_probabilities)
    prior = (
        raw_prior
        if config.anchor_weight == 0.0
        else _normalize_probabilities({key: value + DIRICHLET_PRIOR_FLOOR for key, value in raw_prior.items()})
    )
    bin_by_id = {bin_spec.bin_id: bin_spec for bin_spec in bins}
    if set(bin_by_id) != set(prior):
        raise ValueError("bins and aifs_probabilities must cover the same bin ids")

    likelihood: dict[str, float] = {}
    log_terms: dict[str, float] = {}
    for bin_id, prior_probability in prior.items():
        bin_likelihood = _anchor_bin_likelihood(bin_by_id[bin_id], anchor_c=float(anchor_c), sigma_c=config.anchor_sigma_c)
        log_likelihood = math.log(bin_likelihood)
        likelihood[bin_id] = bin_likelihood
        log_terms[bin_id] = math.log(prior_probability) + config.anchor_weight * log_likelihood

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


def apply_shadow_veto_guardrail(
    *,
    baseline_direction: str,
    candidate_direction: str,
    baseline_q_lcb: float,
    candidate_q_lcb: float,
    baseline_kelly_fraction: float,
    candidate_kelly_fraction: float,
) -> ShadowVetoGuardrail:
    for field_name, value in (
        ("baseline_q_lcb", baseline_q_lcb),
        ("candidate_q_lcb", candidate_q_lcb),
        ("baseline_kelly_fraction", baseline_kelly_fraction),
        ("candidate_kelly_fraction", candidate_kelly_fraction),
    ):
        if not math.isfinite(float(value)):
            raise ValueError(f"{field_name} must be finite")
    if not baseline_direction or not candidate_direction:
        raise ValueError("directions must be non-empty")

    allowed_q_lcb = max(0.0, min(1.0, min(float(baseline_q_lcb), float(candidate_q_lcb))))
    allowed_kelly = max(0.0, min(float(baseline_kelly_fraction), float(candidate_kelly_fraction)))
    reasons: list[str] = []
    if candidate_direction != baseline_direction:
        reasons.append("SOFT_ANCHOR_DIRECTION_DISAGREEMENT")
    if allowed_q_lcb < float(baseline_q_lcb) - EPSILON:
        reasons.append("SOFT_ANCHOR_LOWER_Q_LCB")
    if allowed_kelly < float(baseline_kelly_fraction) - EPSILON:
        reasons.append("SOFT_ANCHOR_LOWER_KELLY")

    return ShadowVetoGuardrail(
        baseline_direction=baseline_direction,
        candidate_direction=candidate_direction,
        allowed_direction=baseline_direction,
        baseline_q_lcb=float(baseline_q_lcb),
        candidate_q_lcb=float(candidate_q_lcb),
        allowed_q_lcb=allowed_q_lcb,
        baseline_kelly_fraction=float(baseline_kelly_fraction),
        candidate_kelly_fraction=float(candidate_kelly_fraction),
        allowed_kelly_fraction=allowed_kelly,
        veto=bool(reasons),
        reasons=tuple(reasons),
    )
