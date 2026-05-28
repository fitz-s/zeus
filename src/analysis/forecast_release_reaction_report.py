"""EDLI forecast release reaction report helpers."""

from __future__ import annotations

import sqlite3


def forecast_release_reaction_report(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "forecast_events": conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='FORECAST_SNAPSHOT_READY'"
        ).fetchone()[0],
        "forecast_completeness_blocks": conn.execute(
            "SELECT COUNT(*) FROM no_trade_regret_events WHERE rejection_stage='FORECAST_COMPLETENESS'"
        ).fetchone()[0],
    }
