"""Shared Day0 window selection helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from src.signal.day0_extrema import RemainingMemberExtrema
from src.signal.ensemble_signal import select_hours_for_target_date
from src.types.metric_identity import HIGH_LOCALDAY_MAX, MetricIdentity


def _parse_forecast_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def remaining_member_extrema_for_day0(
    members_hourly: np.ndarray,
    times: list[str],
    timezone_name: str,
    target_d: date,
    *,
    now: datetime | None = None,
    temperature_metric: MetricIdentity = HIGH_LOCALDAY_MAX,
) -> tuple[RemainingMemberExtrema | None, float]:
    """Select remaining target-date local hours for Day0 observation logic.

    Returns (RemainingMemberExtrema, hours_remaining). Returns (None, 0.0) when
    no remaining hours exist. HIGH sets maxes; LOW sets mins.

    Args:
        temperature_metric: MetricIdentity instance. Bare strings are rejected;
            callers holding a string must convert via MetricIdentity.from_raw().
    """
    if isinstance(temperature_metric, str):
        raise TypeError(
            f"remaining_member_extrema_for_day0 requires a MetricIdentity instance "
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
        return None, 0.0

    remaining_idxs = [
        int(idx)
        for idx in target_day_idxs
        if _parse_forecast_timestamp(times[int(idx)]).astimezone(tz) >= now_local
    ]
    if not remaining_idxs:
        return None, 0.0

    slice_data = members_hourly[:, remaining_idxs]
    if temperature_metric.is_low():
        arr = slice_data.min(axis=1)
    else:
        arr = slice_data.max(axis=1)
    return RemainingMemberExtrema.for_metric(arr, temperature_metric), float(len(remaining_idxs))


# Backward-compat alias `remaining_member_maxes_for_day0` REMOVED in Phase 7B.
# All production callers migrated to remaining_member_extrema_for_day0 in Phase 6.
# All test callers migrated in Phase 7B. Use the dataclass-returning entry point:
#     extrema, hours = remaining_member_extrema_for_day0(...)
#     arr = extrema.maxes  # HIGH — use .mins for LOW
