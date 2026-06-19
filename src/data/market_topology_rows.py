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
from typing import Any


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )
def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
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
        attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" in attached:
            exists = conn.execute(
                "SELECT 1 FROM forecasts.sqlite_master WHERE type='table' AND name='market_events'"
            ).fetchone()
            if exists is not None:
                return "forecasts.market_events"
    except Exception:
        pass
    if _table_exists(conn, "market_events"):
        return "market_events"
    return None
def _market_events_columns(conn: sqlite3.Connection, table_ref: str) -> set[str]:
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
        return {row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()}
    return _table_columns(conn, table_ref)
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
    # ANTIBODY (Fitz #4): detect a keying-broken sibling BEFORE building the
    # family. A separate count of NULL/empty-condition_id rows scoped to THIS
    # family (never the whole table) — additive, so when condition_id is clean
    # the count is 0 and the family construction below is byte-identical to
    # legacy. A non-zero count means a sibling lost its executable identity at
    # the producer (market ingest) → fail loud, naming the family, instead of
    # silently shrinking the MECE partition q/FDR are computed over.
    broken_siblings = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {table_ref}
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND COALESCE(condition_id, '') = ''
        """,
        (city, target_date, metric),
    ).fetchone()
    n_broken = int(broken_siblings[0]) if broken_siblings else 0
    if n_broken:
        raise FamilyKeyingError(
            f"market_events family city={city!r} target_date={target_date!r} "
            f"metric={metric!r} has {n_broken} sibling row(s) with a NULL/empty "
            f"condition_id — the bin lost its executable identity at the ingest "
            f"producer. Refusing to bind a silently-shrunk MECE family (Fitz #4 "
            f"keying antibody). Fix the market_events writer keying, do NOT drop "
            f"the sibling."
        )
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
          AND COALESCE(condition_id, '') != ''
        ORDER BY condition_id, {label_order}, {token_order}
        """,
        (city, target_date, metric),
    )
    names = [description[0] for description in cur.description]
    rows: list[dict[str, Any]] = []
    seen_conditions: set[str] = set()
    for row in cur.fetchall():
        item = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
        condition_id = str(item.get("condition_id") or "")
        if not condition_id or condition_id in seen_conditions:
            continue
        seen_conditions.add(condition_id)
        rows.append(item)
    return rows
