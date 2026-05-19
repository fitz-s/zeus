# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §H
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
#                  preflight/migration_dry_runs.json (2829 BLEEDING rows, daemon_active=true)
"""
Backfill script: rewrite provenance_json for BLEEDING settlements_v2 rows.

PURPOSE:
    Idempotently update provenance_json for rows in settlements_v2 that contain
    'harvester_live_uma_vote' (BLEEDING rows), replacing with typed era provenance.
    After successful backfill, the CI antibody query must return COUNT = 0.

    Target: 2829 BLEEDING rows (per preflight/migration_dry_runs.json).

USAGE:
    python scripts/backfill_settlements_v2_era_provenance.py [--apply] [--row-cap N] [--chunk-size N] [--since DATE]

    --apply is REQUIRED to write; without it the script runs in dry-run mode.

SAFETY CONSTRAINTS:
    - PRAGMA busy_timeout=5000 to coexist with live daemon
    - Use get_forecasts_connection_with_world() (INV-37 compliance)
    - 500-row chunks with SAVEPOINT between chunks
    - --apply required; dry-run is the default

DISK SAFETY NOTE:
    forecasts_db_bytes=49GB, world_db_bytes=38GB, free_space=22Gi.
    Do NOT create backup copies of either DB. Backfill is idempotent.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.state.settlement_writers import (  # noqa: E402
    _build_era_provenance,
    dispatch_era_basis,
)
from src.contracts.resolution_era import EraDispatchOutcome  # noqa: E402

_BLEEDING_TAG = "harvester_live_uma_vote"


def run_backfill(
    *,
    apply: bool = False,
    row_cap: int = 500,
    chunk_size: int = 500,
    since: str | None = None,
) -> dict:
    """Run the backfill. Returns a summary dict."""
    from src.state.db import get_forecasts_connection_with_world

    where_clauses = [f"provenance_json LIKE '%{_BLEEDING_TAG}%'"]
    params: list = []
    if since:
        where_clauses.append("settled_at >= ?")
        params.append(since)

    where_sql = "WHERE " + " AND ".join(where_clauses)

    total_processed = 0
    total_updated = 0
    total_errors = 0
    chunks_done = 0

    with get_forecasts_connection_with_world() as conn:
        conn.execute("PRAGMA busy_timeout = 5000")

        # Fetch BLEEDING rows up to row_cap
        all_rows = conn.execute(
            f"""
            SELECT rowid, city, settled_at, provenance_json
            FROM settlements_v2
            {where_sql}
            ORDER BY settled_at
            LIMIT {row_cap}
            """,
            params,
        ).fetchall()

        total_to_process = len(all_rows)

        if not apply:
            # Dry-run: just classify and report
            for row in all_rows:
                row_dict = dict(zip(("rowid", "city", "settled_at", "provenance_json"), row))
                settled_str = str(row_dict["settled_at"])[:10]
                try:
                    settled_date = date.fromisoformat(settled_str)
                    era_result = dispatch_era_basis(settled_date)
                    if era_result.outcome == EraDispatchOutcome.ERA_RESOLVED:
                        total_updated += 1
                    else:
                        total_errors += 1
                except Exception:
                    total_errors += 1

            return {
                "mode": "dry_run",
                "total_bleeding_found": total_to_process,
                "would_update": total_updated,
                "would_error": total_errors,
                "apply_with": "--apply to execute writes",
            }

        # Apply mode: chunked SAVEPOINT writes
        offset = 0
        while offset < len(all_rows):
            chunk = all_rows[offset : offset + chunk_size]
            savepoint_name = f"backfill_chunk_{chunks_done}"
            conn.execute(f"SAVEPOINT {savepoint_name}")
            try:
                for row in chunk:
                    row_dict = dict(zip(("rowid", "city", "settled_at", "provenance_json"), row))
                    settled_str = str(row_dict["settled_at"])[:10]
                    try:
                        settled_date = date.fromisoformat(settled_str)
                    except (ValueError, TypeError):
                        total_errors += 1
                        continue

                    era_result = dispatch_era_basis(settled_date)
                    if era_result.outcome != EraDispatchOutcome.ERA_RESOLVED:
                        total_errors += 1
                        continue

                    try:
                        existing_prov = json.loads(row_dict["provenance_json"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        existing_prov = {}

                    # Build era provenance — passes empty settlement dict since we only need era fields
                    era_prov = _build_era_provenance({}, era_result.era_basis, None)
                    merged = {**existing_prov, **era_prov}
                    # Remove legacy tag
                    merged.pop("reconstruction_method", None)
                    merged["reconstruction_method"] = era_prov["reconstruction_method"]

                    new_json = json.dumps(merged, sort_keys=True, default=str)
                    conn.execute(
                        "UPDATE settlements_v2 SET provenance_json = ? WHERE rowid = ?",
                        (new_json, row_dict["rowid"]),
                    )
                    total_updated += 1
                    total_processed += 1

                conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                chunks_done += 1
            except Exception as exc:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                print(f"ERROR in chunk {chunks_done}: {exc}", file=sys.stderr)
                total_errors += len(chunk)
                chunks_done += 1

            offset += chunk_size

        # Verify post-backfill count
        remaining = conn.execute(
            f"SELECT COUNT(*) FROM settlements_v2 WHERE provenance_json LIKE '%{_BLEEDING_TAG}%'"
        ).fetchone()[0]

    return {
        "mode": "apply",
        "total_bleeding_found": total_to_process,
        "total_updated": total_updated,
        "total_errors": total_errors,
        "chunks_done": chunks_done,
        "remaining_bleeding_rows": remaining,
        "verdict": "COMPLETE" if remaining == 0 else "PARTIAL",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill settlements_v2 era provenance for BLEEDING rows."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write changes (default: dry-run only)"
    )
    parser.add_argument("--row-cap", type=int, default=500, help="Max rows per run (default: 500)")
    parser.add_argument("--chunk-size", type=int, default=500, help="Rows per SAVEPOINT chunk (default: 500)")
    parser.add_argument("--since", default=None, help="Only backfill rows with settled_at >= DATE")
    args = parser.parse_args()

    if not args.apply:
        print("DRY-RUN mode (pass --apply to write changes)", file=sys.stderr)

    result = run_backfill(
        apply=args.apply,
        row_cap=args.row_cap,
        chunk_size=args.chunk_size,
        since=args.since,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
