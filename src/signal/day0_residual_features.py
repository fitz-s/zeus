"""Point-in-time Day0 residual feature helpers."""

from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass
from datetime import datetime
from statistics import pstdev
from typing import Iterable


@dataclass(frozen=True)
class EnsembleContext:
    available_at: str
    members: tuple[float, ...]
    spread: float | None = None


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def quantile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    idx = q * (len(sorted_values) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(sorted_values[lo])
    frac = idx - lo
    return float(sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac)


def daylight_progress(
    local_timestamp: str | None,
    sunrise_local: str | None,
    sunset_local: str | None,
) -> float | None:
    ts = parse_timestamp(local_timestamp)
    sunrise = parse_timestamp(sunrise_local)
    sunset = parse_timestamp(sunset_local)
    if ts is None or sunrise is None or sunset is None or sunset <= sunrise:
        return None
    if ts <= sunrise:
        return 0.0
    if ts >= sunset:
        return 1.0
    return float((ts - sunrise).total_seconds() / (sunset - sunrise).total_seconds())


def obs_age_minutes(utc_timestamp: str | None, imported_at: str | None) -> float | None:
    obs = parse_timestamp(utc_timestamp)
    imported = parse_timestamp(imported_at)
    if obs is None or imported is None:
        return None
    age = (imported - obs).total_seconds() / 60.0
    return float(age) if age >= 0 else None


def post_peak_confidence(
    daylight_value: float | None,
    diurnal_probability: float | None = None,
) -> float | None:
    if diurnal_probability is not None:
        return min(1.0, max(0.0, float(diurnal_probability)))
    if daylight_value is None:
        return None
    # Heuristic fallback: before daylight midpoint, peak-passed confidence is
    # near zero; after midpoint, it increases linearly toward sunset.
    return min(1.0, max(0.0, (float(daylight_value) - 0.5) / 0.5))


def ensemble_remaining_quantiles(
    running_max: float | None,
    members: Iterable[float] | None,
) -> tuple[float | None, float | None, float | None]:
    if running_max is None or members is None:
        return None, None, None
    residuals = sorted(
        max(0.0, float(member) - float(running_max))
        for member in members
        if math.isfinite(float(member))
    )
    if not residuals:
        return None, None, None
    q50 = quantile(residuals, 0.50)
    q90 = quantile(residuals, 0.90)
    spread = float(pstdev(residuals)) if len(residuals) > 1 else 0.0
    return q50, q90, spread


def parse_member_values(members_json: str | None) -> tuple[float, ...]:
    if not members_json:
        return ()
    try:
        raw_values = json.loads(members_json)
    except (TypeError, json.JSONDecodeError):
        return ()
    values = []
    for value in raw_values or []:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            values.append(numeric)
    return tuple(values)


def latest_ensemble_before(
    utc_timestamp: str,
    snapshots: list[EnsembleContext],
) -> EnsembleContext | None:
    if not snapshots:
        return None
    target = parse_timestamp(utc_timestamp)
    if target is None:
        return None
    ordinals = [
        parsed.timestamp() if (parsed := parse_timestamp(snapshot.available_at)) else float("-inf")
        for snapshot in snapshots
    ]
    pos = bisect.bisect_right(ordinals, target.timestamp()) - 1
    if pos < 0:
        return None
    return snapshots[pos]
