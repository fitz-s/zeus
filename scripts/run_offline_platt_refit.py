# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=2026-05-24
# Authority basis: ENS full_transport_v1 REFIT task 2026-05-24
#   (docs/operations/ENS_REFIT_PLAN_2026-05-24.md).
# Purpose: Drive refit_all_v2() against an isolated staging DB; rebuild-complete
#   sentinel gate preserved; supports --error-model flag.
# Reuse: Run after run_offline_calibration_rebuild.py completes.
"""Offline driver: run refit_all_v2() against an ISOLATED staging DB.

Mirrors run_offline_calibration_rebuild.py. `refit_platt.py main()` wraps the
refit in an operator promotion preflight that guards the SHARED world DB. This
driver calls `refit_all_v2` directly against an explicit isolated DB (refusing
the canonical world DB). The in-function rebuild-complete sentinel gate
(`_assert_rebuild_complete_for_refit_source`) is PRESERVED — we do not bypass it
— so the refit still refuses to train on an incomplete rebuild scope.

USAGE:
    python scripts/run_offline_platt_refit.py --db /tmp/iso.db \
        --temperature-metric high --data-version <TIGGE> \
        --rebuild-n-mc 10000 --error-model full_transport_v1
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline isolated platt_models_v2 refit.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--temperature-metric", default="all", choices=("high", "low", "all"))
    ap.add_argument("--data-version", default=None)
    ap.add_argument("--city", default=None)
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--rebuild-n-mc", type=int, default=None)
    ap.add_argument("--error-model", default="none")
    args = ap.parse_args()

    from scripts.refit_platt import refit_all_v2  # noqa: PLC0415
    from scripts.rebuild_calibration_pairs_v2 import (  # noqa: PLC0415
        _resolve_isolated_calibration_write_db_path,
    )
    from src.state.db import init_schema  # noqa: PLC0415
    from src.state.schema.v2_schema import apply_v2_schema  # noqa: PLC0415

    write_db_path = _resolve_isolated_calibration_write_db_path(
        args.db, script_name="run_offline_platt_refit.py"
    )
    conn = sqlite3.connect(write_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 600000")
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    apply_v2_schema(conn)
    try:
        per_metric = refit_all_v2(
            conn,
            dry_run=False,
            force=True,
            strict=False,
            temperature_metric=args.temperature_metric,
            city_filter=args.city,
            start_date=args.start_date,
            end_date=args.end_date,
            data_version_filter=args.data_version,
            rebuild_n_mc=args.rebuild_n_mc,
            error_model_family=args.error_model,
        )
        conn.commit()
    finally:
        conn.close()
    any_refused = any(s.refused for s in per_metric.values())
    return 1 if any_refused else 0


if __name__ == "__main__":
    sys.exit(main())
