# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Lifecycle: created=2026-05-26; last_reviewed=2026-05-26; last_reused=never
# Purpose: Promote model_bias_ens_v2 STAGING rows to VERIFIED; inspect/verify subcommands read-only.
# Reuse: Dry-run safe (default). Pass --commit to write. Requires fit_full_transport_error_models.py STAGING rows to be present. Verify canonical columns before use.
# Authority basis: Zeus #64 FT-ship F3 — promote STAGING→VERIFIED rows in
#   model_bias_ens_v2 (world.db). INV-37 compliant: single-DB write via
#   get_world_connection(write_class="bulk") + SAVEPOINT. No ATTACH needed
#   (model_bias_ens_v2 is world-class only; no cross-DB writes in this path).
#   Authority: docs/operations/FT_SHIP_EXECUTION_LEDGER_2026-05-25.md F3.
"""Promote model_bias_ens_v2 STAGING rows to VERIFIED in production zeus-world.db.

STAGING rows are written by fit_full_transport_error_models.py with
``authority='STAGING'``.  This script promotes them to ``authority='VERIFIED'``
after the operator confirms shadow-validation results are acceptable.

Subcommands
-----------

* ``inspect``  — read-only summary of STAGING vs VERIFIED row counts per
  (error_model_family, metric).  Exits 0 with counts; never writes.
* ``promote``  — dry-run by default.  With ``--commit``: opens world.db with
  SAVEPOINT, flips authority STAGING→VERIFIED for the selected rows, and
  commits.  Rolls back on any error.
* ``verify``   — read-only post-promote check.  Confirms all promoted rows have
  well-formed identity columns (city, season, metric, live_data_version,
  error_model_family, authority='VERIFIED') and that critical predictive
  fields (bias_c, residual_sd_c, heterogeneity_var_c2) are non-NULL.

Options
-------

``--metric {high,low,both}``   filter by temperature metric (default: both).
``--family TEXT``              filter by error_model_family (default: full_transport_v1).
``--commit``                   apply writes (promote subcommand only).
``--db PATH``                  override world.db path (default: state/zeus-world.db).
``--verbose / -v``             more output.

INV-37 compliance
-----------------
model_bias_ens_v2 is world-class (zeus-world.db).  All mutations in this
script use a single connection to world.db with SAVEPOINT — no ATTACH,
no independent second connection to another DB.  This is the sanctioned
single-DB write path.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

from src.state.db import get_world_connection, ZEUS_WORLD_DB_PATH  # noqa: E402

logger = logging.getLogger(__name__)

TABLE = "model_bias_ens_v2"
_DEFAULT_FAMILY = "full_transport_v1"
_CRITICAL_FIELDS = ("bias_c", "residual_sd_c", "heterogeneity_var_c2", "authority", "error_model_family")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_canonical_columns(conn: sqlite3.Connection) -> bool:
    """Return True if canonical extension columns are present."""
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})").fetchall()}
    return "error_model_family" in existing and "authority" in existing


def _metric_filter(metric: str) -> tuple[str, list]:
    """Return (WHERE fragment, params) for --metric filter."""
    if metric == "both":
        return "", []
    return "AND metric = ?", [metric]


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_inspect(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    """Read-only summary of STAGING vs VERIFIED counts."""
    if not _check_canonical_columns(conn):
        print("ERROR: canonical extension columns absent — run F2 migration first.", file=sys.stderr)
        return 1

    metric_sql, metric_params = _metric_filter(args.metric)
    family_param = args.family

    rows = conn.execute(
        f"""
        SELECT error_model_family, metric, authority, COUNT(*) AS n
        FROM {TABLE}
        WHERE error_model_family = ?
        {metric_sql}
        GROUP BY error_model_family, metric, authority
        ORDER BY error_model_family, metric, authority
        """,
        [family_param] + metric_params,
    ).fetchall()

    if not rows:
        print(f"No rows found for family={family_param!r} metric={args.metric!r}")
        return 0

    print(f"{'family':<25} {'metric':<8} {'authority':<12} {'n':>6}")
    print("-" * 55)
    for r in rows:
        print(f"{r[0]:<25} {r[1]:<8} {r[2]:<12} {r[3]:>6}")

    staging_count = sum(r[3] for r in rows if r[2] == "STAGING")
    verified_count = sum(r[3] for r in rows if r[2] == "VERIFIED")
    print(f"\nSTAGING={staging_count}  VERIFIED={verified_count}")
    return 0


def cmd_promote(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    """Promote STAGING→VERIFIED. Dry-run unless --commit."""
    if not _check_canonical_columns(conn):
        print("ERROR: canonical extension columns absent — run F2 migration first.", file=sys.stderr)
        return 1

    metric_sql, metric_params = _metric_filter(args.metric)
    family_param = args.family

    candidates = conn.execute(
        f"""
        SELECT rowid, city, season, month, metric, live_data_version, error_model_family
        FROM {TABLE}
        WHERE error_model_family = ?
          AND authority = 'STAGING'
        {metric_sql}
        ORDER BY city, metric, season
        """,
        [family_param] + metric_params,
    ).fetchall()

    if not candidates:
        print(f"No STAGING rows for family={family_param!r} metric={args.metric!r}. Nothing to promote.")
        return 0

    print(f"{'DRY-RUN' if not args.commit else 'COMMIT'}: {len(candidates)} STAGING rows → VERIFIED")
    if args.verbose:
        for r in candidates:
            print(f"  rowid={r[0]} city={r[1]} season={r[2]} month={r[3]} "
                  f"metric={r[4]} ldv={r[5]} family={r[6]}")

    if not args.commit:
        print("(dry-run) No changes written. Pass --commit to apply.")
        return 0

    # INV-37 compliant: single-DB SAVEPOINT on world.db connection.
    rowids = [r[0] for r in candidates]
    placeholders = ",".join("?" * len(rowids))
    try:
        conn.execute("SAVEPOINT promote_model_bias_ens_v2")
        conn.execute(
            f"UPDATE {TABLE} SET authority = 'VERIFIED' WHERE rowid IN ({placeholders})",
            rowids,
        )
        # Integrity check before releasing savepoint.
        ic = conn.execute("PRAGMA integrity_check").fetchone()
        if ic[0] != "ok":
            raise RuntimeError(f"PRAGMA integrity_check failed: {ic[0]}")
        conn.execute("RELEASE SAVEPOINT promote_model_bias_ens_v2")
        print(f"Promoted {len(rowids)} rows to VERIFIED.")
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT promote_model_bias_ens_v2")
        conn.execute("RELEASE SAVEPOINT promote_model_bias_ens_v2")
        print(f"ERROR during promote — rolled back: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_verify(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    """Post-promote consistency check."""
    if not _check_canonical_columns(conn):
        print("ERROR: canonical extension columns absent.", file=sys.stderr)
        return 1

    metric_sql, metric_params = _metric_filter(args.metric)
    family_param = args.family

    rows = conn.execute(
        f"""
        SELECT rowid, city, season, month, metric, live_data_version,
               error_model_family, authority,
               bias_c, residual_sd_c, heterogeneity_var_c2
        FROM {TABLE}
        WHERE error_model_family = ?
          AND authority = 'VERIFIED'
        {metric_sql}
        ORDER BY city, metric, season
        """,
        [family_param] + metric_params,
    ).fetchall()

    if not rows:
        print(f"No VERIFIED rows for family={family_param!r} metric={args.metric!r}.")
        return 1

    errors = 0
    for r in rows:
        rowid, city, season, month, metric, ldv, family, authority, bias_c, res_sd, het_var = r
        issues = []
        if authority != "VERIFIED":
            issues.append(f"authority={authority!r}")
        if not city or not season or not metric or not ldv or not family:
            issues.append("NULL identity column")
        if bias_c is None or res_sd is None or het_var is None:
            issues.append(f"NULL predictive field (bias_c={bias_c} res_sd={res_sd} het_var={het_var})")
        if issues:
            print(f"  FAIL rowid={rowid} city={city} {metric} {season}: {'; '.join(issues)}")
            errors += 1
        elif args.verbose:
            print(f"  OK   rowid={rowid} city={city} {metric} {season} ldv={ldv}")

    print(f"\n{len(rows)} VERIFIED rows checked. {errors} failures.")
    return 0 if errors == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("subcommand", choices=["inspect", "promote", "verify"],
                   help="inspect | promote | verify")
    p.add_argument("--metric", choices=["high", "low", "both"], default="both",
                   help="Filter by temperature metric (default: both).")
    p.add_argument("--family", default=_DEFAULT_FAMILY,
                   help=f"error_model_family filter (default: {_DEFAULT_FAMILY!r}).")
    p.add_argument("--commit", action="store_true",
                   help="Apply writes (promote only; default is dry-run).")
    p.add_argument("--db", default=None,
                   help="Override world.db path.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="More output.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s %(message)s")

    db_path = args.db or str(ZEUS_WORLD_DB_PATH)

    # INV-37: single-DB connection. write_class="bulk" for promote against prod;
    # custom --db path uses direct sqlite3.connect (staging/copy DB, not INV-37 subject).
    needs_write = (args.subcommand == "promote" and args.commit)
    if args.db:
        # Custom path override — connect directly (read-write if needed).
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    elif needs_write:
        conn = get_world_connection(write_class="bulk")
    else:
        conn = get_world_connection(write_class=None)

    if not args.db:
        conn.row_factory = sqlite3.Row

    dispatch = {"inspect": cmd_inspect, "promote": cmd_promote, "verify": cmd_verify}
    rc = dispatch[args.subcommand](args, conn)
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
