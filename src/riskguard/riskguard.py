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
from src.state.decision_chain import query_settlement_records
from src.state.portfolio import load_portfolio

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
    portfolio = load_portfolio()

    settlement_rows = query_settlement_records(zeus_conn, limit=50)

    p_forecasts = [float(r["p_posterior"]) for r in settlement_rows if "p_posterior" in r]
    outcomes = [int(r["outcome"]) for r in settlement_rows if "outcome" in r]
    pnl_list = [float(r["pnl"]) for r in settlement_rows[:20] if "pnl" in r]

    # Compute metrics
    b_score = brier_score(p_forecasts, outcomes) if p_forecasts else 0.0
    d_accuracy = directional_accuracy(p_forecasts, outcomes) if p_forecasts else 0.5
    w_rate = win_rate(pnl_list) if pnl_list else 0.5

    # Evaluate levels
    brier_level = evaluate_brier(b_score, thresholds) if p_forecasts else RiskLevel.GREEN
    wr_level = evaluate_win_rate(w_rate, thresholds) if pnl_list else RiskLevel.GREEN

    daily_loss_level = (
        RiskLevel.RED
        if portfolio.daily_loss > portfolio.initial_bankroll * thresholds["max_daily_loss_pct"]
        else RiskLevel.GREEN
    )
    weekly_loss_level = (
        RiskLevel.RED
        if portfolio.weekly_loss > portfolio.initial_bankroll * thresholds["max_weekly_loss_pct"]
        else RiskLevel.GREEN
    )

    level = overall_level(brier_level, wr_level, daily_loss_level, weekly_loss_level)

    # Record
    now = datetime.now(timezone.utc).isoformat()
    risk_conn.execute("""
        INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        level.value, b_score, d_accuracy, w_rate,
        json.dumps({
            "brier_level": brier_level.value,
            "wr_level": wr_level.value,
            "daily_loss_level": daily_loss_level.value,
            "weekly_loss_level": weekly_loss_level.value,
            "daily_loss": round(portfolio.daily_loss, 2),
            "weekly_loss": round(portfolio.weekly_loss, 2),
            "realized_pnl": round(portfolio.realized_pnl, 2),
            "unrealized_pnl": round(portfolio.total_unrealized_pnl, 2),
            "total_pnl": round(portfolio.total_pnl, 2),
            "effective_bankroll": round(portfolio.effective_bankroll, 2),
        }),
        now,
    ))
    risk_conn.commit()

    zeus_conn.close()
    risk_conn.close()

    if level != RiskLevel.GREEN:
        logger.warning("RiskGuard level: %s (Brier=%.3f, WinRate=%.1f%%)",
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
