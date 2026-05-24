"""Engine adapter for EDLI opportunity reactor construction.

The adapter connects EDLI events to the existing cycle runner. It deliberately
does not construct orders itself: source truth, FDR, Kelly, RiskGuard,
FinalExecutionIntent, and executor side effects remain in the established money
path.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable

from src.events.event_store import EventStore
from src.events.opportunity_event import OpportunityEvent
from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor, ReactorConfig
from src.riskguard.risk_level import RiskLevel


UTC = timezone.utc


def build_event_reactor(
    store: EventStore,
    *,
    source_truth_gate,
    executable_snapshot_gate,
    fdr_gate,
    kelly_gate,
    riskguard_gate,
    final_intent_submit,
    reject,
    trade_score_gate=None,
    config: ReactorConfig | None = None,
    regret_ledger=None,
) -> OpportunityEventReactor:
    return OpportunityEventReactor(
        store,
        source_truth_gate=source_truth_gate,
        executable_snapshot_gate=executable_snapshot_gate,
        trade_score_gate=trade_score_gate,
        fdr_gate=fdr_gate,
        kelly_gate=kelly_gate,
        riskguard_gate=riskguard_gate,
        final_intent_submit=final_intent_submit,
        reject=reject,
        config=config,
        regret_ledger=regret_ledger,
    )


def edli_source_truth_gate(event: OpportunityEvent) -> bool:
    """Fail closed unless an EDLI event is source-eligible for a live cycle."""

    payload = _payload(event)
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        return (
            bool(event.causal_snapshot_id)
            and payload.get("completeness_status") == "COMPLETE"
            and payload.get("required_fields_present") is True
            and payload.get("required_steps_present") is True
        )
    if event.event_type == "DAY0_EXTREME_UPDATED":
        return (
            payload.get("source_match_status") == "MATCH"
            and payload.get("local_date_status") == "MATCH"
            and payload.get("station_match_status") == "MATCH"
            and payload.get("dst_status") == "UNAMBIGUOUS"
            and payload.get("metric_match_status") == "MATCH"
            and payload.get("rounding_status") == "MATCH"
            and payload.get("source_authorized_status", "AUTHORIZED") == "AUTHORIZED"
            and payload.get("live_authority_status") == "LIVE_AUTHORITY"
        )
    return False


def executable_snapshot_gate_from_trade_conn(
    trade_conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> Callable[[OpportunityEvent], bool]:
    """Return a gate requiring a fresh executable snapshot bound to the event."""

    checked_at = (now or datetime.now(UTC)).astimezone(UTC)

    def _gate(event: OpportunityEvent) -> bool:
        if not _table_exists(trade_conn, "executable_market_snapshots"):
            return False
        columns = _table_columns(trade_conn, "executable_market_snapshots")
        required = {"freshness_deadline", "yes_token_id", "no_token_id"}
        if not required <= columns:
            return False
        payload = _payload(event)
        predicates = ["freshness_deadline >= ?"]
        params: list[object] = [checked_at.isoformat()]
        binding = _event_snapshot_binding(payload, event=event, columns=columns)
        if binding is None:
            return False
        binding_predicate, binding_params = binding
        predicates.append(binding_predicate)
        params.extend(binding_params)
        if "active" in columns:
            predicates.append("COALESCE(active, 0) = 1")
        if "closed" in columns:
            predicates.append("COALESCE(closed, 0) = 0")
        if "event_slug" in columns:
            predicates.append(
                "(LOWER(COALESCE(event_slug, '')) LIKE '%weather%' "
                "OR LOWER(COALESCE(event_slug, '')) LIKE '%temperature%')"
            )
        row = trade_conn.execute(
            f"""
            SELECT 1
            FROM executable_market_snapshots
            WHERE {' AND '.join(predicates)}
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        return row is not None

    return _gate


def riskguard_allows_new_entries(*, get_current_level: Callable[[], RiskLevel]) -> Callable[[OpportunityEvent], bool]:
    """Return a reactor gate that preserves RiskGuard's entry-blocking law."""

    def _gate(_event: OpportunityEvent) -> bool:
        return get_current_level() == RiskLevel.GREEN

    return _gate


def edli_payload_fdr_gate(event: OpportunityEvent) -> bool:
    """Require durable full-family FDR evidence in the event payload."""

    payload = _payload(event)
    return (
        bool(payload.get("fdr_family_id"))
        and int(payload.get("fdr_hypothesis_count") or 0) > 0
        and payload.get("fdr_pass") is True
    )


def edli_payload_kelly_gate(event: OpportunityEvent) -> bool:
    """Require typed Kelly evidence in the event payload."""

    payload = _payload(event)
    return (
        payload.get("kelly_execution_price_type") == "ExecutionPrice"
        and payload.get("kelly_price_fee_deducted") is True
        and float(payload.get("kelly_size_usd") or 0.0) > 0.0
    )


def edli_trade_score_gate(event: OpportunityEvent) -> bool:
    """Compute robust EDLI TradeScore from event-bound executable inputs."""

    from src.strategy.live_inference.trade_score import TradeScoreInputs, robust_trade_score

    payload = _payload(event)
    required = (
        "p_fill_lcb",
        "q_5pct",
        "q_posterior",
        "c_95pct",
        "c_stress",
        "lambda_edge",
        "lambda_stress",
    )
    if not all(key in payload for key in required):
        return False
    try:
        score = robust_trade_score(
            TradeScoreInputs(
                p_fill_lcb=float(payload["p_fill_lcb"]),
                q_5pct=float(payload["q_5pct"]),
                q_posterior=float(payload["q_posterior"]),
                c_95pct=float(payload["c_95pct"]),
                c_stress=float(payload["c_stress"]),
                lambda_edge=float(payload["lambda_edge"]),
                lambda_stress=float(payload["lambda_stress"]),
            )
        )
    except (TypeError, ValueError):
        return False
    return score > 0.0


def discovery_mode_for_event(event: OpportunityEvent):
    from src.engine.discovery_mode import DiscoveryMode

    if event.event_type == "DAY0_EXTREME_UPDATED":
        return DiscoveryMode.DAY0_CAPTURE
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        return DiscoveryMode.UPDATE_REACTION
    raise ValueError(f"EDLI event type cannot trigger existing cycle: {event.event_type}")


def submit_existing_cycle_for_event(
    event: OpportunityEvent,
    *,
    run_cycle: Callable,
) -> EventSubmissionReceipt:
    """Trigger established money path only when it returns event-bound proof."""

    mode = discovery_mode_for_event(event)
    summary = run_cycle(mode)
    if not isinstance(summary, dict):
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EXISTING_CYCLE_NO_SUMMARY")
    submitted = bool(
        int(summary.get("entry_orders_submitted", 0) or 0) > 0
        or int(summary.get("submit_attempts", 0) or 0) > 0
        or int(summary.get("final_intents_built", 0) or 0) > 0
    )
    if not submitted:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EXISTING_CYCLE_NO_SUBMIT")
    receipt = EventSubmissionReceipt(
        submitted=True,
        event_id=str(summary.get("edli_event_id") or ""),
        causal_snapshot_id=summary.get("causal_snapshot_id"),
        condition_id=summary.get("condition_id"),
        token_id=summary.get("token_id"),
        executable_snapshot_id=summary.get("executable_snapshot_id"),
        reason=str(summary.get("edli_submit_reason") or "event_bound_existing_cycle_submit"),
    )
    if not receipt.event_id:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="UNBOUND_EXISTING_CYCLE_SUMMARY",
        )
    return receipt


def _payload(event: OpportunityEvent) -> dict[str, object]:
    try:
        parsed = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _event_snapshot_binding(
    payload: dict[str, object],
    *,
    event: OpportunityEvent,
    columns: set[str],
) -> tuple[str, list[object]] | None:
    snapshot_id = str(payload.get("executable_snapshot_id") or event.causal_snapshot_id or "")
    if snapshot_id and "snapshot_id" in columns:
        return "snapshot_id = ?", [snapshot_id]
    condition_id = str(payload.get("condition_id") or "")
    token_id = str(payload.get("token_id") or "")
    if condition_id and token_id and "condition_id" in columns:
        return "(condition_id = ? AND (yes_token_id = ? OR no_token_id = ?))", [condition_id, token_id, token_id]
    if condition_id and "condition_id" in columns:
        return "condition_id = ?", [condition_id]
    if token_id:
        return "(yes_token_id = ? OR no_token_id = ?)", [token_id, token_id]
    return None
