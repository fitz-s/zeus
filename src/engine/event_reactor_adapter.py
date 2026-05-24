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
from src.events.reactor import OpportunityEventReactor, ReactorConfig
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
    config: ReactorConfig | None = None,
    regret_ledger=None,
) -> OpportunityEventReactor:
    return OpportunityEventReactor(
        store,
        source_truth_gate=source_truth_gate,
        executable_snapshot_gate=executable_snapshot_gate,
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
        )
    return False


def executable_snapshot_gate_from_trade_conn(
    trade_conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> Callable[[OpportunityEvent], bool]:
    """Return a gate requiring at least one fresh active executable weather market."""

    checked_at = (now or datetime.now(UTC)).astimezone(UTC)

    def _gate(_event: OpportunityEvent) -> bool:
        if not _table_exists(trade_conn, "executable_market_snapshots"):
            return False
        columns = _table_columns(trade_conn, "executable_market_snapshots")
        required = {"freshness_deadline", "yes_token_id", "no_token_id"}
        if not required <= columns:
            return False
        predicates = ["freshness_deadline >= ?"]
        params: list[object] = [checked_at.isoformat()]
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


def existing_cycle_downstream_gate(_event: OpportunityEvent) -> bool:
    """FDR/Kelly/final-intent are enforced inside the existing cycle runner."""

    return True


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
) -> bool:
    """Trigger the established money path and report whether it submitted."""

    mode = discovery_mode_for_event(event)
    summary = run_cycle(mode)
    if not isinstance(summary, dict):
        return False
    return bool(
        int(summary.get("entry_orders_submitted", 0) or 0) > 0
        or int(summary.get("submit_attempts", 0) or 0) > 0
        or int(summary.get("final_intents_built", 0) or 0) > 0
    )


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
