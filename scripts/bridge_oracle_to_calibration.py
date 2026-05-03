#!/usr/bin/env python3
# Lifecycle: created=2026-04-16; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Bridge oracle shadow snapshots into the reviewed oracle error-rate config artifact.
# Reuse: Review source snapshots and high-track settlement filtering before applying output.
"""Bridge oracle shadow snapshots to calibration data.

Compares oracle-time WU/HKO snapshots (captured by
``oracle_snapshot_listener.py``) against PM settlement values, then
updates ``data/oracle_error_rates.json`` with fresh per-city error
rates.

This script is the ONLY writer to oracle_error_rates.json and the ONLY
reader of oracle shadow snapshots.  It bridges the shadow storage layer
to the evaluator's oracle penalty system without touching zeus-world.db.

Usage:
    .venv/bin/python scripts/bridge_oracle_to_calibration.py [--dry-run]

Architecture:
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

# Fitz Rule: Authority before reuse. Scripts must import existing laws.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# _MIN_HOURS_PER_DAY = 22
from scripts.fill_obs_v2_dst_gaps import _MIN_HOURS_PER_DAY
from src.data.tier_resolver import (
    allowed_sources_for_city,
    expected_source_for_city,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("oracle_bridge")

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "raw" / "oracle_shadow_snapshots"
ORACLE_FILE = ROOT / "data" / "oracle_error_rates.json"
DB_PATH = ROOT / "state" / "zeus-world.db"


def _load_settlements(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """Load all VERIFIED settlements keyed by (city, target_date)."""
    rows = conn.execute("""
        SELECT city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
               settlement_source_type, unit
        FROM settlements
        WHERE authority = 'VERIFIED'
          AND temperature_metric = 'high'
    """).fetchall()
    result = {}
    for r in rows:
        result[(r[0], r[1])] = {
            "value": r[2],
            "bin_lo": r[3],
            "bin_hi": r[4],
            "source_type": r[5],
            "unit": r[6],
        }
    return result


def _load_snapshots() -> dict[str, dict[str, dict]]:
    """Load all shadow snapshots, keyed by city → date → snapshot."""
    result: dict[str, dict[str, dict]] = defaultdict(dict)
    if not SNAPSHOT_DIR.exists():
        return result

    for city_dir in sorted(SNAPSHOT_DIR.iterdir()):
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


def _in_bin(value: float, bin_lo: float | None, bin_hi: float | None) -> bool:
    """Check if a value falls within PM settlement bin."""
    if bin_lo is not None and value < bin_lo:
        return False
    if bin_hi is not None and value > bin_hi:
        return False
    return True


def bridge(dry_run: bool = False) -> dict:
    """Run the bridge: compare snapshots vs settlements, update error rates.

    Returns summary stats.
    """
    conn = sqlite3.connect(str(DB_PATH))
    settlements = _load_settlements(conn)

    snapshots = _load_snapshots()
    if not snapshots:
        logger.info("No shadow snapshots found in %s", SNAPSHOT_DIR)
        return {"cities": 0, "comparisons": 0}

    # Coverage check helper
    def _get_day_coverage(city: str, target_date: str) -> tuple[int, int]:
        """Return (primary_hours, max_fallback_hours)."""
        primary_source = expected_source_for_city(city)
        allowed_sources = allowed_sources_for_city(city)
        fallback_sources = [s for s in allowed_sources if s != primary_source]

        # Count distinct hours for primary source
        p_count = conn.execute("""
            SELECT COUNT(DISTINCT utc_timestamp)
            FROM observation_instants_v2
            WHERE city = ? AND target_date = ? AND source = ?
        """, (city, target_date, primary_source)).fetchone()[0]

        # Count distinct hours for fallbacks (if primary is too thin)
        f_max = 0
        if p_count < _MIN_HOURS_PER_DAY and fallback_sources:
            placeholders = ",".join(["?"] * len(fallback_sources))
            f_max = conn.execute(f"""
                SELECT MAX(h) FROM (
                    SELECT COUNT(DISTINCT utc_timestamp) as h
                    FROM observation_instants_v2
                    WHERE city = ? AND target_date = ? AND source IN ({placeholders})
                      AND authority = 'VERIFIED'
                    GROUP BY source
                )
            """, (city, target_date, *fallback_sources)).fetchone()[0] or 0

        return p_count, f_max

    # Existing oracle error rates (to preserve historical data)
    existing: dict[str, dict] = {}
    if ORACLE_FILE.exists():
        with open(ORACLE_FILE) as f:
            existing = json.load(f)

    city_stats: dict[str, dict] = {}

    for city_name, date_snaps in sorted(snapshots.items()):
        matches = 0
        mismatches = 0
        skipped_low_coverage = 0
        mismatch_dates = []
        dates_compared = []

        for target_date, snap in sorted(date_snaps.items()):
            key = (city_name, target_date)
            if key not in settlements:
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

            # Convert WU °F snapshot to °C if settlement is °C
            snap_val = snap_high
            if settle["unit"] == "C" and snap.get("source") == "wu_icao_history":
                # WU returns °F, need to convert to integer °C
                # DANGER: oracle_truncate — PM's UMA voters use floor()
                # for decimal °C (truncation bias). 仅限 oracle 对比使用！
                import math
                snap_val = (snap_high - 32) * 5 / 9
                snap_val = math.floor(snap_val)  # oracle_truncate semantics

            in_bin = _in_bin(
                snap_val,
                settle["bin_lo"],
                settle["bin_hi"],
            )

            dates_compared.append(target_date)
            if in_bin:
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
            city_stats[city_name] = {
                "snapshot_comparisons": total,
                "snapshot_match": matches,
                "snapshot_mismatch": mismatches,
                "skipped_low_coverage": skipped_low_coverage,
                "snapshot_error_rate": round(error_rate, 4),
                "snapshot_mismatch_dates": mismatch_dates,
                "snapshot_dates": dates_compared,
            }
            logger.info(
                "%s: %d/%d match, %d skipped (error=%.1f%%)",
                city_name, matches, total, skipped_low_coverage, error_rate * 100,
            )

    # Merge snapshot results into existing oracle error rates.
    # S2 R4 P10B: write nested {city: {high: {...}, low: {...}}} shape.
    # This bridge only measures HIGH track (daily_high snapshots), so only
    # the "high" subkey is updated here. LOW starts empty and is populated
    # when LOW oracle snapshot infrastructure is added (future phase).
    for city_name, snap_stats in city_stats.items():
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

        # Ensure "high" subkey exists
        if "high" not in city_entry:
            city_entry["high"] = {}

        city_entry["high"]["snapshot_data"] = snap_stats

        # Combine snapshot error rate with historical same-source error rate
        hist_rate = city_entry["high"].get("oracle_error_rate", 0.0)
        snap_rate = snap_stats["snapshot_error_rate"]

        # Use the higher of the two as the effective oracle_error_rate
        combined_rate = max(hist_rate, snap_rate)
        city_entry["high"]["oracle_error_rate"] = combined_rate

        # Update status
        if combined_rate > 0.10:
            city_entry["high"]["status"] = "BLACKLIST"
        elif combined_rate > 0.03:
            city_entry["high"]["status"] = "CAUTION"
        elif combined_rate > 0.0:
            city_entry["high"]["status"] = "INCIDENTAL"
        else:
            city_entry["high"]["status"] = "OK"

    if not dry_run:
        ORACLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ORACLE_FILE, "w") as f:
            json.dump(existing, f, indent=2)
        logger.info("Updated %s with %d snapshot cities", ORACLE_FILE, len(city_stats))

        # Signal the oracle penalty module to reload
        try:
            from src.strategy.oracle_penalty import reload
            reload()
        except ImportError:
            pass  # OK if not running inside Zeus process
    else:
        logger.info("[DRY RUN] Would update %s with %d cities", ORACLE_FILE, len(city_stats))

    conn.close()
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
