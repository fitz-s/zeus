#!/usr/bin/env python3
# Lifecycle: created=2026-03-30; last_reviewed=2026-08-11; last_reused=2026-08-11
# Purpose: Prove forward-cohort realized capital from current-law canonical fill and close facts.
# Reuse: Require explicit UTC --since; never include earlier portfolio history.
"""Read-only forward realized-capital audit.

The audit deliberately requires a cohort boundary.  It does not infer profit
from marks, model EV, commands without fills, or historical portfolio totals.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.riskguard.riskguard import (
    _day0_live_realized_capital_curve,
    _qkernel_live_realized_capital_curve,
)
from src.state.db import get_trade_connection_read_only

_CURRENT_STRATEGIES = frozenset(
    {"day0_nowcast_entry", "forecast_qkernel_entry"}
)
_CURRENT_DECISION_LAW = "predicted_bin_ev_v1"


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _cohort_activity(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    as_of: datetime,
) -> dict[str, object]:
    since_iso = since.isoformat()
    as_of_iso = as_of.isoformat()
    command_counts = {
        f"{str(row[0])}:{str(row[1])}": int(row[2])
        for row in conn.execute(
            "SELECT intent_kind,state,COUNT(*) FROM venue_commands "
            "WHERE created_at>=? AND created_at<=? "
            "GROUP BY intent_kind,state ORDER BY intent_kind,state",
            (since_iso, as_of_iso),
        ).fetchall()
    }
    fill_rows = conn.execute(
        "SELECT ef.order_role,COUNT(DISTINCT ef.command_id) "
        "FROM execution_fact AS ef "
        "JOIN venue_commands AS vc ON vc.command_id=ef.command_id "
        "WHERE ef.filled_at>=? AND ef.filled_at<=? "
        "AND vc.created_at>=? AND vc.created_at<=? "
        "AND lower(COALESCE(ef.terminal_exec_status,''))='filled' "
        "GROUP BY ef.order_role ORDER BY ef.order_role",
        (since_iso, as_of_iso, since_iso, as_of_iso),
    ).fetchall()
    filled_commands_by_role = {str(row[0]): int(row[1]) for row in fill_rows}
    fact_coverage = conn.execute(
        "WITH filled AS ("
        "SELECT DISTINCT ef.command_id FROM execution_fact AS ef "
        "JOIN venue_commands AS vc ON vc.command_id=ef.command_id "
        "WHERE ef.filled_at>=? AND ef.filled_at<=? "
        "AND vc.created_at>=? AND vc.created_at<=? "
        "AND lower(COALESCE(ef.terminal_exec_status,''))='filled'"
        ") "
        "SELECT COUNT(*),SUM(CASE WHEN EXISTS ("
        "SELECT 1 FROM venue_order_facts AS vof "
        "WHERE vof.command_id=filled.command_id AND upper(vof.state)='MATCHED'"
        ") THEN 1 ELSE 0 END) FROM filled",
        (since_iso, as_of_iso, since_iso, as_of_iso),
    ).fetchone()
    cohort_positions = conn.execute(
        "SELECT DISTINCT pc.position_id,pc.strategy_key,pc.decision_law_id "
        "FROM position_current AS pc "
        "JOIN execution_fact AS ef ON ef.position_id=pc.position_id "
        "JOIN venue_commands AS vc ON vc.command_id=ef.command_id "
        "WHERE ef.order_role='entry' AND ef.filled_at>=? AND ef.filled_at<=? "
        "AND vc.created_at>=? AND vc.created_at<=? "
        "AND lower(COALESCE(ef.terminal_exec_status,''))='filled'",
        (since_iso, as_of_iso, since_iso, as_of_iso),
    ).fetchall()
    preboundary_entry_fill_count = int(
        conn.execute(
            "SELECT COUNT(DISTINCT ef.command_id) "
            "FROM execution_fact AS ef "
            "JOIN venue_commands AS vc ON vc.command_id=ef.command_id "
            "WHERE ef.order_role='entry' AND ef.filled_at>=? AND ef.filled_at<=? "
            "AND vc.created_at<? "
            "AND lower(COALESCE(ef.terminal_exec_status,''))='filled'",
            (since_iso, as_of_iso, since_iso),
        ).fetchone()[0]
        or 0
    )
    unclassified = [
        {
            "position_id": str(row[0] or ""),
            "strategy_key": str(row[1] or ""),
            "decision_law_id": str(row[2] or ""),
        }
        for row in cohort_positions
        if str(row[1] or "") not in _CURRENT_STRATEGIES
        or str(row[2] or "") != _CURRENT_DECISION_LAW
    ]
    filled_command_count = int(fact_coverage[0] or 0) if fact_coverage else 0
    chain_matched_fact_count = int(fact_coverage[1] or 0) if fact_coverage else 0
    return {
        "command_counts": command_counts,
        "filled_commands_by_role": filled_commands_by_role,
        "filled_command_count": filled_command_count,
        "chain_matched_fact_count": chain_matched_fact_count,
        "chain_fact_coverage_complete": (
            chain_matched_fact_count == filled_command_count
        ),
        "entry_filled_position_count": len(cohort_positions),
        "preboundary_entry_fill_count": preboundary_entry_fill_count,
        "unclassified_filled_position_count": len(unclassified),
        "unclassified_filled_positions": unclassified,
    }


def _forward_capital_summary(
    *,
    activity: dict[str, object],
    curves: Sequence[dict[str, object]],
) -> dict[str, object]:
    rows = [row for curve in curves for row in list(curve.get("curve") or [])]
    rows.sort(
        key=lambda row: (
            str(row.get("realized_at") or ""),
            str(row.get("position_id") or ""),
        )
    )
    cumulative = 0.0
    combined_curve: list[dict[str, object]] = []
    for row in rows:
        cumulative += float(row["net_realized_pnl_usd"])
        combined_curve.append(
            {**row, "cumulative_net_realized_pnl_usd": round(cumulative, 6)}
        )

    degraded_curve_statuses = {
        "capital_truth_unavailable",
        "capital_truth_degraded",
    }
    curve_truth_complete = not any(
        str(curve.get("status") or "") in degraded_curve_statuses
        for curve in curves
    )
    chain_truth_complete = activity.get("chain_fact_coverage_complete") is True
    attribution_complete = (
        int(activity.get("unclassified_filled_position_count") or 0) == 0
        and int(activity.get("preboundary_entry_fill_count") or 0) == 0
    )
    capital_truth_complete = (
        curve_truth_complete and chain_truth_complete and attribution_complete
    )
    realized_count = len(combined_curve)
    net_pnl = sum(float(row["net_realized_pnl_usd"]) for row in combined_curve)
    gross_pnl = sum(float(row["gross_realized_pnl_usd"]) for row in combined_curve)
    fee_bound = sum(float(row["fee_bound_usd"]) for row in combined_curve)
    realized_capital = sum(
        float(row["capital_committed_usd"]) for row in combined_curve
    )
    capital_committed = sum(
        float(curve.get("capital_committed_usd") or 0.0) for curve in curves
    )
    win_count = sum(float(row["net_realized_pnl_usd"]) > 0.0 for row in combined_curve)
    loss_count = sum(float(row["net_realized_pnl_usd"]) < 0.0 for row in combined_curve)
    flat_count = realized_count - win_count - loss_count
    observed_gain = capital_truth_complete and realized_count > 0 and net_pnl > 0.0

    if not capital_truth_complete:
        status = "capital_truth_degraded"
        reason = "CAPITAL_TRUTH_INCOMPLETE"
    elif realized_count == 0:
        status = (
            "probation_in_flight"
            if int(activity.get("entry_filled_position_count") or 0) > 0
            else "awaiting_current_law_fills"
        )
        reason = "NO_REALIZED_POSITIONS"
    elif net_pnl > 0.0:
        status = "positive_observed"
        reason = "POSITIVE_NET_REALIZED_PNL"
    else:
        status = "nonpositive_observed"
        reason = "NET_REALIZED_PNL_NONPOSITIVE"

    return {
        "status": status,
        "capital_truth_complete": capital_truth_complete,
        "capital_gain_proven": observed_gain,
        "capital_gain_proof_reason": reason,
        "robust_capital_gain_proven": False,
        "robustness_reason": "INDEPENDENT_CLUSTER_STRENGTH_NOT_ESTABLISHED",
        "filled_position_count": sum(
            int(curve.get("filled_position_count") or 0) for curve in curves
        ),
        "open_position_count": sum(
            int(curve.get("open_position_count") or 0) for curve in curves
        ),
        "realized_position_count": realized_count,
        "settled_position_count": sum(
            str(row.get("close_type") or "") == "SETTLED" for row in combined_curve
        ),
        "early_exit_position_count": sum(
            str(row.get("close_type") or "") == "EXIT_ORDER_FILLED"
            for row in combined_curve
        ),
        "win_count": win_count,
        "loss_count": loss_count,
        "flat_count": flat_count,
        "win_rate": round(win_count / realized_count, 6) if realized_count else None,
        "capital_committed_usd": round(capital_committed, 6),
        "open_capital_committed_usd": round(
            max(0.0, capital_committed - realized_capital),
            6,
        ),
        "realized_capital_committed_usd": round(realized_capital, 6),
        "gross_realized_pnl_usd": round(gross_pnl, 6),
        "fee_bound_usd": round(fee_bound, 6),
        "net_realized_pnl_usd": round(net_pnl, 6),
        "return_on_realized_capital": (
            round(net_pnl / realized_capital, 6)
            if realized_capital > 0.0
            else None
        ),
        "curve": combined_curve,
    }


def run_audit(
    *,
    since: datetime,
    as_of: datetime | None = None,
    cohort_id: str | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, object]:
    if since.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    raw_as_of = as_of or datetime.now(timezone.utc)
    if raw_as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    evaluated_at = raw_as_of.astimezone(timezone.utc)
    since_utc = since.astimezone(timezone.utc)
    if since_utc >= evaluated_at:
        raise ValueError("since must precede as_of")
    window_days = (evaluated_at - since_utc).total_seconds() / 86400.0
    owns_connection = connection is None
    conn = connection or get_trade_connection_read_only()
    try:
        activity = _cohort_activity(conn, since=since_utc, as_of=evaluated_at)
        curves = (
            _day0_live_realized_capital_curve(
                conn,
                window_days=window_days,
                as_of=evaluated_at,
            ),
            _qkernel_live_realized_capital_curve(
                conn,
                window_days=window_days,
                as_of=evaluated_at,
            ),
        )
    finally:
        if owns_connection:
            conn.close()
    return {
        "schema_version": 2,
        "cohort_id": str(cohort_id or "forward_current_law"),
        "since": since_utc.isoformat(),
        "as_of": evaluated_at.isoformat(),
        "activity": activity,
        "capital": _forward_capital_summary(activity=activity, curves=curves),
        "strategy_curves": {
            str(curve["strategy_key"]): curve for curve in curves
        },
        "source": "canonical_trade_db_read_only",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        required=True,
        type=_parse_utc,
        help="inclusive UTC cohort boundary (ISO-8601 with offset)",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_utc,
        default=None,
        help="inclusive audit clock; defaults to current UTC time",
    )
    parser.add_argument("--cohort-id", default="forward_current_law")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_audit(
            since=args.since,
            as_of=args.as_of,
            cohort_id=args.cohort_id,
        )
    except ValueError as exc:
        _parser().error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
