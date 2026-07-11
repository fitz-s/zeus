# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/architecture/system_decomposition_plan.md §4.1 / §7 I1 / criterion 3
#   (failure-domain isolation): the executable-substrate producer (P2) reads market_events
#   topology but must NOT import the trading lane. This module relocates the lane-neutral
#   topology-row read cluster (formerly private to src/engine/event_reactor_adapter.py) so
#   BOTH the order runtime's reactor (P1) and the lifted substrate observer (P2) import it
#   from a shared, trading-lane-free home. Pure read helpers: zero engine/execution state.
"""Lane-neutral market_events topology-row reader (shared by P1 reactor and P2 observer).

Extracted VERBATIM from src/engine/event_reactor_adapter.py (the cluster
_event_family_market_topology_rows + FamilyKeyingError + the market_events table/column
shape helpers) so the P2 substrate-observer process can read family topology WITHOUT
importing the trading lane (failure-domain isolation, system_decomposition_plan criterion
3). The reactor adapter now imports these names from here (re-export), so P1 behavior is
byte-identical; only the definition site moved.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _SchemaReadState:
    conn: sqlite3.Connection
    database_names: frozenset[str] | None = None
    table_exists: dict[str, bool] = field(default_factory=dict)
    table_columns: dict[str, frozenset[str]] = field(default_factory=dict)


_SCHEMA_READ_STATES: ContextVar[tuple[_SchemaReadState, ...]] = ContextVar(
    "market_topology_schema_read_states",
    default=(),
)


def _schema_read_state(conn: sqlite3.Connection) -> _SchemaReadState | None:
    return next(
        (state for state in _SCHEMA_READ_STATES.get() if state.conn is conn),
        None,
    )


def prime_frozen_schema_reads(
    connections: Iterable[sqlite3.Connection],
) -> Callable[[], None]:
    """Cache schema metadata only for the lifetime of owned read transactions."""

    states: list[_SchemaReadState] = []
    seen: set[int] = set()
    for conn in connections:
        identity = id(conn)
        if (
            identity in seen
            or not isinstance(conn, sqlite3.Connection)
            or not conn.in_transaction
        ):
            continue
        seen.add(identity)
        states.append(_SchemaReadState(conn=conn))
    if not states:
        return lambda: None
    token = _SCHEMA_READ_STATES.set(tuple(states))
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        _SCHEMA_READ_STATES.reset(token)

    return release


def _database_names(conn: sqlite3.Connection) -> frozenset[str]:
    state = _schema_read_state(conn)
    if state is not None and state.database_names is not None:
        return state.database_names
    names = frozenset(str(row[1]) for row in conn.execute("PRAGMA database_list"))
    if state is not None:
        state.database_names = names
    return names


def _table_ref_exists(conn: sqlite3.Connection, table_ref: str) -> bool:
    state = _schema_read_state(conn)
    if state is not None and table_ref in state.table_exists:
        return state.table_exists[table_ref]
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
        exists = (
            conn.execute(
                f"SELECT 1 FROM {schema}.sqlite_master "
                "WHERE type='table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )
    else:
        exists = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                (table_ref,),
            ).fetchone()
            is not None
        )
    if state is not None:
        state.table_exists[table_ref] = exists
    return exists


def _table_ref_columns(conn: sqlite3.Connection, table_ref: str) -> set[str]:
    state = _schema_read_state(conn)
    if state is not None and table_ref in state.table_columns:
        return set(state.table_columns[table_ref])
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
        rows = conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()
    else:
        rows = conn.execute(f"PRAGMA table_info({table_ref})").fetchall()
    columns = frozenset(str(row[1]) for row in rows)
    if state is not None:
        state.table_columns[table_ref] = columns
    return set(columns)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return _table_ref_exists(conn, table_name)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return _table_ref_columns(conn, table_name)


class FamilyKeyingError(ValueError):
    """A market_events sibling of the bound family carries no resolved condition_id.

    Fitz Constraint #4 antibody (silent keying loss). The family universe for a
    (city, target_date, metric) is the COMPLETE MECE partition in market_events;
    q/FDR are computed over that full partition. A sibling row whose condition_id
    is NULL/empty cannot be keyed to an executable identity. Silently filtering it
    out (the legacy ``COALESCE(condition_id,'') != ''`` behavior) shrinks the
    family with NO diagnosable signal — which either kills every sibling later as
    ``FDR_FAMILY_TOPOLOGY_INCOMPLETE`` or renormalizes q over a subset (~1.2x
    inflation at 3/11 missing). Both are catastrophic and invisible.

    Raising here converts that silent loss into a LOUD, named failure that points
    at the exact family. It is byte-identical to legacy behavior when condition_id
    is clean (the live invariant: 0/21018 market_events rows NULL today), so it
    fabricates no trade and changes no current decision — it only makes a FUTURE
    keying regression impossible to swallow silently at the producer->consumer seam.
    """
def _market_events_table_ref(conn: sqlite3.Connection) -> str | None:
    try:
        if "forecasts" in _database_names(conn):
            if _table_ref_exists(conn, "forecasts.market_events"):
                return "forecasts.market_events"
    except Exception:
        pass
    if _table_exists(conn, "market_events"):
        return "market_events"
    return None
def _market_events_columns(conn: sqlite3.Connection, table_ref: str) -> set[str]:
    return _table_ref_columns(conn, table_ref)
def _optional_column_expr(columns: set[str], column: str) -> str:
    if column in columns:
        return column
    return f"NULL AS {column}"
def _event_family_market_topology_rows(
    conn: sqlite3.Connection,
    payload: dict[str, object],
) -> list[dict[str, Any]]:
    """Return canonical market topology rows for the event city/date/metric.

    Forecast and Day0 events are family facts, not child-token facts. They may
    legitimately lack condition/token ids, but they still must bind through the
    forecast-owned market topology table before executable snapshots can satisfy
    the quote gate. The family universe comes from market_events, not from the
    subset of fresh executable snapshots, so a missing sibling cannot shrink the
    FDR denominator.

    Fail-loud keying antibody (Fitz #4): if ANY market_events row matching the
    family (city, target_date, metric) has a NULL/empty condition_id, the family
    is keying-broken and ``FamilyKeyingError`` is raised rather than the broken
    sibling being silently dropped from the MECE partition. See
    ``FamilyKeyingError`` for why a silent drop is catastrophic and invisible.
    """

    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    metric = str(payload.get("metric") or payload.get("temperature_metric") or "").strip()
    if not (city and target_date and metric):
        return []
    table_ref = _market_events_table_ref(conn)
    if table_ref is None:
        return []
    columns = _market_events_columns(conn, table_ref)
    required = {"city", "target_date", "temperature_metric", "condition_id"}
    if not required.issubset(columns):
        return []
    select_fields = [
        "condition_id",
        _optional_column_expr(columns, "market_slug"),
        _optional_column_expr(columns, "range_label"),
        _optional_column_expr(columns, "range_low"),
        _optional_column_expr(columns, "range_high"),
        _optional_column_expr(columns, "outcome"),
        _optional_column_expr(columns, "token_id"),
        _optional_column_expr(columns, "discovered_at"),
        _optional_column_expr(columns, "captured_at"),
        _optional_column_expr(columns, "available_at"),
        _optional_column_expr(columns, "gamma_updated_at"),
        _optional_column_expr(columns, "created_at"),
        _optional_column_expr(columns, "received_at"),
        _optional_column_expr(columns, "scanned_at"),
        _optional_column_expr(columns, "persisted_at"),
        _optional_column_expr(columns, "updated_at"),
    ]
    label_order = "COALESCE(range_label, outcome, '')" if {"range_label", "outcome"}.issubset(columns) else (
        "COALESCE(range_label, '')" if "range_label" in columns else ("COALESCE(outcome, '')" if "outcome" in columns else "''")
    )
    token_order = "COALESCE(token_id, '')" if "token_id" in columns else "''"
    cur = conn.execute(
        f"""
        SELECT {', '.join(select_fields)}
        FROM {table_ref}
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
        ORDER BY condition_id, {label_order}, {token_order}
        """,
        (city, target_date, metric),
    )
    names = [description[0] for description in cur.description]
    items = [
        {name: row[name] for name in names}
        if isinstance(row, sqlite3.Row)
        else dict(zip(names, row))
        for row in cur.fetchall()
    ]
    n_broken = sum(not str(item.get("condition_id") or "") for item in items)
    if n_broken:
        raise FamilyKeyingError(
            f"market_events family city={city!r} target_date={target_date!r} "
            f"metric={metric!r} has {n_broken} sibling row(s) with a NULL/empty "
            f"condition_id — the bin lost its executable identity at the ingest "
            f"producer. Refusing to bind a silently-shrunk MECE family (Fitz #4 "
            f"keying antibody). Fix the market_events writer keying, do NOT drop "
            f"the sibling."
        )
    condition_ids = [str(item["condition_id"]) for item in items]
    if len(condition_ids) != len(set(condition_ids)):
        raise FamilyKeyingError(
            f"market_events family city={city!r} target_date={target_date!r} "
            f"metric={metric!r} has duplicate condition_id rows"
        )
    rows: list[dict[str, Any]] = []
    seen_conditions: set[str] = set()
    for item in items:
        condition_id = str(item.get("condition_id") or "")
        if not condition_id or condition_id in seen_conditions:
            continue
        seen_conditions.add(condition_id)
        rows.append(item)
    return rows
