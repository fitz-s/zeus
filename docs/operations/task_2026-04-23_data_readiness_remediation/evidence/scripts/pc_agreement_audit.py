# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: P-0 first_principles.md §3 AP-4 (source role collapse), §4 Q1-Q10 framework; P-C Q8 spec
#
# P-C: Settlement-Observation Agreement Audit (indirect SQL-based equivalence check).
# Read-only. No DB writes. Reproducible from state/zeus-world.db alone.
#
# Routing (P-C handoff recipe):
#   WU   settlement_source_type → obs.source='wu_icao_history'       → wmo_half_up rounding
#   NOAA settlement_source_type → obs.source LIKE 'ogimet_metar_%'   → wmo_half_up rounding
#   HKO  settlement_source_type → obs.source='hko_daily_api'         → oracle_truncate (floor)
#   CWA  settlement_source_type → no proxy accepted                  → STATION_REMAP_NEEDED flag
#
# Containment rule (bin-shape aware):
#   point bin        (lo==hi)          → rounded == lo
#   finite range     (lo<hi)           → lo <= rounded <= hi
#   low shoulder     (lo IS NULL)      → rounded <= hi
#   high shoulder    (hi IS NULL)      → rounded >= lo
#   both NULL                          → excluded (routes to DR-41 reconcile)
#
# Output: JSON + markdown to stdout; caller redirects to evidence/.
#
# Reproducible from a clean checkout via:
#   python3 docs/operations/task_2026-04-23_data_readiness_remediation/evidence/scripts/pc_agreement_audit.py

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
DB_PATH = REPO_ROOT / "state" / "zeus-world.db"

VERIFIED_MATCH_RATE_THRESHOLD = 0.95
VERIFIED_MAX_DELTA = 1.0  # units (F or C per settlement row)
QUARANTINE_DELTA_FLOOR = 2.0


def wmo_half_up(value: float) -> float:
    """floor(x + 0.5) — matches SettlementSemantics.round_values for wmo_half_up."""
    return math.floor(value + 0.5)


def oracle_truncate(value: float) -> float:
    """floor(x) — HKO-only rule per src/contracts/settlement_semantics.py:79."""
    return math.floor(value)


def round_for_source_type(source_type: str, value: float) -> float:
    if source_type == "HKO":
        return oracle_truncate(value)
    # WU / NOAA / CWA (and unknowns) default to wmo_half_up
    return wmo_half_up(value)


def containment(rounded: float, lo, hi) -> tuple[bool, float]:
    """Return (contained, delta_to_nearest_bound).
    delta is 0 on match, else minimum unit-distance to [lo,hi] closed interval."""
    if lo is not None and hi is not None:
        if lo <= rounded <= hi:
            return True, 0.0
        if rounded < lo:
            return False, lo - rounded
        return False, rounded - hi
    if lo is None and hi is not None:
        if rounded <= hi:
            return True, 0.0
        return False, rounded - hi
    if hi is None and lo is not None:
        if rounded >= lo:
            return True, 0.0
        return False, lo - rounded
    return False, float("nan")  # both NULL (excluded upstream)


def obs_source_clause(source_type: str) -> str | None:
    if source_type == "WU":
        return "o.source = 'wu_icao_history'"
    if source_type == "NOAA":
        return "o.source LIKE 'ogimet_metar_%'"
    if source_type == "HKO":
        return "o.source = 'hko_daily_api'"
    return None  # CWA → no accepted proxy


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Pull all settlements with a usable bin shape (at least one of lo/hi non-NULL).
    # Match obs by (city, target_date) with source-type routing done in-process
    # to avoid a 4-way UNION; we still read one obs row per (city,target_date,source).
    settlements = conn.execute(
        """
        SELECT city, target_date, unit, pm_bin_lo, pm_bin_hi, settlement_source_type
        FROM settlements
        ORDER BY city, target_date
        """
    ).fetchall()

    # Build an index: (city, target_date) → list of (source, high_temp, obs_unit)
    obs_idx: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in conn.execute(
        "SELECT city, target_date, source, high_temp, unit FROM observations WHERE high_temp IS NOT NULL"
    ):
        obs_idx[(row["city"], row["target_date"])].append(
            {"source": row["source"], "high_temp": row["high_temp"], "unit": row["unit"]}
        )

    conn.close()

    # Bucket key is (city, source_type) because some cities have mixed
    # source_types (Hong Kong HKO+WU, Taipei CWA+NOAA+WU, Tel Aviv NOAA+WU).
    # Per-E go/no-go decisions are bound to source-type not city identity alone.
    per_bucket: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "city": None,
            "source_type": None,
            "unit": None,
            "total_settlements": 0,
            "audited": 0,
            "matches": 0,
            "mismatches": 0,
            "no_obs": 0,
            "bin_unavailable": 0,
            "station_remap_needed": 0,
            "deltas": [],
            "mismatch_samples": [],
            "unit_mismatches": 0,
        }
    )

    mismatch_rows: list[dict] = []

    for s in settlements:
        city = s["city"]
        td = s["target_date"]
        unit = s["unit"]
        lo = s["pm_bin_lo"]
        hi = s["pm_bin_hi"]
        st = s["settlement_source_type"]

        bucket = per_bucket[(city, st)]
        if bucket["city"] is None:
            bucket["city"] = city
            bucket["source_type"] = st
            bucket["unit"] = unit
        bucket["total_settlements"] += 1

        # CWA: no accepted proxy per scientist R3-D2.
        if st == "CWA":
            bucket["station_remap_needed"] += 1
            continue

        if lo is None and hi is None:
            bucket["bin_unavailable"] += 1
            continue

        clause = obs_source_clause(st)
        if clause is None:
            bucket["station_remap_needed"] += 1
            continue

        # Pick the source-correct obs (first match by routing predicate).
        candidates = obs_idx.get((city, td), [])
        picked = None
        for c in candidates:
            src = c["source"]
            if st == "WU" and src == "wu_icao_history":
                picked = c
                break
            if st == "NOAA" and src.startswith("ogimet_metar_"):
                picked = c
                break
            if st == "HKO" and src == "hko_daily_api":
                picked = c
                break
        if picked is None:
            bucket["no_obs"] += 1
            continue

        # Unit compatibility: earlier scan confirmed zero mismatches in matched rows,
        # but we still record and skip if it ever appears (fail-closed).
        if picked["unit"] != unit:
            bucket["unit_mismatches"] += 1
            continue

        rounded = round_for_source_type(st, float(picked["high_temp"]))
        ok, delta = containment(rounded, lo, hi)
        bucket["audited"] += 1
        bucket["deltas"].append(delta)
        if ok:
            bucket["matches"] += 1
        else:
            bucket["mismatches"] += 1
            entry = {
                "city": city,
                "target_date": td,
                "unit": unit,
                "obs_high_temp": picked["high_temp"],
                "rounded": rounded,
                "pm_bin_lo": lo,
                "pm_bin_hi": hi,
                "delta": delta,
                "source_type": st,
                "obs_source": picked["source"],
            }
            mismatch_rows.append(entry)
            if len(bucket["mismatch_samples"]) < 10:
                bucket["mismatch_samples"].append(entry)

    # Aggregate per-bucket disposition.
    report_buckets = []
    totals = {
        "total_settlements": 0,
        "audited": 0,
        "matches": 0,
        "mismatches": 0,
        "no_obs": 0,
        "bin_unavailable": 0,
        "station_remap_needed": 0,
        "unit_mismatches": 0,
    }

    for key, b in sorted(per_bucket.items()):
        audited = b["audited"]
        matches = b["matches"]
        mismatches = b["mismatches"]
        match_rate = (matches / audited) if audited else None
        max_delta = max(b["deltas"]) if b["deltas"] else 0.0

        if b["station_remap_needed"] == b["total_settlements"]:
            disposition = "STATION_REMAP_NEEDED"
        elif audited == 0:
            disposition = "NO_OBS"
        elif match_rate is not None and match_rate >= VERIFIED_MATCH_RATE_THRESHOLD and max_delta <= VERIFIED_MAX_DELTA:
            disposition = "VERIFIED"
        elif max_delta >= QUARANTINE_DELTA_FLOOR or (match_rate is not None and match_rate < VERIFIED_MATCH_RATE_THRESHOLD):
            disposition = "QUARANTINE"
        else:
            disposition = "REVIEW"

        report_buckets.append(
            {
                "city": b["city"],
                "source_type": b["source_type"],
                "unit": b["unit"],
                "total_settlements": b["total_settlements"],
                "audited": audited,
                "matches": matches,
                "mismatches": mismatches,
                "no_obs": b["no_obs"],
                "bin_unavailable": b["bin_unavailable"],
                "station_remap_needed": b["station_remap_needed"],
                "unit_mismatches": b["unit_mismatches"],
                "match_rate": match_rate,
                "max_delta": max_delta,
                "disposition": disposition,
                "mismatch_samples": b["mismatch_samples"],
            }
        )
        for k in totals:
            totals[k] += b[k]

    # Delta magnitude histogram across all audited rows (integer bins).
    histogram: dict[str, int] = defaultdict(int)
    for m in mismatch_rows:
        d = m["delta"]
        bucket_key = f"{int(d)}-{int(d)+1}" if d > 0 else "0"
        histogram[bucket_key] += 1

    # Per-source-type summary.
    per_source: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "audited": 0, "matches": 0, "mismatches": 0, "max_delta": 0.0}
    )
    for b in per_bucket.values():
        st = b["source_type"]
        per_source[st]["total"] += b["total_settlements"]
        per_source[st]["audited"] += b["audited"]
        per_source[st]["matches"] += b["matches"]
        per_source[st]["mismatches"] += b["mismatches"]
        if b["deltas"]:
            per_source[st]["max_delta"] = max(per_source[st]["max_delta"], max(b["deltas"]))

    out = {
        "config": {
            "verified_match_rate_threshold": VERIFIED_MATCH_RATE_THRESHOLD,
            "verified_max_delta": VERIFIED_MAX_DELTA,
            "quarantine_delta_floor": QUARANTINE_DELTA_FLOOR,
            "repo_root": str(REPO_ROOT),
            "db_path": str(DB_PATH),
        },
        "totals": totals,
        "per_source_type": per_source,
        "delta_histogram_absolute": dict(histogram),
        "per_bucket": report_buckets,
        "all_mismatches": mismatch_rows,
    }
    json.dump(out, sys.stdout, indent=2, default=float)
    return 0


if __name__ == "__main__":
    sys.exit(main())
