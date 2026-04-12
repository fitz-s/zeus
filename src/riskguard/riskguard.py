"""RiskGuard: independent monitoring process. Spec §7.

Runs as a SEPARATE process with its own 60-second tick.
Reads authoritative settlement records from zeus.db, writes to risk_state.db,
and emits durable risk actions into zeus.db when the canonical table exists.
Graduated response: GREEN → YELLOW → ORANGE → RED.
"""

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import get_mode, settings, STATE_DIR
from src.riskguard.discord_alerts import alert_halt, alert_resume, alert_warning
from src.riskguard.metrics import (
    brier_score,
    directional_accuracy,
    evaluate_brier,
)
from src.riskguard.risk_level import RiskLevel, overall_level
from src.state.db import (
    RISK_DB_PATH,
    get_connection,
    get_trade_connection_with_world,
    query_authoritative_settlement_rows,
    query_portfolio_loader_view,
    query_strategy_health_snapshot,
    refresh_strategy_health,
)
from src.state.portfolio import PortfolioState, Position, load_portfolio
from src.state.portfolio_loader_policy import choose_portfolio_truth_source
from src.state.strategy_tracker import load_tracker

logger = logging.getLogger(__name__)
TRAILING_LOSS_ROW_TOLERANCE_USD = 0.01
TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE = timedelta(hours=2)
TRAILING_LOSS_SOURCE_OK = "risk_state_history"
TRAILING_LOSS_SOURCE_DEGRADED = "no_trustworthy_reference_row"
TRAILING_LOSS_STATUSES = {
    "ok",
    "insufficient_history",
    "inconsistent_history",
    "no_reference_row",
}


def _get_runtime_trade_connection() -> sqlite3.Connection:
    if get_connection.__module__ != "src.state.db":
        return get_connection()
    return get_trade_connection_with_world()


def _load_riskguard_capital_metadata() -> tuple[PortfolioState, str]:
    try:
        return load_portfolio(), "working_state_metadata"
    except Exception:
        logger.warning("RiskGuard capital metadata fallback to settings.capital_base_usd", exc_info=True)
        return PortfolioState(bankroll=float(settings.capital_base_usd)), "settings_capital_base_fallback"


def _portfolio_position_from_loader_row(row: dict) -> Position:
    return Position(
        trade_id=str(row.get("trade_id") or ""),
        market_id=str(row.get("market_id") or ""),
        city=str(row.get("city") or ""),
        cluster=str(row.get("cluster") or ""),
        target_date=str(row.get("target_date") or ""),
        bin_label=str(row.get("bin_label") or ""),
        direction=str(row.get("direction") or "unknown"),
        unit=str(row.get("unit") or "F"),
        env=str(row.get("env") or get_mode()),
        size_usd=float(row.get("size_usd") or 0.0),
        shares=float(row.get("shares") or 0.0),
        cost_basis_usd=float(row.get("cost_basis_usd") or 0.0),
        entry_price=float(row.get("entry_price") or 0.0),
        p_posterior=float(row.get("p_posterior") or 0.0),
        entered_at=str(row.get("entered_at") or ""),
        day0_entered_at=str(row.get("day0_entered_at") or ""),
        decision_snapshot_id=str(row.get("decision_snapshot_id") or ""),
        entry_method=str(row.get("entry_method") or ""),
        strategy_key=str(row.get("strategy_key") or ""),
        strategy=str(row.get("strategy") or row.get("strategy_key") or ""),
        edge_source=str(row.get("edge_source") or ""),
        discovery_mode=str(row.get("discovery_mode") or ""),
        state=str(row.get("state") or "entered"),
        order_id=str(row.get("order_id") or ""),
        order_status=str(row.get("order_status") or ""),
        chain_state=str(row.get("chain_state") or ""),
        exit_state=str(row.get("exit_state") or ""),
        last_monitor_prob=float(row.get("last_monitor_prob") or 0.0),
        last_monitor_edge=float(row.get("last_monitor_edge") or 0.0),
        last_monitor_market_price=row.get("last_monitor_market_price"),
        admin_exit_reason=str(row.get("admin_exit_reason") or ""),
        entry_fill_verified=bool(row.get("entry_fill_verified", False)),
    )


def _load_riskguard_portfolio_truth(zeus_conn: sqlite3.Connection) -> tuple[PortfolioState, dict]:
    loader_view = query_portfolio_loader_view(zeus_conn)
    policy = choose_portfolio_truth_source(loader_view.get("status"))
    if policy.source == "canonical_db":
        metadata_state, capital_source = _load_riskguard_capital_metadata()
        positions = [
            _portfolio_position_from_loader_row(row)
            for row in loader_view.get("positions", [])
        ]
        bankroll = float(getattr(metadata_state, "bankroll", settings.capital_base_usd) or settings.capital_base_usd)
        portfolio = PortfolioState(
            positions=positions,
            bankroll=bankroll,
            updated_at=str(getattr(metadata_state, "updated_at", "") or ""),
            audit_logging_enabled=True,
            daily_baseline_total=float(getattr(metadata_state, "daily_baseline_total", bankroll) or bankroll),
            weekly_baseline_total=float(getattr(metadata_state, "weekly_baseline_total", bankroll) or bankroll),
            recent_exits=list(getattr(metadata_state, "recent_exits", []) or []),
            ignored_tokens=list(getattr(metadata_state, "ignored_tokens", []) or []),
        )
        return portfolio, {
            "source": "position_current",
            "loader_status": str(loader_view.get("status") or "unknown"),
            "fallback_active": False,
            "fallback_reason": "",
            "position_count": len(positions),
            "capital_source": capital_source,
        }

    portfolio, capital_source = _load_riskguard_capital_metadata()
    return portfolio, {
        "source": "working_state_fallback",
        "loader_status": str(loader_view.get("status") or "unknown"),
        "fallback_active": True,
        "fallback_reason": policy.reason,
        "fallback_escalate": policy.escalate,
        "position_count": len(portfolio.positions),
        "capital_source": capital_source,
    }


def _coerce_finite_float(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _risk_state_reference_from_row(row: sqlite3.Row) -> dict | None:
    try:
        details = json.loads(row["details_json"] or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(details, dict):
        return None
    initial_bankroll = _coerce_finite_float(details.get("initial_bankroll"))
    total_pnl = _coerce_finite_float(details.get("total_pnl"))
    effective_bankroll = _coerce_finite_float(details.get("effective_bankroll"))
    if initial_bankroll is None or total_pnl is None or effective_bankroll is None:
        return None
    expected_equity = round(initial_bankroll + total_pnl, 2)
    if abs(expected_equity - effective_bankroll) > TRAILING_LOSS_ROW_TOLERANCE_USD:
        return None
    return {
        "row_id": int(row["id"]),
        "checked_at": str(row["checked_at"] or ""),
        "initial_bankroll": round(initial_bankroll, 2),
        "total_pnl": round(total_pnl, 2),
        "effective_bankroll": round(effective_bankroll, 2),
    }


def _trailing_loss_reference(
    risk_conn: sqlite3.Connection,
    *,
    now: str,
    lookback: timedelta,
) -> dict:
    cutoff_dt = datetime.fromisoformat(now.replace("Z", "+00:00")) - lookback
    cutoff = cutoff_dt.isoformat()
    window_start = (cutoff_dt - TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE).isoformat()
    total_rows = int(
        risk_conn.execute("SELECT COUNT(*) FROM risk_state").fetchone()[0] or 0
    )
    if total_rows == 0:
        return {
            "status": "no_reference_row",
            "source": TRAILING_LOSS_SOURCE_DEGRADED,
            "reference": None,
        }

    candidate_rows = risk_conn.execute(
        """
        SELECT id, checked_at, details_json
        FROM risk_state
        WHERE checked_at <= ?
          AND checked_at >= ?
        ORDER BY checked_at DESC, id DESC
        """,
        (cutoff, window_start),
    ).fetchall()
    if not candidate_rows:
        return {
            "status": "insufficient_history",
            "source": TRAILING_LOSS_SOURCE_DEGRADED,
            "reference": None,
        }

    for row in candidate_rows:
        if reference := _risk_state_reference_from_row(row):
            return {
                "status": "ok",
                "source": TRAILING_LOSS_SOURCE_OK,
                "reference": reference,
            }

    return {
        "status": "inconsistent_history",
        "source": TRAILING_LOSS_SOURCE_DEGRADED,
        "reference": None,
    }


def _trailing_loss_snapshot(
    risk_conn: sqlite3.Connection,
    *,
    now: str,
    lookback: timedelta,
    current_equity: float,
    initial_bankroll: float,
    threshold_pct: float,
) -> dict:
    reference_info = _trailing_loss_reference(risk_conn, now=now, lookback=lookback)
    status = str(reference_info["status"])
    if status not in TRAILING_LOSS_STATUSES:
        raise RuntimeError(f"unexpected trailing loss status: {status}")
    reference = reference_info.get("reference")
    if status != "ok" or reference is None:
        return {
            "loss": 0.0,
            "level": RiskLevel.YELLOW,
            "status": status,
            "source": str(reference_info["source"]),
            "reference": None,
        }
    reference_equity = float(reference["effective_bankroll"])
    loss = round(max(0.0, reference_equity - current_equity), 2)
    level = (
        RiskLevel.RED
        if loss > float(initial_bankroll) * float(threshold_pct)
        else RiskLevel.GREEN
    )
    return {
        "loss": loss,
        "level": level,
        "status": status,
        "source": str(reference_info["source"]),
        "reference": reference,
    }


def _append_reason(bucket: dict[str, list[str]], key: str, reason: str) -> None:
    reasons = bucket.setdefault(key, [])
    if reason not in reasons:
        reasons.append(reason)


def _canonical_recent_exits_from_settlement_rows(rows: list[dict]) -> list[dict]:
    exits: list[dict] = []
    for row in rows:
        pnl = row.get("pnl")
        if pnl is None:
            continue
        exits.append(
            {
                "city": str(row.get("city") or ""),
                "bin_label": str(row.get("range_label") or row.get("winning_bin") or ""),
                "target_date": str(row.get("target_date") or ""),
                "direction": str(row.get("direction") or ""),
                "token_id": "",
                "no_token_id": "",
                "exit_reason": str(row.get("exit_reason") or "SETTLEMENT"),
                "exited_at": str(row.get("exited_at") or row.get("settled_at") or ""),
                "pnl": float(pnl),
            }
        )
    return exits


def _current_mode_realized_exits(conn: sqlite3.Connection, *, env: str) -> tuple[list[dict], str]:
    if conn is None:
        return [], "none"
    try:
        rows = conn.execute(
            """
            SELECT strategy_key, city, target_date, position_id, exit_reason, settled_at, pnl
            FROM outcome_fact
            WHERE pnl IS NOT NULL
            ORDER BY settled_at DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if rows:
        return (
            [
                {
                    "city": str(row["city"] or ""),
                    "bin_label": str(row["position_id"] or ""),
                    "target_date": str(row["target_date"] or ""),
                    "direction": "",
                    "token_id": "",
                    "no_token_id": "",
                    "exit_reason": str(row["exit_reason"] or "SETTLEMENT"),
                    "exited_at": str(row["settled_at"] or ""),
                    "pnl": float(row["pnl"]),
                    "strategy_key": str(row["strategy_key"] or ""),
                }
                for row in rows
            ],
            "outcome_fact",
        )

    try:
        rows = conn.execute(
            """
            SELECT json_extract(details_json, '$.city') AS city,
                   json_extract(details_json, '$.range_label') AS range_label,
                   json_extract(details_json, '$.target_date') AS target_date,
                   json_extract(details_json, '$.direction') AS direction,
                   json_extract(details_json, '$.exit_reason') AS exit_reason,
                   timestamp AS exited_at,
                   json_extract(details_json, '$.pnl') AS pnl
            FROM chronicle
            WHERE event_type = 'SETTLEMENT'
              AND env = ?
              AND trade_id IS NOT NULL
              AND id IN (
                SELECT MAX(id)
                FROM chronicle
                WHERE event_type = 'SETTLEMENT'
                  AND env = ?
                  AND trade_id IS NOT NULL
                GROUP BY trade_id
              )
            ORDER BY timestamp DESC
            """,
            (env, env),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if rows:
        return (
            [
                {
                    "city": str(row["city"] or ""),
                    "bin_label": str(row["range_label"] or ""),
                    "target_date": str(row["target_date"] or ""),
                    "direction": str(row["direction"] or ""),
                    "token_id": "",
                    "no_token_id": "",
                    "exit_reason": str(row["exit_reason"] or "SETTLEMENT"),
                    "exited_at": str(row["exited_at"] or ""),
                    "pnl": float(row["pnl"]),
                }
                for row in rows
                if row["pnl"] is not None
            ],
            "chronicle_dedup",
        )

    return [], "none"


def _strategy_settlement_summary(rows: list[dict]) -> dict[str, dict]:
    """Aggregate settlement rows into per-strategy counts and PnL.

    K1 invariant (bug #1/#2): this aggregation MUST be deduped by
    trade_id. Settlement rows can come from multiple upstream sources
    (canonical position_events, legacy position_events_legacy, legacy
    decision_log artifacts) and the same underlying trade may appear in
    more than one source or in multiple batches of the same source. Prior
    to dedup, opening_inertia would show 19 settlements on
    2026-04-11 while the canonical truth was 6 unique positions, because
    two decision_log settlement batches (19:43 and 20:43) each recorded
    the same 6 positions. The two bugs are now fixed at the writer layer
    but historical decision_log rows from before the fix still contain
    duplicates, so the reader must dedup defensively.

    Dedup policy: for each trade_id, keep the FIRST row encountered in
    iteration order. Callers should pass rows ordered by occurred_at ASC
    if they want the earliest settlement record; the current caller
    passes most-recent-first order from query_settlement_events, which
    means the last recorded settlement wins. That is fine as long as
    settlement is idempotent at the writer layer (bug #9 fix).
    """
    summary: dict[str, dict] = {}
    seen_trade_ids: set[str] = set()
    for row in rows:
        trade_id = str(row.get("trade_id") or row.get("runtime_trade_id") or "")
        if not trade_id:
            # Rows without a trade_id cannot be deduped; fall back to
            # including them so we do not silently drop data. This should
            # be rare after the settlement writer fixes land.
            pass
        elif trade_id in seen_trade_ids:
            continue
        else:
            seen_trade_ids.add(trade_id)

        strategy = str(row.get("strategy") or "unclassified")
        bucket = summary.setdefault(
            strategy,
            {
                "count": 0,
                "pnl": 0.0,
                "wins": 0,
                # K2 rename (bug #3): this is trade profitability (wins/count),
                # distinct from probability_directional_accuracy at the
                # risk.details top level. The old shared 'accuracy' key name
                # caused LLM reporters to conflate the two metrics.
                "trade_profitability_rate": None,
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
        bucket["trade_profitability_rate"] = (
            round(bucket["wins"] / count, 4) if count else None
        )
    return summary


def _entry_execution_summary(conn: sqlite3.Connection, *, env: str, limit: int = 200) -> dict:
    """Entry execution summary from canonical position_events."""
    try:
        rows = conn.execute(
            """
            SELECT event_type, strategy_key
            FROM position_events
            WHERE event_type IN ('POSITION_OPEN_INTENT', 'ENTRY_ORDER_FILLED', 'ENTRY_ORDER_REJECTED')
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    overall = {"attempted": 0, "filled": 0, "rejected": 0, "fill_rate": None}
    by_strategy: dict[str, dict] = {}
    mapping = {
        "POSITION_OPEN_INTENT": "attempted",
        "ENTRY_ORDER_FILLED": "filled",
        "ENTRY_ORDER_REJECTED": "rejected",
    }
    for row in rows:
        event_type = str(row["event_type"])
        counter_key = mapping.get(event_type)
        if counter_key is None:
            continue
        strategy = str(row["strategy_key"] or "unclassified")
        bucket = by_strategy.setdefault(
            strategy,
            {"attempted": 0, "filled": 0, "rejected": 0, "fill_rate": None},
        )
        overall[counter_key] += 1
        bucket[counter_key] += 1

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
    # B5: Add force_exit_review column if missing (code-level migration, no raw ALTER)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(risk_state)").fetchall()}
    if "force_exit_review" not in cols:
        try:
            conn.execute("ALTER TABLE risk_state ADD COLUMN force_exit_review INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # concurrent process already added it


def tick() -> RiskLevel:
    """Run one RiskGuard evaluation tick. Spec §7: 60-second cycle.

    Reads recent trade data from zeus.db, computes metrics,
    determines risk level, writes to risk_state.db.
    """
    zeus_conn = _get_runtime_trade_connection()
    risk_conn = get_connection(RISK_DB_PATH)
    init_risk_db(risk_conn)

    previous_row = risk_conn.execute(
        "SELECT level FROM risk_state ORDER BY checked_at DESC LIMIT 1"
    ).fetchone()
    previous_level = RiskLevel(previous_row["level"]) if previous_row else None

    thresholds = settings["riskguard"]
    portfolio, portfolio_truth = _load_riskguard_portfolio_truth(zeus_conn)
    current_env = get_mode()

    settlement_rows = query_authoritative_settlement_rows(zeus_conn, limit=50, env=current_env)
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

    realized_exits, realized_truth_source = _current_mode_realized_exits(zeus_conn, env=current_env)
    if realized_exits:
        portfolio = replace(portfolio, recent_exits=realized_exits)
    else:
        canonical_recent_exits = _canonical_recent_exits_from_settlement_rows(settlement_rows)
        if canonical_recent_exits:
            portfolio = replace(portfolio, recent_exits=canonical_recent_exits)
            realized_truth_source = "authoritative_settlement_rows"

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

    # Refresh and query strategy health FIRST to compute canonical PnL
    now = datetime.now(timezone.utc).isoformat()
    durable_action_status = _sync_riskguard_strategy_gate_actions(
        zeus_conn,
        recommended_strategy_gate_reasons,
        issued_at=now,
    )
    strategy_health_refresh = refresh_strategy_health(zeus_conn, as_of=now)
    strategy_health_snapshot = query_strategy_health_snapshot(
        zeus_conn,
        now=now,
    )

    total_realized_pnl = sum(bucket.get("realized_pnl_30d", 0.0) for bucket in strategy_health_snapshot.get("by_strategy", {}).values())
    total_unrealized_pnl = sum(bucket.get("unrealized_pnl", 0.0) for bucket in strategy_health_snapshot.get("by_strategy", {}).values())

    if total_realized_pnl == 0.0 and strategy_health_snapshot.get("status") in ("missing_table", "empty", "fresh", "stale"):
        # Fallback for realized PnL in legacy tests or missing outcome_fact
        total_realized_pnl = sum(float(ext.get("pnl", 0.0)) for ext in getattr(portfolio, "recent_exits", []) if isinstance(ext, dict))
    
    if total_unrealized_pnl == 0.0 and strategy_health_snapshot.get("status") in ("missing_table", "empty", "fresh", "stale"):
        # Fallback for unrealized PnL
        total_unrealized_pnl = sum(float(getattr(p, "unrealized_pnl", 0.0)) for p in getattr(portfolio, "positions", []))

    total_pnl = total_realized_pnl + total_unrealized_pnl

    current_total_value = round(portfolio.initial_bankroll + total_pnl, 2)
    daily_loss_snapshot = _trailing_loss_snapshot(
        risk_conn,
        now=now,
        lookback=timedelta(hours=24),
        current_equity=current_total_value,
        initial_bankroll=float(portfolio.initial_bankroll),
        threshold_pct=float(thresholds["max_daily_loss_pct"]),
    )
    weekly_loss_snapshot = _trailing_loss_snapshot(
        risk_conn,
        now=now,
        lookback=timedelta(days=7),
        current_equity=current_total_value,
        initial_bankroll=float(portfolio.initial_bankroll),
        threshold_pct=float(thresholds["max_weekly_loss_pct"]),
    )
    daily_loss = daily_loss_snapshot["loss"]
    weekly_loss = weekly_loss_snapshot["loss"]
    daily_loss_level = daily_loss_snapshot["level"]
    weekly_loss_level = weekly_loss_snapshot["level"]

    level = overall_level(
        brier_level,
        settlement_quality_level,
        execution_quality_level,
        strategy_signal_level,
        daily_loss_level,
        weekly_loss_level,
    )

    # B5: force_exit_review when daily loss reaches RED
    force_exit_review = 1 if daily_loss_level == RiskLevel.RED else 0

    risk_conn.execute("""
        INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        level.value, b_score, d_accuracy, None,
        json.dumps({
            "brier_level": brier_level.value,
            "settlement_quality_level": settlement_quality_level.value,
            "execution_quality_level": execution_quality_level.value,
            "strategy_signal_level": strategy_signal_level.value,
            "daily_loss_level": daily_loss_level.value,
            "weekly_loss_level": weekly_loss_level.value,
            "daily_loss": None if daily_loss is None else round(float(daily_loss), 2),
            "weekly_loss": None if weekly_loss is None else round(float(weekly_loss), 2),
            "daily_loss_status": daily_loss_snapshot["status"],
            "weekly_loss_status": weekly_loss_snapshot["status"],
            "daily_loss_source": daily_loss_snapshot["source"],
            "weekly_loss_source": weekly_loss_snapshot["source"],
            "daily_loss_reference": daily_loss_snapshot["reference"],
            "weekly_loss_reference": weekly_loss_snapshot["reference"],
            "initial_bankroll": round(portfolio.initial_bankroll, 2),
            "daily_baseline_total": round(portfolio.daily_baseline_total, 2),
            "weekly_baseline_total": round(portfolio.weekly_baseline_total, 2),
            "realized_pnl": round(total_realized_pnl, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "effective_bankroll": round(current_total_value, 2),
            "portfolio_truth_source": portfolio_truth["source"],
            "portfolio_loader_status": portfolio_truth["loader_status"],
            "portfolio_fallback_active": portfolio_truth["fallback_active"],
            "portfolio_fallback_reason": portfolio_truth["fallback_reason"],
            "portfolio_position_count": portfolio_truth["position_count"],
            "portfolio_capital_source": portfolio_truth.get("capital_source", "unknown"),
            "realized_truth_source": realized_truth_source,
            "settlement_sample_size": len(p_forecasts),
            "settlement_storage_source": settlement_storage_source,
            "settlement_row_storage_sources": settlement_row_storage_sources,
            "settlement_authority_levels": settlement_authority_levels,
            "settlement_degraded_row_count": degraded_rows,
            "settlement_learning_snapshot_ready_count": learning_snapshot_ready_count,
            "settlement_canonical_payload_complete_count": canonical_payload_complete_count,
            "settlement_metric_ready_count": len(metric_ready_rows),
            # K2 rename (bug #3): this field is the PROBABILITY-SIDE directional
            # hit rate computed from brier forecasts (did p>0.5 match the
            # outcome?). It is NOT the same as trade profitability rate, which
            # lives inside strategy_settlement_summary as per-strategy
            # 'trade_profitability_rate'. The previous bare 'accuracy' key
            # collided in name with the per-strategy rate and caused LLM
            # reporters to copy 0.8947 as 'win rate'.
            "probability_directional_accuracy": round(d_accuracy, 4),
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
            "strategy_health_refresh_status": strategy_health_refresh["status"],
            "strategy_health_rows_written": strategy_health_refresh.get("rows_written", 0),
            "strategy_health_missing_required_tables": list(strategy_health_refresh.get("missing_required_tables", [])),
            "strategy_health_missing_optional_tables": list(strategy_health_refresh.get("missing_optional_tables", [])),
            "strategy_health_omitted_fields": list(strategy_health_refresh.get("omitted_fields", [])),
            "strategy_health_snapshot_status": strategy_health_snapshot["status"],
            "strategy_health_stale_strategy_keys": list(strategy_health_snapshot.get("stale_strategy_keys", [])),
        }),
        now,
        force_exit_review,
    ))
    zeus_conn.commit()
    risk_conn.commit()

    zeus_conn.close()
    risk_conn.close()

    try:
        if level == RiskLevel.RED:
            failed_rules = []
            if brier_level == RiskLevel.RED:
                failed_rules.append({
                    "name": "brier",
                    "value": round(b_score, 4),
                    "threshold": thresholds["brier_red"],
                    "detail": f"accuracy={d_accuracy:.4f}",
                })
            if settlement_quality_level == RiskLevel.RED:
                failed_rules.append({
                    "name": "settlement_quality",
                    "value": 0,
                    "threshold": 1,
                    "detail": f"storage_source={settlement_storage_source}",
                })
            if daily_loss_level == RiskLevel.RED:
                failed_rules.append({
                    "name": "daily_loss_pct",
                    "value": round(float(daily_loss or 0.0), 4),
                    "threshold": thresholds["max_daily_loss_pct"],
                    "detail": f"effective_bankroll={current_total_value:.2f}",
                })
            if weekly_loss_level == RiskLevel.RED:
                failed_rules.append({
                    "name": "weekly_loss_pct",
                    "value": round(float(weekly_loss or 0.0), 4),
                    "threshold": thresholds["max_weekly_loss_pct"],
                    "detail": f"effective_bankroll={current_total_value:.2f}",
                })
            alert_halt(failed_rules or [{
                "name": "riskguard",
                "value": 1,
                "threshold": 0,
                "detail": f"level={level.value}",
            }])
        elif previous_level == RiskLevel.RED and level == RiskLevel.GREEN:
            alert_resume("rules cleared")
        elif level == RiskLevel.YELLOW:
            if brier_level == RiskLevel.YELLOW:
                alert_warning("Brier score", round(b_score, 4), thresholds["brier_yellow"], detail=f"accuracy={d_accuracy:.4f}")
            if execution_quality_level == RiskLevel.YELLOW:
                alert_warning(
                    "Execution fill rate",
                    round(execution_overall.get("fill_rate", 0.0), 4) if execution_overall.get("fill_rate") is not None else 0.0,
                    0.3,
                    detail=f"observed={execution_observed}",
                )
            if settlement_quality_level == RiskLevel.YELLOW:
                alert_warning("Settlement quality", float(degraded_rows), 1.0, detail=f"storage_source={settlement_storage_source}")
            if strategy_signal_level == RiskLevel.YELLOW:
                alert_warning("Strategy signal", float(len(edge_compression_alerts)), 1.0, detail=strategy_tracker_error or "edge_compression_alerts_present")
    except Exception as exc:
        logger.warning("Discord alert emission failed: %s", exc)

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


def get_force_exit_review() -> bool:
    """Read force_exit_review flag from most recent risk_state row.

    B5: When daily_loss_level reaches RED, this returns True so that
    cycle_runner can block new entries. (Phase 1 scope: entry-blocking;
    forced exit sweep for active positions is a Phase 2 item.)
    Fail-closed: returns True on any error (conservative).
    """
    conn = None
    try:
        conn = get_connection(RISK_DB_PATH)
        init_risk_db(conn)
        row = conn.execute(
            "SELECT force_exit_review FROM risk_state ORDER BY checked_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        return bool(row["force_exit_review"])
    except Exception:
        return True  # fail-closed: assume exit review needed
    finally:
        if conn is not None:
            conn.close()


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
