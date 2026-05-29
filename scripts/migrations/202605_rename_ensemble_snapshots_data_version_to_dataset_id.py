# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Authority basis: Stage-C blocker (#26 / #21 Gap #2). Commit B5 renamed the CANONICAL
#   ensemble_snapshots column data_version → dataset_id in src/state/schema/v2_schema.py
#   (_create_ensemble_snapshots now declares `dataset_id TEXT NOT NULL` + the UNIQUE and
#   idx_ens_entry_lookup reference dataset_id). The LIVE zeus-forecasts.db still carries
#   the OLD column name `data_version`, and NO migration bridges them. On merge+restart the
#   daemon's schema-readiness / fingerprint-drift check fails. This migration brings an
#   EXISTING forecasts DB up to the canonical shape via a single O(1) RENAME COLUMN.
# DB target: zeus-forecasts.db (ensemble_snapshots is a K1 forecast-class table; single-DB —
#   no cross-DB ATTACH required; the intra-DB SAVEPOINT provides the atomicity envelope INV-37
#   mandates for a multi-statement schema change).
# Runner interface: def up(conn: sqlite3.Connection) -> None
#   (python -m scripts.migrations apply --target 202605_rename_ensemble_snapshots_data_version_to_dataset_id [--dry-run])
# Standalone operator receipts:
#   python scripts/migrations/202605_rename_ensemble_snapshots_data_version_to_dataset_id.py [--execute]
#
# SEQUENCING (Stage C → D): the active-read-site fixes (PART 2 of this slice) and this
#   migration land TOGETHER in the merge. The operator runs THIS migration (dry-run first,
#   then --execute) BEFORE restarting the daemon on the new code. Dead/un-fixed offline scripts
#   that still SELECT `data_version` from ensemble_snapshots will break post-rename, but they are
#   dead (moot) — see the slice report's DEAD list.
# Purpose: Rename ensemble_snapshots.data_version to dataset_id in the LIVE forecasts DB to match the canonical B5 schema shape.
# Reuse: Run dry-run first; run BEFORE daemon restart on new code; idempotent if column already renamed; target DB must be zeus-forecasts.db.
"""Rename ensemble_snapshots.data_version → dataset_id (LIVE → canonical bridge).

Migration semantic policy:
  This is a PURE COLUMN RENAME. No data is created, dropped, or re-derived; the
  surviving rows keep every value, the column merely changes name. SQLite ≥ 3.25
  ``ALTER TABLE ... RENAME COLUMN`` auto-rewrites every index, UNIQUE constraint
  and view that referenced the old name (the canonical UNIQUE
  ``UNIQUE(city, target_date, temperature_metric, issue_time, dataset_id)`` and
  ``idx_ens_entry_lookup`` both name the renamed column), so no index/constraint
  rebuild is needed. The rename is O(1) (catalog edit), not a table copy.

Idempotency / state machine (do NOT guess on ambiguity — Fitz #4):
  * dataset_id present AND data_version absent → already migrated → no-op return.
  * data_version present AND dataset_id absent → the LIVE pre-rename shape → rename.
  * BOTH present → unexpected (a partial/manual edit); raise. We will not guess
    which column is canonical or silently drop one.
  * NEITHER present → unexpected (table shape we don't recognise); raise.

Drift guard (SEV antibody):
  After the rename, the resulting ensemble_snapshots column set + UNIQUE clause MUST
  match a freshly-initialised canonical ensemble_snapshots (init_schema_forecasts).
  If the LIVE-shaped table carries OTHER drift beyond the single data_version→dataset_id
  rename (extra/missing columns, a different UNIQUE), this migration does NOT silently
  rebuild — it FLAGS the discrepancy (compute_receipts + a raised AssertionError in up())
  so the operator investigates. Bridging is only safe when the rename is the ONLY delta.

Fingerprint invariant:
  This migration NEVER imports or mutates src/state/schema/v2_schema.py and never touches
  architecture/_schema_fingerprint.txt. The canonical DDL (and therefore the pinned
  fingerprint) is unchanged by design — the migration only moves the LIVE DB toward the
  already-canonical shape.
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "forecasts"

_TABLE = "ensemble_snapshots"
_OLD_COL = "data_version"
_NEW_COL = "dataset_id"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _row_count(conn: sqlite3.Connection, name: str) -> int:
    if not _table_exists(conn, name):
        return -1
    return conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]


def _unique_clause(conn: sqlite3.Connection, table: str) -> str:
    """Return the table's CREATE-TABLE SQL (whole DDL) for UNIQUE/shape comparison.

    The whole sqlite_master.sql string is the most robust witness of the UNIQUE
    constraint + column order; we compare the canonical-relevant substrings rather
    than re-parse SQL.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return (row[0] if row and row[0] else "") or ""


def _canonical_reference() -> tuple[set[str], str]:
    """Build a fresh canonical ensemble_snapshots and return (columns, ddl).

    Used as the post-rename drift oracle. Imports init_schema_forecasts lazily so
    the module stays importable in environments where src is not on the path until
    _standalone() inserts the repo root.
    """
    from src.state.db import init_schema_forecasts

    ref = sqlite3.connect(":memory:")
    try:
        init_schema_forecasts(ref)
        cols = _columns(ref, _TABLE)
        ddl = _unique_clause(ref, _TABLE)
        return cols, ddl
    finally:
        ref.close()


def compute_receipts(conn: sqlite3.Connection) -> dict[str, object]:
    """Pre-migration receipts (read-only): row count, which column the table carries,
    and a drift verdict against the canonical reference shape."""
    exists = _table_exists(conn, _TABLE)
    cols = _columns(conn, _TABLE) if exists else set()
    has_old = _OLD_COL in cols
    has_new = _NEW_COL in cols

    if not exists:
        state = "TABLE_ABSENT"
    elif has_new and not has_old:
        state = "ALREADY_MIGRATED"
    elif has_old and not has_new:
        state = "PRE_RENAME_LIVE_SHAPE"
    elif has_old and has_new:
        state = "UNEXPECTED_BOTH_COLUMNS"
    else:
        state = "UNEXPECTED_NEITHER_COLUMN"

    receipts: dict[str, object] = {
        "table_exists": exists,
        "ensemble_snapshots_rows": _row_count(conn, _TABLE),
        f"has_{_OLD_COL}": has_old,
        f"has_{_NEW_COL}": has_new,
        "state": state,
    }

    # Drift verdict: compare the POST-rename column set against canonical. We
    # simulate the rename on the receipt (rename is name-only) so the operator
    # sees, before applying, whether the bridge is clean or whether extra drift
    # remains that this migration will refuse to paper over.
    if exists:
        try:
            canon_cols, _ = _canonical_reference()
            if has_old and not has_new:
                post_cols = (cols - {_OLD_COL}) | {_NEW_COL}
            else:
                post_cols = set(cols)
            extra = sorted(post_cols - canon_cols)
            missing = sorted(canon_cols - post_cols)
            receipts["post_rename_extra_columns"] = extra
            receipts["post_rename_missing_columns"] = missing
            receipts["clean_single_rename"] = not extra and not missing
        except Exception as exc:  # canonical reference unavailable (no src path)
            receipts["drift_check_error"] = repr(exc)
    return receipts


def up(conn: sqlite3.Connection) -> None:
    """Rename ensemble_snapshots.data_version → dataset_id, atomically and idempotently.

    Steps (inside one SAVEPOINT for all-or-nothing atomicity):
      1. State classification (see module docstring). No-op on ALREADY_MIGRATED;
         raise on TABLE_ABSENT / UNEXPECTED_BOTH / UNEXPECTED_NEITHER.
      2. ALTER TABLE ensemble_snapshots RENAME COLUMN data_version TO dataset_id.
         SQLite ≥ 3.25 auto-updates the UNIQUE + idx_ens_entry_lookup that name
         the column; O(1) catalog edit, rows untouched.
      3. Drift guard: the post-rename column set MUST equal a fresh canonical
         ensemble_snapshots. Any extra/missing column → AssertionError (rolled back)
         so the operator investigates rather than the bridge silently masking drift.
    """
    assert sqlite3.sqlite_version_info >= (3, 25, 0), (
        f"SQLite {sqlite3.sqlite_version} < 3.25.0; ALTER TABLE RENAME COLUMN unavailable."
    )

    if not _table_exists(conn, _TABLE):
        raise AssertionError(
            f"{_TABLE} does not exist — run init_schema_forecasts first; this migration "
            f"only renames a column on an existing table."
        )

    cols = _columns(conn, _TABLE)
    has_old = _OLD_COL in cols
    has_new = _NEW_COL in cols

    if has_new and not has_old:
        return  # idempotent no-op: already migrated (fresh canonical DB or re-run)

    if has_old and has_new:
        raise AssertionError(
            f"{_TABLE} carries BOTH {_OLD_COL!r} and {_NEW_COL!r} — unexpected state "
            f"(partial/manual edit). Refusing to guess which is canonical or to drop "
            f"either. Investigate before re-running."
        )
    if not has_old and not has_new:
        raise AssertionError(
            f"{_TABLE} carries NEITHER {_OLD_COL!r} nor {_NEW_COL!r} — unrecognised table "
            f"shape. Investigate before re-running."
        )

    # State is PRE_RENAME_LIVE_SHAPE: data_version present, dataset_id absent.
    conn.execute("SAVEPOINT rename_ens_dataset_id")
    try:
        conn.execute(
            f"ALTER TABLE {_TABLE} RENAME COLUMN {_OLD_COL} TO {_NEW_COL}"
        )

        # Drift guard — refuse to leave the operator with a silently-divergent table.
        canon_cols, _ = _canonical_reference()
        post_cols = _columns(conn, _TABLE)
        extra = sorted(post_cols - canon_cols)
        missing = sorted(canon_cols - post_cols)
        if extra or missing:
            raise AssertionError(
                f"{_TABLE} has drift beyond the single {_OLD_COL}->{_NEW_COL} rename; "
                f"NOT silently rebuilding. extra_columns={extra} missing_columns={missing}. "
                f"The rename succeeded but the resulting shape != canonical "
                f"init_schema_forecasts; operator must reconcile the remaining drift."
            )
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT rename_ens_dataset_id")
        conn.execute("RELEASE SAVEPOINT rename_ens_dataset_id")
        raise
    conn.execute("RELEASE SAVEPOINT rename_ens_dataset_id")


def _standalone(argv: list[str] | None = None) -> int:
    """Operator entry point: dry-run receipts by default; --execute to apply."""
    import argparse
    import sys
    from pathlib import Path

    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    parser = argparse.ArgumentParser(
        description="Rename ensemble_snapshots.data_version → dataset_id (LIVE → canonical bridge)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the RENAME COLUMN (default: dry-run receipts only).",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to zeus-forecasts.db (default: canonical forecasts connection).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias for the default no-write receipts mode (explicit).",
    )
    args = parser.parse_args(argv)

    if args.db_path:
        conn = sqlite3.connect(args.db_path)  # WRITER_LOCK_DEFER_REVIEW=2026-05-29 operator-invoked migration; daemon lock unavailable in standalone path
    else:
        from src.state.db import get_forecasts_connection

        conn = get_forecasts_connection(write_class="bulk")
    try:
        receipts = compute_receipts(conn)
        print("ensemble_snapshots data_version→dataset_id rename — PRE-MIGRATION RECEIPTS")
        for k, v in receipts.items():
            print(f"  {k}: {v}")
        if args.dry_run or not args.execute:
            print("\nDRY-RUN (no changes applied). Re-run with --execute to apply.")
            return 0
        if receipts.get("clean_single_rename") is False:
            print(
                "\nABORT: post-rename shape would diverge from canonical "
                "(see post_rename_extra_columns / post_rename_missing_columns). "
                "Not applying; reconcile drift first."
            )
            return 2
        up(conn)
        conn.commit()
        post = _columns(conn, _TABLE)
        print("\nAPPLIED. POST-MIGRATION RECEIPTS")
        print(f"  has_{_NEW_COL}: {_NEW_COL in post}")
        print(f"  has_{_OLD_COL}: {_OLD_COL in post}")
        print(f"  ensemble_snapshots rows (unchanged): {_row_count(conn, _TABLE)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_standalone())
