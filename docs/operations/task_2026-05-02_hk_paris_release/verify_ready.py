#!/usr/bin/env python3
"""Step 6 verification — count ready markets after the HK + Paris release."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_scanner import find_weather_markets


def main() -> int:
    markets = find_weather_markets(min_hours_to_resolution=6.0)
    conn = sqlite3.connect("state/zeus-world.db")
    ready = 0
    per_city: dict[str, int] = {}
    pending: list[tuple[str, str, str, int, int]] = []
    for m in markets:
        city = getattr(m["city"], "name", str(m["city"]))
        metric = m.get("metric") or m.get("temperature_metric")
        target = m.get("target_date")
        n_fc = conn.execute(
            "SELECT COUNT(*) FROM ensemble_snapshots_v2 "
            "WHERE city=? AND target_date=? AND temperature_metric=?",
            (city, target, metric),
        ).fetchone()[0]
        n_pl = conn.execute(
            "SELECT COUNT(*) FROM platt_models_v2 "
            "WHERE cluster=? AND temperature_metric=? AND is_active=1",
            (city, metric),
        ).fetchone()[0]
        if n_fc > 0 and n_pl > 0:
            ready += 1
            per_city[city] = per_city.get(city, 0) + 1
        else:
            pending.append((city, target, metric, n_fc, n_pl))
    print(f"ready: {ready}/{len(markets)}")
    for c in sorted(per_city):
        print(f"  {c}: {per_city[c]}")
    if pending:
        print(f"\nNOT READY ({len(pending)}):")
        for row in pending[:30]:
            print(f"  {row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
