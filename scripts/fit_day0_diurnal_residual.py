#!/usr/bin/env python3
# Created: 2026-09-04
# Last reused or audited: 2026-09-29
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
# 2026-09-29: the artifact now carries the in-q mixture weights (docs/authority/
#   replacement_final_form_2026_06_09.md §1e "Day0 diurnal-residual mixture"). The
#   residual grid is the CITY's settlement rounding (SettlementSemantics.round_single;
#   Hong Kong truncates), pooled cells are keyed by settlement unit, and the gap tilt is
#   removed (the validated mixture serves the ungapped pmf).
"""Fit the Day0 diurnal-residual artifact ``state/day0_diurnal_residual.json``.

WHAT IS FITTED. (1) The empirical distribution of D = final_extreme - running_extreme
by (metric, settlement unit, k = hours-to-peak) and by (metric, city, k), as raw COUNTS
per cell; the Empirical-Bayes shrink is applied at serve time. (2) The mixture weight w
per (metric, k bucket): maximum settled likelihood of the served operator
(``DiurnalResidualNowcast.mixture``) over the BASE (unmixed) Day0 posteriors actually
served in the trailing window.

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

GRID PRECISION. Every residual is computed on the CITY's settlement grid
(``SettlementSemantics.for_city(city).round_single``): j = grid(final) - grid(running)
for HIGH, grid(running) - grid(final) for LOW. The server anchors A = grid(running) with
the same function, so fit and serve read the same cell -- including Hong Kong, whose
truncating grid disagrees with WMO half-up at every x.5..x.99.

WALK-FORWARD. For ``fit_date`` T the served counts come from station-days before T-1
(complete in every timezone before any local-T decision). The weights are fitted on
settled posteriors dated [T-31, T-2] scored against counts from station-days before
T-32, so no weight row's own station-day is inside the pmf that scores it. Posteriors are
read through their persisted ``day0_diurnal_base_q`` (the q before this mixture), so a
refit never fits on its own output.

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
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.day0_diurnal_residual import (  # noqa: E402
    J_MAX,
    MIN_WEIGHT_ROWS,
    SCHEMA_VERSION,
    DiurnalResidualNowcast,
    weight_key,
)
from src.config import cities_by_name  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
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


_DAY0_POSTERIOR_SQL = """
SELECT posterior_id, city, target_date, temperature_metric, computed_at
FROM forecast_posteriors
WHERE runtime_layer = 'live' AND target_date BETWEEN ? AND ?
"""


def _native(value_c: float | None, unit: str) -> float | None:
    if value_c is None:
        return None
    return float(value_c) if unit == "C" else round(float(value_c) * 9.0 / 5.0 + 32.0, 6)


def settled_base_posteriors(forecast_db: str, *, fit_date: str) -> list[dict]:
    """The latest served Day0 posterior per (city, date, metric, local hour), settled.

    Only posteriors computed on the target's own local day with an observed extreme
    are Day0 rows. q is the persisted BASE simplex (``day0_diurnal_base_q`` when the
    mixture ran, else ``q_json``); bounds and the running extreme are native degrees.
    """

    fit = date.fromisoformat(fit_date)
    start = (fit - timedelta(days=WEIGHT_WINDOW_DAYS + 1)).isoformat()
    end = (fit - timedelta(days=2)).isoformat()
    settled = _verified_settlements(forecast_db)
    conn = _ro(forecast_db)
    try:
        latest: dict = {}
        for pid, city_name, target, metric, computed_at in conn.execute(
            _DAY0_POSTERIOR_SQL, (start, end)
        ):
            city = cities_by_name.get(city_name)
            if city is None or (city_name, target, metric) not in settled or not computed_at:
                continue
            local = datetime.fromisoformat(str(computed_at).replace("Z", "+00:00")).astimezone(
                ZoneInfo(city.timezone)
            )
            if local.date().isoformat() != target:
                continue
            key = (city_name, target, metric, local.hour)
            if key not in latest or computed_at > latest[key][1]:
                latest[key] = (pid, computed_at, local.hour + local.minute / 60.0)
        ids = sorted(value[0] for value in latest.values())
        fields: dict = {}
        for offset in range(0, len(ids), 500):
            chunk = ids[offset : offset + 500]
            for row in conn.execute(
                "SELECT posterior_id, q_json, "
                "json_extract(provenance_json, '$.day0_diurnal_base_q'), "
                "json_extract(provenance_json, '$.bin_topology'), "
                "json_extract(provenance_json, '$.day0_provisional_observation.observed_extreme_c'), "
                "json_extract(provenance_json, '$.day0_conditioning.observed_extreme_c') "
                "FROM forecast_posteriors WHERE posterior_id IN (%s)" % ",".join("?" * len(chunk)),
                chunk,
            ):
                fields[row[0]] = row[1:]
    finally:
        conn.close()
    out: list[dict] = []
    for (city_name, target, metric, _hour), (pid, _at, local_hour) in latest.items():
        q_json, base_json, topology_json, provisional_c, conditioned_c = fields.get(
            pid, (None,) * 5
        )
        observed_c = provisional_c if provisional_c is not None else conditioned_c
        if q_json is None or topology_json is None or observed_c is None:
            continue
        unit = str(cities_by_name[city_name].settlement_unit).strip().upper()
        value, settled_unit = settled[(city_name, target, metric)]
        if settled_unit and settled_unit != unit:
            continue
        q = json.loads(base_json or q_json)
        topology = json.loads(topology_json)
        try:
            probabilities = [float(q[str(item["bin_id"])]) for item in topology]
        except (KeyError, TypeError, ValueError):
            continue
        bounds = [
            (_native(item.get("lower_c"), unit), _native(item.get("upper_c"), unit))
            for item in topology
        ]
        winners = [
            index
            for index, (low, high) in enumerate(bounds)
            if (low is None or value >= low - 1e-6) and (high is None or value <= high + 1e-6)
        ]
        if len(winners) != 1:
            continue
        out.append(
            {
                "pid": pid,
                "city": city_name,
                "date": target,
                "metric": metric,
                "unit": unit,
                "local_hour": local_hour,
                "running": _native(float(observed_c), unit),
                "bounds": bounds,
                "q": probabilities,
                "winner": winners[0],
            }
        )
    return out


def _city_grid(city: str):
    """The city's settlement rounding (Hong Kong truncates; every other city is WMO
    half-up). The fitter's residual grid and the server's anchor share it."""

    return SettlementSemantics.for_city(cities_by_name[city]).round_single


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
        grid = _city_grid(city)
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
            # Grid the label and every running extreme with the CITY's settlement
            # rounding -- the same law the server uses to place its anchor A.
            final_grid = grid(final)
            for hour in hours:
                cum_grid = grid(cumulative[hour])
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


def build_counts(records: list[dict], *, unit: dict, fit_date: str) -> dict:
    """Histogram counts from station-days strictly before ``fit_date``.

    Anchor hours are also computed from those days only, so nothing on or after the
    fit date reaches the served pmf.
    """

    training = [record for record in records if record["date"] < fit_date]
    peak = _anchor_hours(training, "high")
    trough = _anchor_hours(training, "low")
    pooled: dict = collections.defaultdict(lambda: [0] * (J_MAX + 1))
    city_cells: dict = collections.defaultdict(lambda: [0] * (J_MAX + 1))
    for record in training:
        metric = record["metric"]
        city = record["city"]
        anchor = peak.get(city) if metric == "high" else trough.get(city)
        if anchor is None:
            continue
        k = int(round(anchor - record["h"]))
        j = min(J_MAX, max(0, int(record["D"])))
        pooled[f"{metric}|{record['unit']}|{k}"][j] += 1
        city_cells[f"{metric}|{city}|{k}"][j] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "fit_date": fit_date,
        "history_start": HISTORY_START,
        "j_max": J_MAX,
        "record_counts": {
            "total": len(records),
            "training": len(training),
            "cities": len(unit),
            "pooled_cells": len(pooled),
            "city_cells": len(city_cells),
        },
        "peak_hours": peak,
        "trough_hours": trough,
        "unit": unit,
        "pooled": dict(pooled),
        "city": dict(city_cells),
        "weights": {},
    }


WEIGHT_WINDOW_DAYS = 30
EPS = 1e-6


def weight_rows(
    posteriors: list[dict], *, counts: dict
) -> dict[str, list[tuple[float, float]]]:
    """(base q of the winner, pi of the winner) per weight cell.

    ``posteriors`` are settled served Day0 posteriors carrying their BASE simplex
    (never an already-mixed one). ``counts`` must predate every posterior's own
    station-day. Each row is the live-bin winner probability pair the operator mixes;
    a winner in a dead bin carries no information about w and is skipped.
    """

    nowcast = DiurnalResidualNowcast(counts)
    cells: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
    for row in posteriors:
        city = cities_by_name.get(row["city"])
        if city is None:
            continue
        mixture = nowcast.mixture(
            city=row["city"],
            metric=row["metric"],
            unit=row["unit"],
            local_hour=row["local_hour"],
            running_extreme=row["running"],
            bin_bounds=row["bounds"],
            round_to_grid=_city_grid(row["city"]),
            weight=1.0,
        )
        if mixture is None:
            continue
        winner = row["winner"]
        if mixture.dead[winner]:
            continue
        base = [max(0.0, float(value)) for value in row["q"]]
        total = sum(base)
        if total <= 0.0:
            continue
        base = [value / total for value in base]
        live_mass = 1.0 - sum(v for v, dead in zip(base, mixture.dead) if dead)
        cells[weight_key(row["metric"], mixture.k)].append(
            (base[winner], max(0.0, live_mass) * mixture.pi[winner])
        )
    return cells


def fit_weight(pairs: list[tuple[float, float]]) -> float:
    """argmax_w mean log((1 - w) a + w b) on [0, 1] (concave; golden-section)."""

    def loss(w: float) -> float:
        return -sum(math.log(max((1.0 - w) * a + w * b, EPS)) for a, b in pairs)

    low, high = 0.0, 1.0
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    x1 = high - ratio * (high - low)
    x2 = low + ratio * (high - low)
    f1, f2 = loss(x1), loss(x2)
    for _ in range(80):
        if f1 <= f2:
            high, x2, f2 = x2, x1, f1
            x1 = high - ratio * (high - low)
            f1 = loss(x1)
        else:
            low, x1, f1 = x1, x2, f2
            x2 = low + ratio * (high - low)
            f2 = loss(x2)
    best = (low + high) / 2.0
    return min((0.0, 1.0, best), key=loss)


def build_artifact(
    records: list[dict], *, unit: dict, fit_date: str, posteriors: list[dict]
) -> dict:
    """Counts for serving on ``fit_date`` plus the fitted weight per cell.

    Weights are fit on settled posteriors dated [T-31, T-2] whose pmf comes from
    counts before T-32, so no weight row's own station-day is inside the pmf that
    scores it. A cell with fewer than MIN_WEIGHT_ROWS rows is not written (w = 0).
    """

    fit = date.fromisoformat(fit_date)
    serving = build_counts(records, unit=unit, fit_date=(fit - timedelta(days=1)).isoformat())
    serving["fit_date"] = fit_date
    lagged = build_counts(
        records, unit=unit, fit_date=(fit - timedelta(days=WEIGHT_WINDOW_DAYS + 2)).isoformat()
    )
    window_start = (fit - timedelta(days=WEIGHT_WINDOW_DAYS + 1)).isoformat()
    window_end = (fit - timedelta(days=2)).isoformat()
    window = [row for row in posteriors if window_start <= row["date"] <= window_end]
    weights = {}
    if window and lagged["pooled"]:
        for key, pairs in sorted(weight_rows(window, counts=lagged).items()):
            if len(pairs) >= MIN_WEIGHT_ROWS:
                weights[key] = {"w": fit_weight(pairs), "n": len(pairs)}
    serving["weights"] = weights
    serving["weight_window"] = [window_start, window_end]
    serving["fitted_at_utc"] = datetime.now(timezone.utc).isoformat()
    return serving


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
    posteriors = settled_base_posteriors(args.forecast_db, fit_date=fit_date)
    artifact = build_artifact(records, unit=unit, fit_date=fit_date, posteriors=posteriors)
    _write_artifact_atomic(artifact, args.out)
    counts = artifact["record_counts"]
    print(
        f"wrote {args.out} fit_date={fit_date} "
        f"records={counts['total']} training={counts['training']} "
        f"cities={counts['cities']} pooled={counts['pooled_cells']} "
        f"city={counts['city_cells']} weights={sorted(artifact['weights'])} "
        f"posteriors={len(posteriors)} bytes={os.path.getsize(args.out)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
