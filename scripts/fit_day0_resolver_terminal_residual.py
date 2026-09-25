#!/usr/bin/env python3
# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: resolver-graded Day0 observation model (external review
#   2026-09-24, design decision items 1, 2, 4 and 7).
"""Fit and evaluate the resolver-graded Day0 terminal residual artifact.

LABELS (``src.calibration.day0_resolver_terminal_residual``):
- NOAA WRH, WU-history and HKO-daily settlements from canonical
  ``settlement_outcomes`` (VERIFIED, value present, learning-final A8 resolution
  state, unit from ``settlement_unit``); a label is available at
  ``max(settled_at, recorded_at)``.  Anything else is censored.
- METAR stations: running extreme from AWC and Ogimet renderings in
  ``observation_prints``.  One report identity (observation time) counts once;
  the latest rendering possessed (``fetched_at_utc``) by the checkpoint is
  its value.  Values are converted to the contract unit exactly
  (C -> F = 9/5 C + 32).
- Hong Kong: ``hko_hourly_accumulator`` running max/min possessed by the
  checkpoint (``imported_at``), floored by the HKO contract.
- ``A`` uses ``SettlementSemantics.for_city`` rounding.  One checkpoint per
  station-day per phase: the last local hourly checkpoint with evidence.
- Remaining-gap class comes from the latest carrier posterior at most three
  hours before the checkpoint; otherwise ``gap_missing``.

WALK-FORWARD. The artifact written to ``--out`` uses only labels available
before ``--as-of``.  The evaluation refits once per UTC day D, with cutoff
D 00:00Z, and scores posteriors computed on D.  The station precision stratum
(tenth vs whole-degree, from the AWC T-group share) is itself recomputed at
every cutoff from reports published AND possessed (``fetched_at_utc``) before
it, and every label is re-stratified with that cutoff's classes, so no report
possessed after the cutoff can decide a stratum trained at the cutoff.

EVALUATION. Stored carrier posteriors (AWC/Ogimet/HKO provisional carriers)
are rebuilt on a unit settlement grid from their persisted inputs:
- old: the persisted survival mixture ``s_old * Law(max(b, X)) + (1 - s_old) * Law(X)``;
- new: ``compose_resolver_terminal_distribution`` with the walk-forward ``s``/``G-``.
Scored events: the bin holding ``A`` and terminal non-violation.  Metrics are
log loss and ``E[q - I]``, averaged within city-day first, with a one-sided
95% upper bound across city-days.

READ-ONLY on both databases (``mode=ro`` + ``PRAGMA query_only``).  Writes only
``--out`` and ``--eval-out``.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.calibration.day0_resolver_terminal_residual import (  # noqa: E402
    FAILURE_STEPS,
    HKO_CHANNEL,
    HKO_STATION,
    RESOLVER_BY_SOURCE_TYPE,
    ReportRendering,
    ResolverTerminalArtifact,
    UNKNOWN_CHANNEL,
    TerminalLabel,
    contract_settlement_value,
    fit_resolver_terminal_residual,
    gap_category,
    phase_of_local_hour,
    resolver_product_from_settlement_source,
    running_extreme,
    station_day_labels,
    to_contract_unit,
)
from src.config import cities_by_name, settlement_source_type_for_city  # noqa: E402
from src.contracts.settlement_axes import (  # noqa: E402
    is_learning_eligible_resolution_state,
    settlement_resolution_state_from_row,
)
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data.day0_fast_obs import metar_observation_time_from_raw  # noqa: E402
from src.data.day0_hourly_vectors import build_day0_remaining_probability_carrier  # noqa: E402
from src.forecast.day0_terminal_distribution import (  # noqa: E402
    compose_resolver_terminal_distribution,
    nesting_map,
    unit_settlement_grid,
)
from src.signal.ensemble_signal import sigma_instrument_for_city  # noqa: E402

AWC = "aviationweather_metar"
T_GROUP = re.compile(r"\sT[01]\d{3}[01]\d{3}\b")
CARRIER_SHAPES = (
    "day0_remaining_shared_carrier_v1",
    "day0_remaining_shared_carrier_v2",
    "day0_remaining_shared_carrier_v3",
    "fused_day0_fast_residual_likelihood",
)
MEMBER_LOOKBACK = timedelta(hours=3)
# Commit 6d8f02411 (normalize exact OGIMET Fahrenheit confirmations) committed
# 2026-09-21 20:13 -05:00.
COMMIT_6D8F = "2026-09-22T01:13:03+00:00"
EPS = 1e-6


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _utc(raw: object, *, naive_is_utc: bool = False) -> datetime | None:
    """Aware UTC instant; a naive value is UTC only where SQLite stamped it."""
    try:
        parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc) if naive_is_utc else None
    return parsed.astimezone(timezone.utc)


def _native(value: float, from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return float(value)
    if from_unit == "C" and to_unit == "F":
        return to_contract_unit(value, "F")
    if from_unit == "F" and to_unit == "C":
        return (float(value) - 32.0) * 5.0 / 9.0
    raise ValueError(f"unit {from_unit}->{to_unit}")


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------


def read_settlements(forecasts_db: str, start: str) -> list[dict]:
    """VERIFIED, learning-final labels from canonical ``settlement_outcomes``.

    Semantics follow ``src.data.current_settlement_history``: unit from
    ``settlement_unit``, eligibility from the A8 resolution state, and a label is
    known only once both ``settled_at`` and ``recorded_at`` have passed.
    """

    conn = _ro(forecasts_db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT city, target_date, temperature_metric, winning_bin, settlement_value,
                   settlement_source, settled_at, recorded_at, authority,
                   settlement_unit, outcome_type, resolution_state
              FROM settlement_outcomes
             WHERE authority = 'VERIFIED' AND settlement_value IS NOT NULL
               AND temperature_metric IN ('high', 'low') AND target_date >= ?
            """,
            (start,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        try:
            state = settlement_resolution_state_from_row(dict(row))
        except (TypeError, ValueError):
            continue
        settled, recorded = _utc(row["settled_at"]), _utc(row["recorded_at"], naive_is_utc=True)
        resolver = resolver_product_from_settlement_source(row["settlement_source"])
        if (
            not is_learning_eligible_resolution_state(state)
            or resolver is None
            or settled is None
            or recorded is None
        ):
            continue
        city, target, metric = row["city"], row["target_date"], row["temperature_metric"]
        value, unit, available = row["settlement_value"], row["settlement_unit"], max(settled, recorded)
        out.append(
            {
                "city": city,
                "target_date": str(target)[:10],
                "metric": metric,
                "value": float(value),
                "unit": str(unit or "").upper(),
                "resolver": resolver,
                "available_at": available,
            }
        )
    return out


def read_metar_renderings(world_db: str, start: str, end: datetime) -> tuple[dict, dict]:
    """{station: [ReportRendering (contract unit)]}, {station: [TenthEvidence]}.

    Precision evidence keeps each AWC report's publication and possession
    clocks, so a cutoff can classify a station from what was possessed before it.
    """

    stations = {
        str(city.wu_station).upper(): city
        for city in cities_by_name.values()
        if getattr(city, "wu_station", None)
    }
    conn = _ro(world_db)
    try:
        rows = conn.execute(
            """
            SELECT station_id, source_channel, publish_ts_utc, value_native, unit,
                   fetched_at_utc, raw_report
              FROM observation_prints
             WHERE publish_ts_utc >= ? AND julianday(publish_ts_utc) < julianday(?)
               AND (source_channel = ? OR source_channel LIKE 'ogimet_metar_%')
            """,
            (start, end.isoformat(), AWC),
        ).fetchall()
    finally:
        conn.close()
    renderings: dict[str, list[ReportRendering]] = collections.defaultdict(list)
    tenth: dict[str, list[TenthEvidence]] = collections.defaultdict(list)
    for station_raw, channel, published_raw, value, unit, fetched_raw, raw in rows:
        station = str(station_raw or "").upper()
        city = stations.get(station)
        if city is None or value is None:
            continue
        channel = str(channel)
        if channel != AWC and channel != f"ogimet_metar_{station.lower()}":
            continue
        published, fetched = _utc(published_raw), _utc(fetched_raw)
        unit = str(unit or "").upper()
        if published is None or fetched is None or unit not in {"C", "F"}:
            continue
        if channel == AWC:
            if unit != "C":
                continue
            observed = metar_observation_time_from_raw(str(raw or ""), published_at=published)
            tenth[station].append(
                TenthEvidence(published, fetched, bool(T_GROUP.search(str(raw or ""))))
            )
        else:
            observed = published
        if observed is None:
            continue
        renderings[station].append(
            ReportRendering(
                report_time_utc=observed.astimezone(timezone.utc),
                possessed_at_utc=fetched,
                value_native=_native(float(value), unit, city.settlement_unit),
            )
        )
    return renderings, tenth


def read_hko_accumulator(world_db: str, start: str) -> dict[str, list[tuple]]:
    """{target_date: sorted [(utc_timestamp, imported_at, running_max, running_min)]}."""

    conn = _ro(world_db)
    try:
        rows = conn.execute(
            """
            SELECT target_date, utc_timestamp, imported_at, running_max, running_min
              FROM observation_instants
             WHERE city = 'Hong Kong' AND source = 'hko_hourly_accumulator'
               AND target_date >= ? AND COALESCE(causality_status, 'OK') = 'OK'
            """,
            (start,),
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, list[tuple]] = collections.defaultdict(list)
    for target, ts, imported, high, low in rows:
        observed, possessed = _utc(ts), _utc(imported)
        if observed is None or possessed is None:
            continue
        out[str(target)[:10]].append((observed, possessed, high, low))
    for rows_for_day in out.values():
        rows_for_day.sort()
    return out


def read_carrier_posteriors(forecasts_db: str, start: str) -> list[dict]:
    conn = _ro(forecasts_db)
    placeholders = ",".join("?" * len(CARRIER_SHAPES))
    try:
        rows = conn.execute(
            f"""
            SELECT posterior_id, city, target_date, temperature_metric, computed_at,
                   json_extract(provenance_json, '$.day0_provisional_observation.observed_extreme_c'),
                   json_extract(provenance_json, '$.day0_provisional_observation.source'),
                   json_extract(provenance_json, '$.day0_preliminary_report_survival_likelihood.boundary_survival_probability'),
                   json_extract(provenance_json, '$.day0_remaining_carrier_operator'),
                   json_extract(provenance_json, '$.day0_remaining_carrier_future_extremes_c'),
                   json_extract(provenance_json, '$.day0_remaining_carrier_final_extremes_c'),
                   json_extract(provenance_json, '$.day0_remaining_carrier_path_error_sigma_c'),
                   json_extract(provenance_json, '$.day0_remaining_carrier_q'),
                   json_extract(provenance_json, '$.bin_topology'),
                   q_json
              FROM forecast_posteriors INDEXED BY idx_forecast_posteriors_center_debias
             WHERE q_shape IN ({placeholders}) AND computed_at >= ?
            """,
            (*CARRIER_SHAPES, start),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        (pid, city, target, metric, computed, observed, source, survival, operator,
         future, final, path_sigma, carrier_q, topology, q_json) = row
        computed_at = _utc(computed)
        if None in (computed_at, observed, source, survival, operator, future, path_sigma, carrier_q, topology):
            continue
        out.append(
            {
                "posterior_id": int(pid),
                "city": city,
                "target_date": str(target)[:10],
                "metric": metric,
                "computed_at": computed_at,
                "observed_c": float(observed),
                "source": str(source),
                "survival": float(survival),
                "operator": str(operator),
                "future_c": tuple(float(v) for v in json.loads(future)),
                "final_c": tuple(float(v) for v in json.loads(final or "[]")),
                "path_sigma_c": float(path_sigma),
                "carrier_q": tuple(float(v) for v in json.loads(carrier_q)),
                "topology": [(b["lower_c"], b["upper_c"]) for b in json.loads(topology)],
                "bin_ids": [str(b["bin_id"]) for b in json.loads(topology)],
                "served_q": json.loads(q_json),
            }
        )
    return out


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TenthEvidence:
    """One AWC report: whether it carried a T-group, and when it was published/possessed."""

    published_utc: datetime
    possessed_utc: datetime
    tenth: bool


def channel_classes_at(
    evidence: dict[str, list[TenthEvidence]], cutoff: datetime
) -> dict[str, str]:
    """Station precision class from reports published AND possessed before ``cutoff``.

    A station with no such report is unclassified; its labels and serving cell
    both fall to ``UNKNOWN_CHANNEL``.
    """

    out: dict[str, str] = {}
    for station, rows in evidence.items():
        seen = [row.tenth for row in rows if row.published_utc < cutoff and row.possessed_utc < cutoff]
        if seen:
            out[station] = "metar_tenth" if sum(seen) / len(seen) >= 0.5 else "metar_whole"
    return out


def labels_at(labels: list[TerminalLabel], station_channel: dict[str, str]) -> list[TerminalLabel]:
    """Stamp each METAR label with its station's precision class at one cutoff."""

    return [
        label
        if label.station == HKO_STATION
        else replace(label, channel_class=station_channel.get(label.station, UNKNOWN_CHANNEL))
        for label in labels
    ]


def fit_at(
    labels: list[TerminalLabel], evidence: dict[str, list[TenthEvidence]], cutoff: datetime
) -> dict[str, object]:
    """Walk-forward fit: labels, stratification and station classes all causal at ``cutoff``."""

    station_channel = channel_classes_at(evidence, cutoff)
    return fit_resolver_terminal_residual(
        labels_at(labels, station_channel), fit_cutoff_utc=cutoff, station_channel=station_channel
    )


def build_labels(
    settlements: list[dict],
    renderings: dict[str, list[ReportRendering]],
    hko: dict[str, list[tuple]],
    members_index: dict[tuple, tuple[list, list]],
) -> tuple[list[TerminalLabel], collections.Counter]:
    """METAR labels carry ``UNKNOWN_CHANNEL`` until ``labels_at`` stratifies them."""
    by_station_day: dict[tuple[str, date], list[ReportRendering]] = collections.defaultdict(list)
    stations = {
        str(city.wu_station).upper(): city
        for city in cities_by_name.values()
        if getattr(city, "wu_station", None)
    }
    for station, items in renderings.items():
        tz = ZoneInfo(stations[station].timezone)
        for item in items:
            by_station_day[(station, item.report_time_utc.astimezone(tz).date())].append(item)
    labels: list[TerminalLabel] = []
    skipped: collections.Counter = collections.Counter()
    for row in settlements:
        city = cities_by_name.get(row["city"])
        if city is None or row["unit"] != city.settlement_unit:
            skipped["city_or_unit"] += 1
            continue
        target = date.fromisoformat(row["target_date"])
        semantics = SettlementSemantics.for_city(city)
        metric = row["metric"]
        tz = ZoneInfo(city.timezone)
        day_start = datetime(target.year, target.month, target.day, tzinfo=tz).astimezone(timezone.utc)
        if row["resolver"] == "hko_daily":
            if city.settlement_source_type != "hko":
                skipped["hko_resolver_mismatch"] += 1
                continue
            station, channel = HKO_STATION, HKO_CHANNEL
            day_rows = hko.get(row["target_date"], [])
            column = 2 if metric == "high" else 3

            def running(at: datetime, _rows=day_rows, _column=column):
                usable = [r[_column] for r in _rows if r[0] <= at and r[1] <= at and r[_column] is not None]
                return None if not usable else float(usable[-1])
        else:
            station = str(city.wu_station or "").upper()
            channel = UNKNOWN_CHANNEL
            day_rows = by_station_day.get((station, target), [])

            def running(at: datetime, _rows=day_rows, _start=day_start, _metric=metric):
                return running_extreme(_rows, at=at, day_start_utc=_start, metric=_metric)

        key = (row["city"], row["target_date"], metric)

        def members(at: datetime, _key=key):
            times, values = members_index.get(_key, ([], []))
            index = bisect.bisect_right(times, at) - 1
            if index < 0 or at - times[index] > MEMBER_LOOKBACK:
                return None
            scale = [to_contract_unit(v, city.settlement_unit) for v in values[index]]
            return scale

        produced = station_day_labels(
            city=row["city"],
            station=station,
            target_date=target,
            timezone_name=city.timezone,
            metric=metric,
            resolver_product=row["resolver"],
            channel_class=channel,
            semantics=semantics,
            settled=row["value"],
            settled_available_at=row["available_at"],
            running_extreme_at=running,
            members_at=members,
        )
        if not produced:
            skipped["no_running_extreme"] += 1
        labels.extend(produced)
    return labels, skipped


def members_index_of(posteriors: list[dict]) -> dict[tuple, tuple[list, list]]:
    grouped: dict[tuple, list] = collections.defaultdict(list)
    for row in posteriors:
        grouped[(row["city"], row["target_date"], row["metric"])].append(
            (row["computed_at"], (*row["future_c"], *row["final_c"]))
        )
    out = {}
    for key, rows in grouped.items():
        rows.sort(key=lambda item: item[0])
        out[key] = ([r[0] for r in rows], [r[1] for r in rows])
    return out


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def evaluation_rows(posteriors: list[dict]) -> list[dict]:
    """Last carrier posterior per (city, date, metric, local hour)."""

    latest: dict[tuple, dict] = {}
    for row in posteriors:
        city = cities_by_name.get(row["city"])
        if city is None:
            continue
        hour = row["computed_at"].astimezone(ZoneInfo(city.timezone)).hour
        key = (row["city"], row["target_date"], row["metric"], hour)
        if key not in latest or row["computed_at"] > latest[key]["computed_at"]:
            latest[key] = row
    return list(latest.values())


def score_row(
    row: dict, artifact: ResolverTerminalArtifact, settled: float, center_shift=None
) -> dict | None:
    """Score one carrier posterior; ``center_shift`` is an optional
    ``RemainingBiasTable`` applied to BOTH old and new, exactly as the live carrier
    shifts the remaining-hourly members before the boundary."""
    city = cities_by_name[row["city"]]
    unit = city.settlement_unit
    semantics = SettlementSemantics.for_city(city)
    metric = row["metric"]
    boundary = to_contract_unit(row["observed_c"], unit)
    observed = contract_settlement_value(boundary, semantics)
    members = [to_contract_unit(v, unit) for v in row["future_c"]]
    finals = [to_contract_unit(v, unit) for v in row["final_c"]]
    scale = 1.0 if unit == "C" else 9.0 / 5.0
    native_bins = tuple(
        (
            None if low is None else float(round(to_contract_unit(low, unit))),
            None if high is None else float(round(to_contract_unit(high, unit))),
        )
        for low, high in row["topology"]
    )
    fine = unit_settlement_grid(native_bins, observed=observed, steps=FAILURE_STEPS)
    local = row["computed_at"].astimezone(ZoneInfo(city.timezone))
    shift_c = (
        0.0
        if center_shift is None
        else center_shift.shift(
            city=row["city"], metric=metric, local_hour=local.hour + local.minute / 60.0
        ).shift_c
    )
    common = dict(
        future_extremes_c=members,
        final_extreme_centers_c=finals,
        metric=metric,
        path_error_sigma_c=row["path_sigma_c"] * scale,
        instrument_sigma_c=float(sigma_instrument_for_city(city).to(unit).value),
        bin_bounds_c=fine,
        n_point=1,
        n_samples=2,
        identity_inputs={"city": row["city"], "unit": unit, "station_id": "evaluation"},
        settlement_semantics=semantics,
    )
    s_old = row["survival"]
    old_scenarios = ((boundary, s_old), (None, 1.0 - s_old))
    unshifted_old = np.asarray(
        build_day0_remaining_probability_carrier(boundary_scenarios=old_scenarios, **common)["q"]
    )
    shift = {"remaining_center_bias_native": shift_c * scale}
    old = np.asarray(
        build_day0_remaining_probability_carrier(boundary_scenarios=old_scenarios, **common, **shift)["q"]
    )
    template = build_day0_remaining_probability_carrier(
        boundary_scenarios=((boundary, 1.0),), **common, **shift
    )["q"]
    station = HKO_STATION if row["source"].startswith("hko_hourly_accumulator") else str(city.wu_station).upper()
    resolver = RESOLVER_BY_SOURCE_TYPE.get(settlement_source_type_for_city(city, row["target_date"]))
    local_hour = row["computed_at"].astimezone(ZoneInfo(city.timezone)).hour
    cell = (
        resolver,
        artifact.channel_for(station),
        unit,
        metric,
        phase_of_local_hour(local_hour),
        gap_category((*members, *finals), observed_settlement=observed, metric=metric),
        station,
    )
    terminal = artifact.input_for(cell)
    if resolver is None or terminal is None:
        return None
    new = compose_resolver_terminal_distribution(
        template=template,
        template_bins=fine,
        observed=observed,
        metric=metric,
        nonviolation_probability=terminal.nonviolation_probability,
        failure_magnitude=terminal.failure_magnitude(),
        bins=fine,
    )
    fidelity = float(
        np.max(np.abs(nesting_map(fine, native_bins) @ unshifted_old - np.asarray(row["carrier_q"])))
    )
    high = metric == "high"
    nonviolation = np.asarray(
        [
            (low is not None and low >= observed) if high else (upper is not None and upper <= observed)
            for low, upper in fine
        ]
    )
    home = [
        index
        for index, (low, upper) in enumerate(native_bins)
        if (low is None or observed >= low) and (upper is None or observed <= upper)
    ]
    if len(home) != 1:
        return None
    in_home = nesting_map(fine, native_bins)[home[0]].astype(bool)
    low_h, high_h = native_bins[home[0]]
    served = np.asarray([float(row["served_q"][bin_id]) for bin_id in row["bin_ids"]])
    served_nv_bins = [
        (low is not None and low >= observed) if high else (upper is not None and upper <= observed)
        for low, upper in native_bins
    ]
    exact_nv_split = (low_h == observed) if high else (high_h == observed)
    return {
        "city": row["city"],
        "computed_at": row["computed_at"].isoformat(),
        "bin_served": float(served[home[0]]),
        "nv_served": float(served[np.asarray(served_nv_bins)].sum()) if exact_nv_split else None,
        "target_date": row["target_date"],
        "metric": metric,
        "unit": unit,
        "resolver": resolver,
        "fidelity": fidelity,
        "operator": row["operator"],
        "center_shift_c": shift_c,
        "s_old": s_old,
        "s_new": terminal.nonviolation_probability,
        "nv_old": float(old[nonviolation].sum()),
        "nv_new": float(new[nonviolation].sum()),
        "bin_old": float(old[in_home].sum()),
        "bin_new": float(new[in_home].sum()),
        "nv_hit": (settled >= observed) if high else (settled <= observed),
        "bin_hit": (low_h is None or settled >= low_h) and (high_h is None or settled <= high_h),
    }


def _log_loss(q: float, hit: bool) -> float:
    q = min(max(q, EPS), 1.0 - EPS)
    return -math.log(q if hit else 1.0 - q)


def summarize(scored: list[dict], select) -> dict:
    clusters: dict[tuple, list[dict]] = collections.defaultdict(list)
    for row in scored:
        if select(row):
            clusters[(row["city"], row["target_date"], row["metric"])].append(row)
    if not clusters:
        return {"clusters": 0}
    out: dict[str, object] = {"clusters": len(clusters), "rows": sum(map(len, clusters.values()))}
    for event in ("bin", "nv"):
        for version in ("served", "old", "new"):
            usable = {
                key: [r for r in rows if r.get(f"{event}_{version}") is not None]
                for key, rows in clusters.items()
            }
            usable = {key: rows for key, rows in usable.items() if rows}
            out[f"{event}_{version}_clusters"] = len(usable)
            if not usable:
                continue
            per_loss, per_over, per_q = [], [], []
            for rows in usable.values():
                per_loss.append(np.mean([_log_loss(r[f"{event}_{version}"], r[f"{event}_hit"]) for r in rows]))
                per_over.append(np.mean([r[f"{event}_{version}"] - float(r[f"{event}_hit"]) for r in rows]))
                per_q.append(np.mean([r[f"{event}_{version}"] for r in rows]))
            n = len(per_over)
            sd = float(np.std(per_over, ddof=1)) if n > 1 else float("nan")
            out[f"{event}_{version}_logloss"] = float(np.mean(per_loss))
            out[f"{event}_{version}_over"] = float(np.mean(per_over))
            out[f"{event}_{version}_over_ub95"] = float(np.mean(per_over) + 1.6448536 * sd / math.sqrt(n)) if n > 1 else float("nan")
            out[f"{event}_{version}_q"] = float(np.mean(per_q))
        out[f"{event}_rate"] = float(
            np.mean([np.mean([float(r[f"{event}_hit"]) for r in rows]) for rows in clusters.values()])
        )
    return out


STRATA = {
    "ALL": lambda r: True,
    "noaa_wrh C high": lambda r: (r["resolver"], r["unit"], r["metric"]) == ("noaa_wrh", "C", "high"),
    "noaa_wrh C low": lambda r: (r["resolver"], r["unit"], r["metric"]) == ("noaa_wrh", "C", "low"),
    "US F cities high": lambda r: (r["unit"], r["metric"]) == ("F", "high"),
    "US F cities low": lambda r: (r["unit"], r["metric"]) == ("F", "low"),
    "US F high before 6d8f02411": lambda r: (r["unit"], r["metric"]) == ("F", "high") and r["computed_at"] < COMMIT_6D8F,
    "US F high after 6d8f02411": lambda r: (r["unit"], r["metric"]) == ("F", "high") and r["computed_at"] >= COMMIT_6D8F,
    "US F low before 6d8f02411": lambda r: (r["unit"], r["metric"]) == ("F", "low") and r["computed_at"] < COMMIT_6D8F,
    "US F low after 6d8f02411": lambda r: (r["unit"], r["metric"]) == ("F", "low") and r["computed_at"] >= COMMIT_6D8F,
    "Taipei (wu_history) high": lambda r: (r["city"], r["metric"]) == ("Taipei", "high"),
    "Taipei (wu_history) low": lambda r: (r["city"], r["metric"]) == ("Taipei", "low"),
    "Hong Kong (hko_daily) high": lambda r: (r["city"], r["metric"]) == ("Hong Kong", "high"),
    "Hong Kong (hko_daily) low": lambda r: (r["city"], r["metric"]) == ("Hong Kong", "low"),
}


def label_summary(labels: list[TerminalLabel]) -> list[dict]:
    counts: dict[tuple, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for label in labels:
        key = (label.resolver_product, label.channel_class, label.unit, label.metric)
        counts[key][0] += 1
        counts[key][1] += int(not label.nonviolation)
    days: dict[tuple, set] = collections.defaultdict(set)
    for label in labels:
        if not label.nonviolation:
            days[(label.resolver_product, label.channel_class, label.unit, label.metric)].add(
                (label.city, label.target_date)
            )
    return [
        {"stratum": "|".join(key), "labels": n, "violations": f, "violation_city_days": len(days[key])}
        for key, (n, f, _) in sorted(counts.items())
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--forecasts-db", default=os.path.join(REPO, "state", "zeus-forecasts.db"))
    parser.add_argument("--world-db", default=os.path.join(REPO, "state", "zeus-world.db"))
    parser.add_argument("--out", default=os.path.join(REPO, "state", "day0_resolver_terminal_residual.json"))
    parser.add_argument("--eval-out", default=None)
    parser.add_argument("--as-of", default=None, help="UTC fit cutoff (default: now)")
    parser.add_argument("--history-start", default="2026-07-15")
    parser.add_argument("--eval-start", default="2026-08-20")
    parser.add_argument(
        "--center-shift-artifact",
        default=None,
        help="Optional day0_remaining_center_bias.json applied to both old and new in the evaluation",
    )
    args = parser.parse_args(argv)
    center_shift = None
    if args.center_shift_artifact:
        from src.calibration.day0_remaining_bias import RemainingBiasTable

        with open(args.center_shift_artifact, encoding="utf-8") as handle:
            center_shift = RemainingBiasTable(json.load(handle), identity="evaluation")

    as_of = _utc(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if as_of is None:
        raise SystemExit("--as-of must be an ISO instant with timezone")
    settlements = read_settlements(args.forecasts_db, args.history_start)
    renderings, tenth_evidence = read_metar_renderings(args.world_db, args.history_start, as_of)
    hko = read_hko_accumulator(args.world_db, args.history_start)
    posteriors = read_carrier_posteriors(args.forecasts_db, args.eval_start)
    members_index = members_index_of(posteriors)
    labels, skipped = build_labels(settlements, renderings, hko, members_index)
    print(f"settlements={len(settlements)} labels={len(labels)} skipped={dict(skipped)} posteriors={len(posteriors)}")

    artifact = fit_at(labels, tenth_evidence, as_of)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    tmp = f"{args.out}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, sort_keys=True, indent=1)
    os.replace(tmp, args.out)
    print(f"artifact={args.out} hash={artifact['content_hash']} kappa={artifact['kappa']}")

    settled = {(s["city"], s["target_date"], s["metric"]): s["value"] for s in settlements}
    by_day: dict[date, ResolverTerminalArtifact] = {}
    scored: list[dict] = []
    unscored: collections.Counter = collections.Counter()
    for row in evaluation_rows(posteriors):
        key = (row["city"], row["target_date"], row["metric"])
        if key not in settled:
            unscored["unsettled"] += 1
            continue
        day = row["computed_at"].date()
        if day not in by_day:
            cutoff = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            by_day[day] = ResolverTerminalArtifact.from_payload(fit_at(labels, tenth_evidence, cutoff))
        try:
            result = score_row(row, by_day[day], settled[key], center_shift)
        except ValueError as exc:
            unscored[str(exc)[:60]] += 1
            continue
        if result is None:
            unscored["no_cell"] += 1
            continue
        scored.append(result)
    table = {name: summarize(scored, select) for name, select in STRATA.items()}
    fidelity = np.asarray([r["fidelity"] for r in scored]) if scored else np.zeros(1)
    report = {
        "as_of": as_of.isoformat(),
        "artifact_hash": artifact["content_hash"],
        "labels_by_stratum": label_summary(
            labels_at(
                [label for label in labels if label.available_at_utc < as_of],
                channel_classes_at(tenth_evidence, as_of),
            )
        ),
        "evaluation_rows": len(scored),
        "unscored": dict(unscored),
        "old_carrier_rebuild_fidelity": {
            "max_abs_p50": float(np.median(fidelity)),
            "max_abs_p99": float(np.quantile(fidelity, 0.99)),
            "max_abs_max": float(fidelity.max()),
        },
        "strata": table,
    }
    print(json.dumps(report, indent=1, sort_keys=True))
    if args.eval_out:
        with open(args.eval_out, "w", encoding="utf-8") as handle:
            json.dump({**report, "scored": scored}, handle, sort_keys=True, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
