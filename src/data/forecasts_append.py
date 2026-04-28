"""K2 live NWP forecast appender (Open-Meteo Previous Runs API).

Keeps `forecasts` fresh for all 46 cities × 5 NWP models × 7 leads after
the initial historical backfill. This is the "forecast-history" lane:
rows are raw NWP outputs indexed by `(city, target_date, source, lead)`
where `source` names the model (gfs_previous_runs, ecmwf_previous_runs,
etc.) and `forecast_basis_date = target_date - lead_days` — preserving
point-in-time traceability.

Coverage tracking is at (city, source, target_date) grain — one coverage
row per day per model, with sub_key empty. Individual leads are not
tracked in data_coverage because the forecasts PK already includes lead
and INSERT OR IGNORE prevents duplication; the scanner only cares whether
the day has *any* rows from the model, which is the meaningful "did we
pull this day's forecast yet" signal.

Design note on forecast vs observation plausibility:
The duplicated `_validate_forecast_temps` check applies Earth records
only (Layer 1 subset). It deliberately does NOT apply seasonal
plausibility — forecasts can legitimately be more extreme than
climatology within the model's uncertainty envelope, and no other
forecast-plausibility-envelope data exists at Zeus's disposal. This
matches the pre-existing `scripts/backfill_openmeteo_previous_runs.py`
behavior.

Path A duplication from `scripts/backfill_openmeteo_previous_runs.py`.

Public API:
- `append_forecasts_window(city, start_date, end_date, conn, *,
  rebuild_run_id, models, leads)` — fetch and write one city's
  multi-model multi-lead forecasts for [start, end]
- `daily_tick(conn, *, now_utc)` — daemon daily entrypoint (UTC 07:30
  so the 00Z NWP runs are populated in the Previous Runs API)
- `catch_up_missing(conn, *, days_back)` — boot entrypoint
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from src.config import City, cities as ALL_CITIES
from src.state.data_coverage import (
    CoverageReason,
    DataTable,
    record_failed,
    record_legitimate_gap,
    record_written,
)

logger = logging.getLogger(__name__)

# Hoisted to module level (S2b fix): ExceptionsConfig.load() was previously
# called inside append_forecasts_window, which ran 46× per daily_tick.
# The config is static between deployments; loading once at import time
# means 46 disk reads per tick → 1 disk read per process lifetime.
# Tests that need to override the config can monkeypatch this module-level
# attribute before calling the appender.
_EXCEPTIONS_CONFIG = None  # populated lazily on first use to keep import cheap

def _get_exceptions_config():
    global _EXCEPTIONS_CONFIG
    if _EXCEPTIONS_CONFIG is None:
        from src.data.hole_scanner import ExceptionsConfig
        _EXCEPTIONS_CONFIG = ExceptionsConfig.load()
    return _EXCEPTIONS_CONFIG


from src.data.openmeteo_client import PREVIOUS_RUNS_URL, fetch as openmeteo_fetch

#: Internal Open-Meteo model name → canonical `forecasts.source` value.
#: Must stay aligned with scripts/backfill_openmeteo_previous_runs.py —
#: any drift here silently fragments per-model calibration buckets.
MODEL_SOURCE_MAP: dict[str, str] = {
    "best_match": "openmeteo_previous_runs",
    "gfs_global": "gfs_previous_runs",
    "ecmwf_ifs025": "ecmwf_previous_runs",
    "icon_global": "icon_previous_runs",
    "ukmo_global_deterministic_10km": "ukmo_previous_runs",
}
DEFAULT_MODELS: tuple[str, ...] = tuple(MODEL_SOURCE_MAP)
DEFAULT_LEADS: tuple[int, ...] = tuple(range(1, 8))
DEFAULT_CHUNK_DAYS = 90
SLEEP_BETWEEN_REQUESTS = 0.5

# Earth temperature records — duplicated from backfill_openmeteo_previous_runs.py
_EARTH_HIGH_F = 134.0
_EARTH_LOW_F = -128.6
_EARTH_HIGH_C = 56.7
_EARTH_LOW_C = -89.2


def _retry_embargo(hours: int = 1) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _hourly_variable_for_lead(lead_days: int) -> str:
    if lead_days < 0:
        raise ValueError("lead_days must be >= 0")
    if lead_days == 0:
        return "temperature_2m"
    return f"temperature_2m_previous_day{lead_days}"


def _validate_forecast_temps(high: float, low: float, unit: str) -> str | None:
    """Earth-records-only validation. Returns rejection category or None."""
    if not (math.isfinite(high) and math.isfinite(low)):
        return "not_finite"
    if high < low:
        return "high_less_than_low"
    if unit == "F":
        if high > _EARTH_HIGH_F:
            return "high_above_earth_record_f"
        if low < _EARTH_LOW_F:
            return "low_below_earth_record_f"
    elif unit == "C":
        if high > _EARTH_HIGH_C:
            return "high_above_earth_record_c"
        if low < _EARTH_LOW_C:
            return "low_below_earth_record_c"
    else:
        return f"unknown_unit_{unit}"
    return None


# ---------------------------------------------------------------------------
# Fetch layer (duplicated from scripts/backfill_openmeteo_previous_runs.py)
# ---------------------------------------------------------------------------


def _fetch_previous_runs_chunk(
    city: City,
    start: date,
    end: date,
    *,
    leads: tuple[int, ...],
    models: tuple[str, ...],
) -> dict:
    """Fetch one Open-Meteo Previous Runs chunk with retry-on-429."""
    temp_unit = "fahrenheit" if city.settlement_unit == "F" else "celsius"
    hourly_vars = [_hourly_variable_for_lead(lead) for lead in leads]
    return openmeteo_fetch(
        PREVIOUS_RUNS_URL,
        {
            "latitude": city.lat,
            "longitude": city.lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": hourly_vars,
            "temperature_unit": temp_unit,
            "timezone": city.timezone,
            "models": list(models),
        },
        timeout=60.0,
        endpoint_label="previous_runs",
    )


@dataclass(frozen=True)
class ForecastRow:
    city: str
    target_date: str
    source: str
    forecast_basis_date: str
    forecast_issue_time: Optional[str]
    lead_days: int
    lead_time_hours: float
    forecast_high: float
    forecast_low: float
    temp_unit: str
    retrieved_at: str
    imported_at: str
    rebuild_run_id: Optional[str] = None
    data_source_version: Optional[str] = None
    availability_provenance: Optional[str] = None  # F11 antibody: must be set, writer raises on None


def _rows_from_payload(
    city: City,
    payload: dict,
    *,
    leads: tuple[int, ...],
    models: tuple[str, ...],
    retrieved_at: str,
    imported_at: str,
    rebuild_run_id: str | None = None,
    data_source_version: str | None = None,
) -> tuple[list[ForecastRow], dict[tuple[str, str], int]]:
    """Parse payload into ForecastRow list + per-(source,target_date) row counts.

    The second return value is used to decide which (city, source,
    target_date) triples should be marked WRITTEN in data_coverage after
    the batch insert. A day with at least one lead present counts as
    covered for that source.
    """
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return [], {}

    by_key: dict[tuple[str, int, str], list[float]] = {}
    for model in models:
        source = MODEL_SOURCE_MAP.get(model, f"openmeteo_{model}")
        for lead in leads:
            base_variable = _hourly_variable_for_lead(lead)
            variable = f"{base_variable}_{model}"
            values = hourly.get(variable)
            if values is None and model == "best_match":
                values = hourly.get(base_variable)
            if values is None:
                continue
            if len(values) != len(times):
                continue
            for raw_time, value in zip(times, values):
                if value is None:
                    continue
                target_date = str(raw_time)[:10]
                by_key.setdefault((target_date, lead, source), []).append(float(value))

    from datetime import datetime, timezone
    from src.data.dissemination_schedules import derive_availability

    rows: list[ForecastRow] = []
    covered_days: dict[tuple[str, str], int] = {}  # (source, target_date) → row count
    for (target_date, lead, source), temps in sorted(by_key.items()):
        target = date.fromisoformat(target_date)
        basis = target - timedelta(days=lead)
        high = max(temps)
        low = min(temps)
        if _validate_forecast_temps(high, low, city.settlement_unit):
            continue
        # F11 antibody: derive forecast_issue_time + availability_provenance from
        # source-specific dissemination schedule. Open-Meteo Previous Runs does not
        # expose per-run issue times in payload; we assume 00 UTC base (the run
        # with longest forecast horizon, typically returned by previous_runs).
        base_time = datetime.combine(basis, datetime.min.time(), tzinfo=timezone.utc)
        issue_time, provenance = derive_availability(source, base_time, lead)
        rows.append(ForecastRow(
            city=city.name,
            target_date=target_date,
            source=source,
            forecast_basis_date=basis.isoformat(),
            forecast_issue_time=issue_time.isoformat(),
            lead_days=lead,
            lead_time_hours=float(lead * 24),
            forecast_high=high,
            forecast_low=low,
            temp_unit=city.settlement_unit,
            retrieved_at=retrieved_at,
            imported_at=imported_at,
            rebuild_run_id=rebuild_run_id,
            data_source_version=data_source_version,
            availability_provenance=provenance.value,
        ))
        covered_days[(source, target_date)] = covered_days.get((source, target_date), 0) + 1
    return rows, covered_days


# ---------------------------------------------------------------------------
# Write layer
# ---------------------------------------------------------------------------


_INSERT_SQL = """
INSERT OR IGNORE INTO forecasts (
    city, target_date, source, forecast_basis_date, forecast_issue_time,
    lead_days, lead_time_hours, forecast_high, forecast_low, temp_unit,
    retrieved_at, imported_at, rebuild_run_id, data_source_version,
    availability_provenance
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _insert_rows(conn, rows: list[ForecastRow]) -> int:
    if not rows:
        return 0
    # F11 antibody (Q4): writer rejects rows missing availability_provenance.
    # Wrong code becomes unwritable rather than silently producing untyped rows.
    for r in rows:
        if r.availability_provenance is None or r.forecast_issue_time is None:
            raise ValueError(
                f"ForecastRow rejected: must carry availability_provenance + "
                f"forecast_issue_time (F11 antibody); city={r.city} "
                f"source={r.source} target={r.target_date} lead={r.lead_days}"
            )
    before = conn.total_changes
    conn.executemany(_INSERT_SQL, [
        (
            r.city, r.target_date, r.source, r.forecast_basis_date,
            r.forecast_issue_time, r.lead_days, r.lead_time_hours,
            r.forecast_high, r.forecast_low, r.temp_unit,
            r.retrieved_at, r.imported_at,
            r.rebuild_run_id, r.data_source_version,
            r.availability_provenance,
        )
        for r in rows
    ])
    return conn.total_changes - before


# ---------------------------------------------------------------------------
# Public: per-city window append
# ---------------------------------------------------------------------------


def append_forecasts_window(
    city: City,
    start_date: date,
    end_date: date,
    conn,
    *,
    rebuild_run_id: str,
    models: tuple[str, ...] = DEFAULT_MODELS,
    leads: tuple[int, ...] = DEFAULT_LEADS,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    sleep_seconds: float = SLEEP_BETWEEN_REQUESTS,
) -> dict:
    """Fetch and write NWP forecasts for [start, end] for one city.

    For each (source, target_date) pair that receives at least one
    successfully-inserted row, records WRITTEN in data_coverage. Whole-
    chunk fetch failures mark every (source, target_date) in the chunk
    as FAILED with a 1h retry embargo. Pre-retro-start dates (e.g.
    UKMO < 2024-08-04) are pinned as LEGITIMATE_GAP before the fetch,
    so the API is never asked for data that definitionally doesn't exist.
    """
    stats = {
        "fetched_rows": 0, "inserted": 0,
        "fetch_errors": 0, "dates_marked_written": 0,
        "dates_marked_legitimate_gap": 0,
    }
    if start_date > end_date:
        return stats

    # Pre-pin pre-retro days as LEGITIMATE_GAP so the API isn't hit with
    # definitional gaps. Same whitelist logic as hole_scanner's
    # _static_whitelist_reason. Config is module-level cached.
    cfg = _get_exceptions_config()
    for model in models:
        source = MODEL_SOURCE_MAP.get(model, f"openmeteo_{model}")
        retro_start = cfg.model_retro_starts.get(source)
        if retro_start is None:
            continue
        d = start_date
        while d <= end_date and d < retro_start:
            record_legitimate_gap(
                conn,
                data_table=DataTable.FORECASTS,
                city=city.name,
                data_source=source,
                target_date=d,
                reason=(
                    CoverageReason.UKMO_PRE_START
                    if "ukmo" in source
                    else CoverageReason.SOURCE_NOT_PUBLISHED_YET
                ),
            )
            stats["dates_marked_legitimate_gap"] += 1
            d += timedelta(days=1)
    conn.commit()

    current = max(start_date, min(
        cfg.model_retro_starts.get(MODEL_SOURCE_MAP[m], start_date)
        for m in models
    ))
    if current > end_date:
        return stats

    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        try:
            payload = _fetch_previous_runs_chunk(
                city, current, chunk_end, leads=leads, models=models,
            )
        except Exception as e:
            stats["fetch_errors"] += 1
            logger.warning(
                "forecasts chunk failed %s %s..%s: %s: %s",
                city.name, current, chunk_end, type(e).__name__, e,
            )
            # Mark every (source, target_date) in the chunk FAILED.
            for model in models:
                source = MODEL_SOURCE_MAP.get(model, f"openmeteo_{model}")
                d = current
                while d <= chunk_end:
                    record_failed(
                        conn,
                        data_table=DataTable.FORECASTS,
                        city=city.name,
                        data_source=source,
                        target_date=d,
                        reason=CoverageReason.NETWORK_ERROR,
                        retry_after=_retry_embargo(hours=1),
                    )
                    d += timedelta(days=1)
            conn.commit()
            current = chunk_end + timedelta(days=1)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            continue

        rows, covered_days = _rows_from_payload(
            city, payload, leads=leads, models=models,
            retrieved_at=retrieved_at, imported_at=retrieved_at,
            rebuild_run_id=rebuild_run_id,
        )
        stats["fetched_rows"] += len(rows)
        inserted = _insert_rows(conn, rows)
        stats["inserted"] += inserted

        # Flip (source, target_date) → WRITTEN for every day that got
        # at least one row inserted successfully.
        for (source, target_date_str), _n in covered_days.items():
            record_written(
                conn,
                data_table=DataTable.FORECASTS,
                city=city.name,
                data_source=source,
                target_date=target_date_str,
            )
            stats["dates_marked_written"] += 1

        conn.commit()
        current = chunk_end + timedelta(days=1)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return stats


# ---------------------------------------------------------------------------
# Public: daemon entrypoints
# ---------------------------------------------------------------------------


def daily_tick(
    conn,
    *,
    now_utc: Optional[datetime] = None,
    cities: Optional[Iterable[City]] = None,
    rebuild_run_id: Optional[str] = None,
    past_days: int = 3,
    future_days: int = 7,
) -> dict:
    """Daemon once-per-day entrypoint: fetch recent + near-term forecasts.

    Fires once per day (scheduled at UTC 07:30 in src/main.py) after the
    00Z NWP runs have had time to populate the Previous Runs API.
    Window is [today - past_days, today + future_days] per city.

    Past days are re-fetched to catch any Previous Runs API promotions
    (a day's forecast may be revised slightly after issue as more leads
    arrive). INSERT OR IGNORE on the PK (city, target_date, source,
    forecast_basis_date, lead_days) makes the idempotency clean.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if rebuild_run_id is None:
        rebuild_run_id = f"forecasts_tick_{now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    if cities is None:
        cities = list(ALL_CITIES)

    start_d = now_utc.date() - timedelta(days=past_days)
    end_d = now_utc.date() + timedelta(days=future_days)

    totals = {
        "cities_processed": 0, "fetched_rows": 0, "inserted": 0,
        "fetch_errors": 0, "dates_marked_written": 0,
        "dates_marked_legitimate_gap": 0,
    }
    for city in cities:
        stats = append_forecasts_window(
            city, start_d, end_d, conn, rebuild_run_id=rebuild_run_id,
        )
        totals["cities_processed"] += 1
        for k in ("fetched_rows", "inserted", "fetch_errors",
                  "dates_marked_written", "dates_marked_legitimate_gap"):
            totals[k] += stats.get(k, 0)
    return totals


def catch_up_missing(
    conn,
    *,
    days_back: int = 30,
    max_cities: int = 46,
    rebuild_run_id: Optional[str] = None,
) -> dict:
    """Daemon boot entrypoint: fill MISSING/retry-ready FAILED forecast rows."""
    from src.config import cities_by_name
    from src.state.data_coverage import find_pending_fills

    if rebuild_run_id is None:
        rebuild_run_id = (
            f"forecasts_catchup_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )

    cutoff = date.today() - timedelta(days=days_back)
    rows = find_pending_fills(
        conn, data_table=DataTable.FORECASTS, max_rows=200_000,
    )
    by_city: dict[str, list[date]] = {}
    for r in rows:
        target = date.fromisoformat(r["target_date"])
        if target < cutoff:
            continue
        by_city.setdefault(r["city"], []).append(target)

    totals = {
        "cities_touched": 0, "fetched_rows": 0, "inserted": 0,
        "fetch_errors": 0, "dates_marked_written": 0,
    }
    for i, (city_name, dates) in enumerate(by_city.items()):
        if i >= max_cities:
            break
        city = cities_by_name.get(city_name)
        if city is None:
            continue
        stats = append_forecasts_window(
            city, min(dates), max(dates), conn, rebuild_run_id=rebuild_run_id,
        )
        totals["cities_touched"] += 1
        for k in ("fetched_rows", "inserted", "fetch_errors", "dates_marked_written"):
            totals[k] += stats.get(k, 0)
    return totals
