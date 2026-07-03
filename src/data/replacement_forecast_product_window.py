"""Product-window policy for replacement forecast products.

Live B0 forecast coverage still uses ``forecast_target_contract``. Replacement
products need a separate resolver because AIFS sampled ``2t`` valid times,
Open-Meteo deterministic local-day anchors, and derived posterior rows are not
period-extrema ensemble products.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.data.forecast_target_contract import required_period_end_steps
from src.data.replacement_forecast_metric_identity import replacement_forecast_metric_identity


UTC = timezone.utc


@dataclass(frozen=True)
class ReplacementForecastProductWindowPolicy:
    """Temporal measurement law for a replacement forecast data_version."""

    source_id: str
    product_id: str
    product_class: str
    data_version: str
    aggregation_window_policy: str
    measurement_object: str
    raw_ensemble_eligible: bool
    expected_step_hours: tuple[int, ...]
    required_valid_time_stride_hours: int | None
    requires_member_vectors: bool
    requires_anchor_value: bool
    requires_dependency_posteriors: bool

    @property
    def is_period_extrema(self) -> bool:
        return self.aggregation_window_policy in {
            "period_3h_local_calendar_day",
            "since_prev_postproc_local_calendar_day",
        }


def _to_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _sampled_valid_time_steps(
    *,
    source_cycle_time: datetime,
    target_window_start_utc: datetime,
    target_window_end_utc: datetime,
    stride_hours: int,
) -> tuple[int, ...]:
    if stride_hours <= 0:
        raise ValueError("stride_hours must be positive")
    source_cycle = _to_utc(source_cycle_time, "source_cycle_time")
    window_start = _to_utc(target_window_start_utc, "target_window_start_utc")
    window_end = _to_utc(target_window_end_utc, "target_window_end_utc")
    if window_end <= window_start:
        raise ValueError("target window end must be after start")

    steps: list[int] = []
    step_hour = 0
    while source_cycle + timedelta(hours=step_hour) < window_end:
        valid_time = source_cycle + timedelta(hours=step_hour)
        if window_start <= valid_time < window_end:
            steps.append(step_hour)
        step_hour += stride_hours
    return tuple(steps)


def replacement_forecast_product_window_policy(
    product_label: str,
    temperature_metric: str,
    *,
    source_cycle_time: datetime,
    target_window_start_utc: datetime,
    target_window_end_utc: datetime,
) -> ReplacementForecastProductWindowPolicy:
    """Resolve the temporal support law for a replacement product."""

    identity = replacement_forecast_metric_identity(product_label, temperature_metric)
    policy = identity.aggregation_window_policy
    if policy == "period_3h_local_calendar_day":
        expected_steps = required_period_end_steps(
            source_cycle_time=source_cycle_time,
            target_window_start_utc=target_window_start_utc,
            target_window_end_utc=target_window_end_utc,
            period_hours=3,
        )
        stride = 3
        requires_member_vectors = True
        requires_anchor_value = False
        requires_dependency_posteriors = False
    elif policy == "since_prev_postproc_local_calendar_day":
        expected_steps = required_period_end_steps(
            source_cycle_time=source_cycle_time,
            target_window_start_utc=target_window_start_utc,
            target_window_end_utc=target_window_end_utc,
            period_hours=3,
        )
        stride = None
        requires_member_vectors = True
        requires_anchor_value = False
        requires_dependency_posteriors = False
    elif policy == "sampled_2t_6h_local_calendar_day":
        expected_steps = _sampled_valid_time_steps(
            source_cycle_time=source_cycle_time,
            target_window_start_utc=target_window_start_utc,
            target_window_end_utc=target_window_end_utc,
            stride_hours=6,
        )
        stride = 6
        requires_member_vectors = True
        requires_anchor_value = False
        requires_dependency_posteriors = False
    elif policy == "deterministic_local_calendar_day_anchor":
        expected_steps = ()
        stride = None
        requires_member_vectors = False
        requires_anchor_value = True
        requires_dependency_posteriors = False
    elif policy == "aifs_sampled_2t_6h_plus_deterministic_anchor_local_calendar_day":
        expected_steps = ()
        stride = None
        requires_member_vectors = False
        requires_anchor_value = True
        requires_dependency_posteriors = True
    else:
        raise ValueError(f"unsupported replacement aggregation_window_policy {policy!r}")

    return ReplacementForecastProductWindowPolicy(
        source_id=identity.source_id,
        product_id=identity.product_id,
        product_class=identity.product_class,
        data_version=identity.data_version,
        aggregation_window_policy=policy,
        measurement_object=identity.measurement_object,
        raw_ensemble_eligible=identity.raw_ensemble_eligible,
        expected_step_hours=expected_steps,
        required_valid_time_stride_hours=stride,
        requires_member_vectors=requires_member_vectors,
        requires_anchor_value=requires_anchor_value,
        requires_dependency_posteriors=requires_dependency_posteriors,
    )
