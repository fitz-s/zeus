#!/usr/bin/env python3
# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Standalone hourly Open-Meteo archive tick — sweeps all 46 cities
#          per their dynamic end_date. Decouples ingest cadence from
#          src/main.py scheduler.
# Reuse: Mirrors src/main.py::_k2_hourly_instants_tick.
# Authority basis: docs/operations/task_2026-04-26_g10_ingest_scaffold/plan.md.
"""scripts/ingest/hourly_instants_tick.py — standalone hourly Open-Meteo tick.

Runnable as: `python scripts/ingest/hourly_instants_tick.py`

Mirrors src/main.py::_k2_hourly_instants_tick — calls
`src.data.hourly_instants_append.hourly_tick(conn)` for the 46-city sweep
with per-city dynamic end_date. 3-day rolling window allows Open-Meteo
archive ~2-3 day delay + catches promotions.

Isolation contract: see scripts/ingest/_shared.py docstring.
"""

from __future__ import annotations

import sys

from src.data.hourly_instants_append import hourly_tick

from scripts.ingest._shared import run_tick


def main() -> int:
    return run_tick("hourly_instants_tick", hourly_tick)


if __name__ == "__main__":
    sys.exit(main())
