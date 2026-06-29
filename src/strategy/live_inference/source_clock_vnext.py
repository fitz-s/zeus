"""Source-clocked settlement posterior and executable admission primitives.

This module is intentionally pure: it carries the source availability clock,
market-reaction tightening, settlement-direction bounds, sparse source weights,
and binary log-utility admission math without reading venue state or writing
runtime truth. Callers must supply executable book costs and source/update facts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Sequence


SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES = 10.0
_EPSILON = 1e-12
_SOURCE_FAMILY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ecmwf_", "ecmwf"),
    ("icon_", "dwd_icon"),
    ("ukmo_", "ukmo"),
    ("gfs_", "ncep"),
    ("hrrr", "ncep"),
    ("nam_", "ncep"),
    ("ncep_", "ncep"),
    ("meteofrance_", "meteofrance"),
    ("dmi_", "dmi"),
    ("kma_", "kma"),
    ("jma_", "jma"),
    # Station-calibrated official forecasts (HKO 9-day, etc.) — each agency is its own
    # decorrelated provider family (the station forecast is independent of the gridded models).
    ("hko_", "hko"),
    ("cwa_", "cwa"),
)

MarketReactionBucket = Literal[
    "reaction_unknown",
    "reverse_reaction",
    "underreacted",
    "partial_reaction",
    "absorbed",
    "overreacted",
]


@dataclass(frozen=True)
class SourceRunClock:
    source_id: str
    provider_family: str
    run_initialisation_time: datetime
    run_availability_time: datetime
    zeus_observed_time: datetime | None = None
    update_interval_seconds: int | None = None
    temporal_resolution_seconds: int | None = None
    spatial_resolution_km: float | None = None
    forecast_horizon_hours: int | None = None
    api_surface: str | None = None
    freshness_state: str = "FRESH"


@dataclass(frozen=True)
class SettlementStationGeometry:
    city: str
    metric: str
    market_source: str
    settlement_station_id: str
    station_lat: float
    station_lon: float
    station_elevation: float | None
    source_id: str
    model_grid_lat: float
    model_grid_lon: float
    model_grid_elevation: float | None
    grid_distance_km: float
    elevation_delta_m: float | None
    cell_selection: str
    station_alignment_score: float


@dataclass(frozen=True)
class SourceWeightObservation:
    source_id: str
    provider_family: str
    settlement_logloss: float
    brier: float
    crps: float | None = None
    rmse: float | None = None
    mae: float | None = None
    bias: float | None = None
    stale: bool = False


@dataclass(frozen=True)
class CityMetricLeadSourceWeight:
    city: str
    metric: str
    lead: int
    source_id: str
    provider_family: str
    weight: float


@dataclass(frozen=True)
class WeightedSource:
    source_id: str
    provider_family: str
    weight: float
    settlement_logloss: float
    brier: float
    crps: float | None = None
    rmse: float | None = None
    mae: float | None = None
    bias: float | None = None


@dataclass(frozen=True)
class MarketReactionState:
    token_id: str
    source_update_id: str
    q_shock: float
    price_pre: float | None
    price_5m: float | None = None
    price_15m: float | None = None
    price_60m: float | None = None
    reaction_fraction: float | None = None

    @property
    def bucket(self) -> MarketReactionBucket:
        return market_reaction_bucket(self.reaction_fraction)


@dataclass(frozen=True)
class AdmissionCalibrationObservation:
    city: str
    metric: str
    lead: int
    side: str
    source_age_bucket: str
    market_reaction_bucket: MarketReactionBucket
    q_point: float
    q_lcb: float
    realized_hit: bool

    @property
    def calibration_gap(self) -> float:
        return admission_calibration_gap(
            q_point=self.q_point,
            realized_hit=self.realized_hit,
        )


@dataclass(frozen=True)
class IntraHourExtremeCorrection:
    city: str
    metric: str
    lead: int
    source_id: str
    season_bucket: str
    correction_c: float
    residual_scale_c: float = 0.0


@dataclass(frozen=True)
class SourceClockAdmissionInput:
    q_point: float
    q_lcb: float
    executable_cost: float
    decision_time: datetime
    source_run: SourceRunClock | None
    market_reaction: MarketReactionState | None
    side: str = "YES"
    allow_unknown_market_reaction: bool = False
    availability_wait_minutes: float = SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES


@dataclass(frozen=True)
class SourceClockAdmissionDecision:
    admitted: bool
    reason: str | None
    q_point: float
    q_lcb: float
    q_exec_lcb: float
    executable_cost: float
    edge_lcb: float
    kelly_spend_fraction: float
    expected_log_growth: float
    source_age_bucket: str
    market_reaction_bucket: MarketReactionBucket
    reaction_fraction: float | None


def utc_dt(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def finite_float(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def clamp_probability(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def provider_family_for_source(source_id: str) -> str:
    source = str(source_id or "").strip().lower()
    if not source:
        return "unknown"
    for prefix, family in _SOURCE_FAMILY_PREFIXES:
        if source.startswith(prefix):
            return family
    if "_" in source:
        return source.split("_", 1)[0]
    return source


def geometry_admits_finer_signal(
    *,
    run: SourceRunClock,
    geometry: SettlementStationGeometry,
    station_aligned_certificate: bool = False,
    max_primary_resolution_km: float = 10.0,
    min_alignment_score: float = 0.0,
) -> bool:
    """Return whether a source may be used as a station-level finer signal.

    Sources at or below the primary resolution ceiling can enter when station
    alignment is finite. Coarser sources need a settlement/station certificate;
    otherwise they remain broad priors rather than high/low extrema signals.
    """

    resolution = finite_float(run.spatial_resolution_km)
    distance = finite_float(geometry.grid_distance_km)
    alignment = finite_float(geometry.station_alignment_score)
    if distance is None or alignment is None:
        return False
    if alignment < float(min_alignment_score):
        return False
    if resolution is not None and resolution <= float(max_primary_resolution_km):
        return True
    return bool(station_aligned_certificate)


def apply_intra_hour_extreme_correction(
    *,
    raw_daily_extreme_c: float,
    metric: str,
    correction: IntraHourExtremeCorrection | None,
) -> float:
    """Apply station-learned high/low extrema correction without mixing metrics."""

    raw = finite_float(raw_daily_extreme_c)
    if raw is None:
        raise ValueError("raw_daily_extreme_c must be finite")
    metric_norm = str(metric or "").strip().lower()
    if metric_norm not in {"high", "low"}:
        raise ValueError("metric must be high or low")
    if correction is None:
        return raw
    if str(correction.metric or "").strip().lower() != metric_norm:
        raise ValueError("extreme correction metric mismatch")
    delta = finite_float(correction.correction_c)
    if delta is None:
        raise ValueError("correction_c must be finite")
    return raw + delta


def source_clock_redecision_scope(
    updated_source_ids: Sequence[str],
    weights: Sequence[CityMetricLeadSourceWeight],
) -> tuple[tuple[str, str, int], ...]:
    """Return affected city/metric/lead families for source-update redecision."""

    updated = {str(source_id).strip() for source_id in updated_source_ids if str(source_id).strip()}
    affected: set[tuple[str, str, int]] = set()
    for row in weights:
        if row.source_id not in updated:
            continue
        if finite_float(row.weight) is None or float(row.weight) <= 0.0:
            continue
        affected.add((str(row.city), str(row.metric), int(row.lead)))
    return tuple(sorted(affected))


def source_publicly_usable_at(
    run: SourceRunClock,
    *,
    wait_minutes: float = SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES,
) -> datetime:
    return utc_dt(run.run_availability_time) + timedelta(minutes=float(wait_minutes))


def source_is_publicly_usable(
    run: SourceRunClock | None,
    *,
    decision_time: datetime,
    wait_minutes: float = SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES,
) -> bool:
    if run is None:
        return False
    if str(run.freshness_state or "").strip().upper() != "FRESH":
        return False
    return utc_dt(decision_time) >= source_publicly_usable_at(run, wait_minutes=wait_minutes)


def source_age_minutes(run: SourceRunClock | None, *, decision_time: datetime) -> float | None:
    if run is None:
        return None
    return (utc_dt(decision_time) - utc_dt(run.run_availability_time)).total_seconds() / 60.0


def source_age_bucket_from_minutes(age_minutes: float | None) -> str:
    if age_minutes is None or not math.isfinite(float(age_minutes)):
        return "source_age_unknown"
    age = float(age_minutes)
    if age < 0.0:
        return "pre_availability"
    if age < 10.0:
        return "0m_10m"
    if age < 20.0:
        return "10m_20m"
    if age < 40.0:
        return "20m_40m"
    if age < 60.0:
        return "40m_60m"
    if age < 120.0:
        return "60m_120m"
    return "120m_plus"


def source_age_bucket(run: SourceRunClock | None, *, decision_time: datetime) -> str:
    return source_age_bucket_from_minutes(source_age_minutes(run, decision_time=decision_time))


def market_reaction_fraction(
    *,
    q_shock: float,
    price_pre: float | None,
    price_after: float | None,
) -> float | None:
    shock = finite_float(q_shock)
    before = finite_float(price_pre)
    after = finite_float(price_after)
    if shock is None or before is None or after is None or abs(shock) <= _EPSILON:
        return None
    return (after - before) / shock


def market_reaction_bucket(reaction_fraction: float | None) -> MarketReactionBucket:
    rho = finite_float(reaction_fraction)
    if rho is None:
        return "reaction_unknown"
    if rho < 0.0:
        return "reverse_reaction"
    if rho < 0.25:
        return "underreacted"
    if rho < 0.75:
        return "partial_reaction"
    if rho <= 1.25:
        return "absorbed"
    return "overreacted"


def reaction_adjusted_lcb(
    *,
    q_lcb: float,
    executable_cost: float,
    market_reaction: MarketReactionState | None,
    allow_unknown_market_reaction: bool = False,
) -> float:
    q_value = clamp_probability(q_lcb)
    cost = clamp_probability(executable_cost)
    edge = max(0.0, q_value - cost)
    if edge <= 0.0:
        return min(q_value, cost)
    if market_reaction is None or market_reaction.reaction_fraction is None:
        return q_value if allow_unknown_market_reaction else cost
    rho = finite_float(market_reaction.reaction_fraction)
    if rho is None:
        return q_value if allow_unknown_market_reaction else cost
    if rho <= 0.0:
        return q_value
    if rho >= 1.0:
        return cost
    return cost + edge * (1.0 - rho)


def kelly_spend_fraction(probability: float, executable_cost: float) -> float:
    p = finite_float(probability)
    c = finite_float(executable_cost)
    if p is None or c is None or c <= 0.0 or c >= 1.0:
        return 0.0
    return max(0.0, min(1.0 - 1e-12, (p - c) / (1.0 - c)))


def binary_log_growth(probability: float, executable_cost: float, spend_fraction: float) -> float:
    p = finite_float(probability)
    c = finite_float(executable_cost)
    f = finite_float(spend_fraction)
    if p is None or c is None or f is None or c <= 0.0 or c >= 1.0 or f <= 0.0:
        return 0.0
    if f >= 1.0:
        return float("-inf")
    win_wealth = 1.0 + f * ((1.0 / c) - 1.0)
    lose_wealth = 1.0 - f
    if win_wealth <= 0.0 or lose_wealth <= 0.0:
        return float("-inf")
    return p * math.log(win_wealth) + (1.0 - p) * math.log(lose_wealth)


def optimal_binary_log_growth(probability: float, executable_cost: float) -> tuple[float, float]:
    fraction = kelly_spend_fraction(probability, executable_cost)
    return fraction, binary_log_growth(probability, executable_cost, fraction)


def source_clock_log_utility_admission(
    inputs: SourceClockAdmissionInput,
) -> SourceClockAdmissionDecision:
    q_point = finite_float(inputs.q_point)
    q_lcb = finite_float(inputs.q_lcb)
    cost = finite_float(inputs.executable_cost)
    age_bucket = source_age_bucket(inputs.source_run, decision_time=inputs.decision_time)
    reaction_bucket = (
        "reaction_unknown" if inputs.market_reaction is None else inputs.market_reaction.bucket
    )
    reaction_fraction = (
        None if inputs.market_reaction is None else inputs.market_reaction.reaction_fraction
    )

    def reject(reason: str, *, q_exec_lcb: float = 0.0) -> SourceClockAdmissionDecision:
        q_exec = clamp_probability(q_exec_lcb)
        fraction, growth = optimal_binary_log_growth(q_exec, cost or 0.0)
        return SourceClockAdmissionDecision(
            admitted=False,
            reason=reason,
            q_point=0.0 if q_point is None else q_point,
            q_lcb=0.0 if q_lcb is None else q_lcb,
            q_exec_lcb=q_exec,
            executable_cost=0.0 if cost is None else cost,
            edge_lcb=q_exec - (0.0 if cost is None else cost),
            kelly_spend_fraction=fraction,
            expected_log_growth=growth,
            source_age_bucket=age_bucket,
            market_reaction_bucket=reaction_bucket,
            reaction_fraction=reaction_fraction,
        )

    if q_point is None or q_lcb is None or cost is None:
        return reject("SOURCE_CLOCK_ADMISSION_INPUTS_NONFINITE")
    if q_point < 0.0 or q_point > 1.0 or q_lcb < 0.0 or q_lcb > 1.0:
        return reject("SOURCE_CLOCK_ADMISSION_PROBABILITY_RANGE")
    if q_lcb > q_point + 1e-12:
        return reject("SOURCE_CLOCK_ADMISSION_LCB_EXCEEDS_POINT", q_exec_lcb=min(q_lcb, q_point))
    if cost <= 0.0 or cost >= 1.0:
        return reject("SOURCE_CLOCK_ADMISSION_EXECUTABLE_COST_RANGE", q_exec_lcb=q_lcb)
    if not source_is_publicly_usable(
        inputs.source_run,
        decision_time=inputs.decision_time,
        wait_minutes=inputs.availability_wait_minutes,
    ):
        return reject("SOURCE_CLOCK_NOT_PUBLICLY_USABLE", q_exec_lcb=q_lcb)
    if (
        not inputs.allow_unknown_market_reaction
        and reaction_bucket == "reaction_unknown"
    ):
        return reject("SOURCE_CLOCK_MARKET_REACTION_UNKNOWN", q_exec_lcb=min(q_lcb, cost))

    q_exec_lcb = reaction_adjusted_lcb(
        q_lcb=q_lcb,
        executable_cost=cost,
        market_reaction=inputs.market_reaction,
        allow_unknown_market_reaction=inputs.allow_unknown_market_reaction,
    )
    fraction, growth = optimal_binary_log_growth(q_exec_lcb, cost)
    if fraction <= 0.0 or growth <= 0.0:
        return reject("SOURCE_CLOCK_LOG_UTILITY_NON_POSITIVE", q_exec_lcb=q_exec_lcb)
    return SourceClockAdmissionDecision(
        admitted=True,
        reason=None,
        q_point=q_point,
        q_lcb=q_lcb,
        q_exec_lcb=q_exec_lcb,
        executable_cost=cost,
        edge_lcb=q_exec_lcb - cost,
        kelly_spend_fraction=fraction,
        expected_log_growth=growth,
        source_age_bucket=age_bucket,
        market_reaction_bucket=reaction_bucket,
        reaction_fraction=reaction_fraction,
    )


def source_clock_log_utility_rejection_reason(
    inputs: SourceClockAdmissionInput,
) -> str | None:
    decision = source_clock_log_utility_admission(inputs)
    return None if decision.admitted else decision.reason


def no_side_lcb_from_yes_ucb(q_ucb_yes: float) -> float:
    q_ucb = finite_float(q_ucb_yes)
    if q_ucb is None or q_ucb < 0.0 or q_ucb > 1.0:
        raise ValueError("q_ucb_yes must be finite and in [0, 1]")
    return clamp_probability(1.0 - q_ucb)


def admission_calibration_gap(*, q_point: float, realized_hit: bool | int) -> float:
    q_value = finite_float(q_point)
    if q_value is None or q_value < 0.0 or q_value > 1.0:
        raise ValueError("q_point must be finite and in [0, 1]")
    return (1.0 if bool(realized_hit) else 0.0) - q_value


def sparse_settlement_source_weights(
    observations: Sequence[SourceWeightObservation],
    *,
    softmax_temperature: float = 0.05,
    temperature: float | None = None,
) -> tuple[WeightedSource, ...]:
    """Build nonnegative source weights from settlement-graded scores.

    The function enforces one representative per provider family, excludes stale
    sources, and weights the remaining candidates by settlement log-loss first
    with Brier/RMSE tie-breaks. It returns an empty tuple when no lawful source
    remains, which callers must treat as fail-closed.
    """

    if temperature is not None:
        softmax_temperature = temperature
    if softmax_temperature <= 0.0 or not math.isfinite(float(softmax_temperature)):
        raise ValueError("softmax_temperature must be finite and positive")

    best_by_family: dict[str, SourceWeightObservation] = {}
    for obs in observations:
        if obs.stale:
            continue
        if not obs.source_id or not obs.provider_family:
            continue
        logloss = finite_float(obs.settlement_logloss)
        brier = finite_float(obs.brier)
        if logloss is None or brier is None:
            continue
        family = str(obs.provider_family)
        incumbent = best_by_family.get(family)
        if incumbent is None or _score_key(obs) < _score_key(incumbent):
            best_by_family[family] = obs

    kept = tuple(best_by_family.values())
    if not kept:
        return ()

    losses = [float(obs.settlement_logloss) for obs in kept]
    best_loss = min(losses)
    logits = [-(loss - best_loss) / float(softmax_temperature) for loss in losses]
    max_logit = max(logits)
    weights_raw = [math.exp(logit - max_logit) for logit in logits]
    total = sum(weights_raw)
    if total <= 0.0 or not math.isfinite(total):
        return ()
    return tuple(
        WeightedSource(
            source_id=obs.source_id,
            provider_family=obs.provider_family,
            weight=weight / total,
            settlement_logloss=float(obs.settlement_logloss),
            brier=float(obs.brier),
            crps=None if obs.crps is None else float(obs.crps),
            rmse=None if obs.rmse is None else float(obs.rmse),
            mae=None if obs.mae is None else float(obs.mae),
            bias=None if obs.bias is None else float(obs.bias),
        )
        for obs, weight in zip(kept, weights_raw, strict=True)
    )


def _score_key(obs: SourceWeightObservation) -> tuple[float, float, float, float, float]:
    return (
        _finite_or_inf(obs.settlement_logloss),
        _finite_or_inf(obs.brier),
        _finite_or_inf(obs.rmse),
        _finite_or_inf(obs.mae),
        abs(0.0 if obs.bias is None else _finite_or_inf(obs.bias)),
    )


def _finite_or_inf(value: float | None) -> float:
    parsed = finite_float(value)
    return float("inf") if parsed is None else parsed
