# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Set settlement_outcomes.settlement_unit from cities_by_name[city].settlement_unit
#   for NULL-unit rows (283 May + June rows as of 2026-06-03). One-time repair; run BEFORE
#   202606_install_settlement_unit_verified_triggers.py migration.
# Reuse: verify zeus-forecasts.db settlement_outcomes still has NULL-unit rows
#   (SELECT count(*) WHERE settlement_unit IS NULL AND authority='VERIFIED') before
#   re-running; check src.config.cities_by_name for city coverage. DRY-RUN by default.
# Authority basis: W2 settlement-store convergence (HANDOFF_2026-06-02_emos_ci.md);
#   src.config.cities_by_name[city].settlement_unit is the per-city authoritative
#   settlement unit (US cities settle °F, others °C). The settlement VALUES in
#   settlement_outcomes are already provenance-clean and in each city's authoritative
#   unit (verified 2026-06-03: Atlanta 84=°F, Amsterdam 22=°C); only the
#   settlement_unit COLUMN was left NULL by a stale ingest daemon running pre-#132
#   writer bytecode. This backfill writes the typed unit from the city config,
#   NEVER inferring it from the value.
#
# Purpose: Set settlement_outcomes.settlement_unit from cities_by_name[city].settlement_unit
#   for the rows where it is NULL (283 May + June rows as of 2026-06-03), so settled truth
#   becomes usable for unit-correct calculations (cold-bias refit, bin scoring, pairing).
#
# Method:
#   1. For each NULL-unit row, resolve the city's authoritative unit from cities_by_name.
#      Rows whose city has NO known authoritative unit are SKIPPED (left NULL, reported) —
#      we never guess.
#   2. UPDATE ... SET settlement_unit=? WHERE settlement_id=? AND settlement_unit IS NULL
#      (idempotent: re-running touches nothing because the WHERE no longer matches).
#   3. Atomic SAVEPOINT; dry-run by DEFAULT (prints receipts + rolls back); --commit applies.
#
# ORDERING: run this BEFORE scripts/migrations/202606_install_settlement_unit_verified_triggers.py.
#   The BEFORE UPDATE trigger only aborts on NEW.settlement_unit IS NULL, so this backfill
#   (which sets a non-NULL unit) is never blocked even if the trigger is already installed;
#   running it first simply guarantees a clean pre-trigger state.
#
# Run:
#   dry-run (default):  python scripts/backfill_settlement_unit_2026_06_03.py
#   execute:            python scripts/backfill_settlement_unit_2026_06_03.py --commit
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_DB = Path(__file__).resolve().parent.parent / "state" / "zeus-forecasts.db"


def _resolve_units() -> dict[str, str]:
    """city -> authoritative settlement unit ('F'|'C') from src.config.cities_by_name."""
    _REPO_ROOT = Path(__file__).resolve().parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from src.config import cities_by_name

    out: dict[str, str] = {}
    for name, cfg in cities_by_name.items():
        unit = getattr(cfg, "settlement_unit", None)
        if unit in ("F", "C"):
            out[name] = unit
    return out


def _null_unit_rows(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    return [
        (int(r[0]), str(r[1]))
        for r in conn.execute(
            "SELECT settlement_id, city FROM settlement_outcomes WHERE settlement_unit IS NULL"
        ).fetchall()
    ]


def backfill(conn: sqlite3.Connection, *, commit: bool) -> dict:
    units = _resolve_units()
    rows = _null_unit_rows(conn)

    planned: list[tuple[int, str]] = []          # (settlement_id, unit)
    skipped_unknown: Counter[str] = Counter()    # city -> count (no authoritative unit)
    unit_dist: Counter[str] = Counter()

    for settlement_id, city in rows:
        unit = units.get(city)
        if unit is None:
            skipped_unknown[city] += 1
            continue
        planned.append((settlement_id, unit))
        unit_dist[unit] += 1

    receipts: dict = {
        "db_path": str(_DB),
        "null_unit_rows_before": len(rows),
        "planned_updates": len(planned),
        "unit_distribution": dict(unit_dist),
        "skipped_unknown_city_rows": sum(skipped_unknown.values()),
        "skipped_unknown_cities": dict(skipped_unknown),
        "committed": False,
    }

    if not planned:
        return receipts

    conn.execute("SAVEPOINT backfill_settlement_unit")
    try:
        for settlement_id, unit in planned:
            conn.execute(
                "UPDATE settlement_outcomes SET settlement_unit=? "
                "WHERE settlement_id=? AND settlement_unit IS NULL",
                (unit, settlement_id),
            )
        null_after = conn.execute(
            "SELECT COUNT(*) FROM settlement_outcomes WHERE settlement_unit IS NULL"
        ).fetchone()[0]
        receipts["null_unit_rows_after_in_txn"] = int(null_after)
        if commit:
            conn.execute("RELEASE SAVEPOINT backfill_settlement_unit")
            conn.commit()
            receipts["committed"] = True
        else:
            conn.execute("ROLLBACK TO SAVEPOINT backfill_settlement_unit")
            conn.execute("RELEASE SAVEPOINT backfill_settlement_unit")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT backfill_settlement_unit")
        conn.execute("RELEASE SAVEPOINT backfill_settlement_unit")
        raise

    return receipts


def _standalone(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill settlement_outcomes.settlement_unit from cities_by_name (W2)."
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply the UPDATEs (default: dry-run receipts only, rolled back).",
    )
    parser.add_argument(
        "--db-path",
        default=str(_DB),
        help=f"Path to zeus-forecasts.db (default: {_DB}).",
    )
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db_path)  # WRITER_LOCK_DEFER_REVIEW=2026-06-03 operator-invoked backfill; daemon lock unavailable in standalone path
    try:
        receipts = backfill(conn, commit=args.commit)
    finally:
        conn.close()

    print("settlement_unit backfill — RECEIPTS")
    for k, v in receipts.items():
        print(f"  {k}: {v}")
    if not args.commit:
        print("\nDRY-RUN (no changes applied). Re-run with --commit to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_standalone())
