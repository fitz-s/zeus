"""Engine adapter for EDLI opportunity reactor construction.

The adapter connects EDLI events to the event-bound no-submit proof kernel. It
does not call the broad cycle runner and it does not cross the executor or venue
side-effect boundary.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Callable

from src.contracts.execution_price import ExecutionPrice
from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.compiler import AuthorityEvidence, EvidenceClock, NoSubmitProofBundle
from src.engine.event_bound_final_intent import (
    EventBoundFinalIntent,
    build_event_bound_final_intent_receipt,
    serialize_event_bound_final_intent_receipt,
)
from src.events.candidate_binding import MarketTopologyCandidate
from src.events.decision_engine import EventBoundDecisionEngine, EventBoundDecisionRequest
from src.events.event_store import EventStore
from src.events.money_path_adapters import evaluate_fdr_full_family, evaluate_kelly, evaluate_riskguard
from src.events.opportunity_event import OpportunityEvent
from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor, ReactorConfig
from src.riskguard.risk_level import RiskLevel
from src.types.market import Bin


UTC = timezone.utc


@dataclass(frozen=True)
class _CandidateProof:
    candidate: MarketTopologyCandidate
    token_id: str
    direction: str
    row: dict[str, Any] | None
    executable_snapshot_id: str | None
    execution_price: ExecutionPrice | None
    q_posterior: float
    q_lcb_5pct: float
    c_cost_95pct: float | None
    p_fill_lcb: float
    trade_score: float
    p_value: float
    passed_prefilter: bool
    native_quote_available: bool
    missing_reason: str | None = None


def build_event_reactor(
    store: EventStore,
    *,
    source_truth_gate,
    executable_snapshot_gate,
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
    topology_conn: sqlite3.Connection | None = None,
) -> Callable[[OpportunityEvent, datetime], bool]:
    """Return a gate requiring a fresh executable snapshot bound to the event."""

    fallback_checked_at = now.astimezone(UTC) if now is not None else None

    def _gate(event: OpportunityEvent, decision_time: datetime) -> bool:
        if topology_conn is None:
            return False
        checked_at = (
            decision_time.astimezone(UTC)
            if decision_time.tzinfo is not None and decision_time.utcoffset() is not None
            else fallback_checked_at
        )
        if checked_at is None:
            return False
        if not _table_exists(trade_conn, "executable_market_snapshots"):
            return False
        columns = _table_columns(trade_conn, "executable_market_snapshots")
        required = {"freshness_deadline", "yes_token_id", "no_token_id"}
        if not required <= columns:
            return False
        payload = _payload(event)
        family_topology_rows = _event_family_market_topology_rows(topology_conn, payload)
        if not family_topology_rows:
            return False
        condition_ids = tuple(str(row.get("condition_id") or "") for row in family_topology_rows)
        rows = _latest_snapshot_rows_for_event_family(
            trade_conn,
            event,
            condition_ids=condition_ids,
            fresh_at=checked_at,
        )
        if not rows:
            return False
        if sorted(set(condition_ids)) != sorted(_snapshot_token_maps_by_condition(rows)):
            return False
        return _selected_snapshot_row_for_event(rows, payload) is not None

    return _gate


def riskguard_allows_new_entries(*, get_current_level: Callable[[], RiskLevel]) -> Callable[[OpportunityEvent], bool]:
    """Return a reactor gate that preserves RiskGuard's entry-blocking law."""

    def _gate(_event: OpportunityEvent) -> bool:
        return get_current_level() == RiskLevel.GREEN

    return _gate


def edli_trade_score_gate(event: OpportunityEvent) -> bool:
    """TradeScore is generated inside the event-bound no-submit adapter.

    Forecast and Day0 events are causal facts; they must not carry q/c/FDR/Kelly
    proof fields as event-authoritative payload data.
    """

    return event.event_type in {"FORECAST_SNAPSHOT_READY", "DAY0_EXTREME_UPDATED"}


def event_bound_no_submit_adapter_from_trade_conn(
    trade_conn: sqlite3.Connection,
    *,
    get_current_level: Callable[[], RiskLevel],
    forecast_conn: sqlite3.Connection | None = None,
    topology_conn: sqlite3.Connection | None = None,
    calibration_conn: sqlite3.Connection | None = None,
    bankroll_usd_provider: Callable[[], float | None] | None = None,
) -> Callable[[OpportunityEvent, datetime], EventSubmissionReceipt]:
    """Build a proof-only final-intent receipt adapter for EDLI events."""

    def _submit(event: OpportunityEvent, decision_time: datetime) -> EventSubmissionReceipt:
        return build_event_bound_no_submit_receipt(
            event,
            trade_conn=trade_conn,
            decision_time=decision_time,
            forecast_conn=forecast_conn,
            topology_conn=topology_conn,
            calibration_conn=calibration_conn,
            get_current_level=get_current_level,
            bankroll_usd_provider=bankroll_usd_provider,
        )

    return _submit


def build_event_bound_no_submit_receipt(
    event: OpportunityEvent,
    *,
    trade_conn: sqlite3.Connection,
    decision_time: datetime,
    get_current_level: Callable[[], RiskLevel],
    forecast_conn: sqlite3.Connection | None = None,
    topology_conn: sqlite3.Connection | None = None,
    calibration_conn: sqlite3.Connection | None = None,
    bankroll_usd_provider: Callable[[], float | None] | None = None,
) -> EventSubmissionReceipt:
    """Produce a typed no-submit EDLI proof without running the cycle runner."""

    decision_time = decision_time.astimezone(UTC)
    payload = _payload(event)
    if forecast_conn is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="FORECAST_AUTHORITY_CONNECTION_MISSING")
    if topology_conn is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="TOPOLOGY_AUTHORITY_CONNECTION_MISSING")
    if calibration_conn is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="CALIBRATION_AUTHORITY_CONNECTION_MISSING")
    source_conn = forecast_conn
    topology_authority_conn = topology_conn
    family_topology_rows = _event_family_market_topology_rows(topology_authority_conn, payload)
    if not family_topology_rows:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EVENT_BOUND_MARKET_TOPOLOGY_MISSING")
    family_condition_ids = tuple(str(row.get("condition_id") or "") for row in family_topology_rows)
    family_rows = _latest_snapshot_rows_for_event_family(
        trade_conn,
        event,
        condition_ids=family_condition_ids,
        fresh_at=decision_time,
    )
    if not family_rows:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EVENT_BOUND_EXECUTABLE_SNAPSHOT_MISSING")
    snapshot_token_maps = _snapshot_token_maps_by_condition(family_rows)
    missing_snapshot_conditions = sorted(set(family_condition_ids) - set(snapshot_token_maps))
    if missing_snapshot_conditions:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="FDR_FULL_FAMILY_PROOF_MISSING:missing executable snapshots for sibling conditions "
            + ",".join(missing_snapshot_conditions),
            family_complete=False,
        )
    try:
        topology = tuple(
            _topology_candidate_from_market_event(row, snapshot_token_maps[str(row.get("condition_id") or "")], payload)
            for row in family_topology_rows
        )
    except ValueError as exc:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"EVENT_BOUND_MARKET_TOPOLOGY_INVALID:{exc}",
        )
    row = _selected_snapshot_row_for_event(family_rows, payload)
    if row is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EVENT_BOUND_SELECTED_SNAPSHOT_MISSING")
    decision = EventBoundDecisionEngine().evaluate(
        EventBoundDecisionRequest(
            event=event,
            market_topology=topology,
            decision_time=decision_time,
            market_topology_source="executable_market_snapshots",
        )
    )
    if decision.status != "CANDIDATE_FAMILY_READY" or decision.candidate_family is None:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=decision.rejection_reason or "EVENT_BOUND_CANDIDATE_BINDING_FAILED",
        )
    family = decision.candidate_family
    try:
        proofs = _generate_candidate_proofs(
            event=event,
            payload=payload,
            family=family,
            snapshot_rows=family_rows,
            trade_conn=trade_conn,
            forecast_conn=source_conn,
            calibration_conn=calibration_conn,
            decision_time=decision_time,
        )
    except ValueError as exc:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"LIVE_INFERENCE_INPUTS_MISSING:{exc}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            family_id=family.family_id,
            source_status="MATCH",
            family_complete=True,
        )
    proof = _selected_candidate_proof(payload, proofs)
    if proof is None:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="EVENT_BOUND_SELECTED_CANDIDATE_MISSING",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            family_id=family.family_id,
            source_status="MATCH",
            family_complete=True,
        )
    candidate = proof.candidate
    selected_token_id = proof.token_id
    direction = proof.direction
    execution_price = proof.execution_price
    if execution_price is None:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"EXECUTABLE_NATIVE_ASK_MISSING:{proof.missing_reason or 'native executable quote unavailable'}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=None,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=proof.trade_score,
            native_quote_available=False,
            source_status="MATCH",
            family_complete=True,
        )
    trade_score = proof.trade_score
    if trade_score <= 0.0:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="TRADE_SCORE_NON_POSITIVE",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
        )
    hypothesis_id = f"{family.family_id}:{selected_token_id}"
    try:
        fdr = evaluate_fdr_full_family(
            family_id=family.family_id,
            all_hypothesis_ids=tuple(
                f"{family.family_id}:{token}" for token in family.yes_token_ids + family.no_token_ids
            ),
            selected_hypothesis_ids=(hypothesis_id,),
            hypothesis_p_values={f"{family.family_id}:{candidate.token_id}": candidate.p_value for candidate in proofs},
            passed_prefilter={
                f"{family.family_id}:{candidate.token_id}": candidate.passed_prefilter for candidate in proofs
            },
        )
    except (TypeError, ValueError) as exc:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"FDR_FULL_FAMILY_PROOF_MISSING:{exc}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=False,
        )
    if not fdr.passed:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="FDR_REJECTED",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
            fdr_pass=False,
            fdr_family_id=fdr.fdr_family_id,
            fdr_hypothesis_count=fdr.attempted_hypotheses,
        )
    kelly_cost_basis_id = f"edli_cost:{event.event_id}:{selected_token_id}"
    try:
        bankroll_usd = (
            _bankroll_usd_from_provider(bankroll_usd_provider)
            if bankroll_usd_provider is not None
            else _runtime_bankroll_usd(cached_only=True)
        )
        kelly_multiplier = _runtime_kelly_multiplier()
        kelly = evaluate_kelly(
            kelly_decision_id=f"edli_kelly:{event.event_id}:{selected_token_id}",
            p_posterior=proof.q_posterior,
            execution_price=execution_price,
            bankroll_usd=bankroll_usd,
            kelly_multiplier=kelly_multiplier,
        )
    except (TypeError, ValueError) as exc:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"KELLY_PROOF_MISSING:{exc}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
        )
    if not kelly.passed:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="KELLY_REJECTED",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
            fdr_pass=True,
            fdr_family_id=fdr.fdr_family_id,
            fdr_hypothesis_count=fdr.attempted_hypotheses,
            kelly_pass=False,
            kelly_execution_price_type=execution_price.__class__.__name__,
            kelly_price_fee_deducted=execution_price.fee_deducted,
            kelly_size_usd=kelly.size_usd,
            kelly_cost_basis_id=kelly_cost_basis_id,
        )
    risk = evaluate_riskguard(
        risk_decision_id=f"edli_risk:{event.event_id}:{selected_token_id}",
        level=get_current_level(),
    )
    if not risk.passed:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="RISK_GUARD_BLOCKED")
    intent = EventBoundFinalIntent(
        final_intent_id=f"edli_intent:{event.event_id}:{selected_token_id}",
        event_id=event.event_id,
        family_id=family.family_id,
        candidate_id=f"{family.family_id}:{candidate.condition_id}",
        condition_id=str(candidate.condition_id or ""),
        token_id=selected_token_id,
        direction=direction,
        executable_snapshot_id=str(proof.executable_snapshot_id or ""),
        execution_price=execution_price,
    )
    typed_receipt = build_event_bound_final_intent_receipt(
        intent=intent,
        causal_snapshot_id=str(event.causal_snapshot_id or ""),
        trade_score_id=f"edli_trade_score:{event.event_id}:{selected_token_id}",
        fdr_family_id=fdr.fdr_family_id,
        kelly_decision_id=kelly.kelly_decision_id,
        risk_decision_id=risk.risk_decision_id,
        live_submit_enabled=False,
    )
    raw_receipt = serialize_event_bound_final_intent_receipt(
        typed_receipt,
        trade_score_positive=True,
        fdr_pass=fdr.passed,
        fdr_hypothesis_count=fdr.attempted_hypotheses,
        kelly_pass=kelly.passed,
        kelly_size_usd=kelly.size_usd,
        kelly_cost_basis_id=kelly_cost_basis_id,
    )
    raw_receipt.update(
        {
            "city": family.city,
            "target_date": family.target_date,
            "metric": family.metric,
            "bin_label": candidate.bin.label,
            "outcome_label": "NO" if selected_token_id == candidate.no_token_id else "YES",
            "q_live": proof.q_posterior,
            "q_lcb_5pct": proof.q_lcb_5pct,
            "c_fee_adjusted": execution_price.value,
            "c_cost_95pct": proof.c_cost_95pct,
            "p_fill_lcb": proof.p_fill_lcb,
            "trade_score": trade_score,
            "native_quote_available": True,
            "source_status": "MATCH",
            "family_complete": True,
        }
    )
    proof_bundle = _build_no_submit_proof_bundle_from_adapter_evidence(
        event=event,
        payload=payload,
        decision_time=decision_time,
        family=family,
        family_topology_rows=family_topology_rows,
        family_snapshot_rows=family_rows,
        selected_snapshot_row=row,
        forecast_conn=source_conn,
        calibration_conn=calibration_conn,
        proof=proof,
        raw_receipt=raw_receipt,
        fdr=fdr,
        kelly=kelly,
        risk=risk,
        bankroll_usd=bankroll_usd,
        kelly_multiplier=kelly_multiplier,
    )
    return _event_submission_receipt_from_typed_receipt_payload(
        raw_receipt,
        event,
        decision_proof_bundle=proof_bundle,
    )


def _event_submission_receipt_from_typed_receipt_payload(
    raw_receipt: dict[str, Any],
    event: OpportunityEvent,
    *,
    decision_proof_bundle: NoSubmitProofBundle | None = None,
) -> EventSubmissionReceipt:
    schema = str(raw_receipt.get("schema") or "")
    if schema != "edli_event_bound_no_submit_v1":
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="EDLI_EVENT_BOUND_RECEIPT_SCHEMA_INVALID",
        )
    if str(raw_receipt.get("side_effect_status") or "") != "NO_SUBMIT":
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="EDLI_EVENT_BOUND_RECEIPT_NOT_NO_SUBMIT",
        )
    return EventSubmissionReceipt(
        submitted=bool(raw_receipt.get("submitted")),
        event_id=str(raw_receipt.get("event_id") or ""),
        causal_snapshot_id=raw_receipt.get("causal_snapshot_id"),
        city=raw_receipt.get("city"),
        target_date=raw_receipt.get("target_date"),
        metric=raw_receipt.get("metric"),
        condition_id=raw_receipt.get("condition_id"),
        token_id=raw_receipt.get("token_id"),
        outcome_label=raw_receipt.get("outcome_label"),
        candidate_id=raw_receipt.get("candidate_id"),
        executable_snapshot_id=raw_receipt.get("executable_snapshot_id"),
        family_id=raw_receipt.get("family_id"),
        bin_label=raw_receipt.get("bin_label"),
        direction=raw_receipt.get("direction"),
        q_live=_optional_float(raw_receipt.get("q_live")),
        q_lcb_5pct=_optional_float(raw_receipt.get("q_lcb_5pct")),
        c_fee_adjusted=_optional_float(raw_receipt.get("c_fee_adjusted")),
        c_cost_95pct=_optional_float(raw_receipt.get("c_cost_95pct")),
        p_fill_lcb=_optional_float(raw_receipt.get("p_fill_lcb")),
        trade_score=_optional_float(raw_receipt.get("trade_score")),
        native_quote_available=_optional_bool(raw_receipt.get("native_quote_available")),
        source_status=raw_receipt.get("source_status"),
        family_complete=_optional_bool(raw_receipt.get("family_complete")),
        trade_score_positive=bool(raw_receipt.get("trade_score_positive")),
        fdr_pass=bool(raw_receipt.get("fdr_pass")),
        fdr_family_id=raw_receipt.get("fdr_family_id"),
        fdr_hypothesis_count=int(raw_receipt.get("fdr_hypothesis_count") or 0),
        kelly_pass=bool(raw_receipt.get("kelly_pass")),
        kelly_execution_price_type=raw_receipt.get("kelly_execution_price_type"),
        kelly_price_fee_deducted=bool(raw_receipt.get("kelly_price_fee_deducted")),
        kelly_size_usd=float(raw_receipt.get("kelly_size_usd") or 0.0),
        kelly_cost_basis_id=raw_receipt.get("kelly_cost_basis_id"),
        kelly_decision_id=raw_receipt.get("kelly_decision_id"),
        risk_decision_id=raw_receipt.get("risk_decision_id"),
        final_intent_id=raw_receipt.get("final_intent_id"),
        side_effect_status="NO_SUBMIT",
        reason=str(raw_receipt.get("reason") or "event_bound_final_intent_no_submit"),
        proof_accepted=bool(raw_receipt.get("proof_accepted")),
        decision_proof_bundle=decision_proof_bundle,
    )


def _build_no_submit_proof_bundle_from_adapter_evidence(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    decision_time: datetime,
    family,
    family_topology_rows: list[dict[str, Any]],
    family_snapshot_rows: list[dict[str, Any]],
    selected_snapshot_row: dict[str, Any],
    forecast_conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    proof: _CandidateProof,
    raw_receipt: dict[str, Any],
    fdr,
    kelly,
    risk,
    bankroll_usd: float,
    kelly_multiplier: float,
) -> NoSubmitProofBundle:
    event_clock = EvidenceClock(
        source_available_at=_parse_utc(event.available_at) or decision_time,
        agent_received_at=_parse_utc(event.received_at) or decision_time,
        persisted_at=_parse_utc(event.created_at) or decision_time,
    )
    decision_clock = EvidenceClock(decision_time, decision_time, decision_time)
    quote_clock = _evidence_clock_from_row(selected_snapshot_row, fallback=decision_time)
    forecast_payload, forecast_clock = _forecast_authority_payload_and_clock(
        forecast_conn,
        event=event,
        family=family,
        payload=payload,
        decision_time=decision_time,
    )
    calibration_payload, calibration_clock = _calibration_authority_payload_and_clock(
        calibration_conn,
        event=event,
        family=family,
        payload=payload,
        forecast_payload=forecast_payload,
        decision_time=decision_time,
    )
    projection = {
        "event_id": raw_receipt.get("event_id"),
        "final_intent_id": raw_receipt.get("final_intent_id"),
        "side_effect_status": raw_receipt.get("side_effect_status"),
        "proof_accepted": raw_receipt.get("proof_accepted"),
        "submitted": raw_receipt.get("submitted"),
        "executable_snapshot_id": raw_receipt.get("executable_snapshot_id"),
    }
    projection["projection_hash"] = stable_hash(projection)
    condition_ids = tuple(str(row.get("condition_id") or "") for row in family_topology_rows)
    executable_snapshot_ids = tuple(sorted(str(row.get("snapshot_id") or "") for row in family_snapshot_rows))
    hypothesis_id = f"{family.family_id}:{proof.token_id}"
    execution_price = proof.execution_price
    topology_clock = _evidence_clock_from_rows(family_topology_rows, fallback=decision_time)
    bin_labels_hash = stable_hash(tuple(str(candidate.bin.label) for candidate in family.candidates))
    market_analysis_config_hash = stable_hash(
        {
            "posterior_mode": MODEL_ONLY_POSTERIOR_MODE,
            "edge_bootstrap_n": edge_n_bootstrap(),
            "family_id": family.family_id,
        }
    )
    return NoSubmitProofBundle(
        final_intent_id=str(raw_receipt.get("final_intent_id") or ""),
        source_truth=AuthorityEvidence(
            claims.SOURCE_TRUTH,
            "source_truth",
            "source_truth",
            {
                "identity": event.source,
                "event_source": event.source,
                "event_type": event.event_type,
                "source_status": "MATCH",
                "completeness_status": payload.get("completeness_status"),
                "required_fields_present": payload.get("required_fields_present"),
                "required_steps_present": payload.get("required_steps_present"),
                "source_id": payload.get("source_id"),
                "source_run_id": payload.get("source_run_id"),
                "snapshot_id": payload.get("snapshot_id") or event.causal_snapshot_id,
                "payload_hash": event.payload_hash,
                "causal_snapshot_id": event.causal_snapshot_id,
                "available_at": event.available_at,
                "received_at": event.received_at,
                "event_id": event.event_id,
            },
            event_clock,
            "zeus.events.source_truth_gate",
            algorithm_id="decision_kernel.source_truth.event_bound_adapter",
        ),
        market_topology=AuthorityEvidence(
            claims.MARKET_TOPOLOGY,
            "market_topology",
            "market_topology",
            {
                "identity": family.family_id,
                "family_id": family.family_id,
                "condition_ids": condition_ids,
                "candidate_count": len(tuple(family.candidates)),
                "source_table": "market_events_v2",
                "event_id": event.event_id,
            },
            topology_clock,
            "zeus.forecasts.market_events_v2",
            algorithm_id="decision_kernel.topology.event_bound_adapter",
        ),
        family_closure=AuthorityEvidence(
            claims.FAMILY_CLOSURE,
            "family_closure",
            "family_closure",
            {
                "identity": family.family_id,
                "family_id": family.family_id,
                "condition_ids": condition_ids,
                "yes_token_ids": tuple(family.yes_token_ids),
                "no_token_ids": tuple(family.no_token_ids),
                "sibling_hypothesis_count": len(tuple(family.yes_token_ids)) + len(tuple(family.no_token_ids)),
                "family_complete": True,
                "bin_labels_hash": bin_labels_hash,
                "event_id": event.event_id,
            },
            topology_clock,
            "zeus.events.candidate_binding",
            algorithm_id="decision_kernel.family_closure.event_bound_adapter",
        ),
        forecast_authority=AuthorityEvidence(
            claims.FORECAST_AUTHORITY,
            "forecast_authority",
            "forecast_authority",
            forecast_payload,
            forecast_clock,
            "zeus.data.executable_forecast_reader",
            algorithm_id="decision_kernel.forecast_authority.event_bound_adapter",
        ),
        calibration=AuthorityEvidence(
            claims.CALIBRATION,
            "calibration",
            "calibration",
            calibration_payload,
            calibration_clock,
            "zeus.calibration.manager",
            algorithm_id="decision_kernel.calibration.event_bound_adapter",
        ),
        model_config=AuthorityEvidence(
            claims.MODEL_CONFIG,
            "model_config",
            "model_config",
            {
                "identity": "event_bound_no_submit_v1",
                "posterior_mode": MODEL_ONLY_POSTERIOR_MODE,
                "edge_bootstrap_n": edge_n_bootstrap(),
                "kelly_multiplier": kelly_multiplier,
                "market_analysis_config_hash": market_analysis_config_hash,
                "calibration_input_space": calibration_payload.get("input_space"),
            },
            decision_clock,
            "zeus.config.settings",
            algorithm_id="decision_kernel.model_config.event_bound_adapter",
        ),
        belief=AuthorityEvidence(
            claims.BELIEF,
            "belief",
            "belief",
            {
                "identity": hypothesis_id,
                "q_live": proof.q_posterior,
                "q_lcb_5pct": proof.q_lcb_5pct,
                "p_value": proof.p_value,
                "passed_prefilter": proof.passed_prefilter,
                "forecast_snapshot_id": forecast_payload.get("snapshot_id"),
                "calibrator_model_key": calibration_payload.get("calibrator_model_key"),
                "p_cal_hash": stable_hash({"q_live": proof.q_posterior, "q_lcb_5pct": proof.q_lcb_5pct}),
                "p_live_hash": stable_hash({"q_live": proof.q_posterior}),
                "bin_labels_hash": bin_labels_hash,
                "market_analysis_config_hash": market_analysis_config_hash,
                "bootstrap_n": edge_n_bootstrap(),
            },
            forecast_clock,
            "zeus.strategy.market_analysis_family_scan",
            algorithm_id="decision_kernel.belief.event_bound_adapter",
        ),
        executable_snapshot=AuthorityEvidence(
            claims.EXECUTABLE_SNAPSHOT,
            "executable_snapshot",
            "executable_snapshot",
            {
                "identity": proof.executable_snapshot_id,
                "selected_snapshot_id": proof.executable_snapshot_id,
                "family_snapshot_ids": executable_snapshot_ids,
                "condition_id": raw_receipt.get("condition_id"),
                "token_id": raw_receipt.get("token_id"),
                "cost_basis_id": raw_receipt.get("kelly_cost_basis_id"),
                "orderbook_hash": _hash_jsonish(selected_snapshot_row.get("orderbook_depth_json") or selected_snapshot_row.get("orderbook_depth_jsonb")),
                "fee_details_hash": _hash_jsonish(selected_snapshot_row.get("fee_details_json") or selected_snapshot_row.get("fee_details")),
                "min_tick_size": selected_snapshot_row.get("min_tick_size"),
                "min_order_size": selected_snapshot_row.get("min_order_size"),
                "neg_risk": selected_snapshot_row.get("neg_risk"),
                "captured_at": selected_snapshot_row.get("captured_at"),
                "freshness_deadline": selected_snapshot_row.get("freshness_deadline"),
                "active": selected_snapshot_row.get("active"),
                "closed": selected_snapshot_row.get("closed"),
            },
            quote_clock,
            "zeus.trades.executable_market_snapshots",
            algorithm_id="decision_kernel.executable_snapshot.event_bound_adapter",
        ),
        quote_feasibility=AuthorityEvidence(
            claims.QUOTE_FEASIBILITY,
            "quote_feasibility",
            "quote_feasibility",
            {
                "identity": hypothesis_id,
                "condition_id": raw_receipt.get("condition_id"),
                "token_id": raw_receipt.get("token_id"),
                "direction": proof.direction,
                "native_side": _native_side_for_direction(proof.direction),
                "selected_token_id": proof.token_id,
                "quote_depth_hash": _hash_jsonish(selected_snapshot_row.get("orderbook_depth_json") or selected_snapshot_row.get("orderbook_depth_jsonb")),
                "p_fill_lcb_policy_id": "edli_v1.no_submit_visible_depth_fill_lcb",
                "native_quote_available": proof.native_quote_available,
                "execution_price_type": execution_price.__class__.__name__ if execution_price is not None else None,
                "execution_price_value": execution_price.value if execution_price is not None else None,
                "fee_deducted": execution_price.fee_deducted if execution_price is not None else None,
                "p_fill_lcb": proof.p_fill_lcb,
            },
            quote_clock,
            "zeus.strategy.live_inference.executable_cost",
            algorithm_id="decision_kernel.quote_feasibility.event_bound_adapter",
        ),
        cost_model=AuthorityEvidence(
            claims.COST_MODEL,
            "cost_model",
            "cost_model",
            {
                "identity": str(raw_receipt.get("kelly_cost_basis_id") or hypothesis_id),
                "cost_basis_id": raw_receipt.get("kelly_cost_basis_id"),
                "condition_id": raw_receipt.get("condition_id"),
                "token_id": raw_receipt.get("token_id"),
                "execution_price_type": execution_price.__class__.__name__ if execution_price is not None else None,
                "price_fee_deducted": execution_price.fee_deducted if execution_price is not None else None,
                "c_fee_adjusted": raw_receipt.get("c_fee_adjusted"),
                "c_cost_95pct": proof.c_cost_95pct,
            },
            quote_clock,
            "zeus.contracts.execution_price",
            algorithm_id="decision_kernel.cost_model.event_bound_adapter",
        ),
        pre_trade_evidence=AuthorityEvidence(
            claims.PRE_TRADE_EVIDENCE,
            "pre_trade_evidence",
            "pre_trade_evidence",
            {
                "identity": hypothesis_id,
                "quote_edge_bound": proof.trade_score,
                "conditional_edge_given_fill": None,
                "no_submit_trade_score_evidence": proof.trade_score,
                "actionable_trade_score": 0.0,
            },
            decision_clock,
            "zeus.strategy.market_analysis_family_scan",
            algorithm_id="decision_kernel.pre_trade_evidence.event_bound_adapter",
        ),
        candidate_evidence=AuthorityEvidence(
            claims.CANDIDATE_EVIDENCE,
            "candidate_evidence",
            "candidate_evidence",
            {
                "identity": hypothesis_id,
                "candidate_id": raw_receipt.get("candidate_id"),
                "family_id": family.family_id,
                "condition_id": raw_receipt.get("condition_id"),
                "bin_label": raw_receipt.get("bin_label"),
                "selected_token_id": proof.token_id,
                "direction": proof.direction,
                "hypothesis_id": hypothesis_id,
            },
            decision_clock,
            "zeus.events.decision_engine",
            algorithm_id="decision_kernel.candidate_evidence.event_bound_adapter",
        ),
        testing_protocol=AuthorityEvidence(
            claims.TESTING_PROTOCOL,
            "testing_protocol",
            "testing_protocol",
            {
                "identity": family.family_id,
                "testing_protocol_id": f"edli_testing:{family.family_id}",
                "family_id": family.family_id,
                "mode": "FIXED_WINDOW_BH",
                "optional_stopping_valid": True,
                "sibling_hypothesis_count": fdr.attempted_hypotheses,
            },
            decision_clock,
            "zeus.strategy.fdr_filter",
            algorithm_id="decision_kernel.testing_protocol.event_bound_adapter",
        ),
        fdr=AuthorityEvidence(
            claims.FDR,
            "fdr",
            "fdr",
            {
                "identity": fdr.fdr_family_id,
                "fdr_family_id": fdr.fdr_family_id,
                "selected_hypotheses": tuple(fdr.selected_hypotheses),
                "selected_post_fdr": tuple(fdr.selected_post_fdr),
                "fdr_hypothesis_count": fdr.attempted_hypotheses,
                "edge_bootstrap_n": edge_n_bootstrap(),
                "passed": fdr.passed,
            },
            decision_clock,
            "zeus.strategy.fdr_filter",
            algorithm_id="decision_kernel.fdr.event_bound_adapter",
        ),
        kelly_dry_run=AuthorityEvidence(
            claims.KELLY_DRY_RUN,
            "kelly_dry_run",
            "kelly_dry_run",
            {
                "identity": kelly.kelly_decision_id,
                "kelly_decision_id": kelly.kelly_decision_id,
                "kelly_size_usd": kelly.size_usd,
                "bankroll_usd": bankroll_usd,
                "kelly_multiplier": kelly_multiplier,
                "cost_basis_id": raw_receipt.get("kelly_cost_basis_id"),
                "execution_price_type": execution_price.__class__.__name__ if execution_price is not None else None,
                "price_fee_deducted": execution_price.fee_deducted if execution_price is not None else None,
            },
            decision_clock,
            "zeus.strategy.kelly",
            algorithm_id="decision_kernel.kelly.event_bound_adapter",
        ),
        risk_level=AuthorityEvidence(
            claims.RISK_LEVEL,
            "risk_level",
            "risk_level",
            {
                "identity": risk.risk_decision_id,
                "risk_decision_id": risk.risk_decision_id,
                "risk_level": risk.level.name,
                "passed": risk.passed,
                "final_intent_id": raw_receipt.get("final_intent_id"),
            },
            decision_clock,
            "zeus.riskguard.risk_level",
            algorithm_id="decision_kernel.risk.event_bound_adapter",
        ),
        no_submit_projection=projection,
    )


def _forecast_authority_payload_and_clock(
    conn: sqlite3.Connection,
    *,
    event: OpportunityEvent,
    family,
    payload: dict[str, object],
    decision_time: datetime,
) -> tuple[dict[str, Any], EvidenceClock]:
    allow_latest = event.event_type == "DAY0_EXTREME_UPDATED"
    snapshot = _forecast_snapshot_row_for_event(
        conn,
        event=event,
        family=family,
        allow_latest=allow_latest,
        decision_time=decision_time,
    )
    if snapshot is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:snapshot")
    source_run_id = _nonnull(snapshot.get("source_run_id") or payload.get("source_run_id"))
    source_run_table = _authority_table_ref(conn, "source_run")
    coverage_table = _authority_table_ref(conn, "source_run_coverage")
    if not source_run_id or source_run_table is None or coverage_table is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:scope")
    source_run = _row_by_id(conn, source_run_table, "source_run_id", source_run_id)
    if source_run is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:source_run")
    coverage = _coverage_row_for_snapshot(
        conn,
        coverage_table,
        source_run_id=source_run_id,
        family=family,
        snapshot=snapshot,
    )
    if coverage is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:coverage")
    result = _read_executable_forecast_bundle_result(
        conn,
        snapshot=snapshot,
        source_run=source_run,
        coverage=coverage,
        event=event,
        family=family,
        decision_time=decision_time,
    )
    if not result.ok or result.bundle is None:
        raise ValueError(f"FORECAST_AUTHORITY_EVIDENCE_MISSING:{result.reason_code}")
    evidence = result.bundle.evidence
    payload_out = {
        "identity": str(result.bundle.snapshot.snapshot_id),
        "snapshot_id": str(result.bundle.snapshot.snapshot_id),
        "reader_authority": "read_executable_forecast",
        "reader_status": result.status,
        "reader_reason_code": None if result.reason_code in {None, "", "OK", "LIVE_ELIGIBLE", "EXECUTABLE_FORECAST_READY"} else result.reason_code,
        "city": family.city,
        "target_date": family.target_date,
        "metric": family.metric,
        "forecast_source_id": evidence.forecast_source_id,
        "forecast_data_version": evidence.forecast_data_version,
        "source_transport": evidence.source_transport,
        "source_cycle_time": evidence.source_cycle_time,
        "source_issue_time": evidence.source_issue_time,
        "horizon_profile": snapshot.get("horizon_profile"),
        "source_run_id": evidence.source_run_id,
        "coverage_id": evidence.coverage_id,
        "producer_readiness_id": evidence.producer_readiness_id,
        "entry_readiness_id": evidence.entry_readiness_id,
        "input_snapshot_ids": tuple(str(item) for item in evidence.input_snapshot_ids),
        "raw_payload_hash": evidence.raw_payload_hash,
        "manifest_hash": evidence.manifest_hash,
        "required_steps": tuple(evidence.required_steps),
        "observed_steps": tuple(evidence.observed_steps),
        "expected_members": evidence.expected_members,
        "observed_members": evidence.observed_members,
        "source_run_status": evidence.source_run_status,
        "source_run_completeness_status": evidence.source_run_completeness_status,
        "coverage_completeness_status": evidence.coverage_completeness_status,
        "coverage_readiness_status": evidence.coverage_readiness_status,
        "applied_validations": tuple(evidence.applied_validations),
        "source_available_at": evidence.source_available_at,
        "fetch_started_at": evidence.fetch_started_at,
        "fetch_finished_at": evidence.fetch_finished_at,
        "captured_at": evidence.captured_at,
    }
    source_time = _parse_utc(evidence.source_available_at)
    agent_time = _parse_utc(evidence.fetch_finished_at) or _parse_utc(evidence.captured_at)
    persisted_time = (
        _parse_utc(source_run.get("imported_at"))
        or _parse_utc(coverage.get("computed_at"))
        or _parse_utc(evidence.captured_at)
    )
    if source_time is None or agent_time is None or persisted_time is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:clock")
    return payload_out, EvidenceClock(source_time, agent_time, persisted_time)


def _calibration_authority_payload_and_clock(
    calibration_conn: sqlite3.Connection,
    *,
    event: OpportunityEvent,
    family,
    payload: dict[str, object],
    forecast_payload: dict[str, Any],
    decision_time: datetime,
) -> tuple[dict[str, Any], EvidenceClock]:
    city = runtime_cities_by_name().get(family.city)
    if city is None:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:city")
    source_id = _nonnull(payload.get("source_id") or forecast_payload.get("forecast_source_id"))
    issue_time = _nonnull(
        payload.get("issue_time")
        or payload.get("source_cycle_time")
        or payload.get("cycle")
        or forecast_payload.get("source_issue_time")
        or forecast_payload.get("source_cycle_time")
    )
    if not source_id or not issue_time:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:forecast_provenance")
    from src.calibration.forecast_calibration_domain import derive_phase2_keys_from_ens_result
    from src.calibration.manager import get_calibrator
    from src.data.forecast_source_registry import calibration_source_id_for_lookup

    cycle, raw_source_id, horizon_profile = derive_phase2_keys_from_ens_result(
        {
            "issue_time": issue_time,
            "source_id": source_id,
            "horizon_profile": payload.get("horizon_profile") or forecast_payload.get("horizon_profile"),
        }
    )
    calibration_source_id = calibration_source_id_for_lookup(raw_source_id)
    if calibration_source_id is None:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:source_id")
    cal, level = get_calibrator(
        calibration_conn,
        city,
        str(family.target_date),
        temperature_metric=family.metric,
        cycle=cycle,
        source_id=calibration_source_id,
        horizon_profile=horizon_profile,
    )
    if cal is None:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:model")
    model_key = getattr(cal, "_bucket_model_key", None)
    row = _calibration_model_row(calibration_conn, model_key=model_key)
    if row is None:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:model_row")
    recorded_at = _parse_utc(row.get("recorded_at"))
    fitted_at = _parse_utc(row.get("fitted_at"))
    if recorded_at is None and fitted_at is None:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:clock")
    source_time = recorded_at or fitted_at
    persisted_time = recorded_at or fitted_at
    assert source_time is not None and persisted_time is not None
    payload_out = {
        "identity": str(model_key or ""),
        "calibrator_model_key": model_key,
        "calibrator_version": row.get("model_key"),
        "calibration_source_id": calibration_source_id,
        "raw_source_id": raw_source_id,
        "source_cycle": cycle,
        "horizon_profile": horizon_profile,
        "training_cutoff": row.get("training_cutoff") or row.get("fitted_at"),
        "model_available_at": row.get("recorded_at") or row.get("fitted_at"),
        "model_hash": _hash_jsonish({
            "model_key": row.get("model_key"),
            "param_A": row.get("param_A"),
            "param_B": row.get("param_B"),
            "param_C": row.get("param_C"),
            "bootstrap_params_json": row.get("bootstrap_params_json"),
        }),
        "maturity_level": level,
        "n_samples": row.get("n_samples"),
        "input_space": row.get("input_space"),
        "authority": row.get("authority"),
        "recorded_at": row.get("recorded_at"),
        "fitted_at": row.get("fitted_at"),
    }
    return payload_out, EvidenceClock(source_time, persisted_time, persisted_time)


def _generate_candidate_proofs(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    family,
    snapshot_rows: list[dict[str, Any]],
    trade_conn: sqlite3.Connection,
    forecast_conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    decision_time: datetime,
) -> tuple[_CandidateProof, ...]:
    q_by_condition, q_lcb_by_direction, canonical_p_values, canonical_prefilter = _live_yes_probabilities(
        event=event,
        payload=payload,
        family=family,
        conn=forecast_conn,
        calibration_conn=calibration_conn,
        native_costs=native_costs,
        decision_time=decision_time,
    )
    proofs: list[_CandidateProof] = []
    rows_by_condition = _snapshot_rows_by_condition(snapshot_rows)
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        yes_q = q_by_condition.get(condition_id)
        yes_lcb = q_lcb_by_direction.get((condition_id, "buy_yes"))
        no_lcb = q_lcb_by_direction.get((condition_id, "buy_no"))
        if yes_q is None or yes_lcb is None or no_lcb is None:
            raise ValueError(f"missing q_live for condition {condition_id}")
        row = rows_by_condition.get(condition_id)
        for token_id, direction, q_value, q_lcb in (
            (str(candidate.yes_token_id or ""), "buy_yes", yes_q, yes_lcb),
            (str(candidate.no_token_id or ""), "buy_no", 1.0 - yes_q, no_lcb),
        ):
            execution_price: ExecutionPrice | None = None
            c_cost_95pct: float | None = None
            p_fill_lcb = 0.0
            missing_reason: str | None = None
            if not token_id:
                missing_reason = "missing token id"
            elif row is None:
                missing_reason = "missing executable snapshot row"
            else:
                try:
                    execution_price, p_fill_lcb, c_cost_95pct = _execution_price_from_snapshot(
                        row,
                        selected_token_id=token_id,
                        direction=direction,
                    )
                except ValueError as exc:
                    missing_reason = str(exc)
            score = _robust_trade_score_from_generated_inputs(
                q_posterior=q_value,
                q_lcb_5pct=q_lcb,
                execution_price=execution_price,
                c_cost_95pct=c_cost_95pct,
                p_fill_lcb=p_fill_lcb,
            )
            p_value = canonical_p_values[(condition_id, direction)]
            passed_prefilter = bool(canonical_prefilter.get((condition_id, direction), execution_price is not None and score > 0.0))
            proofs.append(
                _CandidateProof(
                    candidate=candidate,
                    token_id=token_id,
                    direction=direction,
                    row=row,
                    executable_snapshot_id=str(row.get("snapshot_id") or "") if row is not None else None,
                    execution_price=execution_price,
                    q_posterior=q_value,
                    q_lcb_5pct=q_lcb,
                    c_cost_95pct=c_cost_95pct,
                    p_fill_lcb=p_fill_lcb,
                    trade_score=score,
                    p_value=p_value,
                    passed_prefilter=passed_prefilter,
                    native_quote_available=execution_price is not None,
                    missing_reason=missing_reason,
                )
            )
    return tuple(proofs)


def _selected_candidate_proof(payload: dict[str, object], proofs: tuple[_CandidateProof, ...]) -> _CandidateProof | None:
    requested_token = _nonnull(payload.get("token_id"))
    requested_condition = _nonnull(payload.get("condition_id"))
    if requested_token:
        for proof in proofs:
            if proof.token_id != requested_token:
                continue
            if requested_condition and str(proof.candidate.condition_id or "") != requested_condition:
                continue
            return proof
        return None
    executable = [proof for proof in proofs if proof.execution_price is not None]
    if not executable:
        return max(proofs, key=lambda proof: proof.q_lcb_5pct, default=None)
    return max(executable, key=lambda proof: (proof.trade_score, proof.q_lcb_5pct))


def _live_yes_probabilities(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    family,
    conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    decision_time: datetime,
) -> tuple[dict[str, float], dict[tuple[str, str], float], dict[tuple[str, str], float], dict[tuple[str, str], bool]]:
    canonical = _canonical_probability_and_fdr_proof(event=event, family=family, conn=conn)
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        return _forecast_snapshot_probability_and_fdr_proof(
            event=event,
            family=family,
            conn=conn,
            calibration_conn=calibration_conn,
            native_costs=native_costs,
            decision_time=decision_time,
        )
    if event.event_type == "DAY0_EXTREME_UPDATED":
        generated = _forecast_snapshot_probability_and_fdr_proof(
            event=event,
            family=family,
            conn=conn,
            calibration_conn=calibration_conn,
            native_costs=native_costs,
            allow_latest_snapshot=True,
            decision_time=decision_time,
        )
        q_by_condition, lcb_by_condition, p_values, prefilter = generated
        masked_q, masked_lcb, masked_p_values, masked_prefilter = _apply_day0_mask_to_generated_probabilities(
            payload=payload,
            family=family,
            q_by_condition=q_by_condition,
            lcb_by_condition=lcb_by_condition,
        )
        return masked_q, masked_lcb, p_values, prefilter
    raise ValueError(f"unsupported EDLI event type for inference: {event.event_type}")


def _canonical_probability_and_fdr_proof(
    *,
    event: OpportunityEvent,
    family,
    conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    decision_time: datetime,
    allow_latest_snapshot: bool = False,
) -> tuple[dict[str, float], dict[tuple[str, str], float], dict[tuple[str, str], float], dict[tuple[str, str], bool]]:
    snapshot = _forecast_snapshot_row_for_event(
        conn,
        event=event,
        family=family,
        allow_latest=allow_latest_snapshot,
        decision_time=decision_time,
    )
    if snapshot is None:
        if allow_latest_snapshot:
            raise ValueError("Day0 base forecast snapshot missing for event-bound inference")
        raise ValueError("causal forecast snapshot missing for event-bound inference")
    analysis = _market_analysis_from_event_snapshot(
        calibration_conn=calibration_conn,
        snapshot=snapshot,
        family=family,
        native_costs=native_costs,
        payload=_payload(event),
        decision_time=decision_time,
    )
    hypotheses = scan_full_hypothesis_family(analysis, n_bootstrap=edge_n_bootstrap())
    hypothesis_by_label_direction = {
        (hypothesis.range_label, hypothesis.direction): hypothesis
        for hypothesis in hypotheses
    }
    q_by_condition: dict[str, float] = {}
    lcb_by_direction: dict[tuple[str, str], float] = {}
    p_values: dict[tuple[str, str], float] = {}
    prefilter: dict[tuple[str, str], bool] = {}
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        range_label = candidate.bin.label
        yes_probability = probability_rows.get((range_label, "buy_yes"))
        yes_hypothesis = hypothesis_rows.get((range_label, "buy_yes"))
        if yes_probability is None:
            raise ValueError(f"canonical probability_trace_fact missing buy_yes row for {range_label}")
        if yes_hypothesis is None or yes_hypothesis.get("ci_lower") is None:
            raise ValueError(f"canonical selection_hypothesis_fact missing buy_yes CI row for {range_label}")
        q_by_condition[condition_id] = float(yes_probability["p_posterior"])
        for direction in ("buy_yes", "buy_no"):
            row = hypothesis_rows.get((range_label, direction))
            if row is None or row.get("p_value") is None or row.get("ci_lower") is None:
                raise ValueError(f"canonical selection_hypothesis_fact missing {direction} p_value/CI for {range_label}")
            p_values[(condition_id, direction)] = float(row["p_value"])
            lcb_by_direction[(condition_id, direction)] = float(row["ci_lower"])
            prefilter[(condition_id, direction)] = bool(row.get("passed_prefilter"))
    from src.strategy.live_inference.inference_engine import InferenceInputs, evaluate_live_bins

    prior = tuple(max(q_by_condition[str(candidate.condition_id or "")], 1e-9) for candidate in family.candidates)
    live_state = evaluate_live_bins(
        InferenceInputs(
            prior=prior,
            forecast_complete=True,
            orderbook_event=False,
        )
    )
    for index, candidate in enumerate(family.candidates):
        condition_id = str(candidate.condition_id or "")
        q_value = float(live_state.probabilities[str(index)])
        q_by_condition[condition_id] = q_value
    return q_by_condition, lcb_by_direction, p_values, prefilter


def _canonical_probability_rows(
    conn: sqlite3.Connection,
    *,
    event: OpportunityEvent,
    family,
    allow_latest: bool,
    decision_time: datetime,
) -> dict[str, Any] | None:
    table_ref = _authority_table_ref(conn, "ensemble_snapshots_v2")
    if table_ref is None:
        raise ValueError("canonical probability_trace_fact table missing")
    columns = _table_ref_columns(conn, table_ref)
    required = {"city", "target_date", "range_label", "direction", "p_posterior"}
    if not required.issubset(columns):
        return None
    predicates = ["city = ?", "target_date = ?", "temperature_metric = ?"]
    params: list[object] = [family.city, family.target_date, family.metric]
    if not allow_latest:
        predicates.append("CAST(snapshot_id AS TEXT) = ?")
        params.append(str(event.causal_snapshot_id or ""))
        if "available_at" in columns:
            predicates.append("available_at <= ?")
            params.append(decision_time.astimezone(UTC).isoformat())
    elif "available_at" in columns:
        predicates.append("available_at <= ?")
        params.append(decision_time.astimezone(UTC).isoformat())
    if "authority" in columns:
        predicates.append("COALESCE(authority, 'VERIFIED') = 'VERIFIED'")
    if "causality_status" in columns:
        predicates.append("COALESCE(causality_status, 'OK') = 'OK'")
    if "boundary_ambiguous" in columns:
        predicates.append("COALESCE(boundary_ambiguous, 0) = 0")
    order_field = "available_at" if "available_at" in columns else "snapshot_id"
    cur = conn.execute(
        f"""
        SELECT {', '.join(select_fields)}
        FROM {table_ref}
        WHERE {' AND '.join(predicates)}
          AND direction IN ('buy_yes', 'buy_no')
        ORDER BY recorded_at DESC
        """,
        tuple(params),
    )
    names = [description[0] for description in cur.description]
    snapshot = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
    reason = _forecast_snapshot_reader_block_reason(
        conn,
        snapshot=snapshot,
        event=event,
        family=family,
        allow_latest=allow_latest,
        decision_time=decision_time,
    )
    if reason is not None:
        raise ValueError(reason)
    return snapshot


def _canonical_hypothesis_rows(
    conn: sqlite3.Connection,
    *,
    calibration_conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    family,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    payload: dict[str, object],
    decision_time: datetime | None,
) -> MarketAnalysis:
    bins = list(family.bins)
    members = _snapshot_members(snapshot)
    p_raw = _snapshot_p_raw(snapshot, family=family, bins=bins, members=members, payload=payload)
    p_cal = _snapshot_p_cal(
        calibration_conn,
        snapshot=snapshot,
        family=family,
        bins=bins,
        p_raw=p_raw,
        payload=payload,
        decision_time=decision_time,
    )
    if family.event_type == "DAY0_EXTREME_UPDATED":
        p_raw = _apply_day0_mask_to_probability_vector(payload=payload, family=family, vector=p_raw)
        p_cal = _apply_day0_mask_to_probability_vector(payload=payload, family=family, vector=p_cal)
    p_market_yes: list[float] = []
    p_market_no: list[float] = []
    buy_no_available: list[bool] = []
    executable_mask: list[bool] = []
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        yes_cost = native_costs.get((condition_id, "buy_yes"))
        no_cost = native_costs.get((condition_id, "buy_no"))
        yes_price = yes_cost[1].value if yes_cost is not None and yes_cost[1] is not None else None
        no_price = no_cost[1].value if no_cost is not None and no_cost[1] is not None else None
        p_market_yes.append(float(yes_price) if yes_price is not None else 0.999999)
        p_market_no.append(float(no_price) if no_price is not None else 0.999999)
        buy_no_available.append(no_price is not None)
        executable_mask.append(yes_price is not None or no_price is not None)
    sampler = None
    if family.event_type == "DAY0_EXTREME_UPDATED":
        static_p_cal = np.asarray(p_cal, dtype=float)

        def _static_sampler(_analysis, _n_members):
            return static_p_cal

        sampler = _static_sampler
    return MarketAnalysis(
        p_raw=np.asarray(p_raw, dtype=float),
        p_cal=np.asarray(p_cal, dtype=float),
        p_market=np.asarray(p_market_yes, dtype=float),
        p_market_no=np.asarray(p_market_no, dtype=float),
        buy_no_quote_available=np.asarray(buy_no_available, dtype=bool),
        executable_mask=np.asarray(executable_mask, dtype=bool),
        alpha=float(settings["edge"]["base_alpha"]["level1"]),
        bins=bins,
        member_maxes=members,
        unit=_snapshot_unit(snapshot, payload),
        precision=float(snapshot.get("members_precision") or 1.0),
        round_fn=None,
        city_name=family.city,
        season="",
        forecast_source=str(snapshot.get("source_id") or payload.get("source_id") or ""),
        bias_corrected=False,
        market_complete=True,
        posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
        bootstrap_probability_sampler=sampler,
        bootstrap_signal_type="edli_event_bound_day0" if family.event_type == "DAY0_EXTREME_UPDATED" else "edli_event_bound_forecast",
    )
    names = [description[0] for description in cur.description]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in cur.fetchall():
        item = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
        key = (_nonnull(item.get("range_label")), _nonnull(item.get("direction")))
        if key[0] and key[1] and key not in out:
            out[key] = item
    return out


def _snapshot_members(snapshot: dict[str, Any]) -> np.ndarray:
    members = _json_list(snapshot.get("members_json"))
    values = np.asarray([float(item) for item in members if item is not None], dtype=float)
    if values.ndim != 1 or values.size == 0 or not np.isfinite(values).all():
        raise ValueError("causal forecast snapshot members_json invalid")
    return values


def _snapshot_p_raw(
    snapshot: dict[str, Any],
    *,
    family,
    bins: list[Bin],
    members: np.ndarray,
    payload: dict[str, object],
) -> np.ndarray:
    city = runtime_cities_by_name().get(family.city)
    if city is None:
        raise ValueError(f"city config missing for event-bound forecast inference: {family.city}")
    semantics = SettlementSemantics.for_city(city)
    arr = p_raw_vector_from_maxes(members, city, semantics, bins)
    if arr.shape != (len(bins),) or not np.isfinite(arr).all() or np.any(arr < 0.0):
        raise ValueError("event-bound p_raw vector invalid")
    total = float(arr.sum())
    if total <= 0.0:
        raise ValueError("event-bound p_raw vector has zero mass")
    arr = arr / total
    return arr


def _snapshot_p_cal(
    calibration_conn: sqlite3.Connection,
    *,
    snapshot: dict[str, Any],
    family,
    bins: list[Bin],
    p_raw: np.ndarray,
    payload: dict[str, object],
    decision_time: datetime | None,
) -> np.ndarray:
    city = runtime_cities_by_name().get(family.city)
    if city is None:
        raise ValueError(f"CALIBRATION_AUTHORITY_MISSING:city config missing for {family.city}")

    source_id = _nonnull(snapshot.get("source_id") or payload.get("source_id"))
    issue_time = _nonnull(snapshot.get("issue_time") or snapshot.get("source_cycle_time") or payload.get("cycle"))
    lead_days = _snapshot_lead_days(snapshot=snapshot, family=family, payload=payload)
    if not source_id or not issue_time:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:forecast provenance missing")

    from src.calibration.forecast_calibration_domain import derive_phase2_keys_from_ens_result
    from src.calibration.manager import get_calibrator
    from src.calibration.platt import calibrate_and_normalize
    from src.data.forecast_source_registry import calibration_source_id_for_lookup

    cycle, raw_source_id, horizon_profile = derive_phase2_keys_from_ens_result(
        {
            "issue_time": issue_time,
            "source_id": source_id,
            "horizon_profile": snapshot.get("horizon_profile") or payload.get("horizon_profile"),
        }
    )
    calibration_source_id = calibration_source_id_for_lookup(raw_source_id)
    if calibration_source_id is None:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:unsupported forecast source")
    try:
        cal, _level = get_calibrator(
            calibration_conn,
            city,
            str(family.target_date),
            temperature_metric=family.metric,
            cycle=cycle,
            source_id=calibration_source_id,
            horizon_profile=horizon_profile,
        )
    except (sqlite3.Error, ValueError) as exc:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:calibration store unavailable") from exc
    if cal is None:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:no Platt calibrator")
    p_cal = calibrate_and_normalize(
        np.asarray(p_raw, dtype=float),
        cal,
        lead_days,
        bin_widths=[candidate.width for candidate in bins],
    )
    if not _valid_probability_vector(p_cal, len(bins)):
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:p_cal invalid")
    return p_cal


def _snapshot_lead_days(*, snapshot: dict[str, Any], family, payload: dict[str, object]) -> float:
    lead_hours = _optional_float(snapshot.get("lead_hours") or payload.get("lead_hours"))
    if lead_hours is not None and lead_hours >= 0.0:
        return lead_hours / 24.0
    issue = _parse_utc(snapshot.get("issue_time") or snapshot.get("source_cycle_time") or payload.get("cycle"))
    try:
        target_day = date.fromisoformat(str(family.target_date))
    except ValueError as exc:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:target date invalid") from exc
    if issue is None:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:lead_days missing")
    target_start = datetime.combine(target_day, time.min, tzinfo=UTC)
    return max(0.0, (target_start - issue).total_seconds() / 86400.0)


def _valid_probability_vector(value: np.ndarray, expected_len: int) -> bool:
    arr = np.asarray(value, dtype=float)
    return (
        arr.shape == (expected_len,)
        and bool(np.isfinite(arr).all())
        and bool(np.all(arr >= 0.0))
        and float(arr.sum()) > 0.0
    )


def _snapshot_unit(snapshot: dict[str, Any], payload: dict[str, object]) -> str:
    unit = _nonnull(snapshot.get("settlement_unit") or snapshot.get("unit") or payload.get("unit") or payload.get("temperature_unit"))
    if unit in {"F", "C"}:
        return unit
    members_unit = _nonnull(snapshot.get("members_unit"))
    if members_unit == "degC":
        return "C"
    if members_unit == "degF":
        return "F"
    return "F"


def _apply_day0_mask_to_generated_probabilities(
    *,
    payload: dict[str, object],
    family,
    q_by_condition: dict[str, float],
    lcb_by_condition: dict[tuple[str, str], float],
) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
    rounded = _optional_float(payload.get("rounded_value"))
    if rounded is None:
        raise ValueError("Day0 event missing rounded_value")
    metric = _nonnull(payload.get("metric") or payload.get("temperature_metric"))
    mask: list[float] = []
    for candidate in family.candidates:
        bin_value = candidate.bin
        if metric == "high":
            if bin_value.high is not None and rounded > float(bin_value.high):
                mask.append(0.0)
            elif bin_value.high is None and bin_value.low is not None and rounded >= float(bin_value.low):
                mask.append(1.0)
            else:
                mask.append(1.0)
        elif metric == "low":
            if bin_value.low is not None and rounded < float(bin_value.low):
                mask.append(0.0)
            elif bin_value.low is None and bin_value.high is not None and rounded <= float(bin_value.high):
                mask.append(1.0)
            else:
                mask.append(1.0)
        else:
            raise ValueError(f"unsupported Day0 metric: {metric}")
    from src.strategy.live_inference.inference_engine import InferenceInputs, evaluate_live_bins

    prior = tuple(max(q_by_condition[str(candidate.condition_id or "")], 1e-9) for candidate in family.candidates)
    live_state = evaluate_live_bins(
        InferenceInputs(
            prior=prior,
            day0_mask=tuple(mask),
            forecast_complete=True,
            orderbook_event=False,
        )
    )
    masked_q_by_condition: dict[str, float] = {}
    masked_lcb_by_direction: dict[tuple[str, str], float] = {}
    for index, candidate in enumerate(family.candidates):
        condition_id = str(candidate.condition_id or "")
        q_value = float(live_state.probabilities[str(index)])
        masked_q_by_condition[condition_id] = q_value
        yes_lcb = lcb_by_condition[(condition_id, "buy_yes")]
        no_lcb = lcb_by_condition[(condition_id, "buy_no")]
        masked_lcb_by_direction[(condition_id, "buy_yes")] = 0.0 if mask[index] <= 0.0 else min(yes_lcb, q_value)
        masked_lcb_by_direction[(condition_id, "buy_no")] = min(no_lcb, 1.0 - q_value)
    return masked_q_by_condition, masked_lcb_by_direction


def _table_ref_columns(conn: sqlite3.Connection, table_ref: str) -> set[str]:
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
        return {row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()}
    return _table_columns(conn, table_ref)


def _authority_table_ref(conn: sqlite3.Connection, table_name: str) -> str | None:
    try:
        attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
        if "world" in attached:
            exists = conn.execute(
                "SELECT 1 FROM world.sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            if exists is not None:
                return f"world.{table_name}"
    except Exception:
        pass
    if _table_exists(conn, table_name):
        return table_name
    return None


def _snapshot_rows_by_condition(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        condition_id = _nonnull(row.get("condition_id"))
        if condition_id and condition_id not in out:
            out[condition_id] = row
    return out


def _latest_snapshot_rows_for_event_family(
    trade_conn: sqlite3.Connection,
    event: OpportunityEvent,
    *,
    condition_ids: tuple[str, ...],
    fresh_at: datetime | None = None,
) -> list[dict[str, Any]]:
    if not _table_exists(trade_conn, "executable_market_snapshots"):
        return []
    columns = _table_columns(trade_conn, "executable_market_snapshots")
    clean_condition_ids = tuple(condition_id for condition_id in condition_ids if condition_id)
    if not clean_condition_ids or "condition_id" not in columns:
        return []
    predicates = ["freshness_deadline >= ?"]
    params: list[object] = [(fresh_at or datetime.now(UTC)).isoformat()]
    placeholders = ",".join("?" for _ in clean_condition_ids)
    predicates.append(f"condition_id IN ({placeholders})")
    params.extend(clean_condition_ids)
    if "active" in columns:
        predicates.append("COALESCE(active, 0) = 1")
    if "closed" in columns:
        predicates.append("COALESCE(closed, 0) = 0")
    cur = trade_conn.execute(
        f"""
        SELECT *
        FROM executable_market_snapshots
        WHERE {' AND '.join(predicates)}
        ORDER BY captured_at DESC, snapshot_id DESC
        """,
        tuple(params),
    )
    names = [description[0] for description in cur.description]
    rows: list[dict[str, Any]] = []
    seen_side: set[tuple[str, str]] = set()
    for row in cur.fetchall():
        item = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
        condition_id = str(item.get("condition_id") or "")
        selected_token = str(item.get("selected_outcome_token_id") or "")
        side_key = (condition_id, selected_token)
        if not condition_id or side_key in seen_side:
            continue
        seen_side.add(side_key)
        rows.append(item)
    return rows


def _selected_snapshot_row_for_event(
    rows: list[dict[str, Any]],
    payload: dict[str, object],
) -> dict[str, Any] | None:
    snapshot_id = _nonnull(payload.get("executable_snapshot_id"))
    condition_id = _nonnull(payload.get("condition_id"))
    token_id = _nonnull(payload.get("token_id"))
    for row in rows:
        if snapshot_id and str(row.get("snapshot_id") or "") != snapshot_id:
            continue
        if condition_id and str(row.get("condition_id") or "") != condition_id:
            continue
        if not token_id:
            return row
        if token_id not in {str(row.get("yes_token_id") or ""), str(row.get("no_token_id") or "")}:
            continue
        if _nonnull(row.get("selected_outcome_token_id")) == token_id and not _snapshot_outcome_matches_selected_token(row, token_id):
            continue
        return row
    return None


def _snapshot_token_maps_by_condition(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    token_maps: dict[str, dict[str, str]] = {}
    for row in rows:
        condition_id = _nonnull(row.get("condition_id"))
        yes_token_id = _nonnull(row.get("yes_token_id"))
        no_token_id = _nonnull(row.get("no_token_id"))
        if condition_id and yes_token_id and no_token_id:
            token_maps.setdefault(condition_id, {"yes_token_id": yes_token_id, "no_token_id": no_token_id})
    return token_maps


def _topology_candidate_from_market_event(
    row: dict[str, Any],
    snapshot_token_map: dict[str, str],
    payload: dict[str, object],
) -> MarketTopologyCandidate:
    city = _nonnull(payload.get("city"))
    target_date = _nonnull(payload.get("target_date"))
    metric = _nonnull(payload.get("metric") or payload.get("temperature_metric"))
    if not (city and target_date and metric):
        raise ValueError("EDLI event payload missing city/target_date/metric")
    return MarketTopologyCandidate(
        city=city,
        target_date=target_date,
        metric=metric,
        condition_id=_nonnull(row.get("condition_id")),
        yes_token_id=snapshot_token_map["yes_token_id"],
        no_token_id=snapshot_token_map["no_token_id"],
        bin=_bin_from_market_event(row, payload),
        market_slug=_nonnull(row.get("market_slug") or row.get("event_slug")) or None,
    )


def _bin_from_market_event(row: dict[str, Any], payload: dict[str, object]) -> Bin:
    label = _nonnull(row.get("range_label") or row.get("outcome") or payload.get("bin_label") or payload.get("outcome_label"))
    low = row.get("range_low")
    high = row.get("range_high")
    unit = _nonnull(payload.get("unit") or payload.get("temperature_unit") or "F")
    if isinstance(low, (int, float)) or isinstance(high, (int, float)):
        return Bin(
            low=float(low) if isinstance(low, (int, float)) else None,
            high=float(high) if isinstance(high, (int, float)) else None,
            unit=unit,
            label=label,
        )
    raise ValueError("market topology bin range missing")


def _bin_from_payload(payload: dict[str, object]) -> Bin:
    label = _nonnull(payload.get("bin_label") or payload.get("outcome_label"))
    low = payload.get("bin_low")
    high = payload.get("bin_high")
    unit = _nonnull(payload.get("unit") or payload.get("temperature_unit") or "F")
    if isinstance(low, (int, float)) or isinstance(high, (int, float)):
        return Bin(
            low=float(low) if isinstance(low, (int, float)) else None,
            high=float(high) if isinstance(high, (int, float)) else None,
            unit=unit,
            label=label,
        )
    return Bin(low=0, high=1, unit="F", label=label or "0-1°F")


def _snapshot_outcome_matches_selected_token(row: dict[str, Any], selected_token_id: str) -> bool:
    selected_label = "YES" if selected_token_id == str(row.get("yes_token_id") or "") else "NO"
    outcome_label = _nonnull(row.get("outcome_label")).upper()
    return not outcome_label or outcome_label == selected_label


def _execution_price_from_snapshot(
    row: dict[str, Any],
    *,
    selected_token_id: str,
    direction: str,
) -> tuple[ExecutionPrice, float, float]:
    if selected_token_id not in {str(row.get("yes_token_id") or ""), str(row.get("no_token_id") or "")}:
        raise ValueError("EDLI executable snapshot selected token mismatch")
    if _nonnull(row.get("selected_outcome_token_id")) == selected_token_id and not _snapshot_outcome_matches_selected_token(row, selected_token_id):
        raise ValueError("EDLI executable snapshot outcome label mismatch")
    from src.strategy.live_inference import executable_cost as cost_kernel

    book = _native_quote_book_from_snapshot_row(row)
    shares = book.min_order_size
    execution_price = cost_kernel.executable_cost(book, direction=direction, shares=shares)  # type: ignore[arg-type]
    p_fill_lcb = _p_fill_lcb_for_direction(book, direction=direction, shares=shares)
    c_cost_95pct = min(0.999999, execution_price.value + float(book.min_tick_size))
    return execution_price, p_fill_lcb, c_cost_95pct


def _native_quote_book_from_snapshot_row(row: dict[str, Any]):
    from src.contracts.executable_market_snapshot_v2 import fee_rate_fraction_from_details
    from src.strategy.live_inference.executable_cost import NativeQuoteBook, QuoteLevel

    min_tick_size = Decimal(str(row.get("min_tick_size") or row.get("tick_size") or "0.01"))
    min_order_size = Decimal(str(row.get("min_order_size") or "1"))
    fee_details = _json_object(row.get("fee_details_json") or row.get("fee_details") or {})
    fee_rate = fee_rate_fraction_from_details(fee_details)
    neg_risk = bool(_optional_bool(row.get("neg_risk")) or False)
    depth = _json_object(row.get("orderbook_depth_json") or row.get("orderbook_depth_jsonb") or {})
    yes_token_id = str(row.get("yes_token_id") or "")
    no_token_id = str(row.get("no_token_id") or "")
    yes_depth = _depth_for_token_or_label(depth, token_id=yes_token_id, label="YES")
    no_depth = _depth_for_token_or_label(depth, token_id=no_token_id, label="NO")
    if yes_depth is None:
        yes_depth = _explicit_depth_for_selected_token(row, token_id=yes_token_id, min_order_size=min_order_size)
    if no_depth is None:
        no_depth = _explicit_depth_for_selected_token(row, token_id=no_token_id, min_order_size=min_order_size)
    yes_depth = yes_depth or {}
    no_depth = no_depth or {}
    return NativeQuoteBook(
        yes_asks=_parse_quote_levels(yes_depth.get("asks", ())),
        no_asks=_parse_quote_levels(no_depth.get("asks", ())),
        yes_bids=_parse_quote_levels(yes_depth.get("bids", ())),
        no_bids=_parse_quote_levels(no_depth.get("bids", ())),
        min_tick_size=min_tick_size,
        min_order_size=min_order_size,
        fee_rate=fee_rate,
        neg_risk=neg_risk,
    )


def _parse_quote_levels(raw_levels: object):
    from src.strategy.live_inference.executable_cost import QuoteLevel

    levels = []
    if not isinstance(raw_levels, (list, tuple)):
        return tuple()
    for raw in raw_levels:
        if isinstance(raw, dict):
            price = raw.get("price")
            size = raw.get("size")
        else:
            try:
                price, size = raw
            except (TypeError, ValueError):
                continue
        if price in {None, ""} or size in {None, ""}:
            continue
        levels.append(QuoteLevel(Decimal(str(price)), Decimal(str(size))))
    return tuple(levels)


def _depth_for_token_or_label(depth: object, *, token_id: str, label: str) -> dict[str, object] | None:
    if not isinstance(depth, dict):
        return None
    for key in (token_id, label, label.lower()):
        value = depth.get(key)
        if isinstance(value, dict):
            return value
    for key in ("tokens", "outcomes", "books"):
        value = depth.get(key)
        if isinstance(value, dict):
            nested = _depth_for_token_or_label(value, token_id=token_id, label=label)
            if nested is not None:
                return nested
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                if str(item.get("asset_id") or item.get("token_id") or "") == token_id:
                    return item
                if str(item.get("outcome") or item.get("outcome_label") or "").upper() == label:
                    return item
    return None


def _explicit_depth_for_selected_token(
    row: dict[str, Any],
    *,
    token_id: str,
    min_order_size: Decimal,
) -> dict[str, object] | None:
    if _nonnull(row.get("selected_outcome_token_id")) != token_id:
        return None
    ask_price = row.get("orderbook_top_ask")
    bid_price = row.get("orderbook_top_bid")
    ask_size = _decimal_from_optional(
        row.get("depth_at_best_ask")
        or row.get("orderbook_top_ask_size")
        or row.get("best_ask_size")
    )
    bid_size = _decimal_from_optional(
        row.get("depth_at_best_bid")
        or row.get("orderbook_top_bid_size")
        or row.get("best_bid_size")
    )
    asks = _explicit_level(ask_price, ask_size, min_order_size=min_order_size)
    bids = _explicit_level(bid_price, bid_size, min_order_size=min_order_size)
    if not asks and not bids:
        return None
    return {"asks": asks, "bids": bids}


def _explicit_level(price: object, size: Decimal | None, *, min_order_size: Decimal) -> list[dict[str, str]]:
    if price in {None, "", "ABSENT"} or size is None or size < min_order_size:
        return []
    return [{"price": str(price), "size": str(size)}]


def _decimal_from_optional(value: object) -> Decimal | None:
    if value in {None, "", "ABSENT"}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _p_fill_lcb_for_direction(book, *, direction: str, shares: Decimal) -> float:
    levels = {
        "buy_yes": book.yes_asks,
        "buy_no": book.no_asks,
        "sell_yes": book.yes_bids,
        "sell_no": book.no_bids,
    }[direction]
    available = sum((level.size for level in levels), Decimal("0"))
    if available < shares:
        return 0.0
    # Public visible depth is quote feasibility evidence, not fill truth. In
    # no-submit mode there is no FOK/FAK acceptance or user-channel fill proof,
    # so cap the fill lower bound at a conservative configured floor.
    return max(0.0, min(1.0, float(settings["edli_v1"].get("no_submit_visible_depth_fill_lcb", 0.05))))


def _robust_trade_score_from_generated_inputs(
    *,
    q_posterior: float,
    q_lcb_5pct: float,
    execution_price: ExecutionPrice | None,
    c_cost_95pct: float | None,
    p_fill_lcb: float,
) -> float:
    if execution_price is None or c_cost_95pct is None:
        return 0.0
    from src.strategy.live_inference.trade_score import robust_trade_score

    receipt = robust_trade_score(
        trade_score_id="edli_generated_trade_score",
        q_posterior=q_posterior,
        q_5pct=q_lcb_5pct,
        c_95pct=ExecutionPrice(c_cost_95pct, "ask", fee_deducted=True, currency="probability_units"),
        c_stress=ExecutionPrice(c_cost_95pct, "ask", fee_deducted=True, currency="probability_units"),
        p_fill_lcb=p_fill_lcb,
        penalty=0.01,
        stress_penalty=0.01,
    )
    return float(receipt.score)


def _bankroll_usd_from_provider(provider: Callable[[], float | None]) -> float:
    value = provider()
    if value is None:
        raise ValueError("bankroll_provider_unavailable")
    bankroll_usd = float(value)
    if bankroll_usd <= 0:
        raise ValueError("bankroll_provider_nonpositive")
    return bankroll_usd


def _runtime_bankroll_usd(*, cached_only: bool = False) -> float:
    from src.runtime import bankroll_provider

    bankroll = (
        bankroll_provider.cached()
        if cached_only and hasattr(bankroll_provider, "cached")
        else bankroll_provider.current()
    )
    if bankroll is None:
        raise ValueError("bankroll_provider_unavailable")
    if bankroll.authority != "canonical" or bankroll.source != "polymarket_wallet":
        raise ValueError("bankroll_provider_not_canonical")
    if bankroll.value_usd <= 0:
        raise ValueError("bankroll_provider_nonpositive")
    return float(bankroll.value_usd)


def _runtime_kelly_multiplier() -> float:
    from src.config import settings

    value = float(settings["sizing"]["kelly_multiplier"])
    if value <= 0:
        raise ValueError("kelly_multiplier_nonpositive")
    return value


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
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


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _hash_jsonish(value: object) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = value
    return stable_hash(parsed)


def _native_side_for_direction(direction: str | None) -> str | None:
    if direction == "buy_yes":
        return "YES_ASK"
    if direction == "buy_no":
        return "NO_ASK"
    if direction == "sell_yes":
        return "YES_BID"
    if direction == "sell_no":
        return "NO_BID"
    return None


def _calibration_model_row(conn: sqlite3.Connection, *, model_key: object) -> dict[str, Any] | None:
    if not model_key or not _table_exists(conn, "platt_models_v2"):
        return None
    cur = conn.execute("SELECT * FROM platt_models_v2 WHERE model_key = ? LIMIT 1", (str(model_key),))
    row = cur.fetchone()
    if row is None:
        return None
    names = [description[0] for description in cur.description]
    return {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))


def _evidence_clock_from_row(row: dict[str, Any], *, fallback: datetime) -> EvidenceClock:
    source_time = (
        _parse_utc(row.get("book_timestamp"))
        or _parse_utc(row.get("captured_at"))
        or _parse_utc(row.get("source_available_at"))
        or fallback
    )
    agent_time = (
        _parse_utc(row.get("received_at"))
        or _parse_utc(row.get("agent_received_at"))
        or _parse_utc(row.get("captured_at"))
        or source_time
    )
    persisted_time = (
        _parse_utc(row.get("persisted_at"))
        or _parse_utc(row.get("created_at"))
        or _parse_utc(row.get("inserted_at"))
        or agent_time
    )
    return EvidenceClock(source_time, agent_time, persisted_time)


def _evidence_clock_from_rows(rows: list[dict[str, Any]], *, fallback: datetime) -> EvidenceClock:
    clocks = [_evidence_clock_from_topology_row(row, fallback=fallback) for row in rows]
    if not clocks:
        return EvidenceClock(fallback, fallback, fallback)
    return EvidenceClock(
        source_available_at=max(clock.source_available_at for clock in clocks),
        agent_received_at=max(clock.agent_received_at for clock in clocks),
        persisted_at=max(clock.persisted_at for clock in clocks),
    )


def _evidence_clock_from_topology_row(row: dict[str, Any], *, fallback: datetime) -> EvidenceClock:
    has_source_clock = any(row.get(key) not in (None, "") for key in ("discovered_at", "captured_at", "available_at", "gamma_updated_at", "created_at"))
    has_agent_clock = any(row.get(key) not in (None, "") for key in ("received_at", "scanned_at", "captured_at", "created_at"))
    has_persisted_clock = any(row.get(key) not in (None, "") for key in ("persisted_at", "updated_at", "created_at"))
    if not (has_source_clock and has_agent_clock and has_persisted_clock):
        raise ValueError("TOPOLOGY_CLOCK_MISSING")
    source_time = (
        _parse_utc(row.get("discovered_at"))
        or _parse_utc(row.get("captured_at"))
        or _parse_utc(row.get("available_at"))
        or _parse_utc(row.get("gamma_updated_at"))
        or _parse_utc(row.get("created_at"))
        or fallback
    )
    agent_time = (
        _parse_utc(row.get("received_at"))
        or _parse_utc(row.get("scanned_at"))
        or _parse_utc(row.get("captured_at"))
        or _parse_utc(row.get("created_at"))
        or source_time
    )
    persisted_time = (
        _parse_utc(row.get("persisted_at"))
        or _parse_utc(row.get("updated_at"))
        or _parse_utc(row.get("created_at"))
        or agent_time
    )
    return EvidenceClock(source_time, agent_time, persisted_time)


def _read_executable_forecast_bundle_result(
    conn: sqlite3.Connection,
    *,
    snapshot: dict[str, Any],
    source_run: dict[str, Any],
    coverage: dict[str, Any],
    event: OpportunityEvent,
    family,
    decision_time: datetime,
):
    from src.data.executable_forecast_reader import SOURCE_TRANSPORT, read_executable_forecast

    target_date = date.fromisoformat(str(coverage.get("target_local_date") or family.target_date))
    source_id = _nonnull(coverage.get("source_id") or source_run.get("source_id") or snapshot.get("source_id"))
    source_transport = _nonnull(coverage.get("source_transport") or snapshot.get("source_transport") or SOURCE_TRANSPORT)
    data_version = _nonnull(coverage.get("data_version") or snapshot.get("data_version"))
    source_run_id = _nonnull(source_run.get("source_run_id") or snapshot.get("source_run_id"))
    track = _nonnull(coverage.get("track") or source_run.get("track") or snapshot.get("track") or _payload(event).get("track"))
    condition_id = _nonnull(_payload(event).get("condition_id") or (family.condition_ids[0] if family.condition_ids else ""))
    if (
        not source_id
        or not source_transport
        or not data_version
        or not source_run_id
        or not track
        or not condition_id
    ):
        raise ValueError("FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:scope_incomplete")
    return read_executable_forecast(
        conn,
        city_id=str(coverage.get("city_id") or family.city),
        city_name=str(coverage.get("city") or family.city),
        city_timezone=str(coverage.get("city_timezone") or "UTC"),
        target_local_date=target_date,
        temperature_metric=family.metric,
        source_id=source_id,
        source_transport=source_transport,
        data_version=data_version,
        track=track,
        strategy_key="entry_forecast",
        market_family=family.family_id,
        condition_id=condition_id,
        decision_time=decision_time,
        require_entry_readiness=False,
    )


def _forecast_snapshot_reader_block_reason(
    conn: sqlite3.Connection,
    *,
    snapshot: dict[str, Any],
    event: OpportunityEvent,
    family,
    allow_latest: bool,
    decision_time: datetime,
) -> str | None:
    if event.event_type not in {"FORECAST_SNAPSHOT_READY", "DAY0_EXTREME_UPDATED"}:
        return None
    source_run_id = _nonnull(snapshot.get("source_run_id") or _payload(event).get("source_run_id"))
    if not source_run_id:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_id_missing"
    source_run_table = _authority_table_ref(conn, "source_run")
    coverage_table = _authority_table_ref(conn, "source_run_coverage")
    if source_run_table is None or coverage_table is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_authority_missing"
    source_run = _row_by_id(conn, source_run_table, "source_run_id", source_run_id)
    if source_run is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_missing"
    coverage = _coverage_row_for_snapshot(
        conn,
        coverage_table,
        source_run_id=source_run_id,
        family=family,
        snapshot=snapshot,
    )
    if coverage is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:coverage_missing"
    reader_reason = _executable_forecast_reader_authority_block_reason(
        conn,
        snapshot=snapshot,
        source_run=source_run,
        coverage=coverage,
        event=event,
        family=family,
        allow_latest=allow_latest,
        decision_time=decision_time,
    )
    if reader_reason is not None:
        return reader_reason
    return None


def _executable_forecast_reader_authority_block_reason(
    conn: sqlite3.Connection,
    *,
    snapshot: dict[str, Any],
    source_run: dict[str, Any],
    coverage: dict[str, Any],
    event: OpportunityEvent,
    family,
    allow_latest: bool,
    decision_time: datetime,
) -> str | None:
    """Revalidate forecast eligibility through the canonical executable reader."""

    try:
        from src.data.executable_forecast_reader import SOURCE_TRANSPORT, read_executable_forecast

        target_date = date.fromisoformat(str(coverage.get("target_local_date") or family.target_date))
        source_id = _nonnull(coverage.get("source_id") or source_run.get("source_id") or snapshot.get("source_id"))
        source_transport = _nonnull(coverage.get("source_transport") or snapshot.get("source_transport") or SOURCE_TRANSPORT)
        data_version = _nonnull(coverage.get("data_version") or snapshot.get("data_version"))
        source_run_id = _nonnull(source_run.get("source_run_id") or snapshot.get("source_run_id"))
        track = _nonnull(coverage.get("track") or source_run.get("track") or snapshot.get("track") or _payload(event).get("track"))
        condition_id = _nonnull(_payload(event).get("condition_id") or (family.condition_ids[0] if family.condition_ids else ""))
        if (
            not source_id
            or not source_transport
            or not data_version
            or not source_run_id
            or not track
            or not condition_id
        ):
            return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:scope_incomplete"
        result = read_executable_forecast(
            conn,
            city_id=str(coverage.get("city_id") or family.city),
            city_name=str(coverage.get("city") or family.city),
            city_timezone=str(coverage.get("city_timezone") or "UTC"),
            target_local_date=target_date,
            temperature_metric=family.metric,
            source_id=source_id,
            source_transport=source_transport,
            data_version=data_version,
            track=track,
            strategy_key="entry_forecast",
            market_family=family.family_id,
            condition_id=condition_id,
            decision_time=decision_time,
            require_entry_readiness=False,
        )
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        return f"FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:{exc}"
    if not result.ok or result.bundle is None:
        return f"FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:{result.reason_code}"
    selected_snapshot_id = _nonnull(snapshot.get("snapshot_id"))
    if _nonnull(result.bundle.snapshot.snapshot_id) != selected_snapshot_id:
        return "FORECAST_READER_SNAPSHOT_MISMATCH"
    if not allow_latest and _nonnull(event.causal_snapshot_id) != selected_snapshot_id:
        return "FORECAST_READER_CAUSAL_SNAPSHOT_MISMATCH"
    return None


def _row_by_id(conn: sqlite3.Connection, table_ref: str, id_col: str, value: str) -> dict[str, Any] | None:
    cur = conn.execute(f"SELECT * FROM {table_ref} WHERE {id_col} = ? LIMIT 1", (value,))
    row = cur.fetchone()
    if row is None:
        return None
    names = [description[0] for description in cur.description]
    return {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))


def _coverage_row_for_snapshot(
    conn: sqlite3.Connection,
    table_ref: str,
    *,
    source_run_id: str,
    family,
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    columns = _table_ref_columns(conn, table_ref)
    predicates = ["source_run_id = ?"]
    params: list[object] = [source_run_id]
    for column, value in (
        ("city", family.city),
        ("target_local_date", family.target_date),
        ("temperature_metric", family.metric),
        ("source_id", snapshot.get("source_id")),
        ("source_transport", snapshot.get("source_transport")),
        ("data_version", snapshot.get("data_version")),
    ):
        if column in columns and value not in {None, ""}:
            predicates.append(f"{column} = ?")
            params.append(value)
    cur = conn.execute(
        f"""
        SELECT *
        FROM {table_ref}
        WHERE {' AND '.join(predicates)}
        ORDER BY computed_at DESC, recorded_at DESC
        LIMIT 1
        """,
        tuple(params),
    )
    row = cur.fetchone()
    if row is None:
        return None
    names = [description[0] for description in cur.description]
    return {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))


def _payload(event: OpportunityEvent) -> dict[str, object]:
    try:
        parsed = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nonnull(value: object) -> str:
    return str(value or "").strip()


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


def _event_family_market_topology_rows(
    conn: sqlite3.Connection,
    payload: dict[str, object],
) -> list[dict[str, Any]]:
    """Return canonical market topology rows for the event city/date/metric.

    Forecast and Day0 events are family facts, not child-token facts. They may
    legitimately lack condition/token ids, but they still must bind through the
    forecast-owned market topology table before executable snapshots can satisfy
    the quote gate. The family universe comes from market_events_v2, not from the
    subset of fresh executable snapshots, so a missing sibling cannot shrink the
    FDR denominator.
    """

    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    metric = str(payload.get("metric") or payload.get("temperature_metric") or "").strip()
    if not (city and target_date and metric):
        return []
    table_ref = _market_events_table_ref(conn)
    if table_ref is None:
        return []
    columns = _market_events_columns(conn, table_ref)
    required = {"city", "target_date", "temperature_metric", "condition_id"}
    if not required.issubset(columns):
        return []
    select_fields = [
        "condition_id",
        _optional_column_expr(columns, "market_slug"),
        _optional_column_expr(columns, "range_label"),
        _optional_column_expr(columns, "range_low"),
        _optional_column_expr(columns, "range_high"),
        _optional_column_expr(columns, "outcome"),
        _optional_column_expr(columns, "token_id"),
        _optional_column_expr(columns, "discovered_at"),
        _optional_column_expr(columns, "captured_at"),
        _optional_column_expr(columns, "available_at"),
        _optional_column_expr(columns, "gamma_updated_at"),
        _optional_column_expr(columns, "created_at"),
        _optional_column_expr(columns, "received_at"),
        _optional_column_expr(columns, "scanned_at"),
        _optional_column_expr(columns, "persisted_at"),
        _optional_column_expr(columns, "updated_at"),
    ]
    label_order = "COALESCE(range_label, outcome, '')" if {"range_label", "outcome"}.issubset(columns) else (
        "COALESCE(range_label, '')" if "range_label" in columns else ("COALESCE(outcome, '')" if "outcome" in columns else "''")
    )
    token_order = "COALESCE(token_id, '')" if "token_id" in columns else "''"
    cur = conn.execute(
        f"""
        SELECT {', '.join(select_fields)}
        FROM {table_ref}
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND COALESCE(condition_id, '') != ''
        ORDER BY condition_id, {label_order}, {token_order}
        """,
        (city, target_date, metric),
    )
    names = [description[0] for description in cur.description]
    rows: list[dict[str, Any]] = []
    seen_conditions: set[str] = set()
    for row in cur.fetchall():
        item = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
        condition_id = str(item.get("condition_id") or "")
        if not condition_id or condition_id in seen_conditions:
            continue
        seen_conditions.add(condition_id)
        rows.append(item)
    return rows


def _market_events_table_ref(conn: sqlite3.Connection) -> str | None:
    try:
        attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" in attached:
            exists = conn.execute(
                "SELECT 1 FROM forecasts.sqlite_master WHERE type='table' AND name='market_events_v2'"
            ).fetchone()
            if exists is not None:
                return "forecasts.market_events_v2"
    except Exception:
        pass
    if _table_exists(conn, "market_events_v2"):
        return "market_events_v2"
    return None


def _market_events_columns(conn: sqlite3.Connection, table_ref: str) -> set[str]:
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
        return {row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()}
    return _table_columns(conn, table_ref)


def _optional_column_expr(columns: set[str], column: str) -> str:
    if column in columns:
        return column
    return f"NULL AS {column}"


def _qualified_optional_expr(columns: set[str], column: str, alias: str) -> str:
    if column in columns:
        return f"{alias}.{column} AS {column}"
    return f"NULL AS {column}"
