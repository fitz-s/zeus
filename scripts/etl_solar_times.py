#!/usr/bin/env python3
"""ETL: sunrise/sunset daily context into zeus.db:solar_daily.

Imports the pre-downloaded JSONL file produced offline (source is likely the
same Open-Meteo sunrise/sunset endpoint family used by the hourly backfill).
Each row is validated for required fields, lat/lon range, and timezone
parseability before insertion. Rejected lines are counted by category and
logged to stderr.

Consumer: `src/signal/diurnal.py:49` reads `solar_daily` for day0 signal
generation.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection as get_connection, init_schema

logger = logging.getLogger(__name__)

SOLAR_JSONL = Path(
    "/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/solar/"
    "city_solar_times_20240101_20260331.jsonl"
)

_REQUIRED_FIELDS = (
    "city", "target_date", "timezone", "lat", "lon",
    "sunrise_local", "sunset_local", "sunrise_utc", "sunset_utc",
    "utc_offset_minutes", "dst_active",
)


def _validate_row(row: dict) -> str | None:
    """Return rejection category (short string) or None if valid."""
    for field in _REQUIRED_FIELDS:
        if field not in row:
            return f"missing_field_{field}"

    try:
        lat = float(row["lat"])
        lon = float(row["lon"])
    except (ValueError, TypeError):
        return "bad_latlon_type"
    if not (-90.0 <= lat <= 90.0):
        return "lat_out_of_range"
    if not (-180.0 <= lon <= 180.0):
        return "lon_out_of_range"

    tz_name = row["timezone"]
    if not isinstance(tz_name, str) or not tz_name:
        return "bad_timezone_type"
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return "bad_timezone_string"

    if not isinstance(row["dst_active"], (bool, int)):
        return "bad_dst_active_type"

    try:
        int(row["utc_offset_minutes"])
    except (ValueError, TypeError):
        return "bad_utc_offset_type"

    return None


def run_etl(path: Path = SOLAR_JSONL) -> dict:
    if not path.exists():
        return {"imported": 0, "error": f"missing input: {path}"}

    conn = get_connection()
    init_schema(conn)

    inserted = 0
    rejected = 0
    reject_categories: dict[str, int] = {}

    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                rejected += 1
                reject_categories["json_error"] = reject_categories.get("json_error", 0) + 1
                logger.warning("line %d: JSON decode error — %s", line_no, e)
                continue

            reason = _validate_row(row)
            if reason:
                rejected += 1
                reject_categories[reason] = reject_categories.get(reason, 0) + 1
                if rejected <= 10:  # log first 10 only, avoid log flood
                    logger.warning(
                        "line %d: rejected (%s) city=%s target_date=%s",
                        line_no, reason, row.get("city"), row.get("target_date"),
                    )
                continue

            try:
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
            except Exception as e:
                rejected += 1
                reject_categories["insert_error"] = reject_categories.get("insert_error", 0) + 1
                logger.error(
                    "line %d: INSERT failed for city=%s target_date=%s: %s",
                    line_no, row.get("city"), row.get("target_date"), e,
                )

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM solar_daily").fetchone()[0]
    conn.close()
    return {
        "imported": inserted,
        "rejected": rejected,
        "reject_categories": reject_categories,
        "total_rows": total,
        "source": str(path),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_etl()
    print(json.dumps(result, ensure_ascii=False, indent=2))
