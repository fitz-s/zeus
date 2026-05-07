# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_f11_forecast_issue_time/plan.md (Slice F11.4)
"""F11.4 backfill: populate forecast_issue_time + availability_provenance for NULL rows.

Per F11.1 dissemination registry: each source has a deterministic
(base_time, lead_day) -> available_at function plus a provenance tier.
This script applies that derivation to the 23,466 pre-F11 rows whose
fields are NULL.

Base-time assumption: each row's forecast_basis_date is interpreted as
00:00 UTC on that date. This is the conservative choice (later
available_at than 06/12/18 UTC bases would yield) and matches Open-Meteo
Previous Runs' typical behavior (returns the longest-horizon run, which
for ECMWF/GFS is the 00 UTC cycle).

Tier distribution after backfill (expected per F11.1 registry):
- DERIVED_FROM_DISSEMINATION: ECMWF (4,998) + GFS (4,998) = 9,996 rows
- RECONSTRUCTED: ICON (4,284) + UKMO (4,188) + OpenMeteo (4,998) = 13,470 rows

Usage:
  --dry-run: read-only report of derivation outcomes by source
  --apply: actually UPDATE rows; must be combined with --confirm-backup
  --verify: post-state distribution; non-zero exit if any NULL remains
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date as date_type, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.dissemination_schedules import UnknownSourceError, derive_availability
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402


def _column_exists(conn: sqlite3.Connection, column: str) -> bool:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(forecasts)").fetchall()]
    return column in cols


def _rows_needing_backfill(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, source, forecast_basis_date, lead_days
        FROM forecasts
        WHERE forecast_issue_time IS NULL
           OR availability_provenance IS NULL
        ORDER BY id
        """
    ).fetchall()


def _derive_for_row(row: sqlite3.Row) -> tuple[str, str]:
    """Returns (issue_time_iso, provenance_value).

    Raises ValueError if forecast_basis_date is NULL or malformed (the
    backfill schema guarantee was lost when the column was nullable; bad
    rows are caught here and skipped at the caller level).
    """
    raw_basis = row["forecast_basis_date"]
    if raw_basis is None or str(raw_basis).strip() == "":
        raise ValueError(f"NULL forecast_basis_date on row id={row['id']}")
    try:
        basis = date_type.fromisoformat(str(raw_basis))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"malformed forecast_basis_date on row id={row['id']}: {raw_basis!r}"
        ) from exc
    base_time = datetime.combine(basis, datetime.min.time(), tzinfo=timezone.utc)
    lead_day = int(row["lead_days"] or 0)
    issue_time, provenance = derive_availability(str(row["source"]), base_time, lead_day)
    return issue_time.isoformat(), provenance.value


def _summarize(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> dict[tuple[str, str], int]:
    """Return {(source, derived_provenance): count}."""
    summary: dict[tuple[str, str], int] = {}
    unknown_sources: set[str] = set()
    malformed = 0
    for row in rows:
        try:
            _, prov = _derive_for_row(row)
        except UnknownSourceError:
            unknown_sources.add(str(row["source"]))
            continue
        except ValueError as exc:
            malformed += 1
            print(f"[summary] skipping malformed row: {exc}", file=sys.stderr)
            continue
        key = (str(row["source"]), prov)
        summary[key] = summary.get(key, 0) + 1
    if unknown_sources:
        print(f"[summary] WARNING: unregistered sources skipped: {sorted(unknown_sources)}", file=sys.stderr)
    if malformed:
        print(f"[summary] WARNING: {malformed} rows skipped due to NULL/malformed forecast_basis_date", file=sys.stderr)
    return summary


def dry_run(db_path: Path) -> None:
    print(f"[dry-run] Target DB: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if not _column_exists(conn, "availability_provenance"):
        print("[dry-run] FAIL: availability_provenance column missing. Run migrate_forecasts_availability_provenance.py --apply first.")
        sys.exit(1)
    rows = _rows_needing_backfill(conn)
    print(f"[dry-run] Rows requiring backfill: {len(rows):,}")
    summary = _summarize(conn, rows)
    print("[dry-run] Per-source derived tier distribution:")
    for (source, prov), count in sorted(summary.items()):
        print(f"  {source:<28} {prov:<24} {count:>6,}")
    total_derived = sum(c for (_, prov), c in summary.items() if prov == "derived_dissemination")
    total_reconstructed = sum(c for (_, prov), c in summary.items() if prov == "reconstructed")
    print(f"[dry-run] Totals: DERIVED={total_derived:,}  RECONSTRUCTED={total_reconstructed:,}  unmapped={len(rows) - total_derived - total_reconstructed:,}")
    print("[dry-run] No mutation performed.")
    conn.close()


def apply(db_path: Path, *, confirm_backup: bool) -> None:
    if not confirm_backup:
        print("[apply] FAIL: --apply requires --confirm-backup affirming a verified DB backup exists.", file=sys.stderr)
        sys.exit(2)
    print(f"[apply] Target DB: {db_path}")
    with db_writer_lock(db_path, WriteClass.BULK):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            if not _column_exists(conn, "availability_provenance"):
                print("[apply] FAIL: schema migration not run. Apply F11.2 first.", file=sys.stderr)
                sys.exit(1)
            rows = _rows_needing_backfill(conn)
            print(f"[apply] Rows requiring backfill: {len(rows):,}")
            if not rows:
                print("[apply] No rows need backfill; nothing to do.")
                return
            updates: list[tuple[str, str, int]] = []
            unknown_count = 0
            malformed_count = 0
            for row in rows:
                try:
                    issue_time, prov = _derive_for_row(row)
                except UnknownSourceError:
                    unknown_count += 1
                    continue
                except ValueError as exc:
                    malformed_count += 1
                    print(f"[apply] skipping malformed row: {exc}", file=sys.stderr)
                    continue
                updates.append((issue_time, prov, int(row["id"])))
            if unknown_count:
                print(f"[apply] WARNING: {unknown_count} rows have unregistered sources and will remain NULL.", file=sys.stderr)
            if malformed_count:
                print(f"[apply] WARNING: {malformed_count} rows skipped (NULL/malformed forecast_basis_date).", file=sys.stderr)
            print(f"[apply] Writing {len(updates):,} updates in a single transaction...")
            conn.executemany(
                "UPDATE forecasts SET forecast_issue_time = ?, availability_provenance = ? WHERE id = ?",
                updates,
            )
            conn.commit()
            post_null = conn.execute(
                "SELECT COUNT(*) FROM forecasts WHERE forecast_issue_time IS NULL OR availability_provenance IS NULL"
            ).fetchone()[0]
            print(f"[apply] Remaining NULL rows: {post_null:,}")
            expected_remaining = unknown_count + malformed_count
            if post_null != expected_remaining:
                print(f"[apply] WARNING: NULL count ({post_null}) != expected unmapped+malformed ({expected_remaining}).", file=sys.stderr)
        finally:
            conn.close()


def verify(db_path: Path) -> None:
    print(f"[verify] Target DB: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if not _column_exists(conn, "availability_provenance"):
        print("[verify] FAIL: availability_provenance column missing.", file=sys.stderr)
        sys.exit(1)
    distribution = dict(
        conn.execute(
            "SELECT availability_provenance, COUNT(*) FROM forecasts GROUP BY availability_provenance"
        ).fetchall()
    )
    print(f"[verify] Provenance distribution: {distribution}")
    null_count = conn.execute(
        "SELECT COUNT(*) FROM forecasts WHERE forecast_issue_time IS NULL OR availability_provenance IS NULL"
    ).fetchone()[0]
    print(f"[verify] NULL rows remaining: {null_count:,}")
    per_source = conn.execute(
        "SELECT source, availability_provenance, COUNT(*) "
        "FROM forecasts GROUP BY source, availability_provenance ORDER BY source"
    ).fetchall()
    print("[verify] Per-source × provenance distribution:")
    for row in per_source:
        prov = row[1] or "NULL"
        print(f"  {row[0]:<28} {prov:<24} {row[2]:>6,}")
    if null_count == 0:
        print("[verify] OK — all rows backfilled.")
    else:
        print(f"[verify] WARN — {null_count:,} rows still NULL (likely unregistered sources).")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(PROJECT_ROOT.parent.parent / "state" / "zeus-world.db"),
        help="Path to zeus-world.db",
    )
    parser.add_argument(
        "--confirm-backup",
        action="store_true",
        help="Required with --apply; affirms operator has verified a DB backup",
    )
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
        apply(db_path, confirm_backup=args.confirm_backup)
    elif args.verify:
        verify(db_path)


if __name__ == "__main__":
    main()
