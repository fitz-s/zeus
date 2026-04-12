"""K3 migration: collapse calibration_pairs.cluster to city name.

In the worktree empty DB this is a no-op. At production merge time:
1. Updates calibration_pairs SET cluster = city (column already has per-row city)
2. Soft-deletes platt_models rows with regional bucket_key (LIKE "%-%_%") via
   UPDATE SET is_active = 0 (NOT hard DELETE -- keeps them for rollback)
3. Verifies counts before/after
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection, init_schema


def main() -> int:
    conn = get_world_connection()
    init_schema(conn)

    # 1. calibration_pairs: cluster := city (each row already has city + cluster)
    before = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    if before == 0:
        print("calibration_pairs empty -- no migration work needed")
    else:
        conn.execute("UPDATE calibration_pairs SET cluster = city")
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
        assert before == after, f"row count changed during migration: {before} -> {after}"
        distinct = conn.execute("SELECT DISTINCT cluster FROM calibration_pairs").fetchall()
        print(f"calibration_pairs: {before} rows updated; distinct clusters: {len(distinct)}")

    # 2. platt_models: soft-delete regional-bucket models
    # Regional bucket keys contain a hyphen BEFORE the underscore (e.g. US-Northeast_DJF).
    # City-based bucket keys like "Mexico City_DJF" have no hyphen before underscore.
    # LIKE pattern %-%_% matches keys with a hyphen anywhere (regional pattern).
    before_active = conn.execute("SELECT COUNT(*) FROM platt_models WHERE is_active = 1").fetchone()[0]
    conn.execute("UPDATE platt_models SET is_active = 0 WHERE bucket_key LIKE '%-%_%'")
    conn.commit()
    after_active = conn.execute("SELECT COUNT(*) FROM platt_models WHERE is_active = 1").fetchone()[0]
    soft_deleted = conn.execute("SELECT COUNT(*) FROM platt_models WHERE is_active = 0").fetchone()[0]
    print(f"platt_models: {before_active} -> {after_active} active; {soft_deleted} soft-deleted")

    conn.close()
    print("K3 migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
