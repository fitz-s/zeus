"""RiskGuard: independent monitoring process. Spec §7.

Runs as a SEPARATE process with its own 60-second tick.
Reads from zeus.db, writes to risk_state.db.
Graduated response: GREEN → YELLOW → ORANGE → RED.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import settings, STATE_DIR
from src.riskguard.metrics import (
    brier_score, directional_accuracy, win_rate,
    evaluate_brier, evaluate_win_rate,
)
from src.riskguard.risk_level import RiskLevel, overall_level
from src.state.db import get_connection, RISK_DB_PATH

logger = logging.getLogger(__name__)


def init_risk_db(conn: sqlite3.Connection) -> None:
    """Create risk_state tables."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS risk_state (
            id INTEGER PRIMARY KEY,
            level TEXT NOT NULL,
            brier REAL,
            accuracy REAL,
            win_rate REAL,
            details_json TEXT,
            checked_at TEXT NOT NULL
        );
    """)


def tick() -> RiskLevel:
    """Run one RiskGuard evaluation tick. Spec §7: 60-second cycle.

    Reads recent trade data from zeus.db, computes metrics,
    determines risk level, writes to risk_state.db.
    """
    zeus_conn = get_connection()
    risk_conn = get_connection(RISK_DB_PATH)
    init_risk_db(risk_conn)

    thresholds = settings["riskguard"]

    # Get recent settled trades for Brier/accuracy
    rows = zeus_conn.execute("""
        SELECT p_posterior, CASE WHEN status = 'won' THEN 1 ELSE 0 END as outcome
        FROM trade_decisions
        WHERE status IN ('won', 'lost')
        ORDER BY timestamp DESC LIMIT 50
    """).fetchall()

    p_forecasts = [r["p_posterior"] for r in rows]
    outcomes = [r["outcome"] for r in rows]

    # Get recent P&L for win rate
    pnl_rows = zeus_conn.execute("""
        SELECT fill_price FROM trade_decisions
        WHERE status IN ('won', 'lost')
        ORDER BY timestamp DESC LIMIT 20
    """).fetchall()
    # Simplified P&L: won=+1, lost=-1 (actual P&L computed elsewhere)
    pnl_list = [1.0 if r["outcome"] == 1 else -1.0 for r in rows[:20]] if rows else []

    # Compute metrics
    b_score = brier_score(p_forecasts, outcomes) if p_forecasts else 0.0
    d_accuracy = directional_accuracy(p_forecasts, outcomes) if p_forecasts else 0.5
    w_rate = win_rate(pnl_list) if pnl_list else 0.5

    # Evaluate levels
    brier_level = evaluate_brier(b_score, thresholds) if p_forecasts else RiskLevel.GREEN
    wr_level = evaluate_win_rate(w_rate, thresholds) if pnl_list else RiskLevel.GREEN

    level = overall_level(brier_level, wr_level)

    # Record
    now = datetime.now(timezone.utc).isoformat()
    risk_conn.execute("""
        INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        level.value, b_score, d_accuracy, w_rate,
        json.dumps({"brier_level": brier_level.value, "wr_level": wr_level.value}),
        now,
    ))
    risk_conn.commit()

    zeus_conn.close()
    risk_conn.close()

    if level != RiskLevel.GREEN:
        logger.warning("RiskGuard level: %s (Brier=%.3f, WinRate=%.1%%)",
                       level.value, b_score, w_rate * 100)

    return level


def get_current_level() -> RiskLevel:
    """Read current risk level from risk_state.db.

    R4: Fail-closed — if DB error or stale (>5 min), return RED.
    """
    try:
        conn = get_connection(RISK_DB_PATH)
        init_risk_db(conn)
        row = conn.execute(
            "SELECT level, checked_at FROM risk_state ORDER BY checked_at DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if row is None:
            # R3: Bootstrap state — no RiskGuard data yet. Allow trading.
            return RiskLevel.GREEN

        # R4: Staleness check — if last check > 5 min ago, RiskGuard may have crashed
        from datetime import datetime as dt
        last_check = dt.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - last_check).total_seconds()
        if age_seconds > 300:
            logger.warning("RiskGuard STALE: last check was %ds ago. Fail-closed → RED.",
                           int(age_seconds))
            return RiskLevel.RED

        return RiskLevel(row["level"])

    except Exception as e:
        # R4: DB error = fail closed → RED
        logger.error("RiskGuard DB error: %s. Fail-closed → RED.", e)
        return RiskLevel.RED


if __name__ == "__main__":
    """Run RiskGuard as standalone process."""
    import time
    logging.basicConfig(level=logging.INFO)
    logger.info("RiskGuard starting (60s tick)")

    while True:
        try:
            level = tick()
            logger.info("Tick complete: %s", level.value)
        except Exception as e:
            logger.error("RiskGuard tick failed: %s", e)
        time.sleep(60)
