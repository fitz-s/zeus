# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §H
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
"""
Audit script: classify settlements_v2 rows by era provenance status.

PURPOSE:
    Enumerate all rows in settlements_v2 (zeus-forecasts.db) and classify each
    by era provenance status:
      - CLEAN: has typed era in provenance_json matching expected era for settled_at
      - BLEEDING: has 'harvester_live_uma_vote' in provenance_json; needs backfill
      - ANOMALOUS: has era != expected for settled_at date (INV-ERA-1 violation)
      - MISSING_PROVENANCE: provenance_json is NULL, '{}', or empty

USAGE:
    python scripts/audit_settlements_v2_era_provenance.py [--output OUTFILE]

    Options:
      --output PATH   Write JSON report to PATH (default: stdout)
      --since DATE    Only audit rows with settled_at >= DATE (ISO format)
      --city CITY     Filter to one city

DISK SAFETY:
    Read-only; does not write to any DB. Safe to run with daemon active.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Repo root so we can import src
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.state.settlement_writers import ERA_CUTOVER_DATE  # noqa: E402


_BLEEDING_TAG = "harvester_live_uma_vote"
_INTERNAL_ERA = "internal_resolver_post_2026_02_21"
_UMA_ERA = "uma_oo_v2"


def _classify_row(row_dict: dict) -> str:
    """Classify a single settlements_v2 row by era provenance status."""
    provenance_raw = row_dict.get("provenance_json") or ""
    try:
        prov = json.loads(provenance_raw) if provenance_raw else {}
    except (json.JSONDecodeError, TypeError):
        prov = {}

    if not prov:
        return "MISSING_PROVENANCE"

    # BLEEDING: legacy tag still present
    prov_str = json.dumps(prov)
    if _BLEEDING_TAG in prov_str:
        return "BLEEDING"

    era_in_prov = prov.get("era")
    settled_at_str = row_dict.get("settled_at") or ""
    if settled_at_str:
        try:
            settled_date_str = str(settled_at_str)[:10]
            from datetime import date
            settled_date = date.fromisoformat(settled_date_str)
            # INV-ERA-1: uma_oo_v2 era on post-cutover date is anomalous
            if era_in_prov == _UMA_ERA and settled_date >= ERA_CUTOVER_DATE:
                return "ANOMALOUS"
            if era_in_prov == _INTERNAL_ERA and settled_date < ERA_CUTOVER_DATE:
                return "ANOMALOUS"
        except (ValueError, TypeError):
            pass

    if era_in_prov in (_INTERNAL_ERA, _UMA_ERA):
        return "CLEAN"

    return "MISSING_PROVENANCE"


def run_audit(
    db_path: Path,
    *,
    since: str | None = None,
    city: str | None = None,
) -> dict:
    """Run the audit and return a structured report dict."""
    uri = db_path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    where_clauses = []
    params: list = []
    if since:
        where_clauses.append("settled_at >= ?")
        params.append(since)
    if city:
        where_clauses.append("city = ?")
        params.append(city)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    rows = conn.execute(
        f"SELECT city, settled_at, provenance_json FROM settlements_v2 {where_sql} ORDER BY settled_at",
        params,
    ).fetchall()
    conn.close()

    status_counts: dict[str, int] = defaultdict(int)
    per_city_status: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in rows:
        row_dict = dict(row)
        status = _classify_row(row_dict)
        status_counts[status] += 1
        city_name = row_dict.get("city") or "UNKNOWN"
        per_city_status[city_name][status] += 1

    total_rows = len(rows)
    bleeding_count = status_counts.get("BLEEDING", 0)

    if bleeding_count == 0 and status_counts.get("ANOMALOUS", 0) == 0:
        verdict = "CLEAN"
    elif bleeding_count > 0:
        verdict = "BACKFILL_REQUIRED"
    else:
        verdict = "PARTIAL"

    per_city_bleeding = [
        {"city": c, "count": counts["BLEEDING"]}
        for c, counts in sorted(per_city_status.items())
        if counts.get("BLEEDING", 0) > 0
    ]

    return {
        "queried_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_rows": total_rows,
        "status_counts": dict(status_counts),
        "per_city_bleeding": per_city_bleeding,
        "era_cutover_date": ERA_CUTOVER_DATE.isoformat(),
        "verdict": verdict,
        "filters": {
            "since": since,
            "city": city,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit settlements_v2 era provenance status.")
    parser.add_argument(
        "--db", default="state/zeus-forecasts.db",
        help="Path to zeus-forecasts.db (default: state/zeus-forecasts.db)"
    )
    parser.add_argument("--output", default=None, help="Write JSON report to PATH (default: stdout)")
    parser.add_argument("--since", default=None, help="Only audit rows with settled_at >= DATE")
    parser.add_argument("--city", default=None, help="Filter to one city")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    report = run_audit(db_path, since=args.since, city=args.city)
    report_json = json.dumps(report, indent=2)

    if args.output:
        Path(args.output).write_text(report_json + "\n")
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(report_json)


if __name__ == "__main__":
    main()
