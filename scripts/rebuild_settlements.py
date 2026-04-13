"""K4 rebuild: derive settlements from VERIFIED observations.

For each (city, target_date) where authority='VERIFIED' in observations,
applies SettlementSemantics.for_city(city).assert_settlement_value(high_temp)
and writes the result to settlements with authority='VERIFIED'.

Idempotent: re-running produces the same rows via INSERT OR REPLACE.

Usage:
    python scripts/rebuild_settlements.py [--dry-run] [--city <name>] [--db <path>]

Defaults to --dry-run. Pass --no-dry-run to actually write.
K4-exec is gated by 9-round approval. Do NOT run --no-dry-run against
production DB without explicit operator approval.
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name
from src.contracts.settlement_semantics import SettlementSemantics
from src.state.db import get_world_connection, init_schema


def _add_authority_columns_if_missing(conn: sqlite3.Connection) -> None:
    """Add authority column to settlements if not present (migration shim for test DBs)."""
    info = conn.execute("PRAGMA table_info(settlements)").fetchall()
    cols = {row[1] for row in info}
    if "authority" not in cols:
        conn.execute(
            "ALTER TABLE settlements ADD COLUMN "
            "authority TEXT NOT NULL DEFAULT 'UNVERIFIED'"
        )
        conn.commit()


def rebuild_settlements(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    city_filter: str | None = None,
) -> dict:
    """Derive settlements from VERIFIED observations.

    Returns a summary dict with rows_processed, rows_written, per_city breakdown.
    """
    _add_authority_columns_if_missing(conn)

    # Fetch all VERIFIED observations
    if city_filter:
        obs_rows = conn.execute(
            """
            SELECT city, target_date, high_temp, unit
            FROM observations
            WHERE authority = 'VERIFIED' AND city = ?
            ORDER BY city, target_date
            """,
            (city_filter,),
        ).fetchall()
    else:
        obs_rows = conn.execute(
            """
            SELECT city, target_date, high_temp, unit
            FROM observations
            WHERE authority = 'VERIFIED'
            ORDER BY city, target_date
            """
        ).fetchall()

    rows_processed = 0
    rows_written = 0
    rows_skipped = 0
    per_city: dict[str, dict] = defaultdict(lambda: {"processed": 0, "written": 0, "skipped": 0})
    now_iso = datetime.now(timezone.utc).isoformat()

    for obs in obs_rows:
        city_name = obs["city"]
        target_date = obs["target_date"]
        high_temp = obs["high_temp"]

        rows_processed += 1
        per_city[city_name]["processed"] += 1

        city = cities_by_name.get(city_name)
        if city is None:
            print(f"  SKIP {city_name}/{target_date}: city not in config")
            rows_skipped += 1
            per_city[city_name]["skipped"] += 1
            continue

        if high_temp is None:
            print(f"  SKIP {city_name}/{target_date}: high_temp is NULL")
            rows_skipped += 1
            per_city[city_name]["skipped"] += 1
            continue

        try:
            sem = SettlementSemantics.for_city(city)
            settlement_value = sem.assert_settlement_value(
                float(high_temp), context="rebuild_settlements"
            )
        except Exception as e:
            print(f"  SKIP {city_name}/{target_date}: SettlementSemantics error: {e}")
            rows_skipped += 1
            per_city[city_name]["skipped"] += 1
            continue

        if not dry_run:
            conn.execute(
                """
                INSERT OR REPLACE INTO settlements
                (city, target_date, settlement_value, settlement_source,
                 settled_at, authority)
                VALUES (?, ?, ?, 'wu_icao_rebuild', ?, 'VERIFIED')
                """,
                (city_name, target_date, settlement_value, now_iso),
            )
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
    parser = argparse.ArgumentParser(description="K4: Rebuild settlements from VERIFIED observations")
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Preview only — do not write to DB (default: True)",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Actually write rows to DB",
    )
    parser.add_argument(
        "--city", dest="city", default=None,
        help="Limit rebuild to a single city name",
    )
    parser.add_argument(
        "--db", dest="db_path", default=None,
        help="Path to DB (default: production world DB)",
    )
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
    print(f"\n=== rebuild_settlements [{mode}] ===")
    if args.city:
        print(f"  City filter: {args.city}")

    try:
        summary = rebuild_settlements(conn, dry_run=args.dry_run, city_filter=args.city)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    finally:
        conn.close()

    print(f"\nRows processed: {summary['rows_processed']}")
    if args.dry_run:
        print(f"Rows would-write: {summary['rows_would_write']}")
    else:
        print(f"Rows written:    {summary['rows_written']}")
    print(f"Rows skipped:    {summary['rows_skipped']}")

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
