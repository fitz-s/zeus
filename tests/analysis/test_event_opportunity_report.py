# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §15 reports and observability contract.
from __future__ import annotations

import sqlite3

from src.analysis.event_opportunity_report import build_event_opportunity_report
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretEvent, NoTradeRegretLedger


def test_event_opportunity_report_counts_regret_and_violations():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    NoTradeRegretLedger(conn).insert_idempotent(
        NoTradeRegretEvent("event-1", "FDR", "FDR_REJECTED", "FDR_REJECTED")
    )
    report = build_event_opportunity_report(conn)
    assert report["blocked_by_stage"] == {"FDR": 1}
    assert report["violations"]["midpoint_cost_uses"] == 0
    assert report["violations"]["no_complement_cost_uses"] == 0
