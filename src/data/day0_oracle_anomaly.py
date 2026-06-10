# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator green-light 2026-06-10 item E (WU-vs-METAR
#   divergence detector, fail-closed family pause); Paris CDG sensor-tampering
#   incident April 2026 (/tmp/weather_source_research.md §5: trader manipulated
#   a sensor, Polymarket switched Paris settlement to Le Bourget).
"""Day0 settlement-oracle anomaly guard: WU vs METAR divergence.

The settlement reference (WU) and the fast lane (aviationweather.gov METAR)
read the SAME physical station. When their running extremes — compared over
the SAME observation window — diverge beyond conversion/rounding noise, one of
them is wrong: sensor tampering, feed injection, station swap, or a data bug
on our side. Every one of those is a reason NOT to trade the family on day0.

Semantics (relationship contract, tested in
tests/test_day0_fast_obs_lane.py::TestOracleAnomaly):
- The METAR extremes are TRUNCATED at WU's last observation time before
  comparison. METAR is fresher; an extreme that moved after WU's last report
  is normal latency, not an anomaly.
- Threshold is strict-> in settlement units: > 1.5 F / > 1.0 C (covers C->F
  conversion noise <=0.1F via the T-group rule plus WU's whole-degree
  rounding).
- Verdict NONE (no comparison) when either side has no samples in the window —
  absence of evidence is not an anomaly, and it must not pause trading.
- A flagged (city, target_date) pauses the day0 ENTRY lane fail-closed:
  src/engine/event_reactor_adapter._live_yes_probabilities raises
  DAY0_ORACLE_ANOMALY_PAUSED for that family's DAY0 events -> deterministic
  no-submit receipt (LIVE_INFERENCE_INPUTS_MISSING:DAY0_ORACLE_ANOMALY_PAUSED).
- The pause is in-process with a TTL; a daemon restart clears it and the
  detector re-flags on the next comparison if the divergence persists.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

UTC = timezone.utc

#: Conservative DEFAULT divergence thresholds (settlement units) for cities
#: WITHOUT an empirical measurement. Pre-2026-06-10 these (guessed) values
#: applied to every city; measured cities now carry per-city empirical
#: thresholds from config/wu_metar_divergence.json (operator correction
#: 2026-06-10: 30d x 22 cities, same-station timestamp-matched — 21/22
#: byte-identical post-rounding -> threshold 1.0 [tight tamper detector];
#: Seoul RKSI shows REAL +-1C divergence on 4.5% of reports -> threshold 2.0
#: AND settlement_faithful=false).
DIVERGENCE_THRESHOLD = {"F": 1.5, "C": 1.0}

#: How long a flagged family stays paused without re-confirmation.
DEFAULT_PAUSE_TTL_HOURS = 24.0

_DIVERGENCE_MODEL_CACHE: dict[str, dict] = {}


def _divergence_model_path() -> "Path":
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "config" / "wu_metar_divergence.json"


def _load_divergence_model(path: "Optional[Path]" = None) -> dict:
    import json
    from pathlib import Path  # noqa: F401 — typing only

    path_str = str(path if path is not None else _divergence_model_path())
    cached = _DIVERGENCE_MODEL_CACHE.get(path_str)
    if cached is not None:
        return cached
    try:
        with open(path_str, "r", encoding="utf-8") as fh:
            model = json.load(fh)
        if not isinstance(model, dict) or not isinstance(model.get("cities"), dict):
            raise ValueError("wu_metar_divergence.json malformed: missing 'cities'")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "WU_METAR_DIVERGENCE_MODEL_UNAVAILABLE path=%s exc=%s — default thresholds",
            path_str, exc,
        )
        model = {"cities": {}}
    _DIVERGENCE_MODEL_CACHE[path_str] = model
    return model


def divergence_threshold_for_city(
    city_name: str, unit: str, *, path: "Optional[Path]" = None
) -> tuple[float, str]:
    """(threshold, provenance) for the WU-vs-METAR guard.

    Empirical per-city threshold — max(p99(|rounded delta|) + 1 quantum, 1.0),
    measured same-station timestamp-matched over 30d — when present with an
    adequate sample; otherwise the conservative pre-measurement default.
    Provenance is recorded in the guard verdict ('empirical' | 'default_guess').
    """
    entry = _load_divergence_model(path).get("cities", {}).get(str(city_name)) or {}
    threshold = entry.get("empirical_threshold")
    provenance = str(entry.get("threshold_provenance") or "")
    if threshold is not None and provenance == "empirical":
        try:
            value = float(threshold)
            if value > 0.0:
                return value, "empirical"
        except (TypeError, ValueError):
            pass
    return (
        DIVERGENCE_THRESHOLD.get(str(unit).upper(), DIVERGENCE_THRESHOLD["F"]),
        "default_guess",
    )


def city_metar_settlement_faithful(city_name: str, *, path: "Optional[Path]" = None) -> bool:
    """False when the measurement shows the METAR integer is NOT reliably WU's
    settlement integer for this station (Seoul/RKSI class: +-1C disagreement
    on ~4.5% of reports — WU's feed is not the METAR body there). Such a city
    must NOT have METAR drive day0 bin-kill decisions: the fast lane excludes
    it entirely (monotone-safe — absence of fast events never kills a bin).
    Unmeasured cities default to True (the guard threshold still covers them)."""
    entry = _load_divergence_model(path).get("cities", {}).get(str(city_name)) or {}
    verdict = entry.get("settlement_faithful")
    if verdict is None:
        return True
    return bool(verdict)


@dataclass(frozen=True)
class DivergenceVerdict:
    city: str
    target_date: str
    unit: str
    compared: bool
    diverged: bool
    high_delta: Optional[float] = None
    low_delta: Optional[float] = None
    wu_last_obs_time: Optional[str] = None
    metar_samples: int = 0
    detail: str = ""


@dataclass(frozen=True)
class _AnomalyRecord:
    flagged_at: datetime
    detail: str


_REGISTRY: dict[tuple[str, str], _AnomalyRecord] = {}
_REGISTRY_LOCK = threading.Lock()


def flag_day0_oracle_anomaly(city: str, target_date: str, *, detail: str, now: Optional[datetime] = None) -> None:
    """Pause the day0 lane for (city, target_date). Loud by design."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    with _REGISTRY_LOCK:
        _REGISTRY[(str(city), str(target_date))] = _AnomalyRecord(flagged_at=moment, detail=str(detail))
    logger.warning(
        "DAY0_ORACLE_ANOMALY_FLAGGED city=%s target_date=%s detail=%s — day0 entries PAUSED (fail-closed)",
        city, target_date, detail,
    )


def clear_day0_oracle_anomaly(city: str, target_date: str) -> bool:
    """Operator/cleanup hook. Returns True when a record was removed."""
    with _REGISTRY_LOCK:
        return _REGISTRY.pop((str(city), str(target_date)), None) is not None


def is_day0_family_paused(
    city: str,
    target_date: str,
    *,
    now: Optional[datetime] = None,
    ttl_hours: float = DEFAULT_PAUSE_TTL_HOURS,
) -> bool:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    key = (str(city), str(target_date))
    with _REGISTRY_LOCK:
        record = _REGISTRY.get(key)
        if record is None:
            return False
        if moment - record.flagged_at > timedelta(hours=float(ttl_hours)):
            _REGISTRY.pop(key, None)
            return False
        return True


def active_day0_anomalies() -> dict[tuple[str, str], str]:
    """Observability snapshot: {(city, target_date): detail}."""
    with _REGISTRY_LOCK:
        return {key: record.detail for key, record in _REGISTRY.items()}


def _reset_registry_for_tests() -> None:
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


#: WU live-API anomaly checks are throttled per city (the comparison only
#: needs WU's cadence, not the reactor cycle cadence).
_WU_CHECK_INTERVAL_S = 600.0
_WU_CHECK_MEMO: dict[str, float] = {}
_WU_CHECK_MEMO_LOCK = threading.Lock()


def wu_metar_anomaly_check(city: Any, extremes: Any, metar_reports: list) -> None:
    """Throttled WU-vs-METAR divergence check; flags the registry on divergence.

    Signature matches Day0FastObsEmitter.emit_events(anomaly_check=...). Any
    WU-side failure is fail-SOFT for emission (the fast lane keeps running)
    but logged — absence of the cross-check is visibility loss, not an anomaly.
    """
    import time as _time

    city_name = str(getattr(city, "name", "") or "")
    target_date = str(getattr(extremes, "target_date", "") or "")
    if not city_name or not target_date:
        return
    now_monotonic = _time.monotonic()
    with _WU_CHECK_MEMO_LOCK:
        last = _WU_CHECK_MEMO.get(city_name, 0.0)
        if now_monotonic - last < _WU_CHECK_INTERVAL_S:
            return
        _WU_CHECK_MEMO[city_name] = now_monotonic

    from src.data.observation_client import get_current_observation

    try:
        wu_obs = get_current_observation(city, target_date=target_date)
    except Exception as exc:  # noqa: BLE001 — WU side fail-soft, loud
        logger.warning(
            "DAY0_ORACLE_ANOMALY_WU_SIDE_UNAVAILABLE city=%s date=%s exc=%s: %s",
            city_name, target_date, type(exc).__name__, exc,
        )
        return
    wu_time_raw = getattr(wu_obs, "observation_time", None)
    try:
        wu_last_obs_time = (
            datetime.fromisoformat(str(wu_time_raw).replace("Z", "+00:00")) if wu_time_raw else None
        )
        if wu_last_obs_time is not None and wu_last_obs_time.tzinfo is None:
            wu_last_obs_time = None
    except (TypeError, ValueError):
        wu_last_obs_time = None
    verdict = check_wu_metar_divergence(
        city=city,
        target_date=target_date,
        metar_reports=metar_reports,
        wu_high_so_far=getattr(wu_obs, "high_so_far", None),
        wu_low_so_far=getattr(wu_obs, "low_so_far", None),
        wu_last_obs_time=wu_last_obs_time,
    )
    if verdict.compared and verdict.diverged:
        flag_day0_oracle_anomaly(city_name, target_date, detail=verdict.detail)


def check_wu_metar_divergence(
    *,
    city: Any,
    target_date: str,
    metar_reports: list,
    wu_high_so_far: Optional[float],
    wu_low_so_far: Optional[float],
    wu_last_obs_time: Optional[datetime],
) -> DivergenceVerdict:
    """Compare WU running extremes against METAR extremes over the SAME window.

    Caller supplies the WU side (from the existing settlement-bound WU obs
    context — high_so_far / low_so_far / observation_time). The METAR side is
    recomputed here truncated at wu_last_obs_time so latency cannot masquerade
    as divergence. Returns a verdict; flagging is the caller's choice (the
    fast-lane wiring flags + pauses on diverged=True).
    """
    from src.data.day0_fast_obs import running_extremes_for_local_day

    city_name = str(getattr(city, "name", "") or "")
    unit = str(getattr(city, "settlement_unit", "F") or "F").upper()
    if wu_last_obs_time is None or (wu_high_so_far is None and wu_low_so_far is None):
        return DivergenceVerdict(
            city=city_name, target_date=str(target_date), unit=unit,
            compared=False, diverged=False, detail="wu_side_unavailable",
        )
    truncated = running_extremes_for_local_day(
        metar_reports, city=city, target_date=target_date, as_of=wu_last_obs_time.astimezone(UTC)
    )
    if truncated.sample_count == 0:
        return DivergenceVerdict(
            city=city_name, target_date=str(target_date), unit=unit,
            compared=False, diverged=False, detail="metar_side_no_overlapping_samples",
        )
    threshold, threshold_provenance = divergence_threshold_for_city(city_name, unit)
    high_delta = (
        abs(float(wu_high_so_far) - float(truncated.high_so_far))
        if wu_high_so_far is not None and truncated.high_so_far is not None
        else None
    )
    low_delta = (
        abs(float(wu_low_so_far) - float(truncated.low_so_far))
        if wu_low_so_far is not None and truncated.low_so_far is not None
        else None
    )
    diverged = any(delta is not None and delta > threshold for delta in (high_delta, low_delta))
    detail = (
        f"unit={unit} threshold={threshold} threshold_provenance={threshold_provenance} "
        f"high_delta={high_delta} low_delta={low_delta} "
        f"wu_last_obs={wu_last_obs_time.isoformat()} metar_samples={truncated.sample_count}"
    )
    return DivergenceVerdict(
        city=city_name, target_date=str(target_date), unit=unit,
        compared=True, diverged=diverged,
        high_delta=high_delta, low_delta=low_delta,
        wu_last_obs_time=wu_last_obs_time.astimezone(UTC).isoformat(),
        metar_samples=truncated.sample_count, detail=detail,
    )
