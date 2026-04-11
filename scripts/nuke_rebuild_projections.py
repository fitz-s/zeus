#!/usr/bin/env python3
"""Post-nuke projection verifier and rebuilder.

Reads position_events from zeus-{mode}.db, folds each position's event
stream to compute the canonical terminal phase, and aligns
position_current.phase to match. Also flags any residual duplicate events
(bug #9 symptom) that the nuke step 2.3 should have removed.

Usage:
    python3 scripts/nuke_rebuild_projections.py --mode paper
    python3 scripts/nuke_rebuild_projections.py --mode paper --dry-run
    python3 scripts/nuke_rebuild_projections.py --mode live

Pre-conditions:
    - Daemons must be stopped (the script takes exclusive writes)
    - WAL should be checkpointed before running (runbook §1 handles this)
    - Snapshot already taken (runbook §1)
    - Nuke step 2.3 (duplicate SETTLED delete) already executed OR not yet,
      depending on whether you want to see duplicate counts in the report

Exit codes:
    0 — pass: no duplicates, no phase mismatches, position_current aligned
    1 — warnings: phase mismatches found and fixed (or --dry-run prevented fix)
    2 — hard failure: duplicate events remain, or DB inaccessible
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = REPO_ROOT / "state"

TERMINAL_PHASES = frozenset({"settled", "voided", "admin_closed", "quarantined"})


def open_db(mode: str) -> sqlite3.Connection:
    db_path = STATE_DIR / f"zeus-{mode}.db"
    if not db_path.exists():
        print(f"FATAL: {db_path} does not exist", file=sys.stderr)
        sys.exit(2)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_events_by_position(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    """Return {position_id: [rows ordered by (occurred_at, sequence_no)]}."""
    rows = conn.execute(
        """
        SELECT position_id, event_id, event_type, phase_before, phase_after,
               sequence_no, occurred_at
        FROM position_events
        ORDER BY position_id, occurred_at, sequence_no
        """
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["position_id"])].append(row)
    return grouped


def detect_duplicate_events(
    events_by_position: dict[str, list[sqlite3.Row]],
) -> list[tuple[str, str, int]]:
    """Detect (position_id, event_type, count) where count > 1 on event types
    that should be idempotent (SETTLED, ADMIN_VOIDED).
    """
    idempotent_types = frozenset({"SETTLED", "ADMIN_VOIDED"})
    duplicates: list[tuple[str, str, int]] = []
    for position_id, rows in events_by_position.items():
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            counts[str(row["event_type"])] += 1
        for event_type, count in counts.items():
            if event_type in idempotent_types and count > 1:
                duplicates.append((position_id, event_type, count))
    return duplicates


def fold_terminal_phase(rows: list[sqlite3.Row]) -> str | None:
    """Return the last phase_after value across the position's event stream.

    The fold is simple because position_events already enforces legal
    transitions via the CHECK constraint and triggers. We just need the
    final resting phase.
    """
    for row in reversed(rows):
        phase_after = row["phase_after"]
        if phase_after:
            return str(phase_after)
    return None


def fetch_current_phases(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT position_id, phase FROM position_current"
    ).fetchall()
    return {str(r["position_id"]): str(r["phase"]) for r in rows}


def apply_phase_updates(
    conn: sqlite3.Connection,
    updates: list[tuple[str, str]],
    *,
    dry_run: bool,
) -> int:
    if dry_run:
        return 0
    cur = conn.cursor()
    for position_id, phase in updates:
        cur.execute(
            "UPDATE position_current SET phase = ?, updated_at = datetime('now') WHERE position_id = ?",
            (phase, position_id),
        )
    conn.commit()
    return len(updates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=["paper", "live"],
        help="Which zeus-{mode}.db to rebuild",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report mismatches but do not apply UPDATE statements",
    )
    args = parser.parse_args()

    conn = open_db(args.mode)
    print(f"== nuke_rebuild_projections mode={args.mode} dry_run={args.dry_run}")

    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        print(f"FATAL: integrity_check={integrity}", file=sys.stderr)
        return 2
    print(f"integrity_check: ok")

    total_events = conn.execute(
        "SELECT COUNT(*) FROM position_events"
    ).fetchone()[0]
    total_current = conn.execute(
        "SELECT COUNT(*) FROM position_current"
    ).fetchone()[0]
    print(f"position_events rows: {total_events}")
    print(f"position_current rows: {total_current}")

    events_by_position = fetch_events_by_position(conn)
    unique_positions = len(events_by_position)
    print(f"unique position_ids in position_events: {unique_positions}")

    duplicates = detect_duplicate_events(events_by_position)
    if duplicates:
        print("\n!! DUPLICATE IDEMPOTENT EVENTS REMAIN:")
        for position_id, event_type, count in duplicates:
            print(f"   {position_id} {event_type} x{count}")
        print(
            f"\n!! Step 2.3 of the runbook (delete duplicate SETTLED rows) "
            f"is either incomplete or new duplicates appeared. Refusing "
            f"to write projections."
        )
        return 2
    print("no duplicate idempotent events detected")

    current_phases = fetch_current_phases(conn)

    mismatches: list[tuple[str, str, str]] = []
    missing_current: list[str] = []
    for position_id, rows in events_by_position.items():
        folded_phase = fold_terminal_phase(rows)
        if folded_phase is None:
            continue
        current_phase = current_phases.get(position_id)
        if current_phase is None:
            missing_current.append(position_id)
            continue
        if current_phase != folded_phase:
            mismatches.append((position_id, current_phase, folded_phase))

    if missing_current:
        print(
            f"\n!! {len(missing_current)} position_ids have events but no "
            f"position_current row:"
        )
        for pid in missing_current[:10]:
            print(f"   {pid}")
        if len(missing_current) > 10:
            print(f"   ... +{len(missing_current) - 10} more")
        print(
            "\n!! These positions cannot be phase-aligned because there is "
            "no row to UPDATE. The rebuild script does not INSERT new rows "
            "because position_current rows have many fields that only the "
            "monitor loop can populate. Investigate before proceeding."
        )

    if mismatches:
        print(f"\n!! {len(mismatches)} phase mismatches detected:")
        for position_id, current_phase, folded_phase in mismatches:
            marker = "*" if folded_phase in TERMINAL_PHASES else " "
            print(f"  {marker} {position_id}: current={current_phase} fold={folded_phase}")

        updates = [(pid, phase) for pid, _, phase in mismatches]
        applied = apply_phase_updates(conn, updates, dry_run=args.dry_run)
        if args.dry_run:
            print(f"\n--dry-run: would have UPDATEd {len(updates)} rows")
            return 1
        print(f"\napplied {applied} phase UPDATEs to position_current")

        still = fetch_current_phases(conn)
        remaining = [
            pid
            for pid, _, fold in mismatches
            if still.get(pid) != fold
        ]
        if remaining:
            print(
                f"FATAL: {len(remaining)} rows still mismatched after UPDATE; "
                f"see {remaining[:5]}",
                file=sys.stderr,
            )
            return 2
    else:
        print("position_current.phase aligned with event fold (no mismatches)")

    # Summary by terminal/non-terminal phase
    terminal_count = sum(
        1 for phase in current_phases.values() if phase in TERMINAL_PHASES
    )
    active_count = len(current_phases) - terminal_count
    print(f"\nfinal position_current: {active_count} active / {terminal_count} terminal")

    conn.close()
    return 1 if mismatches or missing_current else 0


if __name__ == "__main__":
    sys.exit(main())
