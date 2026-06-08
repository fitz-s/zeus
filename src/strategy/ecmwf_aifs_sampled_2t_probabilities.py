"""AIFS ENS sampled-2t extrema to market-bin probability bridge."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from src.data.ecmwf_aifs_sampled_2t_localday import (
    AGGREGATION_WINDOW_POLICY,
    HIGH_DATA_VERSION,
    LOW_DATA_VERSION,
    PHYSICAL_QUANTITY,
    PRODUCT_ID,
    SOURCE_ID,
    AifsSampledLocalDayExtraction,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import ProbabilityBin
from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import (
    SoftAnchorConfig,
    SoftAnchorPosterior,
    build_soft_anchor_posterior,
)


Metric = Literal["high", "low"]
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"

# Additive (Laplace/Dirichlet) symmetric pseudo-count for the AIFS member-vote prior.
# THE_PATH member-vote smoothing (2026-06-07): a small Dirichlet(alpha) pseudo-count added to
# every market bin BEFORE normalisation, so prior_k = (votes_k + alpha) / (total + K*alpha) is
# strictly positive for EVERY bin. This lifts the soft_anchor.py:197-198 prior<=0 -> -inf veto
# that otherwise makes a 0-vote bin structurally un-hittable (Fitz #5: kill the category).
#
# alpha = 0.05 is deliberately gentle and NOT overfit to any city:
#   * It is << 1 vote, so it never competes with a single real member vote (a Dirichlet
#     pseudo-count of 0.05 is 1/20th of one ensemble member).
#   * With the live 51-member ensemble it gives a 0-vote bin a prior floor of ~0.001 (alpha/
#     (51+K*alpha)) — enough to make the veto term finite so the 0.80-weight anchor Gaussian
#     can place the REAL mass, while the bin's own prior mass stays negligible.
#   * A modal/high-vote bin barely moves (e.g. 40/51 -> within ~1 pp for typical K), so the
#     soft anchor still dominates exactly as before.
# Applied ONLY when explicitly requested (flag-gated upstream); the default path is unchanged.
MEMBER_VOTE_SMOOTHING_ALPHA = 0.05


@dataclass(frozen=True)
class AifsTemperatureBin:
    bin_id: str
    lower_c: float | None = None
    upper_c: float | None = None
    center_c: float | None = None
    display_unit: Literal["C", "F"] = "C"
    settlement_unit: Literal["C", "F"] = "C"
    rounding_rule: Literal["wmo_half_up", "oracle_truncate"] = "wmo_half_up"

    def __post_init__(self) -> None:
        if not self.bin_id:
            raise ValueError("bin_id must be set")
        if _FORBIDDEN_TRANSCRIPT_ALIAS in self.bin_id.lower():
            raise ValueError("bin_id must not use transcript shorthand")
        if self.lower_c is None and self.upper_c is None:
            raise ValueError("temperature bin requires at least one bound")
        for field_name, value in (("lower_c", self.lower_c), ("upper_c", self.upper_c), ("center_c", self.center_c)):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")
        if self.lower_c is not None and self.upper_c is not None and float(self.lower_c) > float(self.upper_c):
            raise ValueError("lower_c cannot be above upper_c")
        if self.display_unit not in {"C", "F"}:
            raise ValueError("display_unit must be C or F")
        if self.settlement_unit not in {"C", "F"}:
            raise ValueError("settlement_unit must be C or F")
        if self.rounding_rule not in {"wmo_half_up", "oracle_truncate"}:
            raise ValueError("rounding_rule must be wmo_half_up or oracle_truncate")

    def contains(self, value_c: float) -> bool:
        value = float(value_c)
        if not math.isfinite(value):
            raise ValueError("temperature value must be finite")
        if self.lower_c is not None and value < float(self.lower_c):
            return False
        if self.upper_c is not None and value > float(self.upper_c):
            return False
        return True

    def contains_settled_source_value(self, value_c: float) -> bool:
        """Return membership after applying the resolution source's unit first."""
        value = float(value_c)
        if not math.isfinite(value):
            raise ValueError("temperature value must be finite")
        if self.display_unit == "C" and self.settlement_unit == "F":
            settled_f = math.floor((value * 9.0 / 5.0 + 32.0) + 0.5)
            display_c = math.floor(((settled_f - 32.0) * 5.0 / 9.0) + 0.5)
            return self.contains(float(display_c))
        if self.display_unit == "F" and self.settlement_unit == "F":
            settled_f = math.floor((value * 9.0 / 5.0 + 32.0) + 0.5)
            lower_f = None if self.lower_c is None else float(self.lower_c) * 9.0 / 5.0 + 32.0
            upper_f = None if self.upper_c is None else float(self.upper_c) * 9.0 / 5.0 + 32.0
            if lower_f is not None and settled_f < math.floor(lower_f + 0.5):
                return False
            if upper_f is not None and settled_f > math.floor(upper_f + 0.5):
                return False
            return True
        if self.display_unit == "C" and self.settlement_unit == "C" and self.rounding_rule == "oracle_truncate":
            settled_c = math.floor(value)
            return self.contains(float(settled_c))
        settled_c = math.floor(value + 0.5)
        return self.contains(float(settled_c))

    def soft_anchor_bin(self) -> ProbabilityBin:
        center = self.center_c
        if center is None:
            if self.lower_c is not None and self.upper_c is not None:
                center = (float(self.lower_c) + float(self.upper_c)) / 2.0
            else:
                raise ValueError(f"open-ended bin {self.bin_id!r} requires center_c for soft-anchor fusion")
        return ProbabilityBin(
            bin_id=self.bin_id,
            lower_c=self.lower_c,
            upper_c=self.upper_c,
            center_c=float(center),
        )


@dataclass(frozen=True)
class AifsBinProbabilityResult:
    metric: Metric
    probabilities: Mapping[str, float]
    member_assignments: Mapping[str, str]
    member_values_c: Mapping[str, float]
    soft_anchor_bins: tuple[ProbabilityBin, ...]
    source_id: str = SOURCE_ID
    product_id: str = PRODUCT_ID
    data_version: str = HIGH_DATA_VERSION
    physical_quantity: str = PHYSICAL_QUANTITY
    aggregation_window_policy: str = AGGREGATION_WINDOW_POLICY
    probability_source: str = "aifs_sampled_2t_member_frequency"
    trade_authority_status: str = "SHADOW_ONLY"
    training_allowed: bool = False

    def __post_init__(self) -> None:
        if self.metric not in {"high", "low"}:
            raise ValueError("metric must be high or low")
        expected_data_version = HIGH_DATA_VERSION if self.metric == "high" else LOW_DATA_VERSION
        if self.data_version != expected_data_version:
            raise ValueError("data_version must match metric")
        for field_name, value in (("source_id", self.source_id), ("product_id", self.product_id), ("data_version", self.data_version)):
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use the full product identity")
        if self.trade_authority_status != "SHADOW_ONLY" or self.training_allowed:
            raise ValueError("AIFS sampled-2t probabilities are shadow-only until promoted by evidence")
        total = sum(float(value) for value in self.probabilities.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError("AIFS probabilities must sum to 1")
        if set(self.probabilities) != {item.bin_id for item in self.soft_anchor_bins}:
            raise ValueError("probabilities and soft_anchor_bins must cover the same bin ids")
        if set(self.member_assignments) != set(self.member_values_c):
            raise ValueError("member assignments and member values must cover the same members")


@dataclass(frozen=True)
class OpenMeteoIfs9AifsSoftAnchorResearchResult:
    metric: Metric
    aifs_probabilities: AifsBinProbabilityResult
    posterior: SoftAnchorPosterior
    anchor_value_c: float
    anchor_source_id: str
    anchor_product_id: str
    source_id: str = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
    product_id: str = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1"
    trade_authority_status: str = "SHADOW_ONLY"
    training_allowed: bool = False

    def __post_init__(self) -> None:
        if self.metric != self.aifs_probabilities.metric:
            raise ValueError("result metric must match AIFS probabilities metric")
        if not math.isfinite(float(self.anchor_value_c)):
            raise ValueError("anchor_value_c must be finite")
        for field_name, value in (("source_id", self.source_id), ("product_id", self.product_id)):
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use the full product identity")
        if self.trade_authority_status != "SHADOW_ONLY" or self.training_allowed:
            raise ValueError("soft-anchor research result is shadow-only until promoted by evidence")


def _normalize_metric(metric: str) -> Metric:
    normalized = metric.lower()
    if normalized not in {"high", "low"}:
        raise ValueError("metric must be high or low")
    return normalized  # type: ignore[return-value]


def _member_value(member: object, metric: Metric) -> float:
    value = getattr(member, "high_c" if metric == "high" else "low_c")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("member extrema must be finite")
    return number


def _contains_settlement_value(value_c: float, bin_spec: AifsTemperatureBin, *, settlement_step_c: float) -> bool:
    if bin_spec.display_unit != "C" or bin_spec.settlement_unit != "C" or bin_spec.rounding_rule != "wmo_half_up":
        return bin_spec.contains_settled_source_value(value_c)
    half_step = float(settlement_step_c) / 2.0
    value = float(value_c)
    lower = None if bin_spec.lower_c is None else float(bin_spec.lower_c) - half_step
    upper = None if bin_spec.upper_c is None else float(bin_spec.upper_c) + half_step
    if lower is not None and value < lower:
        return False
    if upper is not None and value >= upper:
        return False
    return True


def _assign_bin(value_c: float, bins: Sequence[AifsTemperatureBin], *, settlement_step_c: float) -> str:
    matches = [
        bin_spec.bin_id
        for bin_spec in bins
        if _contains_settlement_value(value_c, bin_spec, settlement_step_c=settlement_step_c)
    ]
    if not matches:
        raise ValueError(f"temperature value {value_c}C does not match any market bin")
    if len(matches) > 1:
        raise ValueError(f"temperature value {value_c}C matches multiple market bins: {matches}")
    return matches[0]


def _validate_full_family_bins(bins: Sequence[AifsTemperatureBin], *, settlement_step_c: float) -> None:
    if settlement_step_c <= 0.0 or not math.isfinite(settlement_step_c):
        raise ValueError("settlement_step_c must be positive and finite")
    display_units = {bin_spec.display_unit for bin_spec in bins}
    if len(display_units) != 1:
        raise ValueError("temperature bin family must use one display unit")
    effective_step_c = 1.0 if display_units == {"C"} else float(settlement_step_c)
    ordered = sorted(bins, key=lambda bin_spec: (float("-inf") if bin_spec.lower_c is None else float(bin_spec.lower_c)))
    if ordered[0].lower_c is not None:
        raise ValueError("temperature bin family requires a lower open shoulder")
    if ordered[-1].upper_c is not None:
        raise ValueError("temperature bin family requires an upper open shoulder")
    for left, right in zip(ordered, ordered[1:]):
        if left.upper_c is None:
            raise ValueError("only the final temperature bin may be upper open-ended")
        if right.lower_c is None:
            raise ValueError("only the first temperature bin may be lower open-ended")
        left_upper = float(left.upper_c)
        right_lower = float(right.lower_c)
        if right_lower <= left_upper:
            raise ValueError(f"temperature bin family overlaps between {left.bin_id!r} and {right.bin_id!r}")
        if right_lower - left_upper > effective_step_c + 1e-9:
            raise ValueError(f"temperature bin family has a gap between {left.bin_id!r} and {right.bin_id!r}")


def build_aifs_sampled_2t_bin_probabilities(
    extraction: AifsSampledLocalDayExtraction,
    *,
    metric: str,
    bins: Sequence[AifsTemperatureBin],
    settlement_step_c: float = 1.0,
    bias_shift_c: float | None = None,
    member_vote_smoothing_alpha: float | None = None,
) -> AifsBinProbabilityResult:
    """Convert AIFS member local-day extrema into uncalibrated bin probabilities.

    Each member contributes exactly one vote to the requested high/low market-bin
    family. The output is the AIFS sampled-2t prior used by the Open-Meteo ECMWF
    IFS 9km deterministic soft-anchor posterior; it is not B0 calibration,
    EMOS, raw-honest fallback, or trade authority.

    ``member_vote_smoothing_alpha`` (THE_PATH member-vote smoothing, 2026-06-07): flag-gated
    additive Laplace/Dirichlet pseudo-count. When set (>0), the per-bin prior becomes
    ``prior_k = (votes_k + alpha) / (total + K*alpha)`` over the K market bins, so EVERY bin
    is strictly positive and the soft_anchor.py:197-198 ``prior<=0 -> -inf`` veto can never
    fire — letting the 0.80-weight anchor Gaussian place mass in formerly-0-vote bins (the
    soft anchor becomes soft) and killing the structurally-un-hittable-bin category. The
    smoothed prior STILL sums to 1 (mass-preserving). ``None`` or ``0.0`` reproduces the raw
    ``count/total`` frequency BYTE-IDENTICALLY (default-OFF). Use ``MEMBER_VOTE_SMOOTHING_ALPHA``
    for the gentle, non-overfit value.

    ``bias_shift_c`` (P2_BLEND.md §3,§4): per-city Empirical-Bayes forecast bias in
    degC, sign convention ``bias = forecast - actual`` (negative = cold). When set,
    each member extremum is corrected ``corrected = raw - bias_shift_c`` BEFORE the
    vote is cast, so the votes move (mitigating BOTH the cold center AND the
    soft_anchor zero-prior veto on cold-shifted bins). The member extrema are ALWAYS
    degC here (high_c/low_c are unit-normalized on ingest), so the shift is degC-vs-degC
    and unit-correct by construction — NO Fahrenheit ×1.8 (that belongs to the legacy
    edli p_raw path where members carry the city's settlement unit). ``None`` or ``0.0``
    is byte-identical to the un-corrected path (default-OFF).
    """

    normalized_metric = _normalize_metric(metric)
    if not isinstance(extraction, AifsSampledLocalDayExtraction):
        raise TypeError("extraction must be AifsSampledLocalDayExtraction")
    if not bins:
        raise ValueError("bins must not be empty")
    bin_ids = [bin_spec.bin_id for bin_spec in bins]
    if len(set(bin_ids)) != len(bin_ids):
        raise ValueError("bin ids must be unique")
    _validate_full_family_bins(bins, settlement_step_c=settlement_step_c)

    shift = 0.0 if bias_shift_c is None else float(bias_shift_c)
    if not math.isfinite(shift):
        raise ValueError("bias_shift_c must be finite")

    alpha = 0.0 if member_vote_smoothing_alpha is None else float(member_vote_smoothing_alpha)
    if not math.isfinite(alpha) or alpha < 0.0:
        raise ValueError("member_vote_smoothing_alpha must be a non-negative finite number")

    counts = {bin_spec.bin_id: 0 for bin_spec in bins}
    assignments: dict[str, str] = {}
    member_values: dict[str, float] = {}
    for member in extraction.members:
        # corrected = raw - bias (bias = forecast - actual); cold bias warms the value.
        value_c = _member_value(member, normalized_metric) - shift
        bin_id = _assign_bin(value_c, bins, settlement_step_c=settlement_step_c)
        counts[bin_id] += 1
        assignments[member.member_id] = bin_id
        member_values[member.member_id] = value_c

    total_members = len(extraction.members)
    if total_members <= 0:
        raise ValueError("AIFS probability bridge requires at least one member")
    if alpha == 0.0:
        # Default-OFF path: raw count/total frequency, byte-identical to pre-smoothing.
        probabilities = {bin_id: count / total_members for bin_id, count in counts.items()}
    else:
        # Additive Laplace/Dirichlet smoothing: every bin strictly positive, still sums to 1.
        # prior_k = (votes_k + alpha) / (total + K*alpha); K = number of market bins.
        smoothed_denominator = total_members + len(counts) * alpha
        probabilities = {bin_id: (count + alpha) / smoothed_denominator for bin_id, count in counts.items()}
    return AifsBinProbabilityResult(
        metric=normalized_metric,
        probabilities=probabilities,
        member_assignments=assignments,
        member_values_c=member_values,
        soft_anchor_bins=tuple(bin_spec.soft_anchor_bin() for bin_spec in bins),
        data_version=HIGH_DATA_VERSION if normalized_metric == "high" else LOW_DATA_VERSION,
    )


def build_openmeteo_ifs9_aifs_soft_anchor_result(
    *,
    aifs_extraction: AifsSampledLocalDayExtraction,
    openmeteo_anchor: OpenMeteoIfs9LocalDayAnchor,
    metric: str,
    bins: Sequence[AifsTemperatureBin],
    config: SoftAnchorConfig = SoftAnchorConfig(),
    settlement_step_c: float = 1.0,
    bias_shift_c: float | None = None,
    member_vote_smoothing_alpha: float | None = None,
) -> OpenMeteoIfs9AifsSoftAnchorResearchResult:
    """Build the fixed research posterior from raw AIFS and Open-Meteo anchors.

    ``member_vote_smoothing_alpha`` (THE_PATH member-vote smoothing, 2026-06-07): flag-gated
    additive Laplace/Dirichlet pseudo-count on the AIFS member votes (forwarded to
    build_aifs_sampled_2t_bin_probabilities). When set (>0) every market bin gets a strictly
    positive prior, so the soft_anchor.py:197-198 zero-prior ``-inf`` veto never fires and the
    0.80-weight anchor Gaussian can mass formerly-0-vote bins WITHIN its support. The smoothing
    only WIDENS the point q (the anchor reaches more bins) and feeds the SHIPPED q_lcb settlement
    sigma floor unchanged downstream: that floor still grounds q_lcb at the realized residual on
    the (now slightly wider) point q, so NO overconfidence is introduced on the newly-massed bins
    (iron rule #6). ``None`` / ``0.0`` is byte-identical to today (default-OFF).

    ``bias_shift_c`` (P2_BLEND.md §3,§4,§5): per-city EB forecast bias (degC, sign
    ``forecast - actual``). When set, BOTH the AIFS member votes (via
    build_aifs_sampled_2t_bin_probabilities) AND the deterministic anchor center are
    corrected ``corrected = raw - bias_shift_c`` BEFORE the soft-anchor fusion and the
    zero-prior veto (soft_anchor.py:197-198). LAYERING: this center correction precedes
    the veto and any downstream q_lcb sigma widening, so the widened interval covers the
    corrected location (not the cold-shifted one) and the veto vetoes the corrected
    zero-vote bins. ``None`` / ``0.0`` is byte-identical to today (default-OFF).
    """

    normalized_metric = _normalize_metric(metric)
    if not isinstance(openmeteo_anchor, OpenMeteoIfs9LocalDayAnchor):
        raise TypeError("openmeteo_anchor must be OpenMeteoIfs9LocalDayAnchor")
    shift = 0.0 if bias_shift_c is None else float(bias_shift_c)
    if not math.isfinite(shift):
        raise ValueError("bias_shift_c must be finite")
    aifs_probabilities = build_aifs_sampled_2t_bin_probabilities(
        aifs_extraction,
        metric=normalized_metric,
        bins=bins,
        settlement_step_c=settlement_step_c,
        bias_shift_c=bias_shift_c,
        member_vote_smoothing_alpha=member_vote_smoothing_alpha,
    )
    raw_anchor_value_c = openmeteo_anchor.high_c if normalized_metric == "high" else openmeteo_anchor.low_c
    # Shift the anchor center consistently with the member votes (corrected = raw - bias).
    anchor_value_c = raw_anchor_value_c - shift
    posterior = build_soft_anchor_posterior(
        aifs_probabilities=aifs_probabilities.probabilities,
        bins=aifs_probabilities.soft_anchor_bins,
        anchor_c=anchor_value_c,
        config=config,
    )
    return OpenMeteoIfs9AifsSoftAnchorResearchResult(
        metric=normalized_metric,
        aifs_probabilities=aifs_probabilities,
        posterior=posterior,
        anchor_value_c=anchor_value_c,
        anchor_source_id=openmeteo_anchor.source_id,
        anchor_product_id=openmeteo_anchor.product_id,
    )
