#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Lifecycle: created=2026-05-26; last_reviewed=2026-05-26; last_reused=never
# Purpose: Build isolated ft_staging forecasts DB for full_transport_v1 pair generation; copies tables from prod read-only via ATTACH URI.
# Reuse: Creates new staging DB; does NOT modify prod. Safe to re-run. Verify STAGING_DB path before use.
# Authority basis: operator DECISION FINAL 2026-05-26 — isolated-rebuild path
"""
Build ft_staging DB for full_transport_v1 pair generation.

Creates a fresh staging forecasts DB with:
- calibration_pairs_v2 UNIQUE key extended to include error_model_family
  (prevents ft_v1 rows from colliding with 'none' rows)
- ensemble_snapshots, observations, observation_instants_v2, zeus_meta
  copied read-only from prod zeus-forecasts.db
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

ZEUS_ROOT = Path(__file__).parent.parent
PROD_FORECASTS = ZEUS_ROOT / "state" / "zeus-forecasts.db"
STAGING_DB = ZEUS_ROOT / "state" / "ft_staging_2026-05-26.db"

# Tables to copy wholesale from prod (read via ATTACH, no world-DB tables touched)
COPY_TABLES = [
    "ensemble_snapshots",
    "observations",
    "observation_instants_v2",
    "zeus_meta",
]

# Family-capable calibration_pairs_v2 DDL — UNIQUE includes error_model_family
# so ft_v1 rows coexist with 'none' rows without collision.
CALIBRATION_PAIRS_V2_DDL = """
CREATE TABLE IF NOT EXISTS calibration_pairs_v2 (
    pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL
        CHECK (temperature_metric IN ('high', 'low')),
    observation_field TEXT NOT NULL
        CHECK (observation_field IN ('high_temp', 'low_temp')),
    range_label TEXT NOT NULL,
    p_raw REAL NOT NULL,
    outcome INTEGER NOT NULL,
    lead_days REAL NOT NULL,
    season TEXT NOT NULL,
    cluster TEXT NOT NULL,
    forecast_available_at TEXT NOT NULL,
    settlement_value REAL,
    decision_group_id TEXT,
    bias_corrected INTEGER NOT NULL DEFAULT 0
        CHECK (bias_corrected IN (0, 1)),
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
    bin_source TEXT NOT NULL DEFAULT 'legacy',
    snapshot_id INTEGER REFERENCES ensemble_snapshots(snapshot_id),
    data_version TEXT NOT NULL,
    training_allowed INTEGER NOT NULL DEFAULT 1
        CHECK (training_allowed IN (0, 1)),
    causality_status TEXT NOT NULL DEFAULT 'OK',
    cycle TEXT NOT NULL DEFAULT '00',
    source_id TEXT NOT NULL DEFAULT 'tigge_mars',
    horizon_profile TEXT NOT NULL DEFAULT 'full',
    error_model_family TEXT NOT NULL DEFAULT 'none',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(city, target_date, temperature_metric, range_label, lead_days,
           forecast_available_at, bin_source, data_version, error_model_family)
)
"""

CALIBRATION_PAIRS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_calibration_pairs_v2_bucket ON calibration_pairs_v2(temperature_metric, cluster, season, lead_days)",
    "CREATE INDEX IF NOT EXISTS idx_calibration_pairs_v2_city_date_metric ON calibration_pairs_v2(city, target_date, temperature_metric)",
    "CREATE INDEX IF NOT EXISTS idx_calibration_pairs_v2_refit_core ON calibration_pairs_v2(temperature_metric, cluster, season, lead_days, error_model_family)",
]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def _copy_table(staging: sqlite3.Connection, prod_path: Path, table: str) -> int:
    """Attach prod read-only, INSERT all rows into staging, DETACH."""
    staging.execute(f"ATTACH DATABASE 'file:{prod_path}?mode=ro' AS prod")
    try:
        # Get column names from prod
        cols_info = staging.execute(f"PRAGMA prod.table_info({table})").fetchall()
        if not cols_info:
            print(f"  [WARN] {table} not found in prod — skipping")
            return 0
        cols = [r[1] for r in cols_info]

        # Check destination has matching columns (subset is ok — skip extras)
        dest_cols_info = staging.execute(f"PRAGMA table_info({table})").fetchall()
        dest_cols = {r[1] for r in dest_cols_info}
        copy_cols = [c for c in cols if c in dest_cols]
        col_list = ", ".join(copy_cols)

        staging.execute(
            f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM prod.{table}"
        )
        staging.commit()
        count = staging.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return count
    finally:
        staging.execute("DETACH DATABASE prod")


def main() -> None:
    if STAGING_DB.exists():
        print(f"Staging DB already exists: {STAGING_DB}")
        print("Delete it manually if you want to rebuild. Exiting.")
        sys.exit(1)

    if not PROD_FORECASTS.exists():
        print(f"FATAL: prod forecasts DB not found at {PROD_FORECASTS}")
        sys.exit(2)

    print(f"Building staging DB: {STAGING_DB}")
    print(f"Source prod DB:       {PROD_FORECASTS}")
    print()

    # Step 1: create fresh staging DB with patched schema
    print("Step 1: Creating schema...")
    staging = sqlite3.connect(str(STAGING_DB))
    staging.row_factory = sqlite3.Row
    staging.execute("PRAGMA journal_mode=WAL")
    staging.execute("PRAGMA synchronous=NORMAL")

    # Apply v2_schema for all OTHER tables (observation_instants_v2, platt_models_v2, etc.)
    # then override calibration_pairs_v2 with family-capable DDL
    from src.state.schema.v2_schema import apply_v2_schema

    apply_v2_schema(staging)
    staging.commit()

    # Rebuild calibration_pairs_v2 with the extended UNIQUE key
    # (apply_v2_schema created the standard one; we need to replace it)
    print("Step 2: Patching calibration_pairs_v2 with family-capable UNIQUE key...")
    staging.execute("DROP TABLE IF EXISTS calibration_pairs_v2")
    staging.execute(CALIBRATION_PAIRS_V2_DDL)
    for idx_sql in CALIBRATION_PAIRS_INDEXES:
        staging.execute(idx_sql)
    staging.commit()

    # Verify the UNIQUE now includes error_model_family
    ddl_row = staging.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='calibration_pairs_v2'"
    ).fetchone()
    if "error_model_family" not in (ddl_row[0] if ddl_row else ""):
        print("FATAL: error_model_family not in UNIQUE — aborting")
        staging.close()
        STAGING_DB.unlink()
        sys.exit(3)
    print("  UNIQUE key confirmed includes error_model_family")

    # Step 3: copy source tables from prod
    print()
    for table in COPY_TABLES:
        t0 = time.time()
        print(f"Step 3: Copying {table}...")
        count = _copy_table(staging, PROD_FORECASTS, table)
        elapsed = time.time() - t0
        print(f"  {count:,} rows copied in {elapsed:.1f}s")

    staging.close()
    size_mb = STAGING_DB.stat().st_size / 1024 / 1024
    print()
    print(f"Staging DB ready: {STAGING_DB}  ({size_mb:.0f} MB)")
    print("UNIQUE key: (city, target_date, temperature_metric, range_label, lead_days,")
    print("             forecast_available_at, bin_source, data_version, error_model_family)")


if __name__ == "__main__":
    main()
