"""ETL: DST-safe observation_instants from rainstorm.db -> zeus.db:observation_instants.

This is Zeus's authoritative hourly time-semantic table. New time-sensitive logic
should build from this table rather than from lossy local-hour-only artifacts.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema

RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"

CELSIUS_CITIES = {
    "London", "Paris", "Seoul", "Tokyo", "Shanghai", "Shenzhen",
    "Munich", "Wellington", "Buenos Aires", "Hong Kong", "Singapore",
    "Taipei", "Beijing", "Chengdu", "Chongqing", "Istanbul", "Madrid",
    "Milan", "Moscow", "Sao Paulo", "Warsaw", "Wuhan", "Ankara",
    "Lucknow", "Mexico City", "Tel Aviv",
}


def infer_temp_unit(city: str) -> str:
    return "C" if city in CELSIUS_CITIES else "F"


def _is_temp_sane(temp: float | None, unit: str) -> bool:
    if temp is None:
        return True
    if unit == "C":
        return -45 <= temp <= 55
    return -50 <= temp <= 135


def run_etl(source_db: Path = RAINSTORM_DB) -> dict:
    rs = sqlite3.connect(str(source_db))
    rs.row_factory = sqlite3.Row

    zeus = get_connection()
    init_schema(zeus)

    existing = zeus.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0]
    print(f"observation_instants has {existing} existing rows. Running incremental sync...")

    rows = rs.execute(
        """
        SELECT city, target_date, source, timezone_name, local_hour, local_timestamp,
               utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
               is_missing_local_hour, time_basis, temp_current_f, running_max_f,
               delta_rate_f_per_h, station_id, observation_count, raw_response,
               source_file, imported_at
        FROM observation_instants
        ORDER BY city, target_date, source, utc_timestamp
        """
    ).fetchall()

    print(f"Source rows: {len(rows):,}")

    imported = 0
    rejected = 0
    batch: list[tuple[object, ...]] = []

    for row in rows:
        city = str(row["city"])
        unit = infer_temp_unit(city)

        try:
            local_ts = datetime.fromisoformat(str(row["local_timestamp"]))
            utc_ts = datetime.fromisoformat(str(row["utc_timestamp"]))
        except ValueError:
            rejected += 1
            continue

        if local_ts.tzinfo is None or utc_ts.tzinfo is None:
            rejected += 1
            continue

        if not _is_temp_sane(row["temp_current_f"], unit):
            rejected += 1
            continue
        if not _is_temp_sane(row["running_max_f"], unit):
            rejected += 1
            continue

        batch.append(
            (
                city,
                row["target_date"],
                row["source"],
                row["timezone_name"],
                row["local_hour"],
                row["local_timestamp"],
                row["utc_timestamp"],
                int(row["utc_offset_minutes"]),
                int(bool(row["dst_active"])),
                int(bool(row["is_ambiguous_local_hour"])),
                int(bool(row["is_missing_local_hour"])),
                row["time_basis"],
                row["temp_current_f"],
                row["running_max_f"],
                row["delta_rate_f_per_h"],
                unit,
                row["station_id"],
                row["observation_count"],
                row["raw_response"],
                row["source_file"],
                row["imported_at"],
            )
        )

        if len(batch) >= 10000:
            zeus.executemany(
                """
                INSERT OR REPLACE INTO observation_instants
                (city, target_date, source, timezone_name, local_hour, local_timestamp,
                 utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
                 is_missing_local_hour, time_basis, temp_current, running_max,
                 delta_rate_per_h, temp_unit, station_id, observation_count,
                 raw_response, source_file, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            imported += len(batch)
            batch = []

    if batch:
        zeus.executemany(
            """
            INSERT OR REPLACE INTO observation_instants
            (city, target_date, source, timezone_name, local_hour, local_timestamp,
             utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
             is_missing_local_hour, time_basis, temp_current, running_max,
             delta_rate_per_h, temp_unit, station_id, observation_count,
             raw_response, source_file, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        imported += len(batch)

    zeus.commit()
    final = zeus.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0]

    rs.close()
    zeus.close()

    print(f"Attempted: {imported:,}, Rejected: {rejected:,}")
    print(f"Final row count: {final:,}")
    return {"imported": final, "rejected": rejected}


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
