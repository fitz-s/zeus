#!/usr/bin/env python3
# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §5 (INV-PROJ-1);
#   consult round-3 (thread 6a42bc3d) — "projection recomputability: position_current.phase must be
#   recomputable from source facts."
#
# CORRECTNESS NOTE (2026-06-30): the first cut of this check compared position_current.phase to the
# phase_after of the *latest* event. That was WRONG — observational events (MONITOR_REFRESHED, REVIEW_REQUIRED)
# carry a non-authoritative phase_after that can lag/contradict a terminal transition (a MONITOR_REFRESHED
# fired after ADMIN_VOIDED still says phase_after=active; a REVIEW_REQUIRED after EXIT_ORDER_FILLED says
# quarantined). The A5 authority-aware reducer correctly keeps position_current at the terminal phase, so the
# naive "latest event" comparison produced FALSE POSITIVES. The sound invariant below is event-SOURCING: the
# stored phase must have been produced by SOME event in the position's history (a writer that sets a phase no
# event ever emitted has bypassed the event log — the real multi-writer drift). Phase REGRESSION (terminal ->
# earlier) is a separate invariant (INV-REDUCER-1 monotonicity), not this one.

"""INV-PROJ-1 replay-diff: every position_current.phase must be event-sourced.

A position whose materialized phase was never emitted as a position_events.phase_after (yet the
position HAS phase-setting events) was written by a path that bypassed the event log — the drift the
atlas §7D single-owner fix removes.

Usage (read-only against the live trades DB):
    python3 scripts/dev/replay_position_phase.py --db state/zeus_trades.db --assert-no-diff
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

# Drift = the stored phase is NOT among the phase_after values any event produced for this position,
# restricted to positions that DO have phase-setting events (else there is nothing to source against).
_DRIFT_SQL = """
SELECT pc.position_id AS position_id,
       pc.phase       AS stored_phase,
       (SELECT GROUP_CONCAT(DISTINCT pe.phase_after)
          FROM position_events pe
         WHERE pe.position_id = pc.position_id AND pe.phase_after IS NOT NULL) AS sourced_phases
FROM position_current pc
WHERE pc.phase IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM position_events e2
       WHERE e2.position_id = pc.position_id AND e2.phase_after IS NOT NULL
  )
  AND NOT EXISTS (
      SELECT 1 FROM position_events e1
       WHERE e1.position_id = pc.position_id AND e1.phase_after = pc.phase
  )
ORDER BY pc.position_id
"""


def find_phase_projection_drift(conn: sqlite3.Connection) -> list[dict]:
    """Return positions whose materialized phase was never emitted by any of their events.

    Empty list == INV-PROJ-1 holds (every stored phase is event-sourced).
    """
    cur = conn.execute(_DRIFT_SQL)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="INV-PROJ-1 position_current.phase event-sourcing replay-diff")
    ap.add_argument("--db", required=True, help="path to the trades DB (opened read-only)")
    ap.add_argument(
        "--assert-no-diff",
        action="store_true",
        help="exit 1 if any position's phase was never produced by an event (un-sourced)",
    )
    args = ap.parse_args(argv)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        drift = find_phase_projection_drift(conn)
    finally:
        conn.close()

    if not drift:
        print("INV-PROJ-1 OK: every position_current.phase is event-sourced (produced by some event).")
        return 0

    print(f"INV-PROJ-1 DRIFT: {len(drift)} position(s) with an un-sourced phase (no event ever produced it):")
    for d in drift[:100]:
        print(f"  {d['position_id']}: stored={d['stored_phase']!r} sourced_phases={d['sourced_phases']!r}")
    if len(drift) > 100:
        print(f"  ... and {len(drift) - 100} more")
    return 1 if args.assert_no_diff else 0


if __name__ == "__main__":
    sys.exit(_main())
