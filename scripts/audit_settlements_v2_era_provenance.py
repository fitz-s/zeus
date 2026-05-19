# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §H
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
"""
Audit script: classify settlements_v2 rows by era provenance status.

SCAFFOLD — implementation body not yet written.

PURPOSE:
    Enumerate all rows in settlements_v2 (zeus-forecasts.db) and classify each
    by era provenance status:
      - CLEAN: has typed era in provenance_json matching expected era for settled_at
      - BLEEDING: has 'harvester_live_uma_vote' in provenance_json; needs backfill
      - ANOMALOUS: has era != expected for settled_at date (INV-ERA-1 violation)
      - MISSING_PROVENANCE: provenance_json is NULL, '{}', or empty

    Outputs a JSON report grouped by status + city, matching the structure of
    preflight/migration_dry_runs.json for comparison.

USAGE (SCAFFOLD pseudocode):
    python scripts/audit_settlements_v2_era_provenance.py [--output OUTFILE]

    Options:
      --output PATH   Write JSON report to PATH (default: stdout)
      --since DATE    Only audit rows with settled_at >= DATE (ISO format)
      --city CITY     Filter to one city

IMPLEMENTATION NOTES (SCAFFOLD pseudocode):
    1. Open zeus-forecasts.db in READ-ONLY mode (uri=True, '?mode=ro')
       Do NOT use ATTACH; this script is read-only on one DB.
    2. SELECT condition_id, settled_at, provenance_json FROM settlements_v2
       ORDER BY settled_at
    3. For each row:
       a. Parse provenance_json (json.loads with fallback to {})
       b. Check for 'harvester_live_uma_vote' in provenance_json values → BLEEDING
       c. Check era field: if era == 'uma_oo_v2' and settled_at >= ERA_CUTOVER_DATE → ANOMALOUS
       d. Otherwise: CLEAN or MISSING_PROVENANCE
    4. Aggregate per-city counts using settlement_commands or city from condition_id mapping
    5. Output JSON matching migration_dry_runs.json structure

EXPECTED OUTPUT SHAPE (pseudocode):
    {
      "queried_at_utc": "...",
      "total_rows": 4392,
      "status_counts": {
        "CLEAN": ..., "BLEEDING": 2829, "ANOMALOUS": 0, "MISSING_PROVENANCE": ...
      },
      "per_city_bleeding": [
        {"city": "London", "count": 434, ...}, ...
      ],
      "era_cutover_date": "2026-02-21",
      "verdict": "BACKFILL_REQUIRED | CLEAN | PARTIAL"
    }

DISK SAFETY:
    Read-only; does not write to any DB. Safe to run with daemon active.
"""
