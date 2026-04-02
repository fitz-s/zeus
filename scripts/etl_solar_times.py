#!/usr/bin/env python3
"""ETL: sunrise/sunset daily context into zeus.db:solar_daily."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema

SOLAR_JSONL = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/solar/city_solar_times_20240101_20260331.jsonl")


def run_etl(path: Path = SOLAR_JSONL) -> dict:
    if not path.exists():
        return {"imported": 0, "error": f"missing input: {path}"}

    conn = get_connection()
    init_schema(conn)

    inserted = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO solar_daily
                (city, target_date, timezone, lat, lon, sunrise_local, sunset_local,
                 sunrise_utc, sunset_utc, utc_offset_minutes, dst_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["city"],
                    row["target_date"],
                    row["timezone"],
                    float(row["lat"]),
                    float(row["lon"]),
                    row["sunrise_local"],
                    row["sunset_local"],
                    row["sunrise_utc"],
                    row["sunset_utc"],
                    int(row["utc_offset_minutes"]),
                    1 if row["dst_active"] else 0,
                ),
            )
            inserted += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM solar_daily").fetchone()[0]
    conn.close()
    return {"imported": inserted, "total_rows": total, "source": str(path)}


if __name__ == "__main__":
    result = run_etl()
    print(json.dumps(result, ensure_ascii=False, indent=2))
