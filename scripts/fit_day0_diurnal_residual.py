#!/usr/bin/env python3
# Created: 2026-09-04
# Last reused or audited: 2026-09-13
# Authority basis: diurnal-residual study 2026-09-04 (scratchpad/diurnal/REPORT.md §5).
#   Row construction mirrors the study's build_clim.py / build_clim2.py / merge_clim.py;
#   the histogram cells and shrink constants live with the server in
#   src/calibration/day0_diurnal_residual.py so fit and serve can never disagree.
#
# 2026-09-13: The 48-city migration of settlement_source_type to "noaa"
#   (config/cities.json, commit 274fe3a4b "restore NOAA city universe") starved this
#   fitter of training rows for those cities from 2026-09-02 onward: it was hard-pinned
#   to ``wu_icao_history`` (plus a 4-city ALT_SOURCE_CITIES map) and never followed the
#   era-aware settlement-station switch to the Ogimet METAR mirror. Row selection is now
#   config-derived per (city, day) via ``src.data.tier_resolver`` -- the SAME era-aware
#   routing the observation-instants writer and backfill driver already use -- so a
#   settlement-authority migration in cities.json is picked up here automatically
#   instead of requiring a parallel edit to this script's hardcoded pins. The
#   residual-grid arithmetic also now shares the settlement rounding law
#   (WMO half-up, ``src.contracts.settlement_semantics``) with the server instead of
#   Python's banker's ``round()``, which silently disagreed with it at .5 cumulative
#   values (see module docstring "GRID PRECISION" below).
"""Fit the Day0 diurnal-residual artifact ``state/day0_diurnal_residual.json``.

WHAT IS FITTED. The empirical distribution of D = final_extreme - running_extreme by
(metric, k = hours-to-peak, NWP-gap band), as raw COUNTS per cell. The artifact stores
counts, not the source records, so the loader reads it in milliseconds; the
Empirical-Bayes shrink is applied at serve time from those counts.

ROW SOURCE, per (city, day), one hourly ledger -- config-derived, era-aware:
  * ``src.data.tier_resolver.tier_for_city(city, target_date=day)`` resolves the
    settlement-station family effective on THAT day from ``config/cities.json``
    (``settlement_source_type`` + its effective-date/previous-type era fields), exactly
    as the observation-instants writer and backfill driver do. WU_ICAO -> ``source =
    'wu_icao_history'``; OGIMET_METAR (``settlement_source_type == 'noaa'``) -> ``source
    = 'ogimet_metar_<icao>'`` for that city's settlement ICAO (``city.wu_station``);
    HKO_NATIVE (Hong Kong) -> ``source = 'hko_hourly_accumulator'`` (the openmeteo grid
    archive carries a -0.8 degC median bias against the HKO settlement station, so it is
    never used).
  * FALLBACK: when a day's era-correct ledger (OGIMET_METAR only) has fewer than
    ``MIN_HOURS_ALT`` hours -- typically a day just after the settlement-authority
    migration, before the Ogimet mirror had ramped up, while WU history for that same
    physical ICAO was still being written -- the day falls back to that city's
    ``wu_icao_history`` rows for the SAME station (``station_id`` verified equal to
    ``city.wu_station``; the two ledgers are never merged into one day's envelope, only
    one or the other is used whole). WU_ICAO- and HKO_NATIVE-era days never fall back:
    there is no alternate ledger for the HKO station, and a WU-era day already IS the
    primary.
The cumulative extreme is RECOMPUTED per day from the per-hour running_max/running_min
rather than trusted as stored, and ``final`` is the VERIFIED ``settlement_outcomes``
value when one exists in the same unit, else the day's own cumulative extreme.

GRID PRECISION. The server (``DiurnalResidualNowcast.bin_probability``) anchors its
integer settlement grid at ``round_wmo_half_up_value(running_extreme)`` and reads
final = anchor + j (HIGH) / anchor - j (LOW). This fitter computes the residual against
that SAME anchor -- ``j = final - round_wmo_half_up_value(cumulative_high)`` for HIGH,
``round_wmo_half_up_value(cumulative_low) - final`` for LOW -- instead of rounding the
raw float difference after the fact. The two disagree exactly at a cumulative ending in
``.5``: e.g. cumulative=78.5, final=80 -> naive ``round(80 - 78.5) == round(1.5) == 2``
(Python banker's rounding, half-to-even) but the server's own anchor is
``round_wmo_half_up_value(78.5) == 79``, so the served j is ``80 - 79 == 1``. Since
ogimet's native-tenths ledger (unlike WU's whole-degree one) actually produces `.5`
cumulative values, this divergence is real, not theoretical, for NOAA cities. VERIFIED
settlement values are already integers on the settlement grid (checked empirically:
13296/13296 in the live forecasts DB); the day-extreme fallback is rounded through the
same law for consistency.

WALK-FORWARD. Every record whose date is >= ``fit_date`` is DROPPED. That is the whole
walk-forward guarantee: the server does no date filtering, so an artifact that contained
the target day's own records would leak the outcome into the decision that trades it.
``--fit-date`` therefore also names the first day the artifact may legitimately serve.

READ-ONLY w.r.t. the live databases (``mode=ro`` + ``PRAGMA query_only``); writes only
the JSON artifact.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sqlite3
import statistics
import sys
from datetime import date, datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.day0_diurnal_residual import (  # noqa: E402
    GAP_BAND_EDGES,
    J_MAX,
    SCHEMA_VERSION,
    gap_band_index,
)
from src.config import cities_by_name  # noqa: E402
from src.contracts.settlement_semantics import round_wmo_half_up_value  # noqa: E402
from src.data.tier_resolver import (  # noqa: E402
    EXPECTED_SOURCE_BY_CITY,
    TIER_SCHEDULE,
    Tier,
    UnsupportedTierError,
    expected_source_for_city,
    tier_for_city,
)

DEFAULT_WORLD_DB = os.path.join(REPO, "state", "zeus-world.db")
DEFAULT_FORECAST_DB = os.path.join(REPO, "state", "zeus-forecasts.db")
DEFAULT_OUT = os.path.join(REPO, "state", "day0_diurnal_residual.json")

HISTORY_START = "2024-01-01"
# A WU day needs near-complete hourly coverage before its cumulative curve is
# trustworthy; the ogimet/HKO ledgers are sparser, so they carry their own floor.
# Keyed by ledger FAMILY (Tier), never by a city list, so a settlement-authority
# migration in cities.json changes which floor a city's day is held to automatically.
MIN_HOURS_WU = 22
MIN_HOURS_ALT = 20
WU_SOURCE = "wu_icao_history"
_FLOOR_BY_TIER: dict[Tier, int] = {
    Tier.WU_ICAO: MIN_HOURS_WU,
    Tier.OGIMET_METAR: MIN_HOURS_ALT,
    Tier.HKO_NATIVE: MIN_HOURS_ALT,
}
# Every source string this fitter will ever need to read: wu_icao_history (needed both
# as the WU_ICAO-tier primary and as the OGIMET_METAR-tier fallback) plus each city's
# CURRENT non-WU primary (the physical station -- and hence the Ogimet/HKO source
# string -- does not change across a settlement-authority era switch in this schema).
_ALT_SOURCES: frozenset[str] = frozenset(
    EXPECTED_SOURCE_BY_CITY[name]
    for name, tier in TIER_SCHEDULE.items()
    if tier is not Tier.WU_ICAO
)


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _query(path: str, sql: str, args: tuple = ()) -> list[dict]:
    conn = _ro(path)
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _era_source_and_floor(city: str, day: str) -> tuple[str, int] | None:
    """The settlement-station ledger and hour-floor effective for (city, day).

    Era-aware via ``tier_for_city`` / ``expected_source_for_city`` (config/cities.json
    ``settlement_source_type`` + effective-date/previous-type fields) -- the same
    routing the observation-instants writer and backfill driver use. ``None`` for an
    unknown city (defensive; every configured city has a tier by construction).
    """

    try:
        tier = tier_for_city(city, target_date=day)
        source = expected_source_for_city(city, target_date=day)
    except UnsupportedTierError:
        return None
    return source, _FLOOR_BY_TIER[tier]


def _hourly_days(world_db: str) -> tuple[dict, dict, dict]:
    """{(city, date): {hour: (hi, lo)}}, {city: unit}, {(city, date): source used}."""

    rows = _query(
        world_db,
        """
        SELECT city, target_date, source, station_id, local_hour, running_max,
               running_min, temp_current, temp_unit
        FROM observation_instants
        WHERE target_date >= ? AND local_hour IS NOT NULL
          AND (source = ? OR source IN (%s))
        ORDER BY city, target_date, source, local_hour
        """
        % ",".join("?" * len(_ALT_SOURCES)),
        (HISTORY_START, WU_SOURCE, *_ALT_SOURCES),
    )
    # Buckets are kept PER (city, day, source): the two ledgers are never merged into
    # one day's envelope, only one of them is selected whole below.
    buckets: dict = collections.defaultdict(dict)
    bucket_unit: dict = {}
    bucket_station: dict = collections.defaultdict(set)
    for row in rows:
        try:
            hour = int(round(float(row["local_hour"])))
        except (TypeError, ValueError):
            continue
        if not 0 <= hour <= 23:
            continue
        high = row["running_max"]
        low = row["running_min"]
        if high is None:
            high = row["temp_current"]
        if low is None:
            low = row["temp_current"]
        if high is None:
            continue
        if low is None:
            low = high
        key = (row["city"], row["target_date"], row["source"])
        bucket = buckets[key]
        if hour in bucket:
            # Duplicate hour: keep the extreme envelope, as the study does.
            bucket[hour] = (max(bucket[hour][0], high), min(bucket[hour][1], low))
        else:
            bucket[hour] = (high, low)
        bucket_unit[key] = str(row["temp_unit"] or "").strip().upper()
        if row["station_id"]:
            bucket_station[key].add(str(row["station_id"]).strip().upper())

    days = {(city, day) for city, day, _source in buckets}
    kept: dict = {}
    unit: dict = {}
    source_used: dict = {}
    for city, day in days:
        selection = _era_source_and_floor(city, day)
        if selection is None:
            continue
        primary_source, floor = selection
        chosen_key = None
        primary_key = (city, day, primary_source)
        primary_bucket = buckets.get(primary_key)
        if primary_bucket is not None and len(primary_bucket) >= floor:
            chosen_key = primary_key
        elif primary_source != WU_SOURCE:
            # OGIMET_METAR (or, defensively, any other non-WU) era day short of its
            # floor: fall back to this city's WU history for the SAME physical
            # station, never a different one. HKO_NATIVE has no such alternate
            # ledger, so this only ever fires for OGIMET_METAR-era days.
            wu_key = (city, day, WU_SOURCE)
            wu_bucket = buckets.get(wu_key)
            city_icao = str(cities_by_name[city].wu_station or "").strip().upper()
            if (
                wu_bucket is not None
                and len(wu_bucket) >= MIN_HOURS_WU
                and city_icao
                and bucket_station.get(wu_key, {city_icao}) == {city_icao}
            ):
                chosen_key = wu_key
        if chosen_key is None:
            continue
        kept[(city, day)] = buckets[chosen_key]
        unit[city] = bucket_unit[chosen_key]
        source_used[(city, day)] = chosen_key[2]
    return kept, unit, source_used


def _verified_settlements(forecast_db: str) -> dict:
    """{(city, date, metric): (value, unit)} for VERIFIED settlements."""

    out = {}
    for row in _query(
        forecast_db,
        """
        SELECT city, target_date, temperature_metric, settlement_value, settlement_unit
        FROM settlement_outcomes
        WHERE authority = 'VERIFIED' AND settlement_value IS NOT NULL
          AND temperature_metric IN ('high', 'low') AND target_date >= ?
        """,
        (HISTORY_START,),
    ):
        out[(row["city"], row["target_date"], row["temperature_metric"])] = (
            float(row["settlement_value"]),
            str(row["settlement_unit"] or "").strip().upper(),
        )
    return out


def _nwp_centers(forecast_db: str) -> dict:
    """{metric: {(city, date): center_c}} — median over models of each model's latest
    ``single_runs`` cycle at lead_days <= 1, i.e. the day-of NWP daily-extreme center."""

    centers: dict = {}
    for metric in ("high", "low"):
        latest: dict = collections.defaultdict(dict)
        for row in _query(
            forecast_db,
            """
            SELECT city, target_date, model, forecast_value_c, source_cycle_time
            FROM raw_model_forecasts
            WHERE metric = ? AND endpoint = 'single_runs' AND lead_days <= 1
              AND target_date >= ? AND forecast_value_c IS NOT NULL
            """,
            (metric, HISTORY_START),
        ):
            key = (row["city"], row["target_date"])
            model = row["model"]
            cycle = row["source_cycle_time"]
            current = latest[key].get(model)
            if current is None or cycle > current[0]:
                latest[key][model] = (cycle, float(row["forecast_value_c"]))
        centers[metric] = {
            key: statistics.median([value for _, value in models.values()])
            for key, models in latest.items()
            if models
        }
    return centers


def _anchor_hours(records: list[dict], metric: str) -> dict:
    """Median first-attainment hour of the day's extreme, per city."""

    by_day: dict = collections.defaultdict(dict)
    for record in records:
        if record["metric"] != metric:
            continue
        by_day[(record["city"], record["date"])][record["h"]] = record["cum"]
    hours: dict = collections.defaultdict(list)
    for (city, _day), curve in by_day.items():
        final = max(curve.values()) if metric == "high" else min(curve.values())
        for hour in sorted(curve):
            attained = (
                curve[hour] >= final - 1e-9
                if metric == "high"
                else curve[hour] <= final + 1e-9
            )
            if attained:
                hours[city].append(hour)
                break
    return {city: statistics.median(values) for city, values in hours.items() if values}


def build_records(
    world_db: str, forecast_db: str
) -> tuple[list[dict], dict, dict]:
    """Station-hour residual records, the per-city settlement unit, and the ledger
    source used per (city, day) (diagnostic only -- not part of the artifact)."""

    days, unit, source_used = _hourly_days(world_db)
    settlements = _verified_settlements(forecast_db)
    records: list[dict] = []
    for (city, day), bucket in days.items():
        city_unit = unit.get(city, "")
        hours = sorted(bucket)
        running_high = -math.inf
        running_low = math.inf
        cum_high: dict = {}
        cum_low: dict = {}
        for hour in hours:
            high, low = bucket[hour]
            running_high = max(running_high, high)
            running_low = min(running_low, low)
            cum_high[hour] = running_high
            cum_low[hour] = running_low
        for metric, cumulative, day_extreme in (
            ("high", cum_high, running_high),
            ("low", cum_low, running_low),
        ):
            final = day_extreme
            settled = settlements.get((city, day, metric))
            if settled is not None and settled[1] == city_unit:
                final = settled[0]
            # Grid the label onto the settlement integer scale with the SAME WMO
            # half-up law the server applies to its own anchor. VERIFIED settlement
            # values are already integers (this is a no-op rounding then); the
            # day-extreme fallback is native-precision (tenths, for an ogimet-sourced
            # day) and genuinely needs it.
            final_grid = round_wmo_half_up_value(final)
            for hour in hours:
                cum_grid = round_wmo_half_up_value(cumulative[hour])
                residual = (
                    final_grid - cum_grid
                    if metric == "high"
                    else cum_grid - final_grid
                )
                records.append(
                    {
                        "city": city,
                        "date": day,
                        "metric": metric,
                        "h": hour,
                        "cum": cumulative[hour],
                        "D": residual,
                        "unit": city_unit,
                    }
                )
    return records, unit, source_used


def build_artifact(
    records: list[dict],
    *,
    unit: dict,
    nwp: dict,
    fit_date: str,
) -> dict:
    """Histogram counts per cell, from records strictly before ``fit_date``."""

    peak = _anchor_hours(records, "high")
    trough = _anchor_hours(records, "low")
    training = [record for record in records if record["date"] < fit_date]
    pooled: dict = collections.defaultdict(lambda: [0] * (J_MAX + 1))
    gap: dict = collections.defaultdict(lambda: [0] * (J_MAX + 1))
    city_cells: dict = collections.defaultdict(lambda: [0] * (J_MAX + 1))
    for record in training:
        metric = record["metric"]
        city = record["city"]
        anchor = peak.get(city) if metric == "high" else trough.get(city)
        if anchor is None:
            continue
        k = int(round(anchor - record["h"]))
        # record["D"] is already gridded to an integer (build_records); clamp only.
        j = min(J_MAX, max(0, int(record["D"])))
        pooled[f"{metric}|{k}"][j] += 1
        city_cells[f"{metric}|{city}|{k}"][j] += 1
        center = nwp[metric].get((city, record["date"]))
        if center is None:
            continue
        if record["unit"] == "F":
            center = center * 9.0 / 5.0 + 32.0
        offset = (
            center - record["cum"] if metric == "high" else record["cum"] - center
        )
        band = gap_band_index(offset)
        if band is not None:
            gap[f"{metric}|{k}|{band}"][j] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "fit_date": fit_date,
        "fitted_at_utc": datetime.now(timezone.utc).isoformat(),
        "history_start": HISTORY_START,
        "j_max": J_MAX,
        "gap_band_edges": [
            [None if math.isinf(low) else low, None if math.isinf(high) else high]
            for low, high in GAP_BAND_EDGES
        ],
        "record_counts": {
            "total": len(records),
            "training": len(training),
            "cities": len(unit),
            "pooled_cells": len(pooled),
            "gap_cells": len(gap),
            "city_cells": len(city_cells),
        },
        "peak_hours": peak,
        "trough_hours": trough,
        "unit": unit,
        "pooled": dict(pooled),
        "gap": dict(gap),
        "city": dict(city_cells),
    }


def _write_artifact_atomic(artifact: dict, out_path: str) -> None:
    """Atomic write (tmp + replace) -- the daemon-scheduled refit (src/ingest_main.py
    ``_day0_diurnal_residual_refit_tick``) can be killed mid-run (timeout, restart); a
    half-written file must never replace the live artifact the loader reads
    (src/calibration/day0_diurnal_residual.py). Mirrors scripts/reconcile_realized_fees.py
    ``_write_artifact``, the sibling daily-refit artifact's write."""
    tmp_path = f"{out_path}.tmp"
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, separators=(",", ":"), sort_keys=True)
    os.replace(tmp_path, out_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-db", default=DEFAULT_WORLD_DB)
    parser.add_argument("--forecast-db", default=DEFAULT_FORECAST_DB)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--fit-date",
        default=None,
        help="Exclude records on/after this date (default: today UTC). Also the "
        "first date the artifact may serve.",
    )
    args = parser.parse_args()
    fit_date = args.fit_date or datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(fit_date)

    records, unit, _source_used = build_records(args.world_db, args.forecast_db)
    nwp = _nwp_centers(args.forecast_db)
    artifact = build_artifact(records, unit=unit, nwp=nwp, fit_date=fit_date)
    _write_artifact_atomic(artifact, args.out)
    counts = artifact["record_counts"]
    print(
        f"wrote {args.out} fit_date={fit_date} "
        f"records={counts['total']} training={counts['training']} "
        f"cities={counts['cities']} pooled={counts['pooled_cells']} "
        f"gap={counts['gap_cells']} city={counts['city_cells']} "
        f"bytes={os.path.getsize(args.out)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
