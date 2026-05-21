#!/usr/bin/env python3
# Lifecycle: created=2026-04-16; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Bridge canonical oracle evidence into the reviewed oracle error-rate config artifact.
# Reuse: Review canonical observation/settlement routing and metric-specific settlement filtering before applying output.
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A2; 2026-05-21 live oracle-penalty P0 wiring repair.
"""Bridge oracle evidence to calibration data.

Compares canonical verified observations and oracle-time WU/HKO shadow snapshots
against PM settlement values, then updates ``data/oracle_error_rates.json`` with
fresh per-city error rates.

This script is the ONLY writer to oracle_error_rates.json and the ONLY
reader of oracle shadow snapshots. It bridges canonical observation truth plus
the shadow storage layer to the evaluator's oracle penalty system.

Usage:
    .venv/bin/python scripts/bridge_oracle_to_calibration.py [--dry-run]

Architecture:
    settlements + world.observation_instants_v2 / world.daily_observation_revisions
                                      →  canonical daily settlement values
    oracle_snapshot_listener.py  →  raw/oracle_shadow_snapshots/{city}/{date}.json
                                           ↓
    bridge_oracle_to_calibration.py  →  data/oracle_error_rates.json
                                           ↓
    src/strategy/oracle_penalty.py  →  evaluator.py Kelly sizing
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

# Fitz Rule: Authority before reuse. Scripts must import existing laws.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# _MIN_HOURS_PER_DAY = 22
from scripts.fill_obs_v2_dst_gaps import _MIN_HOURS_PER_DAY
_SHARED_CITY_ORACLE_SOURCE_ROLE = "shared_city_oracle_source_proxy"
from src.data.tier_resolver import (
    allowed_sources_for_city,
    expected_source_for_city,
)
from src.contracts.settlement_semantics import SettlementSemantics
from src.config import runtime_cities_by_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("oracle_bridge")

ROOT = Path(__file__).resolve().parent.parent

# Storage paths centralized in src.state.paths (PLAN.md §A2 + D-10).
# Re-resolved on each call so ZEUS_STORAGE_ROOT env override propagates
# into the bridge without reimport. Kept as module-level callables for
# readability inside the existing single-file procedural style.
from src.state.db import (  # noqa: E402  (path-bootstrap above must run first)
    get_forecasts_connection_with_world,
)
from src.state.paths import (  # noqa: E402  (path-bootstrap above must run first)
    oracle_artifact_heartbeat_path,
    oracle_error_rates_path,
    oracle_snapshot_dir,
    write_heartbeat,
    write_json_atomic,
)

# DB_PATH removed: settlements is forecast_class post-K1-split; use
# get_forecasts_connection_with_world() — K1 fix F40 2026-05-17


def _load_settlements(conn: sqlite3.Connection) -> dict[tuple[str, str, str], dict]:
    """Load all VERIFIED settlements keyed by (city, target_date, temperature_metric)."""
    rows = conn.execute("""
        SELECT city, target_date, temperature_metric, settlement_value, pm_bin_lo, pm_bin_hi,
               settlement_source_type, unit
        FROM settlements
        WHERE authority = 'VERIFIED'
          AND temperature_metric IN ('high', 'low')
    """).fetchall()
    result = {}
    for r in rows:
        result[(r[0], r[1], r[2])] = {
            "temperature_metric": r[2],
            "value": r[3],
            "bin_lo": r[4],
            "bin_hi": r[5],
            "source_type": str(r[6] or "").strip().lower(),
            "unit": str(r[7] or "").strip().upper(),
        }
    return result


def _load_snapshots() -> dict[str, dict[str, dict]]:
    """Load all shadow snapshots, keyed by city → date → snapshot."""
    result: dict[str, dict[str, dict]] = defaultdict(dict)
    snapshot_dir = oracle_snapshot_dir()
    if not snapshot_dir.exists():
        return result

    for city_dir in sorted(snapshot_dir.iterdir()):
        if not city_dir.is_dir():
            continue
        for snap_file in sorted(city_dir.glob("*.json")):
            try:
                with open(snap_file) as f:
                    snap = json.load(f)
                city = snap.get("city", city_dir.name)
                target = snap.get("target_date", snap_file.stem)
                result[city][target] = snap
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Bad snapshot %s: %s", snap_file, exc)
    return result


def _snapshot_daily_high(snap: dict) -> float | None:
    """Extract daily high temperature from a snapshot."""
    # WU snapshot
    if "daily_high_f" in snap:
        return snap["daily_high_f"]
    # HKO snapshot — need to parse from raw payload
    if "hko_raw_payload" in snap:
        target = snap.get("target_date", "")
        if not target:
            return None
        td = date.fromisoformat(target)
        maxt_data = snap["hko_raw_payload"].get("CLMMAXT", {}).get("data", [])
        for row in maxt_data:
            if len(row) >= 5:
                try:
                    y, m, d = int(row[0]), int(row[1]), int(row[2])
                    if (y, m, d) == (td.year, td.month, td.day) and str(row[4]) == "C":
                        return float(row[3])
                except (ValueError, TypeError):
                    pass
    return None


def _settlement_semantics_for(city_name: str, settle: dict) -> SettlementSemantics:
    """Return the canonical rounding contract for this settlement row.

    The bridge compares oracle snapshots to already-settled PM bins, so it must
    honor the settlement row's unit/source_type instead of guessing from the
    snapshot source. Current city config supplies station metadata when it
    matches the row; historical rows fall back to a minimal city-shaped object.
    """

    unit = str(settle.get("unit") or "").upper()
    source_type = str(settle.get("source_type") or "wu_icao").strip().lower()
    city = runtime_cities_by_name().get(city_name)
    if (
        city is not None
        and city.settlement_unit == unit
        and city.settlement_source_type == source_type
    ):
        return SettlementSemantics.for_city(city)

    return SettlementSemantics.for_city(
        SimpleNamespace(
            name=city_name,
            settlement_unit=unit,
            settlement_source_type=source_type,
            wu_station=getattr(city, "wu_station", city_name),
        )
    )


def _snapshot_settlement_value(city_name: str, snap: dict, settle: dict, snap_high: float) -> float:
    """Convert a snapshot daily high into the PM settlement value space."""

    snap_val = float(snap_high)
    if settle["unit"] == "C" and snap.get("source") == "wu_icao_history":
        # WU shadow snapshots store daily_high_f. Convert the physical value
        # to Celsius, then let SettlementSemantics choose WMO vs oracle rules.
        snap_val = (snap_val - 32.0) * 5.0 / 9.0

    return _settlement_semantics_for(city_name, settle).round_single(snap_val)


def _coerce_observation_to_settlement_unit(value: float, obs_unit: str, settlement_unit: str) -> float:
    """Convert canonical observation value to the settlement row's unit."""
    obs_unit = str(obs_unit or "").upper()
    settlement_unit = str(settlement_unit or "").upper()
    val = float(value)
    if obs_unit == settlement_unit or not obs_unit or not settlement_unit:
        return val
    if obs_unit == "F" and settlement_unit == "C":
        return (val - 32.0) * 5.0 / 9.0
    if obs_unit == "C" and settlement_unit == "F":
        return val * 9.0 / 5.0 + 32.0
    return val


def _daily_revision_sources_for_city(city_name: str) -> frozenset[str]:
    """Official daily-observation source tags for canonical daily evidence."""
    sources = {str(source).strip().lower() for source in allowed_sources_for_city(city_name)}
    city = runtime_cities_by_name().get(city_name)
    if city is not None and str(city.settlement_source_type or "").strip().lower() == "hko":
        sources.add("hko_daily_api")
    return frozenset(source for source in sources if source)


def _canonical_observation_daily_metric(
    conn: sqlite3.Connection,
    city_name: str,
    target_date: str,
    temperature_metric: str,
) -> dict | None:
    """Read the canonical verified daily metric from ``world.observation_instants_v2``.

    The old bridge treated sparse shadow snapshots as the authority surface,
    which made normal cities look ``INSUFFICIENT_SAMPLE`` even when the K1 DBs
    already held many verified settlement/observation days. This helper uses
    the current canonical ``world.observation_instants_v2`` table first and keeps the existing
    primary/fallback source coverage rule.
    """
    if temperature_metric not in {"high", "low"}:
        raise ValueError(f"unsupported temperature_metric={temperature_metric!r}")
    aggregate = "MAX(COALESCE(running_max, temp_current))" if temperature_metric == "high" else "MIN(COALESCE(running_min, temp_current))"
    value_key = "daily_high" if temperature_metric == "high" else "daily_low"
    primary_source = expected_source_for_city(city_name)
    allowed_sources = allowed_sources_for_city(city_name)
    source_order = [primary_source, *[s for s in allowed_sources if s != primary_source]]

    for source in source_order:
        row = conn.execute(
            """
            SELECT
                COUNT(DISTINCT utc_timestamp) AS hours,
                {aggregate} AS daily_value,
                COALESCE(
                    MAX(CASE WHEN temp_unit IS NOT NULL AND temp_unit <> '' THEN temp_unit END),
                    'F'
                ) AS temp_unit
            FROM world.observation_instants_v2
            WHERE city = ?
              AND target_date = ?
              AND source = ?
              AND authority = 'VERIFIED'
              AND COALESCE({value_column}, temp_current) IS NOT NULL
            """.format(
                aggregate=aggregate,
                value_column="running_max" if temperature_metric == "high" else "running_min",
            ),
            (city_name, target_date, source),
        ).fetchone()
        hours = int(row[0] or 0) if row is not None else 0
        daily_value = row[1] if row is not None else None
        if hours >= _MIN_HOURS_PER_DAY and daily_value is not None:
            return {
                value_key: float(daily_value),
                "source": source,
                "unit": str(row[2] or "F").upper(),
                "hours": hours,
            }
    revision_observation = _canonical_daily_observation_revision_metric(
        conn,
        city_name,
        target_date,
        temperature_metric,
    )
    if revision_observation is not None:
        return revision_observation

    daily_column = "high_temp" if temperature_metric == "high" else "low_temp"
    daily_sources = sorted(_daily_revision_sources_for_city(city_name))
    if not daily_sources:
        return None
    daily_source_placeholders = ",".join(["?"] * len(daily_sources))
    row = conn.execute(
        f"""
        SELECT {daily_column} AS daily_value,
               COALESCE(
                   MAX(CASE WHEN unit IS NOT NULL AND unit <> '' THEN unit END),
                   'F'
               ) AS temp_unit,
               MAX(source) AS source
         FROM observations
         WHERE city = ?
           AND target_date = ?
           AND lower(source) IN ({daily_source_placeholders})
           AND authority = 'VERIFIED'
           AND {daily_column} IS NOT NULL
        """,
        (city_name, target_date, *daily_sources),
    ).fetchone()
    if row is not None and row[0] is not None:
        return {
            value_key: float(row[0]),
            "source": str(row[2] or "observations"),
            "unit": str(row[1] or "F").upper(),
            "hours": 24,
        }
    return None


def _canonical_daily_observation_revision_metric(
    conn: sqlite3.Connection,
    city_name: str,
    target_date: str,
    temperature_metric: str,
) -> dict | None:
    """Read verified daily-observation revision payloads as canonical evidence."""
    if temperature_metric not in {"high", "low"}:
        raise ValueError(f"unsupported temperature_metric={temperature_metric!r}")
    value_key = "daily_high" if temperature_metric == "high" else "daily_low"
    payload_key = "high_temp" if temperature_metric == "high" else "low_temp"
    primary_source = expected_source_for_city(city_name)
    allowed_sources = _daily_revision_sources_for_city(city_name)
    source_order = [primary_source, *[s for s in allowed_sources if s != primary_source]]

    for source in source_order:
        try:
            rows = conn.execute(
                """
                SELECT incoming_row_json
                  FROM world.daily_observation_revisions
                 WHERE city = ?
                   AND target_date = ?
                   AND lower(source) = lower(?)
                 ORDER BY recorded_at DESC, id DESC
                 LIMIT 5
                """,
                (city_name, target_date, source),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            raw_payload = row[0] if row is not None else None
            if not raw_payload:
                continue
            try:
                payload = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError):
                continue
            payload_source = str(payload.get("source") or "").strip().lower()
            if payload_source and payload_source not in allowed_sources:
                continue
            if str(payload.get("authority") or "").upper() != "VERIFIED":
                continue
            daily_value = payload.get(payload_key)
            if daily_value is None:
                continue
            return {
                value_key: float(daily_value),
                "source": str(payload.get("source") or source or "daily_observation_revisions"),
                "unit": str(
                    payload.get("unit")
                    or payload.get(f"{temperature_metric}_target_unit")
                    or payload.get(f"{temperature_metric}_raw_unit")
                    or "F"
                ).upper(),
                "hours": 24,
                "source_role": "canonical_daily_observation_revisions",
            }
    return None


def _metric_observation_support(
    conn: sqlite3.Connection,
    city_name: str,
    temperature_metric: str,
) -> dict:
    """Return verified daily support for a city/metric observation history."""

    if temperature_metric not in {"high", "low"}:
        raise ValueError(f"unsupported temperature_metric={temperature_metric!r}")
    value_column = "running_max" if temperature_metric == "high" else "running_min"
    allowed_sources = sorted(allowed_sources_for_city(city_name))
    if not allowed_sources:
        return {"days": 0, "last_date": ""}
    placeholders = ",".join(["?"] * len(allowed_sources))
    rows = conn.execute(
        f"""
        SELECT target_date, MAX(hours) AS best_hours
          FROM (
                SELECT target_date, source, COUNT(DISTINCT utc_timestamp) AS hours
                  FROM world.observation_instants_v2
                 WHERE city = ?
                   AND source IN ({placeholders})
                   AND authority = 'VERIFIED'
                   AND {value_column} IS NOT NULL
                 GROUP BY target_date, source
               )
         GROUP BY target_date
        HAVING best_hours >= ?
        """,
        (city_name, *allowed_sources, _MIN_HOURS_PER_DAY),
    ).fetchall()
    dates = [str(row[0]) for row in rows if row[0]]
    revision_sources = sorted(_daily_revision_sources_for_city(city_name))
    if not revision_sources:
        revision_rows = []
    else:
        revision_source_placeholders = ",".join(["?"] * len(revision_sources))
        try:
            revision_rows = conn.execute(
                f"""
                SELECT target_date, incoming_row_json
                  FROM world.daily_observation_revisions
                 WHERE city = ?
                   AND lower(source) IN ({revision_source_placeholders})
                """,
                (city_name, *revision_sources),
            ).fetchall()
        except sqlite3.OperationalError:
            revision_rows = []
    daily_column = "high_temp" if temperature_metric == "high" else "low_temp"
    for row in revision_rows:
        try:
            payload = json.loads(row[1] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if str(payload.get("authority") or "").upper() != "VERIFIED":
            continue
        payload_source = str(payload.get("source") or "").strip().lower()
        if payload_source and payload_source not in revision_sources:
            continue
        if payload.get(daily_column) is None:
            continue
        dates.append(str(row[0]))
    dates = sorted(set(dates))
    return {"days": len(dates), "last_date": max(dates) if dates else ""}


def _canonical_observation_settlement_value(
    city_name: str,
    observation: dict,
    settle: dict,
) -> float:
    """Convert a canonical observation metric into PM settlement value space."""
    metric = str(settle.get("temperature_metric") or "high")
    value_key = "daily_low" if metric == "low" else "daily_high"
    obs_val = _coerce_observation_to_settlement_unit(
        float(observation[value_key]),
        str(observation.get("unit") or ""),
        str(settle.get("unit") or ""),
    )
    return _settlement_semantics_for(city_name, settle).round_single(obs_val)


def _in_bin(value: float, bin_lo: float | None, bin_hi: float | None) -> bool:
    """Check if a value falls within PM settlement bin."""
    if bin_lo is not None and value < bin_lo:
        return False
    if bin_hi is not None and value > bin_hi:
        return False
    return True


def _matches_verified_settlement(value: float, settle: dict) -> bool:
    """Compare a rounded observation value to the verified settlement row.

    Prefer the canonical ``settlement_value`` equality. Historical rows can
    carry PM bin labels in a display unit that does not match the settlement
    value unit, so using only ``pm_bin_lo/hi`` can manufacture mismatches for
    otherwise correct rows. Bin membership remains a fallback for shoulder rows
    or old settlements without a value.
    """
    settlement_value = settle.get("value")
    if settlement_value is not None and float(value) == float(settlement_value):
        return True
    return _in_bin(value, settle["bin_lo"], settle["bin_hi"])


def bridge(dry_run: bool = False) -> dict:
    """Run the bridge: compare snapshots vs settlements, update error rates.

    Returns summary stats.
    """
    with get_forecasts_connection_with_world() as conn:
        settlements = _load_settlements(conn)

        snapshots = _load_snapshots()
        if not snapshots:
            logger.info("No shadow snapshots found in %s", oracle_snapshot_dir())
            logger.info("Falling back to canonical observation_instants_v2 evidence")

        # Coverage check helper (closure over conn — must be inside with block)
        def _get_day_coverage(city: str, target_date: str) -> tuple[int, int]:
            """Return (primary_hours, max_fallback_hours)."""
            primary_source = expected_source_for_city(city)
            allowed_sources = allowed_sources_for_city(city)
            fallback_sources = [s for s in allowed_sources if s != primary_source]

            # Count distinct hours for primary source
            p_count = conn.execute("""
                SELECT COUNT(DISTINCT utc_timestamp)
                FROM world.observation_instants_v2
                WHERE city = ? AND target_date = ? AND source = ?
                  AND authority = 'VERIFIED'
            """, (city, target_date, primary_source)).fetchone()[0]

            # Count distinct hours for fallbacks (if primary is too thin)
            f_max = 0
            if p_count < _MIN_HOURS_PER_DAY and fallback_sources:
                placeholders = ",".join(["?"] * len(fallback_sources))
                f_max = conn.execute(f"""
                    SELECT MAX(h) FROM (
                        SELECT COUNT(DISTINCT utc_timestamp) as h
                        FROM world.observation_instants_v2
                        WHERE city = ? AND target_date = ? AND source IN ({placeholders})
                          AND authority = 'VERIFIED'
                        GROUP BY source
                    )
                """, (city, target_date, *fallback_sources)).fetchone()[0] or 0

            return p_count, f_max

        # Existing oracle error rates (to preserve historical data)
        oracle_file = oracle_error_rates_path()
        existing: dict[str, dict] = {}
        if oracle_file.exists():
            with open(oracle_file) as f:
                existing = json.load(f)

        city_stats: dict[tuple[str, str], dict] = {}

        for (city_name, target_date, temperature_metric), settle in sorted(settlements.items()):
            matches = 0
            mismatches = 0
            mismatch_dates = []
            dates_compared = []
            observation = _canonical_observation_daily_metric(
                conn,
                city_name,
                target_date,
                temperature_metric,
            )
            if observation is None:
                continue

            obs_val = _canonical_observation_settlement_value(city_name, observation, settle)
            dates_compared.append(target_date)
            if _matches_verified_settlement(obs_val, settle):
                matches += 1
            else:
                mismatches += 1
                mismatch_dates.append(target_date)
                logger.info(
                    "MISMATCH %s %s: canonical_obs=%s%s → %s, PM value=%s bin=[%s,%s]",
                    city_name,
                    target_date,
                    observation["daily_low" if temperature_metric == "low" else "daily_high"],
                    observation.get("unit", ""),
                    obs_val,
                    settle.get("value"),
                    settle["bin_lo"],
                    settle["bin_hi"],
                )

            total = matches + mismatches
            if total > 0:
                stats = city_stats.setdefault(
                    (city_name, temperature_metric),
                    {
                        "snapshot_comparisons": 0,
                        "snapshot_match": 0,
                        "snapshot_mismatch": 0,
                        "skipped_low_coverage": 0,
                        "snapshot_error_rate": 0.0,
                        "snapshot_mismatch_dates": [],
                        "snapshot_dates": [],
                        "source_role": observation.get("source_role", "canonical_observation_instants_v2"),
                        "temperature_metric": temperature_metric,
                    },
                )
                stats["snapshot_comparisons"] += total
                stats["snapshot_match"] += matches
                stats["snapshot_mismatch"] += mismatches
                stats["snapshot_mismatch_dates"].extend(mismatch_dates)
                stats["snapshot_dates"].extend(dates_compared)
                stats["snapshot_error_rate"] = round(
                    stats["snapshot_mismatch"] / stats["snapshot_comparisons"],
                    4,
                )

        for city_name, date_snaps in sorted(snapshots.items()):
            matches = 0
            mismatches = 0
            skipped_low_coverage = 0
            mismatch_dates = []
            dates_compared = []

            for target_date, snap in sorted(date_snaps.items()):
                key = (city_name, target_date, "high")
                if key not in settlements:
                    continue
                if (city_name, "high") in city_stats and target_date in set(city_stats[(city_name, "high")].get("snapshot_dates", [])):
                    continue

                # S2 R4 P10C: Coverage filter. Ignore thin days to keep oracle stats clean.
                p_hours, f_hours = _get_day_coverage(city_name, target_date)
                if p_hours < _MIN_HOURS_PER_DAY and f_hours < _MIN_HOURS_PER_DAY:
                    skipped_low_coverage += 1
                    logger.info(
                        "SKIP_LOW_COVERAGE %s %s: primary_h=%d, fallback_max_h=%d (threshold=%d)",
                        city_name, target_date, p_hours, f_hours, _MIN_HOURS_PER_DAY,
                    )
                    continue

                settle = settlements[key]
                snap_high = _snapshot_daily_high(snap)
                if snap_high is None:
                    continue

                snap_val = _snapshot_settlement_value(
                    city_name,
                    snap,
                    settle,
                    snap_high,
                )

                dates_compared.append(target_date)
                if _matches_verified_settlement(snap_val, settle):
                    matches += 1
                else:
                    mismatches += 1
                    mismatch_dates.append(target_date)
                    logger.info(
                        "MISMATCH %s %s: snapshot=%s → %s, PM bin=[%s,%s]",
                        city_name, target_date, snap_high, snap_val,
                        settle["bin_lo"], settle["bin_hi"],
                    )

            total = matches + mismatches
            if total > 0:
                error_rate = mismatches / total
                stats = city_stats.setdefault(
                    (city_name, "high"),
                    {
                        "snapshot_comparisons": 0,
                        "snapshot_match": 0,
                        "snapshot_mismatch": 0,
                        "skipped_low_coverage": 0,
                        "snapshot_error_rate": 0.0,
                        "snapshot_mismatch_dates": [],
                        "snapshot_dates": [],
                        "source_role": "oracle_shadow_snapshot",
                        "temperature_metric": "high",
                    },
                )
                stats["snapshot_comparisons"] += total
                stats["snapshot_match"] += matches
                stats["snapshot_mismatch"] += mismatches
                stats["skipped_low_coverage"] += skipped_low_coverage
                stats["snapshot_error_rate"] = round(
                    stats["snapshot_mismatch"] / stats["snapshot_comparisons"],
                    4,
                )
                stats["snapshot_mismatch_dates"].extend(mismatch_dates)
                stats["snapshot_dates"].extend(dates_compared)
                if stats.get("source_role") != "canonical_observation_instants_v2":
                    stats["source_role"] = "oracle_shadow_snapshot"
                logger.info(
                    "%s: %d/%d match, %d skipped (error=%.1f%%)",
                    city_name, matches, total, skipped_low_coverage, error_rate * 100,
                )

        # LOW daily observations are populated for normal cities even when the
        # settlement table has not yet accumulated enough LOW Polymarket rows
        # for a direct mismatch series. Oracle reliability is a city/source
        # property; a city with HIGH settlement-comparison evidence and verified
        # LOW observation coverage should be penalty/no-penalty, not MISSING.
        for (city_name, metric), high_stats in list(city_stats.items()):
            if metric != "high" or (city_name, "low") in city_stats:
                continue
            support = _metric_observation_support(conn, city_name, "low")
            if int(support.get("days") or 0) < 10:
                continue
            city_stats[(city_name, "low")] = {
                **high_stats,
                "source_role": _SHARED_CITY_ORACLE_SOURCE_ROLE,
                "temperature_metric": "low",
                "source_metric": "high",
                "observation_support_days": int(support["days"]),
                "observation_last_date": str(support.get("last_date") or ""),
            }

        # Merge results into existing oracle error rates.
        # S2 R4 P10B: write nested {city: {high: {...}, low: {...}}} shape.
        # Canonical observation evidence is metric-specific and covers both
        # HIGH and LOW; shadow snapshots remain HIGH-only fallback evidence.
        from src.strategy.oracle_penalty import summarize_oracle_posterior

        for (city_name, metric), snap_stats in city_stats.items():
            if city_name not in existing:
                existing[city_name] = {}

            # Migrate legacy flat structure to nested on first write
            city_entry = existing[city_name]
            if "oracle_error_rate" in city_entry and "high" not in city_entry:
                # Legacy flat: promote to nested "high" subkey
                legacy_rate = city_entry.pop("oracle_error_rate", 0.0)
                legacy_status = city_entry.pop("status", "OK")
                legacy_snap_data = city_entry.pop("snapshot_data", {})
                city_entry["high"] = {
                    "oracle_error_rate": legacy_rate,
                    "status": legacy_status,
                    "snapshot_data": legacy_snap_data,
                }

            # Ensure metric subkey exists.
            if metric not in city_entry:
                city_entry[metric] = {}

            city_entry[metric]["snapshot_data"] = snap_stats

            # PLAN.md §A3: write raw counts at the top level so the reader
            # can compute the Beta-binomial posterior. Pre-A3 the bridge
            # wrote only `oracle_error_rate` (point estimate), losing the
            # n/m split needed for evidence-graded classification. The
            # downstream reader (oracle_penalty) now treats absence of n/m
            # as MISSING (mult 0.5) — files that bridge wrote pre-A3 will
            # carry only oracle_error_rate and degrade until the next bridge
            # run.
            n = int(snap_stats["snapshot_comparisons"])
            m = int(snap_stats["snapshot_mismatch"])
            city_entry[metric]["n"] = n
            city_entry[metric]["mismatches"] = m
            city_entry[metric]["last_observed_date"] = (
                max(snap_stats["snapshot_dates"]) if snap_stats.get("snapshot_dates") else None
            )

            # Keep oracle_error_rate as a derived convenience field — readers
            # compute their own posterior, but operators still grep for the
            # raw rate when triaging. ``error_rate = m/n`` is the maximum-
            # likelihood estimate; the posterior_mean lives in the reader.
            snap_rate = snap_stats["snapshot_error_rate"]
            city_entry[metric]["oracle_error_rate"] = round(snap_rate, 4)
            posterior = summarize_oracle_posterior(
                n=n,
                mismatches=m,
                metric=metric,
                source_role=snap_stats.get("source_role", "oracle_shadow_snapshot"),
                last_date=city_entry[metric]["last_observed_date"] or "",
                city=city_name,
            )
            city_entry[metric].update({
                "metric": metric,
                "source_role": posterior.source_role,
                "posterior_mean": round(posterior.posterior_mean, 6),
                "posterior_upper_95": round(posterior.posterior_upper_95, 6),
                "posterior_prob_gt_03": round(posterior.posterior_prob_gt_03, 6),
                "posterior_prob_gt_10": round(posterior.posterior_prob_gt_10, 6),
                "penalty_multiplier": round(posterior.penalty_multiplier, 6),
            })

            # Status field is now informational. The reader recomputes
            # status via oracle_estimator.classify(m, n, age) on each
            # `get_oracle_info` call — operators changing thresholds in code
            # should NOT need a bridge re-run. We still emit a status hint
            # for human readability of the JSON dump.
            city_entry[metric]["status_hint"] = posterior.status.value
            # Drop the old top-level "status" field; the reader's classify()
            # is the authority. Keep a one-cycle compat shim so anything
            # ad-hoc reading the JSON doesn't crash on missing key.
            city_entry[metric]["status"] = city_entry[metric]["status_hint"]
            for support_key in (
                "source_metric",
                "observation_support_days",
                "observation_last_date",
            ):
                if support_key in snap_stats:
                    city_entry[metric][support_key] = snap_stats[support_key]

        if not dry_run:
            # Atomic write + heartbeat (PLAN.md §A2 + D-10). The previous
            # plain open()+json.dump could leave a partial file on crash;
            # the reader (oracle_penalty.reload) catches that as a JSON error
            # and silently keeps the previous cache, masking the bridge crash.
            # Atomic + heartbeat surfaces the failure mode for §A3 readers.
            meta = write_json_atomic(oracle_file, existing, writer_identity="bridge_oracle_to_calibration")
            write_heartbeat(
                "oracle_error_rates",
                {
                    **meta,
                    "snapshot_cities": len(city_stats),
                    "comparisons": sum(s["snapshot_comparisons"] for s in city_stats.values()),
                    "mismatches": sum(s["snapshot_mismatch"] for s in city_stats.values()),
                },
                heartbeat_path=oracle_artifact_heartbeat_path(),
            )
            logger.info("Updated %s with %d snapshot cities (sha256=%s)",
                        oracle_file, len(city_stats), meta["sha256"][:12])

            # Signal the oracle penalty module to reload
            try:
                from src.strategy.oracle_penalty import reload
                reload()
            except ImportError:
                pass  # OK if not running inside Zeus process
        else:
            logger.info("[DRY RUN] Would update %s with %d cities", oracle_file, len(city_stats))

        return {
            "cities": len(city_stats),
            "comparisons": sum(s["snapshot_comparisons"] for s in city_stats.values()),
            "mismatches": sum(s["snapshot_mismatch"] for s in city_stats.values()),
        }


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    stats = bridge(dry_run=dry_run)
    logger.info(
        "Bridge complete: %d cities, %d comparisons, %d mismatches",
        stats["cities"], stats["comparisons"], stats.get("mismatches", 0),
    )
