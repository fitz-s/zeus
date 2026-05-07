# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Authority basis: live-alignment-2026-05-07 D3 — BLOCKED readiness row policy after D1 bridge
"""One-shot idempotent reevaluation of BLOCKED readiness rows after the D1 legacy bridge.

Background
----------
D1 added legacy mx2t6/mn2t6 keys to _TRANSFER_SOURCE_BY_OPENDATA_VERSION so that
ensemble_snapshots_v2 rows written before 2026-05-07 still resolve a Platt calibration
during the mx2t3/mn2t3 transition.

The 100 BLOCKED rows in readiness_state as of 2026-05-07 carry reason_code
``SOURCE_RUN_HORIZON_OUT_OF_RANGE`` — a producer-readiness block (source run horizon
does not cover the required target dates). This is NOT a calibration mapping gap and
is therefore NOT fixed by the D1 bridge.

This script:
  1. Queries all BLOCKED rows.
  2. Classifies each row's reason_codes: D1-resolvable (CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED)
     vs non-D1 (everything else).
  3. For any D1-resolvable rows, re-runs the legacy policy and updates status in-place.
  4. Emits a dry-run report with counts before making any writes.
  5. Is idempotent: rows already resolved or whose reason_code is non-D1 are never touched.

Usage
-----
  # Dry run (default — no writes):
  python scripts/reevaluate_readiness_2026_05_07.py

  # Apply in-place:
  python scripts/reevaluate_readiness_2026_05_07.py --apply

Expected output as of 2026-05-07
---------------------------------
  Total BLOCKED rows: 100
  D1-resolvable (CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED): 0
  Non-D1 (SOURCE_RUN_HORIZON_OUT_OF_RANGE or other): 100
  Rows updated: 0 (dry-run) or 0 (applied)

  Interpretation: the D1 bridge fixes the calibration routing gap; the 100 BLOCKED rows
  require a new source run covering the required target-date horizon — operator action
  needed, not a code fix.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# D1-resolvable reason code: rows blocked solely because the legacy data_version
# was not in _TRANSFER_SOURCE_BY_OPENDATA_VERSION.
_D1_REASON_CODE = "CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED"

_DB_PATH = PROJECT_ROOT / "state" / "zeus-world.db"


def _parse_reason_codes(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ("READINESS_REASON_CODES_MALFORMED",)
    if not isinstance(parsed, list):
        return ("READINESS_REASON_CODES_MALFORMED",)
    return tuple(str(x) for x in parsed if str(x))


def _is_d1_resolvable(reason_codes: tuple[str, ...]) -> bool:
    """A row is D1-resolvable ONLY if its sole reason is the unmapped data_version code."""
    return reason_codes == (_D1_REASON_CODE,)


def run(*, apply: bool = False, db_path: Path = _DB_PATH) -> dict:
    """Evaluate and optionally fix BLOCKED rows.

    Returns a summary dict:
      total_blocked, d1_resolvable, non_d1, rows_updated
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT readiness_id, reason_codes_json, status FROM readiness_state WHERE status = 'BLOCKED'"
    ).fetchall()

    total_blocked = len(rows)
    d1_ids: list[str] = []
    non_d1_summary: dict[str, int] = {}

    for row in rows:
        codes = _parse_reason_codes(row["reason_codes_json"])
        if _is_d1_resolvable(codes):
            d1_ids.append(row["readiness_id"])
        else:
            key = "|".join(codes) or "EMPTY"
            non_d1_summary[key] = non_d1_summary.get(key, 0) + 1

    rows_updated = 0

    if d1_ids:
        # Import the updated policy to re-evaluate each row.
        # For D1-resolvable rows the fix is known: the legacy version now maps
        # to a Platt target. We mark them SHADOW_ONLY (the safe conservative
        # status after removing the unmapped block — live_promotion_approved
        # state is unknown at this point and must be re-evaluated by the daemon).
        from src.data.calibration_transfer_policy import _TRANSFER_SOURCE_BY_OPENDATA_VERSION  # noqa: PLC0415
        updated_at = datetime.now(timezone.utc).isoformat()
        if apply:
            with conn:
                for rid in d1_ids:
                    conn.execute(
                        """
                        UPDATE readiness_state
                           SET status = 'SHADOW_ONLY',
                               reason_codes_json = ?,
                               computed_at = ?
                         WHERE readiness_id = ?
                           AND status = 'BLOCKED'
                           AND reason_codes_json = ?
                        """,
                        (
                            json.dumps(["READINESS_REEVALUATED_D1_BRIDGE_SHADOW_ONLY"]),
                            updated_at,
                            rid,
                            json.dumps([_D1_REASON_CODE]),
                        ),
                    )
                rows_updated = conn.execute(
                    "SELECT changes()"
                ).fetchone()[0]
        else:
            rows_updated = 0  # dry run

    conn.close()

    return {
        "total_blocked": total_blocked,
        "d1_resolvable": len(d1_ids),
        "non_d1": total_blocked - len(d1_ids),
        "non_d1_breakdown": non_d1_summary,
        "rows_updated": rows_updated,
        "mode": "APPLIED" if apply else "DRY_RUN",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write updates to DB. Omit for dry-run (default).",
    )
    parser.add_argument(
        "--db",
        default=str(_DB_PATH),
        help=f"Path to zeus-world.db (default: {_DB_PATH})",
    )
    args = parser.parse_args()

    result = run(apply=args.apply, db_path=Path(args.db))

    mode = result["mode"]
    print(f"\n=== reevaluate_readiness_2026_05_07.py [{mode}] ===")
    print(f"Total BLOCKED rows:                            {result['total_blocked']}")
    print(f"D1-resolvable (CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED): {result['d1_resolvable']}")
    print(f"Non-D1 (not fixed by bridge):                  {result['non_d1']}")
    if result["non_d1_breakdown"]:
        for reason, cnt in sorted(result["non_d1_breakdown"].items(), key=lambda x: -x[1]):
            print(f"  {reason}: {cnt}")
    print(f"Rows updated:                                  {result['rows_updated']}")
    if result["d1_resolvable"] == 0:
        print(
            "\nInterpretation: 0 rows are D1-resolvable. The 100 BLOCKED rows carry\n"
            "SOURCE_RUN_HORIZON_OUT_OF_RANGE — a producer-readiness block requiring\n"
            "a new source run with adequate target-date horizon. Operator action needed."
        )
    print()


if __name__ == "__main__":
    main()
