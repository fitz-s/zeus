#!/usr/bin/env python3
# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Authority basis: W2 settlement-store convergence (HANDOFF_2026-06-02_emos_ci.md);
#   src/state/schema/v2_schema.py:_create_settlement_outcomes (the canonical fresh-DB
#   trigger shape this migration brings an EXISTING forecasts DB up to).
# DB target: zeus-forecasts.db (settlement_outcomes is a K1 forecast-class table; single-DB —
#   no cross-DB ATTACH; the intra-DB SAVEPOINT provides the INV-37 atomicity envelope).
# Runner interface: def up(conn: sqlite3.Connection) -> None
# Standalone operator receipts: python scripts/migrations/202606_install_settlement_unit_verified_triggers.py [--execute]
# WRITER_LOCK_DEFER_REVIEW=2026-06-03: standalone path opens a raw sqlite3.connect under
#   --db-path (operator stops/has-not the daemon write lease); daemon never imports this module.
"""Install the VERIFIED-settlement-requires-unit triggers on an EXISTING forecasts DB.

ROOT CAUSE THIS MIGRATION CLOSES
--------------------------------
Task #132 (commit cb7f8abe0f) added two BEFORE INSERT / BEFORE UPDATE triggers to
``_create_settlement_outcomes`` in src/state/schema/v2_schema.py:

    _settlement_outcomes_verified_unit_check          (BEFORE INSERT)
    _settlement_outcomes_verified_unit_check_update   (BEFORE UPDATE)

Both ABORT when ``NEW.authority='VERIFIED' AND NEW.settlement_unit IS NULL``.

That schema function uses ``CREATE TABLE IF NOT EXISTS``. On an EXISTING live DB the
``CREATE TABLE`` is a no-op, AND — critically — the two ``CREATE TRIGGER IF NOT EXISTS``
statements in the same function body only run when the function is *called* against that
connection. The live forecasts DB (state/zeus-forecasts.db) was created before #132 and
``init_schema_forecasts`` was never re-run against it after the trigger lines were added,
so the live table carried the column but NOT the triggers. The antibody existed in code
and in fresh-DB tests, but was structurally INERT in production: a stale ingest daemon
running pre-#132 bytecode wrote 283 VERIFIED settlement_outcomes rows with
settlement_unit=NULL and nothing stopped it.

This migration deploys the antibody to the substrate: it installs the two triggers on the
existing DB so a NULL-unit VERIFIED settlement can never be committed again, regardless of
which writer (or which stale daemon) attempts it.

ORDERING NOTE (operator)
------------------------
Run the NULL-unit backfill (scripts/backfill_settlement_unit_2026_06_03.py --commit) FIRST,
then this migration. The BEFORE UPDATE trigger only aborts when the *new* settlement_unit is
NULL, so a backfill that SETs a non-NULL unit is never blocked — but running backfill first
guarantees no existing-row repair path trips the guard. After installing the trigger, restart
the ingest daemon (src.ingest_main) so it runs the #132 writer fix; otherwise the stale daemon's
NULL writes will (correctly, fail-closed) ABORT and be logged as per-event errors.

Idempotency: checks sqlite_master for each trigger before creating; safe to re-run. A trigger
whose stored SQL differs from canonical is DROP+recreated so drift is repaired.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

TARGET_DB = "forecasts"

logger = logging.getLogger(__name__)

_TABLE = "settlement_outcomes"

# Canonical trigger definitions — MUST stay byte-identical (after whitespace
# normalisation) to src/state/schema/v2_schema.py:_create_settlement_outcomes so a
# fresh DB (which gets them via CREATE TRIGGER IF NOT EXISTS) and a migrated DB
# converge to the same shape.
_TRIGGERS: tuple[tuple[str, str], ...] = (
    (
        "_settlement_outcomes_verified_unit_check",
        """
        CREATE TRIGGER _settlement_outcomes_verified_unit_check
        BEFORE INSERT ON settlement_outcomes
        FOR EACH ROW
        WHEN NEW.authority = 'VERIFIED' AND NEW.settlement_unit IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'VERIFIED_SETTLEMENT_REQUIRES_UNIT');
        END
        """,
    ),
    (
        "_settlement_outcomes_verified_unit_check_update",
        """
        CREATE TRIGGER _settlement_outcomes_verified_unit_check_update
        BEFORE UPDATE ON settlement_outcomes
        FOR EACH ROW
        WHEN NEW.authority = 'VERIFIED' AND NEW.settlement_unit IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'VERIFIED_SETTLEMENT_REQUIRES_UNIT');
        END
        """,
    ),
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def _normalize_sql(sql: str | None) -> str:
    return " ".join((sql or "").split()).strip()


def _existing_trigger_sql(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
        (name,),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _trigger_status(conn: sqlite3.Connection) -> dict[str, str]:
    """Return per-trigger status: 'present' | 'drift' | 'absent'."""
    status: dict[str, str] = {}
    for name, canonical in _TRIGGERS:
        existing = _existing_trigger_sql(conn, name)
        if existing is None:
            status[name] = "absent"
        elif _normalize_sql(existing) == _normalize_sql(canonical):
            status[name] = "present"
        else:
            status[name] = "drift"
    return status


def compute_receipts(conn: sqlite3.Connection) -> dict[str, object]:
    """Pre-migration receipts (read-only): table presence + per-trigger status."""
    if not _table_exists(conn, _TABLE):
        return {"table_exists": False, "triggers": {}}
    return {"table_exists": True, "triggers": _trigger_status(conn)}


def up(conn: sqlite3.Connection) -> None:
    """Runner-framework entry point (def up(conn) contract).

    Installs the two VERIFIED-requires-unit triggers on settlement_outcomes, atomically
    and idempotently. A trigger with drifted SQL is DROP+recreated. No-op when both
    triggers already match canonical.
    """
    if not _table_exists(conn, _TABLE):
        raise AssertionError(
            f"{_TABLE} does not exist — run init_schema_forecasts first; this migration only "
            f"installs triggers on an existing table."
        )

    status = _trigger_status(conn)
    todo = [(name, sql) for name, sql in _TRIGGERS if status[name] != "present"]
    if not todo:
        logger.debug("Both settlement_unit triggers already present; no-op.")
        return

    conn.execute("SAVEPOINT install_settlement_unit_triggers")
    try:
        for name, sql in todo:
            if status[name] == "drift":
                conn.execute(f"DROP TRIGGER {name}")
            conn.execute(sql)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT install_settlement_unit_triggers")
        conn.execute("RELEASE SAVEPOINT install_settlement_unit_triggers")
        raise
    conn.execute("RELEASE SAVEPOINT install_settlement_unit_triggers")
    logger.info(
        "Installed/repaired settlement_unit triggers: %s",
        ", ".join(name for name, _ in todo),
    )


def _standalone(argv: list[str] | None = None) -> int:
    """Operator entry point: dry-run receipts by default; --execute to apply."""
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    parser = argparse.ArgumentParser(
        description="Install VERIFIED-requires-unit triggers on settlement_outcomes (W2)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the CREATE TRIGGER statements (default: dry-run receipts only).",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to zeus-forecasts.db (default: canonical forecasts connection).",
    )
    args = parser.parse_args(argv)

    if args.db_path:
        conn = sqlite3.connect(args.db_path)  # WRITER_LOCK_DEFER_REVIEW=2026-06-03 operator-invoked migration; daemon lock unavailable in standalone path
    else:
        from src.state.db import get_forecasts_connection

        conn = get_forecasts_connection(write_class="bulk")
    try:
        receipts = compute_receipts(conn)
        print("settlement_outcomes VERIFIED-unit triggers — PRE-MIGRATION RECEIPTS")
        print(f"  table_exists: {receipts['table_exists']}")
        for name, state in (receipts.get("triggers") or {}).items():
            print(f"  {name}: {state}")
        if not args.execute:
            print("\nDRY-RUN (no changes applied). Re-run with --execute to apply.")
            return 0
        up(conn)
        conn.commit()
        post = _trigger_status(conn)
        print("\nAPPLIED. POST-MIGRATION RECEIPTS")
        for name, state in post.items():
            print(f"  {name}: {state}")
        all_present = all(s == "present" for s in post.values())
        print(f"  all_triggers_present: {all_present}")
        return 0 if all_present else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_standalone())
