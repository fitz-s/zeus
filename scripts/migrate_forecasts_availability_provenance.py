# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_f11_forecast_issue_time/plan.md (Slice F11.2)
"""F11.2 schema migration: add forecasts.availability_provenance.

Adds a typed availability_provenance TEXT column to the forecasts table
with a CHECK constraint enforcing the 4-tier AvailabilityProvenance
enum from src.backtest.decision_time_truth.

Reversible: column defaults NULL; old readers continue to work.

Usage:
  .venv/bin/python scripts/migrate_forecasts_availability_provenance.py --dry-run
  .venv/bin/python scripts/migrate_forecasts_availability_provenance.py --apply
  .venv/bin/python scripts/migrate_forecasts_availability_provenance.py --verify
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.decision_time_truth import AvailabilityProvenance


PROVENANCE_VALUES = sorted(p.value for p in AvailabilityProvenance)
CHECK_CLAUSE = (
    f"CHECK (availability_provenance IS NULL "
    f"OR availability_provenance IN ({', '.join(repr(v) for v in PROVENANCE_VALUES)}))"
)


def _column_exists(conn: sqlite3.Connection) -> bool:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(forecasts)").fetchall()]
    return "availability_provenance" in cols


def _row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]


def _provenance_distribution(conn: sqlite3.Connection) -> dict[str | None, int]:
    if not _column_exists(conn):
        return {}
    return dict(
        conn.execute(
            "SELECT availability_provenance, COUNT(*) "
            "FROM forecasts GROUP BY availability_provenance"
        ).fetchall()
    )


def dry_run(db_path: Path) -> None:
    print(f"[dry-run] Target DB: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    print(f"[dry-run] forecasts row count: {_row_count(conn):,}")
    print(f"[dry-run] availability_provenance column exists: {_column_exists(conn)}")
    print(f"[dry-run] Would execute:")
    print(f"[dry-run]   ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT {CHECK_CLAUSE};")
    print(f"[dry-run] Permitted enum values: {PROVENANCE_VALUES}")
    print(f"[dry-run] No mutation performed.")
    conn.close()


def apply(db_path: Path) -> None:
    print(f"[apply] Target DB: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        if _column_exists(conn):
            print(f"[apply] availability_provenance already exists; nothing to do.")
            return
        before_count = _row_count(conn)
        print(f"[apply] forecasts row count before: {before_count:,}")
        conn.execute(
            f"ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT {CHECK_CLAUSE}"
        )
        conn.commit()
        after_count = _row_count(conn)
        print(f"[apply] forecasts row count after: {after_count:,}")
        if before_count != after_count:
            raise RuntimeError(
                f"Row count changed during ALTER: {before_count} -> {after_count}"
            )
        print(f"[apply] Column added; all {after_count:,} rows have NULL provenance (expected; backfill via F11.4).")
    finally:
        conn.close()


def verify(db_path: Path) -> None:
    print(f"[verify] Target DB: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    if not _column_exists(conn):
        print(f"[verify] FAIL: availability_provenance column NOT present.")
        sys.exit(1)
    print(f"[verify] availability_provenance column present.")
    distribution = _provenance_distribution(conn)
    print(f"[verify] Distribution: {distribution}")
    print(f"[verify] CHECK constraint enforces enum: {PROVENANCE_VALUES}")
    print(f"[verify] OK")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(PROJECT_ROOT.parent.parent / "state" / "zeus-world.db"),
                        help="Path to zeus-world.db (default: parent state)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        dry_run(db_path)
    elif args.apply:
        apply(db_path)
    elif args.verify:
        verify(db_path)


if __name__ == "__main__":
    main()
