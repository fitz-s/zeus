"""
Zeus dual-track refactor skeletons.

These are guidance snippets, not production-ready drop-ins.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Optional


class TemperatureMetric(StrEnum):
    HIGH = "high"
    LOW = "low"


class CausalityStatus(StrEnum):
    OK = "OK"
    NA_CAUSAL_DAY_ALREADY_STARTED = "N/A_CAUSAL_DAY_ALREADY_STARTED"
    NA_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON = "N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON"


@dataclass(frozen=True)
class ForecastTrack:
    temperature_metric: TemperatureMetric
    physical_quantity: str
    aggregation_contract: str
    observation_field: str
    data_version: str
    source_family: str
    geometry_version: str = "local_calendar_day_v1"

    @property
    def is_trainable_family(self) -> bool:
        return self.source_family == "tigge_ecmwf_ens"


HIGH_TRACK = ForecastTrack(
    temperature_metric=TemperatureMetric.HIGH,
    physical_quantity="mx2t6_local_calendar_day_max",
    aggregation_contract="local_calendar_day",
    observation_field="high_temp",
    data_version="tigge_mx2t6_local_calendar_day_max_v1",
    source_family="tigge_ecmwf_ens",
)

LOW_TRACK = ForecastTrack(
    temperature_metric=TemperatureMetric.LOW,
    physical_quantity="mn2t6_local_calendar_day_min",
    aggregation_contract="local_calendar_day",
    observation_field="low_temp",
    data_version="tigge_mn2t6_local_calendar_day_min_v1",
    source_family="tigge_ecmwf_ens",
)

LOW_DAY0_NOWCAST_TRACK = ForecastTrack(
    temperature_metric=TemperatureMetric.LOW,
    physical_quantity="low_day0_nowcast_remaining_window",
    aggregation_contract="partial_local_day_nowcast",
    observation_field="low_temp",
    data_version="low_day0_nowcast_v1",
    source_family="runtime_nowcast",
)


@dataclass(frozen=True)
class ObservationNow:
    current_temp: Optional[float]
    high_so_far: Optional[float]
    low_so_far: Optional[float]
    observation_time_utc: Optional[str]
    source: str


@dataclass(frozen=True)
class SnapshotIdentity:
    city: str
    target_date: str
    track: ForecastTrack
    issue_time_utc: Optional[str]
    causality_status: CausalityStatus
    training_allowed: bool


def assert_metric_alignment(
    *,
    candidate_metric: TemperatureMetric,
    snapshot_track: ForecastTrack,
    model_track: ForecastTrack,
) -> None:
    if candidate_metric != snapshot_track.temperature_metric:
        raise RuntimeError(
            f"candidate metric {candidate_metric} != snapshot metric "
            f"{snapshot_track.temperature_metric}"
        )
    if snapshot_track.temperature_metric != model_track.temperature_metric:
        raise RuntimeError(
            f"snapshot metric {snapshot_track.temperature_metric} != model metric "
            f"{model_track.temperature_metric}"
        )
    if snapshot_track.data_version != model_track.data_version:
        raise RuntimeError(
            f"snapshot data_version {snapshot_track.data_version} != model data_version "
            f"{model_track.data_version}"
        )


class Day0Model(Protocol):
    def p_vector(self, bins: list[object]) -> list[float]:
        ...


@dataclass
class Day0HighSignal:
    observed_high_so_far: float
    current_temp: float
    hours_remaining: float
    member_maxes_remaining: list[float]

    def p_vector(self, bins: list[object]) -> list[float]:
        # TODO: port existing high logic here
        return []


@dataclass
class Day0LowSignal:
    observed_low_so_far: float
    current_temp: float
    hours_remaining: float
    member_mins_remaining: list[float]

    def p_vector(self, bins: list[object]) -> list[float]:
        # TODO: implement independent low logic.
        # Must NOT call day0_blended_highs().
        return []


def build_day0_model(
    *,
    track: ForecastTrack,
    observation: ObservationNow,
    hours_remaining: float,
    remaining_member_extrema: list[float],
) -> Day0Model:
    if track.temperature_metric is TemperatureMetric.HIGH:
        if observation.high_so_far is None or observation.current_temp is None:
            raise RuntimeError("DAY0_HIGH_OBSERVATION_UNAVAILABLE")
        return Day0HighSignal(
            observed_high_so_far=observation.high_so_far,
            current_temp=observation.current_temp,
            hours_remaining=hours_remaining,
            member_maxes_remaining=remaining_member_extrema,
        )

    if track.temperature_metric is TemperatureMetric.LOW:
        if observation.low_so_far is None or observation.current_temp is None:
            raise RuntimeError("DAY0_LOW_OBSERVATION_UNAVAILABLE")
        return Day0LowSignal(
            observed_low_so_far=observation.low_so_far,
            current_temp=observation.current_temp,
            hours_remaining=hours_remaining,
            member_mins_remaining=remaining_member_extrema,
        )

    raise AssertionError(f"Unhandled metric: {track.temperature_metric}")


def make_platt_bucket_key(
    *,
    track: ForecastTrack,
    cluster: str,
    season: str,
    bin_source: str,
    input_space: str = "raw_probability",
) -> str:
    return (
        f"{track.temperature_metric.value}:"
        f"{track.physical_quantity}:"
        f"{track.data_version}:"
        f"{cluster}:{season}:{bin_source}:{input_space}"
    )


def snapshot_is_eligible_for_training(snapshot: dict) -> bool:
    """
    Minimal rule set:
    - must be VERIFIED
    - must be TIGGE ENS canonical data_version
    - low rows must not be boundary ambiguous
    - causality must be OK
    """
    if snapshot.get("authority") != "VERIFIED":
        return False
    if snapshot.get("source_family") != "tigge_ecmwf_ens":
        return False
    if snapshot.get("causality_status") != "OK":
        return False
    if not bool(snapshot.get("training_allowed", False)):
        return False
    return True


def resolve_observation_field(metric: TemperatureMetric) -> str:
    return "low_temp" if metric is TemperatureMetric.LOW else "high_temp"


def resolve_runtime_so_far_key(metric: TemperatureMetric) -> str:
    return "low_so_far" if metric is TemperatureMetric.LOW else "high_so_far"


def forbid_runtime_fallback_training(snapshot: dict) -> None:
    if snapshot.get("source_family") in {"runtime_nowcast", "openmeteo_fallback"}:
        if snapshot.get("training_allowed"):
            raise RuntimeError("runtime fallback row illegally marked training_allowed=true")
