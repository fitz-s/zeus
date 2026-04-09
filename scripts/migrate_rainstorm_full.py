#!/usr/bin/env python3
"""Full rainstorm.db → zeus-shared.db migration.

Migrates all valuable data from rainstorm:
- settlements (with schema mapping)
- observations (all sources)
- forecasts (new table in zeus)
- market_events (gap fill)

Safe: uses INSERT OR IGNORE, idempotent, can run repeatedly.
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"
ZEUS_DB = PROJECT_ROOT / "state" / "zeus-shared.db"


def _ensure_forecasts_table(conn: sqlite3.Connection) -> None:
    """Create forecasts table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            forecast_basis_date TEXT,
            forecast_issue_time TEXT,
            lead_days INTEGER,
            lead_time_hours REAL,
            forecast_high REAL,
            forecast_low REAL,
            temp_unit TEXT DEFAULT 'F',
            retrieved_at TEXT,
            imported_at TEXT,
            UNIQUE(city, target_date, source, forecast_basis_date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_forecasts_city_date
        ON forecasts(city, target_date)
    """)
    conn.commit()


def migrate_settlements(rs: sqlite3.Connection, zs: sqlite3.Connection) -> dict:
    """Migrate settlements with schema mapping."""
    from src.config import cities_by_name

    before = zs.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]

    rows = rs.execute("""
        SELECT city, target_date, event_id, settled_at, winning_range,
               actual_temp_f, inferred_actual_temp_f, temp_unit, actual_temp_source
        FROM settlements
    """).fetchall()

    imported = 0
    for r in rows:
        city = r[0]
        target_date = r[1]
        event_id = r[2] or ""
        settled_at = r[3] or ""
        winning_range = r[4] or ""
        actual_temp = r[5]  # actual_temp_f from rainstorm
        inferred_temp = r[6]
        temp_unit = r[7] or "F"
        actual_source = r[8] or ""

        # Schema mapping: rainstorm winning_range → zeus winning_bin
        winning_bin = winning_range

        # settlement_value: prefer actual_temp_f (WU real temp), fall back to inferred
        settlement_value = actual_temp if actual_temp is not None else inferred_temp
        settlement_source = actual_source if actual_source else "rainstorm_inferred"

        # Rainstorm stores ALL temps as °F in actual_temp_f column.
        # For Celsius cities, convert F→C.
        city_cfg = cities_by_name.get(city)
        if settlement_value is not None and city_cfg and city_cfg.settlement_unit == "C":
            settlement_value = round((settlement_value - 32) * 5 / 9, 1)

        try:
            zs.execute("""
                INSERT OR IGNORE INTO settlements
                (city, target_date, market_slug, winning_bin, settlement_value,
                 settlement_source, settled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                city, target_date, event_id, winning_bin,
                settlement_value, settlement_source, settled_at,
            ))
            imported += zs.total_changes - (before + imported)
        except sqlite3.IntegrityError:
            pass

    zs.commit()
    after = zs.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    return {"before": before, "after": after, "added": after - before, "source_rows": len(rows)}


def migrate_observations(rs: sqlite3.Connection, zs: sqlite3.Connection) -> dict:
    """Migrate all observations."""
    from src.config import cities_by_name

    before = zs.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    # Only migrate sources we care about (skip hourly bulk data that's in hourly_observations)
    valuable_sources = [
        "wu_daily_observed",
        "iem_asos",
        "noaa_cdo_ghcnd",
        "noaa_observed",
        "openmeteo_archive",
        "openmeteo_archive_harvester",
        "meteostat_daily_max",
        "hko_daily_extract",
    ]
    placeholders = ",".join("?" * len(valuable_sources))

    rows = rs.execute(f"""
        SELECT city, target_date, source, temp_high_f, temp_low_f, 
               station_id, imported_at
        FROM observations
        WHERE source IN ({placeholders})
    """, valuable_sources).fetchall()

    for r in rows:
        # Determine correct unit from city config
        city_cfg = cities_by_name.get(r[0])
        unit = city_cfg.settlement_unit if city_cfg else "F"
        try:
            zs.execute("""
                INSERT OR IGNORE INTO observations
                (city, target_date, source, high_temp, low_temp, unit, station_id, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (r[0], r[1], r[2], r[3], r[4], unit, r[5], r[6]))
        except sqlite3.IntegrityError:
            pass

    zs.commit()
    after = zs.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    return {"before": before, "after": after, "added": after - before, "source_rows": len(rows)}


def migrate_forecasts(rs: sqlite3.Connection, zs: sqlite3.Connection) -> dict:
    """Migrate forecasts to new zeus table."""
    from src.config import cities_by_name

    _ensure_forecasts_table(zs)
    before = zs.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]

    rows = rs.execute("""
        SELECT city, target_date, source, forecast_basis_date,
               forecast_issue_time, lead_days, lead_time_hours,
               forecast_high_f, forecast_low_f, retrieved_at, imported_at
        FROM forecasts
    """).fetchall()

    batch_size = 1000
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        for r in batch:
            # Determine correct unit from city config
            city_cfg = cities_by_name.get(r[0])
            unit = city_cfg.settlement_unit if city_cfg else "F"
            try:
                zs.execute("""
                    INSERT OR IGNORE INTO forecasts
                    (city, target_date, source, forecast_basis_date,
                     forecast_issue_time, lead_days, lead_time_hours,
                     forecast_high, forecast_low, temp_unit, retrieved_at, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (*r[:9], unit, r[9], r[10]))
            except sqlite3.IntegrityError:
                pass
        zs.commit()
        if (i + batch_size) % 10000 == 0:
            print(f"  Forecasts: {i + batch_size}/{len(rows)}...")

    after = zs.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
    return {"before": before, "after": after, "added": after - before, "source_rows": len(rows)}


def migrate_market_events(rs: sqlite3.Connection, zs: sqlite3.Connection) -> dict:
    """Fill missing market_events."""
    before = zs.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]

    # Get rainstorm market_events schema
    rs_cols = [col[1] for col in rs.execute("PRAGMA table_info(market_events)").fetchall()]
    zs_cols = [col[1] for col in zs.execute("PRAGMA table_info(market_events)").fetchall()]

    # Map common columns
    common = [c for c in rs_cols if c in zs_cols and c != "id"]

    if not common:
        return {"before": before, "after": before, "added": 0, "error": "no common columns"}

    col_list = ", ".join(common)
    placeholders = ", ".join("?" * len(common))

    rows = rs.execute(f"SELECT {col_list} FROM market_events").fetchall()

    for r in rows:
        try:
            zs.execute(f"""
                INSERT OR IGNORE INTO market_events ({col_list})
                VALUES ({placeholders})
            """, r)
        except sqlite3.IntegrityError:
            pass

    zs.commit()
    after = zs.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
    return {"before": before, "after": after, "added": after - before, "source_rows": len(rows)}


def run_migration(dry_run: bool = False) -> dict:
    """Execute full migration."""
    if not RAINSTORM_DB.exists():
        print(f"ERROR: Rainstorm DB not found at {RAINSTORM_DB}")
        return {"error": "rainstorm db not found"}

    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row
    zs = sqlite3.connect(str(ZEUS_DB))
    zs.row_factory = sqlite3.Row

    results = {}

    print("=== Rainstorm → Zeus Full Migration ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"Source: {RAINSTORM_DB}")
    print(f"Target: {ZEUS_DB}")

    if dry_run:
        print("DRY RUN — no changes will be written")
        # Just report counts
        for table in ["settlements", "observations", "forecasts", "market_events"]:
            try:
                count = rs.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table}: {count} rows to migrate")
            except Exception:
                print(f"  {table}: not found")
        rs.close()
        zs.close()
        return {"dry_run": True}

    # 1. Settlements
    print("\n1. Migrating settlements...")
    results["settlements"] = migrate_settlements(rs, zs)
    print(f"   {results['settlements']['before']} → {results['settlements']['after']} "
          f"(+{results['settlements']['added']})")

    # 2. Observations
    print("\n2. Migrating observations...")
    results["observations"] = migrate_observations(rs, zs)
    print(f"   {results['observations']['before']} → {results['observations']['after']} "
          f"(+{results['observations']['added']})")

    # 3. Forecasts
    print("\n3. Migrating forecasts...")
    results["forecasts"] = migrate_forecasts(rs, zs)
    print(f"   {results['forecasts']['before']} → {results['forecasts']['after']} "
          f"(+{results['forecasts']['added']})")

    # 4. Market Events
    print("\n4. Migrating market events...")
    results["market_events"] = migrate_market_events(rs, zs)
    print(f"   {results['market_events']['before']} → {results['market_events']['after']} "
          f"(+{results['market_events']['added']})")

    rs.close()
    zs.close()

    print("\n=== Migration Complete ===")
    total_added = sum(r.get("added", 0) for r in results.values())
    print(f"Total new rows: {total_added}")

    return results


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run_migration(dry_run=dry)
