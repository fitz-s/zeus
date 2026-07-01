#!/usr/bin/env python3
# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §5 (INV-PROJ-1);
#   consult round-3 (thread 6a42bc3d) — "projection recomputability: recompute position_current.phase from
#   source facts, assert zero-diff." The event log (position_events.phase_after) is the source fact; the
#   projector (state/projection.py:upsert_position_current) keeps position_current.phase in sync with the
#   latest phase-changing event (state/engine/lifecycle_events.py). A divergence = a position_current writer
#   that bypassed the event-log + projector path — the multi-writer drift the atlas §7D single-owner fix removes.

"""INV-PROJ-1 replay-diff: position_current.phase must equal the latest non-null
position_events.phase_after per position.

Usage (read-only against the live trades DB):
    python3 scripts/dev/replay_position_phase.py --db state/zeus_trades.db --assert-no-diff

Exit code 1 (with --assert-no-diff) when any position's materialized phase has drifted from the
event log. Without --assert-no-diff it reports and exits 0 (diagnostic mode).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

# Per position, take the phase_after of the highest-sequence_no event that actually set a phase
# (phase_after IS NOT NULL — e.g. MONITOR_REFRESHED events carry NULL and must be skipped), then
# compare it to the materialized position_current.phase.
_DRIFT_SQL = """
SELECT pc.position_id            AS position_id,
       pc.phase                  AS stored_phase,
       latest.phase_after        AS event_phase_after
FROM position_current pc
JOIN (
    SELECT pe.position_id, pe.phase_after
    FROM position_events pe
    WHERE pe.phase_after IS NOT NULL
      AND pe.sequence_no = (
          SELECT MAX(pe2.sequence_no)
          FROM position_events pe2
          WHERE pe2.position_id = pe.position_id
            AND pe2.phase_after IS NOT NULL
      )
) latest ON latest.position_id = pc.position_id
WHERE pc.phase IS NOT NULL
  AND pc.phase <> latest.phase_after
ORDER BY pc.position_id
"""


def find_phase_projection_drift(conn: sqlite3.Connection) -> list[dict]:
    """Return the positions whose materialized phase disagrees with the latest phase-setting event.

    Empty list == INV-PROJ-1 holds (the phase projection is recomputable / consistent with facts).
    """
    cur = conn.execute(_DRIFT_SQL)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="INV-PROJ-1 position_current.phase replay-diff")
    ap.add_argument("--db", required=True, help="path to the trades DB (opened read-only)")
    ap.add_argument(
        "--assert-no-diff",
        action="store_true",
        help="exit 1 if any position's phase has drifted from the event log",
    )
    args = ap.parse_args(argv)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        drift = find_phase_projection_drift(conn)
    finally:
        conn.close()

    if not drift:
        print("INV-PROJ-1 OK: position_current.phase == latest event phase_after for every position.")
        return 0

    print(f"INV-PROJ-1 DRIFT: {len(drift)} position(s) — stored phase != latest event phase_after:")
    for d in drift[:100]:
        print(f"  {d['position_id']}: stored={d['stored_phase']!r} event={d['event_phase_after']!r}")
    if len(drift) > 100:
        print(f"  ... and {len(drift) - 100} more")
    return 1 if args.assert_no_diff else 0


if __name__ == "__main__":
    sys.exit(_main())
