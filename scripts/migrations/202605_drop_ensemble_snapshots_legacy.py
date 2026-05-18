# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: v1.F20 reader migration — all readers migrated to ensemble_snapshots_v2
#   Run against zeus-world.db (NOT zeus_trades.db, NOT zeus-forecasts.db):
#     python -m scripts.migrations apply \
#       --target 202605_drop_ensemble_snapshots_legacy \
#       --db-path state/zeus-world.db
#
# OPERATOR-INVOKED ONLY. Do NOT run against live DB without confirming all
# readers have been migrated and ensemble_snapshots_v2 is fully populated.


def up(conn):
    """Drop the legacy ensemble_snapshots table and its index from zeus-world.db.

    Idempotent: uses DROP TABLE IF EXISTS / DROP INDEX IF EXISTS so re-running
    after runner failure does not raise OperationalError.

    Prerequisites (all verified by v1.F20 reader migration):
      - src/data/ingest_status_writer.py: ensemble_snapshots block removed
      - src/engine/evaluator.py: dual-write to legacy dropped; v2-only path
      - src/engine/replay.py: _snapshot_legacy_table removed from ReplayContext
      - src/execution/harvester.py: legacy tuple removed from snapshot lookup loops
    """
    conn.execute("DROP INDEX IF EXISTS idx_ensemble_city_date")
    conn.execute("DROP TABLE IF EXISTS ensemble_snapshots")
