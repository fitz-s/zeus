# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Schema migration — add canonical error-model columns to model_bias_ens (idempotent, guarded by PRAGMA table_info).
# Reuse: Safe to re-run; ALTERs are no-ops if columns already exist. Verify schema_version before reuse.
# Authority basis: Zeus #64 / #68 / #69 — canonical error-model schema.
# WRITER_LOCK_DEFER_REVIEW=2026-05-29
# Justification: DDL-only ALTER TABLE ADD COLUMN; --commit gated (dry-run default); targets staging/copy DB only.
# Daemon-DOWN assumption; additive-only schema changes; no INSERT/UPDATE/DELETE.
# Ops-doc entry: docs/archive/2026-Q2/task_2026-05-17_post_karachi_remediation/F22_WRITER_LOCK_FIX.md
#   Extends model_bias_ens to the full domain-identity field set per task spec
#   (FT_SHIP_MASTER_SPEC_2026-05-25 enumerated field list).  Each ALTER is guarded
#   by PRAGMA table_info so re-runs are no-ops.
"""Schema migration: add canonical error-model columns to ``model_bias_ens``.

Migration semantic policy: additive-only / idempotent.
  - Only ADD COLUMN operations; no DROP, no RENAME, no data backfill.
  - Each ALTER is guarded by PRAGMA table_info — re-runs are no-ops.
  - Columns added with no DEFAULT; legacy rows remain NULL.
  - Safe to run against shared production DBs (schema-only, no row writes).
  - Rollback: columns are nullable and ignored by readers that don't use them;
    no migration rollback script required for additive-only schema changes.

Columns added (all nullable, no DEFAULT — legacy rows remain NULL):
  error_model_family   TEXT   — e.g. 'full_transport_v1', 'none'
  error_model_key      TEXT   — composite natural-key string
  transport_delta_policy TEXT — serialised kappa/delta-source descriptor
  bias_c               REAL   — posterior mean bias (forecast - actual), degC
  bias_sd_c            REAL   — posterior bias SD, degC
  residual_sd_c        REAL   — forecast/station residual scale, degC
  heterogeneity_var_c2 REAL   — prior<->live excess variance, degC^2
  correction_strength  REAL   — lambda in [0,1] (SNR gate)
  effective_bias_c     REAL   — lambda * bias_c (what is subtracted pre-MC)
  total_residual_sd_c  REAL   — sqrt(residual_sd^2 + heterogeneity_var)
  code_commit          TEXT   — git HEAD SHA at fit time
  fit_signature_hash   TEXT   — sha256 of sorted inputs+params (16-char prefix)
  authority            TEXT   — 'STAGING' | 'VERIFIED' | 'LEGACY'

Idempotent: each column is only ALTERed if absent.  Re-running on a DB that
already has the columns is a no-op.

Usage (zeus repo root, zeus venv active)::

    # dry-run (default) — prints planned ALTERs, does NOT write
    python scripts/migrate_model_bias_ens_canonical_fields.py --db /path/to/copy.db

    # commit to a COPY of the staging DB (NEVER prod without explicit operator approval)
    python scripts/migrate_model_bias_ens_canonical_fields.py \\
        --db /path/to/copy.db --commit
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logger = logging.getLogger(__name__)

TABLE = "model_bias_ens"

# (column_name, sql_type) — order matters for readability; each is guarded separately.
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("error_model_family",   "TEXT"),
    ("error_model_key",      "TEXT"),
    ("transport_delta_policy", "TEXT"),
    ("bias_c",               "REAL"),
    ("bias_sd_c",            "REAL"),
    ("residual_sd_c",        "REAL"),
    ("heterogeneity_var_c2", "REAL"),
    ("correction_strength",  "REAL"),
    ("effective_bias_c",     "REAL"),
    ("total_residual_sd_c",  "REAL"),
    ("code_commit",          "TEXT"),
    ("fit_signature_hash",   "TEXT"),
    ("authority",            "TEXT"),
]


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def migrate(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
) -> dict:
    """Apply canonical-field ALTERs. Returns a status dict.

    Creates the base table if absent (idempotent), then adds each extension
    column individually.  When ``dry_run=True`` (default) no DB writes occur.
    """
    # Ensure base table exists before attempting ALTER TABLE on it.
    if not dry_run:
        from src.calibration.ens_bias_repo import init_ens_bias_schema  # noqa: PLC0415
        init_ens_bias_schema(conn)

    applied: list[str] = []
    skipped: list[str] = []
    planned: list[str] = []

    # dry_run: if table absent, report all columns as planned but skip PRAGMA check
    table_exists = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()
    )

    for col, sql_type in _NEW_COLUMNS:
        if table_exists and _has_column(conn, TABLE, col):
            skipped.append(col)
            continue
        alter_sql = f"ALTER TABLE {TABLE} ADD COLUMN {col} {sql_type}"
        if dry_run or not table_exists:
            planned.append(alter_sql)
            continue
        # Apply inside individual BEGIN/COMMIT so a failure on one column
        # does not roll back already-applied columns.
        conn.execute("BEGIN")
        try:
            conn.execute(alter_sql)
            conn.execute("COMMIT")
            applied.append(col)
        except sqlite3.OperationalError as exc:
            # Race-safe: another writer slipped in between PRAGMA and ALTER.
            if "duplicate column name" in str(exc).lower():
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                skipped.append(col)
            else:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    return {
        "dry_run": dry_run,
        "applied": applied,
        "skipped_already_present": skipped,
        "planned_if_commit": planned,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Add canonical error-model columns to model_bias_ens (idempotent)."
    )
    p.add_argument(
        "--db",
        required=True,
        type=Path,
        help="Path to the target SQLite DB (use a COPY, never prod without approval).",
    )
    p.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actually apply the ALTERs.  Without this flag the script is a dry-run.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)

    db_path = args.db.resolve()
    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        return 1

    dry_run = not args.commit
    if dry_run:
        logger.info("[DRY RUN] Would apply the following ALTERs (use --commit to apply):")
    else:
        logger.info("Applying canonical-field migration to: %s", db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        result = migrate(conn, dry_run=dry_run)
    finally:
        conn.close()

    if dry_run and result["planned_if_commit"]:
        for sql in result["planned_if_commit"]:
            logger.info("  [planned] %s", sql)
    if result["applied"]:
        logger.info("Applied %d new columns: %s", len(result["applied"]), result["applied"])
    if result["skipped_already_present"]:
        logger.info(
            "Skipped %d columns (already present): %s",
            len(result["skipped_already_present"]),
            result["skipped_already_present"],
        )
    if dry_run and not result["planned_if_commit"]:
        logger.info("All columns already present — no-op.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
