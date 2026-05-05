#!/usr/bin/env python3
# Lifecycle: created=2026-04-16; last_reviewed=2026-05-04; last_reused=2026-05-04
# Purpose: Bridge oracle shadow snapshots into the reviewed oracle error-rate config artifact.
# Reuse: Review source snapshots and high-track settlement filtering before applying output.
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A2 (storage path centralization + atomic write + heartbeat).
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

# Storage paths centralized in src.state.paths (PLAN.md §A2 + D-10).
# Re-resolved on each call so ZEUS_STORAGE_ROOT env override propagates
# into the bridge without reimport. Kept as module-level callables for
# readability inside the existing single-file procedural style.
from src.state.paths import (  # noqa: E402  (path-bootstrap above must run first)
    oracle_artifact_heartbeat_path,
    oracle_error_rates_path,
    oracle_snapshot_dir,
    write_heartbeat,
    write_json_atomic,
)

DB_PATH = ROOT / "state" / "zeus-world.db"


def _load_settlements(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """Load all VERIFIED settlements keyed by (city, target_date)."""
    # 2026-05-05 (architect): LOW track is intentionally NOT bridged here.
    # Load-bearing fail-closed gate is src/strategy/oracle_penalty.py:472 — LOW
    # metric routes to OracleStatus.METRIC_UNSUPPORTED → multiplier 0.0 → no LOW
    # Kelly bets execute. LOW bridge would require upstream `_snapshot_daily_low`
    # listener (HKO CLMMINT, WU daily_low_f) that does not exist yet. HKO LOW
    # rounding/timing semantics differ from CLMMAXT — mirror-HIGH symmetry is
    # unsafe without dedicated listener audit. Revisit when LOW listener PR ships.
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
        logger.info("No shadow snapshots found in %s", oracle_snapshot_dir())
        conn.close()
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
              AND authority = 'VERIFIED'
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
    oracle_file = oracle_error_rates_path()
    existing: dict[str, dict] = {}
    if oracle_file.exists():
        with open(oracle_file) as f:
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
    from src.strategy.oracle_penalty import summarize_oracle_posterior

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
        city_entry["high"]["n"] = n
        city_entry["high"]["mismatches"] = m
        city_entry["high"]["last_observed_date"] = (
            max(snap_stats["snapshot_dates"]) if snap_stats.get("snapshot_dates") else None
        )

        # Keep oracle_error_rate as a derived convenience field — readers
        # compute their own posterior, but operators still grep for the
        # raw rate when triaging. ``error_rate = m/n`` is the maximum-
        # likelihood estimate; the posterior_mean lives in the reader.
        snap_rate = snap_stats["snapshot_error_rate"]
        city_entry["high"]["oracle_error_rate"] = round(snap_rate, 4)
        posterior = summarize_oracle_posterior(
            n=n,
            mismatches=m,
            metric="high",
            source_role="oracle_shadow_snapshot",
            last_date=city_entry["high"]["last_observed_date"] or "",
            city=city_name,
        )
        city_entry["high"].update({
            "metric": "high",
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
        if n < 10:
            city_entry["high"]["status_hint"] = "INSUFFICIENT_SAMPLE"
        elif m == 0:
            city_entry["high"]["status_hint"] = "OK_pending_p95"
        elif snap_rate > 0.10:
            city_entry["high"]["status_hint"] = "BLACKLIST"
        elif snap_rate > 0.03:
            city_entry["high"]["status_hint"] = "CAUTION"
        else:
            city_entry["high"]["status_hint"] = "INCIDENTAL"
        # Drop the old top-level "status" field; the reader's classify()
        # is the authority. Keep a one-cycle compat shim so anything
        # ad-hoc reading the JSON doesn't crash on missing key.
        city_entry["high"]["status"] = city_entry["high"]["status_hint"]

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
