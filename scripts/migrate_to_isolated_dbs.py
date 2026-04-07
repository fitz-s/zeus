#!/usr/bin/env python3
"""Phase 3: Migrate zeus.db into isolated paper + shared databases.

Reads state/zeus.db (legacy shared DB) and creates:
  - state/zeus-paper.db: trade tables filtered to env='paper'
  - state/zeus-shared.db: world data tables (no env column)

DRY RUN by default. Pass --apply to execute.

Usage:
    python scripts/migrate_to_isolated_dbs.py          # dry run
    python scripts/migrate_to_isolated_dbs.py --apply  # execute migration
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
SOURCE_DB = STATE_DIR / "zeus.db"
PAPER_DB = STATE_DIR / "zeus-paper.db"
SHARED_DB = STATE_DIR / "zeus-shared.db"

# Tables that go into zeus-paper.db (trade-facing, have env column).
# Filtered to env='paper' only.
PAPER_TABLES = [
    "trade_decisions",
    "position_events",
    "position_events_legacy",
    "position_current",
    "chronicle",
    "decision_log",
    "outcome_fact",
    "opportunity_fact",
    "execution_fact",
    "replay_results",
    "shadow_signals",
]

# Tables that go into zeus-shared.db (world data, no env filtering).
SHARED_TABLES = [
    "settlements",
    "observations",
    "observation_instants",
    "hourly_observations",
    "ensemble_snapshots",
    "calibration_pairs",
    "platt_models",
    "market_events",
    "token_price_log",
    "market_price_history",
    "forecast_skill",
    "model_bias",
    "model_skill",
    "historical_forecasts",
    "solar_daily",
    "diurnal_curves",
    "diurnal_peak_prob",
    "temp_persistence",
    "strategy_health",
    "asos_wu_offsets",
    "alpha_overrides",
    "availability_fact",
    "control_overrides",
    "risk_actions",
]

# Tables with env column (need WHERE env='paper' filter)
ENV_TABLES = {
    "chronicle",
    "decision_log",
    "position_current",
    "position_events",
    "position_events_legacy",
    "trade_decisions",
}

# Index definitions to recreate (table -> list of CREATE INDEX statements)
INDEXES = {
    "calibration_pairs": [
        "CREATE INDEX IF NOT EXISTS idx_calibration_bucket ON calibration_pairs(cluster, season)",
    ],
    "decision_log": [
        "CREATE INDEX IF NOT EXISTS idx_decision_log_ts ON decision_log(timestamp)",
    ],
    "ensemble_snapshots": [
        "CREATE INDEX IF NOT EXISTS idx_ensemble_city_date ON ensemble_snapshots(city, target_date, available_at)",
    ],
    "market_events": [
        "CREATE INDEX IF NOT EXISTS idx_market_events_slug ON market_events(market_slug)",
    ],
    "observation_instants": [
        "CREATE INDEX IF NOT EXISTS idx_observation_instants_city_date ON observation_instants(city, target_date, utc_timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_observation_instants_source ON observation_instants(source, city, target_date)",
    ],
    "observations": [
        "CREATE INDEX IF NOT EXISTS idx_observations_city_date ON observations(city, target_date, source)",
    ],
    "position_events_legacy": [
        "CREATE INDEX IF NOT EXISTS idx_position_events_legacy_trade_ts ON position_events_legacy(runtime_trade_id, timestamp)",
    ],
    "replay_results": [
        "CREATE INDEX IF NOT EXISTS idx_replay_run ON replay_results(replay_run_id)",
    ],
    "settlements": [
        "CREATE INDEX IF NOT EXISTS idx_settlements_city_date ON settlements(city, target_date)",
    ],
    "token_price_log": [
        "CREATE INDEX IF NOT EXISTS idx_token_price_token ON token_price_log(token_id, timestamp)",
    ],
}


def get_source_count(cur: sqlite3.Cursor, table: str, env_filter: bool) -> int:
    """Count rows in source table, optionally filtered by env='paper'."""
    if env_filter and table in ENV_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM [{table}] WHERE env = 'paper'")
    else:
        cur.execute(f"SELECT COUNT(*) FROM [{table}]")
    return cur.fetchone()[0]


def get_table_schema(cur: sqlite3.Cursor, table: str) -> str:
    """Get the CREATE TABLE statement from source DB."""
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"Table {table!r} not found in source DB")
    return row[0]


def migrate_table(
    source_conn: sqlite3.Connection,
    dest_conn: sqlite3.Connection,
    table: str,
    env_filter: bool,
    dry_run: bool,
) -> tuple[int, int]:
    """Migrate a single table. Returns (source_count, dest_count)."""
    src_cur = source_conn.cursor()
    source_count = get_source_count(src_cur, table, env_filter)

    if dry_run:
        action = f"WHERE env='paper'" if env_filter and table in ENV_TABLES else "(all rows)"
        print(f"  [DRY RUN] {table}: {source_count} rows {action}")
        return source_count, 0

    # Get schema and create table in destination
    schema_sql = get_table_schema(src_cur, table)
    dest_conn.execute(schema_sql)

    # Copy data
    if env_filter and table in ENV_TABLES:
        src_cur.execute(f"SELECT * FROM [{table}] WHERE env = 'paper'")
    else:
        src_cur.execute(f"SELECT * FROM [{table}]")

    rows = src_cur.fetchall()
    if rows:
        placeholders = ",".join(["?"] * len(rows[0]))
        dest_conn.executemany(f"INSERT INTO [{table}] VALUES ({placeholders})", rows)

    # Create indexes
    for idx_sql in INDEXES.get(table, []):
        dest_conn.execute(idx_sql)

    dest_conn.commit()

    # Verify
    dest_cur = dest_conn.cursor()
    dest_cur.execute(f"SELECT COUNT(*) FROM [{table}]")
    dest_count = dest_cur.fetchone()[0]

    return source_count, dest_count


def check_source_tables(source_conn: sqlite3.Connection) -> list[str]:
    """Check which expected tables exist in source and report missing/extra."""
    cur = source_conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    actual = {r[0] for r in cur.fetchall()}

    expected = set(PAPER_TABLES) | set(SHARED_TABLES)
    missing = expected - actual
    extra = actual - expected - {"sqlite_sequence"}

    warnings = []
    if missing:
        warnings.append(f"WARNING: Missing tables in source: {sorted(missing)}")
    if extra:
        warnings.append(f"NOTE: Extra tables not migrated: {sorted(extra)}")
    return warnings


def main():
    parser = argparse.ArgumentParser(description="Migrate zeus.db to isolated paper + shared DBs")
    parser.add_argument("--apply", action="store_true", help="Actually execute the migration (default: dry run)")
    args = parser.parse_args()
    dry_run = not args.apply

    if not SOURCE_DB.exists():
        print(f"ERROR: Source DB not found: {SOURCE_DB}")
        sys.exit(1)

    source_size_mb = SOURCE_DB.stat().st_size / (1024 * 1024)
    print(f"Source: {SOURCE_DB} ({source_size_mb:.1f} MB)")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print()

    source_conn = sqlite3.connect(str(SOURCE_DB))

    # Pre-flight checks
    warnings = check_source_tables(source_conn)
    for w in warnings:
        print(w)
    if warnings:
        print()

    # Check destination DBs don't already have data
    if not dry_run:
        for dest_path in [PAPER_DB, SHARED_DB]:
            if dest_path.exists() and dest_path.stat().st_size > 0:
                print(f"ERROR: Destination DB already has data: {dest_path}")
                print("       Remove it first or back it up before re-running.")
                sys.exit(1)

    paper_conn = sqlite3.connect(str(PAPER_DB)) if not dry_run else None
    shared_conn = sqlite3.connect(str(SHARED_DB)) if not dry_run else None

    # Enable WAL mode for write performance
    if paper_conn:
        paper_conn.execute("PRAGMA journal_mode=WAL")
    if shared_conn:
        shared_conn.execute("PRAGMA journal_mode=WAL")

    # Migrate paper tables
    print("=== Paper DB (trade tables, env='paper') ===")
    print(f"    Target: {PAPER_DB}")
    paper_totals = {"source": 0, "dest": 0}
    paper_results = []
    for table in PAPER_TABLES:
        try:
            src_count, dest_count = migrate_table(
                source_conn, paper_conn, table,
                env_filter=True, dry_run=dry_run,
            )
            paper_totals["source"] += src_count
            paper_totals["dest"] += dest_count
            paper_results.append((table, src_count, dest_count, "OK"))
        except ValueError as e:
            print(f"  SKIP {table}: {e}")
            paper_results.append((table, 0, 0, "MISSING"))

    print()

    # Migrate shared tables
    print("=== Shared DB (world data, all rows) ===")
    print(f"    Target: {SHARED_DB}")
    shared_totals = {"source": 0, "dest": 0}
    shared_results = []
    for table in SHARED_TABLES:
        try:
            src_count, dest_count = migrate_table(
                source_conn, shared_conn, table,
                env_filter=False, dry_run=dry_run,
            )
            shared_totals["source"] += src_count
            shared_totals["dest"] += dest_count
            shared_results.append((table, src_count, dest_count, "OK"))
        except ValueError as e:
            print(f"  SKIP {table}: {e}")
            shared_results.append((table, 0, 0, "MISSING"))

    print()

    # Verification summary
    print("=== Verification ===")
    all_ok = True

    if not dry_run:
        print(f"\nPaper DB ({PAPER_DB.name}):")
        for table, src, dest, status in paper_results:
            match = "MATCH" if src == dest else "MISMATCH"
            if src != dest and status == "OK":
                all_ok = False
            print(f"  {table:35s}  source={src:>7d}  dest={dest:>7d}  {match}")
        print(f"  {'TOTAL':35s}  source={paper_totals['source']:>7d}  dest={paper_totals['dest']:>7d}")

        print(f"\nShared DB ({SHARED_DB.name}):")
        for table, src, dest, status in shared_results:
            match = "MATCH" if src == dest else "MISMATCH"
            if src != dest and status == "OK":
                all_ok = False
            print(f"  {table:35s}  source={src:>7d}  dest={dest:>7d}  {match}")
        print(f"  {'TOTAL':35s}  source={shared_totals['source']:>7d}  dest={shared_totals['dest']:>7d}")

        paper_size = PAPER_DB.stat().st_size / (1024 * 1024)
        shared_size = SHARED_DB.stat().st_size / (1024 * 1024)
        print(f"\nFile sizes: paper={paper_size:.1f}MB  shared={shared_size:.1f}MB  source={source_size_mb:.1f}MB")

        if all_ok:
            print("\nAll row counts match. Migration successful.")
        else:
            print("\nWARNING: Row count mismatches detected!")
            sys.exit(1)
    else:
        print("DRY RUN complete. Pass --apply to execute migration.")
        total_paper = paper_totals["source"]
        total_shared = shared_totals["source"]
        print(f"\nWould migrate:")
        print(f"  Paper DB:  {len(PAPER_TABLES)} tables, {total_paper} rows")
        print(f"  Shared DB: {len(SHARED_TABLES)} tables, {total_shared} rows")
        print(f"  Total:     {total_paper + total_shared} rows from {source_size_mb:.1f}MB source")

        # Show what gets filtered out
        src_cur = source_conn.cursor()
        for table in ENV_TABLES:
            if table in PAPER_TABLES:
                src_cur.execute(f"SELECT env, COUNT(*) FROM [{table}] WHERE env != 'paper' GROUP BY env")
                non_paper = src_cur.fetchall()
                if non_paper:
                    for env, cnt in non_paper:
                        print(f"  NOTE: {table} has {cnt} rows with env='{env}' (will be excluded)")

    source_conn.close()
    if paper_conn:
        paper_conn.close()
    if shared_conn:
        shared_conn.close()


if __name__ == "__main__":
    main()
