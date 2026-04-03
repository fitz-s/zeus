"""RiskGuard: independent monitoring process. Spec §7.

Runs as a SEPARATE process with its own 60-second tick.
Reads authoritative settlement records from zeus.db, writes to risk_state.db,
and emits durable risk actions into zeus.db when the canonical table exists.
Graduated response: GREEN → YELLOW → ORANGE → RED.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import settings, STATE_DIR
from src.riskguard.metrics import (
    brier_score,
    directional_accuracy,
    evaluate_brier,
)
from src.riskguard.risk_level import RiskLevel, overall_level
from src.state.db import RISK_DB_PATH, get_connection, query_authoritative_settlement_rows
from src.state.portfolio import load_portfolio
from src.state.strategy_tracker import load_tracker

logger = logging.getLogger(__name__)


def _append_reason(bucket: dict[str, list[str]], key: str, reason: str) -> None:
    reasons = bucket.setdefault(key, [])
    if reason not in reasons:
        reasons.append(reason)


def _strategy_settlement_summary(rows: list[dict]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for row in rows:
        strategy = str(row.get("strategy") or "unclassified")
        bucket = summary.setdefault(
            strategy,
            {
                "count": 0,
                "pnl": 0.0,
                "wins": 0,
                "accuracy": None,
            },
        )
        bucket["count"] += 1
        pnl = row.get("pnl")
        if pnl is not None:
            bucket["pnl"] += float(pnl)
        outcome = row.get("outcome")
        if outcome == 1:
            bucket["wins"] += 1

    for strategy, bucket in summary.items():
        count = bucket["count"]
        bucket["pnl"] = round(bucket["pnl"], 2)
        bucket["accuracy"] = round(bucket["wins"] / count, 4) if count else None
    return summary


def _entry_execution_summary(conn: sqlite3.Connection, *, env: str, limit: int = 200) -> dict:
    try:
        rows = conn.execute(
            """
            SELECT event_type, strategy
            FROM position_events
            WHERE env = ?
              AND event_type IN ('ORDER_ATTEMPTED', 'ORDER_FILLED', 'ORDER_REJECTED')
            ORDER BY id DESC
            LIMIT ?
            """,
            (env, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    overall = {"attempted": 0, "filled": 0, "rejected": 0, "fill_rate": None}
    by_strategy: dict[str, dict] = {}
    for row in rows:
        event_type = str(row["event_type"])
        strategy = str(row["strategy"] or "unclassified")
        bucket = by_strategy.setdefault(
            strategy,
            {"attempted": 0, "filled": 0, "rejected": 0, "fill_rate": None},
        )
        if event_type == "ORDER_ATTEMPTED":
            overall["attempted"] += 1
            bucket["attempted"] += 1
        elif event_type == "ORDER_FILLED":
            overall["filled"] += 1
            bucket["filled"] += 1
        elif event_type == "ORDER_REJECTED":
            overall["rejected"] += 1
            bucket["rejected"] += 1

    def _finalize(bucket: dict) -> None:
        denom = bucket["filled"] + bucket["rejected"]
        bucket["fill_rate"] = round(bucket["filled"] / denom, 4) if denom else None

    _finalize(overall)
    for bucket in by_strategy.values():
        _finalize(bucket)
    return {"overall": overall, "by_strategy": by_strategy}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _sync_riskguard_strategy_gate_actions(
    conn: sqlite3.Connection,
    recommended_strategy_gate_reasons: dict[str, list[str]],
    *,
    issued_at: str,
) -> dict[str, int | str]:
    if not _table_exists(conn, "risk_actions"):
        logger.info("RiskGuard durable risk_actions table unavailable; skipping action emission")
        return {
            "status": "skipped_missing_table",
            "emitted_count": 0,
            "expired_count": 0,
        }

    recommended = {
        strategy: "|".join(sorted(reasons))
        for strategy, reasons in sorted(recommended_strategy_gate_reasons.items())
    }

    existing_rows = conn.execute(
        """
        SELECT action_id, strategy_key
        FROM risk_actions
        WHERE source = 'riskguard'
          AND action_type = 'gate'
          AND status = 'active'
        """
    ).fetchall()
    existing_by_strategy = {str(row["strategy_key"]): str(row["action_id"]) for row in existing_rows}
    expired_count = 0

    for strategy, reason in recommended.items():
        action_id = existing_by_strategy.get(strategy, f"riskguard:gate:{strategy}")
        conn.execute(
            """
            INSERT INTO risk_actions (
                action_id,
                strategy_key,
                action_type,
                value,
                issued_at,
                effective_until,
                reason,
                source,
                precedence,
                status
            ) VALUES (?, ?, 'gate', 'true', ?, NULL, ?, 'riskguard', 50, 'active')
            ON CONFLICT(action_id) DO UPDATE SET
                strategy_key = excluded.strategy_key,
                value = excluded.value,
                issued_at = excluded.issued_at,
                effective_until = NULL,
                reason = excluded.reason,
                precedence = excluded.precedence,
                status = 'active'
            """,
            (action_id, strategy, issued_at, reason),
        )

    for strategy, action_id in existing_by_strategy.items():
        if strategy in recommended:
            continue
        conn.execute(
            """
            UPDATE risk_actions
            SET effective_until = ?,
                status = 'expired'
            WHERE action_id = ?
            """,
            (issued_at, action_id),
        )
        expired_count += 1

    return {
        "status": "emitted",
        "emitted_count": len(recommended),
        "expired_count": expired_count,
    }


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
    current_env = settings.mode

    settlement_rows = query_authoritative_settlement_rows(zeus_conn, limit=50)
    settlement_row_storage_sources = sorted({str(r.get("source", "unknown")) for r in settlement_rows})
    settlement_storage_source = (
        settlement_row_storage_sources[0]
        if len(settlement_row_storage_sources) == 1
        else ("mixed" if settlement_row_storage_sources else "none")
    )
    settlement_authority_levels: dict[str, int] = {}
    degraded_rows = 0
    learning_snapshot_ready_count = 0
    canonical_payload_complete_count = 0
    metric_ready_rows = []
    for row in settlement_rows:
        authority_level = str(row.get("authority_level", "unknown"))
        settlement_authority_levels[authority_level] = settlement_authority_levels.get(authority_level, 0) + 1
        if row.get("is_degraded", False):
            degraded_rows += 1
        if row.get("learning_snapshot_ready", False):
            learning_snapshot_ready_count += 1
        if row.get("canonical_payload_complete", False):
            canonical_payload_complete_count += 1
        if row.get("metric_ready", True) and row.get("p_posterior") is not None and row.get("outcome") is not None:
            metric_ready_rows.append(row)

    p_forecasts = [float(r["p_posterior"]) for r in metric_ready_rows]
    outcomes = [int(r["outcome"]) for r in metric_ready_rows]
    strategy_settlement_summary = _strategy_settlement_summary(metric_ready_rows)
    entry_execution_summary = _entry_execution_summary(zeus_conn, env=current_env)
    try:
        tracker = load_tracker()
        tracker_summary = tracker.summary()
        edge_compression_alerts = tracker.edge_compression_check()
        tracker_accounting = dict(getattr(tracker, "accounting", {}))
        strategy_tracker_error = ""
    except Exception as exc:
        tracker_summary = {}
        edge_compression_alerts = []
        tracker_accounting = {}
        strategy_tracker_error = str(exc)

    # Compute metrics from authoritative settlement rows only.
    b_score = brier_score(p_forecasts, outcomes) if p_forecasts else 0.0
    d_accuracy = directional_accuracy(p_forecasts, outcomes) if p_forecasts else 0.5

    # Evaluate levels
    brier_level = evaluate_brier(b_score, thresholds) if p_forecasts else RiskLevel.GREEN
    settlement_quality_level = RiskLevel.GREEN
    if settlement_rows and not metric_ready_rows:
        settlement_quality_level = RiskLevel.RED
    elif degraded_rows > 0:
        settlement_quality_level = RiskLevel.YELLOW
    execution_quality_level = RiskLevel.GREEN
    execution_overall = entry_execution_summary["overall"]
    execution_observed = execution_overall["filled"] + execution_overall["rejected"]
    recommended_control_reasons: dict[str, list[str]] = {}
    recommended_strategy_gate_reasons: dict[str, list[str]] = {}
    if execution_overall["fill_rate"] is not None and execution_observed >= 10 and execution_overall["fill_rate"] < 0.3:
        execution_quality_level = RiskLevel.YELLOW
        _append_reason(
            recommended_control_reasons,
            "tighten_risk",
            f"execution_decay(fill_rate={execution_overall['fill_rate']}, observed={execution_observed})",
        )
    strategy_signal_level = RiskLevel.YELLOW if (edge_compression_alerts or strategy_tracker_error) else RiskLevel.GREEN
    for alert in edge_compression_alerts:
        if not alert.startswith("EDGE_COMPRESSION: "):
            continue
        strategy = alert.split(": ", 1)[1].split(" edge", 1)[0]
        _append_reason(recommended_strategy_gate_reasons, strategy, "edge_compression")
    for strategy, bucket in entry_execution_summary.get("by_strategy", {}).items():
        observed = bucket["filled"] + bucket["rejected"]
        fill_rate = bucket.get("fill_rate")
        if fill_rate is not None and observed >= 10 and fill_rate < 0.3:
            _append_reason(
                recommended_strategy_gate_reasons,
                strategy,
                f"execution_decay(fill_rate={fill_rate}, observed={observed})",
            )
    recommended_strategy_gates = sorted(recommended_strategy_gate_reasons)
    recommended_controls = []
    if execution_quality_level == RiskLevel.YELLOW:
        recommended_controls.append("tighten_risk")
    if recommended_strategy_gates:
        recommended_controls.append("review_strategy_gates")
        review_gate_reasons = [
            f"{strategy}:{'|'.join(sorted(recommended_strategy_gate_reasons.get(strategy, [])))}"
            for strategy in recommended_strategy_gates
        ]
        recommended_control_reasons["review_strategy_gates"] = review_gate_reasons

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

    level = overall_level(
        brier_level,
        settlement_quality_level,
        execution_quality_level,
        strategy_signal_level,
        daily_loss_level,
        weekly_loss_level,
    )

    # Record
    now = datetime.now(timezone.utc).isoformat()
    durable_action_status = _sync_riskguard_strategy_gate_actions(
        zeus_conn,
        recommended_strategy_gate_reasons,
        issued_at=now,
    )
    risk_conn.execute("""
        INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        level.value, b_score, d_accuracy, None,
        json.dumps({
            "brier_level": brier_level.value,
            "settlement_quality_level": settlement_quality_level.value,
            "execution_quality_level": execution_quality_level.value,
            "strategy_signal_level": strategy_signal_level.value,
            "daily_loss_level": daily_loss_level.value,
            "weekly_loss_level": weekly_loss_level.value,
            "daily_loss": round(portfolio.daily_loss, 2),
            "weekly_loss": round(portfolio.weekly_loss, 2),
            "realized_pnl": round(portfolio.realized_pnl, 2),
            "unrealized_pnl": round(portfolio.total_unrealized_pnl, 2),
            "total_pnl": round(portfolio.total_pnl, 2),
            "effective_bankroll": round(portfolio.effective_bankroll, 2),
            "settlement_sample_size": len(p_forecasts),
            "settlement_storage_source": settlement_storage_source,
            "settlement_row_storage_sources": settlement_row_storage_sources,
            "settlement_authority_levels": settlement_authority_levels,
            "settlement_degraded_row_count": degraded_rows,
            "settlement_learning_snapshot_ready_count": learning_snapshot_ready_count,
            "settlement_canonical_payload_complete_count": canonical_payload_complete_count,
            "settlement_metric_ready_count": len(metric_ready_rows),
            "accuracy": round(d_accuracy, 4),
            "strategy_settlement_summary": strategy_settlement_summary,
            "entry_execution_summary": entry_execution_summary,
            "strategy_tracker_summary": tracker_summary,
            "strategy_edge_compression_alerts": edge_compression_alerts,
            "strategy_tracker_accounting": tracker_accounting,
            "strategy_tracker_error": strategy_tracker_error,
            "recommended_strategy_gates": recommended_strategy_gates,
            "recommended_strategy_gate_reasons": {
                strategy: sorted(reasons)
                for strategy, reasons in sorted(recommended_strategy_gate_reasons.items())
            },
            "recommended_controls": recommended_controls,
            "recommended_control_reasons": {
                control: list(reasons)
                for control, reasons in sorted(recommended_control_reasons.items())
            },
            "durable_risk_action_emission_status": durable_action_status["status"],
            "durable_risk_action_emitted_count": durable_action_status["emitted_count"],
            "durable_risk_action_expired_count": durable_action_status["expired_count"],
        }),
        now,
    ))
    zeus_conn.commit()
    risk_conn.commit()

    zeus_conn.close()
    risk_conn.close()

    if level != RiskLevel.GREEN:
        logger.warning("RiskGuard level: %s (storage_source=%s, Brier=%.3f, Accuracy=%.1f%%)",
                       level.value, settlement_storage_source, b_score, d_accuracy * 100)

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
            logger.warning("RiskGuard has no persisted state row. Fail-closed → RED.")
            return RiskLevel.RED

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
