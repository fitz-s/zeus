#!/usr/bin/env python3
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
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.config import City, cities, cities_by_name
from src.state.db import get_shared_connection, init_schema

PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
SOURCE = "openmeteo_previous_runs"
DEFAULT_CHUNK_DAYS = 90
DEFAULT_LEADS = tuple(range(1, 8))


@dataclass(frozen=True)
class ForecastBackfillRow:
    city: str
    target_date: str
    source: str
    forecast_basis_date: str
    forecast_issue_time: str | None
    lead_days: int
    lead_time_hours: float
    forecast_high: float
    forecast_low: float
    temp_unit: str
    retrieved_at: str
    imported_at: str


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
) -> dict:
    temp_unit = "fahrenheit" if city.settlement_unit == "F" else "celsius"
    hourly_vars = [_hourly_variable_for_lead(lead) for lead in leads]
    response = httpx.get(
        PREVIOUS_RUNS_URL,
        params={
            "latitude": city.lat,
            "longitude": city.lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": hourly_vars,
            "temperature_unit": temp_unit,
            "timezone": city.timezone,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


def _rows_from_payload(
    city: City,
    payload: dict,
    *,
    leads: tuple[int, ...],
    retrieved_at: str,
    imported_at: str,
) -> tuple[list[ForecastBackfillRow], Counter]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    counters: Counter = Counter()
    if not times:
        counters["missing_time"] += 1
        return [], counters

    by_date: dict[tuple[str, int], list[float]] = {}
    for lead in leads:
        variable = _hourly_variable_for_lead(lead)
        values = hourly.get(variable) or []
        if len(values) != len(times):
            counters[f"{variable}_length_mismatch"] += 1
            continue
        for raw_time, value in zip(times, values):
            if value is None:
                counters[f"{variable}_null"] += 1
                continue
            target_date = str(raw_time)[:10]
            by_date.setdefault((target_date, lead), []).append(float(value))

    rows: list[ForecastBackfillRow] = []
    for (target_date, lead), temps in sorted(by_date.items()):
        target = date.fromisoformat(target_date)
        basis = target - timedelta(days=lead)
        rows.append(
            ForecastBackfillRow(
                city=city.name,
                target_date=target_date,
                source=SOURCE,
                forecast_basis_date=basis.isoformat(),
                forecast_issue_time=None,
                lead_days=lead,
                lead_time_hours=float(lead * 24),
                forecast_high=max(temps),
                forecast_low=min(temps),
                temp_unit=city.settlement_unit,
                retrieved_at=retrieved_at,
                imported_at=imported_at,
            )
        )
    return rows, counters


def _insert_rows(conn, rows: list[ForecastBackfillRow]) -> int:
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO forecasts (
            city,
            target_date,
            source,
            forecast_basis_date,
            forecast_issue_time,
            lead_days,
            lead_time_hours,
            forecast_high,
            forecast_low,
            temp_unit,
            retrieved_at,
            imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.city,
                row.target_date,
                row.source,
                row.forecast_basis_date,
                row.forecast_issue_time,
                row.lead_days,
                row.lead_time_hours,
                row.forecast_high,
                row.forecast_low,
                row.temp_unit,
                row.retrieved_at,
                row.imported_at,
            )
            for row in rows
        ],
    )
    return conn.total_changes - before


def _resolve_cities(names: list[str] | None, *, only_missing_forecast_skill: bool) -> list[City]:
    selected = [cities_by_name[name] for name in names] if names else list(cities)
    if not only_missing_forecast_skill:
        return selected
    conn = get_shared_connection()
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
    conn = get_shared_connection()
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
                )
                rows, counters = _rows_from_payload(
                    city,
                    payload,
                    leads=leads,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities", nargs="+", default=None)
    parser.add_argument("--missing-forecast-skill", action="store_true")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--days", type=int, default=95)
    parser.add_argument("--leads", type=_parse_leads, default=DEFAULT_LEADS)
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
