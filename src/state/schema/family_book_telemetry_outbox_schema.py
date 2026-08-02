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

CREATE_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS family_book_telemetry_meta (
    k TEXT PRIMARY KEY,
    v INTEGER NOT NULL
)
"""

# Round-6 Z2: the pending backlog is maintained here transactionally, in the
# SAME transaction as every insert/delete, so the capture path's admission
# check is an O(1) two-key lookup instead of a COUNT(*)+SUM(LENGTH(...))
# full-table aggregate whose cost grew with the very backlog it was meant to
# bound (capture got slower exactly when the spool was in trouble).
_META_PENDING_COUNT = "pending_count"
_META_PENDING_BYTES = "pending_bytes"

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

# Round-6 Z2: ``latest_per_family`` (restart bootstrap) runs a correlated
# MAX(spool_seq) per family; without this index that is a full scan per
# family, on the startup path, while a backlog is at its largest.
CREATE_FAMILY_SEQ_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_fb_outbox_family_seq
    ON family_book_telemetry_outbox (family_id, spool_seq DESC)
"""

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


def ensure_table(conn: sqlite3.Connection, *, max_page_count: int = 0) -> None:
    """Create the outbox and its pending-metadata sidecar, and arm the spool
    file's PHYSICAL ceilings (round-6 Z2).

    ``max_page_count`` makes the ceiling real rather than advisory: past it
    SQLite itself returns SQLITE_FULL, so a runaway spool fails its own writes
    instead of consuming the disk the live-money DB needs. ``journal_size_limit``
    bounds the WAL that a large burst would otherwise leave permanently grown.
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_META_TABLE_SQL)
    conn.execute(CREATE_FAMILY_SEQ_INDEX_SQL)
    conn.execute("PRAGMA journal_size_limit=67108864")  # 64 MiB
    if max_page_count > 0:
        conn.execute(f"PRAGMA max_page_count={int(max_page_count)}")


def _bump_meta(conn: sqlite3.Connection, *, d_count: int, d_bytes: int) -> None:
    """Adjust the pending counters inside the CALLER's transaction, so they
    commit or roll back atomically with the row change they describe.

    Upsert, not UPDATE: the counter rows come into existence with the first
    row they count. That removes any need for a startup backfill pass -- and
    with it the open-time ``commit()`` such a pass would require, which would
    write to the spool before the caller's own transaction discipline applies.
    """
    for key, delta in ((_META_PENDING_COUNT, d_count), (_META_PENDING_BYTES, d_bytes)):
        conn.execute(
            "INSERT INTO family_book_telemetry_meta (k, v) VALUES (?, MAX(0, ?)) "
            "ON CONFLICT(k) DO UPDATE SET v = MAX(0, v + ?)",
            (key, delta, delta),
        )


def insert_outbox_row(conn: sqlite3.Connection, row: dict) -> int:
    """Insert one outbox row (dict keyed by column name, minus spool_seq).
    Returns the assigned spool_seq (lastrowid)."""
    placeholders = ", ".join("?" for _ in _COLUMNS)
    cur = conn.execute(
        f"INSERT INTO family_book_telemetry_outbox ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        tuple(row[c] for c in _COLUMNS),
    )
    _bump_meta(
        conn,
        d_count=1,
        d_bytes=len(row["canonical_payload"]) + len(row["source_manifest_json"]),
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
    freed = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(canonical_payload) + LENGTH(source_manifest_json)), 0) "
        "FROM family_book_telemetry_outbox WHERE spool_seq <= ?",
        (max_seq,),
    ).fetchone()
    cur = conn.execute(
        "DELETE FROM family_book_telemetry_outbox WHERE spool_seq <= ?", (max_seq,)
    )
    _bump_meta(conn, d_count=-int(freed[0]), d_bytes=-int(freed[1]))
    return cur.rowcount


def pending_budget(conn: sqlite3.Connection) -> tuple[int, int]:
    """O(1) ``(pending_count, pending_bytes)`` for the capture-path admission
    check -- two primary-key lookups, cost independent of backlog size.
    Raises if the metadata table cannot be READ -- the caller must then fail
    closed rather than admit writes with an unknown backlog (round-6 Z2). An
    ABSENT key is not unknown: the counters are created by the first row they
    count, so no key means no row was ever inserted, i.e. zero pending."""
    rows = dict(
        conn.execute(
            "SELECT k, v FROM family_book_telemetry_meta WHERE k IN (?, ?)",
            (_META_PENDING_COUNT, _META_PENDING_BYTES),
        ).fetchall()
    )
    return int(rows.get(_META_PENDING_COUNT, 0)), int(rows.get(_META_PENDING_BYTES, 0))


def pending_stats(conn: sqlite3.Connection) -> dict:
    """Observability snapshot: pending rows, pending bytes, and oldest pending
    ``enqueued_at_utc``. Counts/bytes come from the O(1) metadata; the oldest
    timestamp is a single indexed MIN. Metrics path only -- the capture path
    calls ``pending_budget`` instead."""
    count, byts = pending_budget(conn)
    oldest = conn.execute(
        "SELECT MIN(enqueued_at_utc) FROM family_book_telemetry_outbox"
    ).fetchone()[0]
    return {"pending_count": count, "pending_bytes_approx": byts, "oldest_enqueued_at_utc": oldest}


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
