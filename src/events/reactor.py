"""EDLI opportunity event reactor.

This module intentionally has no venue-adapter import. Execution side effects
must flow through injected final-intent/executor seams owned by `src.engine` and
`src.execution`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from src.events.event_store import EventStore
from src.events.opportunity_event import OpportunityEvent, assert_available_for_decision

UTC = timezone.utc

Gate = Callable[[OpportunityEvent], bool]
Reject = Callable[[OpportunityEvent, str, str], None]
Submit = Callable[[OpportunityEvent], bool | None]


@dataclass
class ReactorConfig:
    reactor_mode: str = "live"
    taker_fok_fak_live_enabled: bool = False
    tiny_live_max_notional_usd: float = 5.0
    tiny_live_max_orders_per_day: int = 1


@dataclass
class ReactorResult:
    processed: int = 0
    rejected: int = 0
    submitted: int = 0
    dead_lettered: int = 0
    rejection_reasons: list[str] = field(default_factory=list)


class OpportunityEventReactor:
    def __init__(
        self,
        store: EventStore,
        *,
        source_truth_gate: Gate,
        executable_snapshot_gate: Gate,
        fdr_gate: Gate,
        kelly_gate: Gate,
        riskguard_gate: Gate,
        final_intent_submit: Submit,
        reject: Reject,
        config: ReactorConfig | None = None,
        regret_ledger: Any | None = None,
    ) -> None:
        self._store = store
        self._source_truth_gate = source_truth_gate
        self._executable_snapshot_gate = executable_snapshot_gate
        self._fdr_gate = fdr_gate
        self._kelly_gate = kelly_gate
        self._riskguard_gate = riskguard_gate
        self._submit = final_intent_submit
        self._reject = reject
        self._config = config or ReactorConfig()
        self._regret_ledger = regret_ledger
        self._family_logged: set[str] = set()
        self._day0_live_orders_today = 0
        from src.strategy.live_inference.promotion_ledger import EdliLiveCapLedger

        self._live_cap_ledger = EdliLiveCapLedger(store.conn)

    def process_pending(self, *, decision_time: datetime, limit: int = 100) -> ReactorResult:
        result = ReactorResult()
        events = self._store.fetch_pending(decision_time=decision_time.astimezone(UTC).isoformat(), limit=limit)
        for event in events:
            if not self._store.claim(event.event_id, claimed_at=decision_time.astimezone(UTC).isoformat()):
                continue
            try:
                self._process_one(event, decision_time=decision_time, result=result)
                self._store.mark_processed(event.event_id, processed_at=decision_time.astimezone(UTC).isoformat())
                result.processed += 1
            except Exception as exc:
                self._reject(event, "UNKNOWN_REVIEW_REQUIRED", str(exc))
                self._write_regret(event, "UNKNOWN_REVIEW_REQUIRED", str(exc))
                self._store.mark_dead_letter(
                    event,
                    failure_stage="UNKNOWN_REVIEW_REQUIRED",
                    error_message=str(exc),
                    created_at=decision_time.astimezone(UTC).isoformat(),
                )
                result.dead_lettered += 1
        return result

    def _process_one(self, event: OpportunityEvent, *, decision_time: datetime, result: ReactorResult) -> None:
        assert_available_for_decision(event, decision_time)
        if event.event_type in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED", "NEW_MARKET_DISCOVERED"}:
            self._reject_event(event, "EXECUTABLE_QUOTE", "MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE", result)
            return
        if self._config.reactor_mode != "live":
            self._reject_event(event, "LIVE_CAP", "REACTOR_NOT_LIVE", result)
            return
        if event.event_type == "DAY0_EXTREME_UPDATED" and not _day0_hard_fact_payload_live_eligible(event):
            self._reject_event(event, "SOURCE_TRUTH", "DAY0_HARD_FACT_AUTHORITY_BLOCKED", result)
            return
        if not self._source_truth_gate(event):
            self._reject_event(event, "SOURCE_TRUTH", "SOURCE_TRUTH_BLOCKED", result)
            return
        if not self._executable_snapshot_gate(event):
            self._reject_event(event, "EXECUTABLE_QUOTE", "EXECUTABLE_SNAPSHOT_BLOCKED", result)
            return
        self._log_family_once(event)
        if not self._fdr_gate(event):
            self._reject_event(event, "FDR", "FDR_REJECTED", result)
            return
        if not self._kelly_gate(event):
            self._reject_event(event, "KELLY", "KELLY_TOO_SMALL", result)
            return
        if not self._riskguard_gate(event):
            self._reject_event(event, "RISK_GUARD", "RISK_GUARD_BLOCKED", result)
            return
        if event.event_type == "DAY0_EXTREME_UPDATED":
            cap_decision = self._day0_tiny_cap_decision(event, decision_time=decision_time)
            if not cap_decision.allowed:
                self._reject_event(event, "LIVE_CAP", cap_decision.reason, result)
                return
        submit_result = self._submit(event)
        if submit_result is False:
            self._reject_event(event, "EXECUTOR_EXPRESSIBILITY", "EXISTING_CYCLE_NO_SUBMIT", result)
            return
        if event.event_type == "DAY0_EXTREME_UPDATED":
            self._day0_live_orders_today += 1
            self._live_cap_ledger.reserve_day0(
                event_id=event.event_id,
                decision_time=decision_time,
                notional_usd=self._config.tiny_live_max_notional_usd,
            )
        result.submitted += 1

    def _reject_event(self, event: OpportunityEvent, stage: str, reason: str, result: ReactorResult) -> None:
        self._reject(event, stage, reason)
        self._write_regret(event, stage, reason)
        result.rejected += 1
        result.rejection_reasons.append(reason)

    def _write_regret(self, event: OpportunityEvent, stage: str, reason: str) -> None:
        if self._regret_ledger is None:
            return
        from src.strategy.live_inference.no_trade_regret import NoTradeRegretEvent

        payload = _payload_dict(event)
        self._regret_ledger.insert_idempotent(
            NoTradeRegretEvent(
                event_id=event.event_id,
                rejection_stage=stage,  # type: ignore[arg-type]
                rejection_reason=reason,
                regret_bucket=_regret_bucket_for(reason),  # type: ignore[arg-type]
                market_slug=payload.get("market_slug"),
                condition_id=payload.get("condition_id"),
                token_id=payload.get("token_id"),
                outcome_label=payload.get("outcome_label"),
            )
        )

    def _log_family_once(self, event: OpportunityEvent) -> None:
        family_key = event.entity_key.rsplit("|", 1)[0]
        self._family_logged.add(family_key)

    def family_log_count(self) -> int:
        return len(self._family_logged)

    def _day0_tiny_cap_decision(self, event: OpportunityEvent, *, decision_time: datetime):
        if self._day0_live_orders_today >= self._config.tiny_live_max_orders_per_day:
            from src.strategy.live_inference.promotion_ledger import LiveCapDecision

            return LiveCapDecision(
                False,
                "DAY0_TINY_ORDER_CAP_BLOCKED",
                self._day0_live_orders_today,
                0.0,
            )
        return self._live_cap_ledger.check_day0(
            event_id=event.event_id,
            decision_time=decision_time,
            max_orders_per_day=self._config.tiny_live_max_orders_per_day,
            max_notional_usd=self._config.tiny_live_max_notional_usd,
        )


def _payload_dict(event: OpportunityEvent) -> dict[str, Any]:
    try:
        parsed = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _day0_hard_fact_payload_live_eligible(event: OpportunityEvent) -> bool:
    payload = _payload_dict(event)
    return (
        payload.get("source_match_status") == "MATCH"
        and payload.get("local_date_status") == "MATCH"
        and payload.get("station_match_status") == "MATCH"
        and payload.get("dst_status") == "UNAMBIGUOUS"
        and payload.get("metric_match_status") == "MATCH"
        and payload.get("rounding_status") == "MATCH"
        and payload.get("source_authorized_status", "AUTHORIZED") == "AUTHORIZED"
    )


def _regret_bucket_for(reason: str) -> str:
    if reason in {"FDR_REJECTED"}:
        return "FDR_REJECTED"
    if reason in {"KELLY_TOO_SMALL"}:
        return "KELLY_TOO_SMALL"
    if "RISK" in reason:
        return "RISK_CAP"
    if "QUOTE" in reason or "SNAPSHOT" in reason:
        return "QUOTE_UNAVAILABLE"
    if "SOURCE" in reason or "DAY0_HARD_FACT" in reason:
        return "SOURCE_WRONG"
    if "FAMILY" in reason:
        return "FAMILY_INCOMPLETE"
    if "LEAK" in reason or "AVAILABLE_AT" in reason:
        return "LEAKAGE_BLOCKED"
    return "UNKNOWN_REVIEW_REQUIRED"
