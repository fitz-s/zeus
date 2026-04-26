#!/usr/bin/env python3
# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Standalone hole-scanner daily patrol — runs HoleScanner.scan_all()
#          to find physical-table rows not yet in data_coverage and write
#          MISSING markers. Appenders pick up new MISSING rows via
#          find_pending_fills.
# Reuse: Mirrors src/main.py::_k2_hole_scanner_tick.
# Authority basis: docs/operations/task_2026-04-26_g10_ingest_scaffold/plan.md.
"""scripts/ingest/hole_scanner_tick.py — standalone hole-scanner tick.

Runnable as: `python scripts/ingest/hole_scanner_tick.py`

Mirrors src/main.py::_k2_hole_scanner_tick — instantiates HoleScanner
and runs scan_all(), logging compact summary per data_table.

Isolation contract: see scripts/ingest/_shared.py docstring.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# G10 syspath-shim (2026-04-26, con-nyx MAJOR #2): bootstrap sys.path
# so direct invocation works. See daily_obs_tick.py for rationale.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data.hole_scanner import HoleScanner  # noqa: E402

from scripts.ingest._shared import setup_tick_logging, world_connection  # noqa: E402


def main() -> int:
    logger = setup_tick_logging("hole_scanner_tick")
    try:
        with world_connection() as conn:
            scanner = HoleScanner(conn)
            results = scanner.scan_all()
            for r in results:
                logger.info("hole_scanner %s: %s", r.data_table.value, r.as_dict())
        return 0
    except Exception as exc:
        logger.exception("hole_scanner_tick failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
