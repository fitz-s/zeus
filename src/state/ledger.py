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

    conn.executescript(load_architecture_kernel_sql())
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
    """Batch canonical event append with a single final projection update."""
    assert_canonical_transaction_schema(conn)
    require_payload_fields(
        projection, CANONICAL_POSITION_CURRENT_COLUMNS, label="projection"
    )
    for idx, event in enumerate(events, 1):
        require_payload_fields(
            event, CANONICAL_POSITION_EVENT_COLUMNS, label=f"event[{idx}]"
        )
    validate_event_projection_batch(events, projection)
    with conn:
        for event in events:
            conn.execute(
                f"""
                INSERT INTO position_events ({", ".join(CANONICAL_POSITION_EVENT_COLUMNS)})
                VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_EVENT_COLUMNS))})
                """,
                ordered_values(event, CANONICAL_POSITION_EVENT_COLUMNS),
            )
        upsert_position_current(conn, projection)
