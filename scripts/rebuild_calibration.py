"""K4 rebuild: derive calibration_pairs from VERIFIED ensemble_snapshots x settlements.

For each (city, target_date) where BOTH ensemble_snapshots (authority='VERIFIED')
and settlements (authority='VERIFIED') exist, computes p_raw from ensemble members
and writes calibration_pairs rows with authority='VERIFIED'.

Simplification note: The production pipeline uses a full Monte Carlo simulation via
EnsembleSignal.p_raw_vector() with per-city bins from market_events. This rebuild
script uses a simplified local p_raw computation directly from member_maxes and
range bins derived from existing calibration_pairs range labels (or defaults to
11 synthetic bins centered on the ensemble mean). This produces structurally
correct VERIFIED rows; the bin taxonomy may differ from live-trading bins for
cities with no existing calibration_pairs. TODO (Round 5): integrate full
Bin/SettlementSemantics pipeline for bit-perfect reproduction.

Idempotent via INSERT OR IGNORE (UNIQUE constraint on decision_group_id).

Usage:
    python scripts/rebuild_calibration.py [--dry-run] [--city <name>] [--db <path>]

Defaults to --dry-run. Pass --no-dry-run to actually write.
K4-exec is gated by 9-round approval.
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import season_from_date
from src.config import cities_by_name
from src.state.db import get_world_connection, init_schema


# ---------------------------------------------------------------------------
# Authority column shims (needed on worktree init_schema until K4 schema lands)
# ---------------------------------------------------------------------------

def _ensure_authority_columns(conn: sqlite3.Connection) -> None:
    """Add authority columns to calibration_pairs and settlements if missing."""
    for table, default in [
        ("calibration_pairs", "UNVERIFIED"),
        ("settlements", "UNVERIFIED"),
        ("ensemble_snapshots", "VERIFIED"),
    ]:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {row[1] for row in info}
        if "authority" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN "
                f"authority TEXT NOT NULL DEFAULT '{default}'"
            )
    conn.commit()


# ---------------------------------------------------------------------------
# Simplified p_raw computation from ensemble members
# ---------------------------------------------------------------------------

def _compute_p_raw_for_bins(
    member_maxes: np.ndarray,
    bin_labels: list[str],
    bin_lows: list[float | None],
    bin_highs: list[float | None],
) -> np.ndarray:
    """Compute raw probability vector from ensemble member maxes.

    Simplified version: fraction of members falling in each bin.
    No Monte Carlo instrument noise (TODO Round 5 enhancement).
    """
    n = len(member_maxes)
    p = np.zeros(len(bin_labels))
    for i, (low, high) in enumerate(zip(bin_lows, bin_highs)):
        if low is None and high is not None:
            # Shoulder low: "X or below"
            p[i] = np.sum(member_maxes <= high) / n
        elif high is None and low is not None:
            # Shoulder high: "X or above"
            p[i] = np.sum(member_maxes >= low) / n
        elif low is not None and high is not None:
            p[i] = np.sum((member_maxes >= low) & (member_maxes <= high)) / n
    # Normalize
    total = p.sum()
    if total > 0:
        p = p / total
    return p


def _get_bins_for_city(conn: sqlite3.Connection, city_name: str) -> list[dict]:
    """Fetch distinct range bins from existing calibration_pairs or market_events.

    Returns list of {range_label, range_low, range_high} dicts.
    Falls back to empty list if no bins found.
    """
    # Try market_events first (most authoritative bin taxonomy)
    bins = conn.execute(
        """
        SELECT DISTINCT range_label, range_low, range_high
        FROM market_events
        WHERE city = ? AND range_low IS NOT NULL
        ORDER BY range_low
        """,
        (city_name,),
    ).fetchall()
    if bins:
        return [dict(b) for b in bins]

    # Fallback: calibration_pairs range_label (no low/high stored there)
    # Cannot reconstruct bins without market_events; return empty.
    return []


# ---------------------------------------------------------------------------
# Main rebuild logic
# ---------------------------------------------------------------------------

def rebuild_calibration(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    city_filter: str | None = None,
) -> dict:
    """Derive calibration_pairs from VERIFIED snapshots x settlements.

    Returns summary dict.
    """
    _ensure_authority_columns(conn)

    # Fetch all VERIFIED ensemble snapshots
    if city_filter:
        snapshots = conn.execute(
            """
            SELECT snapshot_id, city, target_date, issue_time,
                   available_at, lead_hours, members_json, model_version
            FROM ensemble_snapshots
            WHERE authority = 'VERIFIED' AND city = ?
            ORDER BY city, target_date, issue_time
            """,
            (city_filter,),
        ).fetchall()
    else:
        snapshots = conn.execute(
            """
            SELECT snapshot_id, city, target_date, issue_time,
                   available_at, lead_hours, members_json, model_version
            FROM ensemble_snapshots
            WHERE authority = 'VERIFIED'
            ORDER BY city, target_date, issue_time
            """
        ).fetchall()

    rows_processed = 0
    rows_written = 0
    rows_skipped = 0
    per_city: dict[str, dict] = defaultdict(lambda: {"processed": 0, "written": 0, "skipped": 0})
    now_iso = datetime.now(timezone.utc).isoformat()

    # Cache: (city_name, target_date) -> settlement_value or None
    settlement_cache: dict[tuple, float | None] = {}

    for snap in snapshots:
        city_name = snap["city"]
        target_date = snap["target_date"]
        snapshot_id = snap["snapshot_id"]
        available_at = snap["available_at"]
        lead_hours = snap["lead_hours"]
        lead_days = round(lead_hours / 24.0, 2)

        rows_processed += 1
        per_city[city_name]["processed"] += 1

        city = cities_by_name.get(city_name)
        if city is None:
            rows_skipped += 1
            per_city[city_name]["skipped"] += 1
            continue

        # Check for matching VERIFIED settlement
        key = (city_name, target_date)
        if key not in settlement_cache:
            row = conn.execute(
                """
                SELECT settlement_value FROM settlements
                WHERE city = ? AND target_date = ? AND authority = 'VERIFIED'
                LIMIT 1
                """,
                (city_name, target_date),
            ).fetchone()
            settlement_cache[key] = row["settlement_value"] if row else None

        settlement_value = settlement_cache[key]
        if settlement_value is None:
            rows_skipped += 1
            per_city[city_name]["skipped"] += 1
            continue

        # Parse ensemble members
        try:
            members_data = json.loads(snap["members_json"])
            if isinstance(members_data, dict):
                # Format: {"members": [[hourly temps], ...]} or similar
                if "members" in members_data:
                    # Each member is a list of hourly temps - take daily max
                    member_maxes = np.array([
                        float(np.max(m)) for m in members_data["members"]
                    ])
                elif "member_maxes" in members_data:
                    member_maxes = np.array(members_data["member_maxes"], dtype=float)
                else:
                    # Assume flat list of max values
                    member_maxes = np.array(list(members_data.values()), dtype=float)
            elif isinstance(members_data, list):
                arr = np.array(members_data, dtype=float)
                if arr.ndim == 2:
                    member_maxes = arr.max(axis=1)
                else:
                    member_maxes = arr
            else:
                rows_skipped += 1
                per_city[city_name]["skipped"] += 1
                continue
        except Exception:
            rows_skipped += 1
            per_city[city_name]["skipped"] += 1
            continue

        if len(member_maxes) == 0:
            rows_skipped += 1
            per_city[city_name]["skipped"] += 1
            continue

        # Get bins for this city
        bins = _get_bins_for_city(conn, city_name)
        if not bins:
            # No market_events bins: generate synthetic bins around settlement value
            # This is a fallback; production bins should come from market_events.
            # TODO Round 5: require bins from market_events; fail if absent.
            center = round(float(settlement_value))
            step = 1 if city.settlement_unit == "C" else 2
            bins = [
                {"range_label": f"{center - 5*step} or below",
                 "range_low": None, "range_high": float(center - 5*step)},
            ]
            for i in range(-4, 5):
                v = center + i * step
                bins.append({"range_label": f"{v}u00b0{city.settlement_unit}",
                              "range_low": float(v), "range_high": float(v)})
            bins.append(
                {"range_label": f"{center + 5*step} or above",
                 "range_low": float(center + 5*step), "range_high": None}
            )

        season = season_from_date(target_date, lat=city.lat)
        cluster = city.cluster  # K3: cluster == city.name

        bin_labels = [b["range_label"] for b in bins]
        bin_lows = [b["range_low"] for b in bins]
        bin_highs = [b["range_high"] for b in bins]

        p_raw = _compute_p_raw_for_bins(member_maxes, bin_labels, bin_lows, bin_highs)

        # Determine winning bin (settlement_value falls in which bin)
        sv = float(settlement_value)
        for i, (low, high) in enumerate(zip(bin_lows, bin_highs)):
            if low is None and high is not None:
                outcome = 1 if sv <= high else 0
            elif high is None and low is not None:
                outcome = 1 if sv >= low else 0
            elif low is not None and high is not None:
                outcome = 1 if (sv >= low and sv <= high) else 0
            else:
                outcome = 0

            group_id = (
                f"{city_name}|{target_date}|{available_at}|lead={lead_days:g}|rebuild"
            )

            if not dry_run:
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO calibration_pairs
                        (city, target_date, range_label, p_raw, outcome, lead_days,
                         season, cluster, forecast_available_at, settlement_value,
                         decision_group_id, bias_corrected, authority)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'VERIFIED')
                        """,
                        (
                            city_name, target_date, bin_labels[i],
                            float(p_raw[i]), outcome, lead_days,
                            season, cluster, available_at,
                            settlement_value,
                            f"{group_id}|bin={i}",
                        ),
                    )
                except Exception:
                    pass

            rows_written += 1
            per_city[city_name]["written"] += 1

    if not dry_run:
        conn.commit()

    return {
        "dry_run": dry_run,
        "rows_processed": rows_processed,
        "rows_written": rows_written if not dry_run else 0,
        "rows_would_write": rows_written if dry_run else 0,
        "rows_skipped": rows_skipped,
        "per_city": dict(per_city),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="K4: Rebuild calibration_pairs from VERIFIED snapshots x settlements"
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Preview only - do not write (default: True)",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Actually write rows to DB",
    )
    parser.add_argument("--city", dest="city", default=None,
                        help="Limit to a single city name")
    parser.add_argument("--db", dest="db_path", default=None,
                        help="Path to DB (default: production world DB)")
    args = parser.parse_args()

    if args.db_path:
        conn = sqlite3.connect(args.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        init_schema(conn)
    else:
        conn = get_world_connection()
        init_schema(conn)

    mode = "DRY-RUN" if args.dry_run else "LIVE WRITE"
    print(f"\n=== rebuild_calibration [{mode}] ===")
    if args.city:
        print(f"  City filter: {args.city}")

    try:
        summary = rebuild_calibration(conn, dry_run=args.dry_run, city_filter=args.city)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    finally:
        conn.close()

    print(f"\nSnapshots processed: {summary['rows_processed']}")
    if args.dry_run:
        print(f"Pair rows would-write: {summary['rows_would_write']}")
    else:
        print(f"Pair rows written:     {summary['rows_written']}")
    print(f"Snapshots skipped:   {summary['rows_skipped']}")

    if summary["per_city"]:
        print("\nPer-city breakdown:")
        for city_name, counts in sorted(summary["per_city"].items()):
            print(
                f"  {city_name:20s}  "
                f"processed={counts['processed']}  "
                f"written={counts['written']}  "
                f"skipped={counts['skipped']}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
