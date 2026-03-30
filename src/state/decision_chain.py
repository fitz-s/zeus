"""Decision Chain: every cycle records what happened AND why things didn't happen.

Blueprint v2 §3: NoTradeCase is not optional. When Zeus doesn't trade, it must
record WHY with the same rigor as when it does trade.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NoTradeCase:
    """Records why a trade was NOT made. Blueprint v2 §3."""
    decision_id: str
    city: str
    target_date: str
    range_label: str
    direction: str
    rejection_stage: str  # MARKET_FILTER | SIGNAL_QUALITY | EDGE_INSUFFICIENT |
                          # FDR_FILTERED | RISK_REJECTED | SIZING_TOO_SMALL |
                          # EXECUTION_FAILED | ANTI_CHURN
    rejection_reasons: list[str] = field(default_factory=list)
    best_edge: float = 0.0
    model_prob: float = 0.0
    market_price: float = 0.0
    timestamp: str = ""


@dataclass
class MonitorResult:
    """Per-position per-cycle exit evaluation record."""
    position_id: str
    fresh_prob: float
    fresh_edge: float
    should_exit: bool
    exit_reason: str = ""
    neg_edge_count: int = 0


@dataclass
class CycleArtifact:
    """One per cycle. Links all decisions. Blueprint v2 §3."""
    mode: str
    started_at: str
    completed_at: str = ""
    skipped_reason: str = ""
    trade_cases: list[dict] = field(default_factory=list)
    no_trade_cases: list[NoTradeCase] = field(default_factory=list)
    monitor_results: list[MonitorResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def add_no_trade(self, ntc: NoTradeCase):
        self.no_trade_cases.append(ntc)

    def add_monitor_result(self, mr: MonitorResult):
        self.monitor_results.append(mr)

    def add_trade(self, trade_info: dict):
        self.trade_cases.append(trade_info)


def store_artifact(conn, artifact: CycleArtifact) -> None:
    """Store cycle artifact to decision_log table."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (
        artifact.mode, artifact.started_at, artifact.completed_at,
        json.dumps(asdict(artifact), default=str), now,
    ))
    conn.commit()


def query_no_trade_cases(conn, city: str = None, hours: int = 24) -> list[dict]:
    """Query recent NoTradeCase entries for diagnostics."""
    cutoff = datetime.now(timezone.utc).isoformat()
    rows = conn.execute("""
        SELECT artifact_json FROM decision_log
        WHERE timestamp > datetime('now', ?)
        ORDER BY timestamp DESC LIMIT 100
    """, (f"-{hours} hours",)).fetchall()

    results = []
    for r in rows:
        artifact = json.loads(r["artifact_json"])
        for ntc in artifact.get("no_trade_cases", []):
            if city is None or ntc.get("city") == city:
                results.append(ntc)
    return results
