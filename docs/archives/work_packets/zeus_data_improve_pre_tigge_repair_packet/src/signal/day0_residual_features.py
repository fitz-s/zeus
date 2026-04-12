from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass
from datetime import datetime
from statistics import pstdev
from typing import Iterable


@dataclass(slots=True)
class EnsembleContext:
    available_at: str
    members: list[float]
    spread: float | None = None


@dataclass(slots=True)
class Day0ResidualFact:
    fact_id: str
    city: str
    target_date: str
    source: str
    local_timestamp: str
    utc_timestamp: str
    local_hour: float | None
    temp_current: float | None
    running_max: float | None
    delta_rate_per_h: float | None
    daylight_progress: float | None
    obs_age_minutes: float | None
    post_peak_confidence: float | None
    ens_q50_remaining: float | None
    ens_q90_remaining: float | None
    ens_spread: float | None
    settlement_value: float | None
    residual_upside: float | None
    has_upside: int | None
    fact_status: str
    missing_reason: list[str]



def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
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



def daylight_progress(local_timestamp: str | None, sunrise_local: str | None, sunset_local: str | None) -> float | None:
    ts = _parse_dt(local_timestamp)
    sunrise = _parse_dt(sunrise_local)
    sunset = _parse_dt(sunset_local)
    if ts is None or sunrise is None or sunset is None or sunset <= sunrise:
        return None
    if ts <= sunrise:
        return 0.0
    if ts >= sunset:
        return 1.0
    return float((ts - sunrise).total_seconds() / (sunset - sunrise).total_seconds())



def obs_age_minutes(utc_timestamp: str | None, imported_at: str | None) -> float | None:
    obs = _parse_dt(utc_timestamp)
    imp = _parse_dt(imported_at)
    if obs is None or imp is None:
        return None
    delta = (imp - obs).total_seconds() / 60.0
    return float(delta) if delta >= 0 else None



def ensemble_remaining_quantiles(running_max: float | None, members: Iterable[float] | None) -> tuple[float | None, float | None, float | None]:
    if running_max is None or members is None:
        return None, None, None
    residuals = sorted(max(0.0, float(m) - float(running_max)) for m in members)
    if not residuals:
        return None, None, None
    q50 = quantile(residuals, 0.50)
    q90 = quantile(residuals, 0.90)
    spread = float(pstdev(residuals)) if len(residuals) > 1 else 0.0
    return q50, q90, spread



def latest_ensemble_before(timestamp: str, snapshots: list[EnsembleContext]) -> EnsembleContext | None:
    if not snapshots:
        return None
    keys = [_parse_dt(s.available_at) for s in snapshots]
    target = _parse_dt(timestamp)
    if target is None:
        return None
    ordinal = [k.timestamp() if k else float("-inf") for k in keys]
    pos = bisect.bisect_right(ordinal, target.timestamp()) - 1
    if pos < 0:
        return None
    return snapshots[pos]
