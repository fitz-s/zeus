# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4
# SCAFFOLD: migration script outline — production execution pending T1 production pass

"""
Migration: create decision_events table on world DB.

Full CREATE TABLE SQL in scaffolds/t1_scaffold.md §3.
Idempotent — IF NOT EXISTS guards prevent errors on re-run.
Does NOT bump SCHEMA_VERSION (that happens in src/state/db.py production pass).

Production implementation steps:
  1. from src.state.db import get_world_connection
  2. conn = get_world_connection(write_class="bulk")
  3. conn.execute(CREATE TABLE IF NOT EXISTS decision_events (...))  -- 29 columns per §3
  4. conn.execute(CREATE INDEX IF NOT EXISTS idx_decision_events_market ...)
  5. conn.execute(CREATE INDEX IF NOT EXISTS idx_decision_events_strategy ...)
  6. conn.execute(CREATE INDEX IF NOT EXISTS idx_decision_events_time ...)
  7. conn.commit()
  8. print(f"decision_events table created/verified in {ZEUS_WORLD_DB_PATH}")

Usage (production pass):
    python scripts/migrate_decision_events_create_2026_05_19.py
"""

# SCAFFOLD: CREATE TABLE SQL in scaffolds/t1_scaffold.md §3 (full 29-column schema)
# Columns: decision_group_id, decision_seq, decision_time (PK+time)
#          + 8 identity + 4 probability + 5 PR-3 source context
#          + 8 PR-6 source context + 2 provenance


def main() -> None:
    """SCAFFOLD — production body pending T1 production pass."""
    raise NotImplementedError("SCAFFOLD — pending T1 production")


if __name__ == "__main__":
    main()
