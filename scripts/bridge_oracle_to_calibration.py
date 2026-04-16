#!/usr/bin/env python3
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
    conn.close()

    snapshots = _load_snapshots()
    if not snapshots:
        logger.info("No shadow snapshots found in %s", SNAPSHOT_DIR)
        return {"cities": 0, "comparisons": 0}

    # Existing oracle error rates (to preserve historical data)
    existing: dict[str, dict] = {}
    if ORACLE_FILE.exists():
        with open(ORACLE_FILE) as f:
            existing = json.load(f)

    city_stats: dict[str, dict] = {}

    for city_name, date_snaps in sorted(snapshots.items()):
        matches = 0
        mismatches = 0
        mismatch_dates = []
        dates_compared = []

        for target_date, snap in sorted(date_snaps.items()):
            key = (city_name, target_date)
            if key not in settlements:
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
                "snapshot_error_rate": round(error_rate, 4),
                "snapshot_mismatch_dates": mismatch_dates,
                "snapshot_dates": dates_compared,
            }
            logger.info(
                "%s: %d/%d match (error=%.1f%%)",
                city_name, matches, total, error_rate * 100,
            )

    # Merge snapshot results into existing oracle error rates
    for city_name, snap_stats in city_stats.items():
        if city_name not in existing:
            existing[city_name] = {}
        existing[city_name]["snapshot_data"] = snap_stats

        # Combine snapshot error rate with historical same-source error rate
        hist_rate = existing[city_name].get("oracle_error_rate", 0.0)
        snap_rate = snap_stats["snapshot_error_rate"]

        # Use the higher of the two as the effective oracle_error_rate
        combined_rate = max(hist_rate, snap_rate)
        existing[city_name]["oracle_error_rate"] = combined_rate

        # Update status
        if combined_rate > 0.10:
            existing[city_name]["status"] = "BLACKLIST"
        elif combined_rate > 0.03:
            existing[city_name]["status"] = "CAUTION"
        elif combined_rate > 0.0:
            existing[city_name]["status"] = "INCIDENTAL"
        else:
            existing[city_name]["status"] = "OK"

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
