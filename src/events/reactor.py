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


@dataclass(frozen=True)
class EventSubmissionReceipt:
    """Proof that an executor-facing intent belongs to the current EDLI event.

    ``proof_accepted`` means the EDLI reactor accepted the event-bound
    money-path proof. ``submitted`` is reserved for real executor/venue submit
    semantics and must stay false for ``side_effect_status=NO_SUBMIT``.
    """

    submitted: bool
    event_id: str
    causal_snapshot_id: str | None = None
    city: str | None = None
    target_date: str | None = None
    metric: str | None = None
    condition_id: str | None = None
    token_id: str | None = None
    outcome_label: str | None = None
    candidate_id: str | None = None
    executable_snapshot_id: str | None = None
    family_id: str | None = None
    bin_label: str | None = None
    direction: str | None = None
    q_live: float | None = None
    q_lcb_5pct: float | None = None
    c_fee_adjusted: float | None = None
    c_cost_95pct: float | None = None
    p_fill_lcb: float | None = None
    trade_score: float | None = None
    native_quote_available: bool | None = None
    source_status: str | None = None
    family_complete: bool | None = None
    trade_score_positive: bool = False
    fdr_pass: bool = False
    fdr_family_id: str | None = None
    fdr_hypothesis_count: int = 0
    kelly_pass: bool = False
    kelly_execution_price_type: str | None = None
    kelly_price_fee_deducted: bool = False
    kelly_size_usd: float = 0.0
    kelly_cost_basis_id: str | None = None
    final_intent_id: str | None = None
    side_effect_status: str = "NO_SUBMIT"
    reason: str = ""
    proof_accepted: bool | None = None

    def __post_init__(self) -> None:
        if self.proof_accepted is None:
            object.__setattr__(self, "proof_accepted", bool(self.submitted))


Submit = Callable[[OpportunityEvent, datetime], bool | None | EventSubmissionReceipt]


@dataclass
class ReactorConfig:
    reactor_mode: str = "live_no_submit"
    real_order_submit_enabled: bool = False
    taker_fok_fak_live_enabled: bool = False
    tiny_live_max_notional_usd: float = 5.0
    tiny_live_max_orders_per_day: int = 1


@dataclass
class ReactorResult:
    processed: int = 0
    rejected: int = 0
    proof_accepted: int = 0
    dead_lettered: int = 0
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def submitted(self) -> int:
        return self.proof_accepted


class OpportunityEventReactor:
    def __init__(
        self,
        store: EventStore,
        *,
        source_truth_gate: Gate,
        executable_snapshot_gate: Gate,
        riskguard_gate: Gate,
        final_intent_submit: Submit,
        reject: Reject,
        config: ReactorConfig | None = None,
        regret_ledger: Any | None = None,
    ) -> None:
        self._store = store
        self._source_truth_gate = source_truth_gate
        self._executable_snapshot_gate = executable_snapshot_gate
        self._riskguard_gate = riskguard_gate
        self._submit = final_intent_submit
        self._reject = reject
        self._config = config or ReactorConfig()
        self._regret_ledger = regret_ledger
        self._family_logged: set[str] = set()
        self._day0_live_orders_today = 0
        from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
        from src.strategy.live_inference.promotion_ledger import EdliLiveCapLedger

        self._no_submit_receipt_ledger = EdliNoSubmitReceiptLedger(store.conn)
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
        if self._config.reactor_mode not in {"live", "live_no_submit"}:
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
        if not self._riskguard_gate(event):
            self._reject_event(event, "RISK_GUARD", "RISK_GUARD_BLOCKED", result)
            return
        submit_result = self._submit(event, decision_time.astimezone(UTC))
        receipt = _submission_receipt(event, submit_result)
        if receipt is None or not _receipt_matches_event(event, receipt):
            reason = receipt.reason if receipt is not None and receipt.reason else "EVENT_SUBMISSION_RECEIPT_MISSING_OR_UNBOUND"
            self._reject_event(event, "EXECUTOR_EXPRESSIBILITY", reason, result, receipt=receipt)
            return
        proof_stage, proof_reason = _receipt_money_path_blocker(receipt)
        if proof_stage is not None:
            self._reject_event(event, proof_stage, proof_reason, result, receipt=receipt)
            return
        if receipt.side_effect_status != "NO_SUBMIT" and not self._config.real_order_submit_enabled:
            self._reject_event(event, "EXECUTOR_EXPRESSIBILITY", "EDLI_REAL_ORDER_SUBMIT_DISABLED", result, receipt=receipt)
            return
        if (
            event.event_type == "DAY0_EXTREME_UPDATED"
            and self._config.real_order_submit_enabled
            and receipt.side_effect_status in {"COMMAND_CREATED", "SUBMITTED"}
        ):
            cap_decision = self._day0_tiny_cap_decision(event, decision_time=decision_time)
            if not cap_decision.allowed:
                self._reject_event(event, "LIVE_CAP", cap_decision.reason, result, receipt=receipt)
                return
            self._day0_live_orders_today += 1
            self._live_cap_ledger.reserve_day0(
                event_id=event.event_id,
                decision_time=decision_time,
                notional_usd=self._config.tiny_live_max_notional_usd,
            )
        if receipt.side_effect_status == "NO_SUBMIT":
            self._no_submit_receipt_ledger.insert_idempotent(receipt, decision_time=decision_time)
        result.proof_accepted += 1

    def _reject_event(
        self,
        event: OpportunityEvent,
        stage: str,
        reason: str,
        result: ReactorResult,
        *,
        receipt: EventSubmissionReceipt | None = None,
    ) -> None:
        self._reject(event, stage, reason)
        self._write_regret(event, stage, reason, receipt=receipt)
        result.rejected += 1
        result.rejection_reasons.append(reason)

    def _write_regret(
        self,
        event: OpportunityEvent,
        stage: str,
        reason: str,
        *,
        receipt: EventSubmissionReceipt | None = None,
    ) -> None:
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
                condition_id=_receipt_or_payload(receipt, payload, "condition_id"),
                token_id=_receipt_or_payload(receipt, payload, "token_id"),
                outcome_label=_receipt_or_payload(receipt, payload, "outcome_label"),
                decision_time=payload.get("decision_time"),
                city=_receipt_or_payload(receipt, payload, "city"),
                target_date=_receipt_or_payload(receipt, payload, "target_date"),
                metric=_receipt_or_payload(receipt, payload, "metric"),
                observation_time=payload.get("observation_time"),
                decision_seq=_optional_int(payload.get("decision_seq")),
                family_id=_receipt_or_payload(receipt, payload, "family_id"),
                bin_label=_receipt_or_payload(receipt, payload, "bin_label"),
                direction=_receipt_or_payload(receipt, payload, "direction"),
                q_live=_optional_float(_receipt_or_payload(receipt, payload, "q_live")),
                q_lcb_5pct=_optional_float(_receipt_or_payload(receipt, payload, "q_lcb_5pct")),
                c_fee_adjusted=_optional_float(_receipt_or_payload(receipt, payload, "c_fee_adjusted")),
                c_cost_95pct=_optional_float(_receipt_or_payload(receipt, payload, "c_cost_95pct")),
                p_fill_lcb=_optional_float(_receipt_or_payload(receipt, payload, "p_fill_lcb")),
                trade_score=_optional_float(_receipt_or_payload(receipt, payload, "trade_score")),
                native_quote_available=_optional_bool(_receipt_or_payload(receipt, payload, "native_quote_available")),
                source_status=_receipt_or_payload(receipt, payload, "source_status"),
                family_complete=_optional_bool(_receipt_or_payload(receipt, payload, "family_complete")),
                hypothetical_order_type=payload.get("hypothetical_order_type"),
                hypothetical_fill_status=payload.get("hypothetical_fill_status"),
                hypothetical_fill_price=_optional_float(payload.get("hypothetical_fill_price")),
                causal_snapshot_id=event.causal_snapshot_id,
                executable_snapshot_id=_receipt_or_payload(receipt, payload, "executable_snapshot_id"),
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


def _receipt_or_payload(
    receipt: EventSubmissionReceipt | None,
    payload: dict[str, Any],
    field_name: str,
) -> Any:
    if receipt is not None and hasattr(receipt, field_name):
        value = getattr(receipt, field_name)
        if value is not None:
            return value
    return payload.get(field_name)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes"}:
        return True
    if lowered in {"0", "false", "no"}:
        return False
    return None


def _submission_receipt(
    event: OpportunityEvent,
    submit_result: bool | None | EventSubmissionReceipt,
) -> EventSubmissionReceipt | None:
    if isinstance(submit_result, EventSubmissionReceipt):
        return submit_result
    if submit_result is False:
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            reason="NO_SUBMIT_PROOF_FALSE",
        )
    if submit_result is None:
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            reason="legacy_injected_test_submit",
        )
    if submit_result is True:
        return None
    return None


def _receipt_matches_event(event: OpportunityEvent, receipt: EventSubmissionReceipt) -> bool:
    if receipt.event_id != event.event_id:
        return False
    if event.causal_snapshot_id and receipt.causal_snapshot_id != event.causal_snapshot_id:
        return False
    payload = _payload_dict(event)
    for field in ("city", "target_date", "metric", "condition_id", "token_id"):
        expected = payload.get(field)
        observed = getattr(receipt, field)
        if expected and observed != expected:
            return False
    executable_snapshot_id = payload.get("executable_snapshot_id")
    if executable_snapshot_id and receipt.executable_snapshot_id != executable_snapshot_id:
        return False
    return True


def _receipt_money_path_blocker(receipt: EventSubmissionReceipt) -> tuple[str | None, str]:
    if receipt.side_effect_status in {"COMMAND_CREATED", "SUBMITTED"}:
        return "EXECUTOR_EXPRESSIBILITY", receipt.reason or "EDLI_REAL_ORDER_SIDE_EFFECT_FORBIDDEN"
    if not receipt.trade_score_positive:
        return "TRADE_SCORE", receipt.reason or "TRADE_SCORE_BLOCKED"
    if not receipt.fdr_pass or not receipt.fdr_family_id or receipt.fdr_hypothesis_count <= 0:
        return "FDR", receipt.reason or "FDR_REJECTED"
    if receipt.kelly_execution_price_type != "ExecutionPrice" or receipt.kelly_price_fee_deducted is not True:
        return "KELLY", receipt.reason or "EDLI_KELLY_PROOF_MISSING"
    if not receipt.kelly_cost_basis_id:
        return "KELLY", receipt.reason or "EDLI_KELLY_COST_BASIS_MISSING"
    if not receipt.kelly_pass or receipt.kelly_size_usd <= 0.0:
        return "KELLY", receipt.reason or "KELLY_TOO_SMALL"
    if not receipt.final_intent_id:
        return "EXECUTOR_EXPRESSIBILITY", receipt.reason or "FINAL_INTENT_RECEIPT_MISSING"
    return None, ""


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
        and payload.get("live_authority_status") == "LIVE_AUTHORITY"
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
