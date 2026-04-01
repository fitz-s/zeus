"""Shared Day0 window selection helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np


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
) -> tuple[np.ndarray, float]:
    """Select remaining target-date local hours for Day0 observation logic."""

    tz = ZoneInfo(timezone_name)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    idxs: list[int] = []
    for idx, ts in enumerate(times):
        dt_utc = _parse_forecast_timestamp(ts)
        dt_local = dt_utc.astimezone(tz)
        if dt_local.date() != target_d:
            continue
        if dt_local < now_local:
            continue
        idxs.append(idx)

    if not idxs:
        for idx, ts in enumerate(times):
            dt_utc = _parse_forecast_timestamp(ts)
            if dt_utc.astimezone(tz).date() == target_d:
                idxs.append(idx)

    if not idxs:
        return np.array([]), 0.0

    return members_hourly[:, idxs].max(axis=1), float(len(idxs))
