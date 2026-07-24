#!/usr/bin/env python3
# Lifecycle: created=2026-07-09; last_reviewed=2026-07-15; last_reused=2026-07-15
# Purpose: read-only observation for EDLI YES/NO selection skew from canonical order events.
# Authority basis: AGENTS.md live-money proof gates; operator focus on absent high-quality YES fills.
"""Audit recent EDLI YES/NO candidate selection skew from canonical event payloads.

This observation reads ``edli_live_order_events`` in read-only SQLite mode. It
does not authorize live trading, mutate DB truth, or contact venues.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.decision.family_decision_engine import (
    roi_frontier_growth_density,
    roi_frontier_min_payoff_q_lcb,
    roi_frontier_min_profit_lcb_usd,
)


DEFAULT_TRADE_DB = ROOT / "state" / "zeus_trades.db"
DEFAULT_FORECAST_DB = ROOT / "state" / "zeus-forecasts.db"
CHAIN_EVENT_TYPES = (
    "SubmitPlanBuilt",
    "VenueSubmitAcknowledged",
    "UserTradeObserved",
)
CHAIN_LOOKUP_EVENT_TYPES = (*CHAIN_EVENT_TYPES, "PreSubmitRevalidated", "SubmitRejected")


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
        if math.isnan(parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _metric(candidate: dict[str, Any], name: str) -> float | None:
    qkernel = _nested_dict(candidate.get("qkernel_execution_economics"))
    payoff = _nested_dict(candidate.get("payoff_vector"))
    entry = _nested_dict(candidate.get("entry_economics"))
    aliases = {
        "cost": ("cost", "price", "c_fee_adjusted", "ask", "best_ask"),
        "delta_u_at_min": ("delta_u_at_min", "min_delta_u"),
        "edge_lcb": ("edge_lcb", "edge_lcb_native"),
        "optimal_delta_u": ("optimal_delta_u", "delta_u", "expected_utility_delta"),
        "q_lcb": ("payoff_q_lcb", "q_lcb_5pct", "q_lcb"),
        "q_point": ("payoff_q_point", "q_dot_payoff", "q_point", "p_posterior"),
        "robust_trade_score": ("robust_trade_score", "trade_score"),
        "stake": ("optimal_stake_usd", "kelly_size_usd", "stake", "size_usd", "notional_usd"),
    }
    for key in aliases[name]:
        for source in (candidate, qkernel, payoff, entry):
            if key in source:
                parsed = _float(source.get(key))
                if parsed is not None:
                    return parsed
    return None


def _candidate_label(candidate: dict[str, Any]) -> str | None:
    for key in ("bin_label", "market_title", "label"):
        value = candidate.get(key)
        if value:
            return str(value)
    return None


def _empty_direction_counts() -> dict[str, int]:
    return {"buy_yes": 0, "buy_no": 0, "unknown": 0, "total": 0}


def _empty_confirmed_trade_counts() -> dict[str, int]:
    return {
        "buy_yes": 0,
        "buy_no": 0,
        "unknown": 0,
        "total": 0,
        "confirmed": 0,
        "missing_price": 0,
        "missing_size": 0,
        "missing_pre_submit": 0,
        "pre_submit_side_not_armed": 0,
    }


def _empty_high_quality_yes_chain() -> dict[str, Any]:
    return {
        "pre_submit_q_lcb_ge_025": 0,
        "submit_rejected": 0,
        "venue_acknowledged": 0,
        "venue_matched_or_filled": 0,
        "position_entry_filled": 0,
        "settled": 0,
        "settled_wins": 0,
        "settled_losses": 0,
        "user_trade_observed_confirmed": 0,
        "day0_observed_boundary_pre_submit": 0,
        "day0_observed_boundary_venue_filled": 0,
        "day0_observed_boundary_settled_losses": 0,
        "q_lcb_guard_basis_counts": {},
        "selection_guard_basis_counts": {},
        "q_lcb_calibration_source_counts": {},
        "selection_guard_n_buckets": {},
        "samples": [],
    }


def _payload_positive_number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        parsed = _float(payload.get(key))
        if parsed is not None and parsed > 0.0:
            return parsed
    return None


def _is_confirmed_user_trade(payload: dict[str, Any]) -> bool:
    status_values = {
        str(payload.get("trade_status") or "").strip().upper(),
        str(payload.get("fill_authority_state") or "").strip().upper(),
    }
    if not ({"CONFIRMED", "FILL_CONFIRMED"} & status_values):
        return False
    if _payload_positive_number(payload, "fill_price", "avg_fill_price", "price") is None:
        return False
    if _payload_positive_number(payload, "filled_size", "size") is None:
        return False
    return bool(str(payload.get("trade_id") or payload.get("venue_order_id") or "").strip())


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row["name"]) == column for row in rows)


def _direction_from_payload(
    payload: dict[str, Any],
    intent_direction: dict[str, str],
) -> str:
    direction = payload.get("direction")
    if direction in ("buy_yes", "buy_no"):
        return str(direction)
    final_intent_id = payload.get("final_intent_id")
    if final_intent_id is not None:
        mapped = intent_direction.get(str(final_intent_id))
        if mapped in ("buy_yes", "buy_no"):
            return mapped
    return "unknown"


def _positive_objective(candidate: dict[str, Any]) -> bool:
    for name in ("optimal_delta_u", "delta_u_at_min", "robust_trade_score"):
        value = _metric(candidate, name)
        if value is not None and value > 0:
            return True
    return False


def _side(candidate: dict[str, Any]) -> str | None:
    qkernel = _nested_dict(candidate.get("qkernel_execution_economics"))
    side = qkernel.get("side")
    if side in ("YES", "NO"):
        return str(side)
    direction = str(candidate.get("direction") or "").strip().lower()
    if direction == "buy_yes":
        return "YES"
    if direction == "buy_no":
        return "NO"
    return None


def _roi_frontier_diag(candidate: dict[str, Any]) -> dict[str, Any]:
    side = _side(candidate)
    cost = _metric(candidate, "cost")
    edge_lcb = _metric(candidate, "edge_lcb")
    q_lcb = _metric(candidate, "q_lcb")
    stake = _metric(candidate, "stake")
    delta_u_at_min = _metric(candidate, "delta_u_at_min")
    min_payoff_q_lcb = (
        roi_frontier_min_payoff_q_lcb(side=side, cost=float(cost))
        if cost is not None
        else None
    )
    min_profit_lcb_usd = roi_frontier_min_profit_lcb_usd()
    growth_density = (
        roi_frontier_growth_density(
            cost=float(cost),
            edge_lcb=float(edge_lcb),
            payoff_q_lcb=float(q_lcb),
        )
        if cost is not None and edge_lcb is not None and q_lcb is not None
        else None
    )
    edge_roi_lcb = (
        float(edge_lcb) / float(cost)
        if cost is not None and edge_lcb is not None and float(cost) > 0.0
        else None
    )
    profit_lcb_usd = (
        float(stake) * float(edge_roi_lcb)
        if stake is not None and edge_roi_lcb is not None
        else None
    )
    reasons: list[str] = []
    if stake is None or not math.isfinite(float(stake)) or float(stake) <= 0.0:
        reasons.append("stake_not_positive")
    if delta_u_at_min is None or not math.isfinite(float(delta_u_at_min)) or float(delta_u_at_min) <= 0.0:
        reasons.append("delta_u_at_min_not_positive")
    if q_lcb is None or min_payoff_q_lcb is None or float(q_lcb) < float(min_payoff_q_lcb):
        reasons.append("payoff_q_lcb_below_side_floor")
    if profit_lcb_usd is None or float(profit_lcb_usd) < float(min_profit_lcb_usd):
        reasons.append("profit_lcb_below_floor")
    if growth_density is None or not math.isfinite(float(growth_density)):
        reasons.append("growth_density_not_finite")
    return {
        "side": side,
        "cost": cost,
        "edge_roi_lcb": edge_roi_lcb,
        "growth_density": growth_density,
        "min_payoff_q_lcb": min_payoff_q_lcb,
        "min_profit_lcb_usd": min_profit_lcb_usd,
        "profit_lcb_usd": profit_lcb_usd,
        "reasons": reasons,
        "roi_frontier_useful": not reasons,
    }


def _roi_frontier_diag_at_floor(
    candidate: dict[str, Any],
    *,
    min_payoff_q_lcb: float,
) -> dict[str, Any]:
    diag = dict(_roi_frontier_diag(candidate))
    q_lcb = _metric(candidate, "q_lcb")
    reasons = [
        reason
        for reason in diag["reasons"]
        if reason != "payoff_q_lcb_below_side_floor"
    ]
    if q_lcb is None or float(q_lcb) < float(min_payoff_q_lcb):
        reasons.append("payoff_q_lcb_below_side_floor")
    diag["min_payoff_q_lcb"] = float(min_payoff_q_lcb)
    diag["reasons"] = reasons
    diag["roi_frontier_useful"] = not reasons
    return diag


def _roi_key_from_diag(candidate: dict[str, Any], diag: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        diag["growth_density"] or -1e99,
        diag["edge_roi_lcb"] or -1e99,
        diag["profit_lcb_usd"] or -1e99,
        _metric(candidate, "optimal_delta_u") or -1e99,
    )


def _rank_value(candidate: dict[str, Any]) -> tuple[float, float, float]:
    return (
        _metric(candidate, "optimal_delta_u") or -1e99,
        _metric(candidate, "delta_u_at_min") or -1e99,
        _metric(candidate, "robust_trade_score") or -1e99,
    )


def _strategy_key(payload: dict[str, Any], audit: dict[str, Any], candidate: dict[str, Any] | None = None) -> str:
    if candidate is not None:
        value = candidate.get("strategy_key")
        if value:
            return str(value)
    return str(audit.get("strategy_key") or payload.get("strategy_key") or "missing")


def _percentiles(values: list[float], points: tuple[float, ...]) -> dict[str, float | None]:
    if not values:
        return {str(point): None for point in points}
    ordered = sorted(values)
    out: dict[str, float | None] = {}
    for point in points:
        idx = min(len(ordered) - 1, max(0, int((point / 100.0) * (len(ordered) - 1))))
        out[str(point)] = ordered[idx]
    return out


def _q_bucket(value: float | None) -> str:
    if value is None:
        return "missing"
    for lo, hi in (
        (0.0, 0.05),
        (0.05, 0.10),
        (0.10, 0.15),
        (0.15, 0.20),
        (0.20, 0.25),
        (0.25, 1.01),
    ):
        if lo <= value < hi:
            return f"[{lo:.2f},{hi:.2f})"
    return "outside"


def _empty_outcome_bucket() -> dict[str, int]:
    return {"n": 0, "wins": 0}


def _finalize_outcome_bucket(bucket: dict[str, int]) -> dict[str, float | int | None]:
    n = int(bucket["n"])
    wins = int(bucket["wins"])
    return {
        "n": n,
        "wins": wins,
        "win_rate": (wins / n) if n else None,
    }


def _candidate_condition_id(candidate: dict[str, Any]) -> str | None:
    value = candidate.get("condition_id")
    if value:
        return str(value)
    qkernel = _nested_dict(candidate.get("qkernel_execution_economics"))
    route_cost = _nested_dict(qkernel.get("route_cost"))
    value = route_cost.get("condition_id")
    return str(value) if value else None


def _market_event_for_condition(
    conn: sqlite3.Connection,
    condition_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT city, target_date, temperature_metric, condition_id, token_id,
               range_label, range_low, range_high
          FROM market_events
         WHERE condition_id = ?
         ORDER BY event_id DESC
         LIMIT 1
        """,
        (condition_id,),
    ).fetchone()


def _settlement_for_market(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT city, target_date, temperature_metric, winning_bin,
               settlement_value, settlement_unit, settled_at, authority
          FROM settlement_outcomes
         WHERE city = ?
           AND target_date = ?
           AND temperature_metric = ?
           AND authority = 'VERIFIED'
         ORDER BY datetime(settled_at) DESC, settlement_id DESC
         LIMIT 1
        """,
        (city, target_date, temperature_metric),
    ).fetchone()


def _settled_yes_won(market: sqlite3.Row, settlement: sqlite3.Row) -> bool | None:
    settlement_value = _float(settlement["settlement_value"])
    if settlement_value is None:
        return None
    low = _float(market["range_low"])
    high = _float(market["range_high"])
    if low is None and high is None:
        return None
    if low is None:
        return settlement_value <= float(high)
    if high is None:
        return settlement_value >= float(low)
    return low <= settlement_value <= high


class _SettlementLookup:
    def __init__(self, forecast_db: Path | None) -> None:
        self._forecast_db = forecast_db
        self._conn: sqlite3.Connection | None = None
        self._market_cache: dict[str, sqlite3.Row | None] = {}
        self._settlement_cache: dict[tuple[str, str, str], sqlite3.Row | None] = {}

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connection(self) -> sqlite3.Connection | None:
        if self._forecast_db is None:
            return None
        if self._conn is None:
            self._conn = _open_readonly(self._forecast_db)
        return self._conn

    def outcome_for_candidate(
        self,
        candidate: dict[str, Any],
        audit: dict[str, Any],
    ) -> dict[str, Any] | None:
        condition_id = _candidate_condition_id(candidate)
        if not condition_id:
            return None
        conn = self._connection()
        if conn is None:
            return None
        if condition_id not in self._market_cache:
            self._market_cache[condition_id] = _market_event_for_condition(
                conn,
                condition_id,
            )
        market = self._market_cache[condition_id]
        if market is None:
            return {
                "condition_id": condition_id,
                "market_found": False,
                "settlement_found": False,
                "yes_won": None,
            }
        city = str(market["city"] or audit.get("city") or "")
        target_date = str(market["target_date"] or audit.get("target_date") or "")
        temperature_metric = str(market["temperature_metric"] or audit.get("metric") or "")
        if not city or not target_date or not temperature_metric:
            return {
                "condition_id": condition_id,
                "market_found": True,
                "settlement_found": False,
                "yes_won": None,
            }
        key = (city, target_date, temperature_metric)
        if key not in self._settlement_cache:
            self._settlement_cache[key] = _settlement_for_market(
                conn,
                city=city,
                target_date=target_date,
                temperature_metric=temperature_metric,
            )
        settlement = self._settlement_cache[key]
        if settlement is None:
            return {
                "condition_id": condition_id,
                "market_found": True,
                "settlement_found": False,
                "yes_won": None,
                "city": city,
                "target_date": target_date,
                "temperature_metric": temperature_metric,
                "range_label": market["range_label"],
            }
        return {
            "condition_id": condition_id,
            "market_found": True,
            "settlement_found": True,
            "yes_won": _settled_yes_won(market, settlement),
            "city": city,
            "target_date": target_date,
            "temperature_metric": temperature_metric,
            "range_label": market["range_label"],
            "range_low": market["range_low"],
            "range_high": market["range_high"],
            "settlement_value": settlement["settlement_value"],
            "winning_bin": settlement["winning_bin"],
            "settled_at": settlement["settled_at"],
        }


def _summarize_candidate(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "label": _candidate_label(candidate),
        "cost": _metric(candidate, "cost"),
        "delta_u_at_min": _metric(candidate, "delta_u_at_min"),
        "edge_lcb": _metric(candidate, "edge_lcb"),
        "optimal_delta_u": _metric(candidate, "optimal_delta_u"),
        "q_point": _metric(candidate, "q_point"),
        "q_lcb": _metric(candidate, "q_lcb"),
        "robust_trade_score": _metric(candidate, "robust_trade_score"),
        "roi_frontier": _roi_frontier_diag(candidate),
        "stake": _metric(candidate, "stake"),
    }


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _position_won_from_settlement_payload(payload: dict[str, Any]) -> bool | None:
    """Read held-side settlement truth without treating a NO win as a loss."""
    explicit = payload.get("position_won")
    outcome = payload.get("outcome")
    if outcome in {0, 1, "0", "1"}:
        held_won = bool(int(outcome))
        if isinstance(explicit, bool) and explicit != held_won:
            return None
        return held_won
    if isinstance(explicit, bool):
        return explicit
    legacy = payload.get("won")
    return legacy if isinstance(legacy, bool) else None


def _venue_fill_evidence(
    conn: sqlite3.Connection,
    *,
    venue_order_id: str,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "venue_order_id": venue_order_id,
        "venue_fact_states": [],
        "venue_command_states": [],
        "position_ids": [],
        "position_entry_filled": False,
        "venue_matched_or_filled": False,
        "settled": False,
        "settled_won": None,
        "settled_pnl": None,
    }
    if not venue_order_id:
        return evidence
    try:
        fact_rows = conn.execute(
            """
            SELECT state, matched_size, remaining_size
              FROM venue_order_facts
             WHERE venue_order_id = ?
             ORDER BY observed_at DESC, ingested_at DESC
             LIMIT 20
            """,
            (venue_order_id,),
        ).fetchall()
    except sqlite3.Error:
        fact_rows = []
    for row in fact_rows:
        state = str(row["state"] or "").upper()
        evidence["venue_fact_states"].append(state)
        if state in {"MATCHED", "FILLED"}:
            evidence["venue_matched_or_filled"] = True

    try:
        command_rows = conn.execute(
            """
            SELECT state
              FROM venue_commands
             WHERE venue_order_id = ?
             ORDER BY updated_at DESC, created_at DESC
             LIMIT 20
            """,
            (venue_order_id,),
        ).fetchall()
    except sqlite3.Error:
        command_rows = []
    for row in command_rows:
        state = str(row["state"] or "").upper()
        evidence["venue_command_states"].append(state)
        if state in {"MATCHED", "FILLED"}:
            evidence["venue_matched_or_filled"] = True

    try:
        position_rows = conn.execute(
            """
            SELECT position_id, event_type, payload_json
              FROM position_events
             WHERE order_id = ?
             ORDER BY sequence_no
            """,
            (venue_order_id,),
        ).fetchall()
    except sqlite3.Error:
        position_rows = []
    position_ids: set[str] = set()
    for row in position_rows:
        position_id = str(row["position_id"] or "").strip()
        if position_id:
            position_ids.add(position_id)
        event_type = str(row["event_type"] or "")
        if event_type == "ENTRY_ORDER_FILLED":
            evidence["position_entry_filled"] = True
            evidence["venue_matched_or_filled"] = True
    for position_id in sorted(position_ids):
        try:
            latest = conn.execute(
                """
                SELECT phase, realized_pnl_usd
                  FROM position_current
                 WHERE position_id = ?
                 LIMIT 1
                """,
                (position_id,),
            ).fetchone()
        except sqlite3.Error:
            latest = None
        if latest is not None and str(latest["phase"] or "") == "settled":
            evidence["settled"] = True
            evidence["settled_pnl"] = _float(latest["realized_pnl_usd"])
    if position_ids:
        try:
            settled_rows = conn.execute(
                f"""
                SELECT payload_json
                  FROM position_events
                 WHERE position_id IN ({",".join("?" for _ in position_ids)})
                   AND event_type = 'SETTLED'
                 ORDER BY sequence_no DESC
                 LIMIT 1
                """,
                tuple(position_ids),
            ).fetchall()
        except sqlite3.Error:
            settled_rows = []
        if settled_rows:
            evidence["settled"] = True
            try:
                payload = json.loads(str(settled_rows[0]["payload_json"] or "{}"))
            except (TypeError, ValueError, KeyError, IndexError):
                payload = {}
            if isinstance(payload, dict):
                evidence["settled_won"] = _position_won_from_settlement_payload(
                    payload
                )
                evidence["settled_pnl"] = _float(payload.get("pnl"))
    evidence["position_ids"] = sorted(position_ids)
    return evidence


def audit_selection_skew(
    *,
    trade_db: Path = DEFAULT_TRADE_DB,
    forecast_db: Path | None = DEFAULT_FORECAST_DB,
    since: str | None = None,
    days: float = 7.0,
    sample_limit: int = 10,
) -> dict[str, Any]:
    cutoff = since
    if cutoff is None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    summary = {
        "decision_proof_rows": 0,
        "selected_buy_no": 0,
        "selected_buy_yes": 0,
        "yes_candidates": 0,
        "no_candidates": 0,
        "positive_yes_candidates": 0,
        "positive_no_candidates": 0,
        "families_with_positive_yes_and_no": 0,
        "selected_no_top_yes_objective_better": 0,
        "selected_no_top_yes_score_better_only": 0,
        "top_yes_roi_frontier_useful": 0,
        "top_no_roi_frontier_useful": 0,
        "selected_no_top_yes_roi_key_better": 0,
    }
    metric_wins: dict[str, int] = {}
    frontier_rejections: dict[str, int] = {}
    selected_strategy_counts: Counter[str] = Counter()
    yes_candidate_strategy_counts: Counter[str] = Counter()
    yes_guard_basis_counts: Counter[str] = Counter()
    yes_selection_basis_counts: Counter[str] = Counter()
    yes_selection_n_counts: Counter[str] = Counter()
    yes_q_lcbs: list[float] = []
    yes_q_points: list[float] = []
    yes_costs: list[float] = []
    yes_edge_lcbs: list[float] = []
    yes_point_evs: list[float] = []
    yes_above_side_floor = 0
    max_yes_record: dict[str, Any] | None = None
    counterfactual_floors = (0.05, 0.10, 0.15, 0.20, 0.25)
    yes_floor_counterfactual = {
        f"{floor:.2f}": {
            "top_yes_roi_frontier_useful": 0,
            "top_yes_roi_key_better_than_top_no": 0,
        }
        for floor in counterfactual_floors
    }
    objective_better_samples: list[dict[str, Any]] = []
    score_only_samples: list[dict[str, Any]] = []
    yes_outcome_summary = {
        "candidates_with_condition_id": 0,
        "with_market_event": 0,
        "with_verified_settlement": 0,
        "with_bin_outcome": 0,
        "actual_yes_wins": 0,
        "actual_win_point_ev_positive": 0,
    }
    yes_outcome_by_q_lcb_bucket: dict[str, dict[str, int]] = defaultdict(_empty_outcome_bucket)
    unique_yes_outcomes: dict[str, dict[str, Any]] = {}
    actual_win_point_ev_samples: list[dict[str, Any]] = []
    by_day: dict[str, Counter[str]] = defaultdict(Counter)
    confirmed_yes_trade_samples: list[dict[str, Any]] = []
    confirmed_yes_q_lcbs: list[float] = []
    confirmed_yes_guard_basis_counts: Counter[str] = Counter()
    high_quality_yes_chain = _empty_high_quality_yes_chain()
    high_quality_q_lcb_guard_basis_counts: Counter[str] = Counter()
    high_quality_selection_guard_basis_counts: Counter[str] = Counter()
    high_quality_q_lcb_calibration_source_counts: Counter[str] = Counter()
    high_quality_selection_guard_n_counts: Counter[str] = Counter()

    settlement_lookup = _SettlementLookup(forecast_db)
    conn = _open_readonly(trade_db)
    try:
        has_aggregate_id = _table_has_column(conn, "edli_live_order_events", "aggregate_id")
        chain_select = (
            "SELECT created_at, aggregate_id, event_type, payload_json"
            if has_aggregate_id
            else "SELECT created_at, NULL AS aggregate_id, event_type, payload_json"
        )
        rows = conn.execute(
            """
            SELECT created_at, payload_json
              FROM edli_live_order_events
             WHERE event_type = 'DecisionProofAccepted'
               AND datetime(created_at) >= datetime(?)
             ORDER BY created_at
            """,
            (cutoff,),
        ).fetchall()
        chain_event_placeholders = ", ".join("?" for _ in CHAIN_LOOKUP_EVENT_TYPES)
        chain_rows = conn.execute(
            f"""
            {chain_select}
              FROM edli_live_order_events
             WHERE event_type IN ({chain_event_placeholders})
               AND datetime(created_at) >= datetime(?)
             ORDER BY created_at
            """,
            (*CHAIN_LOOKUP_EVENT_TYPES, cutoff),
        ).fetchall()
    finally:
        conn.close()

    intent_direction: dict[str, str] = {}
    chain_payloads: list[tuple[str, str, str | None, dict[str, Any]]] = []
    pre_submit_by_aggregate: dict[str, dict[str, Any]] = {}
    submit_rejected_by_aggregate: dict[str, dict[str, Any]] = {}
    venue_order_by_aggregate: dict[str, str] = {}
    user_trade_confirmed_by_aggregate: set[str] = set()
    for row in chain_rows:
        payload = json.loads(row["payload_json"])
        event_type = str(row["event_type"])
        aggregate_id = str(row["aggregate_id"]) if row["aggregate_id"] is not None else None
        chain_payloads.append((str(row["created_at"]), event_type, aggregate_id, payload))
        if event_type == "PreSubmitRevalidated" and aggregate_id:
            pre_submit_by_aggregate[aggregate_id] = payload
        elif event_type == "SubmitRejected" and aggregate_id:
            submit_rejected_by_aggregate[aggregate_id] = payload
        elif event_type == "VenueSubmitAcknowledged" and aggregate_id:
            venue_order_id = str(payload.get("venue_order_id") or "").strip()
            if venue_order_id:
                venue_order_by_aggregate[aggregate_id] = venue_order_id
        if event_type != "SubmitPlanBuilt":
            continue
        final_intent_id = payload.get("final_intent_id")
        direction = payload.get("direction")
        if final_intent_id is not None and direction in ("buy_yes", "buy_no"):
            intent_direction[str(final_intent_id)] = str(direction)

    execution_chain = {event_type: _empty_direction_counts() for event_type in CHAIN_EVENT_TYPES}
    confirmed_user_trade_chain = _empty_confirmed_trade_counts()
    for created_at, event_type, aggregate_id, payload in chain_payloads:
        direction = _direction_from_payload(payload, intent_direction)
        if event_type not in execution_chain:
            continue
        counts = execution_chain[event_type]
        counts[direction] += 1
        counts["total"] += 1
        if event_type != "UserTradeObserved":
            continue
        fill_price = _payload_positive_number(payload, "fill_price", "avg_fill_price", "price")
        filled_size = _payload_positive_number(payload, "filled_size", "size")
        if fill_price is None:
            confirmed_user_trade_chain["missing_price"] += 1
        if filled_size is None:
            confirmed_user_trade_chain["missing_size"] += 1
        if not _is_confirmed_user_trade(payload):
            continue
        if aggregate_id:
            user_trade_confirmed_by_aggregate.add(str(aggregate_id))
        confirmed_user_trade_chain[direction] += 1
        confirmed_user_trade_chain["total"] += 1
        confirmed_user_trade_chain["confirmed"] += 1
        pre_submit = pre_submit_by_aggregate.get(str(aggregate_id or ""))
        if pre_submit is None:
            confirmed_user_trade_chain["missing_pre_submit"] += 1
            qkernel = {}
        else:
            qkernel = _nested_dict(pre_submit.get("qkernel_execution_economics"))
            if str(qkernel.get("selection_guard_basis") or "").strip() == "SIDE_NOT_ARMED":
                confirmed_user_trade_chain["pre_submit_side_not_armed"] += 1
        if direction != "buy_yes":
            continue
        q_lcb = _float(pre_submit.get("q_lcb_5pct")) if pre_submit else None
        if q_lcb is not None:
            confirmed_yes_q_lcbs.append(float(q_lcb))
        basis = str(qkernel.get("selection_guard_basis") or "missing")
        confirmed_yes_guard_basis_counts[basis] += 1
        if len(confirmed_yes_trade_samples) < sample_limit:
            confirmed_yes_trade_samples.append(
                {
                    "created_at": created_at,
                    "aggregate_id": aggregate_id,
                    "condition_id": pre_submit.get("condition_id") if pre_submit else None,
                    "strategy_key": pre_submit.get("strategy_key") if pre_submit else None,
                    "q_live": _float(pre_submit.get("q_live")) if pre_submit else None,
                    "q_lcb": q_lcb,
                    "limit_price": _float(pre_submit.get("limit_price")) if pre_submit else None,
                    "fill_price": fill_price,
                    "filled_size": filled_size,
                    "selection_guard_basis": basis,
                    "q_lcb_guard_basis": qkernel.get("q_lcb_guard_basis"),
                }
            )

    with _open_readonly(trade_db) as fill_conn:
        for aggregate_id, pre_submit in sorted(pre_submit_by_aggregate.items()):
            if pre_submit.get("direction") != "buy_yes":
                continue
            q_lcb = _float(pre_submit.get("q_lcb_5pct"))
            if q_lcb is None or q_lcb < 0.25:
                continue
            high_quality_yes_chain["pre_submit_q_lcb_ge_025"] += 1
            qkernel = _nested_dict(pre_submit.get("qkernel_execution_economics"))
            q_lcb_guard_basis = str(qkernel.get("q_lcb_guard_basis") or "missing")
            selection_guard_basis = str(qkernel.get("selection_guard_basis") or "missing")
            q_lcb_calibration_source = str(pre_submit.get("q_lcb_calibration_source") or "missing")
            high_quality_q_lcb_guard_basis_counts[q_lcb_guard_basis] += 1
            high_quality_selection_guard_basis_counts[selection_guard_basis] += 1
            high_quality_q_lcb_calibration_source_counts[q_lcb_calibration_source] += 1
            selection_n = _float(qkernel.get("selection_guard_n"))
            if selection_n is None:
                high_quality_selection_guard_n_counts["missing"] += 1
            elif selection_n <= 1:
                high_quality_selection_guard_n_counts["<=1"] += 1
            elif selection_n < 10:
                high_quality_selection_guard_n_counts["2-9"] += 1
            elif selection_n < 30:
                high_quality_selection_guard_n_counts["10-29"] += 1
            else:
                high_quality_selection_guard_n_counts[">=30"] += 1
            uses_day0_observed_boundary = (
                q_lcb_guard_basis == "DAY0_OBSERVED_BOUNDARY"
                or selection_guard_basis == "DAY0_OBSERVED_BOUNDARY"
            )
            if uses_day0_observed_boundary:
                high_quality_yes_chain["day0_observed_boundary_pre_submit"] += 1
            if aggregate_id in submit_rejected_by_aggregate:
                high_quality_yes_chain["submit_rejected"] += 1
            if aggregate_id in user_trade_confirmed_by_aggregate:
                high_quality_yes_chain["user_trade_observed_confirmed"] += 1
            venue_order_id = venue_order_by_aggregate.get(aggregate_id, "")
            if venue_order_id:
                high_quality_yes_chain["venue_acknowledged"] += 1
            evidence = _venue_fill_evidence(fill_conn, venue_order_id=venue_order_id)
            if evidence["venue_matched_or_filled"]:
                high_quality_yes_chain["venue_matched_or_filled"] += 1
                if uses_day0_observed_boundary:
                    high_quality_yes_chain["day0_observed_boundary_venue_filled"] += 1
            if evidence["position_entry_filled"]:
                high_quality_yes_chain["position_entry_filled"] += 1
            if evidence["settled"]:
                high_quality_yes_chain["settled"] += 1
                if evidence["settled_won"] is True:
                    high_quality_yes_chain["settled_wins"] += 1
                elif evidence["settled_won"] is False:
                    high_quality_yes_chain["settled_losses"] += 1
                    if uses_day0_observed_boundary:
                        high_quality_yes_chain["day0_observed_boundary_settled_losses"] += 1
            if len(high_quality_yes_chain["samples"]) < sample_limit:
                high_quality_yes_chain["samples"].append(
                    {
                        "aggregate_id": aggregate_id,
                        "city": pre_submit.get("city"),
                        "target_date": pre_submit.get("target_date"),
                        "strategy_key": pre_submit.get("strategy_key"),
                        "bin_label": pre_submit.get("bin_label"),
                        "q_lcb": q_lcb,
                        "q_live": _float(pre_submit.get("q_live")),
                        "q_lcb_guard_basis": q_lcb_guard_basis,
                        "selection_guard_basis": selection_guard_basis,
                        "q_lcb_calibration_source": q_lcb_calibration_source,
                        "selection_guard_n": selection_n,
                        "day0_observed_boundary_guard": uses_day0_observed_boundary,
                        "limit_price": _float(pre_submit.get("limit_price")),
                        "size": _float(pre_submit.get("size")),
                        "venue_order_id": venue_order_id or None,
                        "submit_rejected": aggregate_id in submit_rejected_by_aggregate,
                        "user_trade_observed_confirmed": aggregate_id in user_trade_confirmed_by_aggregate,
                        **evidence,
                    }
                )
        high_quality_yes_chain["q_lcb_guard_basis_counts"] = dict(
            sorted(high_quality_q_lcb_guard_basis_counts.items())
        )
        high_quality_yes_chain["selection_guard_basis_counts"] = dict(
            sorted(high_quality_selection_guard_basis_counts.items())
        )
        high_quality_yes_chain["q_lcb_calibration_source_counts"] = dict(
            sorted(high_quality_q_lcb_calibration_source_counts.items())
        )
        high_quality_yes_chain["selection_guard_n_buckets"] = dict(
            sorted(high_quality_selection_guard_n_counts.items())
        )

    for row in rows:
        payload = json.loads(row["payload_json"])
        audit = _nested_dict(payload.get("decision_audit"))
        book = _nested_dict(audit.get("opportunity_book"))
        selected_direction = audit.get("direction")
        day_key = str(row["created_at"] or "")[:10] or "missing"
        by_day[day_key]["decision_proof_rows"] += 1
        selected_strategy_counts[_strategy_key(payload, audit)] += 1
        if selected_direction == "buy_no":
            summary["selected_buy_no"] += 1
            by_day[day_key]["selected_buy_no"] += 1
        elif selected_direction == "buy_yes":
            summary["selected_buy_yes"] += 1
            by_day[day_key]["selected_buy_yes"] += 1
        candidates = book.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        yes = [c for c in candidates if isinstance(c, dict) and c.get("direction") == "buy_yes"]
        no = [c for c in candidates if isinstance(c, dict) and c.get("direction") == "buy_no"]
        by_day[day_key]["yes_candidates"] += len(yes)
        by_day[day_key]["no_candidates"] += len(no)
        for candidate in yes:
            yes_candidate_strategy_counts[_strategy_key(payload, audit, candidate)] += 1
            yes_roi = _roi_frontier_diag(candidate)
            q_lcb = _metric(candidate, "q_lcb")
            q_point = _metric(candidate, "q_point")
            cost = _metric(candidate, "cost")
            edge_lcb = _metric(candidate, "edge_lcb")
            point_ev = _nested_dict(candidate.get("qkernel_execution_economics")).get("point_ev")
            point_ev_f = _float(point_ev)
            if q_lcb is not None:
                yes_q_lcbs.append(float(q_lcb))
                if float(q_lcb) >= 0.20:
                    by_day[day_key]["yes_q_lcb_ge_020"] += 1
                if float(q_lcb) >= 0.25:
                    by_day[day_key]["yes_q_lcb_ge_025"] += 1
                min_q = yes_roi["min_payoff_q_lcb"]
                if min_q is not None and float(q_lcb) >= float(min_q):
                    yes_above_side_floor += 1
                if max_yes_record is None or float(q_lcb) > float(max_yes_record["q_lcb"]):
                    max_yes_record = {
                        "created_at": row["created_at"],
                        "city": audit.get("city"),
                        "target_date": audit.get("target_date"),
                        "strategy_key": _strategy_key(payload, audit, candidate),
                        "candidate": _summarize_candidate(candidate),
                        "q_lcb": float(q_lcb),
                    }
            if q_point is not None:
                yes_q_points.append(float(q_point))
            if cost is not None:
                yes_costs.append(float(cost))
            if edge_lcb is not None:
                yes_edge_lcbs.append(float(edge_lcb))
            if point_ev_f is not None:
                yes_point_evs.append(float(point_ev_f))
            outcome = settlement_lookup.outcome_for_candidate(candidate, audit)
            if outcome is not None:
                yes_outcome_summary["candidates_with_condition_id"] += 1
                if outcome.get("market_found"):
                    yes_outcome_summary["with_market_event"] += 1
                if outcome.get("settlement_found"):
                    yes_outcome_summary["with_verified_settlement"] += 1
                yes_won = outcome.get("yes_won")
                if yes_won is not None:
                    yes_outcome_summary["with_bin_outcome"] += 1
                    if bool(yes_won):
                        yes_outcome_summary["actual_yes_wins"] += 1
                    bucket = _q_bucket(q_lcb)
                    yes_outcome_by_q_lcb_bucket[bucket]["n"] += 1
                    yes_outcome_by_q_lcb_bucket[bucket]["wins"] += 1 if yes_won else 0
                    condition_id = str(outcome["condition_id"])
                    unique_score = (
                        float(q_lcb) if q_lcb is not None else float("-inf"),
                        (
                            float(q_point) - float(cost)
                            if q_point is not None and cost is not None
                            else float("-inf")
                        ),
                    )
                    previous = unique_yes_outcomes.get(condition_id)
                    if previous is None or unique_score > previous["unique_score"]:
                        unique_yes_outcomes[condition_id] = {
                            "unique_score": unique_score,
                            "q_lcb_bucket": bucket,
                            "yes_won": bool(yes_won),
                            "q_lcb": q_lcb,
                            "q_point": q_point,
                            "cost": cost,
                            "edge_lcb": edge_lcb,
                            "city": outcome.get("city"),
                            "target_date": outcome.get("target_date"),
                            "temperature_metric": outcome.get("temperature_metric"),
                            "label": _candidate_label(candidate) or outcome.get("range_label"),
                            "settlement_value": outcome.get("settlement_value"),
                            "winning_bin": outcome.get("winning_bin"),
                        }
                    if (
                        bool(yes_won)
                        and q_point is not None
                        and cost is not None
                        and float(q_point) > float(cost)
                    ):
                        yes_outcome_summary["actual_win_point_ev_positive"] += 1
                        if len(actual_win_point_ev_samples) < sample_limit:
                            actual_win_point_ev_samples.append(
                                {
                                    "created_at": row["created_at"],
                                    "city": outcome.get("city"),
                                    "target_date": outcome.get("target_date"),
                                    "temperature_metric": outcome.get("temperature_metric"),
                                    "label": _candidate_label(candidate) or outcome.get("range_label"),
                                    "cost": cost,
                                    "q_point": q_point,
                                    "q_lcb": q_lcb,
                                    "edge_lcb": edge_lcb,
                                    "settlement_value": outcome.get("settlement_value"),
                                    "winning_bin": outcome.get("winning_bin"),
                                }
                            )
            basis = (
                _nested_dict(candidate.get("qkernel_execution_economics")).get("q_lcb_guard_basis")
                or candidate.get("q_lcb_calibration_source")
                or "missing"
            )
            yes_guard_basis_counts[str(basis)] += 1
            qkernel = _nested_dict(candidate.get("qkernel_execution_economics"))
            selection_basis = qkernel.get("selection_guard_basis") or "missing"
            yes_selection_basis_counts[str(selection_basis)] += 1
            if str(selection_basis) == "SIDE_NOT_ARMED":
                by_day[day_key]["yes_side_not_armed"] += 1
            selection_n = _float(qkernel.get("selection_guard_n"))
            if selection_n is None:
                yes_selection_n_counts["missing"] += 1
            elif selection_n <= 1:
                yes_selection_n_counts["<=1"] += 1
            elif selection_n < 10:
                yes_selection_n_counts["2-9"] += 1
            elif selection_n < 30:
                yes_selection_n_counts["10-29"] += 1
            else:
                yes_selection_n_counts[">=30"] += 1
        positive_yes = [c for c in yes if _positive_objective(c)]
        positive_no = [c for c in no if _positive_objective(c)]
        summary["decision_proof_rows"] += 1
        summary["yes_candidates"] += len(yes)
        summary["no_candidates"] += len(no)
        summary["positive_yes_candidates"] += len(positive_yes)
        summary["positive_no_candidates"] += len(positive_no)
        if not positive_yes or not positive_no:
            continue
        summary["families_with_positive_yes_and_no"] += 1
        top_yes = max(positive_yes, key=_rank_value)
        top_no = max(positive_no, key=_rank_value)
        yes_roi = _roi_frontier_diag(top_yes)
        no_roi = _roi_frontier_diag(top_no)
        if yes_roi["roi_frontier_useful"]:
            summary["top_yes_roi_frontier_useful"] += 1
        for reason in yes_roi["reasons"]:
            frontier_rejections[f"top_yes_{reason}"] = frontier_rejections.get(f"top_yes_{reason}", 0) + 1
        if no_roi["roi_frontier_useful"]:
            summary["top_no_roi_frontier_useful"] += 1
        for reason in no_roi["reasons"]:
            frontier_rejections[f"top_no_{reason}"] = frontier_rejections.get(f"top_no_{reason}", 0) + 1
        yes_roi_key = _roi_key_from_diag(top_yes, yes_roi)
        no_roi_key = _roi_key_from_diag(top_no, no_roi)
        for floor in counterfactual_floors:
            cf = yes_floor_counterfactual[f"{floor:.2f}"]
            cf_yes_roi = _roi_frontier_diag_at_floor(
                top_yes,
                min_payoff_q_lcb=floor,
            )
            if cf_yes_roi["roi_frontier_useful"]:
                cf["top_yes_roi_frontier_useful"] += 1
                if _roi_key_from_diag(top_yes, cf_yes_roi) > no_roi_key:
                    cf["top_yes_roi_key_better_than_top_no"] += 1
        if (
            selected_direction == "buy_no"
            and yes_roi["roi_frontier_useful"]
            and no_roi["roi_frontier_useful"]
            and yes_roi_key > no_roi_key
        ):
            summary["selected_no_top_yes_roi_key_better"] += 1
        for metric_name in (
            "optimal_delta_u",
            "delta_u_at_min",
            "robust_trade_score",
            "edge_lcb",
            "q_lcb",
        ):
            yes_value = _metric(top_yes, metric_name)
            no_value = _metric(top_no, metric_name)
            if yes_value is None or no_value is None:
                metric_wins[f"missing_{metric_name}"] = metric_wins.get(f"missing_{metric_name}", 0) + 1
            elif yes_value > no_value:
                metric_wins[f"yes_gt_no_{metric_name}"] = metric_wins.get(f"yes_gt_no_{metric_name}", 0) + 1
            elif no_value > yes_value:
                metric_wins[f"no_gt_yes_{metric_name}"] = metric_wins.get(f"no_gt_yes_{metric_name}", 0) + 1
            else:
                metric_wins[f"tie_{metric_name}"] = metric_wins.get(f"tie_{metric_name}", 0) + 1

        yes_objective = _metric(top_yes, "optimal_delta_u")
        no_objective = _metric(top_no, "optimal_delta_u")
        yes_score = _metric(top_yes, "robust_trade_score")
        no_score = _metric(top_no, "robust_trade_score")
        record = {
            "created_at": row["created_at"],
            "selected_direction": selected_direction,
            "city": audit.get("city"),
            "target_date": audit.get("target_date"),
            "top_yes": _summarize_candidate(top_yes),
            "top_no": _summarize_candidate(top_no),
        }
        if (
            selected_direction == "buy_no"
            and yes_objective is not None
            and no_objective is not None
            and yes_objective > no_objective
        ):
            summary["selected_no_top_yes_objective_better"] += 1
            if len(objective_better_samples) < sample_limit:
                objective_better_samples.append(record)
        elif (
            selected_direction == "buy_no"
            and yes_score is not None
            and no_score is not None
            and yes_score > no_score
        ):
            summary["selected_no_top_yes_score_better_only"] += 1
            if len(score_only_samples) < sample_limit:
                score_only_samples.append(record)

    settlement_lookup.close()

    unique_buckets: dict[str, dict[str, int]] = defaultdict(_empty_outcome_bucket)
    for outcome in unique_yes_outcomes.values():
        bucket = str(outcome["q_lcb_bucket"])
        unique_buckets[bucket]["n"] += 1
        unique_buckets[bucket]["wins"] += 1 if outcome["yes_won"] else 0
    unique_actual_yes_wins = sum(1 for outcome in unique_yes_outcomes.values() if outcome["yes_won"])
    yes_outcome_with_bin = int(yes_outcome_summary["with_bin_outcome"])
    unique_count = len(unique_yes_outcomes)

    return {
        "trade_db": str(trade_db),
        "forecast_db": str(forecast_db) if forecast_db is not None else None,
        "cutoff": cutoff,
        "summary": summary,
        "execution_chain": execution_chain,
        "confirmed_user_trade_chain": confirmed_user_trade_chain,
        "by_day": {
            key: dict(sorted(counter.items()))
            for key, counter in sorted(by_day.items())
        },
        "frontier_rejections": dict(sorted(frontier_rejections.items())),
        "metric_wins": dict(sorted(metric_wins.items())),
        "strategy_counts": {
            "selected": dict(sorted(selected_strategy_counts.items())),
            "yes_candidates": dict(sorted(yes_candidate_strategy_counts.items())),
        },
        "yes_quality_distribution": {
            "count": len(yes_q_lcbs),
            "above_side_floor": yes_above_side_floor,
            "max": max_yes_record,
            "percentiles": _percentiles(yes_q_lcbs, (50, 75, 90, 95, 99, 99.9)),
            "point_q_percentiles": _percentiles(yes_q_points, (50, 75, 90, 95, 99, 99.9)),
            "cost_percentiles": _percentiles(yes_costs, (50, 75, 90, 95, 99, 99.9)),
            "edge_lcb_percentiles": _percentiles(yes_edge_lcbs, (50, 75, 90, 95, 99, 99.9)),
            "point_ev_percentiles": _percentiles(yes_point_evs, (50, 75, 90, 95, 99, 99.9)),
            "guard_basis_counts": dict(sorted(yes_guard_basis_counts.items())),
            "selection_guard_basis_counts": dict(sorted(yes_selection_basis_counts.items())),
            "selection_guard_n_buckets": dict(sorted(yes_selection_n_counts.items())),
            "floor_counterfactual": yes_floor_counterfactual,
        },
        "confirmed_yes_trade_quality": {
            "count": len(confirmed_yes_q_lcbs),
            "q_lcb_ge_025": sum(1 for value in confirmed_yes_q_lcbs if value >= 0.25),
            "q_lcb_percentiles": _percentiles(
                confirmed_yes_q_lcbs,
                (0, 50, 90, 100),
            ),
            "selection_guard_basis_counts": dict(
                sorted(confirmed_yes_guard_basis_counts.items())
            ),
            "samples": confirmed_yes_trade_samples,
        },
        "high_quality_yes_chain": high_quality_yes_chain,
        "yes_settlement_outcome": {
            **yes_outcome_summary,
            "actual_yes_win_rate": (
                yes_outcome_summary["actual_yes_wins"] / yes_outcome_with_bin
                if yes_outcome_with_bin
                else None
            ),
            "by_q_lcb_bucket": {
                key: _finalize_outcome_bucket(bucket)
                for key, bucket in sorted(yes_outcome_by_q_lcb_bucket.items())
            },
            "unique_conditions": {
                "count": unique_count,
                "actual_yes_wins": unique_actual_yes_wins,
                "actual_yes_win_rate": (
                    unique_actual_yes_wins / unique_count if unique_count else None
                ),
                "by_q_lcb_bucket": {
                    key: _finalize_outcome_bucket(bucket)
                    for key, bucket in sorted(unique_buckets.items())
                },
            },
            "actual_win_point_ev_positive_samples": actual_win_point_ev_samples,
        },
        "objective_better_samples": objective_better_samples,
        "score_only_samples": score_only_samples,
        "verdict": _verdict(summary, high_quality_yes_chain),
    }


def _verdict(
    summary: dict[str, int],
    high_quality_yes_chain: dict[str, Any] | None = None,
) -> str:
    if summary["decision_proof_rows"] <= 0:
        return "NO_RECENT_DECISION_PROOF_ROWS"
    if summary["selected_no_top_yes_roi_key_better"] > 0:
        return "SELECTOR_ANOMALY_TOP_YES_ROI_KEY_BETTER"
    if high_quality_yes_chain:
        hq_pre = int(high_quality_yes_chain.get("pre_submit_q_lcb_ge_025") or 0)
        hq_venue = int(high_quality_yes_chain.get("venue_matched_or_filled") or 0)
        hq_user = int(high_quality_yes_chain.get("user_trade_observed_confirmed") or 0)
        hq_losses = int(high_quality_yes_chain.get("settled_losses") or 0)
        hq_day0_boundary_filled = int(
            high_quality_yes_chain.get("day0_observed_boundary_venue_filled") or 0
        )
        hq_day0_boundary_losses = int(
            high_quality_yes_chain.get("day0_observed_boundary_settled_losses") or 0
        )
        if hq_day0_boundary_filled > 0 and hq_day0_boundary_losses > 0:
            return "HIGH_Q_YES_DAY0_OBSERVED_BOUNDARY_FILLED_SETTLED_LOSS"
        if hq_venue > hq_user:
            return "HIGH_Q_YES_VENUE_FILLED_BUT_USER_TRADE_OBSERVED_MISSING"
        if hq_losses > 0:
            return "HIGH_Q_YES_FILLED_AND_SETTLED_LOSS"
        if hq_pre > 0 and hq_venue <= 0:
            return "HIGH_Q_YES_PRESUBMIT_WITHOUT_CONFIRMED_FILL"
    if summary["selected_no_top_yes_objective_better"] > 0:
        return "OBJECTIVE_METRIC_FALSE_POSITIVE_NO_ROI_SELECTOR_ANOMALY"
    if summary["selected_buy_yes"] <= 0:
        return "NO_SELECTED_YES_BUT_NO_OBJECTIVE_SELECTOR_ANOMALY"
    return "YES_SELECTED_WITHOUT_OBJECTIVE_SELECTOR_ANOMALY"


def _print_markdown(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("# YES/NO Selection Skew Audit")
    print()
    print(f"- trade_db: `{report['trade_db']}`")
    print(f"- forecast_db: `{report['forecast_db']}`")
    print(f"- cutoff: `{report['cutoff']}`")
    print(f"- verdict: `{report['verdict']}`")
    print(f"- decision_proof_rows: {summary['decision_proof_rows']}")
    print(f"- selected_buy_no: {summary['selected_buy_no']}")
    print(f"- selected_buy_yes: {summary['selected_buy_yes']}")
    print(f"- candidates: YES={summary['yes_candidates']} NO={summary['no_candidates']}")
    print(
        "- positive candidates: "
        f"YES={summary['positive_yes_candidates']} NO={summary['positive_no_candidates']}"
    )
    print(
        "- selected NO with top YES objective better: "
        f"{summary['selected_no_top_yes_objective_better']}"
    )
    print(
        "- selected NO with top YES score better only: "
        f"{summary['selected_no_top_yes_score_better_only']}"
    )
    print("- execution chain:")
    for event_type, counts in report["execution_chain"].items():
        print(
            f"  - {event_type}: YES={counts['buy_yes']} "
            f"NO={counts['buy_no']} unknown={counts['unknown']} total={counts['total']}"
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-db", type=Path, default=DEFAULT_TRADE_DB)
    parser.add_argument("--forecast-db", type=Path, default=DEFAULT_FORECAST_DB)
    parser.add_argument("--days", type=float, default=7.0)
    parser.add_argument("--since", help="ISO timestamp cutoff; overrides --days")
    parser.add_argument("--sample-limit", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = audit_selection_skew(
        trade_db=args.trade_db,
        forecast_db=args.forecast_db,
        since=args.since,
        days=args.days,
        sample_limit=max(0, args.sample_limit),
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, default=str))
    else:
        _print_markdown(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
