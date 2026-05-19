# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §H
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
"""
Rollback script: restore pre-backfill provenance_json for settlements_v2 rows.

SCAFFOLD — implementation body not yet written.

PURPOSE:
    Rollback a partial or complete provenance backfill by restoring
    provenance_json rows to the legacy 'harvester_live_uma_vote' form.
    Used when:
      - The backfill produced incorrect era assignments
      - PR 1 is reverted and the system needs to return to legacy behaviour
      - A partial backfill must be unwound before re-running cleanly

    IMPORTANT: This script requires a pre-backfill snapshot to restore from.
    It does NOT attempt to infer what the original provenance was from current
    DB state. The snapshot must be produced by audit_settlements_v2_era_provenance.py
    before the backfill is run.

USAGE (SCAFFOLD pseudocode):
    python scripts/rollback_settlements_v2_era_provenance.py
        --snapshot PATH
        [--dry-run]
        [--row-cap N]

    Options:
      --snapshot PATH  Path to JSON snapshot produced by audit script pre-backfill (REQUIRED)
      --dry-run        Print what would be restored; write nothing
      --row-cap N      Limit total rows restored in one run (default: 500)

SNAPSHOT FORMAT (pseudocode):
    The snapshot is a JSON file output by audit_settlements_v2_era_provenance.py
    with --mode pre-backfill-snapshot, containing per-row original provenance_json.

IMPLEMENTATION NOTES (SCAFFOLD pseudocode):
    1. Load snapshot JSON
    2. Verify snapshot was produced for the same DB (check total_rows, queried_at_utc)
    3. For each row in snapshot with era_status == BLEEDING (i.e., rows we backfilled):
       a. SAVEPOINT rollback_chunk_N
       b. UPDATE settlements_v2 SET provenance_json = snapshot_row['original_provenance_json']
          WHERE condition_id = snapshot_row['condition_id']
          AND settled_at = snapshot_row['settled_at']
       c. RELEASE SAVEPOINT (or ROLLBACK on exception)
    4. Verify post-rollback: COUNT(*) WHERE LIKE 'harvester_live_uma_vote' matches snapshot count

DISK SAFETY:
    Same constraints as backfill: PRAGMA busy_timeout=5000, 500-row chunks,
    no bare sqlite3.connect(), use get_forecasts_connection_with_world().
    disk_sufficient_for_rebuild=false; no DB copy required.
"""
