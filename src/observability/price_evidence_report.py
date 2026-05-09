"""Derived operator visibility for price/orderbook evidence modes."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

SCHEMA_VERSION = 1
AUTHORITY = "derived_operator_visibility"

_FULL_LINKAGE_SOURCES = (
    "CLOB_WS_MARKET",
    "CLOB_BEST_BID_ASK",
    "CLOB_ORDERBOOK",
)
_MARKET_PRICE_HISTORY_REQUIRED_COLUMNS = frozenset({
    "market_price_linkage",
    "source",
    "best_bid",
    "best_ask",
    "raw_orderbook_hash",
    "snapshot_id",
    "condition_id",
})
_SNAPSHOT_REQUIRED_COLUMNS = frozenset({"snapshot_id"})
_SNAPSHOT_ORDERBOOK_COLUMNS = frozenset({
    "orderbook_top_bid",
    "orderbook_top_ask",
    "raw_orderbook_hash",
})


def _empty_report(status: str, *, source_errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "authority": AUTHORITY,
        "counts": {
            "market_price_history_rows": None,
            "token_price_log_rows": None,
            "executable_market_snapshots_rows": None,
            "executable_orderbook_snapshot_rows": None,
            "invalid_full_linkage_rows": None,
            "full_linkage_without_snapshot_rows": None,
        },
        "modes": {
            "price_only": {"row_count": 0, "token_count": 0, "source_counts": {}},
            "full_linkage_rows": {"row_count": 0, "token_count": 0, "source_counts": {}},
            "executable_snapshot_backed": {"row_count": 0, "token_count": 0, "source_counts": {}},
        },
        "blockers": [],
        "source_errors": source_errors or [],
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _count(conn: sqlite3.Connection, table: str, where_sql: str | None = None) -> int:
    query = f"SELECT COUNT(*) FROM {table}"
    if where_sql:
        query += f" WHERE {where_sql}"
    return int(conn.execute(query).fetchone()[0] or 0)


def _distinct_count(conn: sqlite3.Connection, table: str, column: str, where_sql: str | None = None) -> int:
    query = f"SELECT COUNT(DISTINCT {column}) FROM {table}"
    if where_sql:
        query += f" WHERE {where_sql}"
    return int(conn.execute(query).fetchone()[0] or 0)


def _source_counts(conn: sqlite3.Connection, where_sql: str) -> dict[str, int]:
    rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(source, ''), 'unknown') AS source, COUNT(*) AS count
          FROM market_price_history
         WHERE {where_sql}
         GROUP BY COALESCE(NULLIF(source, ''), 'unknown')
         ORDER BY source
        """
    ).fetchall()
    return {str(row[0]): int(row[1] or 0) for row in rows}


def _in_values(values: Iterable[str]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _full_linkage_where(alias: str = "market_price_history") -> str:
    prefix = f"{alias}." if alias else ""
    sources_sql = _in_values(_FULL_LINKAGE_SOURCES)
    return (
        f"LOWER(COALESCE({prefix}market_price_linkage, '')) = 'full' "
        f"AND UPPER(COALESCE({prefix}source, '')) IN ({sources_sql}) "
        f"AND {prefix}best_bid IS NOT NULL "
        f"AND {prefix}best_ask IS NOT NULL "
        f"AND {prefix}best_bid >= 0.0 AND {prefix}best_bid <= 1.0 "
        f"AND {prefix}best_ask >= 0.0 AND {prefix}best_ask <= 1.0 "
        f"AND {prefix}best_bid <= {prefix}best_ask "
        f"AND COALESCE({prefix}raw_orderbook_hash, '') <> '' "
        f"AND COALESCE({prefix}snapshot_id, '') <> '' "
        f"AND COALESCE({prefix}condition_id, '') <> ''"
    )


def _snapshot_orderbook_where() -> str:
    return (
        "orderbook_top_bid IS NOT NULL "
        "AND orderbook_top_ask IS NOT NULL "
        "AND orderbook_top_bid >= 0.0 AND orderbook_top_bid <= 1.0 "
        "AND orderbook_top_ask >= 0.0 AND orderbook_top_ask <= 1.0 "
        "AND orderbook_top_bid <= orderbook_top_ask "
        "AND COALESCE(raw_orderbook_hash, '') <> ''"
    )


def _snapshot_backed_where() -> str:
    return (
        _full_linkage_where("mph")
        + " AND EXISTS ("
        + "SELECT 1 FROM executable_market_snapshots ems "
        + "WHERE ems.snapshot_id = mph.snapshot_id)"
    )


def build_price_evidence_report(conn: sqlite3.Connection | None) -> dict[str, Any]:
    """Report price-only and executable-snapshot-backed evidence modes.

    The result is derived operator visibility. It reads already-persisted price
    and executable snapshot evidence; it is not replay, execution, or trading
    authority.
    """
    if conn is None:
        return _empty_report(
            "query_error",
            source_errors=[{"source": "connection", "error": "missing_connection"}],
        )
    if not hasattr(conn, "execute"):
        return _empty_report(
            "query_error",
            source_errors=[{"source": "connection", "error": "invalid_connection"}],
        )

    source_errors: list[dict[str, Any]] = []
    for table in ("market_price_history", "executable_market_snapshots"):
        if not _table_exists(conn, table):
            source_errors.append({"source": table, "error": "missing_table"})
    if source_errors:
        return _empty_report("query_error", source_errors=source_errors)

    price_columns = _table_columns(conn, "market_price_history")
    snapshot_columns = _table_columns(conn, "executable_market_snapshots")
    missing_price_columns = sorted(_MARKET_PRICE_HISTORY_REQUIRED_COLUMNS - price_columns)
    missing_snapshot_columns = sorted(_SNAPSHOT_REQUIRED_COLUMNS - snapshot_columns)
    if missing_price_columns:
        source_errors.append({
            "source": "market_price_history",
            "error": "missing_columns",
            "columns": missing_price_columns,
        })
    if missing_snapshot_columns:
        source_errors.append({
            "source": "executable_market_snapshots",
            "error": "missing_columns",
            "columns": missing_snapshot_columns,
        })
    if source_errors:
        return _empty_report("query_error", source_errors=source_errors)

    counts: dict[str, int | None] = {
        "market_price_history_rows": _count(conn, "market_price_history"),
        "token_price_log_rows": _count(conn, "token_price_log") if _table_exists(conn, "token_price_log") else None,
        "executable_market_snapshots_rows": _count(conn, "executable_market_snapshots"),
        "executable_orderbook_snapshot_rows": None,
        "invalid_full_linkage_rows": None,
        "full_linkage_without_snapshot_rows": None,
    }

    snapshot_orderbook_columns_missing = sorted(_SNAPSHOT_ORDERBOOK_COLUMNS - snapshot_columns)
    if snapshot_orderbook_columns_missing:
        source_errors.append({
            "source": "executable_market_snapshots",
            "error": "missing_orderbook_columns",
            "columns": snapshot_orderbook_columns_missing,
        })
    else:
        counts["executable_orderbook_snapshot_rows"] = _count(
            conn,
            "executable_market_snapshots",
            _snapshot_orderbook_where(),
        )

    price_only_where = "LOWER(COALESCE(market_price_linkage, '')) = 'price_only'"
    full_linkage_where = _full_linkage_where()
    snapshot_backed_where = _snapshot_backed_where()
    raw_full_where = "LOWER(COALESCE(market_price_linkage, '')) = 'full'"

    price_only_count = _count(conn, "market_price_history", price_only_where)
    full_linkage_count = _count(conn, "market_price_history", full_linkage_where)
    raw_full_count = _count(conn, "market_price_history", raw_full_where)
    executable_snapshot_backed_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM market_price_history mph WHERE {snapshot_backed_where}"
        ).fetchone()[0]
        or 0
    )
    full_linkage_without_snapshot = int(
        conn.execute(
            f"SELECT COUNT(*) FROM market_price_history mph WHERE {_full_linkage_where('mph')} "
            "AND NOT EXISTS ("
            "SELECT 1 FROM executable_market_snapshots ems "
            "WHERE ems.snapshot_id = mph.snapshot_id)"
        ).fetchone()[0]
        or 0
    )
    invalid_full_linkage_count = max(0, raw_full_count - full_linkage_count)
    counts["invalid_full_linkage_rows"] = invalid_full_linkage_count
    counts["full_linkage_without_snapshot_rows"] = full_linkage_without_snapshot

    modes = {
        "price_only": {
            "row_count": price_only_count,
            "token_count": _distinct_count(conn, "market_price_history", "token_id", price_only_where),
            "source_counts": _source_counts(conn, price_only_where),
        },
        "full_linkage_rows": {
            "row_count": full_linkage_count,
            "token_count": _distinct_count(conn, "market_price_history", "token_id", full_linkage_where),
            "source_counts": _source_counts(conn, full_linkage_where),
        },
        "executable_snapshot_backed": {
            "row_count": executable_snapshot_backed_count,
            "token_count": int(
                conn.execute(
                    f"SELECT COUNT(DISTINCT mph.token_id) FROM market_price_history mph WHERE {snapshot_backed_where}"
                ).fetchone()[0]
                or 0
            ),
            "source_counts": {
                str(row[0]): int(row[1] or 0)
                for row in conn.execute(
                    f"""
                    SELECT COALESCE(NULLIF(mph.source, ''), 'unknown') AS source, COUNT(*) AS count
                      FROM market_price_history mph
                     WHERE {snapshot_backed_where}
                     GROUP BY COALESCE(NULLIF(mph.source, ''), 'unknown')
                     ORDER BY source
                    """
                ).fetchall()
            },
        },
    }

    blockers: list[str] = []
    if counts["market_price_history_rows"] and executable_snapshot_backed_count <= 0:
        blockers.append("no_executable_snapshot_backed_price_rows")
    if full_linkage_without_snapshot > 0:
        blockers.append("full_linkage_without_executable_snapshot")
    if invalid_full_linkage_count > 0:
        blockers.append("invalid_full_linkage_rows")
    if snapshot_orderbook_columns_missing:
        blockers.append("snapshot_orderbook_columns_unavailable")

    status = "observed"
    if source_errors:
        status = "partial"
    elif (
        counts["market_price_history_rows"] == 0
        and counts["executable_market_snapshots_rows"] == 0
        and (counts["token_price_log_rows"] in (None, 0))
    ):
        status = "certified_empty"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "authority": AUTHORITY,
        "counts": counts,
        "modes": modes,
        "blockers": blockers,
        "source_errors": source_errors,
    }
