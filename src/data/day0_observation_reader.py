# Created: 2026-05-22
# Last reused/audited: 2026-07-20 (source-specific HKO cumulative snapshots)
# Authority basis: docs/archive/2026-Q2/operations_historical/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-C;
#   docs/operations/task_2026-05-22_forecast_bundle_layer_fix/SPEC.md §5;
#   docs/evidence/upstream_physical_2026_07_17/day0_mechanism_first_principles_audit.md §M-2/§H-3
"""Day-0 observation extrema reader — semantics-correct high_so_far / low_so_far.

LIVE WIRING STATUS (2026-06-24): ``read_day0_observed_extrema`` is the canonical
held-position Day0 monitor source for settlement stations whose executable
observation evidence is already materialized in ``observation_instants`` rather
than served by ``src.data.observation_client`` live fetchers. That includes
NOAA-settled Ogimet METAR stations such as Moscow/UUWW and Tel Aviv/LLBG. WU and
HKO fetch paths remain in ``observation_client``; this reader is the DB-backed
canonical observation surface for the monitor path, not an experimental helper.

Root C fix: WU ``running_max`` is a PER-HOUR BUCKET maximum, so WU requires MAX
over all qualifying rows. HKO ``running_max`` is an official cumulative snapshot,
so HKO requires the latest qualifying snapshot. Treating both shapes alike either
forgets an earlier WU peak or makes a provisional HKO value falsely absorbing.

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
    GAP_SUSPECT  — rows exist, but the qualifying-row timeline has a hole of
                   >= 120 minutes that overlaps a metric's likely extreme
                   window (M-2/H-3 fix: a mid-day ingest stall spanning the
                   peak/trough silently understates the running extreme while
                   the row COUNT still reads OK).  Which metric(s) are affected
                   is carried in gap_suspect_metrics; use
                   coverage_status_for_metric() for the per-metric verdict —
                   a midnight hole must not degrade a HIGH market.
    NO_DATA      — no qualifying rows for any source in source_priority

Extreme windows (measured, docs/evidence/upstream_physical_2026_07_17/
day0_percity_diurnal_timing.md): HIGH peak local 11:00–17:00 (median in
[12,16] for 49/50 cities); LOW trough local 02:00–08:00 (median 3–5am for
most cities; wide window used deliberately).

Relationship to src/data/day0_coverage_proof.py: that module's GAP_INCOMPLETE
is a cadence-tolerance proof (2.5x expected cadence, no extreme-window
awareness) consumed only by day0_source_health; this reader's GAP_SUSPECT is
the settlement-metric-aware verdict for the live entry/monitor lanes.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

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

_HKO_SOURCE = "hko_hourly_accumulator"
_HKO_EXTREMA_BASIS = "hko_since_midnight_extrema_1min_mean"

# coverage_status constants
COVERAGE_OK = "OK"
COVERAGE_LOW = "LOW_COVERAGE"
COVERAGE_NONE = "NO_DATA"
COVERAGE_GAP_SUSPECT = "GAP_SUSPECT"
_LOW_COVERAGE_THRESHOLD = 6

# M-2/H-3 gap detector: a qualifying-row hole at least this long that overlaps a
# metric's likely extreme window makes the running extreme suspect for that metric.
GAP_SUSPECT_MIN_GAP_MINUTES = 120.0
# Measured per-city diurnal extreme timing (day0_percity_diurnal_timing.md):
# HIGH peak median in local [12,16] for 49/50 cities -> guard window 11:00-17:00.
# LOW trough median 3-5am local for most cities -> wide guard window 02:00-08:00.
_EXTREME_WINDOWS_LOCAL_HOURS: dict[str, tuple[int, int]] = {
    "high": (11, 17),
    "low": (2, 8),
}


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
        'OK' (>=6 rows), 'LOW_COVERAGE' (1–5 rows), 'NO_DATA' (0 rows), or
        'GAP_SUSPECT' (a >=120min qualifying-row hole overlaps at least one
        metric's likely extreme window). GAP_SUSPECT is metric-attributed via
        gap_suspect_metrics; metric-aware consumers must use
        coverage_status_for_metric() so a midnight hole never degrades a HIGH
        market.
    decision_time_utc:
        ISO8601 string of the decision time used as the cutoff.
    max_gap_minutes:
        Largest hole in the qualifying-row timeline over
        [min(local-day-start, first row), min(decision_time, local-day-end)],
        in minutes. None when no rows or timestamps were unavailable.
    gap_suspect_metrics:
        Metrics ('high'/'low') whose likely extreme window overlaps a
        >=120min hole. Empty when coverage is contiguous enough.
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
    max_gap_minutes: Optional[float] = None
    gap_suspect_metrics: tuple[str, ...] = ()
    provenance: dict = field(default_factory=dict, compare=False)

    def coverage_status_for_metric(self, metric: str) -> str:
        """Per-metric coverage verdict (M-2/H-3).

        GAP_SUSPECT only when the hole overlaps THIS metric's extreme window;
        otherwise the plain row-count status — a midnight hole must not
        degrade a HIGH market.
        """
        if str(metric or "").strip().lower() in self.gap_suspect_metrics:
            return COVERAGE_GAP_SUSPECT
        if self.row_count == 0:
            return COVERAGE_NONE
        if self.row_count < _LOW_COVERAGE_THRESHOLD:
            return COVERAGE_LOW
        return COVERAGE_OK


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_EXTREMA_SQL = """
    SELECT
        MAX(running_max) AS agg_high,
        MIN(running_min) AS agg_low,
        COUNT(*) AS n_rows,
        MAX(utc_timestamp) AS last_observation_time_utc
    FROM {table_ref}
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
      {source_semantics}
"""

_CURRENT_TEMP_SQL = """
    SELECT temp_current
    FROM {table_ref}
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
      {source_semantics}
    ORDER BY utc_timestamp DESC
    LIMIT 1
"""

_LATEST_CONTEXT_SQL = """
    SELECT
        temp_current,
        running_max,
        running_min,
        station_id,
        temp_unit,
        imported_at,
        source_role,
        authority,
        data_version,
        training_allowed,
        causality_status
    FROM {table_ref}
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
      {source_semantics}
    ORDER BY datetime(utc_timestamp) DESC, datetime(imported_at) DESC, id DESC
    LIMIT 1
"""

_LATEST_EXTREMA_SQL = """
    SELECT running_max, running_min
    FROM {table_ref}
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
      {source_semantics}
    ORDER BY datetime(utc_timestamp) DESC, id DESC
    LIMIT 1
"""


# Timestamps of the qualifying rows, for the M-2/H-3 gap detector. A separate
# query (not GROUP_CONCAT on the aggregate) was chosen deliberately: SQLite's
# GROUP_CONCAT has no guaranteed element order, so the string would need
# splitting AND re-sorting in Python anyway; the separate query is
# correct-by-construction, runs once (chosen source only), and keeps the shared
# WHERE clause identical to _EXTREMA_SQL.
_TIMESTAMPS_SQL = """
    SELECT utc_timestamp
    FROM {table_ref}
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
      {source_semantics}
    ORDER BY datetime(utc_timestamp)
"""


def _auth_placeholders() -> str:
    return ", ".join("?" for _ in _TRUSTED_AUTHORITIES)


def _auth_values() -> tuple[str, ...]:
    return tuple(sorted(_TRUSTED_AUTHORITIES))


def _source_semantics(source: str) -> tuple[str, tuple[str, ...]]:
    """Return source-specific executable-evidence predicates and parameters."""

    if source != _HKO_SOURCE:
        return "", ()
    return (
        """
        AND CASE
                WHEN NOT json_valid(COALESCE(provenance_json, '')) THEN 0
                WHEN json_extract(
                     provenance_json, '$.observation_basis'
                ) <> ? THEN 0
                WHEN COALESCE(json_type(
                     provenance_json, '$.official_running_high_c'
                ), '') NOT IN ('integer', 'real') THEN 0
                WHEN COALESCE(json_type(
                     provenance_json, '$.official_running_low_c'
                ), '') NOT IN ('integer', 'real') THEN 0
                ELSE 1
            END = 1
        """,
        (_HKO_EXTREMA_BASIS,),
    )


def _parse_row_utc(raw: object) -> Optional[datetime]:
    """Parse a stored utc_timestamp into an aware UTC datetime; None on failure."""
    try:
        text = str(raw).strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coverage_gap_analysis(
    *,
    sample_times_utc: list[datetime],
    target_date: str,
    timezone_name: str,
    decision_time_utc: datetime,
    cumulative_rows: bool,
) -> tuple[Optional[float], tuple[str, ...]]:
    """M-2/H-3 gap detector: (max_gap_minutes, gap_suspect_metrics).

    The hole timeline is [min(local-day-start, first row), min(decision_time,
    local-day-end)].  A leading hole (rows start late) and a trailing hole
    (ingest stalled and has not resumed by decision time) are real holes: the
    extreme inside them was never recorded.

    ``cumulative_rows`` (HKO since-midnight extrema): each row already absorbs
    the whole day so far, so leading/interior holes lose nothing — only the
    trailing hole after the last row can hide an extreme.

    A metric becomes suspect when a hole of >= GAP_SUSPECT_MIN_GAP_MINUTES
    overlaps that metric's likely extreme window in the city's LOCAL time
    (HIGH 11:00-17:00, LOW 02:00-08:00; measured per-city evidence).  On an
    unusable timezone the metric attribution degrades to none (row-count
    status keeps authority); max_gap is still reported.
    """
    if not sample_times_utc:
        return None, ()
    ordered = sorted(sample_times_utc)

    tz: Optional[ZoneInfo] = None
    day_start_utc: Optional[datetime] = None
    day_end_utc: Optional[datetime] = None
    try:
        tz = ZoneInfo(str(timezone_name))
        target_d = date.fromisoformat(str(target_date))
        day_start_utc = datetime(
            target_d.year, target_d.month, target_d.day, tzinfo=tz
        ).astimezone(timezone.utc)
        next_d = target_d + timedelta(days=1)
        day_end_utc = datetime(
            next_d.year, next_d.month, next_d.day, tzinfo=tz
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, OSError):
        tz = None

    decision_utc = decision_time_utc.astimezone(timezone.utc)
    left = ordered[0] if day_start_utc is None else min(day_start_utc, ordered[0])
    right = decision_utc if day_end_utc is None else min(decision_utc, day_end_utc)
    if cumulative_rows:
        # Since-midnight rows: only the tail after the last row is unobserved.
        bounds = [ordered[-1], right]
    else:
        bounds = [left, *ordered, right]

    gaps: list[tuple[datetime, datetime]] = [
        (a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b > a
    ]
    if not gaps:
        return None, ()
    max_gap_minutes = max((b - a).total_seconds() / 60.0 for a, b in gaps)

    if tz is None or day_start_utc is None:
        return max_gap_minutes, ()

    target_d = date.fromisoformat(str(target_date))
    suspect: set[str] = set()
    for metric, (win_lo_h, win_hi_h) in _EXTREME_WINDOWS_LOCAL_HOURS.items():
        win_start = datetime(
            target_d.year, target_d.month, target_d.day, win_lo_h, tzinfo=tz
        ).astimezone(timezone.utc)
        win_end = datetime(
            target_d.year, target_d.month, target_d.day, win_hi_h, tzinfo=tz
        ).astimezone(timezone.utc)
        for gap_start, gap_end in gaps:
            if (gap_end - gap_start).total_seconds() / 60.0 < GAP_SUSPECT_MIN_GAP_MINUTES:
                continue
            if min(gap_end, win_end) > max(gap_start, win_start):
                suspect.add(metric)
                break
    return max_gap_minutes, tuple(sorted(suspect))


def read_day0_observed_extrema(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    timezone_name: str,
    decision_time_utc: datetime,
    source_priority: Sequence[str] = _DEFAULT_SOURCE_PRIORITY,
    table_ref: str = "observation_instants",
) -> Day0ObservedExtrema:
    """Read day-0 observed extrema using source-specific aggregation.

    WU/hourly rows are bucket facts and aggregate with MAX/MIN across the local
    day. HKO rows are cumulative official snapshots; the latest qualifying
    snapshot replaces earlier provisional snapshots and must not be aggregated
    again across time.

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
    table_ref:
        Canonical observation table, optionally through an attached ``world``
        schema. Only the fixed runtime table names are accepted.

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
    if table_ref not in {
        "observation_instants",
        "world.observation_instants",
        "forecasts.observation_instants",
    }:
        raise ValueError(f"unsupported observation table_ref: {table_ref!r}")

    # Normalise to UTC ISO8601 string that SQLite's datetime() accepts.
    # Use +00:00 suffix (not Z) — consistent with writer format.
    decision_str = (
        decision_time_utc.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S+00:00")
    )

    auth_ph = _auth_placeholders()
    auth_vals = _auth_values()

    chosen_source: Optional[str] = None
    agg_high: Optional[float] = None
    agg_low: Optional[float] = None
    n_rows: int = 0
    last_observation_time_utc: Optional[str] = None

    for source in source_priority:
        source_sql, source_vals = _source_semantics(source)
        extrema_sql = _EXTREMA_SQL.format(
            auth_placeholders=auth_ph,
            source_semantics=source_sql,
            table_ref=table_ref,
        )
        row = conn.execute(
            extrema_sql,
            (city, target_date, source, decision_str) + auth_vals + source_vals,
        ).fetchone()
        if row is None or row[2] == 0:
            continue
        # WU/hourly rows are bucket facts. HKO rows are cumulative provider
        # snapshots, so current decision-time truth is the latest snapshot.
        chosen_source = source
        agg_high = row[0]  # may be None if all running_max were NULL
        agg_low = row[1]   # may be None if all running_min were NULL
        n_rows = int(row[2])
        last_observation_time_utc = str(row[3]) if row[3] is not None else None
        if source == _HKO_SOURCE:
            latest_extrema_sql = _LATEST_EXTREMA_SQL.format(
                auth_placeholders=auth_ph,
                source_semantics=source_sql,
                table_ref=table_ref,
            )
            latest_extrema = conn.execute(
                latest_extrema_sql,
                (city, target_date, source, decision_str) + auth_vals + source_vals,
            ).fetchone()
            if latest_extrema is None:
                continue
            agg_high, agg_low = latest_extrema[0], latest_extrema[1]
        break

    # M-2/H-3: qualifying-row timeline for the chosen source only (never mixed).
    max_gap_minutes: Optional[float] = None
    gap_suspect_metrics: tuple[str, ...] = ()
    if chosen_source is not None:
        source_sql, source_vals = _source_semantics(chosen_source)
        timestamps_sql = _TIMESTAMPS_SQL.format(
            auth_placeholders=auth_ph,
            source_semantics=source_sql,
            table_ref=table_ref,
        )
        sample_times = [
            parsed
            for (raw,) in conn.execute(
                timestamps_sql,
                (city, target_date, chosen_source, decision_str) + auth_vals + source_vals,
            )
            if (parsed := _parse_row_utc(raw)) is not None
        ]
        max_gap_minutes, gap_suspect_metrics = _coverage_gap_analysis(
            sample_times_utc=sample_times,
            target_date=target_date,
            timezone_name=timezone_name,
            decision_time_utc=decision_time_utc,
            # HKO rows are official since-midnight extrema: each row absorbs the
            # whole day so far, so only the trailing hole can hide an extreme.
            cumulative_rows=chosen_source == _HKO_SOURCE,
        )

    # Fetch latest temp_current for the chosen source (diagnostic only).
    current_temp: Optional[float] = None
    if chosen_source is not None:
        source_sql, source_vals = _source_semantics(chosen_source)
        current_temp_sql = _CURRENT_TEMP_SQL.format(
            auth_placeholders=auth_ph,
            source_semantics=source_sql,
            table_ref=table_ref,
        )
        try:
            ct_row = conn.execute(
                current_temp_sql,
                (city, target_date, chosen_source, decision_str) + auth_vals + source_vals,
            ).fetchone()
        except sqlite3.OperationalError:
            ct_row = None
        if ct_row is not None:
            current_temp = ct_row[0]

    if n_rows == 0:
        coverage_status = COVERAGE_NONE
    elif gap_suspect_metrics:
        coverage_status = COVERAGE_GAP_SUSPECT
    elif n_rows < _LOW_COVERAGE_THRESHOLD:
        coverage_status = COVERAGE_LOW
    else:
        coverage_status = COVERAGE_OK

    hko_snapshot = chosen_source == _HKO_SOURCE
    provenance = {
        "running_max_semantics": (
            "cumulative_snapshot_latest"
            if hko_snapshot
            else "hour_bucket_max_aggregated_by_MAX"
        ),
        "aggregation": (
            "latest qualifying HKO cumulative snapshot"
            if hko_snapshot
            else "MAX(running_max) / MIN(running_min) over qualifying rows"
        ),
        "authority_filter": sorted(_TRUSTED_AUTHORITIES),
        "decision_cutoff_utc": decision_str,
        "timezone_name": timezone_name,
        "source_priority_tried": list(source_priority),
        "chosen_source": chosen_source,
        "row_count": n_rows,
        "coverage_status": coverage_status,
        "max_gap_minutes": max_gap_minutes,
        "gap_suspect_metrics": list(gap_suspect_metrics),
        "gap_suspect_min_gap_minutes": GAP_SUSPECT_MIN_GAP_MINUTES,
        "extreme_windows_local_hours": dict(_EXTREME_WINDOWS_LOCAL_HOURS),
        "last_observation_time_utc": last_observation_time_utc,
        "table_ref": table_ref,
        "source_semantics": (
            "hko_official_since_midnight_extrema_only"
            if chosen_source == _HKO_SOURCE
            else "source_role_and_authority"
        ),
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
        max_gap_minutes=max_gap_minutes,
        gap_suspect_metrics=gap_suspect_metrics,
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
    source_sql, source_vals = _source_semantics(result.chosen_source)
    latest_sql = _LATEST_CONTEXT_SQL.format(
        auth_placeholders=auth_ph,
        source_semantics=source_sql,
        table_ref="observation_instants",
    )
    try:
        latest = conn.execute(
            latest_sql,
            (city_name, str(target_date), result.chosen_source, decision_str)
            + _auth_values()
            + source_vals,
        ).fetchone()
    except sqlite3.OperationalError:
        latest = conn.execute(
            """
            SELECT
                temp_current,
                running_max,
                running_min,
                source_role,
                authority,
                data_version,
                training_allowed,
                causality_status
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
              {source_semantics}
            ORDER BY datetime(utc_timestamp) DESC
            LIMIT 1
            """.format(
                auth_placeholders=auth_ph,
                source_semantics=source_sql,
            ),
            (city_name, str(target_date), result.chosen_source, decision_str)
            + _auth_values()
            + source_vals,
        ).fetchone()
    latest_current = latest_hi = latest_low = None
    station_id = ""
    observed_unit = unit
    available_at = result.decision_time_utc
    latest_source_role = ""
    latest_source_authority = ""
    latest_data_version = ""
    latest_training_allowed = None
    latest_causality_status = ""
    if latest is not None:
        latest_current = latest[0]
        latest_hi = latest[1]
        latest_low = latest[2]
        if len(latest) >= 11:
            station_id = str(latest[3] or "").strip().upper()
            observed_unit = str(latest[4] or unit or "C")
            available_at = str(latest[5] or result.decision_time_utc)
            latest_source_role = str(latest[6] or "").strip()
            latest_source_authority = str(latest[7] or "").strip()
            latest_data_version = str(latest[8] or "").strip()
            latest_training_allowed = bool(latest[9]) if latest[9] is not None else None
            latest_causality_status = str(latest[10] or "").strip()
        elif len(latest) >= 8:
            latest_source_role = str(latest[3] or "").strip()
            latest_source_authority = str(latest[4] or "").strip()
            latest_data_version = str(latest[5] or "").strip()
            latest_training_allowed = bool(latest[6]) if latest[6] is not None else None
            latest_causality_status = str(latest[7] or "").strip()
        elif len(latest) >= 4:
            latest_source_role = str(latest[3] or "").strip()

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
        source_role=latest_source_role,
        source_authority=latest_source_authority,
        data_version=latest_data_version,
        training_allowed=latest_training_allowed,
        causality_status=latest_causality_status or "OK",
        max_gap_minutes=result.max_gap_minutes,
        gap_suspect_metrics=result.gap_suspect_metrics,
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
