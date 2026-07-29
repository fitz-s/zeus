# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   round-4 review Y1 (bounded outbox): the prior spool reused the canonical
#   append-only family_book_states/family_book_observations schemas
#   verbatim, so every 30s ingest pass replayed the family's ENTIRE lifetime
#   history (no watermark, no LIMIT, no-delete triggers meant it could never
#   drain) into the live-money DB. This table is transport-only, lives in
#   the PRIVATE spool file (never the canonical trade DB), and is
#   deliberately mutable/erasable -- the opposite of the canonical tables'
#   append-only contract, because a transient outbox row's job is to be
#   deleted once safely delivered.
"""family_book_telemetry_outbox -- bounded, deletable at-least-once outbox.

One row per SAMPLED envelope (the writer thread's spool write, X1-safe,
unchanged). ``spool_seq`` is an AUTOINCREMENT rowid alias -- SQLite's
AUTOINCREMENT guarantees it is NEVER reused even across deletes, so
``ORDER BY spool_seq`` is a stable, monotonic watermark for bounded batches.

Protocol (the daemon's ingest job, never the writer thread -- see
src/events/family_book_telemetry_writer.py ``run_bounded_ingest``):
  1. SELECT ... ORDER BY spool_seq LIMIT <row/byte budget>.
  2. INSERT the batch into the canonical family_book_states/
     family_book_observations tables in ONE bounded transaction (targeted
     ON CONFLICT DO NOTHING makes this idempotent).
  3. COMMIT the canonical transaction.
  4. ONLY THEN, in a SEPARATE transaction against THIS (spool) connection,
     DELETE the ingested spool_seq range.
A crash between steps 3 and 4 causes replay on the next pass -- safe,
because step 2 is idempotent. This table carries redundant per-row
``canonical_payload``/state fields (a state repeats across every
observation of an unchanged book) -- acceptable because it is deleted
immediately after ingestion, never a long-lived store.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS family_book_telemetry_outbox (
    spool_seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    enqueued_at_utc        TEXT NOT NULL,
    -- state fields (redundant across observations of one state; transient)
    state_id               TEXT NOT NULL,
    content_hash            TEXT NOT NULL,
    hash_version            INTEGER NOT NULL,
    topology_hash           TEXT NOT NULL,
    complete_book           INTEGER NOT NULL,
    canonical_payload       TEXT NOT NULL,
    payload_schema_version  INTEGER NOT NULL,
    -- observation fields
    observation_id          TEXT NOT NULL,
    family_id               TEXT NOT NULL,
    city                    TEXT NOT NULL,
    target_date             TEXT NOT NULL,
    temperature_metric      TEXT NOT NULL,
    decision_id             TEXT NOT NULL,
    receipt_hash            TEXT NOT NULL,
    source_manifest_json    TEXT NOT NULL,
    decision_time           TEXT NOT NULL,
    causal_snapshot_id      TEXT,
    predictive_identity_hash TEXT,
    our_mu_native           REAL,
    our_sigma_native        REAL,
    measurement_unit        TEXT NOT NULL,
    model_q_json            TEXT,
    model_q_identity_hash   TEXT,
    market_q_json           TEXT,
    market_q_basis          TEXT,
    market_q_depth_score    REAL,
    market_q_spread_score   REAL,
    market_q_projection_error REAL,
    market_q_book_hash      TEXT,
    market_center_native    REAL,
    market_center_status    TEXT NOT NULL,
    market_center_version   TEXT NOT NULL,
    sampling_reason         TEXT NOT NULL,
    state_changed           INTEGER NOT NULL,
    heartbeat_due           INTEGER NOT NULL,
    pre_veto_selected       INTEGER NOT NULL,
    selected_bin_id         TEXT,
    selected_side           TEXT,
    sampling_policy_version INTEGER NOT NULL,
    capture_seam            TEXT NOT NULL,
    schema_version          INTEGER NOT NULL
)
"""
# Deliberately NO append-only triggers -- this is a transient transport
# buffer, not evidence; rows are deleted once safely ingested into the
# canonical (append-only) trade-DB tables.

_COLUMNS = (
    "enqueued_at_utc", "state_id", "content_hash", "hash_version", "topology_hash",
    "complete_book", "canonical_payload", "payload_schema_version",
    "observation_id", "family_id", "city", "target_date", "temperature_metric",
    "decision_id", "receipt_hash", "source_manifest_json", "decision_time",
    "causal_snapshot_id", "predictive_identity_hash", "our_mu_native", "our_sigma_native",
    "measurement_unit", "model_q_json", "model_q_identity_hash", "market_q_json",
    "market_q_basis", "market_q_depth_score", "market_q_spread_score",
    "market_q_projection_error", "market_q_book_hash", "market_center_native",
    "market_center_status", "market_center_version", "sampling_reason",
    "state_changed", "heartbeat_due", "pre_veto_selected", "selected_bin_id",
    "selected_side", "sampling_policy_version", "capture_seam", "schema_version",
)


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)


def insert_outbox_row(conn: sqlite3.Connection, row: dict) -> int:
    """Insert one outbox row (dict keyed by column name, minus spool_seq).
    Returns the assigned spool_seq (lastrowid)."""
    placeholders = ", ".join("?" for _ in _COLUMNS)
    cur = conn.execute(
        f"INSERT INTO family_book_telemetry_outbox ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        tuple(row[c] for c in _COLUMNS),
    )
    return int(cur.lastrowid)


def fetch_batch(
    conn: sqlite3.Connection, *, after_seq: int = 0, limit: int = 500
) -> list[sqlite3.Row]:
    """Bounded batch, oldest-first, strictly after ``after_seq``."""
    conn.row_factory = sqlite3.Row
    return conn.execute(
        f"SELECT spool_seq, {', '.join(_COLUMNS)} FROM family_book_telemetry_outbox "
        "WHERE spool_seq > ? ORDER BY spool_seq LIMIT ?",
        (after_seq, limit),
    ).fetchall()


def delete_up_to(conn: sqlite3.Connection, max_seq: int) -> int:
    """Delete every row with spool_seq <= max_seq (the ingested batch).
    Returns the number of rows deleted."""
    cur = conn.execute(
        "DELETE FROM family_book_telemetry_outbox WHERE spool_seq <= ?", (max_seq,)
    )
    return cur.rowcount


def pending_stats(conn: sqlite3.Connection) -> dict:
    """count, approximate total bytes (canonical_payload + source_manifest_json,
    the two large columns), and oldest enqueued_at_utc among pending rows."""
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(canonical_payload) + LENGTH(source_manifest_json)), 0), "
        "MIN(enqueued_at_utc) FROM family_book_telemetry_outbox"
    ).fetchone()
    count, approx_bytes, oldest = row
    return {"pending_count": count, "pending_bytes_approx": approx_bytes, "oldest_enqueued_at_utc": oldest}


def latest_per_family(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """family_id -> (state_id, decision_time) for the newest PENDING (not yet
    ingested) row per family -- used to seed the sampling cache alongside the
    canonical DB's latest durable observation (Y4: max(canonical, pending))."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT family_id, state_id, decision_time
        FROM family_book_telemetry_outbox o
        WHERE spool_seq = (
            SELECT MAX(spool_seq) FROM family_book_telemetry_outbox
            WHERE family_id = o.family_id
        )
        """
    ).fetchall()
    return {r["family_id"]: (r["state_id"], r["decision_time"]) for r in rows}
