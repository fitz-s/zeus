# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md §"Schema impact"
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: SCAFFOLD migration for no_trade_events table-rebuild + tail_stress_scenarios creation (Phase 3 T2)
# Reuse: SCAFFOLD — run() raises NotImplementedError; T2 production pass owns execution; review _ROLLBACK SQL before running

"""Phase 3 T2 — Table-rebuild migration for no_trade_events (world DB).

Expands the no_trade_events CHECK constraint to accept 6 new SHOULDER_*
NoTradeReason members added in Phase 3 T2. Because SQLite does not support
ALTER TABLE ... ALTER COLUMN, this migration performs a CREATE-NEW / INSERT /
DROP-OLD / RENAME sequence under ATTACH+SAVEPOINT per INV-37.

Migration steps (all under a single SAVEPOINT for atomicity):
  1. CREATE new no_trade_events_v2 with expanded CHECK constraint
     (NoTradeReason enum-derived — auto-includes SHOULDER_* members).
  2. INSERT all rows from no_trade_events → no_trade_events_v2.
  3. DROP TABLE no_trade_events.
  4. ALTER TABLE no_trade_events_v2 RENAME TO no_trade_events.
  5. Re-create indices on the renamed table.
  6. Also create tail_stress_scenarios table (new in Phase 3 T2).

INV-37: single world-DB connection + SAVEPOINT (no ATTACH needed for this
single-DB migration; ATTACH is required only when reading from forecasts DB).
PRAGMA user_version is updated to SCHEMA_VERSION (18) inside the SAVEPOINT.

Usage
-----
    python scripts/migrate_no_trade_events_rebuild_phase3_t2.py [--dry-run] [--db PATH]

--dry-run: print the migration SQL without touching the DB.
--db PATH: override world DB path (default: from src.config STATE_DIR).

SCAFFOLD — main() raises NotImplementedError. Structure + ATTACH+SAVEPOINT
block is visible for review. T2 production pass owns execution.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL block — visible for review per SCAFFOLD discipline
# ---------------------------------------------------------------------------

_SAVEPOINT_NAME = "phase3_t2_no_trade_rebuild"

# Step 1: create new table with expanded CHECK (enum-derived — see schema module).
_STEP1_CREATE_NEW = """
-- Step 1: create no_trade_events_v2 with expanded CHECK (SHOULDER_* members).
-- _REASON_VALUES_SQL is populated at runtime by importing the schema module.
{create_v2_sql}
"""

# Step 2: copy all existing rows.
_STEP2_INSERT = """
-- Step 2: copy rows from no_trade_events → no_trade_events_v2.
INSERT INTO no_trade_events_v2
SELECT
    market_slug,
    temperature_metric,
    target_date,
    observation_time,
    decision_seq,
    reason,
    reason_detail,
    observed_at,
    schema_version
FROM no_trade_events;
"""

# Step 3: drop old table.
_STEP3_DROP = "DROP TABLE no_trade_events;"

# Step 4: rename new table.
_STEP4_RENAME = "ALTER TABLE no_trade_events_v2 RENAME TO no_trade_events;"

# Step 5: re-create indices.
_STEP5_INDEX_MARKET_TIME = """
CREATE INDEX IF NOT EXISTS idx_no_trade_events_market_time
    ON no_trade_events(market_slug, observed_at);
"""
_STEP5_INDEX_REASON = """
CREATE INDEX IF NOT EXISTS idx_no_trade_events_reason
    ON no_trade_events(reason);
"""

# Step 6: bump PRAGMA user_version.
_STEP6_USER_VERSION = "PRAGMA user_version = 18;"


def _build_create_v2_sql() -> str:
    """Build CREATE TABLE SQL for no_trade_events_v2 with expanded CHECK."""
    from src.state.schema.no_trade_events_schema import _REASON_VALUES_SQL

    return f"""
CREATE TABLE no_trade_events_v2 (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL CHECK (reason IN ({_REASON_VALUES_SQL})),
    reason_detail       TEXT,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16)),
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""


def run(db_path: Path, dry_run: bool = False) -> None:
    """Execute the table-rebuild migration under a SAVEPOINT (INV-37).

    SCAFFOLD — raises NotImplementedError. T2 production pass owns execution.
    """
    raise NotImplementedError(
        "T2 production pass owns run() execution. "
        "Review the SAVEPOINT block above before removing this guard."
    )
    # ── SAVEPOINT block (structure visible for review) ────────────────────
    # import sqlite3
    # create_v2_sql = _build_create_v2_sql()
    # conn = sqlite3.connect(str(db_path))
    # try:
    #     conn.execute(f"SAVEPOINT {_SAVEPOINT_NAME}")
    #     conn.execute(create_v2_sql)        # Step 1
    #     conn.execute(_STEP2_INSERT)        # Step 2
    #     conn.execute(_STEP3_DROP)          # Step 3
    #     conn.execute(_STEP4_RENAME)        # Step 4
    #     conn.execute(_STEP5_INDEX_MARKET_TIME)  # Step 5a
    #     conn.execute(_STEP5_INDEX_REASON)       # Step 5b
    #     # Step 6: also create tail_stress_scenarios
    #     from src.state.schema.tail_stress_scenarios_schema import (
    #         CREATE_TABLE_SQL as _TAIL_CREATE,
    #         CREATE_INDEX_MARKET_DATE_SQL as _TAIL_IDX,
    #     )
    #     conn.execute(_TAIL_CREATE)
    #     conn.execute(_TAIL_IDX)
    #     conn.execute(_STEP6_USER_VERSION)  # Step 6: bump user_version → 18
    #     conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
    #     conn.commit()
    # except Exception:
    #     conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT_NAME}")
    #     conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
    #     raise
    # finally:
    #     conn.close()


def main() -> None:
    """CLI entry point.

    SCAFFOLD — raises NotImplementedError until T2 production pass.
    """
    raise NotImplementedError(
        "T2 production pass owns main() execution. "
        "Run with --dry-run to preview SQL once guard is lifted."
    )
    # parser = argparse.ArgumentParser(description=__doc__)
    # parser.add_argument("--dry-run", action="store_true")
    # parser.add_argument("--db", type=Path, default=None)
    # args = parser.parse_args()
    # if args.db is None:
    #     from src.config import STATE_DIR
    #     args.db = STATE_DIR / "zeus-world.db"
    # run(args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    raise NotImplementedError("T2 production pass owns execution")
