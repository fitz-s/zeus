"""EDLI orderbook execution feasibility report helpers."""

from __future__ import annotations

import sqlite3


def orderbook_execution_feasibility_report(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "quote_rows": conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0],
        "fillable_rows": conn.execute(
            "SELECT COUNT(*) FROM execution_feasibility_evidence WHERE would_have_edge_after_fee=1"
        ).fetchone()[0],
    }
