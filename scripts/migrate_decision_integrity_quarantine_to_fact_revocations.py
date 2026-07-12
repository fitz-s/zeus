#!/usr/bin/env python3
# Created: 2026-07-12
# Last reused or audited: 2026-07-12
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md DIQ packet
#   (Consult adjudication "DIQ CONDITIONAL": create -> backfill -> per-reason
#   count + payload-hash parity -> switch readers -> assert parity -> drop old).
"""One-shot RED migration: decision_integrity_quarantine (trade DB, single
central table) -> fact_revocations (owner-local, one instance per owning DB).

This is a 3-DB migration with data backfill, NOT a rename: the predecessor
side-table tagged rows in 7 tables spanning all three physical DBs
(src/state/domains.py) while living only in zeus_trades.db. This script
redistributes its rows to the owning DB of each tagged table:
  - opportunity_fact                              -> trade  (co-located; no-op move)
  - decision_certificates, decision_events,
    probability_trace_fact, selection_family_fact,
    selection_hypothesis_fact                     -> world
  - calibration_pairs                              -> forecasts

Reason codes are rewritten from the predecessor's QUARANTINED_* vocabulary to
the REVOKED_* vocabulary (src/state/fact_revocation.py) — historical data,
like historical position_events strings elsewhere in this excision, is
rewritten rather than left stale (T5 replay-analysis precedent).

Sequence (adjudicated RED order):
  1. create   — ensure_table on trade/world/forecasts (idempotent).
  2. backfill — copy each old row into its owning DB's fact_revocations
                (INSERT OR IGNORE; idempotent re-run).
  3. parity   — per (table_name, new_reason_code) row COUNT and a payload-hash
                digest (row_id + meta_json, sorted) must match between the old
                table and the new distributed set. Abort (no drop) on mismatch.
  4. drop     — DROP TABLE decision_integrity_quarantine from trade DB, ONLY
                after parity asserts. Readers already point at fact_revocations
                in this same packet (executor.py, command_recovery.py,
                evidence_report.py, refit_platt.py, scripts/*).

Safety:
  - Refuses to run against live DB paths unless --apply is combined with
    --confirm-backup (mirrors scripts/backfill_forecast_issue_time.py).
  - Idempotent: safe to re-run. If the old table is already gone, reports
    ALREADY_MIGRATED and exits 0 without touching anything.
  - Dry-run (default): reports the plan and parity check without writing.

Usage:
    python scripts/migrate_decision_integrity_quarantine_to_fact_revocations.py [--dry-run]
    python scripts/migrate_decision_integrity_quarantine_to_fact_revocations.py \\
        --apply --confirm-backup \\
        [--trade-db PATH] [--world-db PATH] [--forecasts-db PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.state.schema.fact_revocations_schema import ensure_table  # noqa: E402

OLD_TABLE = "decision_integrity_quarantine"
NEW_TABLE = "fact_revocations"

# table_name (as tagged in the old side-table) -> owning DB, per src/state/domains.py.
TABLE_OWNER_DOMAIN: dict[str, str] = {
    "opportunity_fact": "trade",
    "decision_certificates": "world",
    "decision_events": "world",
    "probability_trace_fact": "world",
    "selection_family_fact": "world",
    "selection_hypothesis_fact": "world",
    "calibration_pairs": "forecasts",
}

# Predecessor QUARANTINED_* reason-code values -> DIQ packet REVOKED_* values
# (src/state/fact_revocation.py). Historical rows are rewritten, not left stale.
REASON_CODE_RENAME: dict[str, str] = {
    "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA": "REVOKED_NON_CONTRIBUTING_FORECAST_EXTREMA",
    "QUARANTINED_INVALID_LIVE_ACTIONABLE_CERTIFICATE": "REVOKED_INVALID_LIVE_ACTIONABLE_CERTIFICATE",
    "QUARANTINED_INVALID_LIVE_MONEY_PARENT_MODE": "REVOKED_INVALID_LIVE_MONEY_PARENT_MODE",
}


class ParityError(RuntimeError):
    """Backfill parity check failed — migration aborted before drop."""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _payload_digest(rows: list[tuple[str, str]]) -> str:
    """Deterministic digest over sorted (row_id, meta_json) pairs."""
    joined = "\n".join(f"{row_id}\x1f{meta_json}" for row_id, meta_json in sorted(rows))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def plan_backfill(trade_conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Read every row from the old table, grouped by target domain.

    Returns {domain: [(table_name, row_id, new_reason_code, forecast_snapshot_id,
    recorded_at, meta_json), ...]}. Raises ValueError on an unmapped table_name
    (a new tagged table added to decision_integrity_quarantine after this
    script was written) — fail loud rather than silently drop rows.
    """
    rows = trade_conn.execute(
        f"SELECT table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json "
        f"FROM {OLD_TABLE} ORDER BY id"
    ).fetchall()
    plan: dict[str, list[tuple]] = {"trade": [], "world": [], "forecasts": []}
    for table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json in rows:
        domain = TABLE_OWNER_DOMAIN.get(str(table_name))
        if domain is None:
            raise ValueError(
                f"migrate_decision_integrity_quarantine_to_fact_revocations: "
                f"unmapped table_name {table_name!r} in old {OLD_TABLE} — "
                f"add it to TABLE_OWNER_DOMAIN before migrating (never silently drop)."
            )
        new_reason = REASON_CODE_RENAME.get(str(reason_code), str(reason_code))
        plan[domain].append(
            (str(table_name), str(row_id), new_reason, forecast_snapshot_id, recorded_at, meta_json)
        )
    return plan


def apply_backfill(
    *,
    trade_conn: sqlite3.Connection,
    world_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    plan: dict[str, list[tuple]],
) -> dict[str, int]:
    """INSERT OR IGNORE each planned row into its owning DB's fact_revocations. Idempotent."""
    conn_for_domain = {"trade": trade_conn, "world": world_conn, "forecasts": forecasts_conn}
    inserted: dict[str, int] = {"trade": 0, "world": 0, "forecasts": 0}
    insert_sql = (
        f"INSERT OR IGNORE INTO {NEW_TABLE} "
        "(table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    for domain, rows in plan.items():
        if not rows:
            continue
        conn = conn_for_domain[domain]
        pre = conn.execute(f"SELECT COUNT(*) FROM {NEW_TABLE}").fetchone()[0]
        conn.executemany(insert_sql, rows)
        post = conn.execute(f"SELECT COUNT(*) FROM {NEW_TABLE}").fetchone()[0]
        inserted[domain] = post - pre
    return inserted


def assert_parity(
    *,
    trade_conn: sqlite3.Connection,
    world_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    plan: dict[str, list[tuple]],
) -> dict[str, object]:
    """Per-(table_name, new_reason_code) row-count AND payload-hash parity.

    Compares the planned rows (derived from the OLD table, reason-renamed)
    against what is actually present in the NEW per-DB tables. Raises
    ParityError on any mismatch — caller must not drop the old table.
    """
    conn_for_domain = {"trade": trade_conn, "world": world_conn, "forecasts": forecasts_conn}
    old_groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for domain, rows in plan.items():
        for table_name, row_id, new_reason, _snap, _recorded, meta_json in rows:
            old_groups.setdefault((table_name, new_reason), []).append((row_id, str(meta_json or "{}")))

    mismatches: list[str] = []
    checked_groups = 0
    for (table_name, new_reason), old_rows in old_groups.items():
        domain = TABLE_OWNER_DOMAIN[table_name]
        conn = conn_for_domain[domain]
        new_rows = conn.execute(
            f"SELECT row_id, meta_json FROM {NEW_TABLE} WHERE table_name=? AND reason_code=?",
            (table_name, new_reason),
        ).fetchall()
        new_rows_norm = [(str(r[0]), str(r[1] or "{}")) for r in new_rows]
        checked_groups += 1
        if len(old_rows) != len(new_rows_norm):
            mismatches.append(
                f"{table_name}/{new_reason}: old count={len(old_rows)} new count={len(new_rows_norm)}"
            )
            continue
        old_digest = _payload_digest(old_rows)
        new_digest = _payload_digest(new_rows_norm)
        if old_digest != new_digest:
            mismatches.append(f"{table_name}/{new_reason}: payload-hash mismatch")

    if mismatches:
        raise ParityError("; ".join(mismatches))
    return {"checked_groups": checked_groups, "total_rows": sum(len(v) for v in plan.values())}


def run_migration(
    *,
    trade_conn: sqlite3.Connection,
    world_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    apply: bool,
) -> dict[str, object]:
    """Full create -> backfill -> parity -> (drop if apply) sequence.

    dry-run (apply=False): no writes at all — reports the plan (per-domain
    candidate counts) so the operator can preview before committing. Never
    touches the old table or writes to any new table.

    apply=True: ensure_table -> backfill (INSERT OR IGNORE, idempotent) ->
    assert_parity -> on success, DROP the old table and commit all three
    connections; on ParityError, roll back every connection (nothing
    committed, old table untouched) and re-raise.
    """
    if not _table_exists(trade_conn, OLD_TABLE):
        return {"status": "ALREADY_MIGRATED", "apply": apply}

    plan = plan_backfill(trade_conn)

    if not apply:
        return {
            "status": "DRY_RUN_OK",
            "apply": False,
            "planned_rows": {domain: len(rows) for domain, rows in plan.items()},
        }

    ensure_table(trade_conn)
    ensure_table(world_conn)
    ensure_table(forecasts_conn)
    try:
        inserted = apply_backfill(
            trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, plan=plan
        )
        parity = assert_parity(
            trade_conn=trade_conn, world_conn=world_conn, forecasts_conn=forecasts_conn, plan=plan
        )
        trade_conn.execute(f"DROP TABLE {OLD_TABLE}")
    except ParityError:
        trade_conn.rollback()
        world_conn.rollback()
        forecasts_conn.rollback()
        raise
    except Exception:
        trade_conn.rollback()
        world_conn.rollback()
        forecasts_conn.rollback()
        raise

    trade_conn.commit()
    world_conn.commit()
    forecasts_conn.commit()
    return {
        "status": "MIGRATED",
        "apply": True,
        "inserted": inserted,
        "parity": parity,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Commit the migration and drop the old table (default: dry-run)")
    p.add_argument(
        "--confirm-backup",
        action="store_true",
        help="Required with --apply: affirms the operator has taken a verified backup of all three DBs.",
    )
    p.add_argument("--trade-db", default=None, metavar="PATH")
    p.add_argument("--world-db", default=None, metavar="PATH")
    p.add_argument("--forecasts-db", default=None, metavar="PATH")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    if args.apply and not args.confirm_backup:
        print(
            "[apply] REFUSED: --apply requires --confirm-backup affirming a verified "
            "backup of trade/world/forecasts DBs exists.",
            file=sys.stderr,
        )
        return 1

    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH, _zeus_trade_db_path

    trade_db = pathlib.Path(args.trade_db) if args.trade_db else pathlib.Path(_zeus_trade_db_path())
    world_db = pathlib.Path(args.world_db) if args.world_db else pathlib.Path(ZEUS_WORLD_DB_PATH)
    forecasts_db = pathlib.Path(args.forecasts_db) if args.forecasts_db else pathlib.Path(ZEUS_FORECASTS_DB_PATH)

    for label, path in (("trade", trade_db), ("world", world_db), ("forecasts", forecasts_db)):
        if not path.exists():
            print(f"ERROR: {label} DB not found: {path}", file=sys.stderr)
            return 1

    lock_context = None
    if args.apply:
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        lock_context = db_writer_lock(trade_db, WriteClass.BULK)

    def _do_run() -> int:
        trade_conn = sqlite3.connect(str(trade_db))
        world_conn = sqlite3.connect(str(world_db))
        forecasts_conn = sqlite3.connect(str(forecasts_db))
        try:
            result = run_migration(
                trade_conn=trade_conn,
                world_conn=world_conn,
                forecasts_conn=forecasts_conn,
                apply=args.apply,
            )
        except ParityError as exc:
            print(f"PARITY CHECK FAILED — migration aborted, old table untouched: {exc}", file=sys.stderr)
            return 1
        finally:
            trade_conn.close()
            world_conn.close()
            forecasts_conn.close()
        print(json.dumps(result, indent=2, default=str))
        return 0

    if lock_context is not None:
        with lock_context:
            return _do_run()
    return _do_run()


if __name__ == "__main__":
    raise SystemExit(main())
