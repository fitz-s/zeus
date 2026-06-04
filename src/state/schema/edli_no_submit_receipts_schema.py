"""EDLI no-submit receipt schema owner."""

from __future__ import annotations

import json
import sqlite3

from src.decision_kernel.canonicalization import stable_hash


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS edli_no_submit_receipts (
    receipt_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    causal_snapshot_id TEXT,
    decision_time TEXT NOT NULL,
    family_id TEXT,
    candidate_id TEXT,
    condition_id TEXT,
    token_id TEXT,
    direction TEXT,
    executable_snapshot_id TEXT,
    final_intent_id TEXT,
    side_effect_status TEXT NOT NULL CHECK (side_effect_status = 'NO_SUBMIT'),
    q_live REAL,
    q_lcb_5pct REAL,
    c_fee_adjusted REAL,
    c_cost_95pct REAL,
    p_fill_lcb REAL,
    trade_score REAL,
    fdr_family_id TEXT,
    fdr_hypothesis_count INTEGER NOT NULL DEFAULT 0,
    kelly_cost_basis_id TEXT,
    kelly_decision_id TEXT,
    risk_decision_id TEXT,
    kelly_size_usd REAL NOT NULL DEFAULT 0.0,
    projection_hash TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(event_id, final_intent_id)
)
"""

CREATE_EVENT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_no_submit_receipts_event
    ON edli_no_submit_receipts(event_id)
"""

CREATE_DECISION_TIME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_no_submit_receipts_decision_time
    ON edli_no_submit_receipts(decision_time)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    _ensure_column(conn, "kelly_decision_id", "TEXT")
    _ensure_column(conn, "risk_decision_id", "TEXT")
    _ensure_column(conn, "projection_hash", "TEXT")
    _backfill_projection_hash(conn)
    # Mainstream-agreement gate columns (#135, 2026-06-03).
    # Added via _ensure_column so existing DBs are migrated without data loss.
    _ensure_column(conn, "mainstream_agreement_pass", "INTEGER")
    _ensure_column(conn, "mainstream_agreement_fail_reason", "TEXT")
    _ensure_column(conn, "mainstream_point", "REAL")
    _ensure_column(conn, "mainstream_delta", "REAL")
    _ensure_column(conn, "mainstream_bin_label", "TEXT")
    _ensure_column(conn, "mainstream_source", "TEXT")
    _ensure_column(conn, "mainstream_fetched_at_utc", "TEXT")
    # B2 (PR-4, 2026-06-03): edge-axis measurement column.
    # alpha_gap = q_live - c_fee_adjusted.  NULL when c_fee_adjusted is NULL.
    # Added via _ensure_column so existing live DBs are migrated on next boot.
    _ensure_column(conn, "alpha_gap", "REAL")
    _backfill_alpha_gap(conn)
    conn.execute(CREATE_EVENT_INDEX_SQL)
    conn.execute(CREATE_DECISION_TIME_INDEX_SQL)


def _ensure_column(conn: sqlite3.Connection, column_name: str, column_sql: str) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(edli_no_submit_receipts)").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE edli_no_submit_receipts ADD COLUMN {column_name} {column_sql}")


def _backfill_alpha_gap(conn: sqlite3.Connection) -> None:
    """Backfill alpha_gap for existing rows from receipt_json.

    For rows where alpha_gap IS NULL: recover q_live and c_fee_adjusted from the
    stored receipt_json blob and compute alpha_gap = q_live - c_fee_adjusted.
    Rows where c_fee_adjusted is missing in JSON are left NULL (fail-closed).

    This ensures the column is populated for the ~60k existing shadow receipts on
    the live DB without requiring a full receipt re-process.
    """
    rows = conn.execute(
        """
        SELECT receipt_id, receipt_json
        FROM edli_no_submit_receipts
        WHERE alpha_gap IS NULL
        """
    ).fetchall()
    for row in rows:
        receipt_id = row["receipt_id"] if isinstance(row, sqlite3.Row) else row[0]
        receipt_json_str = row["receipt_json"] if isinstance(row, sqlite3.Row) else row[1]
        try:
            payload = json.loads(receipt_json_str)
        except (ValueError, TypeError):
            continue
        q_live = payload.get("q_live")
        c_fee_adjusted = payload.get("c_fee_adjusted")
        if q_live is None or c_fee_adjusted is None:
            continue  # leave NULL — fail-closed, no executable price
        try:
            alpha_gap = float(q_live) - float(c_fee_adjusted)
        except (ValueError, TypeError):
            continue
        conn.execute(
            "UPDATE edli_no_submit_receipts SET alpha_gap = ? WHERE receipt_id = ?",
            (alpha_gap, receipt_id),
        )


def _backfill_projection_hash(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT receipt_id, event_id, final_intent_id, side_effect_status,
               executable_snapshot_id, receipt_json
        FROM edli_no_submit_receipts
        WHERE projection_hash IS NULL OR projection_hash = ''
        """
    ).fetchall()
    for row in rows:
        receipt_json = row["receipt_json"] if isinstance(row, sqlite3.Row) else row[5]
        payload = json.loads(receipt_json)
        projection = {
            "event_id": row["event_id"] if isinstance(row, sqlite3.Row) else row[1],
            "final_intent_id": row["final_intent_id"] if isinstance(row, sqlite3.Row) else row[2],
            "side_effect_status": row["side_effect_status"] if isinstance(row, sqlite3.Row) else row[3],
            "proof_accepted": payload.get("proof_accepted"),
            "submitted": payload.get("submitted"),
            "executable_snapshot_id": row["executable_snapshot_id"] if isinstance(row, sqlite3.Row) else row[4],
        }
        conn.execute(
            "UPDATE edli_no_submit_receipts SET projection_hash = ? WHERE receipt_id = ?",
            (
                stable_hash(projection),
                row["receipt_id"] if isinstance(row, sqlite3.Row) else row[0],
            ),
        )
