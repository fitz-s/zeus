#!/usr/bin/env python3
# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Standalone solar (sunrise/sunset) daily tick — once per day,
#          fetches [today, today+14] per city. Deterministic astronomical
#          data so no backoff/retry semantics needed beyond network errors.
# Reuse: Mirrors src/main.py::_k2_solar_daily_tick.
# Authority basis: docs/operations/task_2026-04-26_g10_ingest_scaffold/plan.md.
"""scripts/ingest/solar_daily_tick.py — standalone solar daily tick.

Runnable as: `python scripts/ingest/solar_daily_tick.py`

Mirrors src/main.py::_k2_solar_daily_tick — calls
`src.data.solar_append.daily_tick(conn)`.

Isolation contract: see scripts/ingest/_shared.py docstring.
"""

from __future__ import annotations

import sys

from src.data.solar_append import daily_tick

from scripts.ingest._shared import run_tick


def main() -> int:
    return run_tick("solar_daily_tick", daily_tick)


if __name__ == "__main__":
    sys.exit(main())
