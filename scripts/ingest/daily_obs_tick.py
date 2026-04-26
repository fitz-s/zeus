#!/usr/bin/env python3
# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Standalone daily-observation tick (WU + HKO + Ogimet via the
#          bundled daily_tick at src/data/daily_obs_append.py). Decouples
#          the ingest cadence from src/main.py scheduler.
# Reuse: G10-source-split followup will fan out to per-source ticks
#        (wu_icao_tick, hko_tick, ogimet_tick) once daily_tick supports
#        a source filter.
# Authority basis: docs/operations/task_2026-04-26_g10_ingest_scaffold/plan.md
#   §1 + parent docs/operations/task_2026-04-26_live_readiness_completion/plan.md.
"""scripts/ingest/daily_obs_tick.py — standalone daily-observation tick.

Runnable as: `python scripts/ingest/daily_obs_tick.py`

Mirrors src/main.py::_k2_daily_obs_tick body — calls the same
`src.data.daily_obs_append.daily_tick(conn)` underneath. The bundled
function handles WU + HKO + Ogimet sources internally.

Imports comply with G10 isolation contract (no engine / execution /
strategy / signal / supervisor_api / control / observability / main).
Verified by tests/test_ingest_isolation.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

# G10 syspath-shim (2026-04-26, con-nyx MAJOR #2): bootstrap sys.path so
# `python scripts/ingest/daily_obs_tick.py` (direct invocation) resolves
# `src.*` and `scripts.*` from the repo root. Without this, default
# sys.path[0] is the script's directory and project imports fail with
# ModuleNotFoundError. Matches the convention in scripts/live_smoke_test.py.
# Both `python scripts/ingest/X.py` and `python -m scripts.ingest.X` work.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data.daily_obs_append import daily_tick  # noqa: E402

from scripts.ingest._shared import run_tick  # noqa: E402


def main() -> int:
    return run_tick("daily_obs_tick", daily_tick)


if __name__ == "__main__":
    sys.exit(main())
