# Created: (pre-rule legacy)
# Lifecycle: created=2026-04-23; last_reviewed=2026-04-25; last_reused=2026-06-10
# Purpose: Build canonical diurnal analytics from reader-safe obs_v2 instants.
# Reuse: Check active packet scope and obs_v2 reader-gate predicates before running; writes state/zeus-world.db.
# Last reused/audited: 2026-07-19 (read/compute isolated from projection publish)
#   2026-06-10: STALE_REWRITE of p_high_set semantics —
#   adversarial review /tmp/day0_adversarial_review.md finding 3: the prior
#   computation compared the PER-HOUR bucket max against the daily max, i.e.
#   "P(the peak occurs at hour h)" — a PMF-shaped, non-monotone quantity that
#   collapsed to ~0 by evening (Chicago Jun h17=0.10, h20=0.00; impossible for
#   the documented "P(daily high already set by hour h)"). The day0 maturity
#   gate load-bears on this column; the broken shape sunset-locked US exits and
#   granted Seoul PRE-peak authority (h13=0.64). Fixed: CUMULATIVE running max
#   through hour h vs daily max (a survival-shaped, monotone non-decreasing
#   curve by construction per day) + an isotonic (cumulative-max) pass on the
#   monthly aggregate so hour-coverage sampling noise can never re-introduce
#   local decreases. Antibody: tests/test_diurnal_peak_prob_monotone.py.
# Authority basis: .omc/plans/observation-instants-migration-iter3.md Phase 2 +
#                  docs/operations/task_2026-04-21_gate_f_data_backfill/step4_phase2_cutover.md +
#                  /tmp/day0_adversarial_review.md finding 3 (2026-06-10)
"""ETL: Aggregate DST-safe intraday observations -> diurnal_curves/diurnal_peak_prob.

Source: `observation_instants_current` VIEW (Phase 2 atomic cutover indirection
over `observation_instants`). Pre-Phase-2 flip the VIEW returns 0 rows,
which this script treats as a fail-closed condition. Post-flip the VIEW returns
the active `data_version` corpus (currently `v1.wu-native`, station-native).
This script then applies the P3 reader gate locally: only reader-safe authority,
training-allowed, source-role eligible, causally safe, provenance-bearing rows
feed canonical diurnal analytics.

Temperature source: `COALESCE(temp_current, running_max)`. Legacy
`observation_instants` populated `temp_current` (single hourly snapshot);
`observation_instants` populates `running_max`/`running_min` per hour
(extremum-preserving aggregation that captures intra-hour SPECI peaks). Using
COALESCE keeps this script correct across both shapes and aligns the diurnal
sample with settlement semantics (daily high is the running max of hourly
maxima).

Ambiguous DST fallback hours are excluded from statistical tables rather than
forced into a normal local-hour bucket. Multi-source hourly observations are
collapsed to one canonical city/date/hour sample before seasonal aggregation.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import (  # noqa: E402
    get_world_connection as get_write_connection,
    get_world_connection_read_only as get_read_connection,
)


ETL_WORLD_WRITE_BUSY_TIMEOUT_MS = 250


OBSERVATION_READER_GATE_SQL = """
    authority IN ('VERIFIED', 'ICAO_STATION_NATIVE')
    AND COALESCE(training_allowed, 0) = 1
    AND source_role = 'historical_hourly'
    AND UPPER(TRIM(CAST(causality_status AS TEXT))) = 'OK'
    AND (
        CASE
          WHEN provenance_json IS NULL
            OR TRIM(CAST(provenance_json AS TEXT)) = ''
            OR json_valid(provenance_json) = 0
          THEN 0
          WHEN (
            json_extract(provenance_json, '$.payload_hash') IS NULL
            OR TRIM(CAST(json_extract(provenance_json, '$.payload_hash') AS TEXT)) = ''
            OR json_extract(provenance_json, '$.parser_version') IS NULL
            OR TRIM(CAST(json_extract(provenance_json, '$.parser_version') AS TEXT)) = ''
            OR (
                (
                    json_extract(provenance_json, '$.source_url') IS NULL
                    OR TRIM(CAST(json_extract(provenance_json, '$.source_url') AS TEXT)) = ''
                )
                AND (
                    json_extract(provenance_json, '$.source_file') IS NULL
                    OR TRIM(CAST(json_extract(provenance_json, '$.source_file') AS TEXT)) = ''
                )
            )
            OR (
                (
                    json_extract(provenance_json, '$.station_id') IS NULL
                    OR TRIM(CAST(json_extract(provenance_json, '$.station_id') AS TEXT)) = ''
                )
                AND (
                    json_extract(provenance_json, '$.station_registry_version') IS NULL
                    OR TRIM(CAST(json_extract(provenance_json, '$.station_registry_version') AS TEXT)) = ''
                )
                AND (
                    json_extract(provenance_json, '$.station_registry_hash') IS NULL
                    OR TRIM(CAST(json_extract(provenance_json, '$.station_registry_hash') AS TEXT)) = ''
                )
            )
          )
          THEN 0
          ELSE 1
        END
    ) = 1
"""


def season_from_date(date_str: str, city_name: str = "") -> str:
    """Hemisphere-aware season code."""
    from src.calibration.manager import season_from_date as _sfd, lat_for_city
    lat = lat_for_city(city_name) if city_name else 90.0
    return _sfd(date_str, lat=lat)


def _obs_hour(local_timestamp: str) -> int:
    return datetime.fromisoformat(local_timestamp).hour


def _isotonic_by_hour(mean_by_hour: dict) -> dict:
    """ISOTONIC ENFORCEMENT (2026-06-10 finding-3 fix, part 2).

    The per-day high-set indicator is monotone, but hour buckets are averaged
    over DIFFERENT day subsets (coverage varies by hour), which can
    re-introduce small local decreases in the aggregate. P(high already set
    by h) is monotone non-decreasing BY DEFINITION, so enforce the shape with
    a cumulative-max pass over hours (applies to both the monthly table the
    day0 maturity gate reads and the seasonal fallback column).
    """
    out: dict = {}
    running = 0.0
    for hour in sorted(mean_by_hour):
        running = max(running, float(mean_by_hour[hour]))
        out[hour] = min(1.0, running)
    return out


def _cumulative_high_set_indicators(samples: list) -> list:
    """Per-day survival indicators: 1[cum_max(through hour h) == daily_max].

    ``samples``: dicts with 'hour' and 'running_max' (per-hour bucket max).
    Sorted ascending by hour internally. Returns [(hour, indicator)] —
    monotone non-decreasing in hour by construction (finding-3 fix, part 1).
    """
    ordered = sorted(samples, key=lambda sample: int(sample["hour"]))
    final_high = max(float(sample["running_max"]) for sample in ordered)
    cumulative = float("-inf")
    out: list = []
    for sample in ordered:
        cumulative = max(cumulative, float(sample["running_max"]))
        out.append((int(sample["hour"]), 1.0 if cumulative >= final_high - 1e-9 else 0.0))
    return out


def run_etl() -> dict:
    source = get_read_connection()
    try:
        current_count = source.execute(
            "SELECT COUNT(*) FROM observation_instants_current"
        ).fetchone()[0]
        if current_count == 0:
            print(
                "ERROR: observation_instants_current is empty. "
                "Check zeus_meta.observation_data_version (Phase 2 cutover) and "
                "observation_instants population."
            )
            return {"stored": 0, "error": "no observation_instants_current"}

        safe_count = source.execute(
            f"""
            SELECT COUNT(*)
            FROM observation_instants_current
            WHERE {OBSERVATION_READER_GATE_SQL}
            """
        ).fetchone()[0]
        if safe_count == 0:
            print(
                "ERROR: observation_instants_current has no reader-safe rows. "
                "Require authority, provenance identity, training_allowed=1, "
                "source_role='historical_hourly', and causality_status='OK'."
            )
            return {
                "stored": 0,
                "error": "no_reader_safe_observation_instants_current",
                "current_rows": current_count,
            }

        rows = source.execute(
            f"""
            SELECT city, target_date, source, local_timestamp, utc_timestamp,
                   COALESCE(temp_current, running_max) AS temp_current,
                   running_max
            FROM observation_instants_current
            WHERE {OBSERVATION_READER_GATE_SQL}
              AND COALESCE(temp_current, running_max) IS NOT NULL
              AND is_missing_local_hour = 0
              AND is_ambiguous_local_hour = 0
            ORDER BY city, target_date, source, utc_timestamp
            """
        ).fetchall()
    finally:
        source.close()

    print(
        f"Source: {safe_count:,} reader-safe observation_instants_current "
        f"rows ({current_count:,} current rows before gate)"
    )
    print(
        f"Using {len(rows):,} non-missing, non-ambiguous "
        f"reader-safe observation_instants_current rows for diurnal aggregation"
    )

    canonical_day_hour = defaultdict(list)
    for row in rows:
        city = str(row["city"])
        target_date = str(row["target_date"])
        hour = _obs_hour(str(row["local_timestamp"]))
        canonical_day_hour[(city, target_date, hour)].append(
            {
                "temp_current": float(row["temp_current"]),
                "running_max": float(row["running_max"]) if row["running_max"] is not None else None,
            }
        )

    grouped = defaultdict(list)
    seasonal_high_set = defaultdict(list)
    monthly_high_set = defaultdict(list)
    per_day = defaultdict(list)

    for (city, target_date, hour), source_samples in canonical_day_hour.items():
        season = season_from_date(target_date, city_name=city)
        month = int(target_date.split("-")[1])
        temp = float(np.mean([sample["temp_current"] for sample in source_samples]))
        running_max_candidates = [
            sample["running_max"] if sample["running_max"] is not None else sample["temp_current"]
            for sample in source_samples
        ]
        running_max = max(running_max_candidates)

        grouped[(city, season, hour)].append(temp)
        per_day[(city, target_date)].append(
            {
                "hour": hour,
                "month": month,
                "season": season,
                "temp_current": temp,
                "running_max": running_max,
            }
        )

    for (city, target_date), samples in per_day.items():
        # p_high_set SEMANTICS FIX (2026-06-10, adversarial review finding 3):
        # "P(daily high already set by hour h)" requires the CUMULATIVE running
        # max through hour h, not the hour-h bucket max. The bucket-max version
        # computed P(peak occurs near hour h) — PMF-shaped, ~0 in the evening —
        # which inverted the day0 maturity gate's behavior in both directions.
        # cum_max(h) is monotone non-decreasing, so the per-day indicator
        # 1[cum_max(h) == daily_max] is a monotone step function (survival
        # shape), exactly what the gate's post_peak_confidence assumes.
        #
        # FULL-DAY COVERAGE GATE: a day whose sampling stops early (e.g. last
        # bucket 14:00 local) has an UNDERSTATED final_high, which inflates
        # early-hour p_high_set (the truncated 'daily max' is reached early) —
        # exactly the pre-peak-authority hazard the gate must not re-acquire.
        # Only days observed through late evening (>= 21:00 local) and from
        # early morning (<= 09:00 local) define a trustworthy daily max.
        hours_present = {int(sample["hour"]) for sample in samples}
        if max(hours_present) < 21 or min(hours_present) > 9:
            continue
        season_by_hour = {int(s["hour"]): str(s["season"]) for s in samples}
        month_by_hour = {int(s["hour"]): int(s["month"]) for s in samples}
        for hour, high_set in _cumulative_high_set_indicators(samples):
            seasonal_high_set[(city, season_by_hour[hour], hour)].append(high_set)
            monthly_high_set[(city, month_by_hour[hour], hour)].append(high_set)

    seasonal_means = defaultdict(dict)
    for (city, season, hour), obs in seasonal_high_set.items():
        if obs:
            seasonal_means[(city, season)][int(hour)] = float(np.mean(obs))
    seasonal_iso = {
        key: _isotonic_by_hour(means) for key, means in seasonal_means.items()
    }

    curve_rows: list[tuple] = []
    for (city, season, hour), temps in sorted(grouped.items()):
        if len(temps) < 5:
            continue
        arr = np.array(temps, dtype=float)
        p_high_set = seasonal_iso.get((city, season), {}).get(int(hour))
        curve_rows.append(
            (
                city,
                season,
                hour,
                float(arr.mean()),
                float(arr.std()),
                len(temps),
                p_high_set,
            )
        )

    monthly_means = defaultdict(dict)
    monthly_counts = defaultdict(dict)
    for (city, month, hour), obs in monthly_high_set.items():
        if len(obs) < 5:
            continue
        monthly_means[(city, month)][int(hour)] = float(np.mean(obs))
        monthly_counts[(city, month)][int(hour)] = len(obs)

    peak_rows: list[tuple] = []
    for (city, month), means in sorted(monthly_means.items()):
        iso = _isotonic_by_hour(means)
        for hour in sorted(iso):
            peak_rows.append(
                (
                    city,
                    month,
                    hour,
                    iso[hour],
                    monthly_counts[(city, month)][hour],
                )
            )

    # Open the writer only after the read snapshot and all aggregation are done.
    # The live DB is boot-initialized; repeating init_schema here can hold
    # WORLD's single WAL writer for minutes.
    zeus = get_write_connection(
        write_class="bulk",
        busy_timeout_ms=ETL_WORLD_WRITE_BUSY_TIMEOUT_MS,
    )
    try:
        zeus.execute("BEGIN IMMEDIATE")
        zeus.execute("DELETE FROM diurnal_curves")
        zeus.execute("DELETE FROM diurnal_peak_prob")
        zeus.executemany(
            """
            INSERT OR REPLACE INTO diurnal_curves
            (city, season, hour, avg_temp, std_temp, n_samples, p_high_set)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            curve_rows,
        )
        zeus.executemany(
            """
            INSERT OR REPLACE INTO diurnal_peak_prob
            (city, month, hour, p_high_set, n_obs)
            VALUES (?, ?, ?, ?, ?)
            """,
            peak_rows,
        )
        zeus.commit()
        stored = len(curve_rows)
        monthly_rows = len(peak_rows)
    except BaseException:
        zeus.rollback()
        raise
    finally:
        zeus.close()

    print(f"\nStored {stored} diurnal curve entries and {monthly_rows} monthly probability rows")
    return {"stored": stored, "monthly_rows": monthly_rows}


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
