# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §H
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
#                  preflight/migration_dry_runs.json (2829 BLEEDING rows, daemon_active=true)
"""
Backfill script: rewrite provenance_json for BLEEDING settlements_v2 rows.

SCAFFOLD — implementation body not yet written.

PURPOSE:
    Idempotently update provenance_json for rows in settlements_v2 that contain
    'harvester_live_uma_vote' (BLEEDING rows), replacing with typed era provenance.
    After successful backfill, the CI antibody query:
        SELECT COUNT(*) FROM settlements_v2
        WHERE provenance_json LIKE '%harvester_live_uma_vote%'
        AND settled_at >= '<PR1_MERGE_DATE>'
    MUST equal 0.

    Target: 2829 BLEEDING rows (per preflight/migration_dry_runs.json).

USAGE (SCAFFOLD pseudocode):
    python scripts/backfill_settlements_v2_era_provenance.py
        [--dry-run]
        [--row-cap N]
        [--chunk-size N]
        [--since DATE]

    Options:
      --dry-run       Print what would be updated; write nothing (DEFAULT: always test first)
      --row-cap N     Limit total rows updated in one run (default: 500)
      --chunk-size N  Process in chunks of N rows (default: 500)
      --since DATE    Only backfill rows with settled_at >= DATE

SAFETY CONSTRAINTS:
    - PRAGMA busy_timeout=5000 to coexist with live daemon (daemon_active=true per preflight)
    - NEVER open zeus-world.db or zeus-forecasts.db with bare sqlite3.connect()
    - Use get_forecasts_connection_with_world() for ATTACH+SAVEPOINT pattern
    - 500-row chunks with commit between chunks to avoid holding long write locks
    - --dry-run is the DEFAULT behavior; require --apply to actually write
    - disk_sufficient_for_rebuild=false (22Gi free per preflight) — no DB copy/rebuild

DISK SAFETY NOTE (from preflight/migration_dry_runs.json):
    forecasts_db_bytes=49GB, world_db_bytes=38GB, free_space=22Gi.
    Do NOT create backup copies of either DB before backfill.
    The backfill is idempotent (re-running produces same result).
    Use WAL mode + PRAGMA synchronous=NORMAL during backfill for performance.

IMPLEMENTATION NOTES (SCAFFOLD pseudocode):
    1. Run audit_settlements_v2_era_provenance.py first; confirm BLEEDING count
    2. PRAGMA busy_timeout = 5000
    3. SELECT condition_id, settled_at, provenance_json FROM settlements_v2
       WHERE provenance_json LIKE '%harvester_live_uma_vote%'
       ORDER BY settled_at
       LIMIT chunk_size OFFSET offset
    4. For each chunk:
       a. BEGIN SAVEPOINT backfill_chunk_N
       b. For each row:
          - Parse provenance_json
          - dispatch_era_basis(settled_at.date()) → era_basis
          - Build new provenance_json via _build_era_provenance(...)
          - UPDATE settlements_v2 SET provenance_json = new_json
            WHERE condition_id = row['condition_id']
       c. RELEASE SAVEPOINT backfill_chunk_N (or ROLLBACK on exception)
    5. After all chunks: verify COUNT(*) WHERE LIKE 'harvester_live_uma_vote' == 0

IDEMPOTENCY:
    Running backfill twice is safe. The second run finds 0 BLEEDING rows
    and exits cleanly without any DB writes.
"""
