# Created: 2026-05-22
# Last reused/audited: 2026-05-23
# Authority basis: docs/archive/2026-Q2/operations_historical/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-C;
#   docs/operations/task_2026-05-22_forecast_bundle_layer_fix/SPEC.md §5
"""Day-0 observation extrema reader — semantics-correct high_so_far / low_so_far.

LIVE WIRING STATUS (2026-06-24): ``read_day0_observed_extrema`` is the canonical
held-position Day0 monitor source for settlement stations whose executable
observation evidence is already materialized in ``observation_instants`` rather
than served by ``src.data.observation_client`` live fetchers. That includes
NOAA-settled Ogimet METAR stations such as Moscow/UUWW and Tel Aviv/LLBG. WU and
HKO fetch paths remain in ``observation_client``; this reader is the DB-backed
canonical observation surface for the monitor path, not an experimental helper.

Root C fix: observation_instants.running_max is a PER-HOUR BUCKET maximum
(non-monotonic across the day).  The live writer stores the hourly max for that
observation window, NOT a cumulative day-so-far monotone.  The naive approach of
reading the latest row's running_max gives the WRONG answer whenever the peak
occurred earlier in the day (e.g. 25 at 15:00, 17 at 23:00 → naive returns 17).

The correct approach: MAX(running_max) over ALL qualifying rows for the city/date
up to the decision timestamp.

Physical law (from authority doc, §Physical law):
    H_D = settle(max_{t in local day} T(t))
    At decision τ: H_j = settle(max(H_obs_so_far, max_{t>τ} T_j(t)))
    Observation is a LOWER BOUND only; current_temp must NEVER lower the future max.

Source selection rule (§PR-C "never mix sources silently"):
    Walk source_priority in order; pick the FIRST source that has qualifying
    rows.  Compute MAX/MIN over rows of THAT source only.  Do NOT aggregate
    across sources.

coverage_status:
    OK           — chosen source has >= 6 qualifying rows
    LOW_COVERAGE — chosen source has 1–5 qualifying rows
    NO_DATA      — no qualifying rows for any source in source_priority
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Default source priority (tier-descending canonical preference).
# HK callers should override with ('hko_hourly_accumulator',).
# ---------------------------------------------------------------------------
_DEFAULT_SOURCE_PRIORITY: tuple[str, ...] = (
    "wu_icao_history",
    "hko_hourly_accumulator",
    "ogimet_metar_ltfm",
    "ogimet_metar_uuww",
    "ogimet_metar_llbg",
)

# Authorities the reader trusts (A4 filter).
_TRUSTED_AUTHORITIES: frozenset[str] = frozenset({"VERIFIED", "ICAO_STATION_NATIVE"})

# coverage_status constants
COVERAGE_OK = "OK"
COVERAGE_LOW = "LOW_COVERAGE"
COVERAGE_NONE = "NO_DATA"
_LOW_COVERAGE_THRESHOLD = 6


@dataclass(frozen=True)
class Day0ObservedExtrema:
    """Observation-side extrema for a single city/date/decision-time triple.

    high_so_far and low_so_far may be None when coverage_status == 'NO_DATA'.
    current_temp is diagnostic only; may be None regardless of coverage.

    Attributes
    ----------
    city:
        City name as stored in observation_instants.
    target_date:
        Local calendar date string 'YYYY-MM-DD'.
    chosen_source:
        Source tag whose rows were used, or None on NO_DATA.
    high_so_far:
        MAX(running_max) over qualifying rows — the correct day-so-far high.
        NOT the latest row's running_max.
    low_so_far:
        MIN(running_min) over qualifying rows — the correct day-so-far low.
    current_temp:
        Diagnostic only.  Latest temp_current value; may be NULL in DB.
        MUST NOT be used to bound or lower the future max.
    row_count:
        Number of qualifying rows for the chosen source.
    last_observation_time_utc:
        Latest qualifying observation timestamp for the chosen source. This is
        the freshness clock consumed by live monitor gates; it is never
        synthesized from decision_time_utc.
    coverage_status:
        'OK' (>=6 rows), 'LOW_COVERAGE' (1–5 rows), or 'NO_DATA' (0 rows).
    decision_time_utc:
        ISO8601 string of the decision time used as the cutoff.
    provenance:
        Metadata dict describing how extrema were computed.
    """

    city: str
    target_date: str
    chosen_source: Optional[str]
    high_so_far: Optional[float]
    low_so_far: Optional[float]
    current_temp: Optional[float]
    row_count: int
    coverage_status: str
    decision_time_utc: str
    last_observation_time_utc: Optional[str] = None
    provenance: dict = field(default_factory=dict, compare=False)


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_EXTREMA_SQL = """
    SELECT
        MAX(running_max) AS agg_high,
        MIN(running_min) AS agg_low,
        COUNT(*) AS n_rows,
        MAX(utc_timestamp) AS last_observation_time_utc
    FROM observation_instants
    WHERE city = ?
      AND target_date = ?
      AND source = ?
      AND datetime(utc_timestamp) <= datetime(?)
      AND authority IN ({auth_placeholders})
      AND COALESCE(causality_status, '') = 'OK'
      AND (
            (
                COALESCE(source_role, '') = 'historical_hourly'
                AND COALESCE(training_allowed, 0) = 1
            )
            OR (
                COALESCE(source_role, '') = 'runtime_monitoring'
                AND COALESCE(training_allowed, 0) = 0
            )
      )
"""

_CURRENT_TEMP_SQL = """
    SELECT temp_current
    FROM observation_instants
    WHERE city = ?
      AND target_date = ?
      AND source = ?
      AND datetime(utc_timestamp) <= datetime(?)
      AND authority IN ({auth_placeholders})
      AND COALESCE(causality_status, '') = 'OK'
      AND (
            (
                COALESCE(source_role, '') = 'historical_hourly'
                AND COALESCE(training_allowed, 0) = 1
            )
            OR (
                COALESCE(source_role, '') = 'runtime_monitoring'
                AND COALESCE(training_allowed, 0) = 0
            )
      )
      AND temp_current IS NOT NULL
    ORDER BY utc_timestamp DESC
    LIMIT 1
"""

_LATEST_CONTEXT_SQL = """
    SELECT temp_current, running_max, running_min, station_id, temp_unit, imported_at, source_role
    FROM observation_instants
    WHERE city = ?
      AND target_date = ?
      AND source = ?
      AND datetime(utc_timestamp) <= datetime(?)
      AND authority IN ({auth_placeholders})
      AND COALESCE(causality_status, '') = 'OK'
      AND (
            (
                COALESCE(source_role, '') = 'historical_hourly'
                AND COALESCE(training_allowed, 0) = 1
            )
            OR (
                COALESCE(source_role, '') = 'runtime_monitoring'
                AND COALESCE(training_allowed, 0) = 0
            )
      )
    ORDER BY datetime(utc_timestamp) DESC, datetime(imported_at) DESC, id DESC
    LIMIT 1
"""


def _auth_placeholders() -> str:
    return ", ".join("?" for _ in _TRUSTED_AUTHORITIES)


def _auth_values() -> tuple[str, ...]:
    return tuple(sorted(_TRUSTED_AUTHORITIES))


def read_day0_observed_extrema(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    timezone_name: str,
    decision_time_utc: datetime,
    source_priority: Sequence[str] = _DEFAULT_SOURCE_PRIORITY,
) -> Day0ObservedExtrema:
    """Read day-0 observed extrema using semantics-correct MAX aggregation.

    The key invariant: high_so_far = MAX(running_max) over all qualifying
    rows, NOT the latest row's running_max.  This is the only correct
    reading of the misnamed column (see module docstring).

    Parameters
    ----------
    conn:
        SQLite connection to zeus-world.db (must have observation_instants).
    city:
        City name as stored in observation_instants.
    target_date:
        Local calendar date string 'YYYY-MM-DD'.
    timezone_name:
        IANA timezone name (stored in provenance; not used for filtering
        because target_date carries local-day attribution in the writer).
    decision_time_utc:
        Cutoff: only rows with utc_timestamp <= this time are included.
        Must be timezone-aware UTC.
    source_priority:
        Ordered sequence of source tags to try.  The first source that has
        qualifying rows is used exclusively.  Defaults to canonical tier order.

    Returns
    -------
    Day0ObservedExtrema
        Always returns a dataclass (never raises on empty data).
        coverage_status='NO_DATA' with None extrema when no rows found.

    Raises
    ------
    ValueError
        If decision_time_utc is not timezone-aware.
    """
    if decision_time_utc.tzinfo is None:
        raise ValueError(
            "decision_time_utc must be timezone-aware. "
            f"Got naive datetime: {decision_time_utc!r}"
        )

    # Normalise to UTC ISO8601 string that SQLite's datetime() accepts.
    # Use +00:00 suffix (not Z) — consistent with writer format.
    decision_str = (
        decision_time_utc.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S+00:00")
    )

    auth_ph = _auth_placeholders()
    auth_vals = _auth_values()

    extrema_sql = _EXTREMA_SQL.format(auth_placeholders=auth_ph)
    current_temp_sql = _CURRENT_TEMP_SQL.format(auth_placeholders=auth_ph)

    chosen_source: Optional[str] = None
    agg_high: Optional[float] = None
    agg_low: Optional[float] = None
    n_rows: int = 0
    last_observation_time_utc: Optional[str] = None

    for source in source_priority:
        row = conn.execute(
            extrema_sql,
            (city, target_date, source, decision_str) + auth_vals,
        ).fetchone()
        if row is None or row[2] == 0:
            continue
        # row[2] is COUNT(*); row[0] is MAX(running_max), row[1] is MIN(running_min)
        chosen_source = source
        agg_high = row[0]  # may be None if all running_max were NULL
        agg_low = row[1]   # may be None if all running_min were NULL
        n_rows = int(row[2])
        last_observation_time_utc = str(row[3]) if row[3] is not None else None
        break

    # Fetch latest temp_current for the chosen source (diagnostic only).
    current_temp: Optional[float] = None
    if chosen_source is not None:
        ct_row = conn.execute(
            current_temp_sql,
            (city, target_date, chosen_source, decision_str) + auth_vals,
        ).fetchone()
        if ct_row is not None:
            current_temp = ct_row[0]

    if n_rows == 0:
        coverage_status = COVERAGE_NONE
    elif n_rows < _LOW_COVERAGE_THRESHOLD:
        coverage_status = COVERAGE_LOW
    else:
        coverage_status = COVERAGE_OK

    provenance = {
        "running_max_semantics": "hour_bucket_max_aggregated_by_MAX",
        "aggregation": "MAX(running_max) / MIN(running_min) over qualifying rows",
        "authority_filter": sorted(_TRUSTED_AUTHORITIES),
        "decision_cutoff_utc": decision_str,
        "timezone_name": timezone_name,
        "source_priority_tried": list(source_priority),
        "chosen_source": chosen_source,
        "row_count": n_rows,
        "coverage_status": coverage_status,
        "last_observation_time_utc": last_observation_time_utc,
        "reader": "src.data.day0_observation_reader.read_day0_observed_extrema",
    }

    return Day0ObservedExtrema(
        city=city,
        target_date=target_date,
        chosen_source=chosen_source,
        high_so_far=agg_high,
        low_so_far=agg_low,
        current_temp=current_temp,
        row_count=n_rows,
        coverage_status=coverage_status,
        decision_time_utc=decision_str,
        last_observation_time_utc=last_observation_time_utc,
        provenance=provenance,
    )


def source_priority_for_city(city: object) -> tuple[str, ...]:
    """Return settlement-source-specific priority for executable Day0 observations."""

    source_type = str(getattr(city, "settlement_source_type", "") or "wu_icao").strip()
    station = str(getattr(city, "wu_station", "") or "").strip().lower()
    if source_type == "hko":
        return ("hko_hourly_accumulator",)
    if source_type == "noaa":
        if station:
            return (f"ogimet_metar_{station}",)
        return tuple(src for src in _DEFAULT_SOURCE_PRIORITY if src.startswith("ogimet_metar_"))
    if source_type == "wu_icao":
        return ("wu_icao_history",)
    return _DEFAULT_SOURCE_PRIORITY


def read_day0_observation_context_from_instants(
    conn: sqlite3.Connection,
    *,
    city: object,
    target_date: str,
    decision_time_utc: datetime,
    source_priority: Sequence[str] | None = None,
):
    """Build the executable Day0 observation context from canonical observation_instants.

    This is the shared live source for entry and monitor when the settlement-grade
    observed-so-far surface is already materialized locally. The WU/ICAO and
    Ogimet writers store the authoritative running extrema but generally do not
    store an exact ``temp_current``; current temperature is diagnostic for the
    high/low settlement math, so this adapter supplies a finite latest-hour
    diagnostic value only to satisfy the typed context contract.
    """

    from src.data.observation_client import Day0ObservationContext

    city_name = str(getattr(city, "name", "") or "")
    timezone_name = str(getattr(city, "timezone", "") or "")
    unit = str(getattr(city, "settlement_unit", "") or "C")
    if not city_name or not timezone_name:
        return None
    priority = tuple(source_priority or source_priority_for_city(city))
    result = read_day0_observed_extrema(
        conn,
        city=city_name,
        target_date=str(target_date),
        timezone_name=timezone_name,
        decision_time_utc=decision_time_utc,
        source_priority=priority,
    )
    if result.coverage_status == COVERAGE_NONE or result.chosen_source is None:
        return None
    if result.high_so_far is None or result.low_so_far is None:
        return None
    observation_time = result.last_observation_time_utc
    if not observation_time:
        return None

    decision_str = (
        decision_time_utc.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S+00:00")
    )
    auth_ph = _auth_placeholders()
    latest_sql = _LATEST_CONTEXT_SQL.format(auth_placeholders=auth_ph)
    try:
        latest = conn.execute(
            latest_sql,
            (city_name, str(target_date), result.chosen_source, decision_str) + _auth_values(),
        ).fetchone()
    except sqlite3.OperationalError:
        latest = conn.execute(
            """
            SELECT temp_current, running_max, running_min, source_role
            FROM observation_instants
            WHERE city = ?
              AND target_date = ?
              AND source = ?
              AND datetime(utc_timestamp) <= datetime(?)
              AND authority IN ({auth_placeholders})
              AND COALESCE(causality_status, '') = 'OK'
              AND (
                    (
                        COALESCE(source_role, '') = 'historical_hourly'
                        AND COALESCE(training_allowed, 0) = 1
                    )
                    OR (
                        COALESCE(source_role, '') = 'runtime_monitoring'
                        AND COALESCE(training_allowed, 0) = 0
                    )
              )
            ORDER BY datetime(utc_timestamp) DESC
            LIMIT 1
            """.format(auth_placeholders=auth_ph),
            (city_name, str(target_date), result.chosen_source, decision_str) + _auth_values(),
        ).fetchone()
    latest_current = latest_hi = latest_low = None
    station_id = ""
    observed_unit = unit
    available_at = result.decision_time_utc
    latest_source_role = ""
    if latest is not None:
        latest_current = latest[0]
        latest_hi = latest[1]
        latest_low = latest[2]
        if len(latest) > 3:
            if len(latest) == 4:
                latest_source_role = str(latest[3] or "").strip()
            else:
                station_id = str(latest[3] or "").strip().upper()
        if len(latest) > 4:
            observed_unit = str(latest[4] or unit or "C")
        if len(latest) > 5:
            available_at = str(latest[5] or result.decision_time_utc)
        if len(latest) > 6:
            latest_source_role = str(latest[6] or "").strip()

    if (
        latest_source_role == "runtime_monitoring"
        and _finite_float(latest_current) is None
    ):
        current_temp = float("nan")
    else:
        current_temp = _diagnostic_current_temp(
            latest_current,
            latest_hi,
            latest_low,
            fallback_high=result.high_so_far,
            fallback_low=result.low_so_far,
        )
    return Day0ObservationContext(
        current_temp=current_temp,
        high_so_far=float(result.high_so_far),
        low_so_far=float(result.low_so_far),
        source=str(result.chosen_source),
        observation_time=str(observation_time),
        unit=observed_unit,
        station_id=station_id,
        sample_count=int(result.row_count),
        last_sample_time=str(observation_time),
        coverage_status=str(result.coverage_status),
        observation_available_at=available_at,
        provider_reported_time="canonical_observation_instants",
    )


def _diagnostic_current_temp(
    current_temp: object,
    latest_high: object,
    latest_low: object,
    *,
    fallback_high: object,
    fallback_low: object,
) -> float:
    for value in (current_temp,):
        parsed = _finite_float(value)
        if parsed is not None:
            return parsed
    latest_hi = _finite_float(latest_high)
    latest_lo = _finite_float(latest_low)
    if latest_hi is not None and latest_lo is not None:
        return (latest_hi + latest_lo) / 2.0
    for value in (latest_hi, latest_lo, fallback_high, fallback_low):
        parsed = _finite_float(value)
        if parsed is not None:
            return parsed
    return float("nan")


def _finite_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None
