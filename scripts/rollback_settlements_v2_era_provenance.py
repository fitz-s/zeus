# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §H
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=2026-05-19
# Purpose: Restore settlements_v2 provenance_json rows from pre-backfill snapshot
# Reuse: operator_invoked only; requires --apply + snapshot file; never called by daemon
"""
Rollback script: restore pre-backfill provenance_json for settlements_v2 rows.

PURPOSE:
    Rollback a partial or complete provenance backfill by restoring
    provenance_json rows to the legacy 'harvester_live_uma_vote' form.
    Used when the backfill produced incorrect era assignments or PR 1 is reverted.

    IMPORTANT: This script requires a pre-backfill snapshot to restore from.
    The snapshot must be produced by the operator before running the backfill.

    SNAPSHOT FORMAT: JSON with {"rows": [...]} where each row contains:
        {"city": "...", "target_date": "YYYY-MM-DD", "temperature_metric": "high|low",
         "original_provenance_json": "{...}"}
    settlements_v2 is keyed by (city, target_date, temperature_metric).
    There is NO condition_id column in settlements_v2.

USAGE:
    python scripts/rollback_settlements_v2_era_provenance.py
        --snapshot PATH
        [--apply]
        [--row-cap N]

    --apply is REQUIRED to write; without it the script runs in dry-run mode.

DISK SAFETY:
    Same constraints as backfill: PRAGMA busy_timeout=5000, 500-row chunks,
    no bare sqlite3.connect(), use get_forecasts_connection_with_world().
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_BLEEDING_TAG = "harvester_live_uma_vote"


def run_rollback(
    snapshot_path: Path,
    *,
    apply: bool = False,
    row_cap: int = 500,
    chunk_size: int = 500,
) -> dict:
    """Run the rollback from snapshot. Returns a summary dict."""
    from src.state.db import get_forecasts_connection_with_world

    if not snapshot_path.exists():
        return {"error": f"Snapshot file not found: {snapshot_path}"}

    snapshot = json.loads(snapshot_path.read_text())
    rows_to_restore = snapshot.get("rows", [])
    if not rows_to_restore:
        return {"error": "Snapshot has no 'rows' key or is empty — cannot rollback"}

    # Limit to row_cap
    rows_to_restore = rows_to_restore[:row_cap]

    total_restored = 0
    total_errors = 0
    chunks_done = 0

    if not apply:
        return {
            "mode": "dry_run",
            "total_to_restore": len(rows_to_restore),
            "apply_with": "--apply to execute writes",
        }

    with get_forecasts_connection_with_world() as conn:
        conn.execute("PRAGMA busy_timeout = 5000")

        offset = 0
        while offset < len(rows_to_restore):
            chunk = rows_to_restore[offset : offset + chunk_size]
            savepoint_name = f"rollback_chunk_{chunks_done}"
            conn.execute(f"SAVEPOINT {savepoint_name}")
            try:
                for row_snap in chunk:
                    city = row_snap.get("city")
                    target_date = row_snap.get("target_date")
                    temperature_metric = row_snap.get("temperature_metric")
                    original_prov = row_snap.get("original_provenance_json")
                    # settlements_v2 UNIQUE key: (city, target_date, temperature_metric)
                    # There is no condition_id column in settlements_v2.
                    if not (city and target_date and temperature_metric) or original_prov is None:
                        total_errors += 1
                        continue
                    conn.execute(
                        """
                        UPDATE settlements_v2
                        SET provenance_json = ?
                        WHERE city = ? AND target_date = ? AND temperature_metric = ?
                        """,
                        (original_prov, city, target_date, temperature_metric),
                    )
                    total_restored += 1
                conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                chunks_done += 1
            except Exception as exc:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                print(f"ERROR in rollback chunk {chunks_done}: {exc}", file=sys.stderr)
                total_errors += len(chunk)
                chunks_done += 1

            offset += chunk_size

        # Verify post-rollback count
        remaining_clean = conn.execute(
            f"SELECT COUNT(*) FROM settlements_v2 WHERE provenance_json NOT LIKE '%{_BLEEDING_TAG}%'"
            " AND provenance_json IS NOT NULL AND provenance_json != '{}'"
        ).fetchone()[0]

    return {
        "mode": "apply",
        "total_rows_in_snapshot": len(snapshot.get("rows", [])),
        "total_restored": total_restored,
        "total_errors": total_errors,
        "chunks_done": chunks_done,
        "snapshot_queried_at_utc": snapshot.get("queried_at_utc"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rollback settlements_v2 era provenance backfill from snapshot."
    )
    parser.add_argument(
        "--snapshot", required=True,
        help="Path to JSON snapshot produced before backfill (REQUIRED)"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write changes (default: dry-run only)"
    )
    parser.add_argument("--row-cap", type=int, default=500, help="Max rows per run (default: 500)")
    parser.add_argument("--chunk-size", type=int, default=500, help="Rows per SAVEPOINT chunk (default: 500)")
    args = parser.parse_args()

    if not args.apply:
        print("DRY-RUN mode (pass --apply to write changes)", file=sys.stderr)

    result = run_rollback(
        Path(args.snapshot),
        apply=args.apply,
        row_cap=args.row_cap,
        chunk_size=args.chunk_size,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
