# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: operator-authorized one-shot purge of permanently-unresolvable PARTIAL FSR events
# Background: 941 stale PARTIAL FORECAST_SNAPSHOT_READY events (source_run_completeness_status=PARTIAL)
# accumulated in opportunity_events because the first 12Z ingest ran with the contributes_to_target_extrema
# bug (observed_members=0 → completeness=PARTIAL). These events have frozen PARTIAL status in their payload
# and can NEVER pass the certificate gate (requires COMPLETE). Their snapshot IDs no longer exist (source_run
# was cleared+rewritten by the corrected reingest). This is an event-queue hygiene operation, not trade-state.
"""
One-shot admin script: purge stale PARTIAL FORECAST_SNAPSHOT_READY events.

Operator-authorized 2026-05-31. Drops the append-only triggers, deletes the 941
unresolvable PARTIAL FSR events, restores triggers. Leaves all COMPLETE FSR events intact.

Usage:
    cd /Users/leofitz/.openclaw/workspace-venus/zeus
    source .venv/bin/activate
    python scripts/purge_partial_fsr_events.py [--dry-run]
"""
import argparse
import sqlite3
import sys


WORLD_DB = "state/zeus-world.db"

DROP_NO_DELETE = "DROP TRIGGER IF EXISTS trg_opportunity_events_no_delete"
DROP_NO_UPDATE = "DROP TRIGGER IF EXISTS trg_opportunity_events_no_update"

RESTORE_NO_DELETE = """
CREATE TRIGGER IF NOT EXISTS trg_opportunity_events_no_delete
BEFORE DELETE ON opportunity_events
BEGIN
    SELECT RAISE(ABORT, 'opportunity_events is append-only');
END
"""

RESTORE_NO_UPDATE = """
CREATE TRIGGER IF NOT EXISTS trg_opportunity_events_no_update
BEFORE UPDATE ON opportunity_events
BEGIN
    SELECT RAISE(ABORT, 'opportunity_events is append-only');
END
"""

DELETE_PARTIAL = """
DELETE FROM opportunity_events
WHERE event_type = 'FORECAST_SNAPSHOT_READY'
  AND json_extract(payload_json, '$.source_run_completeness_status') = 'PARTIAL'
"""

COUNT_COMPLETE = """
SELECT COUNT(*) FROM opportunity_events
WHERE event_type = 'FORECAST_SNAPSHOT_READY'
  AND json_extract(payload_json, '$.source_run_completeness_status') = 'COMPLETE'
"""

COUNT_PARTIAL = """
SELECT COUNT(*) FROM opportunity_events
WHERE event_type = 'FORECAST_SNAPSHOT_READY'
  AND json_extract(payload_json, '$.source_run_completeness_status') = 'PARTIAL'
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Count without deleting")
    args = parser.parse_args()

    conn = sqlite3.connect(WORLD_DB)
    try:
        partial_count = conn.execute(COUNT_PARTIAL).fetchone()[0]
        complete_count = conn.execute(COUNT_COMPLETE).fetchone()[0]
        print(f"Pre-purge: PARTIAL={partial_count}, COMPLETE={complete_count}")

        if args.dry_run:
            print("DRY RUN — no changes made.")
            return

        if partial_count == 0:
            print("Nothing to delete.")
            return

        # Drop append-only guards, delete, restore guards — all in one connection.
        # Inner try/finally guarantees triggers are ALWAYS recreated and committed,
        # even if the DELETE/COMMIT raises; the outer finally only closes the connection.
        conn.execute(DROP_NO_DELETE)
        conn.execute(DROP_NO_UPDATE)
        try:
            conn.execute("BEGIN")
            cur = conn.execute(DELETE_PARTIAL)
            deleted = cur.rowcount
            conn.execute("COMMIT")
        except Exception:
            # Attempt rollback in case DELETE raised before COMMIT
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            # Restore triggers unconditionally, even on DELETE failure
            conn.execute(RESTORE_NO_DELETE)
            conn.execute(RESTORE_NO_UPDATE)
            conn.commit()

        complete_after = conn.execute(COUNT_COMPLETE).fetchone()[0]
        partial_after = conn.execute(COUNT_PARTIAL).fetchone()[0]

        print(f"Deleted {deleted} PARTIAL FSR events.")
        print(f"Post-purge: PARTIAL={partial_after}, COMPLETE={complete_after}")

        if partial_after != 0:
            print("WARNING: PARTIAL count not zero after delete!", file=sys.stderr)
            sys.exit(1)
        if complete_after != complete_count:
            print(f"WARNING: COMPLETE count changed {complete_count} → {complete_after}", file=sys.stderr)
            sys.exit(1)

        print("OK: triggers restored, COMPLETE events intact.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
