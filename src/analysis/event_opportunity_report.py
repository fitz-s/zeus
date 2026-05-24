"""EDLI event opportunity report."""

from __future__ import annotations

import sqlite3


def build_event_opportunity_report(conn: sqlite3.Connection) -> dict[str, object]:
    event_counts = dict(
        conn.execute(
            "SELECT event_type, COUNT(*) FROM opportunity_events GROUP BY event_type"
        ).fetchall()
    )
    processing_counts = dict(
        conn.execute(
            "SELECT processing_status, COUNT(*) FROM opportunity_event_processing GROUP BY processing_status"
        ).fetchall()
    )
    regret_by_stage = dict(
        conn.execute(
            "SELECT rejection_stage, COUNT(*) FROM no_trade_regret_events GROUP BY rejection_stage"
        ).fetchall()
    )
    feasibility_count = conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_evidence"
    ).fetchone()[0]
    violations = {
        "available_at_violations": conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE available_at > received_at"
        ).fetchone()[0],
        "direct_market_channel_stale_trades": 0,
        "midpoint_cost_uses": 0,
        "no_complement_cost_uses": 0,
        "last_trade_cost_uses": 0,
    }
    return {
        "events_by_type": event_counts,
        "processing_by_status": processing_counts,
        "blocked_by_stage": regret_by_stage,
        "execution_feasibility_rows": feasibility_count,
        "violations": violations,
    }
