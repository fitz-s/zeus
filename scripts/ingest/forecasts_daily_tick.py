#!/usr/bin/env python3
# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Standalone NWP forecasts daily tick — fetches [today-3, today+7]
#          × 5 models × 7 leads per city. Fires after ECMWF 00Z and GFS 06Z
#          runs are populated in the Previous Runs API (~UTC 07:00).
# Reuse: Mirrors src/main.py::_k2_forecasts_daily_tick.
# Authority basis: docs/operations/task_2026-04-26_g10_ingest_scaffold/plan.md.
"""scripts/ingest/forecasts_daily_tick.py — standalone NWP forecasts tick.

Runnable as: `python scripts/ingest/forecasts_daily_tick.py`

Mirrors src/main.py::_k2_forecasts_daily_tick — calls
`src.data.forecasts_append.daily_tick(conn)`.

Isolation contract: see scripts/ingest/_shared.py docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

# G10 syspath-shim (2026-04-26, con-nyx MAJOR #2): bootstrap sys.path
# so direct invocation works. See daily_obs_tick.py for rationale.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data.forecasts_append import daily_tick  # noqa: E402

from scripts.ingest._shared import run_tick  # noqa: E402


def main() -> int:
    return run_tick("forecasts_daily_tick", daily_tick)


if __name__ == "__main__":
    sys.exit(main())
