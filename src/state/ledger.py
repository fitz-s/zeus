from __future__ import annotations

from pathlib import Path
import sqlite3

from src.state.projection import (
    CANONICAL_POSITION_CURRENT_COLUMNS,
    ordered_values,
    require_payload_fields,
    table_columns,
    upsert_position_current,
    validate_event_projection_batch,
    validate_event_projection_pair,
)


ARCHITECTURE_KERNEL_SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "architecture/2026_04_02_architecture_kernel.sql"
)

CANONICAL_POSITION_EVENT_COLUMNS = (
    "event_id",
    "position_id",
    "event_version",
    "sequence_no",
    "event_type",
    "occurred_at",
    "phase_before",
    "phase_after",
    "strategy_key",
    "decision_id",
    "snapshot_id",
    "order_id",
    "command_id",
    "caused_by",
    "idempotency_key",
    "venue_status",
    "source_module",
    "payload_json",
)

TOKEN_SUPPRESSION_COLUMNS = (
    "token_id",
    "condition_id",
    "suppression_reason",
    "source_module",
    "created_at",
    "updated_at",
    "evidence_json",
)


def load_architecture_kernel_sql() -> str:
    return ARCHITECTURE_KERNEL_SQL_PATH.read_text()


def assert_canonical_transaction_schema(conn: sqlite3.Connection) -> None:
    event_columns = table_columns(conn, "position_events")
    current_columns = table_columns(conn, "position_current")
    if not event_columns or not current_columns:
        raise RuntimeError(
            "canonical transaction boundary requires migrated position_events and position_current tables"
        )
    if not set(CANONICAL_POSITION_EVENT_COLUMNS).issubset(event_columns):
        raise RuntimeError("canonical position_events schema not installed")
    if not set(CANONICAL_POSITION_CURRENT_COLUMNS).issubset(current_columns):
        raise RuntimeError("canonical position_current schema not installed")


def _ensure_token_suppression_reason_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'token_suppression'"
    ).fetchone()
    create_sql = str(row[0] if row and row[0] else "")
    if not create_sql or "chain_only_quarantined" in create_sql:
        return

    with conn:
        conn.execute("ALTER TABLE token_suppression RENAME TO token_suppression_old")
        conn.execute(
            """
            CREATE TABLE token_suppression (
                token_id TEXT PRIMARY KEY,
                condition_id TEXT,
                suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
                    'operator_quarantine_clear',
                    'chain_only_quarantined',
                    'settled_position'
                )),
                source_module TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        old_columns = table_columns(conn, "token_suppression_old")
        shared_columns = [column for column in TOKEN_SUPPRESSION_COLUMNS if column in old_columns]
        if shared_columns:
            conn.execute(
                f"""
                INSERT INTO token_suppression ({", ".join(shared_columns)})
                SELECT {", ".join(shared_columns)}
                FROM token_suppression_old
                """
            )
        conn.execute("DROP TABLE token_suppression_old")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_token_suppression_reason
                ON token_suppression(suppression_reason, updated_at)
            """
        )


def apply_architecture_kernel_schema(conn: sqlite3.Connection) -> None:
    """Apply the canonical architecture schema only when no legacy collision exists."""
    event_columns = table_columns(conn, "position_events")
    if event_columns and not set(CANONICAL_POSITION_EVENT_COLUMNS).issubset(
        event_columns
    ):
        raise RuntimeError(
            "legacy position_events table blocks canonical schema bootstrap; "
            "freeze a dedicated migration packet before changing live/runtime schema"
        )

    # B070 legacy collision guard: kernel SQL declares `control_overrides`
    # as a VIEW backed by `control_overrides_history`. On a legacy DB where
    # `control_overrides` already exists as a TABLE, `CREATE VIEW IF NOT
    # EXISTS` silently no-ops (SQLite treats name as already-defined across
    # both types). Result: writes go to the new history table, reads still
    # hit the stale legacy table — silent split-brain. Fail-fast and point
    # at the migration script.
    co_row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name='control_overrides'"
    ).fetchone()
    if co_row is not None and str(co_row[0] if isinstance(co_row, tuple) else co_row["type"]) == "table":
        raise RuntimeError(
            "legacy control_overrides TABLE blocks B070 event-sourced VIEW "
            "bootstrap; run scripts/migrate_b070_control_overrides_to_history.py "
            "--apply with ZEUS_DESTRUCTIVE_CONFIRMED=1 before restarting the daemon"
        )

    conn.executescript(load_architecture_kernel_sql())
    _ensure_token_suppression_reason_schema(conn)
    # Legacy-DB column reconciliation: `CREATE TABLE IF NOT EXISTS` in the
    # kernel SQL no-ops when position_current exists from a pre-kernel
    # schema. Backfill every canonical column that the legacy table is
    # missing. Plain TEXT affinity matches the existing 3-token-column
    # pattern and satisfies assert_canonical_transaction_schema's set-
    # membership check below. Runtime writers go through
    # require_payload_fields and always supply every canonical field, so
    # the absence of NOT NULL / CHECK constraints on ALTER-migrated
    # columns does not affect write-path correctness.
    existing_columns = table_columns(conn, "position_current")
    for column in CANONICAL_POSITION_CURRENT_COLUMNS:
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE position_current ADD COLUMN {column} TEXT;")
    assert_canonical_transaction_schema(conn)


def append_event_and_project(
    conn: sqlite3.Connection, event: dict, projection: dict
) -> None:
    """Canonical transaction-boundary helper for target event + projection writes."""
    assert_canonical_transaction_schema(conn)
    require_payload_fields(event, CANONICAL_POSITION_EVENT_COLUMNS, label="event")
    require_payload_fields(
        projection, CANONICAL_POSITION_CURRENT_COLUMNS, label="projection"
    )
    validate_event_projection_pair(event, projection)
    with conn:
        conn.execute(
            f"""
            INSERT INTO position_events ({", ".join(CANONICAL_POSITION_EVENT_COLUMNS)})
            VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_EVENT_COLUMNS))})
            """,
            ordered_values(event, CANONICAL_POSITION_EVENT_COLUMNS),
        )
        upsert_position_current(conn, projection)


def append_many_and_project(
    conn: sqlite3.Connection, events: list[dict], projection: dict
) -> None:
    """Batch canonical event append with a single final projection update.

    Atomicity (DR-33-B, 2026-04-24): uses an explicit SAVEPOINT (not
    `with conn:`) so callers that already hold an outer SAVEPOINT can
    invoke this function without the Python `with conn:` idiom silently
    releasing their outer SAVEPOINT. Per memory rule L30 (`with conn:`
    inside SAVEPOINT atomicity collision): Python sqlite3's `with conn:`
    commits + releases the innermost active SAVEPOINT on clean exit,
    which — when this function was invoked inside a caller's SAVEPOINT —
    broke the caller's ROLLBACK path on subsequent errors. Explicit
    SAVEPOINT nesting avoids the collision: nested SAVEPOINTs are
    released independently in SQLite, so the caller's outer SAVEPOINT
    survives a clean release of this function's inner SAVEPOINT.

    Torn-state closure: the pre-DR-33-B `cycle_runtime.py:1246-1252`
    pattern explicitly placed `_dual_write_canonical_entry_if_available`
    OUTSIDE the `sp_candidate_*` SAVEPOINT guard because the `with conn:`
    in this function would have released sp_candidate_* on commit. With
    DR-33-B, the dual-write can run INSIDE sp_candidate_* — if the
    dual-write fails, ROLLBACK TO sp_candidate_* correctly rolls back
    both the trade_decisions writes and the position_events writes.

    Callers outside any SAVEPOINT (top-level): SQLite opens an implicit
    transaction at the first SAVEPOINT, and clean RELEASE at the
    outermost level commits. Existing top-level callers continue to
    work unchanged.
    """
    import secrets

    assert_canonical_transaction_schema(conn)
    require_payload_fields(
        projection, CANONICAL_POSITION_CURRENT_COLUMNS, label="projection"
    )
    for idx, event in enumerate(events, 1):
        require_payload_fields(
            event, CANONICAL_POSITION_EVENT_COLUMNS, label=f"event[{idx}]"
        )
    validate_event_projection_batch(events, projection)
    sp_name = f"sp_ampp_{secrets.token_hex(6)}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        for event in events:
            conn.execute(
                f"""
                INSERT INTO position_events ({", ".join(CANONICAL_POSITION_EVENT_COLUMNS)})
                VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_EVENT_COLUMNS))})
                """,
                ordered_values(event, CANONICAL_POSITION_EVENT_COLUMNS),
            )
        upsert_position_current(conn, projection)
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise
