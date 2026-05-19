# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/critic_1_pr1_settlement.md P7-3
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
"""
Migration script: quarantine settlement_commands in-flight at the era flip boundary.

PURPOSE (Critic 1 P7-3):
    At the moment of era flip (2026-02-21), any settlement_commands rows with
    status IN ('SUBMITTED', 'PENDING_FILL', 'PARTIAL') were logically in-flight
    under the UMA_OO_V2 era but may have settled under INTERNAL_RESOLVER_POST_2026_02_21.
    These rows are ambiguous and must be quarantined for operator review.

USAGE:
    python scripts/migrate_settlement_commands_in_flight_at_era_flip.py
        [--apply]
        [--era-flip-date DATE]   (default: 2026-02-21)

    --apply is REQUIRED to write; without it the script runs in dry-run mode.

DISK SAFETY:
    PRAGMA busy_timeout=5000. Use get_forecasts_connection_with_world() for
    ATTACH+SAVEPOINT. No bare sqlite3.connect().
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_DEFAULT_ERA_FLIP_DATE = "2026-02-21"
_IN_FLIGHT_STATUSES = ("SUBMITTED", "PENDING_FILL", "PARTIAL")

_QUARANTINE_DDL = """
CREATE TABLE IF NOT EXISTS settlement_commands_era_quarantine (
    id INTEGER PRIMARY KEY,
    original_command_id INTEGER NOT NULL,
    era_flip_date TEXT NOT NULL,
    operator_classification TEXT,
    quarantined_at TEXT NOT NULL,
    original_provenance_json TEXT NOT NULL
)
"""


def run_migration(
    *,
    apply: bool = False,
    era_flip_date: str = _DEFAULT_ERA_FLIP_DATE,
) -> dict:
    """Run the quarantine migration. Returns a summary dict."""
    from src.state.db import get_forecasts_connection_with_world

    status_placeholders = ",".join(["?" for _ in _IN_FLIGHT_STATUSES])

    with get_forecasts_connection_with_world() as conn:
        conn.execute("PRAGMA busy_timeout = 5000")

        # Find settlement_commands in-flight at era flip boundary
        # Table may not exist — handle gracefully
        try:
            in_flight_rows = conn.execute(
                f"""
                SELECT id, city, target_date, status, created_at, completed_at, provenance_json
                FROM settlement_commands
                WHERE status IN ({status_placeholders})
                  AND created_at < ?
                  AND (completed_at IS NULL OR completed_at >= ?)
                ORDER BY created_at
                """,
                (*_IN_FLIGHT_STATUSES, era_flip_date, era_flip_date),
            ).fetchall()
        except Exception as exc:
            return {
                "mode": "dry_run" if not apply else "apply",
                "error": f"settlement_commands table query failed: {exc}",
                "note": "Table may not exist — no commands to quarantine",
                "quarantine_count": 0,
            }

        in_flight_count = len(in_flight_rows)

        if not apply:
            return {
                "mode": "dry_run",
                "era_flip_date": era_flip_date,
                "in_flight_commands_found": in_flight_count,
                "statuses_checked": list(_IN_FLIGHT_STATUSES),
                "apply_with": "--apply to execute writes",
            }

        if in_flight_count == 0:
            return {
                "mode": "apply",
                "era_flip_date": era_flip_date,
                "quarantined": 0,
                "verdict": "NO_IN_FLIGHT_COMMANDS",
            }

        # Create quarantine table and perform migration
        conn.execute("SAVEPOINT era_flip_quarantine")
        try:
            conn.execute(_QUARANTINE_DDL)
            quarantined_at = datetime.now(timezone.utc).isoformat()
            quarantined = 0

            for row in in_flight_rows:
                row_dict = dict(zip(
                    ("id", "city", "target_date", "status", "created_at", "completed_at", "provenance_json"),
                    row,
                ))
                conn.execute(
                    """
                    INSERT INTO settlement_commands_era_quarantine
                        (original_command_id, era_flip_date, quarantined_at, original_provenance_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row_dict["id"],
                        era_flip_date,
                        quarantined_at,
                        row_dict.get("provenance_json") or "{}",
                    ),
                )
                conn.execute(
                    "UPDATE settlement_commands SET status = 'ERA_QUARANTINED' WHERE id = ?",
                    (row_dict["id"],),
                )
                quarantined += 1

            conn.execute("RELEASE SAVEPOINT era_flip_quarantine")
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT era_flip_quarantine")
            return {
                "mode": "apply",
                "error": f"Migration failed, rolled back: {exc}",
                "quarantined": 0,
            }

    return {
        "mode": "apply",
        "era_flip_date": era_flip_date,
        "quarantined": quarantined,
        "verdict": "OPERATOR_REVIEW_REQUIRED" if quarantined > 0 else "NO_IN_FLIGHT_COMMANDS",
        "note": (
            f"Quarantined {quarantined} in-flight commands. "
            "Operator must classify each via settlement_commands_era_quarantine."
        ) if quarantined > 0 else "No in-flight commands found at era flip boundary.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quarantine settlement_commands in-flight at the era flip boundary."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write changes (default: dry-run only)"
    )
    parser.add_argument(
        "--era-flip-date", default=_DEFAULT_ERA_FLIP_DATE,
        help=f"Era flip boundary date (default: {_DEFAULT_ERA_FLIP_DATE})"
    )
    args = parser.parse_args()

    if not args.apply:
        print("DRY-RUN mode (pass --apply to write changes)", file=sys.stderr)

    result = run_migration(apply=args.apply, era_flip_date=args.era_flip_date)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
