"""Shared Day0 window selection helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from src.signal.ensemble_signal import select_hours_for_target_date
from src.types.metric_identity import HIGH_LOCALDAY_MAX, MetricIdentity


def _parse_forecast_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def remaining_member_maxes_for_day0(
    members_hourly: np.ndarray,
    times: list[str],
    timezone_name: str,
    target_d: date,
    *,
    now: datetime | None = None,
    temperature_metric: MetricIdentity = HIGH_LOCALDAY_MAX,
) -> tuple[np.ndarray, float]:
    """Select remaining target-date local hours for Day0 observation logic.

    For low-temperature markets, returns per-member daily mins.

    Args:
        temperature_metric: MetricIdentity instance. Bare strings are rejected;
            callers holding a string (e.g. ``monitor_refresh`` reading from
            portfolio state) must convert at their own seam via
            ``MetricIdentity.from_raw()``.
    """
    if isinstance(temperature_metric, str):
        raise TypeError(
            f"remaining_member_maxes_for_day0 requires a MetricIdentity instance "
            f"for temperature_metric, got str {temperature_metric!r}. "
            f"Convert via MetricIdentity.from_raw() at the caller seam."
        )

    tz = ZoneInfo(timezone_name)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    try:
        target_day_idxs = select_hours_for_target_date(
            target_d,
            tz,
            times=times,
        )
    except ValueError:
        return np.array([]), 0.0

    remaining_idxs = [
        int(idx)
        for idx in target_day_idxs
        if _parse_forecast_timestamp(times[int(idx)]).astimezone(tz) >= now_local
    ]
    if not remaining_idxs:
        return np.array([]), 0.0

    slice_data = members_hourly[:, remaining_idxs]
    if temperature_metric.is_low():
        return slice_data.min(axis=1), float(len(remaining_idxs))
    return slice_data.max(axis=1), float(len(remaining_idxs))
