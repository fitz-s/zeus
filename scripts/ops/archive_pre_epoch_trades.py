#!/usr/bin/env python3
# Lifecycle: created=2026-07-23; last_reviewed=2026-07-23; last_reused=never
# Purpose: operator tool — archive pre-epoch (default 2026-07-01) trade records
#   out of zeus_trades.db into a standalone archive DB, then delete them
#   FK-safe child-first; dry-run by default, --execute gated on a backup.
# Reuse: run dry-run before any retention/archive operation on zeus_trades.db;
#   re-audit on any change to the trade-class schema, its append-only guard
#   triggers, or FK edges.
# Authority basis: operator directive 2026-07-2x "清空7月之前所有的交易记录作为archive
#   不要再分析" (archive all pre-2026-07-01 trade records; analytics must never
#   consult them again). Trigger drop/restore pattern is the one already
#   operator-authorized in scripts/purge_partial_fsr_events.py (2026-05-31).
#   Online-backup/manifest convention from scripts/ops/backup_canonical_dbs.py
#   (W1 P0). Companion analysis-side constant: src/analysis/epoch.py.
"""Operator tool: archive pre-epoch trade records out of zeus_trades.db.

Default mode is DRY-RUN: prints per-table candidate counts, the FK-ordered
delete plan, and the precondition verdict, and writes nothing.

--execute performs, on a single connection, in this order:
  1. Precondition gate (re-checked, never assumed): zero OPEN positions
     (phase IN pending_entry/active/day0_window/pending_exit) may have any
     venue_commands row with created_at < epoch. Any violation aborts before
     any write.
  2. Backup gate: requires either a --backup-manifest pointing at a same-day,
     verify_ok manifest produced by scripts/ops/backup_canonical_dbs.py that
     covers zeus_trades.db, or an explicit --i-have-a-backup acknowledgement.
  3. Candidate discovery for every table in DELETE_ORDER (materialized as an
     explicit list of primary keys, so later phases operate on a frozen set
     immune to concurrent live-daemon writes — which, by construction, can
     only ever be POST-epoch).
  4. Archive copy: a NEW sqlite DB at state/archive/zeus_trades_pre<date>.db,
     one CREATE TABLE per source table (copied verbatim from the live table's
     own sqlite_master SQL, which excludes triggers — type='table' only), rows
     copied via ATTACH + INSERT...SELECT in batches of <= BATCH_SIZE, one
     transaction per batch. Refuses to proceed to delete if archived counts
     don't match candidate counts for every table.
  5. Delete: for GUARDED_TABLES (position_events, position_lots,
     venue_order_facts, venue_trade_facts — append-only BEFORE DELETE
     triggers), drops the guard trigger, deletes in FK-safe child-first order
     in batches of <= BATCH_SIZE (each batch its own transaction, so the live
     daemon is never blocked for long), and restores the trigger
     unconditionally in a finally block even if a delete raised.
  6. Reconciliation: prints archived == deleted == candidate per table.
     Refuses to report success if any table's counts disagree.

Usage:
    python3 scripts/ops/archive_pre_epoch_trades.py                     # dry-run, live DB
    python3 scripts/ops/archive_pre_epoch_trades.py --db /path/to.db     # dry-run, other DB
    python3 scripts/ops/archive_pre_epoch_trades.py --execute \\
        --backup-manifest /Volumes/backup/zeus/2026-07-23/backup_manifest_20260723T...Z.json
    python3 scripts/ops/archive_pre_epoch_trades.py --execute --i-have-a-backup
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis.epoch import ANALYSIS_EPOCH  # noqa: E402
from src.execution.settlement_commands import SettlementState  # noqa: E402
from src.state.db import _zeus_trade_db_path  # noqa: E402

DEFAULT_EPOCH = ANALYSIS_EPOCH.replace("+00:00", "Z")  # "2026-07-01T00:00:00Z"

BATCH_SIZE = 5000

OPEN_PHASES = ("pending_entry", "active", "day0_window", "pending_exit")
TERMINAL_PHASES = ("settled", "voided", "admin_closed", "economically_closed")

_SETTLEMENT_TERMINAL_STATES = tuple(
    s.value
    for s in (
        SettlementState.REDEEM_CONFIRMED,
        SettlementState.REDEEM_FAILED,
        SettlementState.REDEEM_REVIEW_REQUIRED,
    )
)

# Tables the operator's safety audit excluded entirely: append-only with ZERO
# pre-epoch rows by construction (created 2026-07-13), or not trade records
# (market snapshot data whose rotation is a separate op). Never touched here.
EXCLUDED_TABLES = (
    "wallet_fill_observations",
    "payout_observations",
    "executable_market_snapshots",
)

# Schema-audit finding (this task, 2026-07-23): the brief called these
# "deletable-with-care", but src/state/db.py's _TRADE_CLASS_DDL gives all four
# the SAME append-only BEFORE DELETE guard as the tables above. Precedent for
# an operator-authorized bypass: scripts/purge_partial_fsr_events.py
# (2026-05-31) drops+restores an identical guard on opportunity_events.
GUARDED_TABLES = {
    "position_events": ("trg_position_events_no_delete",),
    "position_lots": ("position_lots_no_delete",),
    "venue_order_facts": ("venue_order_facts_no_delete",),
    "venue_trade_facts": ("venue_trade_facts_no_delete",),
}

# FK-safe child-first delete order. Declared REFERENCES only (verified against
# sqlite_master at runtime by _assert_no_unhandled_fk_edges — this list is not
# trusted blindly).
DELETE_ORDER = (
    "position_lots",              # -> venue_commands, venue_trade_facts
    "venue_order_facts",          # -> venue_commands
    "venue_trade_facts",          # -> venue_commands
    "exit_mutex_holdings",        # -> venue_commands
    "position_events",            # no enforced FK; position-scoped
    "venue_command_events",       # no enforced FK; command-scoped
    "venue_commands",              # parent of the four above
    "position_current",            # no incoming FK
    "trade_decisions",             # no incoming FK
    "settlement_command_events",   # -> settlement_commands
    "settlement_commands",         # parent
)

_KNOWN_PARENTS = {"venue_commands", "venue_trade_facts", "settlement_commands"}
_KNOWN_CHILD_EDGES = {
    ("venue_order_facts", "venue_commands"),
    ("venue_trade_facts", "venue_commands"),
    ("position_lots", "venue_commands"),
    ("position_lots", "venue_trade_facts"),
    ("exit_mutex_holdings", "venue_commands"),
    ("settlement_command_events", "settlement_commands"),
}


def _connect(db_path: Path) -> sqlite3.Connection:
    # isolation_level=None (autocommit): the delete/copy phases manage their
    # own explicit BEGIN/COMMIT per batch; mixing that with sqlite3's default
    # implicit-transaction handling deadlocks the ATTACHed archive DB on DETACH.
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _assert_no_unhandled_fk_edges(conn: sqlite3.Connection) -> None:
    """Discover every FK edge in the live schema pointing at a table this
    script deletes from, and refuse to proceed if one exists that isn't in
    _KNOWN_CHILD_EDGES. This is the runtime discovery the operator asked for:
    the hardcoded DELETE_ORDER above is a claim, not an assumption — verified
    here every run against whatever schema is actually on disk.
    """
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    unhandled = []
    for child in tables:
        for fk in conn.execute(f'PRAGMA foreign_key_list("{child}")').fetchall():
            parent = fk["table"]
            if parent in DELETE_ORDER or parent in _KNOWN_PARENTS:
                if (child, parent) not in _KNOWN_CHILD_EDGES:
                    unhandled.append((child, parent, dict(fk)))
    if unhandled:
        raise SystemExit(
            "REFUSED: discovered FK edge(s) not accounted for in "
            f"DELETE_ORDER: {unhandled}. Add explicit handling before running."
        )


def _archived_position_ids(conn: sqlite3.Connection, epoch: str) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT position_id FROM position_current
        WHERE updated_at < ?
          AND phase IN ({','.join('?' for _ in TERMINAL_PHASES)})
        """,
        (epoch, *TERMINAL_PHASES),
    ).fetchall()
    return [r["position_id"] for r in rows]


def _orphan_residue_position_ids(conn: sqlite3.Connection, epoch: str) -> list[str]:
    """Position ids with rows in position_events/position_lots but no
    position_current row at all (already-absent positions), where every row
    for that position in BOTH tables predates the epoch."""
    known = {r["position_id"] for r in conn.execute("SELECT position_id FROM position_current").fetchall()}

    pe_ids = {
        r["position_id"]
        for r in conn.execute("SELECT DISTINCT position_id FROM position_events").fetchall()
        if r["position_id"] not in known
    }
    pl_ids = {
        str(r["position_id"])
        for r in conn.execute("SELECT DISTINCT position_id FROM position_lots").fetchall()
        if str(r["position_id"]) not in known
    }
    candidates = pe_ids | pl_ids

    orphans = []
    for pid in candidates:
        pe_max = conn.execute(
            "SELECT MAX(occurred_at) FROM position_events WHERE position_id = ?", (pid,)
        ).fetchone()[0]
        pl_max = conn.execute(
            "SELECT MAX(observed_at) FROM position_lots WHERE CAST(position_id AS TEXT) = ?", (pid,)
        ).fetchone()[0]
        if (pe_max is None or pe_max < epoch) and (pl_max is None or pl_max < epoch):
            orphans.append(pid)
    return orphans


def _precondition_violations(conn: sqlite3.Connection, epoch: str) -> list[dict]:
    """Zero OPEN positions may have any venue_commands row created before
    epoch. Returns the offending (position_id, phase, command_id) triples;
    empty list means the gate passes."""
    rows = conn.execute(
        f"""
        SELECT vc.position_id, pc.phase, vc.command_id, vc.created_at
        FROM venue_commands vc
        JOIN position_current pc ON pc.position_id = vc.position_id
        WHERE vc.created_at < ?
          AND pc.phase IN ({','.join('?' for _ in OPEN_PHASES)})
        """,
        (epoch, *OPEN_PHASES),
    ).fetchall()
    return [dict(r) for r in rows]


def _candidate_venue_command_ids(
    conn: sqlite3.Connection, epoch: str, position_ids: list[str]
) -> list[str]:
    """Pre-epoch commands of archived/orphan positions with no post-epoch
    child rows (order/trade facts, lots, exit-mutex holdings)."""
    if not position_ids:
        return []
    placeholders = ",".join("?" for _ in position_ids)
    rows = conn.execute(
        f"""
        SELECT vc.command_id
        FROM venue_commands vc
        WHERE vc.position_id IN ({placeholders})
          AND vc.created_at < ?
          AND NOT EXISTS (
              SELECT 1 FROM venue_order_facts f
              WHERE f.command_id = vc.command_id AND f.observed_at >= ?
          )
          AND NOT EXISTS (
              SELECT 1 FROM venue_trade_facts f
              WHERE f.command_id = vc.command_id AND f.observed_at >= ?
          )
          AND NOT EXISTS (
              SELECT 1 FROM position_lots pl
              WHERE pl.source_command_id = vc.command_id AND pl.observed_at >= ?
          )
          AND NOT EXISTS (
              SELECT 1 FROM exit_mutex_holdings em
              WHERE em.command_id = vc.command_id AND em.acquired_at >= ?
          )
        """,
        (*position_ids, epoch, epoch, epoch, epoch, epoch),
    ).fetchall()
    return [r["command_id"] for r in rows]


def _in_clause(col: str, n: int) -> str:
    return f"{col} IN ({','.join('?' for _ in range(n))})"


def _candidates(conn: sqlite3.Connection, epoch: str) -> dict:
    """Compute the frozen candidate primary-key list for every table this
    script touches. Returns {table: (pk_column, [pk, ...])}."""
    archived_positions = _archived_position_ids(conn, epoch)
    orphan_positions = _orphan_residue_position_ids(conn, epoch)
    all_positions = archived_positions + orphan_positions
    # Commands are swept for archived AND orphan positions: an orphan's
    # command chain is exactly as pre-epoch as its events/lots residue, and
    # the same post-epoch-child NOT EXISTS guards apply (Copilot #442).
    command_ids = _candidate_venue_command_ids(conn, epoch, all_positions)

    out: dict[str, tuple[str, list]] = {}

    if all_positions:
        ph = ",".join("?" for _ in all_positions)
        out["position_events"] = (
            "event_id",
            [
                r["event_id"]
                for r in conn.execute(
                    f"SELECT event_id FROM position_events WHERE position_id IN ({ph}) AND occurred_at < ?",
                    (*all_positions, epoch),
                ).fetchall()
            ],
        )
        out["position_lots"] = (
            "lot_id",
            [
                r["lot_id"]
                for r in conn.execute(
                    f"SELECT lot_id FROM position_lots WHERE CAST(position_id AS TEXT) IN ({ph})",
                    all_positions,
                ).fetchall()
            ],
        )
    else:
        out["position_events"] = ("event_id", [])
        out["position_lots"] = ("lot_id", [])

    if archived_positions:
        ph = ",".join("?" for _ in archived_positions)
        out["position_current"] = (
            "position_id",
            [
                r["position_id"]
                for r in conn.execute(
                    f"SELECT position_id FROM position_current WHERE position_id IN ({ph}) AND updated_at < ? "
                    f"AND phase IN ({','.join('?' for _ in TERMINAL_PHASES)})",
                    (*archived_positions, epoch, *TERMINAL_PHASES),
                ).fetchall()
            ],
        )
    else:
        out["position_current"] = ("position_id", [])

    if command_ids:
        ph = ",".join("?" for _ in command_ids)
        out["venue_order_facts"] = (
            "fact_id",
            [
                r["fact_id"]
                for r in conn.execute(
                    f"SELECT fact_id FROM venue_order_facts WHERE command_id IN ({ph})", command_ids
                ).fetchall()
            ],
        )
        out["venue_trade_facts"] = (
            "trade_fact_id",
            [
                r["trade_fact_id"]
                for r in conn.execute(
                    f"SELECT trade_fact_id FROM venue_trade_facts WHERE command_id IN ({ph})", command_ids
                ).fetchall()
            ],
        )
        out["exit_mutex_holdings"] = (
            "mutex_key",
            [
                r["mutex_key"]
                for r in conn.execute(
                    f"SELECT mutex_key FROM exit_mutex_holdings WHERE command_id IN ({ph})", command_ids
                ).fetchall()
            ],
        )
        out["venue_command_events"] = (
            "event_id",
            [
                r["event_id"]
                for r in conn.execute(
                    f"SELECT event_id FROM venue_command_events WHERE command_id IN ({ph})", command_ids
                ).fetchall()
            ],
        )
        out["venue_commands"] = ("command_id", list(command_ids))
    else:
        out["venue_order_facts"] = ("fact_id", [])
        out["venue_trade_facts"] = ("trade_fact_id", [])
        out["exit_mutex_holdings"] = ("mutex_key", [])
        out["venue_command_events"] = ("event_id", [])
        out["venue_commands"] = ("command_id", [])

    out["trade_decisions"] = (
        "trade_id",
        [
            r["trade_id"]
            for r in conn.execute(
                "SELECT trade_id FROM trade_decisions WHERE timestamp < ?", (epoch,)
            ).fetchall()
        ],
    )

    if _table_exists(conn, "settlement_commands"):
        ph_states = ",".join("?" for _ in _SETTLEMENT_TERMINAL_STATES)
        sc_ids = [
            r["command_id"]
            for r in conn.execute(
                f"SELECT command_id FROM settlement_commands WHERE requested_at < ? AND state IN ({ph_states})",
                (epoch, *_SETTLEMENT_TERMINAL_STATES),
            ).fetchall()
        ]
        out["settlement_commands"] = ("command_id", sc_ids)
        if sc_ids:
            ph = ",".join("?" for _ in sc_ids)
            out["settlement_command_events"] = (
                "id",
                [
                    r["id"]
                    for r in conn.execute(
                        f"SELECT id FROM settlement_command_events WHERE command_id IN ({ph})", sc_ids
                    ).fetchall()
                ],
            )
        else:
            out["settlement_command_events"] = ("id", [])
    else:
        out["settlement_commands"] = ("command_id", [])
        out["settlement_command_events"] = ("id", [])

    return out


def _batched(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _validate_backup_manifest(manifest_path: Path, db_name: str = "zeus_trades.db") -> None:
    if not manifest_path.exists():
        raise SystemExit(f"REFUSED: --backup-manifest not found: {manifest_path}")
    data = json.loads(manifest_path.read_text())
    created_at = data.get("created_at", "")
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if not created_at.startswith(today):
        raise SystemExit(
            f"REFUSED: backup manifest is not from today (UTC): created_at={created_at!r}, today={today!r}"
        )
    entries = data.get("entries", [])
    match = next((e for e in entries if e.get("db") == db_name), None)
    if match is None:
        raise SystemExit(f"REFUSED: manifest has no entry for {db_name}: {manifest_path}")
    if not match.get("verify", {}).get("ok"):
        raise SystemExit(f"REFUSED: manifest entry for {db_name} is not verify_ok: {match}")


def _report_plan(candidates: dict, precondition_violations: list[dict], epoch: str) -> None:
    print(f"Epoch boundary: {epoch}")
    print()
    if precondition_violations:
        print("PRECONDITION VIOLATION: OPEN positions hold pre-epoch venue_commands:")
        for v in precondition_violations:
            print(f"  position_id={v['position_id']} phase={v['phase']} command_id={v['command_id']} created_at={v['created_at']}")
        print()
        print("ABORT: cannot proceed while any open position has pre-epoch command history.")
        return

    print("Precondition gate: PASS (no open position holds a pre-epoch command).")
    print()
    print("Candidate rows per table (FK-ordered delete plan):")
    for table in DELETE_ORDER:
        _, ids = candidates.get(table, (None, []))
        print(f"  {table}: {len(ids)}")
    print()
    guarded = [t for t in DELETE_ORDER if t in GUARDED_TABLES and candidates.get(t, (None, []))[1]]
    if guarded:
        print(f"Append-only guard triggers will be dropped+restored for: {', '.join(guarded)}")


def _run_dry_run(conn: sqlite3.Connection, epoch: str) -> int:
    _assert_no_unhandled_fk_edges(conn)
    violations = _precondition_violations(conn, epoch)
    candidates = _candidates(conn, epoch) if not violations else {}
    _report_plan(candidates, violations, epoch)
    return 1 if violations else 0


def _create_archive_tables(conn: sqlite3.Connection, archive_path: Path, tables: list[str]) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        raise SystemExit(f"REFUSED: archive DB already exists: {archive_path}")
    arc = sqlite3.connect(str(archive_path))
    try:
        for table in tables:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if row is None or row["sql"] is None:
                raise SystemExit(f"REFUSED: no CREATE TABLE SQL found for {table}")
            arc.execute(row["sql"])
        arc.commit()
    finally:
        arc.close()


def _archive_and_delete(
    conn: sqlite3.Connection, archive_path: Path, candidates: dict, epoch: str
) -> dict:
    tables_with_rows = [t for t in DELETE_ORDER if candidates.get(t, (None, []))[1]]
    _create_archive_tables(conn, archive_path, tables_with_rows)

    # Archive tables are created only for tables with candidates (some, like
    # venue_commands, may be empty for an orphan-residue-only run) — a
    # position_lots/venue_trade_facts row copied via an ATTACHed connection
    # that still enforces foreign_keys would then fail FK validation against
    # an archive table that was never created. The archive DB is a passive
    # historical copy, not a live-enforced schema, so FK checking is off for
    # this phase only; it is back ON for the delete phase below, where it is
    # a real safety net against a DELETE_ORDER mistake.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ATTACH DATABASE ? AS arc", (str(archive_path),))
    archived_counts: dict[str, int] = {}
    try:
        for table in tables_with_rows:
            pk_col, ids = candidates[table]
            total = 0
            for batch in _batched(ids, BATCH_SIZE):
                ph = ",".join("?" for _ in batch)
                conn.execute("BEGIN")
                cur = conn.execute(
                    f'INSERT INTO arc."{table}" SELECT * FROM "{table}" WHERE {pk_col} IN ({ph})', batch
                )
                total += cur.rowcount
                conn.execute("COMMIT")
            archived_counts[table] = total
    finally:
        conn.execute("DETACH DATABASE arc")
        conn.execute("PRAGMA foreign_keys = ON")

    for table in tables_with_rows:
        _, ids = candidates[table]
        if archived_counts.get(table, 0) != len(ids):
            raise SystemExit(
                f"REFUSED: archived count for {table} ({archived_counts.get(table)}) "
                f"!= candidate count ({len(ids)}). No deletes performed for this table."
            )

    deleted_counts: dict[str, int] = {}
    dropped_triggers: list[tuple[str, str]] = []
    try:
        for table in tables_with_rows:
            if table in GUARDED_TABLES:
                for trig in GUARDED_TABLES[table]:
                    conn.execute(f'DROP TRIGGER IF EXISTS "{trig}"')
                    dropped_triggers.append((table, trig))

        for table in DELETE_ORDER:
            pk_col, ids = candidates.get(table, (None, []))
            if not ids:
                deleted_counts[table] = 0
                continue
            total = 0
            for batch in _batched(ids, BATCH_SIZE):
                ph = ",".join("?" for _ in batch)
                conn.execute("BEGIN")
                cur = conn.execute(f'DELETE FROM "{table}" WHERE {pk_col} IN ({ph})', batch)
                total += cur.rowcount
                conn.execute("COMMIT")
            deleted_counts[table] = total
    finally:
        # Restore every dropped trigger unconditionally, even on failure above,
        # from the canonical DDL (triggers were DROPped, not stashed).
        _restore_guarded_triggers(conn, [t for t, _ in dropped_triggers])

    return {"archived": archived_counts, "deleted": deleted_counts}


_TRIGGER_DDL = {
    "trg_position_events_no_delete": """
        CREATE TRIGGER IF NOT EXISTS trg_position_events_no_delete
        BEFORE DELETE ON position_events
        BEGIN
            SELECT RAISE(FAIL, 'position_events is append-only');
        END
    """,
    "position_lots_no_delete": """
        CREATE TRIGGER IF NOT EXISTS position_lots_no_delete
        BEFORE DELETE ON position_lots
        BEGIN
          SELECT RAISE(ABORT, 'position_lots is append-only');
        END
    """,
    "venue_order_facts_no_delete": """
        CREATE TRIGGER IF NOT EXISTS venue_order_facts_no_delete
        BEFORE DELETE ON venue_order_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_order_facts is append-only');
        END
    """,
    "venue_trade_facts_no_delete": """
        CREATE TRIGGER IF NOT EXISTS venue_trade_facts_no_delete
        BEFORE DELETE ON venue_trade_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
        END
    """,
}


def _restore_guarded_triggers(conn: sqlite3.Connection, tables: list[str]) -> None:
    for table in tables:
        for trig in GUARDED_TABLES[table]:
            conn.execute(_TRIGGER_DDL[trig])
    conn.commit()


def _report_reconciliation(result: dict, candidates: dict) -> bool:
    print("Reconciliation (candidate == archived == deleted):")
    ok = True
    for table in DELETE_ORDER:
        cand = len(candidates.get(table, (None, []))[1])
        arc = result["archived"].get(table, 0)
        deleted = result["deleted"].get(table, 0)
        row_ok = cand == arc == deleted
        ok = ok and row_ok
        flag = "OK" if row_ok else "MISMATCH"
        print(f"  {table}: candidate={cand} archived={arc} deleted={deleted}  [{flag}]")
    return ok


def _run_execute(
    conn: sqlite3.Connection, epoch: str, archive_path: Path, backup_manifest: Optional[Path], i_have_a_backup: bool
) -> int:
    violations = _precondition_violations(conn, epoch)
    if violations:
        _report_plan({}, violations, epoch)
        return 1

    if backup_manifest is not None:
        _validate_backup_manifest(backup_manifest)
    elif not i_have_a_backup:
        raise SystemExit(
            "REFUSED: --execute requires --backup-manifest <path> (from "
            "scripts/ops/backup_canonical_dbs.py) or --i-have-a-backup."
        )

    _assert_no_unhandled_fk_edges(conn)
    candidates = _candidates(conn, epoch)
    _report_plan(candidates, [], epoch)
    print()

    result = _archive_and_delete(conn, archive_path, candidates, epoch)
    print()
    ok = _report_reconciliation(result, candidates)
    if not ok:
        print("REFUSED: reconciliation mismatch — see table above.", file=sys.stderr)
        return 1
    print("OK: archive complete, all tables reconciled.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--epoch", default=DEFAULT_EPOCH, help=f"ISO8601 UTC boundary (default: {DEFAULT_EPOCH})")
    ap.add_argument("--db", default=None, help="Path to zeus_trades.db (default: live path)")
    ap.add_argument("--execute", action="store_true", help="Perform the archive+delete (default is dry-run)")
    ap.add_argument("--backup-manifest", default=None, help="Path to a same-day backup_canonical_dbs.py manifest")
    ap.add_argument("--i-have-a-backup", action="store_true", help="Explicit ack in lieu of --backup-manifest")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else _zeus_trade_db_path()
    if not db_path.exists():
        raise SystemExit(f"REFUSED: DB not found: {db_path}")

    conn = _connect(db_path)
    try:
        if not args.execute:
            return _run_dry_run(conn, args.epoch)

        epoch_date = args.epoch[:10]
        archive_path = db_path.parent / "archive" / f"zeus_trades_pre{epoch_date}.db"
        backup_manifest = Path(args.backup_manifest) if args.backup_manifest else None
        return _run_execute(conn, args.epoch, archive_path, backup_manifest, args.i_have_a_backup)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
