#!/usr/bin/env python3
"""Backfill daily observations from authoritative settlement rows.

Use when the settlement source's temperature is itself the settlement truth.
The inserted observation source is namespaced as `settlement_source:<source>` so
these rows are auditable and not confused with raw provider pulls.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name
from src.state.db import get_shared_connection, init_schema


def _source_name(raw_source: str | None) -> str:
    source = (raw_source or "unknown").strip() or "unknown"
    return f"settlement_source:{source}"


def backfill_observations_from_settlements(*, dry_run: bool = False, limit: int | None = None) -> dict:
    conn = get_shared_connection()
    init_schema(conn)
    query = """
        SELECT s.city, s.target_date, s.settlement_value, s.settlement_source
        FROM settlements s
        WHERE s.settlement_value IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM observations o
              WHERE o.city = s.city
                AND o.target_date = s.target_date
                AND o.high_temp IS NOT NULL
          )
        ORDER BY s.city, s.target_date
    """
    rows = conn.execute(query).fetchall()
    if limit is not None:
        rows = rows[:limit]

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped_no_city = 0
    by_source: Counter[str] = Counter()
    samples: list[dict] = []

    for row in rows:
        city = cities_by_name.get(str(row["city"]))
        if city is None:
            skipped_no_city += 1
            continue
        source = _source_name(row["settlement_source"])
        by_source[source] += 1
        if len(samples) < 10:
            samples.append(
                {
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "source": source,
                    "high_temp": row["settlement_value"],
                    "unit": city.settlement_unit,
                }
            )
        if dry_run:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO observations
            (city, target_date, source, high_temp, unit, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["city"],
                row["target_date"],
                source,
                float(row["settlement_value"]),
                city.settlement_unit,
                now,
            ),
        )
        inserted += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return {
        "dry_run": dry_run,
        "candidate_rows": len(rows),
        "inserted": 0 if dry_run else inserted,
        "skipped_no_city": skipped_no_city,
        "by_source": dict(sorted(by_source.items())),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    result = backfill_observations_from_settlements(dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
