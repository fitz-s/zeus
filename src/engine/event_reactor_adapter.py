"""Engine adapter for EDLI opportunity reactor construction.

The adapter connects EDLI events to the event-bound no-submit proof kernel. It
does not call the broad cycle runner and it does not cross the executor or venue
side-effect boundary.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from src.contracts.execution_price import ExecutionPrice
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
        family_topology_rows = _event_family_market_topology_rows(trade_conn, payload)
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
) -> Callable[[OpportunityEvent], EventSubmissionReceipt]:
    """Build a proof-only final-intent receipt adapter for EDLI events."""

    def _submit(event: OpportunityEvent) -> EventSubmissionReceipt:
        return build_event_bound_no_submit_receipt(
            event,
            trade_conn=trade_conn,
            get_current_level=get_current_level,
        )

    return _submit


def build_event_bound_no_submit_receipt(
    event: OpportunityEvent,
    *,
    trade_conn: sqlite3.Connection,
    get_current_level: Callable[[], RiskLevel],
) -> EventSubmissionReceipt:
    """Produce a typed no-submit EDLI proof without running the cycle runner."""

    payload = _payload(event)
    family_topology_rows = _event_family_market_topology_rows(trade_conn, payload)
    if not family_topology_rows:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EVENT_BOUND_MARKET_TOPOLOGY_MISSING")
    family_condition_ids = tuple(str(row.get("condition_id") or "") for row in family_topology_rows)
    family_rows = _latest_snapshot_rows_for_event_family(trade_conn, event, condition_ids=family_condition_ids)
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
    topology = tuple(
        _topology_candidate_from_market_event(row, snapshot_token_maps[str(row.get("condition_id") or "")], payload)
        for row in family_topology_rows
    )
    row = _selected_snapshot_row_for_event(family_rows, payload)
    if row is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EVENT_BOUND_SELECTED_SNAPSHOT_MISSING")
    decision = EventBoundDecisionEngine().evaluate(
        EventBoundDecisionRequest(
            event=event,
            market_topology=topology,
            decision_time=datetime.now(UTC),
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
        bankroll_usd = _runtime_bankroll_usd()
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
    return _event_submission_receipt_from_typed_receipt_payload(raw_receipt, event)


def _event_submission_receipt_from_typed_receipt_payload(
    raw_receipt: dict[str, Any],
    event: OpportunityEvent,
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
        final_intent_id=raw_receipt.get("final_intent_id"),
        side_effect_status="NO_SUBMIT",
        reason=str(raw_receipt.get("reason") or "event_bound_final_intent_no_submit"),
    )


def _generate_candidate_proofs(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    family,
    snapshot_rows: list[dict[str, Any]],
    trade_conn: sqlite3.Connection,
) -> tuple[_CandidateProof, ...]:
    q_by_condition, q_lcb_by_direction, canonical_p_values, canonical_prefilter = _live_yes_probabilities(
        event=event,
        payload=payload,
        family=family,
        conn=trade_conn,
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
) -> tuple[dict[str, float], dict[tuple[str, str], float], dict[tuple[str, str], float], dict[tuple[str, str], bool]]:
    canonical = _canonical_probability_and_fdr_proof(event=event, family=family, conn=conn)
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        return canonical
    if event.event_type == "DAY0_EXTREME_UPDATED":
        q_by_condition, lcb_by_condition, p_values, prefilter = canonical
        masked_q, masked_lcb = _apply_day0_mask_to_canonical_probabilities(
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
) -> tuple[dict[str, float], dict[tuple[str, str], float], dict[tuple[str, str], float], dict[tuple[str, str], bool]]:
    probability_rows = _canonical_probability_rows(conn, event=event, family=family)
    hypothesis_rows = _canonical_hypothesis_rows(conn, event=event, family=family)
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
) -> dict[tuple[str, str], dict[str, Any]]:
    table_ref = _authority_table_ref(conn, "probability_trace_fact")
    if table_ref is None:
        raise ValueError("canonical probability_trace_fact table missing")
    columns = _table_ref_columns(conn, table_ref)
    required = {"city", "target_date", "range_label", "direction", "p_posterior"}
    if not required.issubset(columns):
        raise ValueError("canonical probability_trace_fact schema missing required columns")
    predicates = ["city = ?", "target_date = ?", "COALESCE(range_label, '') != ''"]
    params: list[object] = [family.city, family.target_date]
    if "decision_snapshot_id" in columns and event.causal_snapshot_id:
        predicates.append("(decision_snapshot_id = ? OR decision_snapshot_id IS NULL OR decision_snapshot_id = '')")
        params.append(event.causal_snapshot_id)
    select_fields = [
        "range_label",
        "direction",
        "p_posterior",
        _optional_column_expr(columns, "recorded_at"),
    ]
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
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in cur.fetchall():
        item = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
        key = (_nonnull(item.get("range_label")), _nonnull(item.get("direction")))
        if key[0] and key[1] and key not in out and item.get("p_posterior") is not None:
            out[key] = item
    return out


def _canonical_hypothesis_rows(
    conn: sqlite3.Connection,
    *,
    event: OpportunityEvent,
    family,
) -> dict[tuple[str, str], dict[str, Any]]:
    hypothesis_ref = _authority_table_ref(conn, "selection_hypothesis_fact")
    family_ref = _authority_table_ref(conn, "selection_family_fact")
    if hypothesis_ref is None:
        raise ValueError("canonical selection_hypothesis_fact table missing")
    columns = _table_ref_columns(conn, hypothesis_ref)
    required = {"city", "target_date", "range_label", "direction", "p_value", "ci_lower"}
    if not required.issubset(columns):
        raise ValueError("canonical selection_hypothesis_fact schema missing required columns")
    join_clause = ""
    predicates = ["h.city = ?", "h.target_date = ?", "COALESCE(h.range_label, '') != ''"]
    params: list[object] = [family.city, family.target_date]
    if family_ref is not None and event.causal_snapshot_id:
        join_clause = f"LEFT JOIN {family_ref} f ON h.family_id = f.family_id"
        predicates.append("(f.decision_snapshot_id = ? OR f.decision_snapshot_id IS NULL OR f.decision_snapshot_id = '')")
        params.append(event.causal_snapshot_id)
    select_fields = [
        "h.range_label AS range_label",
        "h.direction AS direction",
        "h.p_value AS p_value",
        "h.ci_lower AS ci_lower",
        _qualified_optional_expr(columns, "passed_prefilter", "h"),
        _qualified_optional_expr(columns, "recorded_at", "h"),
    ]
    cur = conn.execute(
        f"""
        SELECT {', '.join(select_fields)}
        FROM {hypothesis_ref} h
        {join_clause}
        WHERE {' AND '.join(predicates)}
          AND h.direction IN ('buy_yes', 'buy_no')
        ORDER BY recorded_at DESC
        """,
        tuple(params),
    )
    names = [description[0] for description in cur.description]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in cur.fetchall():
        item = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
        key = (_nonnull(item.get("range_label")), _nonnull(item.get("direction")))
        if key[0] and key[1] and key not in out:
            out[key] = item
    return out


def _apply_day0_mask_to_canonical_probabilities(
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
    return _bin_from_payload(payload)


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
    if yes_depth is None or no_depth is None:
        yes_ask = row.get("orderbook_top_ask") if str(row.get("selected_outcome_token_id") or "") == yes_token_id else None
        no_ask = row.get("orderbook_top_ask") if str(row.get("selected_outcome_token_id") or "") == no_token_id else None
        yes_bid = row.get("orderbook_top_bid") if str(row.get("selected_outcome_token_id") or "") == yes_token_id else None
        no_bid = row.get("orderbook_top_bid") if str(row.get("selected_outcome_token_id") or "") == no_token_id else None
        yes_depth = {"asks": _single_level(yes_ask, min_order_size), "bids": _single_level(yes_bid, min_order_size)}
        no_depth = {"asks": _single_level(no_ask, min_order_size), "bids": _single_level(no_bid, min_order_size)}
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


def _single_level(price: object, size: Decimal) -> list[dict[str, str]]:
    if price in {None, "", "ABSENT"}:
        return []
    return [{"price": str(price), "size": str(size)}]


def _p_fill_lcb_for_direction(book, *, direction: str, shares: Decimal) -> float:
    levels = {
        "buy_yes": book.yes_asks,
        "buy_no": book.no_asks,
        "sell_yes": book.yes_bids,
        "sell_no": book.no_bids,
    }[direction]
    available = sum((level.size for level in levels), Decimal("0"))
    return 1.0 if available >= shares else 0.0


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


def _runtime_bankroll_usd() -> float:
    from src.runtime import bankroll_provider

    bankroll = bankroll_provider.current()
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
