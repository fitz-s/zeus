# Lifecycle: created=2026-07-02; last_reviewed=2026-07-02; last_reused=never
# Purpose: SCH-W1.1-CAS-LEDGER live-DB migration — additive collateral schema
#   (converted_amount column, collateral_unsettled_proceeds table + index,
#   trg_reservations_no_overreserve trigger) plus the exchange_reconcile_findings
#   'collateral_identity_mismatch' finding-kind CHECK widening.
#
# Migration semantic policy:
#   DB target: state/zeus_trades.db only (single-DB, Domain.TRADE per
#   src/state/domains.py:45-46,79 — INV-37 not triggered, no cross-DB ATTACH).
#   Tables touched:
#     collateral_reservations       — ADD COLUMN converted_amount (additive, safe)
#     collateral_unsettled_proceeds — CREATE TABLE IF NOT EXISTS (new table)
#     trg_reservations_no_overreserve — CREATE TRIGGER IF NOT EXISTS (new trigger)
#     exchange_reconcile_findings   — CHECK-widen kind IN (...) via SQLite
#       table-rebuild (SQLite cannot ALTER CHECK in place). Precedent search
#       (packet decision 3) found TWO precedents using this exact
#       CREATE-new + INSERT-copy + DROP-old + RENAME recipe:
#         - scripts/migrations/202605_add_redeem_operator_required_state.py
#           (settlement_commands.state CHECK widening, current formal
#           migrations-framework precedent — this migration follows ITS shape)
#         - commit c8b4962a (pre-framework, backtest_runs.lane CHECK widening)
#       Both precedents found BEFORE any schema was written; the packet's
#       table-rebuild fallback recipe was not needed — the precedent covers it.
#   Schema fingerprint: exempted. scripts/check_schema_fingerprint.py:55-68
#   excludes the entire _TRADE_CLASS_DDL block (which includes every table this
#   migration touches) from the fingerprint; no fingerprint refresh applies
#   here (packet ci_gates_required note).
#   Reversibility: down() is NOT provided — converted_amount and
#   collateral_unsettled_proceeds are additive/inert without writers (packet
#   rollback note); the exchange_reconcile_findings CHECK rebuild only
#   reverses safely if zero collateral_identity_mismatch rows exist, which is
#   an operator-verified precondition, not a mechanical revert.
#   Idempotent: safe to re-run; each step checks current state first.
# Authority basis: docs/rebuild/schema_packets/w1_1_cas_reservation_ledger_schema_packet_2026-07-02.md
#   (work_packet_id SCH-W1.1-CAS-LEDGER), critic ruling 3 (decision 3 ordering:
#   search migration history for a prior CHECK-widening precedent FIRST).
"""Live-DB migration for SCH-W1.1-CAS-LEDGER.

Runner interface: def up(conn: sqlite3.Connection) -> None
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "trade"

_NEW_FINDINGS_CHECK_FRAGMENT = "collateral_identity_mismatch"

_NEW_FINDINGS_TABLE_DDL = """
CREATE TABLE exchange_reconcile_findings_v2 (
  finding_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN (
    'exchange_ghost_order','local_orphan_order','unrecorded_trade',
    'position_drift','heartbeat_suspected_cancel','cutover_wipe',
    'collateral_identity_mismatch'
  )),
  subject_id TEXT NOT NULL,
  context TEXT NOT NULL CHECK (context IN ('periodic','ws_gap','heartbeat_loss','cutover','operator')),
  evidence_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT,
  resolution TEXT,
  resolved_by TEXT
)
"""

_FINDINGS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_findings_unresolved "
    "ON exchange_reconcile_findings (resolved_at) WHERE resolved_at IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_findings_unresolved_subject "
    "ON exchange_reconcile_findings (kind, subject_id, context) WHERE resolved_at IS NULL",
]

_UNSETTLED_PROCEEDS_DDL = """
CREATE TABLE IF NOT EXISTS collateral_unsettled_proceeds (
  command_id TEXT PRIMARY KEY,
  direction TEXT NOT NULL CHECK (direction IN ('OUTGOING_DEDUCTION','INCOMING_PROCEEDS')),
  reservation_type TEXT NOT NULL CHECK (reservation_type IN ('PUSD_BUY','CTF_SELL')),
  token_id TEXT,
  amount_micro INTEGER NOT NULL CHECK (amount_micro >= 0),
  created_at TEXT NOT NULL,
  settled_at TEXT,
  settle_reason TEXT,
  CHECK (
    (reservation_type = 'PUSD_BUY' AND token_id IS NULL AND direction = 'OUTGOING_DEDUCTION')
    OR (reservation_type = 'CTF_SELL' AND token_id IS NOT NULL AND direction = 'INCOMING_PROCEEDS')
  )
)
"""

_UNSETTLED_PROCEEDS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_unsettled_open "
    "ON collateral_unsettled_proceeds (settled_at) WHERE settled_at IS NULL"
)

_OVERRESERVE_TRIGGER_DDL = """
CREATE TRIGGER IF NOT EXISTS trg_reservations_no_overreserve
AFTER INSERT ON collateral_reservations
WHEN NEW.reservation_type = 'PUSD_BUY'
AND (
  (SELECT pusd_balance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1)
  - (SELECT COALESCE(SUM(amount),0) FROM collateral_reservations
     WHERE reservation_type='PUSD_BUY' AND released_at IS NULL)
  - (SELECT COALESCE(SUM(amount_micro),0) FROM collateral_unsettled_proceeds
     WHERE direction='OUTGOING_DEDUCTION' AND settled_at IS NULL)
) < 0
BEGIN
  SELECT RAISE(ABORT, 'COLLATERAL_OVERRESERVE');
END
"""


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()[0]
        > 0
    )


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _findings_check_already_widened(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='exchange_reconcile_findings' "
        "AND sql LIKE ?",
        (f"%{_NEW_FINDINGS_CHECK_FRAGMENT}%",),
    ).fetchone()
    return row is not None


def _rebuild_exchange_reconcile_findings(conn: sqlite3.Connection) -> None:
    """SQLite cannot ALTER CHECK in place — rebuild via CREATE new + INSERT
    copy + DROP old + RENAME (precedent: 202605_add_redeem_operator_required_state.py)."""
    legacy_alter_row = conn.execute("PRAGMA legacy_alter_table").fetchone()
    legacy_alter_before = int(legacy_alter_row[0] if legacy_alter_row else 0)
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        conn.execute(_NEW_FINDINGS_TABLE_DDL.strip())
        conn.execute(
            "INSERT INTO exchange_reconcile_findings_v2 "
            "(finding_id, kind, subject_id, context, evidence_json, recorded_at, resolved_at, resolution, resolved_by) "
            "SELECT finding_id, kind, subject_id, context, evidence_json, recorded_at, resolved_at, resolution, resolved_by "
            "FROM exchange_reconcile_findings"
        )
        conn.execute("DROP TABLE exchange_reconcile_findings")
        conn.execute("ALTER TABLE exchange_reconcile_findings_v2 RENAME TO exchange_reconcile_findings")
        for idx_sql in _FINDINGS_INDEXES:
            conn.execute(idx_sql)
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={legacy_alter_before}")


def up(conn: sqlite3.Connection) -> None:
    """Apply SCH-W1.1-CAS-LEDGER's additive collateral schema plus the
    exchange_reconcile_findings CHECK widening. Idempotent."""

    # --- collateral_reservations.converted_amount (additive column) --------
    if _has_table(conn, "collateral_reservations") and not _has_column(
        conn, "collateral_reservations", "converted_amount"
    ):
        conn.execute(
            "ALTER TABLE collateral_reservations ADD COLUMN converted_amount INTEGER NOT NULL DEFAULT 0"
        )

    # --- collateral_unsettled_proceeds (new table, additive+inert) ---------
    conn.execute(_UNSETTLED_PROCEEDS_DDL.strip())
    conn.execute(_UNSETTLED_PROCEEDS_INDEX)

    # --- trg_reservations_no_overreserve (new trigger, belt-and-braces) ----
    if _has_table(conn, "collateral_ledger_snapshots") and _has_table(
        conn, "collateral_reservations"
    ):
        conn.execute(_OVERRESERVE_TRIGGER_DDL.strip())

    # --- exchange_reconcile_findings CHECK widening (table rebuild) --------
    if _has_table(conn, "exchange_reconcile_findings") and not _findings_check_already_widened(
        conn
    ):
        # PRAGMA foreign_keys must be set OUTSIDE the transaction (SQLite docs).
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            before_violations = {tuple(row) for row in conn.execute("PRAGMA foreign_key_check")}
            _rebuild_exchange_reconcile_findings(conn)
            after_violations = {tuple(row) for row in conn.execute("PRAGMA foreign_key_check")}
            new_violations = after_violations - before_violations
            if new_violations:
                conn.execute("ROLLBACK")
                raise RuntimeError(
                    f"foreign_key_check returned {len(new_violations)} new violations after "
                    f"exchange_reconcile_findings rebuild: {sorted(new_violations)[:5]!r}"
                )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    conn.commit()
