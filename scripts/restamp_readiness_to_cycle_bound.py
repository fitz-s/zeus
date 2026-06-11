#!/usr/bin/env python3
# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ("仍未出单按rule1执行" → RULE-1 twin-clock
#   incident): readiness expiry derives from the single staleness authority
#   (replacement_readiness_expires_at = source_cycle_time + derived bound). Existing
#   readiness_state rows were stamped under the dead computed_at+3h clock and expired
#   while their cycles were still lawful; this ONE-SHOT restamps them to the lawful
#   bound. Forward stamping is fixed in code (materializer + request builder).
"""One-shot: restamp readiness_state.expires_at to the cycle-policy bound.

For each readiness_state row whose scope's newest CERTIFIED posterior carries a
source_cycle_time still within the staleness bound, set
expires_at = source_cycle_time + bound. Never extends beyond the law; never touches
rows whose cycle is already beyond the bound (those stay expired, correctly).

Usage: PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python scripts/restamp_readiness_to_cycle_bound.py [--apply]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_cycle_policy import (  # noqa: E402
    replacement_readiness_expires_at,
)
from src.state.db import _connect  # noqa: E402

UTC = timezone.utc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    now = datetime.now(UTC)
    conn = _connect(Path("state/zeus-forecasts.db"))
    try:
        rows = conn.execute(
            """
            SELECT r.rowid AS rid, r.city, r.target_local_date, r.temperature_metric,
                   r.status, r.expires_at,
                   (SELECT MAX(p.source_cycle_time) FROM forecast_posteriors p
                     WHERE p.city = r.city AND p.target_date = r.target_local_date
                       AND p.temperature_metric = r.temperature_metric
                       AND p.q_lcb_json IS NOT NULL) AS newest_certified_cycle
            FROM readiness_state r
            WHERE r.status = 'READY' OR r.status = 'LIVE_ELIGIBLE'
            """
        ).fetchall()
        plans = []
        for rid, city, td, metric, status, old_exp, cyc in rows:
            if not cyc:
                continue
            cycle = datetime.fromisoformat(str(cyc).replace("Z", "+00:00"))
            if cycle.tzinfo is None:
                cycle = cycle.replace(tzinfo=UTC)
            lawful = replacement_readiness_expires_at(cycle)
            if lawful <= now:
                continue  # cycle beyond bound: stays expired, correctly
            plans.append((rid, city, td, metric, str(old_exp), lawful.isoformat()))
        print(f"rows eligible for lawful restamp: {len(plans)}")
        for p in plans[:6]:
            print("  ", p[1], p[2], p[3], "old", p[4][:16], "-> new", p[5][:16])
        if not args.apply:
            print("DRY RUN (pass --apply to write)")
            return 0
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        n = 0
        for rid, *_rest, new_exp in [(p[0], p[5]) for p in plans]:
            cur.execute(
                "UPDATE readiness_state SET expires_at = ? WHERE rowid = ?",
                (new_exp, rid),
            )
            n += 1
        conn.commit()
        print(f"RESTAMPED {n} rows to the cycle-policy bound")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
