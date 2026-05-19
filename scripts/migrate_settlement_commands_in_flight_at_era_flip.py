# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/critic_1_pr1_settlement.md P7-3
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
"""
Migration script: quarantine settlement_commands in-flight at the era flip boundary.

SCAFFOLD — implementation body not yet written.

PURPOSE (Critic 1 P7-3):
    At the moment of era flip (2026-02-21), any settlement_commands rows with
    status IN ('SUBMITTED', 'PENDING_FILL', 'PARTIAL') were logically in-flight
    under the UMA_OO_V2 era but may have settled under INTERNAL_RESOLVER_POST_2026_02_21.
    These rows are ambiguous and must be quarantined for operator review.

    This script:
    1. Identifies in-flight commands at the era flip date
    2. Copies them to settlement_commands_era_quarantine (sibling table)
    3. Marks original rows with status='ERA_QUARANTINED'
    4. Produces a report for operator review

    The operator then classifies each quarantined command as:
      - CONFIRM_UMA: the UMA vote was definitive; keep UMA_OO_V2 era assignment
      - CONFIRM_INTERNAL: the internal resolver was authoritative; reassign era
      - DISCARD: the command was superseded and should not affect settlement counts

USAGE (SCAFFOLD pseudocode):
    python scripts/migrate_settlement_commands_in_flight_at_era_flip.py
        [--dry-run]
        [--era-flip-date DATE]   (default: 2026-02-21)

ERA_FLIP_DATE boundary:
    in_flight_commands = SELECT * FROM settlement_commands
        WHERE status IN ('SUBMITTED', 'PENDING_FILL', 'PARTIAL')
        AND created_at < era_flip_date
        AND (completed_at IS NULL OR completed_at >= era_flip_date)

SIBLING TABLE DDL (pseudocode — NOT executed here; table created in PR 1 migration):
    CREATE TABLE IF NOT EXISTS settlement_commands_era_quarantine (
        id INTEGER PRIMARY KEY,
        original_command_id INTEGER NOT NULL,
        era_flip_date TEXT NOT NULL,
        operator_classification TEXT,       -- NULL until operator reviews
        quarantined_at TEXT NOT NULL,
        original_provenance_json TEXT NOT NULL,
        FOREIGN KEY (original_command_id) REFERENCES settlement_commands(id)
    )

IMPLEMENTATION NOTES (SCAFFOLD pseudocode):
    1. BEGIN SAVEPOINT era_flip_quarantine
    2. SELECT in-flight commands at era flip boundary
    3. For each command:
       a. INSERT INTO settlement_commands_era_quarantine (original values)
       b. UPDATE settlement_commands SET status = 'ERA_QUARANTINED'
          WHERE id = command_id
    4. RELEASE SAVEPOINT era_flip_quarantine
    5. Print report: N commands quarantined, operator action required

DISK SAFETY:
    PRAGMA busy_timeout=5000. Use get_forecasts_connection_with_world() for
    ATTACH+SAVEPOINT. No bare sqlite3.connect().
    disk_sufficient_for_rebuild=false — no full DB copy.
"""
