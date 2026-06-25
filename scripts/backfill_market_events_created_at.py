# Created: 2026-05-30
# Last reused or audited: 2026-05-30
# Authority basis: TOPOLOGY_CLOCK_MISSING live-throughput gate.
#   src/engine/event_reactor_adapter.py::_evidence_clock_from_topology_row reads
#   market_events.created_at (among the discovered_at/captured_at/.../created_at
#   key list) as the topology clock. Every existing market_events row has
#   created_at NULL (writer never propagated it pre-fix), so the reactor raises
#   TOPOLOGY_CLOCK_MISSING and the family is rejected before scoring.
"""One-time backfill: populate market_events.created_at for NULL rows.

The writer fix (src/data/market_scanner.py) guarantees FUTURE rows carry a
clock, but INSERT OR IGNORE never rewrites the ~18.5k existing NULL-clock
rows the live reactor reads right now. This heals the live state.

Backfill value semantics (honest, conservative):
    created_at := ISO-8601(recorded_at)

``recorded_at`` is the row's true persistence timestamp (CURRENT_TIMESTAMP at
insert), stored space-separated and naive UTC. We rewrite it as a tz-aware
ISO-8601 string (``T`` separator + ``+00:00``) so the reactor's _parse_utc
accepts it. Using the PERSISTED time as the clock is conservative: it is the
weakest (latest) of the three evidence-clock slots, so the backfill never
claims a market was source-available earlier than it actually was — it cannot
fabricate a causality advantage.

K1: market_events is a forecast-class table (zeus-forecasts.db). Writes go
through the canonical forecasts connection with write_class="bulk" so the
backfill uses the BULK flock and does not contend with the live daemon's LIVE
writer lock (K1 split design).

Usage:
    python scripts/backfill_market_events_created_at.py --dry-run
    python scripts/backfill_market_events_created_at.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.state.db import get_forecasts_connection  # noqa: E402


def _to_iso_utc(recorded_at: str) -> str | None:
    """Convert a space-separated naive UTC timestamp to tz-aware ISO-8601.

    '2026-05-30 17:39:12' -> '2026-05-30T17:39:12+00:00'
    Returns None if the value is empty/unparseable so the caller can skip it
    rather than write garbage.
    """
    if not recorded_at or not isinstance(recorded_at, str):
        return None
    value = recorded_at.strip()
    if not value:
        return None
    # Normalise separator and timezone marker.
    iso = value.replace(" ", "T", 1)
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    if "+" not in iso and "-" not in iso[10:]:
        # No offset present -> it is naive UTC; stamp +00:00.
        iso = iso + "+00:00"
    # Validate it round-trips through fromisoformat (same parser family the
    # reactor's _parse_utc uses).
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be updated without writing.",
    )
    args = parser.parse_args()

    conn = get_forecasts_connection(write_class="bulk")
    try:
        rows = conn.execute(
            "SELECT event_id, recorded_at FROM market_events WHERE created_at IS NULL"
        ).fetchall()
        total_null = len(rows)
        updates: list[tuple[str, int]] = []
        unparseable = 0
        for row in rows:
            event_id = row["event_id"]
            iso = _to_iso_utc(row["recorded_at"])
            if iso is None:
                unparseable += 1
                continue
            updates.append((iso, event_id))

        print(f"[INFO] NULL-created_at rows: {total_null}", file=sys.stderr)
        print(f"[INFO] backfillable (recorded_at parseable): {len(updates)}", file=sys.stderr)
        print(f"[INFO] unparseable recorded_at (skipped): {unparseable}", file=sys.stderr)

        if args.dry_run:
            print("[DRY-RUN] no rows written", file=sys.stderr)
            return 0

        if not updates:
            print("[INFO] nothing to backfill", file=sys.stderr)
            return 0

        conn.executemany(
            "UPDATE market_events SET created_at = ? WHERE event_id = ? AND created_at IS NULL",
            updates,
        )
        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM market_events WHERE created_at IS NULL"
        ).fetchone()[0]
        print(f"[DONE] backfilled {len(updates)} rows; remaining NULL: {remaining}", file=sys.stderr)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
