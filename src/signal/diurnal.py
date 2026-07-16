"""Diurnal analysis utilities.

Provides data-driven peak hour and post-peak confidence from diurnal_curves table.
Replaces hardcoded `historical_peak_hour = 15.0` with per-city×season values.
"""

import logging
import re
from datetime import date, datetime, time, timezone
import math
from typing import Optional
from zoneinfo import ZoneInfo

from src.calibration.manager import season_from_month
from src.types import Day0TemporalContext, ObservationInstant, SolarDay

logger = logging.getLogger(__name__)


# G10 helper-extraction (2026-04-26, con-nyx APPROVE_WITH_CONDITIONS MAJOR #1):
# `_is_missing_local_hour` moved to src.contracts.dst_semantics so ingest-lane
# callers (src.data.daily_obs_append, src.data.hourly_instants_append, plus
# scripts/ingest/* tick scripts) don't transitively pull in src.signal — the
# trading-engine surface fenced off by tests/test_ingest_isolation.py.
# Re-exported here for back-compat: existing callers (src.data.ingestion_guard,
# this module's L340/L415, tests via `from src.signal.diurnal import
# _is_missing_local_hour`) keep working unchanged.
from src.contracts.dst_semantics import _is_missing_local_hour  # noqa: F401  (re-export)


def get_solar_day(city_name: str, target_date: date, *, conn=None) -> SolarDay | None:
    """Load DST-aware solar context for one city/day from Zeus-owned storage."""
    owns_connection = conn is None
    try:
        if owns_connection:
            from src.state.db import get_world_connection

            conn = get_world_connection()
        row = conn.execute(
            """
            SELECT timezone, sunrise_local, sunset_local, sunrise_utc, sunset_utc,
                   utc_offset_minutes, dst_active
            FROM solar_daily WHERE city = ? AND target_date = ?
            """,
            (city_name, target_date.isoformat()),
        ).fetchone()
        if row is None:
            return None

        return SolarDay(
            city=city_name,
            target_date=target_date,
            timezone=row["timezone"],
            sunrise_local=datetime.fromisoformat(row["sunrise_local"]),
            sunset_local=datetime.fromisoformat(row["sunset_local"]),
            sunrise_utc=datetime.fromisoformat(row["sunrise_utc"]),
            sunset_utc=datetime.fromisoformat(row["sunset_utc"]),
            utc_offset_minutes=int(row["utc_offset_minutes"]),
            dst_active=bool(row["dst_active"]),
        )
    except Exception as e:
        logger.debug("Solar context unavailable for %s %s: %s", city_name, target_date, e)
        return None
    finally:
        if owns_connection and conn is not None:
            conn.close()


def _solar_eval_hour(current_local_hour: int | float) -> float:
    """Return the local hour used for solar phase checks.

    Integer callers historically meant an hourly bucket, so evaluate them at
    the bucket midpoint for compatibility. Fractional callers already carry
    minute-level runtime truth and must not be rounded to the next bucket.
    """

    hour = float(current_local_hour)
    if isinstance(current_local_hour, int):
        return hour + 0.5
    return hour


def _apply_solar_bounds(confidence: float, current_local_hour: int | float, solar_day: SolarDay | None) -> float:
    """Constrain confidence with hard daylight facts when solar data exists."""
    if solar_day is None:
        return confidence
    current = _solar_eval_hour(current_local_hour)
    if solar_day.is_before_sunrise(current):
        return min(confidence, 0.05)
    if solar_day.is_after_sunset(current):
        return max(confidence, 0.98)
    return confidence


def _solar_heuristic_confidence(
    current_local_hour: int | float,
    peak_hour: int,
    solar_day: SolarDay | None,
) -> float | None:
    """Continuous daylight-phase heuristic using sunrise/sunset if available."""
    if solar_day is None:
        return None

    sunrise_hour = solar_day.sunrise_hour
    sunset_hour = solar_day.sunset_hour
    daylight = solar_day.daylight_hours
    if daylight <= 1.0:
        return None

    current = _solar_eval_hour(current_local_hour)
    if current < sunrise_hour:
        return 0.0
    if current >= sunset_hour:
        return 0.98

    progress = (current - sunrise_hour) / daylight
    peak_progress = (peak_hour + 0.5 - sunrise_hour) / daylight
    peak_progress = min(0.85, max(0.45, peak_progress))

    if progress <= peak_progress:
        return 0.05 + 0.45 * (progress / peak_progress)
    return 0.5 + 0.45 * ((progress - peak_progress) / max(0.05, 1.0 - peak_progress))


def _solar_only_post_peak_confidence(
    current_local_hour: int | float,
    solar_day: SolarDay | None,
) -> float | None:
    if solar_day is None:
        return None
    return _apply_solar_bounds(0.0, current_local_hour, solar_day)


def _row_value(row, key: str):
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return None


def _lookup_hourly_confidence(conn, query: str, params_prefix: tuple, hour: int) -> float | None:
    row = conn.execute(query, (*params_prefix, hour)).fetchone()
    if row is None:
        return None
    value = _row_value(row, "p_high_set")
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence):
        return None
    return min(1.0, max(0.0, confidence))


def _interpolate_hourly_confidence(
    *,
    lower_hour: int,
    lower_confidence: float | None,
    upper_hour: int,
    upper_confidence: float | None,
    current_local_hour: int | float,
) -> float | None:
    """Linearly interpolate hourly empirical confidence within the hour.

    Runtime Day0 contexts carry fractional local time. Without interpolation,
    the monitor can jump from one hourly cell to the next without any new
    observation, which makes post-peak probability changes look like new
    market evidence rather than wall-clock evidence.
    """

    if lower_confidence is None and upper_confidence is None:
        return None
    if lower_confidence is None:
        return upper_confidence
    if upper_confidence is None or upper_hour <= lower_hour:
        return lower_confidence
    hour = float(current_local_hour)
    weight = min(1.0, max(0.0, (hour - lower_hour) / (upper_hour - lower_hour)))
    return lower_confidence + (upper_confidence - lower_confidence) * weight


def _lookup_interpolated_monthly_confidence(
    conn,
    *,
    city_name: str,
    month: int,
    current_local_hour: int | float,
) -> float | None:
    hour = float(current_local_hour)
    lower_hour = int(math.floor(hour))
    upper_hour = int(math.ceil(hour))
    query = (
        "SELECT p_high_set FROM diurnal_peak_prob "
        "WHERE city = ? AND month = ? AND hour = ?"
    )
    lower = _lookup_hourly_confidence(conn, query, (city_name, month), lower_hour)
    upper = lower if upper_hour == lower_hour else _lookup_hourly_confidence(
        conn, query, (city_name, month), upper_hour
    )
    return _interpolate_hourly_confidence(
        lower_hour=lower_hour,
        lower_confidence=lower,
        upper_hour=upper_hour,
        upper_confidence=upper,
        current_local_hour=current_local_hour,
    )


def _interpolated_seasonal_confidence(
    season_rows,
    current_local_hour: int | float,
) -> float | None:
    hour = float(current_local_hour)
    values: list[tuple[int, float]] = []
    for row in season_rows:
        raw_hour = _row_value(row, "hour")
        raw_confidence = _row_value(row, "p_high_set")
        if raw_hour is None or raw_confidence is None:
            continue
        try:
            row_hour = int(raw_hour)
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(confidence):
            continue
        values.append((row_hour, min(1.0, max(0.0, confidence))))
    if not values:
        return None
    values.sort(key=lambda item: item[0])
    lower_hour = int(math.floor(hour))
    upper_hour = int(math.ceil(hour))
    by_hour = {row_hour: confidence for row_hour, confidence in values}
    lower = by_hour.get(lower_hour)
    upper = by_hour.get(upper_hour)
    if lower is None:
        lower_candidates = [item for item in values if item[0] <= hour]
        lower = lower_candidates[-1][1] if lower_candidates else None
        lower_hour = lower_candidates[-1][0] if lower_candidates else lower_hour
    if upper is None:
        upper_candidates = [item for item in values if item[0] >= hour]
        upper = upper_candidates[0][1] if upper_candidates else None
        upper_hour = upper_candidates[0][0] if upper_candidates else upper_hour
    return _interpolate_hourly_confidence(
        lower_hour=lower_hour,
        lower_confidence=lower,
        upper_hour=upper_hour,
        upper_confidence=upper,
        current_local_hour=current_local_hour,
    )


def get_peak_hour_context(
    city_name: str,
    target_date: date,
    current_local_hour: int | float,
    *,
    conn=None,
    solar_day: SolarDay | None = None,
) -> tuple[Optional[int], float, str]:
    """Single source of truth for peak hour.
    Returns: (peak_hour, confidence, fallback_reason)

    Confidence = empirical P(daily max already set | city, month, hour).
    Resolution hierarchy:
      1. diurnal_peak_prob (monthly) — 30+ days per cell
      2. diurnal_curves.p_high_set (seasonal) — ~120 days per cell
      3. heuristic slope — cities without openmeteo_archive daily coverage
    """
    from src.calibration.manager import lat_for_city
    city_lat = lat_for_city(city_name)
    season = season_from_month(target_date.month, lat=city_lat)
    month = target_date.month
    if solar_day is None:
        solar_day = get_solar_day(city_name, target_date, conn=conn)

    owns_connection = conn is None
    try:
        if owns_connection:
            from src.state.db import get_world_connection

            conn = get_world_connection()

        # Peak hour from seasonal avg_temp curve (unchanged)
        season_rows = conn.execute(
            "SELECT hour, avg_temp, std_temp, p_high_set FROM diurnal_curves "
            "WHERE city = ? AND season = ? ORDER BY hour",
            (city_name, season),
        ).fetchall()

        if not season_rows or len(season_rows) < 12:
            # Fail-closed fallback. AC11 (tests/test_diurnal_curves_empty_hk_handled.py)
            # pins this behavior for Hong Kong where diurnal_curves is empty
            # by design (plan v3 Option A: accumulator-forward-only, no
            # historical). Plan v3 S3 Recovery mentioned a "fleet-average
            # diurnal shape" fallback; Phase 3 closeout (step7) explicitly
            # rejected that in favor of this fail-closed path because (1) the
            # fleet spans temperate + tropical + Southern-hemisphere climates
            # whose diurnal shapes do not transfer to HK, and (2) plan v3
            # Option A's whole premise is "do not fabricate HK data". A future
            # packet may revisit with a geographic-peer-average (Guangzhou /
            # Shenzhen / Singapore) if HK trading needs a diurnal prior before
            # the hko_hourly_accumulator builds native history.
            solar_conf = _solar_only_post_peak_confidence(current_local_hour, solar_day)
            if solar_conf is not None:
                return None, solar_conf, "solar_only_no_diurnal_history"
            return None, 0.0, "insufficient_diurnal_data_rows"

        peak_row = max(season_rows, key=lambda r: r["avg_temp"])
        peak_hour = int(peak_row["hour"])

        # 1. Monthly lookup
        monthly_confidence = _lookup_interpolated_monthly_confidence(
            conn,
            city_name=city_name,
            month=month,
            current_local_hour=current_local_hour,
        )

        if monthly_confidence is not None:
            conf = _apply_solar_bounds(monthly_confidence, current_local_hour, solar_day)
            return peak_hour, conf, "monthly_empirical"

        # 2. Seasonal fallback
        seasonal_confidence = _interpolated_seasonal_confidence(
            season_rows,
            current_local_hour,
        )
        current_row = next((r for r in season_rows if r["hour"] == int(math.floor(float(current_local_hour)))), None)
        if seasonal_confidence is not None:
            conf = _apply_solar_bounds(seasonal_confidence, current_local_hour, solar_day)
            return peak_hour, conf, "seasonal_empirical"

        # 3. Solar-aware heuristic fallback
        solar_conf = _solar_heuristic_confidence(current_local_hour, peak_hour, solar_day)
        if solar_conf is not None:
            return peak_hour, solar_conf, "solar_heuristic"

        # 4. Legacy slope heuristic (cities without solar context)
        peak_temp = peak_row["avg_temp"]
        if current_local_hour < peak_hour - 2:
            return peak_hour, 0.1, "well_before_peak"
        if current_local_hour < peak_hour:
            return peak_hour, 0.3, "approaching_peak"
        if current_local_hour == peak_hour:
            return peak_hour, 0.5, "at_peak_uncertain"
        if current_row is None:
            return peak_hour, 0.95, "late_night_wrap"
        hours_past_peak = current_local_hour - peak_hour
        temp_drop = peak_temp - current_row["avg_temp"]
        drop_zscore = temp_drop / peak_row["std_temp"] if peak_row["std_temp"] > 0 else 1.0
        time_confidence = min(0.95, 0.5 + hours_past_peak * 0.1)
        drop_confidence = min(0.95, 0.5 + drop_zscore * 0.15)
        return peak_hour, max(time_confidence, drop_confidence), "heuristic_slope"

    except Exception as e:
        logger.debug("Failed to fetch peak hour context for %s: %s", city_name, e)
        return None, 0.0, f"exception_or_no_data: {e}"
    finally:
        if owns_connection and conn is not None:
            conn.close()


def post_peak_confidence(
    city_name: str,
    target_date: date,
    current_local_hour: int | float,
) -> float:
    """Empirical P(daily high already set | city, month, hour).

    Resolution hierarchy:
      1. diurnal_peak_prob (monthly) — most precise, 30+ days/cell
      2. diurnal_curves.p_high_set (seasonal) — ~120 days/cell
      3. heuristic slope — cities without openmeteo_archive coverage

    Returns:
        0.0 - 0.3: pre-peak (observation not yet dominant)
        0.3 - 0.7: near peak, uncertain
        0.7 - 1.0: post-peak, observation dominates ENS
    """
    from src.calibration.manager import lat_for_city
    season = season_from_month(target_date.month, lat=lat_for_city(city_name))
    month = target_date.month
    solar_day = get_solar_day(city_name, target_date)

    try:
        from src.state.db import get_world_connection

        conn = get_world_connection()

        # 1. Monthly lookup
        monthly_confidence = _lookup_interpolated_monthly_confidence(
            conn,
            city_name=city_name,
            month=month,
            current_local_hour=current_local_hour,
        )
        if monthly_confidence is not None:
            conn.close()
            return _apply_solar_bounds(monthly_confidence, current_local_hour, solar_day)

        # 2. Seasonal fallback
        season_rows = conn.execute(
            "SELECT hour, avg_temp, std_temp, p_high_set FROM diurnal_curves "
            "WHERE city = ? AND season = ? ORDER BY hour",
            (city_name, season),
        ).fetchall()
        conn.close()

        if not season_rows or len(season_rows) < 12:
            solar_conf = _solar_only_post_peak_confidence(current_local_hour, solar_day)
            return solar_conf if solar_conf is not None else 0.0

        seasonal_confidence = _interpolated_seasonal_confidence(
            season_rows,
            current_local_hour,
        )
        current_row = next((r for r in season_rows if r["hour"] == int(math.floor(float(current_local_hour)))), None)
        if seasonal_confidence is not None:
            return _apply_solar_bounds(seasonal_confidence, current_local_hour, solar_day)

        # 3. Solar-aware heuristic fallback
        peak_row = max(season_rows, key=lambda r: r["avg_temp"])
        peak_hour = peak_row["hour"]
        solar_conf = _solar_heuristic_confidence(current_local_hour, peak_hour, solar_day)
        if solar_conf is not None:
            return solar_conf

        # 4. Legacy slope heuristic
        peak_temp = peak_row["avg_temp"]

        if current_local_hour < peak_hour - 2:
            return 0.1
        if current_local_hour < peak_hour:
            return 0.3
        if current_local_hour == peak_hour:
            return 0.5
        if current_row is None:
            return 0.95

        hours_past_peak = current_local_hour - peak_hour
        temp_drop = peak_temp - current_row["avg_temp"]
        drop_zscore = temp_drop / peak_row["std_temp"] if peak_row["std_temp"] > 0 else 1.0
        time_confidence = min(0.95, 0.5 + hours_past_peak * 0.1)
        drop_confidence = min(0.95, 0.5 + drop_zscore * 0.15)
        return max(time_confidence, drop_confidence)

    except Exception as e:
        logger.debug("Post-peak confidence failed for %s: %s", city_name, e)
        return 0.0


def get_current_local_hour(timezone: str) -> int:
    """Get the current hour in a city's local timezone."""
    tz = ZoneInfo(timezone)
    return datetime.now(tz).hour


def get_daylight_progress(city_name: str, target_date: date, current_local_hour: int) -> float | None:
    solar_day = get_solar_day(city_name, target_date)
    if solar_day is None:
        return None
    return solar_day.daylight_progress(current_local_hour + 0.5)


def _fractional_local_hour(ts: datetime) -> float:
    return ts.hour + ts.minute / 60.0 + ts.second / 3600.0


def _instant_from_local_hour(
    city_name: str,
    target_date: date,
    timezone_name: str,
    local_hour: float,
    *,
    source: str,
    time_basis: str,
) -> ObservationInstant:
    tz = ZoneInfo(timezone_name)
    hour = int(local_hour)
    minute_float = (float(local_hour) - hour) * 60.0
    minute = int(minute_float)
    second = int(round((minute_float - minute) * 60.0))
    if second == 60:
        second = 0
        minute += 1
    if minute == 60:
        minute = 0
        hour += 1
    local_ts = datetime.combine(target_date, time(hour % 24, minute, second), tzinfo=tz)
    utc_ts = local_ts.astimezone(timezone.utc)
    dst_delta = local_ts.dst()
    return ObservationInstant(
        city=city_name,
        target_date=target_date,
        source=source,
        timezone=timezone_name,
        local_timestamp=local_ts,
        utc_timestamp=utc_ts,
        utc_offset_minutes=int(local_ts.utcoffset().total_seconds() / 60.0),
        dst_active=bool(dst_delta and dst_delta.total_seconds() != 0.0),
        is_ambiguous_local_hour=bool(getattr(local_ts, "fold", 0)),
        is_missing_local_hour=_is_missing_local_hour(local_ts, tz),
        time_basis=time_basis,
        local_hour=float(local_hour),
    )


def _parse_runtime_observation_instant(
    city_name: str,
    target_date: date,
    timezone_name: str,
    observation_time,
    *,
    source: str,
) -> ObservationInstant | None:
    if observation_time is None:
        return None

    tz = ZoneInfo(timezone_name)
    local_ts: datetime | None = None
    time_basis = ""

    try:
        if isinstance(observation_time, (int, float)):
            utc_ts = datetime.fromtimestamp(float(observation_time), tz=timezone.utc)
            local_ts = utc_ts.astimezone(tz)
            time_basis = "runtime_epoch_utc"
        else:
            raw = str(observation_time).strip()
            if raw.isdigit():
                utc_ts = datetime.fromtimestamp(float(raw), tz=timezone.utc)
                local_ts = utc_ts.astimezone(tz)
                time_basis = "runtime_epoch_utc"
            else:
                parsed: datetime | None = None
                try:
                    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    stripped = re.sub(r"\s+[A-Z]{2,5}$", "", raw)
                    for fmt in (
                        "%Y-%m-%d %I:%M %p",
                        "%Y-%m-%d %I:%M:%S %p",
                        "%Y-%m-%d %H:%M",
                        "%Y-%m-%d %H:%M:%S",
                    ):
                        try:
                            parsed = datetime.strptime(stripped, fmt)
                            break
                        except ValueError:
                            continue
                if parsed is None:
                    return None
                if parsed.tzinfo is None:
                    local_ts = parsed.replace(tzinfo=tz)
                    time_basis = "runtime_local_timestamp"
                else:
                    local_ts = parsed.astimezone(tz)
                    time_basis = "runtime_timestamp"
    except (OSError, OverflowError, ValueError):
        return None

    if local_ts is None:
        return None

    utc_ts = local_ts.astimezone(timezone.utc)
    dst_delta = local_ts.dst()
    return ObservationInstant(
        city=city_name,
        target_date=target_date,
        source=source,
        timezone=timezone_name,
        local_timestamp=local_ts,
        utc_timestamp=utc_ts,
        utc_offset_minutes=int(local_ts.utcoffset().total_seconds() / 60.0),
        dst_active=bool(dst_delta and dst_delta.total_seconds() != 0.0),
        is_ambiguous_local_hour=bool(getattr(local_ts, "fold", 0)),
        is_missing_local_hour=_is_missing_local_hour(local_ts, tz),
        time_basis=time_basis,
        local_hour=_fractional_local_hour(local_ts),
    )


def build_day0_temporal_context(
    city_name: str,
    target_date: date,
    timezone: str,
    current_local_hour: float | None = None,
    observation_time=None,
    observation_source: str = "",
    *,
    conn=None,
) -> Day0TemporalContext | None:
    """Single entry point for Day0 time semantics.

    Returns None only when solar data unavailable (SolarLookupFailed)
    or observation date doesn't match target (ObservationDateMismatch).
    Callers wrapped in try/except will get the specific exception type.
    """
    solar_day = get_solar_day(city_name, target_date, conn=conn)
    if solar_day is None:
        logger.warning(
            "Day0 solar lookup failed for %s %s — no solar data available",
            city_name, target_date,
        )
        return None

    observation_instant = _parse_runtime_observation_instant(
        city_name,
        target_date,
        timezone,
        observation_time,
        source=observation_source or "runtime_observation",
    )
    if observation_instant is None and current_local_hour is not None:
        observation_instant = _instant_from_local_hour(
            city_name,
            target_date,
            timezone,
            float(current_local_hour),
            source=observation_source or "synthetic_local_hour",
            time_basis="synthetic_local_hour",
        )
    if observation_instant is None:
        observation_instant = _instant_from_local_hour(
            city_name,
            target_date,
            timezone,
            float(get_current_local_hour(timezone)),
            source="system_clock",
            time_basis="system_clock",
        )

    if observation_instant.local_timestamp.date() != target_date:
        logger.warning(
            "Refusing Day0 temporal context for %s %s: observation local date %s mismatches target_date",
            city_name,
            target_date,
            observation_instant.local_timestamp.date(),
        )
        return None

    local_hour = observation_instant.local_hour_fraction
    peak_hour, confidence, reason = get_peak_hour_context(
        city_name,
        target_date,
        local_hour,
        conn=conn,
        solar_day=solar_day,
    )
    daylight_progress = solar_day.daylight_progress(local_hour)
    return Day0TemporalContext(
        city=city_name,
        target_date=target_date,
        timezone=timezone,
        current_local_timestamp=observation_instant.local_timestamp,
        current_utc_timestamp=observation_instant.utc_timestamp,
        current_local_hour=local_hour,
        solar_day=solar_day,
        observation_instant=observation_instant,
        peak_hour=peak_hour,
        post_peak_confidence=confidence,
        daylight_progress=daylight_progress,
        utc_offset_minutes=observation_instant.utc_offset_minutes,
        dst_active=observation_instant.dst_active,
        is_ambiguous_local_hour=observation_instant.is_ambiguous_local_hour,
        is_missing_local_hour=observation_instant.is_missing_local_hour,
        time_basis=observation_instant.time_basis,
        confidence_source=reason,
    )
