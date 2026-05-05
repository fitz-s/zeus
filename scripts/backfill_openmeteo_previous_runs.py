#!/usr/bin/env python3
# Created: 2026-04-21
# Last reused/audited: 2026-04-27
# Lifecycle: created=2026-04-21; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Backfill Open-Meteo previous-runs forecast rows into forecasts.
# Reuse: Run only through packet-approved ETL/backfill workflows; dry-run first for live DB work.
# Authority basis: R3 F1 forecast provenance wiring + historical forecast backfill packet.
"""Backfill city forecast history from Open-Meteo Previous Runs.

This is the dynamic-city forecast-history lane. It writes raw forecast rows to
`forecasts`; downstream skill/bias/profile tables are derived by existing ETLs.

The Previous Runs API exposes hourly variables such as
`temperature_2m_previous_day1`. We aggregate each requested lead to a target-day
high/low, preserving `forecast_basis_date = target_date - lead_days` so the row
stays point-in-time traceable.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.config import City, cities, cities_by_name
from src.data.forecast_source_registry import (
    get_source,
    source_id_for_previous_runs_model,
    stable_payload_hash,
)
from src.state.db import get_world_connection, init_schema

logger = logging.getLogger(__name__)

PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
SOURCE = "openmeteo_previous_runs"
MODEL_SOURCE_MAP = {
    "best_match": "openmeteo_previous_runs",
    "gfs_global": "gfs_previous_runs",
    "ecmwf_ifs025": "ecmwf_previous_runs",
    "icon_global": "icon_previous_runs",
    "ukmo_global_deterministic_10km": "ukmo_previous_runs",
}
DEFAULT_MODELS = tuple(MODEL_SOURCE_MAP)
DEFAULT_CHUNK_DAYS = 90
DEFAULT_LEADS = tuple(range(1, 8))
MAX_RETRIES = 3

# Earth temperature records — reject any forecast that exceeds these as the
# model outputting garbage (NaN propagation, numerical overflow, etc.). These
# match IngestionGuard Layer 1 sub-check b thresholds, kept local to avoid
# importing the observation-oriented guard into a forecast script.
_EARTH_HIGH_F = 134.0   # Death Valley 1913
_EARTH_LOW_F  = -128.6  # Vostok 1983
_EARTH_HIGH_C = 56.7
_EARTH_LOW_C  = -89.2


def _validate_forecast_temps(
    high: float,
    low: float,
    unit: str,
) -> str | None:
    """Return rejection category (short string) or None if valid.

    Checks: finite, Earth records, high >= low. Does NOT check seasonal
    plausibility (forecasts can legitimately be more extreme than observations
    within the model's uncertainty envelope) and does NOT check TIGGE-derived
    p01/p99 bounds (TIGGE bounds under-represent real tails and mis-reject
    legitimate values — same issue that caused the 2026-04-13 WU backfill
    Layer 2 false-positive incident).
    """
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


# F11 (2026-04-28) + R3 (origin/plan-pre5 merge): the local
# ForecastBackfillRow dataclass + _INSERT_SQL duplicated
# src.data.forecasts_append.ForecastRow + _insert_rows AND bypassed
# the F11 antibody (NULL availability_provenance / forecast_issue_time
# would have been silently inserted). This script now imports the
# canonical ForecastRow + writer from forecasts_append, so backfill
# rows carry typed F11 provenance + R3 source_id/payload_hash/captured_at/
# authority_tier identically to the live ingest path. Path A duplication
# eliminated.
from src.data.dissemination_schedules import (
    UnknownSourceError,
    derive_availability,
)
from src.data.forecasts_append import (
    ForecastRow,
    _insert_rows as _canonical_insert_rows,
)


def _hourly_variable_for_lead(lead_days: int) -> str:
    if lead_days < 0:
        raise ValueError("lead_days must be >= 0")
    if lead_days == 0:
        return "temperature_2m"
    return f"temperature_2m_previous_day{lead_days}"


def _date_range(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def _fetch_previous_runs_chunk(
    city: City,
    start: date,
    end: date,
    *,
    leads: tuple[int, ...],
    models: tuple[str, ...],
) -> dict:
    temp_unit = "fahrenheit" if city.settlement_unit == "F" else "celsius"
    hourly_vars = [_hourly_variable_for_lead(lead) for lead in leads]
    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": hourly_vars,
        "temperature_unit": temp_unit,
        "timezone": city.timezone,
        "models": list(models),
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = httpx.get(
                PREVIOUS_RUNS_URL,
                params=params,
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            # Network layer error (ConnectTimeout, ReadTimeout, ProxyError etc.)
            # The original script only retried 429 and let network errors
            # propagate, losing entire chunks on transient glitches.
            if attempt < MAX_RETRIES:
                wait = 5.0 * attempt
                logger.warning(
                    "Network error on %s %s..%s attempt %d/%d: %s "
                    "(retrying in %.1fs)",
                    city.name, start, end, attempt, MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)
                continue
            raise
        if response.status_code == 429 and attempt < MAX_RETRIES:
            retry_after = response.headers.get("retry-after")
            try:
                sleep_seconds = float(retry_after) if retry_after else 15.0 * attempt
            except ValueError:
                sleep_seconds = 15.0 * attempt
            time.sleep(sleep_seconds)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("_fetch_previous_runs_chunk exhausted retries unexpectedly")


def _rows_from_payload(
    city: City,
    payload: dict,
    *,
    leads: tuple[int, ...],
    models: tuple[str, ...],
    retrieved_at: str,
    imported_at: str,
) -> tuple[list[ForecastRow], Counter]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    counters: Counter = Counter()
    if not times:
        counters["missing_time"] += 1
        return [], counters

    payload_hash = stable_payload_hash(payload)
    by_date: dict[tuple[str, int, str], list[float]] = {}
    for model in models:
        source = source_id_for_previous_runs_model(model)
        for lead in leads:
            base_variable = _hourly_variable_for_lead(lead)
            variable = f"{base_variable}_{model}"
            values = hourly.get(variable)
            if values is None and model == "best_match":
                values = hourly.get(base_variable)
            if values is None:
                counters[f"{variable}_missing"] += 1
                continue
            if len(values) != len(times):
                counters[f"{variable}_length_mismatch"] += 1
                continue
            for raw_time, value in zip(times, values):
                if value is None:
                    counters[f"{variable}_null"] += 1
                    continue
                target_date = str(raw_time)[:10]
                by_date.setdefault((target_date, lead, source), []).append(float(value))

    rows: list[ForecastRow] = []
    for (target_date, lead, source), temps in sorted(by_date.items()):
        source_spec = get_source(source)
        target = date.fromisoformat(target_date)
        basis = target - timedelta(days=lead)
        high = max(temps)
        low = min(temps)
        reason = _validate_forecast_temps(high, low, city.settlement_unit)
        if reason:
            counters[f"rejected_{reason}"] += 1
            logger.warning(
                "forecast rejected: %s %s lead=%d src=%s high=%s low=%s unit=%s reason=%s",
                city.name, target_date, lead, source, high, low, city.settlement_unit, reason,
            )
            continue
        # F11 antibody (2026-04-28): derive issue_time + provenance from the
        # source-specific dissemination schedule. Unregistered sources fail
        # gracefully (skip + counter) rather than aborting the whole backfill.
        base_time = datetime.combine(basis, datetime.min.time(), tzinfo=timezone.utc)
        try:
            issue_time, provenance = derive_availability(source, base_time, lead)
        except UnknownSourceError:
            counters[f"rejected_unregistered_source_{source}"] += 1
            logger.warning(
                "forecast skipped (unregistered source for F11): %s %s lead=%d src=%s",
                city.name, target_date, lead, source,
            )
            continue
        rows.append(
            ForecastRow(
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
                source_id=source_spec.source_id,
                raw_payload_hash=payload_hash,
                captured_at=retrieved_at,
                authority_tier=source_spec.authority_tier,
                rebuild_run_id=None,
                data_source_version=f"{source_spec.source_id}_v1",
                availability_provenance=provenance.value,
            )
        )
    return rows, counters


def _insert_rows(conn, rows: list[ForecastRow]) -> int:
    """Delegate to the canonical F11+R3-aware writer in src.data.forecasts_append.

    This eliminates the prior duplicate writer that bypassed the F11 antibody
    (NULL availability_provenance / forecast_issue_time would have been
    silently inserted) and the R3 source/payload/capture/authority_tier fields.
    Now any caller of this script gets the same fail-fast contract as the
    live cron path.
    """
    return _canonical_insert_rows(conn, rows)


def _resolve_cities(names: list[str] | None, *, only_missing_forecast_skill: bool) -> list[City]:
    selected = [cities_by_name[name] for name in names] if names else list(cities)
    if not only_missing_forecast_skill:
        return selected
    conn = get_world_connection()
    init_schema(conn)
    covered = {
        row[0]
        for row in conn.execute("SELECT DISTINCT city FROM forecast_skill").fetchall()
    }
    conn.close()
    return [city for city in selected if city.name not in covered]


def run_backfill(
    *,
    city_names: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 95,
    leads: tuple[int, ...] = DEFAULT_LEADS,
    models: tuple[str, ...] = DEFAULT_MODELS,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    sleep_seconds: float = 0.5,
    dry_run: bool = False,
    only_missing_forecast_skill: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)
    end = date.fromisoformat(end_date) if end_date else now.date() - timedelta(days=2)
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=days - 1)
    if start > end:
        raise ValueError("start_date must be <= end_date")

    selected = _resolve_cities(city_names, only_missing_forecast_skill=only_missing_forecast_skill)
    conn = get_world_connection()
    init_schema(conn)
    before = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]

    results = []
    totals = Counter()
    for city in selected:
        city_total = Counter(city=city.name)
        for chunk_start, chunk_end in _date_range(start, end, chunk_days):
            retrieved_at = datetime.now(timezone.utc).isoformat()
            try:
                payload = _fetch_previous_runs_chunk(
                    city,
                    chunk_start,
                    chunk_end,
                    leads=leads,
                    models=models,
                )
                rows, counters = _rows_from_payload(
                    city,
                    payload,
                    leads=leads,
                    models=models,
                    retrieved_at=retrieved_at,
                    imported_at=retrieved_at,
                )
                inserted = 0 if dry_run else _insert_rows(conn, rows)
                if not dry_run:
                    conn.commit()
                city_total["candidate_rows"] += len(rows)
                city_total["inserted"] += inserted
                city_total.update(counters)
            except Exception as exc:
                city_total["errors"] += 1
                city_total[f"error:{type(exc).__name__}"] += 1
                city_total["last_error"] = str(exc)[:200]
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
        totals.update({k: v for k, v in city_total.items() if isinstance(v, int)})
        results.append(dict(city_total))

    after = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
    city_count = conn.execute("SELECT COUNT(DISTINCT city) FROM forecasts").fetchone()[0]
    conn.close()
    return {
        "dry_run": dry_run,
        "source": SOURCE,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "leads": list(leads),
        "models": list(models),
        "city_count": len(selected),
        "forecasts_before": int(before),
        "forecasts_after": int(after),
        "forecasts_added": 0 if dry_run else int(after - before),
        "forecast_city_count": int(city_count),
        "totals": dict(totals),
        "cities": results,
    }


def _parse_leads(raw: str) -> tuple[int, ...]:
    leads = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    if not leads:
        raise argparse.ArgumentTypeError("lead list cannot be empty")
    if any(lead < 0 or lead > 7 for lead in leads):
        raise argparse.ArgumentTypeError("leads must be between 0 and 7")
    return tuple(sorted(set(leads)))


def _parse_models(raw: str) -> tuple[str, ...]:
    models = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not models:
        raise argparse.ArgumentTypeError("model list cannot be empty")
    unknown = [model for model in models if model not in MODEL_SOURCE_MAP]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown Open-Meteo model(s): {', '.join(unknown)}"
        )
    return tuple(dict.fromkeys(models))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities", nargs="+", default=None)
    parser.add_argument("--missing-forecast-skill", action="store_true")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--days", type=int, default=95)
    parser.add_argument("--leads", type=_parse_leads, default=DEFAULT_LEADS)
    parser.add_argument("--models", type=_parse_models, default=DEFAULT_MODELS)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run_backfill(
                city_names=args.cities,
                start_date=args.start_date,
                end_date=args.end_date,
                days=args.days,
                leads=args.leads,
                models=args.models,
                chunk_days=args.chunk_days,
                sleep_seconds=args.sleep,
                dry_run=args.dry_run,
                only_missing_forecast_skill=args.missing_forecast_skill,
            ),
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
