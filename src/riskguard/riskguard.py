"""RiskGuard: independent monitoring process. Spec §7.

Runs as a SEPARATE process with its own 60-second tick.
Reads authoritative settlement records from zeus.db, writes to risk_state.db,
and emits durable risk actions into zeus.db when the canonical table exists.
Graduated response: GREEN → YELLOW → ORANGE → RED.

# Created: (pre-audit)
# Last reused or audited: 2026-05-10
# Authority basis: connection-leak audit 2026-05-10 — 51 open zeus-world.db-wal
#   handles observed on PID 18538. Root cause: tick() and tick_with_portfolio()
#   opened zeus_conn / risk_conn without try/finally, so any exception in the
#   tick body left both connections dangling. Fixed by wrapping tick bodies in
#   try/finally to guarantee conn.close() on every exit path.
"""

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import settings, STATE_DIR, get_mode
from src.riskguard.discord_alerts import alert_halt, alert_resume, alert_warning
from src.riskguard.metrics import (
    brier_score,
    directional_accuracy,
    evaluate_brier,
)
from src.riskguard.risk_level import RiskLevel, overall_level
from src.runtime import bankroll_provider
from src.state.db import (
    RISK_DB_PATH,
    get_connection,
    get_trade_connection_with_world,
    query_authoritative_settlement_rows,
    query_portfolio_loader_view,
    query_strategy_health_snapshot,
    refresh_strategy_health,
)
from src.state.portfolio import (
    ENTRY_ECONOMICS_LEGACY_UNKNOWN,
    FILL_GRADE_ENTRY_AUTHORITIES,
    FILL_GRADE_FILL_AUTHORITIES,
    FILL_AUTHORITY_NONE,
    PortfolioState,
    Position,
    load_portfolio,
)
from src.state.portfolio_loader_policy import choose_portfolio_truth_source
from src.state.strategy_tracker import load_tracker

logger = logging.getLogger(__name__)
TRAILING_LOSS_ROW_TOLERANCE_USD = 0.01
TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE = timedelta(hours=2)
TRAILING_LOSS_SOURCE_OK = "risk_state_history"
TRAILING_LOSS_SOURCE_DEGRADED = "no_trustworthy_reference_row"
TRAILING_LOSS_STATUSES = {
    "ok",
    "stale_reference",
    "insufficient_history",
    "inconsistent_history",
    "no_reference_row",
}


def _finite_float_or_none(value):
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _get_runtime_trade_connection() -> sqlite3.Connection:
    # v4 plan §AX3: riskguard runtime = LIVE class.
    if get_connection.__module__ != "src.state.db":
        return get_connection()
    return get_trade_connection_with_world(write_class="live")


def _load_riskguard_capital_metadata() -> tuple[PortfolioState, str]:
    try:
        return load_portfolio(), "working_state_metadata"
    except Exception:
        logger.error("RiskGuard capital metadata load FAILED — refusing to fall back to settings", exc_info=True)
        raise


def _portfolio_position_from_loader_row(row: dict) -> Position:
    # B052: Enforce strict canonical fields rather than filling defaults
    required = ["trade_id", "market_id", "city", "target_date", "direction", "unit", "env", "size_usd"]
    for req in required:
        if row.get(req) is None or str(row.get(req)) == "":
            raise ValueError(f"Canonical loader row missing critical field {req!r}")

    entry_authority = str(row.get("entry_economics_authority") or ENTRY_ECONOMICS_LEGACY_UNKNOWN)
    fill_authority = str(row.get("fill_authority") or FILL_AUTHORITY_NONE)
    if entry_authority in FILL_GRADE_ENTRY_AUTHORITIES or fill_authority in FILL_GRADE_FILL_AUTHORITIES:
        if str(row.get("entry_economics_source") or "") != "execution_fact":
            raise ValueError("fill-grade loader row missing execution_fact source provenance")
        if not str(row.get("execution_fact_intent_id") or ""):
            raise ValueError("fill-grade loader row missing execution_fact_intent_id provenance")
        if not str(row.get("execution_fact_filled_at") or ""):
            raise ValueError("fill-grade loader row missing execution_fact_filled_at provenance")

    return Position(
        trade_id=str(row["trade_id"]),
        market_id=str(row["market_id"]),
        city=str(row["city"]),
        cluster=str(row.get("cluster") or ""),
        target_date=str(row["target_date"]),
        bin_label=str(row.get("bin_label") or ""),
        direction=str(row["direction"]),
        unit=str(row["unit"]),
        temperature_metric=str(row.get("temperature_metric") or "high"),
        env=str(row["env"]),
        size_usd=float(row["size_usd"]),
        shares=float(row.get("shares") or 0.0),
        cost_basis_usd=float(row.get("cost_basis_usd") or 0.0),
        entry_price=float(row.get("entry_price") or 0.0),
        submitted_notional_usd=float(row.get("submitted_size_usd") or 0.0),
        filled_cost_basis_usd=float(row.get("filled_cost_basis_usd") or 0.0),
        entry_price_avg_fill=float(row.get("entry_price_avg_fill") or 0.0),
        shares_filled=float(row.get("shares_filled") or 0.0),
        entry_economics_authority=entry_authority,
        fill_authority=fill_authority,
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
        token_id=str(row.get("token_id") or ""),
        no_token_id=str(row.get("no_token_id") or ""),
        condition_id=str(row.get("condition_id") or ""),
        exit_state=str(row.get("exit_state") or ""),
        last_monitor_prob=_finite_float_or_none(row.get("last_monitor_prob")),
        last_monitor_edge=_finite_float_or_none(row.get("last_monitor_edge")),
        last_monitor_market_price=row.get("last_monitor_market_price"),
        admin_exit_reason=str(row.get("admin_exit_reason") or ""),
        entry_fill_verified=bool(row.get("entry_fill_verified", False)),
    )


def _load_riskguard_portfolio_truth(zeus_conn: sqlite3.Connection) -> tuple[PortfolioState, dict]:
    loader_view = query_portfolio_loader_view(zeus_conn)
    policy = choose_portfolio_truth_source(loader_view.get("status"))
    if policy.source != "canonical_db":
        raise RuntimeError(
            f"riskguard requires canonical truth source, got {policy.source!r}: {policy.reason}"
        )
    metadata_state, capital_source = _load_riskguard_capital_metadata()
    positions = []
    for row in loader_view.get("positions", []):
        try:
            positions.append(_portfolio_position_from_loader_row(row))
        except ValueError as exc:
            # B052: Quarantine broken rows and escalate to avoid silent masking
            logger.error("Quarantining invalid canonical portfolio row: %s", exc)
            raise RuntimeError(f"RiskGuard DB loader fault: {exc}")

    # B053 [YELLOW / flag for SD-A authority-separation reviewer]:
    # Dual-source consistency locking. A position-count mismatch between
    # canonical_db (the authoritative source) and capital metadata (the
    # blending input) indicates stale or drifted state. Elevate to ERROR
    # log level and expose both counts on the returned dict so downstream
    # callers can fail-close on `consistency_lock == 'mismatched'` rather
    # than silently blend inconsistent authority sources.
    metadata_positions = getattr(metadata_state, "positions", [])
    if len(positions) != len(metadata_positions):
        logger.error(
            "B053 Consistency Mismatch: canonical_db has %d positions vs %d in capital metadata. RiskGuard blending MUST NOT proceed on the blended view without caller-side consistency_lock check.",
            len(positions), len(metadata_positions)
        )

    # Bankroll truth comes from on-chain wallet via src.runtime.bankroll_provider.
    # If metadata_state has no bankroll (or 0/falsy), do NOT fall back to a config
    # literal — return 0.0 so downstream sizing fails-CLOSED and RiskGuard.tick()
    # surfaces DATA_DEGRADED via the bankroll_provider.current() check upstream.
    # Removed 2026-05-04: previously fell back to retired config-literal capital;
    # see docs/operations/task_2026-05-01_bankroll_truth_chain/.
    bankroll = float(getattr(metadata_state, "bankroll", 0.0) or 0.0)
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
        "capital_source": "dual_source_blended",
        "consistency_lock": "pass" if len(positions) == len(metadata_positions) else "mismatched",
        # B053: expose both source counts so callers can diff explicitly
        # rather than rely on a single boolean lock.
        "metadata_position_count": len(metadata_positions),
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

    # P0-A cutover-day guard (followup_design.md §6.2, §7 hazard #3):
    # Pre-cutover risk_state rows could store config-literal capital plus PnL as
    # `effective_bankroll`. After cutover, `effective_bankroll` is the real
    # on-chain wallet. Without this guard, trailing-loss math could compare
    # different economic objects and trigger false RED → force_exit_review.
    # Only rows tagged `bankroll_truth_source="polymarket_wallet"` are eligible
    # references. Old rows (no field, or any other value) are filtered out.
    if str(details.get("bankroll_truth_source") or "") != "polymarket_wallet":
        return None

    initial_bankroll = _coerce_finite_float(details.get("initial_bankroll"))
    effective_bankroll = _coerce_finite_float(details.get("effective_bankroll"))
    if initial_bankroll is None or effective_bankroll is None:
        return None

    # Definition A (followup_design.md §2.1): post-cutover effective_bankroll
    # equals initial_bankroll (= wallet_balance_usd) — no PnL math added in.
    # `total_pnl` may still be present in details_json for analytics, but it is
    # NOT the equity formula. Keep the legacy tolerance check on
    # (initial_bankroll == effective_bankroll) so a malformed row is rejected.
    total_pnl = _coerce_finite_float(details.get("total_pnl")) or 0.0
    if abs(initial_bankroll - effective_bankroll) > TRAILING_LOSS_ROW_TOLERANCE_USD:
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
    total_rows = int(
        risk_conn.execute("SELECT COUNT(*) FROM risk_state").fetchone()[0] or 0
    )
    if total_rows == 0:
        return {
            "status": "no_reference_row",
            "source": TRAILING_LOSS_SOURCE_DEGRADED,
            "reference": None,
        }

    # SF7 fix (2026-05-04): pre-filter to post-cutover rows at the SQL layer.
    # Without this, the LIMIT-100 window can be dominated by rows that lack the
    # top-level `bankroll_truth_source` field (transient writer regressions, or
    # error-state rows like `bankroll_provider_unavailable`). All such rows fail
    # `_risk_state_reference_from_row` line 196, so the for-loop falls through to
    # `inconsistent_history` and the daemon stays DATA_DEGRADED indefinitely —
    # even when 918 post-cutover rows exist further back in history. Filtering at
    # the SQL layer means: if no post-cutover row is old enough we get the proper
    # `insufficient_history` (already bootstrap-allowlisted to GREEN), and only
    # rows that COULD pass trustworthiness reach the for-loop. Architectural
    # `inconsistent_history` signal is preserved for genuine post-cutover
    # disagreement (initial != effective), which is what the lines 302-304
    # comment intends to gate.
    candidate_rows = risk_conn.execute(
        """
        SELECT id, checked_at, details_json
        FROM risk_state
        WHERE checked_at <= ?
          AND json_extract(details_json, '$.bankroll_truth_source') = 'polymarket_wallet'
        ORDER BY checked_at DESC, id DESC
        LIMIT 100
        """,
        (cutoff,),
    ).fetchall()
    if not candidate_rows:
        return {
            "status": "insufficient_history",
            "source": TRAILING_LOSS_SOURCE_DEGRADED,
            "reference": None,
        }

    for row in candidate_rows:
        if reference := _risk_state_reference_from_row(row):
            ref_dt = datetime.fromisoformat(reference["checked_at"].replace("Z", "+00:00"))
            staleness = cutoff_dt - ref_dt
            if staleness > TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE:
                status = "stale_reference"
            else:
                status = "ok"
            
            return {
                "status": status,
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

    # Cold-start handling (operator directive 2026-05-01 + architecture review):
    # `_trailing_loss_reference` returns "no_reference_row" / "insufficient_history"
    # on a fresh deploy — risk_state has no rows older than the lookback window
    # (e.g., 24h). The previous behaviour mapped both states to DATA_DEGRADED,
    # which the cycle reads as "block all entries" — making every fresh deploy
    # permanently undeployable until someone manually seeds risk_state. That
    # was a deadlock by design, not the structural intent: when there is no
    # history yet, no loss can have occurred against it. The right level is
    # GREEN with an explicit `bootstrap_no_history` annotation that downstream
    # observability can show. `inconsistent_history` is a different beast — it
    # means rows exist but disagree, which IS a data integrity signal worth
    # gating on, so it stays DATA_DEGRADED.
    if status in ("no_reference_row", "insufficient_history"):
        return {
            "loss": 0.0,
            "level": RiskLevel.GREEN,
            "degraded": False,
            "status": f"bootstrap_no_history:{status}",
            "source": str(reference_info["source"]),
            "reference": None,
        }
    if status not in ("ok", "stale_reference") or reference is None:
        return {
            "loss": 0.0,
            "level": RiskLevel.DATA_DEGRADED,
            "degraded": True,
            "status": f"degraded:{status}",
            "source": str(reference_info["source"]),
            "reference": None,
        }
    reference_equity = float(reference["effective_bankroll"])
    loss = round(max(0.0, reference_equity - current_equity), 2)
    level_from_loss = (
        RiskLevel.RED
        if loss > float(initial_bankroll) * float(threshold_pct)
        else RiskLevel.GREEN
    )
    
    # Staleness handling (operator directive 2026-05-01 + cold-start follow-up):
    # `stale_reference` = we have a reference row but it's older than the
    # staleness tolerance (default 2h beyond the lookback cutoff). The previous
    # behaviour mapped this to DATA_DEGRADED whenever loss didn't already trip
    # RED — meaning every fresh restart after a long unload window saw the
    # 17-hour-old reference, flagged stale, and blocked entries. This is
    # symmetric to the `no_reference_row` cold-start: the reference is from
    # before the latest deploy and doesn't reflect current state. If there's
    # no demonstrable loss against it (level_from_loss == GREEN), treat as
    # bootstrap and unblock. RED stays RED — a stale reference showing a real
    # loss is still a loss signal worth honouring.
    if status == "stale_reference":
        if level_from_loss == RiskLevel.RED:
            level = RiskLevel.RED
            is_degraded = True
        else:
            level = RiskLevel.GREEN
            is_degraded = False
            status = "bootstrap_stale_reference"
    else:
        level = level_from_loss
        is_degraded = False
    return {
        "loss": loss,
        "level": level,
        "degraded": is_degraded,
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
        if not row.get("metric_ready", False):
            continue
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


def _current_mode_realized_exits(
    conn: sqlite3.Connection,
    *,
    settlement_rows: list[dict] | None = None,
    env: str | None = None,
) -> tuple[list[dict], str, bool]:
    """Returns (exits, source_name, degraded)."""
    if conn is None:
        return [], "none", False
    if settlement_rows is not None:
        exits = _canonical_recent_exits_from_settlement_rows(settlement_rows)
        degraded = any(bool(row.get("is_degraded", False)) for row in settlement_rows)
        return exits, "authoritative_settlement_rows", degraded and not exits

    outcome_fact_available = True
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
        outcome_fact_available = False
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
            False,
        )
    if outcome_fact_available:
        # Table exists but is empty — valid empty result, not degradation
        return [], "outcome_fact", False

    # Degradation: outcome_fact unavailable, falling back to chronicle
    logger.warning("outcome_fact unavailable — degrading realized exits to chronicle")
    chronicle_env = str(env or get_mode()).strip()
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
            (chronicle_env, chronicle_env),
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
            True,
        )

    return [], "none", False


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


def _entry_execution_summary(conn: sqlite3.Connection, *, limit: int = 200) -> dict:
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

    Connection discipline (2026-05-10 leak fix): zeus_conn and risk_conn are
    opened once and closed in a finally block. Prior to this fix, any
    exception mid-tick left both handles open; with a 60s tick and recurring
    errors this produced 51+ accumulated zeus-world.db-wal reader handles
    (observed on PID 18538), blocking all WAL writers (data-ingest, live-trading).
    """
    zeus_conn = _get_runtime_trade_connection()
    risk_conn = get_connection(RISK_DB_PATH, write_class="live")
    try:
        init_risk_db(risk_conn)

        previous_row = risk_conn.execute(
            "SELECT level FROM risk_state ORDER BY checked_at DESC LIMIT 1"
        ).fetchone()
        previous_level = RiskLevel(previous_row["level"]) if previous_row else None

        thresholds = settings["riskguard"]
        portfolio, portfolio_truth = _load_riskguard_portfolio_truth(zeus_conn)

        # P0-A bankroll truth chain (architect memo §7): trailing-loss math must
        # use the on-chain wallet, NOT the config constant routed through
        # PortfolioState.bankroll. When the wallet is unreachable AND no fresh
        # cache exists, fail-closed at DATA_DEGRADED rather than silently falling
        # back to retired config-literal capital.
        bankroll_of_record = bankroll_provider.current()
        if bankroll_of_record is None:
            now_ts = datetime.now(timezone.utc).isoformat()
            risk_conn.execute(
                """
                INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)
                VALUES (?, NULL, NULL, NULL, ?, ?, 0)
                """,
                (
                    RiskLevel.DATA_DEGRADED.value,
                    json.dumps({
                        "status": "bankroll_provider_unavailable",
                        "bankroll_truth": {
                            "source": "polymarket_wallet",
                            "value_usd": None,
                            "fetched_at": None,
                            "staleness_seconds": None,
                            "cached": False,
                            "reason": "wallet query failed and no fresh cache",
                        },
                    }),
                    now_ts,
                ),
            )
            risk_conn.commit()
            logger.error(
                "RiskGuard tick fail-closed: bankroll_provider unavailable (no fresh cache)",
            )
            return RiskLevel.DATA_DEGRADED

        current_bankroll_usd = float(bankroll_of_record.value_usd)
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

        realized_exits, realized_truth_source, realized_degraded = _current_mode_realized_exits(
            zeus_conn,
            settlement_rows=settlement_rows,
        )
        portfolio = replace(portfolio, recent_exits=realized_exits)

        p_forecasts = [float(r["p_posterior"]) for r in metric_ready_rows]
        outcomes = [int(r["outcome"]) for r in metric_ready_rows]
        strategy_settlement_summary = _strategy_settlement_summary(metric_ready_rows)
        entry_execution_summary = _entry_execution_summary(zeus_conn)
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

        if total_unrealized_pnl == 0.0 and strategy_health_snapshot.get("status") in ("missing_table", "empty", "fresh", "stale"):
            # Fallback for unrealized PnL
            total_unrealized_pnl = sum(float(getattr(p, "unrealized_pnl", 0.0)) for p in getattr(portfolio, "positions", []))

        total_pnl = total_realized_pnl + total_unrealized_pnl
        settlement_authority_missing_tables = list(
            strategy_health_refresh.get("settlement_authority_missing_tables", [])
        )
        if settlement_authority_missing_tables:
            realized_degraded = True

        # P0-A correction (followup_design.md §2.1, §7 Definition A):
        # current_equity = wallet_balance_usd ONLY. Do NOT add total_pnl — realized
        # PnL is already in the on-chain wallet (cash-settled exits move balance);
        # adding it again double-counts. Unrealized PnL is not live bankroll
        # authority and is excluded from equity by definition. The retired path used
        # config-literal capital plus total_pnl, which was a synthetic equity object.
        # Now: wallet (real, no math).
        current_total_value = round(current_bankroll_usd, 2)
        daily_loss_snapshot = _trailing_loss_snapshot(
            risk_conn,
            now=now,
            lookback=timedelta(hours=24),
            current_equity=current_total_value,
            initial_bankroll=current_bankroll_usd,
            threshold_pct=float(thresholds["max_daily_loss_pct"]),
        )
        weekly_loss_snapshot = _trailing_loss_snapshot(
            risk_conn,
            now=now,
            lookback=timedelta(days=7),
            current_equity=current_total_value,
            initial_bankroll=current_bankroll_usd,
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
                # P0-A: this field is now the on-chain wallet snapshot used as
                # equity base for trailing-loss math (architect memo §7), NOT the
                # config-constant fiction the legacy field name implies.
                "initial_bankroll": round(current_bankroll_usd, 2),
                # Cutover-day guard (followup_design.md §6.2, §7 hazard #3):
                # this provenance marker tells `_trailing_loss_reference` to skip
                # pre-cutover rows whose `effective_bankroll` came from retired
                # config-literal capital.
                # New rows after this code lands carry "polymarket_wallet"; old rows
                # have no `bankroll_truth_source` field at all → filtered out.
                "bankroll_truth_source": "polymarket_wallet",
                "bankroll_truth": {
                    "value_usd": round(current_bankroll_usd, 2),
                    "source": bankroll_of_record.source,
                    "authority": bankroll_of_record.authority,
                    "fetched_at": bankroll_of_record.fetched_at,
                    "staleness_seconds": round(float(bankroll_of_record.staleness_seconds), 3),
                    "cached": bool(bankroll_of_record.cached),
                },
                "daily_baseline_total": round(portfolio.daily_baseline_total, 2),
                "weekly_baseline_total": round(portfolio.weekly_baseline_total, 2),
                "realized_pnl": round(total_realized_pnl, 2),
                "realized_pnl_source": "strategy_health.realized_pnl_30d",
                "realized_pnl_window_days": 30,
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
                "realized_degraded": realized_degraded,
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
                "strategy_health_settlement_authority_missing_tables": settlement_authority_missing_tables,
                "strategy_health_omitted_fields": list(strategy_health_refresh.get("omitted_fields", [])),
                "strategy_health_snapshot_status": strategy_health_snapshot["status"],
                "strategy_health_stale_strategy_keys": list(strategy_health_snapshot.get("stale_strategy_keys", [])),
            }),
            now,
            force_exit_review,
        ))
        zeus_conn.commit()
        risk_conn.commit()

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
            elif level == RiskLevel.DATA_DEGRADED:
                if daily_loss_level == RiskLevel.DATA_DEGRADED:
                    alert_warning("Daily Loss Monitoring", 0.0, 0.0, detail="DATA_DEGRADED: Missing trailing loss baseline")
                if weekly_loss_level == RiskLevel.DATA_DEGRADED:
                    alert_warning("Weekly Loss Monitoring", 0.0, 0.0, detail="DATA_DEGRADED: Missing trailing loss baseline")
        except Exception as exc:
            logger.warning("Discord alert emission failed: %s", exc)

        if level != RiskLevel.GREEN:
            logger.warning("RiskGuard level: %s (storage_source=%s, Brier=%.3f, Accuracy=%.1f%%)",
                           level.value, settlement_storage_source, b_score, d_accuracy * 100)

        return level
    finally:
        zeus_conn.close()
        risk_conn.close()


def tick_with_portfolio(portfolio: PortfolioState) -> RiskLevel:
    """DT#6 graceful-degradation entry: run one tick with a pre-loaded PortfolioState.

    Callers that have already checked portfolio.authority can pass the degraded
    state here. If authority != 'canonical_db', new-entry paths are suppressed
    but monitor / exit / reconciliation lanes run read-only.

    Connection discipline: both connections closed in finally so exceptions
    never leave dangling handles (same leak fix as tick(), 2026-05-10).
    """
    risk_conn = get_connection(RISK_DB_PATH, write_class="live")
    zeus_conn = _get_runtime_trade_connection()
    try:
        init_risk_db(risk_conn)

        now = datetime.now(timezone.utc).isoformat()

        if portfolio.authority != "canonical_db":
            logger.warning(
                "tick_with_portfolio: portfolio authority=%r (degraded) — new-entry paths suppressed",
                portfolio.authority,
            )

        thresholds = settings["riskguard"]
        settlement_rows = query_authoritative_settlement_rows(zeus_conn, limit=50)

        # P0-A second callsite (followup_design.md §2.4, §7 hazard #2):
        # tick_with_portfolio is the graceful-degradation entry used by callers
        # that have already loaded a portfolio. Trailing-loss math here was
        # reading `portfolio.bankroll` (= config-constant fiction). Same DEF A
        # rewire as tick(): on-chain wallet for both equity AND threshold base,
        # no PnL math added. Fail-closed at DATA_DEGRADED if wallet unreachable.
        bankroll_of_record = bankroll_provider.current()
        if bankroll_of_record is None:
            logger.error(
                "RiskGuard tick_with_portfolio fail-closed: bankroll_provider unavailable",
            )
            return RiskLevel.DATA_DEGRADED

        current_bankroll_usd = float(bankroll_of_record.value_usd)
        current_equity = current_bankroll_usd
        initial_bankroll = current_bankroll_usd

        daily_loss_snapshot = _trailing_loss_snapshot(
            risk_conn,
            now=now,
            lookback=timedelta(hours=24),
            current_equity=current_equity,
            initial_bankroll=initial_bankroll,
            threshold_pct=float(thresholds["max_daily_loss_pct"]),
        )
        weekly_loss_snapshot = _trailing_loss_snapshot(
            risk_conn,
            now=now,
            lookback=timedelta(days=7),
            current_equity=current_equity,
            initial_bankroll=initial_bankroll,
            threshold_pct=float(thresholds["max_weekly_loss_pct"]),
        )

        daily_loss_level = daily_loss_snapshot["level"]
        weekly_loss_level = weekly_loss_snapshot["level"]

        level = overall_level(
            RiskLevel.DATA_DEGRADED if portfolio.portfolio_loader_degraded else RiskLevel.GREEN,
            RiskLevel.GREEN,
            RiskLevel.GREEN,
            RiskLevel.GREEN,
            daily_loss_level,
            weekly_loss_level,
        )

        return level
    finally:
        zeus_conn.close()
        risk_conn.close()


def get_current_level() -> RiskLevel:
    """Read current risk level from risk_state.db.

    R4: Fail-closed — if DB error or stale (>5 min), return RED.
    """
    try:
        conn = get_connection(RISK_DB_PATH, write_class="live")
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
        conn = get_connection(RISK_DB_PATH, write_class="live")
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

    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()

    while True:
        try:
            level = tick()
            logger.info("Tick complete: %s", level.value)
        except Exception as e:
            logger.error("RiskGuard tick failed: %s", e)
        time.sleep(60)
