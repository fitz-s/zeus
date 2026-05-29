# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=2026-05-24
# Authority basis: ENS full_transport_v1 REFIT task 2026-05-24
#   (docs/operations/ENS_REFIT_PLAN_2026-05-24.md).
# Purpose: Drive rebuild_all_v2() against an isolated staging DB (never the shared
#   world DB); supports --error-model flag for full_transport_v1 corrected rebuild.
# Reuse: Run after seed_isolated_calibration_db.py; before run_offline_platt_refit.py.
"""Offline driver: run rebuild_all_v2() against an ISOLATED staging DB.

`rebuild_calibration_pairs.py main()` wraps the rebuild in an operator
promotion preflight (`_assert_rebuild_preflight_ready`) + bulk writer lock that
guard the SHARED world DB. For an isolated, single-purpose staging rebuild
(no promotion), this driver invokes the same library function
(`rebuild_all_v2`) directly against an explicit isolated DB. It refuses the
canonical shared world DB via the same guard the script uses.

The rebuild SELECTs only `spec.allowed_data_version` (TIGGE archive); the
isolated DB was seeded (scripts/seed_isolated_calibration_db.py) with the live
source tables verbatim, so the same physical rows + training_allowed flags drive
both the rebuild and the predictive-error fit.

USAGE:
    python scripts/run_offline_calibration_rebuild.py --db /tmp/iso.db \
        --temperature-metric high --city "San Francisco" \
        --n-mc 1000 --workers 1 --mc-seed-base 42 --error-model full_transport_v1
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline isolated calibration_pairs_v2 rebuild.")
    ap.add_argument("--db", required=True, help="Isolated staging DB (must not be the shared world DB).")
    ap.add_argument("--temperature-metric", default="all", choices=("high", "low", "all"))
    ap.add_argument(
        "--data-version", default=None,
        help="Scope the rebuild to one snapshot data_version (e.g. the TIGGE "
             "archive). Recommended: pass the metric's allowed_data_version so "
             "only the TIGGE-archive snapshots are rebuilt into pairs (the "
             "in-spec OpenData rows still feed the error-model residuals).",
    )
    ap.add_argument("--city", default=None)
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--n-mc", type=int, default=None)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--mc-seed-base", type=int, default=None)
    ap.add_argument("--error-model", default=None)
    args = ap.parse_args()

    from scripts.rebuild_calibration_pairs import (  # noqa: PLC0415
        _resolve_isolated_calibration_write_db_path,
        rebuild_all_v2,
    )
    from src.state.db import init_schema  # noqa: PLC0415
    from src.state.schema.v2_schema import apply_canonical_schema  # noqa: PLC0415

    write_db_path = _resolve_isolated_calibration_write_db_path(
        args.db, script_name="run_offline_calibration_rebuild.py"
    )

    conn = sqlite3.connect(write_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 600000")
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    apply_canonical_schema(conn)
    try:
        per_metric = rebuild_all_v2(
            conn,
            dry_run=False,
            force=True,
            city_filter=args.city,
            start_date=args.start_date,
            end_date=args.end_date,
            data_version_filter=args.data_version,
            temperature_metric=args.temperature_metric,
            n_mc=args.n_mc,
            db_path=write_db_path,
            workers=args.workers,
            mc_seed_base=args.mc_seed_base,
            error_model_family=args.error_model,
        )
    finally:
        conn.close()
    any_refused = any(s.refused for s in per_metric.values())
    return 1 if any_refused else 0


if __name__ == "__main__":
    sys.exit(main())
