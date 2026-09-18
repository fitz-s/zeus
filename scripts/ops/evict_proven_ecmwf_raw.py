# Created: 2026-09-18
# Authority basis: docs/operations/current/encoding_audit_all_sources_2026-09-17.md +
#   the 2026-09-18 identity-drift fix (coordsha-suffixed source_run_id) in
#   src/data/ecmwf_open_data.py. Reuses that module's own plan/apply pair verbatim —
#   this script adds no retention policy of its own and no new delete authority.
# WRITER_LOCK: none needed. Planning uses a READ-ONLY forecasts connection and the
#   apply step only unlinks files on disk; it never writes to any database. The
#   in-daemon caller deliberately applies AFTER its canonical commit and outside the
#   BULK writer lock for the same reason (ecmwf_open_data.py: "Large raw files are
#   removed only after the canonical transaction commits and the BULK writer lock is
#   released").
"""One-shot eviction of raw ECMWF GRIB whose decode is already proven in the DB.

Why this exists: raw retention runs only as a tail step of a successful ingest cycle,
so a fix to the retention predicate does not take effect until the forecast-live daemon
both reloads AND completes a cycle. When the disk is already critical and a mesh restart
is refused (open positions require continuous capital monitoring), this applies the
same proof-gated plan out of process, immediately.

The proof gate is NOT reimplemented here. A group is eligible only if
_plan_decoded_open_data_raw_retention says so, which requires, per cycle+track:

  * every source_run recorded for that cycle is SUCCESS + COMPLETE + partial_run=0
    with expected_count == observed_count (one unproven coordsha pin retains the group);
  * all pins agree on observed_count;
  * ensemble_snapshots for that run/metric number exactly observed_count, every row
    authority='VERIFIED'.

Unknown filenames, symlinks, and anything the regex does not recognise are never
candidates.

The plan also sweeps spent transport sidecars (`.partial`, `.ranges.json`) — but only
those whose canonical `.grib2` already exists or is being evicted in the same plan. A
sidecar with no canonical is an in-flight or resumable download and is left alone.

Usage:
    python3 scripts/ops/evict_proven_ecmwf_raw.py              # dry run, prints the plan
    python3 scripts/ops/evict_proven_ecmwf_raw.py --apply      # delete the planned files
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data import ecmwf_open_data as eod  # noqa: E402
from src.state.db import get_connection_read_only  # noqa: E402


def _forecasts_db_path() -> Path:
    """Resolve the live forecasts DB.

    Deliberately explicit rather than via src.config: a worktree's STATE_DIR resolves
    to its own empty state/, which would silently plan against an empty database and
    (correctly but uselessly) find nothing eligible.
    """

    return _REPO_ROOT / "state" / "zeus-forecasts.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete the planned files; omitted, the script only prints the plan",
    )
    parser.add_argument(
        "--raw-root",
        default=str(_REPO_ROOT / "51 source data"),
        help="root containing raw/ecmwf_open_ens/ecmwf (default: repo '51 source data')",
    )
    parser.add_argument(
        "--forecasts-db",
        default=None,
        help=(
            "explicit path to the live zeus-forecasts.db. Required when running from a "
            "worktree, whose own state/ is empty — planning against it would report "
            "nothing eligible, which is correct but useless."
        ),
    )
    args = parser.parse_args()

    db_path = (
        Path(args.forecasts_db) if args.forecasts_db else _forecasts_db_path()
    )
    if not db_path.exists():
        print(f"FORECASTS_DB_MISSING {db_path}", file=sys.stderr)
        return 2

    # get_connection_read_only, not a bare sqlite3.connect(): the writer-lock
    # antibody fails CI on direct connects, and this helper is the sanctioned
    # read-only open (query_only, bounded lock wait, no DB creation, no
    # write-oriented pragmas).
    conn = get_connection_read_only(db_path)
    try:
        plan = eod._plan_decoded_open_data_raw_retention(
            conn,
            raw_root=Path(args.raw_root),
            reference_date=datetime.now(timezone.utc).date(),
        )
    finally:
        conn.close()

    print(f"root                : {plan.root}")
    print(f"retention_days      : {eod._RAW_RETENTION_CALENDAR_DAYS}")
    print(f"eligible groups     : {plan.eligible_group_count}")
    print(f"retained groups     : {plan.retained_group_count}")
    print(f"unrecognized .grib2 : {plan.unrecognized_file_count}")
    print(f"files planned       : {len(plan.files)}")
    print(f"bytes planned       : {plan.planned_bytes / 1e9:.2f} GB")

    if not args.apply:
        for path in plan.files[:5]:
            print(f"  would delete: {path.parent.name}/{path.name}")
        if len(plan.files) > 5:
            print(f"  ... and {len(plan.files) - 5} more")
        print("\nDRY RUN — nothing deleted. Re-run with --apply.")
        return 0

    summary = eod._apply_decoded_open_data_raw_retention(plan)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary.get("status") in {"APPLIED", "NO_ELIGIBLE_RAW"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
