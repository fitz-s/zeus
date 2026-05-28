"""EDLI Day0 boundary report helpers."""

from __future__ import annotations

import sqlite3


def day0_boundary_report(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "day0_events": conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchone()[0],
        "day0_regret_rows": conn.execute(
            "SELECT COUNT(*) FROM no_trade_regret_events WHERE rejection_stage IN ('SOURCE_TRUTH','INFERENCE')"
        ).fetchone()[0],
    }
