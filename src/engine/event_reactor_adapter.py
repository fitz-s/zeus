"""Engine adapter for EDLI opportunity reactor construction.

The adapter connects EDLI events to the event-bound no-submit proof kernel. It
does not call the broad cycle runner and it does not cross the executor or venue
side-effect boundary.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace as dataclass_replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
from collections.abc import Mapping
from typing import Any, Callable

import numpy as np

from src.contracts.execution_intent import ExecutableCostBasis
from src.contracts.execution_price import ExecutionPrice
from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import DecisionCertificate, build_certificate
from src.decision_kernel.certificates.action import build_actionable_trade_certificate
from src.decision_kernel.certificates.execution import (
    build_execution_command_certificate_from_final_intent,
    build_execution_receipt_certificate,
    build_executor_expressibility_certificate,
    build_final_intent_certificate_from_actionable,
    build_live_cap_transition_certificate,
    build_pre_submit_revalidation_certificate,
)
from src.decision_kernel.compiler import (
    DecisionCompiler,
    FORECAST_LIVE_ELIGIBLE_STATUS,
    AuthorityEvidence,
    EvidenceClock,
    NoSubmitProofBundle,
    normalize_forecast_reader_status,
)
from src.engine.event_bound_final_intent import (
    EventBoundExecutorSubmitResult,
    EventBoundFinalIntent,
    build_event_bound_final_intent_receipt,
    serialize_event_bound_final_intent_receipt,
    validate_final_intent_cert_for_existing_executor,
)
from src.state.snapshot_repo import executable_snapshot_from_row, get_snapshot
from src.events.candidate_binding import MarketTopologyCandidate
from src.events.decision_engine import EventBoundDecisionEngine, EventBoundDecisionRequest
from src.events.event_store import EventStore
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.money_path_adapters import evaluate_fdr_full_family, evaluate_kelly, evaluate_riskguard
from src.events.opportunity_event import OpportunityEvent
from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor, ReactorConfig
from src.riskguard.risk_level import RiskLevel
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.config import runtime_cities_by_name, edge_n_bootstrap, settings
from src.contracts.settlement_semantics import SettlementSemantics
from src.strategy.market_fusion import MODEL_ONLY_POSTERIOR_MODE
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
    p_cal_vector_hash: str
    p_live_vector_hash: str
    missing_reason: str | None = None


@dataclass(frozen=True)
class PreSubmitAuthorityWitness:
    quote_seen_at: str
    book_hash: str
    current_best_bid: float
    current_best_ask: float
    tick_size: float
    min_order_size: float
    neg_risk: bool
    heartbeat_status: str
    user_ws_status: str
    venue_connectivity_status: str
    balance_allowance_status: str
    book_authority_id: str
    book_captured_at: str
    heartbeat_authority_id: str
    heartbeat_checked_at: str
    user_ws_authority_id: str
    user_ws_checked_at: str
    venue_connectivity_authority_id: str
    venue_connectivity_checked_at: str
    balance_allowance_authority_id: str
    balance_allowance_checked_at: str
    checked_at: str | None = None
    max_quote_age_ms: int = 1000


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
            require_fresh=False,  # entry gate proves market identity; price-freshness is enforced at submission
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
    live_cap_conn: sqlite3.Connection | None = None,
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


def event_bound_live_adapter_from_trade_conn(
    trade_conn: sqlite3.Connection,
    *,
    get_current_level: Callable[[], RiskLevel],
    forecast_conn: sqlite3.Connection | None = None,
    topology_conn: sqlite3.Connection | None = None,
    calibration_conn: sqlite3.Connection | None = None,
    live_cap_conn: sqlite3.Connection | None = None,
    bankroll_usd_provider: Callable[[], float | None] | None = None,
    real_order_submit_enabled: bool = False,
    live_canary_enabled: bool = False,
    tiny_live_max_notional_usd: float = 5.0,
    executor_submit: Callable[[DecisionCertificate, DecisionCertificate], EventBoundExecutorSubmitResult] | None = None,
    pre_submit_authority_provider: Callable[[DecisionCertificate, DecisionCertificate, datetime], PreSubmitAuthorityWitness] | None = None,
    durable_submit_outbox_enabled: bool = False,
    canary_force_taker_provider: Callable[[], bool] | None = None,
    taker_fok_fak_live_enabled: bool = False,
) -> Callable[[OpportunityEvent, datetime], EventSubmissionReceipt]:
    """Build the event-bound live certificate chain up to the executor boundary.

    This first full-live increment deliberately stops before executor submit
    when real submit is disabled. It creates the durable proof shape that a
    later live-canary cut can submit through the existing executor seam.
    """

    def _submit(event: OpportunityEvent, decision_time: datetime) -> EventSubmissionReceipt:
        no_submit_receipt = build_event_bound_no_submit_receipt(
            event,
            trade_conn=trade_conn,
            decision_time=decision_time,
            forecast_conn=forecast_conn,
            topology_conn=topology_conn,
            calibration_conn=calibration_conn,
            get_current_level=get_current_level,
            bankroll_usd_provider=bankroll_usd_provider,
        )
        if no_submit_receipt.proof_accepted is not True or no_submit_receipt.decision_proof_bundle is None:
            return no_submit_receipt
        if real_order_submit_enabled and not live_canary_enabled:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="LIVE_CANARY_DISABLED",
                proof_accepted=False,
            )
        if real_order_submit_enabled and not durable_submit_outbox_enabled:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED",
                proof_accepted=False,
            )
        if real_order_submit_enabled and executor_submit is None:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="EXECUTOR_BOUNDARY_MISSING",
                proof_accepted=False,
            )
        # Canary knob (§7): force the taker branch (bypassing the governor's
        # maker/taker CHOICE, never its NO_TRADE/risk gates) while the canary is
        # active and below its min fill count. main.py owns the count gate via
        # ``canary_force_taker_provider``; absent a provider, the canary stage
        # flag itself drives the force (the count gate lives upstream in the
        # stage-readiness check).
        if canary_force_taker_provider is not None:
            try:
                canary_force_taker = bool(canary_force_taker_provider())
            except Exception:
                canary_force_taker = bool(live_canary_enabled)
        else:
            canary_force_taker = bool(live_canary_enabled)
        try:
            if real_order_submit_enabled:
                build_conn = live_cap_conn or trade_conn
                command_certificates = _run_live_order_build_savepoint(
                    build_conn,
                    lambda: _build_live_execution_command_certificates(
                        event=event,
                        receipt=no_submit_receipt,
                        decision_time=decision_time.astimezone(UTC),
                        tiny_live_max_notional_usd=tiny_live_max_notional_usd,
                        live_cap_conn=build_conn,
                        trade_conn=trade_conn,
                        pre_submit_authority_provider=pre_submit_authority_provider,
                        canary_force_taker=canary_force_taker,
                        taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
                    ),
                )
                final_intent = _required_cert(command_certificates, claims.FINAL_INTENT)
                command = _required_cert(command_certificates, claims.EXECUTION_COMMAND)
                assert executor_submit is not None
                _append_venue_submit_attempted_aggregate_event(
                    live_cap_conn or trade_conn,
                    command,
                    decision_time=decision_time.astimezone(UTC),
                )
                submit_result = executor_submit(final_intent, command)
                receipt_cert = build_execution_receipt_certificate(
                    execution_command_cert=command,
                    decision_time=decision_time.astimezone(UTC),
                    status=submit_result.status,
                    reason_code=submit_result.reason_code,
                    submit_started_at=submit_result.submit_started_at,
                    submit_finished_at=submit_result.submit_finished_at,
                    venue_order_id=submit_result.venue_order_id,
                    raw_response=submit_result.raw_response,
                    raw_response_hash=submit_result.raw_response_hash,
                    reconciliation_followup_required=submit_result.reconciliation_followup_required,
                    venue_call_started=submit_result.venue_call_started,
                    venue_ack_received=submit_result.venue_ack_received,
                    side_effect_known=submit_result.side_effect_known,
                )
                _append_submit_terminal_aggregate_event(
                    live_cap_conn or trade_conn,
                    command,
                    receipt_cert,
                    submit_result=submit_result,
                    decision_time=decision_time.astimezone(UTC),
                )
                transition_cert = _transition_live_cap_after_submit(
                    command_certificates,
                    live_cap_conn or trade_conn,
                    command,
                    receipt_cert,
                    submit_result,
                    decision_time=decision_time.astimezone(UTC),
                )
                certificates = command_certificates + (receipt_cert, transition_cert)
                side_effect_status = submit_result.status
                submitted = submit_result.status in {"SUBMITTED"}
                reason = submit_result.reason_code
            else:
                build_conn = live_cap_conn or trade_conn
                certificates = _run_live_order_build_savepoint(
                    build_conn,
                    lambda: _build_submit_disabled_live_certificates(
                        event=event,
                        receipt=no_submit_receipt,
                        decision_time=decision_time.astimezone(UTC),
                        tiny_live_max_notional_usd=tiny_live_max_notional_usd,
                        live_cap_conn=build_conn,
                        trade_conn=trade_conn,
                        pre_submit_authority_provider=pre_submit_authority_provider,
                        canary_force_taker=canary_force_taker,
                        taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
                    ),
                )
                side_effect_status = "SUBMIT_DISABLED"
                submitted = False
                reason = "real_order_submit_disabled"
        except Exception as exc:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason=f"EDLI_LIVE_CERTIFICATE_BUILD_FAILED:{exc}",
                proof_accepted=False,
            )
        return EventSubmissionReceipt(
            submitted=submitted,
            event_id=no_submit_receipt.event_id,
            causal_snapshot_id=no_submit_receipt.causal_snapshot_id,
            city=no_submit_receipt.city,
            target_date=no_submit_receipt.target_date,
            metric=no_submit_receipt.metric,
            condition_id=no_submit_receipt.condition_id,
            token_id=no_submit_receipt.token_id,
            outcome_label=no_submit_receipt.outcome_label,
            candidate_id=no_submit_receipt.candidate_id,
            executable_snapshot_id=no_submit_receipt.executable_snapshot_id,
            family_id=no_submit_receipt.family_id,
            bin_label=no_submit_receipt.bin_label,
            direction=no_submit_receipt.direction,
            q_live=no_submit_receipt.q_live,
            q_lcb_5pct=no_submit_receipt.q_lcb_5pct,
            c_fee_adjusted=no_submit_receipt.c_fee_adjusted,
            c_cost_95pct=no_submit_receipt.c_cost_95pct,
            p_fill_lcb=no_submit_receipt.p_fill_lcb,
            trade_score=no_submit_receipt.trade_score,
            native_quote_available=no_submit_receipt.native_quote_available,
            source_status=no_submit_receipt.source_status,
            family_complete=no_submit_receipt.family_complete,
            trade_score_positive=no_submit_receipt.trade_score_positive,
            fdr_pass=no_submit_receipt.fdr_pass,
            fdr_family_id=no_submit_receipt.fdr_family_id,
            fdr_hypothesis_count=no_submit_receipt.fdr_hypothesis_count,
            kelly_pass=no_submit_receipt.kelly_pass,
            kelly_execution_price_type=no_submit_receipt.kelly_execution_price_type,
            kelly_price_fee_deducted=no_submit_receipt.kelly_price_fee_deducted,
            kelly_size_usd=no_submit_receipt.kelly_size_usd,
            kelly_cost_basis_id=no_submit_receipt.kelly_cost_basis_id,
            kelly_decision_id=no_submit_receipt.kelly_decision_id,
            risk_decision_id=no_submit_receipt.risk_decision_id,
            final_intent_id=no_submit_receipt.final_intent_id,
            neg_risk=no_submit_receipt.neg_risk,
            side_effect_status=side_effect_status,
            reason=reason,
            proof_accepted=True,
            decision_proof_bundle=certificates,
        )

    return _submit


def _run_live_order_build_savepoint(
    conn: sqlite3.Connection,
    build: Callable[[], tuple[DecisionCertificate, ...]],
) -> tuple[DecisionCertificate, ...]:
    conn.execute("SAVEPOINT edli_live_order_build")
    try:
        result = build()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT edli_live_order_build")
        conn.execute("RELEASE SAVEPOINT edli_live_order_build")
        raise
    conn.execute("RELEASE SAVEPOINT edli_live_order_build")
    return result


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
        require_fresh=False,  # FDR proves family identity/completeness; price-freshness is enforced at submission
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
        (
            kelly_multiplier,
            _bias_decay_applied,
            _bias_decay_native,
            _bias_decay_reason,
        ) = _maybe_bias_decay_kelly_haircut(kelly_multiplier, family=family)
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
            "bias_decay_applied": bool(_bias_decay_applied),
            "bias_decay_bias_native": _bias_decay_native,
            "bias_decay_reason": _bias_decay_reason,
            "bias_decay_kelly_factor": float(settings["edli_v1"].get("bias_decay_kelly_factor", 0.5)) if _bias_decay_applied else 1.0,
            "neg_risk": bool(row.get("neg_risk") or False),
            "native_quote_available": True,
            "source_status": FORECAST_LIVE_ELIGIBLE_STATUS,
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
        trade_conn=trade_conn,
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
        neg_risk=bool(raw_receipt.get("neg_risk") or False),
        side_effect_status="NO_SUBMIT",
        reason=str(raw_receipt.get("reason") or "event_bound_final_intent_no_submit"),
        proof_accepted=bool(raw_receipt.get("proof_accepted")),
        decision_proof_bundle=decision_proof_bundle,
    )


def _build_submit_disabled_live_certificates(
    *,
    event: OpportunityEvent,
    receipt: EventSubmissionReceipt,
    decision_time: datetime,
    tiny_live_max_notional_usd: float,
    live_cap_conn: sqlite3.Connection | None = None,
    trade_conn: sqlite3.Connection | None = None,
    pre_submit_authority_provider: Callable[[DecisionCertificate, DecisionCertificate, datetime], PreSubmitAuthorityWitness] | None = None,
    canary_force_taker: bool = False,
    taker_fok_fak_live_enabled: bool = False,
) -> tuple[DecisionCertificate, ...]:
    command_certificates = _build_live_execution_command_certificates(
        event=event,
        receipt=receipt,
        decision_time=decision_time,
        tiny_live_max_notional_usd=tiny_live_max_notional_usd,
        live_cap_conn=live_cap_conn,
        trade_conn=trade_conn,
        pre_submit_authority_provider=pre_submit_authority_provider,
        canary_force_taker=canary_force_taker,
        taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
    )
    command = _required_cert(command_certificates, claims.EXECUTION_COMMAND)
    receipt_cert = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=decision_time,
        status="SUBMIT_DISABLED",
        reason_code="REAL_ORDER_SUBMIT_DISABLED",
    )
    transition_cert = _release_live_cap_for_submit_disabled(
        command_certificates,
        receipt_cert,
        live_cap_conn,
        decision_time=decision_time,
    )
    return command_certificates + (receipt_cert, transition_cert)


def _build_live_execution_command_certificates(
    *,
    event: OpportunityEvent,
    receipt: EventSubmissionReceipt,
    decision_time: datetime,
    tiny_live_max_notional_usd: float,
    live_cap_conn: sqlite3.Connection | None = None,
    trade_conn: sqlite3.Connection | None = None,
    pre_submit_authority_provider: Callable[[DecisionCertificate, DecisionCertificate, datetime], PreSubmitAuthorityWitness] | None = None,
    canary_force_taker: bool = False,
    taker_fok_fak_live_enabled: bool = False,
) -> tuple[DecisionCertificate, ...]:
    proof_bundle = receipt.decision_proof_bundle
    compile_result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        mode="NO_SUBMIT",
        proof_bundle=proof_bundle,
    )
    if compile_result.status != "VERIFIED":
        # KILLER 2 (2026-05-31): surface the UNDERLYING failing assertion, not just the
        # generic stage reason_code. compiler._rejected() captures the specific failure
        # message in CompileFailure.reason_detail (e.g. the exact field/parent that failed
        # _validate_no_submit_parent_consistency), but only reason_code was propagated —
        # so 147/308 positive-edge candidates died as opaque NO_SUBMIT_CERTIFICATE_REJECTED
        # with no diagnosable sub-reason in no_trade_regret_events. Append reason_detail so
        # the regret row records WHY the no-submit certificate was rejected.
        if compile_result.failures:
            failure = compile_result.failures[0]
            reason = failure.reason_code
            detail = getattr(failure, "reason_detail", None)
            if detail:
                reason = f"{reason}:{detail}"
        else:
            reason = "NO_SUBMIT_CERTIFICATE_REJECTED"
        raise ValueError(reason)
    base_certs = tuple(
        cert
        for cert in compile_result.certificates
        if cert.certificate_type not in {claims.NO_SUBMIT_DECISION, claims.NO_SUBMIT_MODE}
    )
    executable_snapshot = _required_cert(base_certs, claims.EXECUTABLE_SNAPSHOT)
    live_cap = _build_live_cap_certificate_from_ledger(
        event=event,
        receipt=receipt,
        decision_time=decision_time,
        max_notional_usd=tiny_live_max_notional_usd,
        live_cap_conn=live_cap_conn,
        persist=False,
    )
    try:
        actionable = build_actionable_trade_certificate(
            payload=_actionable_payload_from_receipt(receipt, live_cap),
            parent_certificates=base_certs + (live_cap,),
            decision_time=decision_time,
        )
        forecast_authority = _required_cert(base_certs, claims.FORECAST_AUTHORITY)
        quote_feasibility = _required_cert(base_certs, claims.QUOTE_FEASIBILITY)
        cost_model = _required_cert(base_certs, claims.COST_MODEL)
        quote_payload = quote_feasibility.payload
        best_bid = _optional_float(quote_payload.get("best_bid"))
        best_ask = _optional_float(quote_payload.get("best_ask"))
        order_mode = _select_edli_order_mode(
            actionable_payload=actionable.payload,
            quote_payload=quote_payload,
            best_bid=best_bid,
            best_ask=best_ask,
            executable_snapshot=executable_snapshot,
            canary_force_taker=canary_force_taker,
        )
        # WALL #1 (GATE #85 follow-on, 2026-06-01): the passive-maker context is a
        # MAKER-ONLY structural input. ``FinalExecutionIntent`` only requires it when
        # ``order_policy == "post_only_passive_limit"`` (execution_intent.py:1735); a
        # taker FOK/FAK crosses the JIT book at submit and never rests, so its
        # economics do not depend on the snapshot's top-of-book maker context.
        #
        # The pre-#85 path built ``_passive_maker_context_from_authorities``
        # UNCONDITIONALLY, which raises QUOTE_FEASIBILITY_BID_ASK_REQUIRED whenever the
        # elected snapshot has no captured book — killing every taker candidate whose
        # snapshot happened to be book-less (the DOMINANT live wall: 713/2h). Conditioning
        # the construction on order_mode makes that rejection CATEGORY impossible for
        # taker orders (Fitz #1: make the category impossible, not the instance). MAKER
        # still requires the maker context (and still raises if bid/ask are absent — the
        # correct fail-closed behavior, since a resting maker order genuinely needs a book).
        passive_maker_context = (
            _passive_maker_context_from_authorities(
                actionable=actionable,
                quote_feasibility_cert=quote_feasibility,
                executable_snapshot_cert=executable_snapshot,
                decision_time=decision_time,
            )
            if str(order_mode).strip().upper() == "MAKER"
            else None
        )
        # SIZE-TO-DEPTH + SWEEP-VWAP (Wall B / Wall C, 2026-06-01):
        # For TAKER FOK orders, compute the crossable depth and sweep VWAP from
        # the elected snapshot's live book BEFORE building the cert.  This ensures:
        #   (a) size is capped at available depth (FOK semantics preserved on the
        #       sized amount → no DEPTH_INSUFFICIENT at executor validation).
        #   (b) expected_fill_price_before_fee = sweep VWAP, not limit_price, so
        #       the executor sweep-average check (executor.py:1778) passes on
        #       multi-level books.
        # If no trade_conn is available, or order is MAKER, skip (legacy behaviour).
        available_crossable_shares: float | None = None
        sweep_expected_fill_price: float | None = None
        if str(order_mode).strip().upper() == "TAKER" and trade_conn is not None:
            from src.contracts.execution_intent import simulate_clob_sweep
            _snap_id_for_depth = str(
                executable_snapshot.payload.get("identity")
                or executable_snapshot.payload.get("selected_snapshot_id")
                or ""
            )
            try:
                _snap_for_depth = get_snapshot(trade_conn, _snap_id_for_depth) if _snap_id_for_depth else None
            except Exception:
                _snap_for_depth = None
            if _snap_for_depth is not None:
                _action_payload = actionable.payload
                _min_order_size_d = Decimal(str(
                    executable_snapshot.payload.get("min_order_size") or "1.0"
                ))
                _tick_size_d = Decimal(str(
                    executable_snapshot.payload.get("min_tick_size") or "0.01"
                ))
                _reservation = Decimal(str(_action_payload.get("c_fee_adjusted") or "0"))
                _ask_for_limit = (
                    Decimal(str(best_ask)) if best_ask is not None else _reservation
                )
                _limit_price_d = min(_ask_for_limit, _reservation)
                # Tick-align limit price (floor) using the canonical tick_size
                import math as _math
                if _tick_size_d > 0:
                    _limit_price_d = Decimal(str(
                        round(_math.floor(float(_limit_price_d) / float(_tick_size_d) + 1e-9) * float(_tick_size_d), 10)
                    ))
                _reserved_notional = Decimal(str(
                    _action_payload.get("live_cap_reserved_notional_usd")
                    or _action_payload.get("kelly_size_usd")
                    or "0"
                ))
                _desired_shares = max(_min_order_size_d, _reserved_notional / _limit_price_d) if _limit_price_d > 0 else _min_order_size_d
                _depth_sweep = simulate_clob_sweep(
                    snapshot=_snap_for_depth,
                    direction=str(_action_payload.get("direction") or "buy_no"),
                    requested_size_kind="shares",
                    requested_size_value=_desired_shares,
                    limit_price=_limit_price_d,
                )
                if _depth_sweep.filled_shares > 0:
                    available_crossable_shares = float(_depth_sweep.filled_shares)
                    sweep_expected_fill_price = float(_depth_sweep.average_price) if _depth_sweep.average_price is not None else None
        final_intent = build_final_intent_certificate_from_actionable(
            actionable_cert=actionable,
            executable_snapshot_cert=executable_snapshot,
            quote_feasibility_cert=quote_feasibility,
            cost_model_cert=cost_model,
            forecast_authority_cert=forecast_authority,
            decision_source_context=forecast_authority.payload,
            passive_maker_context=passive_maker_context,
            decision_time=decision_time,
            order_mode=order_mode,
            tick_size=_float_or_default(executable_snapshot.payload.get("min_tick_size"), 0.01),
            min_order_size=_float_or_default(executable_snapshot.payload.get("min_order_size"), 1.0),
            best_bid=best_bid,
            best_ask=best_ask,
            taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
            available_crossable_shares=available_crossable_shares,
            sweep_expected_fill_price=sweep_expected_fill_price,
        )
        executor_native_intent_hash = validate_final_intent_cert_for_existing_executor(final_intent)
        aggregate_ledger = LiveOrderAggregateLedger(live_cap_conn)
        aggregate_id = _live_order_aggregate_id(event.event_id, str(final_intent.payload["final_intent_id"]))
        decision_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="DecisionProofAccepted",
            payload={
                "event_id": event.event_id,
                "final_intent_id": final_intent.payload["final_intent_id"],
                "no_submit_certificate_count": len(base_certs),
                "no_submit_receipt_event_id": receipt.event_id,
            },
            occurred_at=decision_time,
            source_authority="decision_kernel",
        )
        submit_plan_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": event.event_id,
                "final_intent_id": final_intent.payload["final_intent_id"],
                "condition_id": final_intent.payload["condition_id"],
                "token_id": final_intent.payload["token_id"],
                "direction": final_intent.payload["direction"],
                "order_type": final_intent.payload["order_type"],
                "time_in_force": final_intent.payload["time_in_force"],
                "post_only": final_intent.payload["post_only"],
                "limit_price": final_intent.payload["limit_price"],
                "size": final_intent.payload["size"],
            },
            occurred_at=decision_time,
            source_authority="engine_adapter",
            expected_parent_event_hash=decision_event.event_hash,
        )
        pre_submit_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_revalidation_payload_from_final_intent(
                final_intent=final_intent,
                executable_snapshot=executable_snapshot,
                decision_time=decision_time,
                authority_witness=_require_pre_submit_authority_witness(
                    pre_submit_authority_provider,
                    final_intent,
                    executable_snapshot,
                    decision_time,
                ),
            ),
            occurred_at=decision_time,
            source_authority="engine_adapter",
            expected_parent_event_hash=submit_plan_event.event_hash,
        )
        live_cap_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="LiveCapReserved",
            payload={
                "event_id": event.event_id,
                "final_intent_id": final_intent.payload["final_intent_id"],
                "usage_id": live_cap.payload["usage_id"],
                "reserved_notional_usd": live_cap.payload["reserved_notional_usd"],
                "reservation_status": live_cap.payload["reservation_status"],
            },
            occurred_at=decision_time,
            source_authority="live_cap_ledger",
            expected_parent_event_hash=pre_submit_event.event_hash,
        )
        execution_command_id = _execution_command_id_from_final_intent(actionable, final_intent)
        expressibility = build_executor_expressibility_certificate(
            final_intent_cert=final_intent,
            executable_snapshot_cert=executable_snapshot,
            live_cap_cert=live_cap,
            decision_time=decision_time,
            executor_native_intent_hash=executor_native_intent_hash,
        )
        command_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": event.event_id,
                "final_intent_id": final_intent.payload["final_intent_id"],
                "execution_command_id": execution_command_id,
                "pre_submit_event_hash": pre_submit_event.event_hash,
                "live_cap_reserved_event_hash": live_cap_event.event_hash,
                "usage_id": live_cap.payload["usage_id"],
            },
            occurred_at=decision_time,
            source_authority="engine_adapter",
            expected_parent_event_hash=live_cap_event.event_hash,
        )
        pre_submit = build_pre_submit_revalidation_certificate(
            pre_submit_event=pre_submit_event,
            final_intent_cert=final_intent,
            live_cap_cert=live_cap,
            decision_time=decision_time,
            execution_command_event_hash=command_event.event_hash,
        )
        command = build_execution_command_certificate_from_final_intent(
            actionable_cert=actionable,
            final_intent_cert=final_intent,
            executor_expressibility_cert=expressibility,
            live_cap_cert=live_cap,
            pre_submit_revalidation_cert=pre_submit,
            decision_time=decision_time,
        )
        from src.events.live_cap import LiveCapLedger

        reserve_result = LiveCapLedger(live_cap_conn).reserve(
            event_id=event.event_id,
            decision_time=decision_time,
            cap_scope="tiny_live_canary",
            requested_notional_usd=float(live_cap.payload["reserved_notional_usd"]),
            max_notional_usd=float(tiny_live_max_notional_usd),
            max_orders_per_day=1,
            final_intent_id=str(final_intent.payload["final_intent_id"]),
            execution_command_id=execution_command_id,
        )
        if reserve_result.usage_id != str(live_cap.payload["usage_id"]):
            raise ValueError("live cap reservation drift for provisional certificate")
    except Exception:
        raise
    return base_certs + (live_cap, actionable, final_intent, expressibility, pre_submit, command)


def _actionable_payload_from_receipt(
    receipt: EventSubmissionReceipt,
    live_cap_cert: DecisionCertificate,
) -> dict[str, object]:
    reserved_notional = float(live_cap_cert.payload["reserved_notional_usd"])
    return {
        "event_id": receipt.event_id,
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": receipt.causal_snapshot_id,
        "family_id": receipt.family_id,
        "candidate_id": receipt.candidate_id,
        "condition_id": receipt.condition_id,
        "token_id": receipt.token_id,
        "direction": receipt.direction,
        "executable_snapshot_id": receipt.executable_snapshot_id,
        "q_live": receipt.q_live,
        "q_lcb_5pct": receipt.q_lcb_5pct,
        "c_fee_adjusted": receipt.c_fee_adjusted,
        "c_cost_95pct": receipt.c_cost_95pct,
        "p_fill_lcb": receipt.p_fill_lcb,
        "trade_score": receipt.trade_score,
        "action_score": receipt.trade_score,
        "fdr_family_id": receipt.fdr_family_id,
        "kelly_decision_id": receipt.kelly_decision_id,
        "kelly_size_usd": receipt.kelly_size_usd,
        "risk_decision_id": receipt.risk_decision_id,
        "live_cap_usage_id": live_cap_cert.payload["usage_id"],
        "live_cap_reserved_notional_usd": reserved_notional,
        "final_intent_id": receipt.final_intent_id,
        "neg_risk": receipt.neg_risk,
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": receipt.native_quote_available,
        "submitted": False,
    }


def _live_order_aggregate_id(event_id: str, final_intent_id: str) -> str:
    return f"{event_id}:{final_intent_id}"


def _execution_command_id_from_final_intent(
    actionable: DecisionCertificate,
    final_intent: DecisionCertificate,
) -> str:
    action = actionable.payload
    intent = final_intent.payload
    return (
        f"edli_exec_cmd:{action['event_id']}:{intent['final_intent_id']}:"
        f"{intent['token_id']}:{intent['direction']}"
    )


def _pre_submit_revalidation_payload_from_final_intent(
    *,
    final_intent: DecisionCertificate,
    executable_snapshot: DecisionCertificate,
    decision_time: datetime,
    authority_witness: PreSubmitAuthorityWitness,
) -> dict[str, object]:
    payload = final_intent.payload
    limit_price = _float_or_default(payload.get("limit_price"), 0.01)
    quote_seen_at = _parse_utc(authority_witness.quote_seen_at)
    if quote_seen_at is None:
        raise ValueError("PRE_SUBMIT_QUOTE_SEEN_AT_REQUIRED")
    quote_age_ms = int(max(0.0, (decision_time.astimezone(UTC) - quote_seen_at).total_seconds() * 1000.0))
    current_best_bid = float(authority_witness.current_best_bid)
    current_best_ask = float(authority_witness.current_best_ask)
    tick_size = float(authority_witness.tick_size)
    min_order_size = float(authority_witness.min_order_size)
    side = str(payload["side"])
    would_cross = _would_cross_post_only_book(
        side=side,
        limit_price=limit_price,
        current_best_bid=current_best_bid,
        current_best_ask=current_best_ask,
    )
    return {
        "event_id": payload["event_id"],
        "final_intent_id": payload["final_intent_id"],
        "condition_id": payload["condition_id"],
        "token_id": payload["token_id"],
        "side": payload["side"],
        "direction": payload["direction"],
        "order_type": payload["order_type"],
        "time_in_force": payload["time_in_force"],
        "post_only": payload["post_only"],
        "checked_at": authority_witness.checked_at or decision_time.isoformat(),
        "quote_seen_at": authority_witness.quote_seen_at,
        "quote_age_ms": quote_age_ms,
        "max_quote_age_ms": int(authority_witness.max_quote_age_ms),
        "book_hash": authority_witness.book_hash,
        "current_best_bid": current_best_bid,
        "current_best_ask": current_best_ask,
        "limit_price": limit_price,
        "would_cross_book": would_cross,
        "tick_size": tick_size,
        "tick_aligned": _is_price_tick_aligned(limit_price, tick_size),
        "min_order_size": min_order_size,
        "size_ok": _float_or_default(payload.get("size"), 0.0) >= min_order_size,
        "neg_risk": authority_witness.neg_risk,
        "heartbeat_status": authority_witness.heartbeat_status,
        "user_ws_status": authority_witness.user_ws_status,
        "venue_connectivity_status": authority_witness.venue_connectivity_status,
        "balance_allowance_status": authority_witness.balance_allowance_status,
        "book_authority_id": authority_witness.book_authority_id,
        "book_captured_at": authority_witness.book_captured_at,
        "heartbeat_authority_id": authority_witness.heartbeat_authority_id,
        "heartbeat_checked_at": authority_witness.heartbeat_checked_at,
        "user_ws_authority_id": authority_witness.user_ws_authority_id,
        "user_ws_checked_at": authority_witness.user_ws_checked_at,
        "venue_connectivity_authority_id": authority_witness.venue_connectivity_authority_id,
        "venue_connectivity_checked_at": authority_witness.venue_connectivity_checked_at,
        "balance_allowance_authority_id": authority_witness.balance_allowance_authority_id,
        "balance_allowance_checked_at": authority_witness.balance_allowance_checked_at,
        "expected_edge_source_certificate_hash": payload.get("actionable_certificate_hash"),
        "cost_basis_source_certificate_hash": payload.get("cost_basis_hash"),
        "final_intent_certificate_hash": final_intent.certificate_hash,
    }


def _require_pre_submit_authority_witness(
    provider: Callable[[DecisionCertificate, DecisionCertificate, datetime], PreSubmitAuthorityWitness] | None,
    final_intent: DecisionCertificate,
    executable_snapshot: DecisionCertificate,
    decision_time: datetime,
) -> PreSubmitAuthorityWitness:
    if provider is None:
        raise ValueError("PRE_SUBMIT_AUTHORITY_WITNESS_REQUIRED")
    witness = provider(final_intent, executable_snapshot, decision_time)
    if not isinstance(witness, PreSubmitAuthorityWitness):
        raise ValueError("PRE_SUBMIT_AUTHORITY_WITNESS_REQUIRED")
    required_text_fields = {
        "book_hash": witness.book_hash,
        "book_authority_id": witness.book_authority_id,
        "book_captured_at": witness.book_captured_at,
        "heartbeat_authority_id": witness.heartbeat_authority_id,
        "heartbeat_checked_at": witness.heartbeat_checked_at,
        "user_ws_authority_id": witness.user_ws_authority_id,
        "user_ws_checked_at": witness.user_ws_checked_at,
        "venue_connectivity_authority_id": witness.venue_connectivity_authority_id,
        "venue_connectivity_checked_at": witness.venue_connectivity_checked_at,
        "balance_allowance_authority_id": witness.balance_allowance_authority_id,
        "balance_allowance_checked_at": witness.balance_allowance_checked_at,
    }
    missing = [field for field, value in required_text_fields.items() if not str(value or "").strip()]
    if missing:
        raise ValueError("PRE_SUBMIT_AUTHORITY_PROVENANCE_REQUIRED:" + ",".join(missing))
    return witness


def _would_cross_post_only_book(
    *,
    side: str,
    limit_price: float,
    current_best_bid: float,
    current_best_ask: float,
) -> bool:
    if side == "BUY":
        return limit_price >= current_best_ask
    if side == "SELL":
        return limit_price <= current_best_bid
    raise ValueError(f"unsupported pre-submit side: {side!r}")


def _is_price_tick_aligned(price: float, tick_size: float) -> bool:
    if tick_size <= 0:
        return False
    units = round(price / tick_size)
    return abs(price - units * tick_size) < 1e-9


def _build_live_cap_certificate_from_ledger(
    *,
    event: OpportunityEvent,
    receipt: EventSubmissionReceipt,
    decision_time: datetime,
    max_notional_usd: float,
    live_cap_conn: sqlite3.Connection | None,
    persist: bool = True,
) -> DecisionCertificate:
    if live_cap_conn is None:
        raise ValueError("LIVE_CAP_LEDGER_CONNECTION_REQUIRED")
    from src.events.live_cap import LiveCapLedger
    price = _float_or_default(receipt.c_fee_adjusted, 0.01)
    min_order_notional = min(max_notional_usd, max(price, 0.01))
    requested_notional = max(min(float(receipt.kelly_size_usd or 0.0), max_notional_usd), min_order_notional)
    usage_id = LiveCapLedger._usage_id(event.event_id, "tiny_live_canary")
    if persist:
        reservation = LiveCapLedger(live_cap_conn).reserve(
            event_id=event.event_id,
            decision_time=decision_time,
            cap_scope="tiny_live_canary",
            requested_notional_usd=float(requested_notional),
            max_notional_usd=float(max_notional_usd),
            max_orders_per_day=1,
            final_intent_id=receipt.final_intent_id,
        )
    else:
        from src.events.live_cap import LiveCapReservation

        reservation = LiveCapReservation(
            usage_id=usage_id,
            event_id=event.event_id,
            decision_time=decision_time,
            cap_scope="tiny_live_canary",
            max_notional_usd=float(max_notional_usd),
            max_orders_per_day=1,
            reserved_notional_usd=float(requested_notional),
            order_count=1,
            reservation_status="RESERVED",
            final_intent_id=receipt.final_intent_id,
        )
    payload = reservation.certificate_payload()
    return build_certificate(
        certificate_type=claims.LIVE_CAP,
        semantic_key=f"live_cap:{reservation.usage_id}",
        claim_type=claims.LIVE_CAP,
        mode="LIVE",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload=payload,
        parent_edges=(),
        parent_certificates=(),
        authority_id="edli.live_cap",
        authority_version="v1",
        algorithm_id="edli.submit_disabled_live_cap",
        algorithm_version="v1",
    )


def _release_live_cap_for_submit_disabled(
    certificates: tuple[DecisionCertificate, ...],
    receipt_cert: DecisionCertificate,
    live_cap_conn: sqlite3.Connection | None,
    *,
    decision_time: datetime,
) -> DecisionCertificate:
    live_cap = _required_cert(certificates, claims.LIVE_CAP)
    command = _required_cert(certificates, claims.EXECUTION_COMMAND)
    _release_live_cap_certificate(live_cap, live_cap_conn, reason="SUBMIT_DISABLED")
    cap_event_hash = _append_cap_transition_aggregate_event(
        live_cap_conn,
        command,
        receipt_cert,
        to_status="RELEASED",
        projection_status="RELEASED",
        reason_code="SUBMIT_DISABLED",
        decision_time=decision_time,
    )
    return build_live_cap_transition_certificate(
        live_cap_cert=live_cap,
        execution_receipt_cert=receipt_cert,
        decision_time=decision_time,
        to_status="RELEASED",
        reason_code="SUBMIT_DISABLED",
        aggregate_event_hash=cap_event_hash,
    )


def _transition_live_cap_after_submit(
    certificates: tuple[DecisionCertificate, ...],
    live_cap_conn: sqlite3.Connection,
    command: DecisionCertificate,
    receipt_cert: DecisionCertificate,
    submit_result: EventBoundExecutorSubmitResult,
    *,
    decision_time: datetime,
) -> DecisionCertificate:
    live_cap = _required_cert(certificates, claims.LIVE_CAP)
    usage_id = str(live_cap.payload["usage_id"])
    from src.events.live_cap import LiveCapLedger

    ledger = LiveCapLedger(live_cap_conn)
    if submit_result.status == "SUBMITTED":
        ledger.consume(
            usage_id,
            final_intent_id=str(command.payload["final_intent_id"]),
            execution_command_id=str(command.payload["execution_command_id"]),
        )
        return build_live_cap_transition_certificate(
            live_cap_cert=live_cap,
            execution_receipt_cert=receipt_cert,
            decision_time=decision_time,
            to_status="CONSUMED",
            reason_code=submit_result.reason_code,
            aggregate_event_hash=_append_cap_transition_aggregate_event(
                live_cap_conn,
                command,
                receipt_cert,
                to_status="CONSUMED",
                projection_status="CONSUMED",
                reason_code=submit_result.reason_code,
                decision_time=decision_time,
            ),
        )
    elif submit_result.status in {"REJECTED", "PRE_SUBMIT_ERROR"}:
        ledger.release(usage_id, submit_result.reason_code)
        return build_live_cap_transition_certificate(
            live_cap_cert=live_cap,
            execution_receipt_cert=receipt_cert,
            decision_time=decision_time,
            to_status="RELEASED",
            reason_code=submit_result.reason_code,
            aggregate_event_hash=_append_cap_transition_aggregate_event(
                live_cap_conn,
                command,
                receipt_cert,
                to_status="RELEASED",
                projection_status="RELEASED",
                reason_code=submit_result.reason_code,
                decision_time=decision_time,
            ),
        )
    elif submit_result.status in {"TIMEOUT_UNKNOWN", "POST_SUBMIT_UNKNOWN"}:
        _append_submit_unknown_aggregate_event(
            live_cap_conn,
            command,
            receipt_cert,
            submit_result=submit_result,
            decision_time=decision_time,
        )
        return build_live_cap_transition_certificate(
            live_cap_cert=live_cap,
            execution_receipt_cert=receipt_cert,
            decision_time=decision_time,
            to_status="PENDING_RECONCILE",
            projection_status="RESERVED",
            reason_code=submit_result.reason_code,
            aggregate_event_hash=_append_cap_transition_aggregate_event(
                live_cap_conn,
                command,
                receipt_cert,
                to_status="PENDING_RECONCILE",
                projection_status="RESERVED",
                reason_code=submit_result.reason_code,
                decision_time=decision_time,
            ),
        )
    raise ValueError(f"unsupported submit result status for live cap transition: {submit_result.status!r}")


def _append_venue_submit_attempted_aggregate_event(
    conn: sqlite3.Connection | None,
    command: DecisionCertificate,
    *,
    decision_time: datetime,
) -> str | None:
    if conn is None:
        return None
    aggregate_id = str(command.payload.get("aggregate_id") or "")
    if not aggregate_id:
        return None
    event = LiveOrderAggregateLedger(conn).append_event(
        aggregate_id=aggregate_id,
        event_type="VenueSubmitAttempted",
        payload={
            "event_id": command.payload["event_id"],
            "final_intent_id": command.payload["final_intent_id"],
            "execution_command_id": command.payload["execution_command_id"],
            "idempotency_key": command.payload.get("idempotency_key"),
        },
        occurred_at=decision_time,
        source_authority="existing_executor",
    )
    return event.event_hash


def _append_submit_terminal_aggregate_event(
    conn: sqlite3.Connection | None,
    command: DecisionCertificate,
    receipt_cert: DecisionCertificate,
    *,
    submit_result: EventBoundExecutorSubmitResult,
    decision_time: datetime,
) -> str | None:
    if conn is None:
        return None
    aggregate_id = str(command.payload.get("aggregate_id") or "")
    if not aggregate_id:
        return None
    if submit_result.status == "SUBMITTED":
        event = LiveOrderAggregateLedger(conn).append_event(
            aggregate_id=aggregate_id,
            event_type="VenueSubmitAcknowledged",
            payload={
                "event_id": command.payload["event_id"],
                "final_intent_id": command.payload["final_intent_id"],
                "execution_command_id": command.payload["execution_command_id"],
                "execution_receipt_hash": receipt_cert.certificate_hash,
                "venue_order_id": submit_result.venue_order_id,
                "venue_ack_received": submit_result.venue_ack_received,
                "raw_response_hash": submit_result.raw_response_hash,
            },
            occurred_at=decision_time,
            source_authority="existing_executor",
        )
        return event.event_hash
    if submit_result.status in {"REJECTED", "PRE_SUBMIT_ERROR"}:
        event = LiveOrderAggregateLedger(conn).append_event(
            aggregate_id=aggregate_id,
            event_type="SubmitRejected",
            payload={
                "event_id": command.payload["event_id"],
                "final_intent_id": command.payload["final_intent_id"],
                "execution_command_id": command.payload["execution_command_id"],
                "execution_receipt_hash": receipt_cert.certificate_hash,
                "reason_code": submit_result.reason_code,
                "venue_order_id": submit_result.venue_order_id,
                "raw_response_hash": submit_result.raw_response_hash,
            },
            occurred_at=decision_time,
            source_authority="existing_executor",
        )
        return event.event_hash
    return None


def _append_cap_transition_aggregate_event(
    conn: sqlite3.Connection | None,
    command: DecisionCertificate,
    receipt_cert: DecisionCertificate,
    *,
    to_status: str,
    projection_status: str,
    reason_code: str,
    decision_time: datetime,
) -> str | None:
    if conn is None:
        return None
    aggregate_id = str(command.payload.get("aggregate_id") or "")
    if not aggregate_id:
        return None
    event = LiveOrderAggregateLedger(conn).append_event(
        aggregate_id=aggregate_id,
        event_type="CapTransitioned",
        payload={
            "event_id": command.payload["event_id"],
            "final_intent_id": command.payload["final_intent_id"],
            "execution_command_id": command.payload["execution_command_id"],
            "execution_receipt_hash": receipt_cert.certificate_hash,
            "to_status": to_status,
            "projection_status": projection_status,
            "transition_reason": reason_code,
        },
        occurred_at=decision_time,
        source_authority="live_cap_ledger",
    )
    return event.event_hash


def _append_submit_unknown_aggregate_event(
    conn: sqlite3.Connection | None,
    command: DecisionCertificate,
    receipt_cert: DecisionCertificate,
    *,
    submit_result: EventBoundExecutorSubmitResult,
    decision_time: datetime,
) -> str | None:
    if conn is None:
        return None
    aggregate_id = str(command.payload.get("aggregate_id") or "")
    if not aggregate_id:
        return None
    event = LiveOrderAggregateLedger(conn).append_event(
        aggregate_id=aggregate_id,
        event_type="SubmitUnknown",
        payload={
            "event_id": command.payload["event_id"],
            "final_intent_id": command.payload["final_intent_id"],
            "execution_command_id": command.payload["execution_command_id"],
            "execution_receipt_hash": receipt_cert.certificate_hash,
            "submit_status": submit_result.status,
            "reason_code": submit_result.reason_code,
            "venue_call_started": submit_result.venue_call_started,
            "side_effect_known": submit_result.side_effect_known,
            "reconciliation_followup_required": submit_result.reconciliation_followup_required,
        },
        occurred_at=decision_time,
        source_authority="existing_executor",
    )
    return event.event_hash


def _passive_maker_context_from_authorities(
    *,
    actionable: DecisionCertificate,
    quote_feasibility_cert: DecisionCertificate,
    executable_snapshot_cert: DecisionCertificate,
    decision_time: datetime,
) -> dict[str, object]:
    quote_payload = quote_feasibility_cert.payload
    best_bid = quote_payload.get("best_bid")
    best_ask = quote_payload.get("best_ask")
    if best_bid in (None, "") or best_ask in (None, ""):
        raise ValueError("QUOTE_FEASIBILITY_BID_ASK_REQUIRED")
    quote_available_at = quote_feasibility_cert.header.source_available_at
    snapshot_available_at = executable_snapshot_cert.header.source_available_at
    if quote_available_at is None:
        raise ValueError("QUOTE_FEASIBILITY_SOURCE_AVAILABLE_AT_REQUIRED")
    if snapshot_available_at is None:
        raise ValueError("EXECUTABLE_SNAPSHOT_SOURCE_AVAILABLE_AT_REQUIRED")
    spread_usd = max(0.0, float(best_ask) - float(best_bid))
    p_fill_lcb = float(actionable.payload.get("p_fill_lcb") or 0.0)
    # Adverse-selection proxy (§4 Dim 4.2): A ~= recent belief volatility x spread.
    # Belief volatility is sourced from the actionable's prior-cycle posterior when
    # available (|q_posterior - q_posterior_prev|); absent a trustworthy prior we
    # fall back to A = 0, which biases the §2 boundary toward maker (the
    # conservative, documented default — never fabricate adverse cost we can't
    # source). queue_depth_ahead uses the quote's visible depth when present.
    adverse_selection_score = _adverse_selection_proxy(
        actionable_payload=actionable.payload,
        spread_usd=spread_usd,
    )
    queue_depth_ahead = _queue_depth_ahead_from_quote(quote_payload)
    return {
        "spread_usd": spread_usd,
        "quote_age_ms": int(max(0.0, (decision_time - quote_available_at).total_seconds() * 1000.0)),
        "expected_fill_probability": str(max(min(p_fill_lcb, 1.0), 0.0001)),
        "queue_depth_ahead": queue_depth_ahead,
        "adverse_selection_score": adverse_selection_score,
        "orderbook_hash_age_ms": int(max(0.0, (decision_time - snapshot_available_at).total_seconds() * 1000.0)),
        "best_bid": float(best_bid),
        "best_ask": float(best_ask),
    }


def _adverse_selection_proxy(*, actionable_payload: Mapping[str, object], spread_usd: float) -> str | None:
    """A ~= |q_posterior - q_posterior_prev| * spread (Dim 4.2 cheap proxy).

    Returns None (the conservative default that biases toward maker) when no
    trustworthy prior-cycle belief is available — Fitz #4: do not fabricate an
    adverse-selection cost from data we do not have.
    """
    q_now = actionable_payload.get("q_live")
    q_prev = actionable_payload.get("q_live_prev_cycle")
    if q_now in (None, "") or q_prev in (None, ""):
        return None
    try:
        belief_move = abs(float(q_now) - float(q_prev))
    except (TypeError, ValueError):
        return None
    return str(max(0.0, belief_move * float(spread_usd)))


def _queue_depth_ahead_from_quote(quote_payload: Mapping[str, object]) -> str | None:
    """Best-effort queue-ahead size from the quote's visible depth, else None."""
    for key in ("queue_depth_ahead", "bid_queue_size", "visible_depth"):
        raw = quote_payload.get(key)
        if raw in (None, ""):
            continue
        try:
            return str(max(0.0, float(raw)))
        except (TypeError, ValueError):
            continue
    return None


def _select_edli_order_mode(
    *,
    actionable_payload: Mapping[str, object],
    quote_payload: Mapping[str, object],
    best_bid: float | None,
    best_ask: float | None,
    executable_snapshot: DecisionCertificate,
    canary_force_taker: bool = False,
    canary_edge_floor: float | None = None,
) -> str:
    """Select MAKER/TAKER for the entry per design §1-§2 (governor + EV override).

    Authority order (Fitz #4 provenance):
      1. Canary knob (§7): when ``canary_force_taker`` and the post-cross edge
         clears the 5c floor, FORCE taker. This bypasses the governor's
         maker/taker CHOICE but never its NO_TRADE/risk gates (those gate the
         candidate upstream before this point and remain in force).
      2. Governor (§1): consult ``maker_or_taker`` when a global governor is
         configured. NO_TRADE is impossible here (the candidate already cleared
         the gates) but is mapped to MAKER (the conservative resting default).
      3. EV override (§2): even when the governor says MAKER, cross if the
         economic boundary ``e*(1-P_fill) >= s/2*(1+P_fill) + f - A`` holds.

    Defaults to MAKER (the pre-change passive law) whenever inputs are missing —
    a partial/uncertain signal must never silently produce a taker cross.
    """
    side = "BUY" if str(actionable_payload.get("direction")) in {"buy_yes", "buy_no"} else "SELL"
    reservation = _optional_float(actionable_payload.get("c_fee_adjusted"))

    # --- 1. Canary force-taker (with 5c post-cross edge floor) ---
    if canary_force_taker:
        floor = 0.05 if canary_edge_floor is None else float(canary_edge_floor)
        post_cross_edge = _post_cross_edge(
            actionable_payload=actionable_payload, best_bid=best_bid, best_ask=best_ask, side=side
        )
        if post_cross_edge is not None and post_cross_edge >= floor:
            return "TAKER"
        # Floor not met: fall through to governor/EV (do NOT force a sub-floor cross).

    # --- 2. Governor maker_or_taker ---
    governor_mode = _governor_mode_for_snapshot(executable_snapshot)
    if governor_mode == "TAKER":
        return "TAKER"

    # --- 3. Economic EV override (§2 boundary) ---
    if _ev_boundary_favors_cross(
        actionable_payload=actionable_payload,
        quote_payload=quote_payload,
        best_bid=best_bid,
        best_ask=best_ask,
        reservation=reservation,
        side=side,
    ):
        return "TAKER"
    return "MAKER"


def _governor_mode_for_snapshot(executable_snapshot: DecisionCertificate) -> str:
    """Return the global governor's maker/taker mode, or MAKER if unavailable.

    The candidate has already passed the upstream NO_TRADE/risk gates, so a
    NO_TRADE here (or an unconfigured governor) maps to the conservative MAKER
    resting default rather than blocking — the design routes order-TYPE only.
    """
    try:
        from src.risk_allocator import select_global_order_type

        order_type = select_global_order_type(executable_snapshot.payload)
    except Exception:
        return "MAKER"
    return "TAKER" if str(order_type).strip().upper() in {"FOK", "FAK"} else "MAKER"


def _post_cross_edge(
    *,
    actionable_payload: Mapping[str, object],
    best_bid: float | None,
    best_ask: float | None,
    side: str,
) -> float | None:
    """q_posterior - far_touch - fee  (the §7 canary edge floor numerator)."""
    q = _optional_float(actionable_payload.get("q_live"))
    fee = _optional_float(actionable_payload.get("fee_rate")) or 0.0
    if q is None:
        return None
    if side == "BUY":
        if best_ask is None:
            return None
        return q - best_ask - fee
    if best_bid is None:
        return None
    return best_bid - q - fee


def _ev_boundary_favors_cross(
    *,
    actionable_payload: Mapping[str, object],
    quote_payload: Mapping[str, object],
    best_bid: float | None,
    best_ask: float | None,
    reservation: float | None,
    side: str,
) -> bool:
    """§2 boundary: cross iff e*(1-P_fill) >= s/2*(1+P_fill) + f - A.

    Conservative: returns False (rest as maker) on any missing input.
    """
    e = _optional_float(actionable_payload.get("trade_score"))
    if e is None:
        e = _optional_float(actionable_payload.get("q_live"))
        c = _optional_float(actionable_payload.get("c_fee_adjusted"))
        if e is not None and c is not None:
            e = (e - c) if side == "BUY" else (c - e)
    if e is None or best_bid is None or best_ask is None:
        return False
    spread = max(0.0, best_ask - best_bid)
    p_fill = _optional_float(actionable_payload.get("p_fill_lcb"))
    if p_fill is None:
        return False
    p_fill = max(0.0, min(1.0, p_fill))
    fee = _optional_float(actionable_payload.get("fee_rate")) or 0.0
    adverse = _adverse_selection_proxy(actionable_payload=actionable_payload, spread_usd=spread)
    a = float(adverse) if adverse is not None else 0.0
    lhs = e * (1.0 - p_fill)
    rhs = (spread / 2.0) * (1.0 + p_fill) + fee - a
    return lhs >= rhs


def _release_live_cap_certificate(
    live_cap: DecisionCertificate,
    live_cap_conn: sqlite3.Connection | None,
    *,
    reason: str,
) -> None:
    if live_cap_conn is None:
        return
    from src.events.live_cap import LiveCapError, LiveCapLedger

    try:
        LiveCapLedger(live_cap_conn).release(str(live_cap.payload["usage_id"]), reason)
    except LiveCapError:
        return


def _required_cert(certs: tuple[DecisionCertificate, ...], certificate_type: str) -> DecisionCertificate:
    for cert in certs:
        if cert.certificate_type == certificate_type:
            return cert
    raise ValueError(f"missing required certificate: {certificate_type}")


def _require_snapshot_hash(snapshot: object) -> str:
    """Return executable_snapshot_hash from a hydrated snapshot; raise if absent."""
    if snapshot is None:
        raise ValueError("EXECUTABLE_SNAPSHOT_HASH_UNAVAILABLE: snapshot not found in trade DB")
    h = snapshot.executable_snapshot_hash  # type: ignore[union-attr]
    if not h:
        raise ValueError("EXECUTABLE_SNAPSHOT_HASH_UNAVAILABLE: hash is empty")
    return h


def _require_cost_basis(
    snapshot: object,
    *,
    direction: str,
    size_usd: float,
    execution_price: "ExecutionPrice",
) -> "ExecutableCostBasis":
    """Build canonical ExecutableCostBasis from a hydrated snapshot.

    Uses the fee-adjusted execution_price.value as final_limit_price /
    expected_fill_price_before_fee — for the no-submit passive path the limit
    price IS the pre-fee ask (fee is added on top inside from_snapshot).
    We pass fee_adjusted_execution_price to let from_snapshot verify consistency.

    Raises a clear COST_BASIS_HASH_UNAVAILABLE error on any failure so the cert
    pipeline fails closed rather than emitting a blank hash.
    """
    if snapshot is None:
        raise ValueError("COST_BASIS_HASH_UNAVAILABLE: snapshot not found in trade DB")
    try:
        # For the no-submit adapter path the limit is a passive post-only order.
        # execution_price.value is fee-adjusted; snapshot.orderbook_top_ask (for
        # buy) is the canonical pre-fee price used as limit/expected_fill.
        # Fall back to execution_price.value if top_ask/bid unavailable.
        snap = snapshot  # type: ignore[union-attr]
        # Strip selected_outcome_token_id / outcome_label so from_snapshot does not
        # raise a direction-mismatch when the snapshot row was fetched for the other
        # side of the same condition (the adapter reuses one row for both buy_yes and
        # buy_no proofs of the same condition).
        if snap.selected_outcome_token_id or snap.outcome_label:
            snap = dataclass_replace(snap, selected_outcome_token_id=None, outcome_label=None)  # type: ignore[arg-type]
        if direction.startswith("buy_"):
            pre_fee_limit = (
                snap.orderbook_top_ask
                if snap.orderbook_top_ask is not None
                else Decimal(str(execution_price.value))
            )
        else:
            pre_fee_limit = (
                snap.orderbook_top_bid
                if snap.orderbook_top_bid is not None
                else Decimal(str(execution_price.value))
            )
        pre_fee_limit = Decimal(str(pre_fee_limit))
        requested_size = Decimal(str(max(size_usd, 0.01)))
        return ExecutableCostBasis.from_snapshot(
            snapshot=snap,
            direction=direction,
            order_policy="post_only_passive_limit",
            requested_size_kind="notional_usd",
            requested_size_value=requested_size,
            final_limit_price=pre_fee_limit,
            expected_fill_price_before_fee=pre_fee_limit,
            depth_status="NOT_MARKETABLE_PASSIVE_LIMIT",
        )
    except Exception as exc:
        raise ValueError(f"COST_BASIS_HASH_UNAVAILABLE: {exc}") from exc


def _build_no_submit_proof_bundle_from_adapter_evidence(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    decision_time: datetime,
    family,
    family_topology_rows: list[dict[str, Any]],
    family_snapshot_rows: list[dict[str, Any]],
    selected_snapshot_row: dict[str, Any],
    trade_conn: sqlite3.Connection,
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
    topology_clock = _evidence_clock_from_rows(family_topology_rows)
    bin_labels_hash = stable_hash(tuple(str(candidate.bin.label) for candidate in family.candidates))
    bin_units = tuple(sorted({str(candidate.bin.unit) for candidate in family.candidates if candidate.bin.unit}))
    forecast_payload = {**forecast_payload, "bin_labels_hash": bin_labels_hash}
    market_analysis_config_hash = stable_hash(
        {
            "posterior_mode": MODEL_ONLY_POSTERIOR_MODE,
            "edge_bootstrap_n": edge_n_bootstrap(),
            "family_id": family.family_id,
        }
    )
    _hydrated_snapshot = get_snapshot(trade_conn, str(proof.executable_snapshot_id or ""))
    _canonical_cost_basis = _require_cost_basis(
        _hydrated_snapshot,
        direction=proof.direction,
        size_usd=kelly.size_usd,
        execution_price=execution_price,
    )
    # Align kelly_cost_basis_id in raw_receipt to the canonical cost_basis:{hash[:16]} form.
    # DecisionCompiler validates kelly.cost_basis_id == cost_model.cost_basis_id; both
    # must use the canonical form.
    raw_receipt["kelly_cost_basis_id"] = _canonical_cost_basis.cost_basis_id
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
                "source_status": forecast_payload.get("reader_status"),
                "source_authority_id": "read_executable_forecast",
                "source_reason_code": forecast_payload.get("reader_reason_code"),
                "derived_from_certificate_type": claims.FORECAST_AUTHORITY,
                "derived_from_snapshot_id": forecast_payload.get("snapshot_id"),
                "derived_from_reader_status": forecast_payload.get("reader_status"),
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
                "source_table": "market_events",
                "event_id": event.event_id,
            },
            topology_clock,
            "zeus.forecasts.market_events",
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
                "bin_units": bin_units,
                "metric": family.metric,
                "target_date": family.target_date,
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
                "calibrator_model_key": calibration_payload.get("calibrator_model_key"),
                "calibrator_model_hash": calibration_payload.get("model_hash"),
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
                "calibrator_model_hash": calibration_payload.get("model_hash"),
                "p_cal_vector_hash": proof.p_cal_vector_hash,
                "p_live_vector_hash": proof.p_live_vector_hash,
                "p_cal_hash": proof.p_cal_vector_hash,
                "p_live_hash": proof.p_live_vector_hash,
                "bin_labels_hash": bin_labels_hash,
                "members_json_hash": forecast_payload.get("members_json_hash"),
                "market_analysis_config_hash": market_analysis_config_hash,
                "bootstrap_n": edge_n_bootstrap(),
                "unit": forecast_payload.get("unit"),
                "unit_authority_source": forecast_payload.get("unit_authority_source"),
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
                "min_tick_size": str(_hydrated_snapshot.min_tick_size),
                "min_order_size": str(_hydrated_snapshot.min_order_size),
                "neg_risk": bool(_hydrated_snapshot.neg_risk),
                "captured_at": selected_snapshot_row.get("captured_at"),
                "freshness_deadline": selected_snapshot_row.get("freshness_deadline"),
                "active": selected_snapshot_row.get("active"),
                "closed": selected_snapshot_row.get("closed"),
                "executable_snapshot_hash": _require_snapshot_hash(_hydrated_snapshot),
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
                "cost_source": _native_cost_source_for_direction(proof.direction),
                "quote_source_kind": "executable_market_snapshot_native_book",
                "forbidden_cost_source": False,
                "selected_token_id": proof.token_id,
                # Top-of-book is the SAME causally-bound, freshness-gated selected_snapshot_row
                # that already passed entry gates and from which quote_clock
                # (source_available_at) is derived. The passive-maker consumer
                # (_passive_maker_context_from_authorities) requires best_bid/best_ask on this
                # cert; the production payload previously omitted them, so the live cert build
                # failed QUOTE_FEASIBILITY_BID_ASK_REQUIRED for every candidate. No quote
                # newer than decision_time and no relaxed staleness bound is introduced here.
                "best_bid": _optional_float(selected_snapshot_row.get("orderbook_top_bid")),
                "best_ask": _optional_float(selected_snapshot_row.get("orderbook_top_ask")),
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
                "identity": _canonical_cost_basis.cost_basis_id,
                "cost_basis_id": _canonical_cost_basis.cost_basis_id,
                "cost_basis_hash": _canonical_cost_basis.cost_basis_hash,
                "condition_id": raw_receipt.get("condition_id"),
                "token_id": raw_receipt.get("token_id"),
                "cost_source": _native_cost_source_for_direction(proof.direction),
                "quote_source_kind": "executable_market_snapshot_native_book",
                "forbidden_cost_source": False,
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
                "passed": kelly.passed,
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
    if not tuple(evidence.applied_validations):
        raise ValueError("FORECAST_AUTHORITY_VALIDATIONS_MISSING")
    unit = _snapshot_unit(snapshot, payload)
    city_config = runtime_cities_by_name().get(family.city)
    if city_config is None:
        raise ValueError(f"FORECAST_AUTHORITY_EVIDENCE_MISSING:city:{family.city}")
    members_json_hash = _snapshot_members_json_hash(snapshot)
    # horizon_profile is NOT a column on ensemble_snapshots and is not populated upstream
    # (forecast_calibration_domain.derive_phase2_keys_from_ens_result docstring). The calibrator
    # lookup DERIVES the horizon stratum from the forecast cycle (00/12 -> 'full', else 'short').
    # The forecast authority must carry that SAME derived value so the no-submit cert can enforce a
    # real calibration.horizon_profile == forecast.horizon_profile equality instead of silently
    # comparing a derived 'full' against a structural None (the live FORECAST horizon mismatch leak).
    from src.calibration.forecast_calibration_domain import derive_phase2_keys_from_ens_result

    _, _, derived_horizon_profile = derive_phase2_keys_from_ens_result(
        {
            "issue_time": _nonnull(
                evidence.source_issue_time
                or evidence.source_cycle_time
                or snapshot.get("source_issue_time")
                or snapshot.get("source_cycle_time")
                or payload.get("issue_time")
                or payload.get("source_cycle_time")
            ),
            "source_id": _nonnull(evidence.forecast_source_id or snapshot.get("source_id") or payload.get("source_id")),
            "horizon_profile": snapshot.get("horizon_profile"),
        }
    )
    payload_out = {
        "identity": str(result.bundle.snapshot.snapshot_id),
        "snapshot_id": str(result.bundle.snapshot.snapshot_id),
        "reader_authority": "read_executable_forecast",
        "reader_status": normalize_forecast_reader_status(result.status, result.reason_code) or result.status,
        "reader_reason_code": None if result.reason_code in {None, "", "OK", "LIVE_ELIGIBLE", "EXECUTABLE_FORECAST_READY"} else result.reason_code,
        "city": family.city,
        "target_date": family.target_date,
        "metric": family.metric,
        "temperature_metric": family.metric,
        "members_extrema_metric_identity": snapshot.get("temperature_metric"),
        "members_extrema_transform": _members_extrema_transform(family.metric),
        "members_json_source": "ensemble_snapshots.daily_extrema",
        "members_json_hash": members_json_hash,
        "target_local_date": family.target_date,
        "city_timezone": city_config.timezone,
        "settlement_unit": snapshot.get("settlement_unit"),
        "members_unit": snapshot.get("members_unit"),
        "unit": unit,
        "unit_authority_source": _snapshot_unit_authority_source(snapshot),
        "local_date_window_hash": stable_hash(
            {
                "city": snapshot.get("city"),
                "target_date": snapshot.get("target_date"),
                "temperature_metric": snapshot.get("temperature_metric"),
                "members_json_hash": members_json_hash,
                "local_day_start_utc": snapshot.get("local_day_start_utc"),
                "forecast_window_start_utc": snapshot.get("forecast_window_start_utc"),
                "forecast_window_end_utc": snapshot.get("forecast_window_end_utc"),
            }
        ),
        "forecast_source_id": evidence.forecast_source_id,
        "forecast_data_version": evidence.forecast_data_version,
        "source_transport": evidence.source_transport,
        "source_cycle_time": evidence.source_cycle_time,
        "source_issue_time": evidence.source_issue_time,
        "horizon_profile": _nonnull(snapshot.get("horizon_profile")) or derived_horizon_profile,
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
    native_costs = _native_costs_by_candidate_direction(family=family, snapshot_rows=snapshot_rows)
    (
        q_by_condition,
        q_lcb_by_direction,
        generated_p_values,
        generated_prefilter,
        probability_evidence,
    ) = _live_yes_probabilities(
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
            p_value = generated_p_values[(condition_id, direction)]
            passed_prefilter = bool(generated_prefilter.get((condition_id, direction), execution_price is not None and score > 0.0))
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
                    p_cal_vector_hash=str(probability_evidence["p_cal_vector_hash"]),
                    p_live_vector_hash=str(probability_evidence["p_live_vector_hash"]),
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
) -> tuple[
    dict[str, float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], bool],
    dict[str, str],
]:
    # 2026-05-30: canonical kernel reconstructed (snapshot fetch + MarketAnalysis assembly +
    # hypothesis-family scan + evaluate_live_bins). Gated by the acceptance suite in
    # tests/engine/test_event_reactor_no_bypass.py; SHADOW until #24 bias. See task Break-4.
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        return _canonical_probability_and_fdr_proof(
            event=event,
            family=family,
            conn=conn,
            calibration_conn=calibration_conn,
            native_costs=native_costs,
            decision_time=decision_time,
        )
    if event.event_type == "DAY0_EXTREME_UPDATED":
        generated = _canonical_probability_and_fdr_proof(
            event=event,
            family=family,
            conn=conn,
            calibration_conn=calibration_conn,
            native_costs=native_costs,
            allow_latest_snapshot=True,
            decision_time=decision_time,
        )
        q_by_condition, lcb_by_condition, p_values, prefilter, evidence = generated
        masked_q, masked_lcb = _apply_day0_mask_to_generated_probabilities(
            payload=payload,
            family=family,
            q_by_condition=q_by_condition,
            lcb_by_condition=lcb_by_condition,
        )
        return masked_q, masked_lcb, p_values, prefilter, {
            **evidence,
            "p_live_vector_hash": _probability_vector_hash(
                masked_q[str(candidate.condition_id or "")]
                for candidate in family.candidates
            ),
        }
    raise ValueError(f"unsupported EDLI event type for inference: {event.event_type}")


def _forecast_snapshot_probability_and_fdr_proof(
    *,
    event: OpportunityEvent,
    family,
    conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    decision_time: datetime,
    allow_latest_snapshot: bool = False,
) -> tuple[
    dict[str, float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], bool],
    dict[str, str],
]:
    """
    FAIL-CLOSED STUB — codex never authored the EDLI probability + FDR inference kernel.

    The full implementation requires authoring EDLI's live-money probability
    semantics (Platt p_cal lookup, hypothesis bootstrap, FDR proof construction)
    which is out-of-scope for rebase-resolution. Until codex provides the
    canonical implementation, this stub returns empty mappings so:

      1. Module imports succeed (event reactor tests pass)
      2. Any production path reaching this function admits NO candidates
         (q_by_condition empty → no executable proofs → no_submit decision)
      3. Evidence dict explicitly documents the gap for downstream audit

    Returns an empty inference result. Do not "fill in" the empty dicts with
    placeholder probabilities — that would silently mis-trade.
    """
    q_by_condition: dict[str, float] = {}
    q_lcb_by_direction: dict[tuple[str, str], float] = {}
    generated_p_values: dict[tuple[str, str], float] = {}
    generated_prefilter: dict[tuple[str, str], bool] = {}
    probability_evidence: dict[str, str] = {
        "status": "no_submit_fail_closed",
        "reason": "edli_probability_kernel_unauthored",
        "TODO": "codex must implement _forecast_snapshot_probability_and_fdr_proof per EDLI v1 spec",
        "event_type": event.event_type,
        "allow_latest_snapshot": str(allow_latest_snapshot),
        "decision_time": decision_time.isoformat(),
    }
    return q_by_condition, q_lcb_by_direction, generated_p_values, generated_prefilter, probability_evidence


def _canonical_probability_and_fdr_proof(
    *,
    event: OpportunityEvent,
    family,
    conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    decision_time: datetime,
    allow_latest_snapshot: bool = False,
) -> tuple[
    dict[str, float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], bool],
    dict[str, str],
]:
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
    from src.strategy.market_analysis_family_scan import scan_full_hypothesis_family
    from src.config import edge_n_bootstrap

    hypotheses = scan_full_hypothesis_family(analysis, n_bootstrap=edge_n_bootstrap())
    hypothesis_by_label_direction = {
        (hypothesis.range_label, hypothesis.direction): hypothesis
        for hypothesis in hypotheses
    }
    q_by_condition: dict[str, float] = {}
    lcb_by_direction: dict[tuple[str, str], float] = {}
    p_values: dict[tuple[str, str], float] = {}
    prefilter: dict[tuple[str, str], bool] = {}
    # Live FDR truth comes from the family hypothesis scan above (the same
    # scan_full_hypothesis_family / FullFamilyHypothesis the legacy evaluator uses),
    # keyed by (range_label, direction). Each hypothesis carries p_posterior (calibrated
    # forecast probability), bootstrap p_value, ci_lower, and prefilter. We read those
    # directly — no DB selection-fact round-trip — fail-closed if any bin/direction is absent.
    p_market_yes_vec = np.asarray(analysis.p_market, dtype=float)
    p_market_no_vec = np.asarray(analysis.p_market_no, dtype=float)
    p_posterior_vec = np.asarray(analysis.p_posterior, dtype=float)
    for index, candidate in enumerate(family.candidates):
        condition_id = str(candidate.condition_id or "")
        range_label = candidate.bin.label
        # q-posterior is defined for EVERY bin from the calibrated forecast (market-independent),
        # so the full MECE family prior is always complete even for bins with no executable quote.
        yes_posterior = float(p_posterior_vec[index])
        q_by_condition[condition_id] = yes_posterior
        # The hypothesis bootstrap returns the EDGE CI (percentile of p_posterior - c_b). The
        # robust trade score consumes q's LOWER bound in probability space (it subtracts the cost
        # itself). Because c_b is fixed in the bootstrap, percentile(p_post - c_b) =
        # percentile(p_post) - c_b, so q_lcb = edge_lcb + c_b. Restore probability space here; the
        # FDR keeps using edge-space p_value + prefilter (which already encode edge_lcb>0).
        cost_by_direction = {
            "buy_yes": float(p_market_yes_vec[index]),
            "buy_no": float(p_market_no_vec[index]),
        }
        for direction in ("buy_yes", "buy_no"):
            hyp = hypothesis_by_label_direction.get((range_label, direction))
            if hyp is not None and hyp.p_value is not None and hyp.ci_lower is not None:
                p_values[(condition_id, direction)] = float(hyp.p_value)
                lcb_by_direction[(condition_id, direction)] = float(hyp.ci_lower) + cost_by_direction[direction]
                prefilter[(condition_id, direction)] = bool(hyp.passed_prefilter)
            else:
                # scan_full_hypothesis_family omits a direction when that side has no executable
                # market (bin skipped entirely if YES non-executable; buy_no omitted if NO side
                # non-executable). Emit neutral, non-actionable values: the direction is then
                # rejected downstream by the missing native execution price
                # (EXECUTABLE_NATIVE_ASK_MISSING), not by a family-level fail-closed raise.
                q_point = yes_posterior if direction == "buy_yes" else (1.0 - yes_posterior)
                p_values[(condition_id, direction)] = 1.0
                lcb_by_direction[(condition_id, direction)] = q_point
                prefilter[(condition_id, direction)] = False
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
    probability_evidence = {
        "p_cal_vector_hash": _probability_vector_hash(float(value) for value in analysis.p_cal),
        "p_live_vector_hash": _probability_vector_hash(
            q_by_condition[str(candidate.condition_id or "")]
            for candidate in family.candidates
        ),
    }
    # P1 (continuous re-decision): cache this family's belief (q-posterior per bin) so the periodic
    # re-decision scan can cheap-screen it against fresh prices WITHOUT re-running this kernel between
    # forecast cycles. Best-effort + double-guarded — a cache hiccup must never break the decision.
    # DISABLED 2026-05-31: persist_belief_live opened a SECOND world connection and
    # INSERT+committed probability_trace_fact WHILE this kernel runs inside the reactor's
    # OWN world write-transaction (process_pending's per-event SAVEPOINT) → SQLite
    # self-deadlock that HUNG every event in process_pending (faulthandler-pinned:
    # continuous_redecision.cache_belief:124). The surrounding try/except could not catch
    # it because it HANGS, not raises. The belief cache is currently write-only — no live
    # reader (enqueue_live_redecisions/screen_exit are unwired dead code per the 2026-05-31
    # audit) — so skipping the write is safe and is the unlock for the first receipt.
    # Re-enable under plan A2 by writing the belief through the reactor's EXISTING
    # connection (same transaction), never a fresh get_world_connection().
    return q_by_condition, lcb_by_direction, p_values, prefilter, probability_evidence


def _forecast_snapshot_row_for_event(
    conn: sqlite3.Connection,
    *,
    event: OpportunityEvent,
    family,
    allow_latest: bool,
    decision_time: datetime,
) -> dict[str, Any] | None:
    """Fetch the causal (or, for Day0, latest-available) ensemble_snapshots row for a family.

    ``allow_latest`` selects the latest available snapshot (Day0 base) rather than the exact
    causal snapshot bound by the event. Returns the row as a dict, or None if the authority
    table/columns are absent. Raises (fail-closed) if the forecast reader block-reason fires.
    """
    table_ref = _authority_table_ref(conn, "ensemble_snapshots")
    if table_ref is None:
        raise ValueError("ensemble_snapshots authority table missing for event-bound inference")
    columns = _table_ref_columns(conn, table_ref)
    required = {"city", "target_date", "temperature_metric", "snapshot_id"}
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
    if "authority" in columns:
        predicates.append("COALESCE(authority, 'VERIFIED') = 'VERIFIED'")
    if "causality_status" in columns:
        predicates.append("COALESCE(causality_status, 'OK') = 'OK'")
    if "boundary_ambiguous" in columns:
        predicates.append("COALESCE(boundary_ambiguous, 0) = 0")
    order_field = "available_at" if "available_at" in columns else "snapshot_id"
    cur = conn.execute(
        f"""
        SELECT *
        FROM {table_ref}
        WHERE {' AND '.join(predicates)}
        ORDER BY {order_field} DESC
        """,
        tuple(params),
    )
    row = cur.fetchone()
    if row is None:
        return None
    names = [description[0] for description in cur.description]
    snapshot = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
    reason, elected_snapshot_id = _forecast_snapshot_reader_block_reason(
        conn,
        snapshot=snapshot,
        event=event,
        family=family,
        allow_latest=allow_latest,
        decision_time=decision_time,
    )
    if reason is not None:
        raise ValueError(reason)
    # Compute inference on the reader-ELECTED executable snapshot (the single forecast
    # authority), not the causal-pinned seed. The causal snapshot triggers the event but its
    # source_run may still be re-ingesting members (captured_at advances past the decision
    # moment), so the reader's causality gate legitimately drops it and elects the freshest
    # fully-captured FULL_CONTRIBUTOR (often an earlier cycle). Returning that row — instead of
    # asserting reader==causal — dissolves the permanent FORECAST_READER_SNAPSHOT_MISMATCH leak.
    # causal_snapshot_id stays as event provenance.
    if elected_snapshot_id is not None and _nonnull(snapshot.get("snapshot_id")) != _nonnull(elected_snapshot_id):
        cur = conn.execute(
            f"SELECT * FROM {table_ref} WHERE CAST(snapshot_id AS TEXT) = ?",
            (str(elected_snapshot_id),),
        )
        elected_row = cur.fetchone()
        if elected_row is not None:
            names = [description[0] for description in cur.description]
            return (
                {name: elected_row[name] for name in names}
                if isinstance(elected_row, sqlite3.Row)
                else dict(zip(names, elected_row))
            )
    return snapshot


def _market_analysis_from_event_snapshot(
    *,
    calibration_conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    family,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    payload: dict[str, object],
    decision_time: datetime | None,
) -> MarketAnalysis:
    from src.strategy.market_analysis import MarketAnalysis
    from src.config import settings

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


def _snapshot_members(snapshot: dict[str, Any]) -> np.ndarray:
    members = _json_list(snapshot.get("members_json"))
    values = np.asarray([float(item) for item in members if item is not None], dtype=float)
    if values.ndim != 1 or values.size == 0 or not np.isfinite(values).all():
        raise ValueError("causal forecast snapshot members_json invalid")
    return values


def _snapshot_members_json_hash(snapshot: dict[str, Any]) -> str:
    return _probability_vector_hash(_snapshot_members(snapshot))


_EDLI_BIAS_FAMILY = "edli_per_city_v1"


def _maybe_bias_decay_kelly_haircut(
    kelly_multiplier: float,
    *,
    family,
) -> tuple[float, bool, float | None, str]:
    """INTERIM (data-insufficient phase) pre-submit Kelly haircut on high-bias cities.

    Operator directive 2026-05-31: if the per-city forecast bias magnitude exceeds the
    unit-aware threshold (edli_v1.bias_decay_threshold_c for C-settled cities,
    bias_decay_threshold_f for F-settled SF/Seattle), multiply the Kelly multiplier by
    bias_decay_kelly_factor (0.5 = halve). Sizes DOWN cities whose forecast we cannot yet
    trust enough to fully correct (corrected-#24 showed a full p_raw correction worsens
    the live gate -> edge-reversal risk). Does NOT shift p_raw.

    Bias source: model_bias_ens.effective_bias_c (edli_per_city_v1, VERIFIED). The stored
    bias is degC; for F-settled cities compare |eff_c * 1.8| to the F threshold.
    FAIL-SAFE: no VERIFIED bias row (data absent = the data-insufficient trigger) -> apply
    the haircut + WARN. FAIL-OPEN on UNEXPECTED ERROR only: any exception -> NO haircut +
    WARN (never crash or zero a live size). Flag-gated: edli_v1.bias_decay_kelly_haircut_enabled.
    """
    try:
        ev = settings["edli_v1"]
        if not bool(ev.get("bias_decay_kelly_haircut_enabled", False)):
            return kelly_multiplier, False, None, "disabled"
        import contextlib
        import logging
        from src.calibration.manager import season_from_date
        from src.calibration.ens_bias_repo import read_bias_model
        from src.state.db import get_world_connection

        city = runtime_cities_by_name().get(family.city)
        if city is None:
            return kelly_multiplier, False, None, "no_city"
        unit = getattr(city, "settlement_unit", "C")
        metric = family.metric
        ldv = (
            "ecmwf_opendata_mx2t3_local_calendar_day_max"
            if metric == "high"
            else "ecmwf_opendata_mn2t3_local_calendar_day_min"
        )
        season = season_from_date(str(family.target_date), lat=city.lat)
        month = int(str(family.target_date)[5:7])
        eff_c = None
        with contextlib.closing(get_world_connection()) as conn:
            conn.row_factory = sqlite3.Row
            row = read_bias_model(
                conn,
                city=city.name,
                season=season,
                metric=metric,
                live_data_version=ldv,
                month=month,
                target_month=month,
                authority="VERIFIED",
                error_model_family=_EDLI_BIAS_FAMILY,
            )
        if row is not None:
            try:
                eff_c = float(row["effective_bias_c"])
            except Exception:
                eff_c = None
        factor = float(ev.get("bias_decay_kelly_factor", 0.5))
        if eff_c is None:
            logging.getLogger("zeus.edli_bias").warning(
                "bias-decay haircut APPLIED (fail-safe: no VERIFIED bias row) city=%s metric=%s factor=%.2f",
                family.city, metric, factor,
            )
            return kelly_multiplier * factor, True, None, "no_bias_row_conservative"
        if unit == "F":
            bias_native = eff_c * 1.8
            thr = float(ev.get("bias_decay_threshold_f", 3.0))
        else:
            bias_native = eff_c
            thr = float(ev.get("bias_decay_threshold_c", 2.0))
        if abs(bias_native) > thr:
            logging.getLogger("zeus.edli_bias").info(
                "bias-decay haircut APPLIED city=%s unit=%s bias_native=%.2f thr=%.2f factor=%.2f",
                family.city, unit, bias_native, thr, factor,
            )
            return kelly_multiplier * factor, True, bias_native, "bias_exceeds"
        return kelly_multiplier, False, bias_native, "within_threshold"
    except Exception as exc:  # fail-OPEN on unexpected error: never crash/zero a live size
        try:
            import logging
            logging.getLogger("zeus.edli_bias").warning(
                "bias-decay haircut SKIPPED (fail-open on error, no haircut): %s", exc
            )
        except Exception:
            pass
        return kelly_multiplier, False, None, "error_fail_open"


def _maybe_apply_edli_bias_correction(
    members: np.ndarray,
    *,
    snapshot: dict[str, Any],
    family,
    city,
    payload: dict[str, object],
) -> tuple[np.ndarray, bool]:
    """A4 per-city promoted bias correction for the LIVE EDLI p_raw path.

    Subtracts the promoted ``model_bias_ens.effective_bias_c`` (per city x season x
    metric x live_data_version, authority='VERIFIED', error_model_family='edli_per_city_v1',
    weight_live>0) from the member maxes BEFORE p_raw is computed. The bias sign
    convention is ``effective_bias_c = mean(forecast - observed)`` so subtracting it
    de-biases toward observed truth (cold forecast => negative bias_c => members warmed).

    Flag-gated by ``edli_v1.edli_bias_correction_enabled`` (default OFF: prepared, not
    active). FAIL-CLOSED: any missing flag/row/field or error returns the raw members
    with applied=False, so the live path never breaks and never applies an unverified
    correction. When applied, the caller marks payload['_edli_bias_corrected']=True so
    the calibration step uses identity Platt for the corrected p_raw domain (train/serve
    lockstep — calibration_pairs were fit on uncorrected p_raw).
    """
    try:
        if not bool(settings["edli_v1"].get("edli_bias_correction_enabled", False)):
            return members, False
        import contextlib
        from src.calibration.manager import season_from_date
        from src.calibration.ens_bias_repo import read_bias_model
        from src.state.db import get_world_connection

        ldv = _nonnull(
            snapshot.get("dataset_id")
            or snapshot.get("data_version")
            or payload.get("dataset_id")
        )
        if not ldv:
            return members, False
        season = season_from_date(str(family.target_date), lat=city.lat)
        _tmonth = int(str(family.target_date)[5:7])
        with contextlib.closing(get_world_connection()) as conn:
            row = read_bias_model(
                conn,
                city=city.name,
                season=season,
                metric=family.metric,
                live_data_version=str(ldv),
                month=_tmonth,
                target_month=_tmonth,
                authority="VERIFIED",
                error_model_family=_EDLI_BIAS_FAMILY,
            )
        if row is None:
            return members, False
        keys = set(row.keys())
        eff = row["effective_bias_c"] if "effective_bias_c" in keys else None
        wl = row["weight_live"] if "weight_live" in keys else 0.0
        if eff is None or float(wl or 0.0) <= 0.0:
            return members, False
        # UNIT FIX (2026-05-31): effective_bias_c is degC; members carry the city's
        # SETTLEMENT unit. SF/Seattle settle degF, so a degC bias must be converted to
        # degF (x1.8) before subtracting — else F-cities are under-corrected 1.8x.
        # Validated by settled-truth backtest (SF bin_bias<=1 8%->65% with unit-correct form).
        _unit = getattr(city, "settlement_unit", "C")
        eff_native = float(eff) * 1.8 if _unit == "F" else float(eff)
        corrected = np.asarray(members, dtype=float) - eff_native
        import logging
        logging.getLogger("zeus.edli_bias").info(
            "EDLI bias correction applied city=%s season=%s metric=%s unit=%s eff_bias_c=%.3f eff_native=%.3f",
            city.name, season, family.metric, _unit, float(eff), eff_native,
        )
        return corrected, True
    except Exception as exc:  # fail-closed: never break the live decision path
        try:
            import logging
            logging.getLogger("zeus.edli_bias").warning(
                "EDLI bias correction skipped (fail-closed): %s", exc
            )
        except Exception:
            pass
        return members, False


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
    _snapshot_unit(snapshot, payload)
    _validate_snapshot_members_metric_identity(snapshot=snapshot, family=family, payload=payload)
    semantics = SettlementSemantics.for_city(city)
    # A4 (2026-05-31): per-city promoted bias correction on member maxes BEFORE p_raw.
    # Flag-gated (edli_v1.edli_bias_correction_enabled, default OFF) + FAIL-CLOSED.
    members, _bias_corrected = _maybe_apply_edli_bias_correction(
        members, snapshot=snapshot, family=family, city=city, payload=payload
    )
    if _bias_corrected:
        payload["_edli_bias_corrected"] = True
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

    # A4 lockstep: when the member maxes were bias-corrected pre-p_raw, the existing
    # Platt models were fit on UNCORRECTED p_raw and would mis-calibrate the shifted
    # domain. Use identity Platt (p_cal = normalized p_raw) for the corrected domain
    # until a Platt is refit on the corrected p_raw_domain. Enforces train/serve match.
    if bool(payload.get("_edli_bias_corrected")):
        arr = np.asarray(p_raw, dtype=float)
        total = float(arr.sum())
        if not _valid_probability_vector(arr, len(bins)) or total <= 0.0:
            raise ValueError("CALIBRATION_AUTHORITY_MISSING:bias-corrected p_raw invalid")
        return arr / total

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


def _probability_vector_hash(values) -> str:
    return stable_hash(tuple(round(float(value), 12) for value in values))


def _snapshot_unit(snapshot: dict[str, Any], payload: dict[str, object]) -> str:
    unit = _nonnull(snapshot.get("settlement_unit") or snapshot.get("unit"))
    if unit in {"F", "C"}:
        return unit
    members_unit = _nonnull(snapshot.get("members_unit"))
    if members_unit == "degC":
        return "C"
    if members_unit == "degF":
        return "F"
    raise ValueError("FORECAST_UNIT_AUTHORITY_MISSING")


def _snapshot_unit_authority_source(snapshot: dict[str, Any]) -> str:
    if _nonnull(snapshot.get("settlement_unit") or snapshot.get("unit")):
        return "ensemble_snapshots.settlement_unit"
    if _nonnull(snapshot.get("members_unit")):
        return "ensemble_snapshots.members_unit"
    raise ValueError("FORECAST_UNIT_AUTHORITY_MISSING")


def _validate_snapshot_members_metric_identity(*, snapshot: dict[str, Any], family, payload: dict[str, object]) -> None:
    snapshot_metric = _nonnull(snapshot.get("temperature_metric") or snapshot.get("members_extrema_metric_identity"))
    family_metric = _nonnull(getattr(family, "metric", None) or payload.get("metric") or payload.get("temperature_metric"))
    if not snapshot_metric or not family_metric:
        raise ValueError("FORECAST_MEMBERS_METRIC_IDENTITY_MISSING")
    if snapshot_metric != family_metric:
        raise ValueError("FORECAST_MEMBERS_METRIC_IDENTITY_MISMATCH")


def _members_extrema_transform(metric: object) -> str:
    if metric == "high":
        return "daily_max"
    if metric == "low":
        return "daily_min"
    raise ValueError("FORECAST_MEMBERS_METRIC_IDENTITY_MISSING")


def _day0_absorbing_mask(*, payload: dict[str, object], family) -> "np.ndarray":
    """Absorbing-boundary mask over family bins for a Day0 observed extreme.

    A bin is zeroed when the observed rounded extreme already rules it out:
    for ``high`` the observed max exceeds the bin's upper edge; for ``low`` the observed
    min falls below the bin's lower edge. Shoulder bins (open-ended edge) are retained.
    """
    rounded = _optional_float(payload.get("rounded_value"))
    if rounded is None:
        raise ValueError("Day0 event missing rounded_value")
    metric = _nonnull(payload.get("metric") or payload.get("temperature_metric"))
    mask = np.ones(len(family.candidates), dtype=float)
    for index, candidate in enumerate(family.candidates):
        bin_value = candidate.bin
        if metric == "high":
            if bin_value.high is not None and rounded > float(bin_value.high):
                mask[index] = 0.0
        elif metric == "low":
            if bin_value.low is not None and rounded < float(bin_value.low):
                mask[index] = 0.0
        else:
            raise ValueError(f"unsupported Day0 metric: {metric}")
    return mask


def _apply_day0_mask_to_probability_vector(*, payload: dict[str, object], family, vector) -> "np.ndarray":
    """Apply the Day0 absorbing-boundary mask to a probability vector and renormalize.

    Used pre-inference on p_raw / p_cal so the calibrated forecast respects the observed
    extreme before posterior + hypothesis construction. If the mask eliminates all support
    (degenerate observation) the unmasked vector is returned unchanged rather than dividing
    by zero — the downstream gates then reject on absent edge.
    """
    arr = np.asarray(vector, dtype=float)
    mask = _day0_absorbing_mask(payload=payload, family=family)
    masked = arr * mask
    total = float(masked.sum())
    if total <= 0.0:
        return arr
    return masked / total


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
        # BLOCKER #3 fix (day0 critic 2026-05-31): direct dict lookup raised KeyError
        # for any bin-direction with no executable market quote (common in day0 where
        # some bins are illiquid/delisted), propagating as LIVE_INFERENCE_INPUTS_MISSING
        # and killing the ENTIRE family (zero candidates) instead of skipping just the
        # non-executable direction. .get(...,0.0) → that direction gets no fill confidence
        # (min(0.0,·)=0.0 → not acceptable) while bins WITH quotes still proceed.
        yes_lcb = lcb_by_condition.get((condition_id, "buy_yes"), 0.0)
        no_lcb = lcb_by_condition.get((condition_id, "buy_no"), 0.0)
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
    require_fresh: bool = True,
) -> list[dict[str, Any]]:
    """Latest executable snapshot row per family condition_id.

    ``require_fresh`` controls whether the 30s PRICE-freshness window
    (``freshness_deadline``) is applied. The entry/FDR family-completeness gate proves
    MARKET IDENTITY (a snapshot row exists for every MECE sibling), which does not decay
    with price age — once a market is captured it does not "disappear". A full family is
    captured bin-by-bin and can span >30s, so applying the price window here would drop
    early-captured siblings and make large-family decisions structurally impossible. Callers
    proving identity pass ``require_fresh=False``; PRICE-freshness for the actually-traded
    selected bin is enforced at submission (``assert_snapshot_executable``). Operator design
    law 2026-05-30: "freshness 针对价格不针对市场; 市场捕捉了不会突然消失."
    """
    if not _table_exists(trade_conn, "executable_market_snapshots"):
        return []
    columns = _table_columns(trade_conn, "executable_market_snapshots")
    clean_condition_ids = tuple(condition_id for condition_id in condition_ids if condition_id)
    if not clean_condition_ids or "condition_id" not in columns:
        return []
    predicates: list[str] = []
    params: list[object] = []
    if require_fresh:
        predicates.append("freshness_deadline >= ?")
        params.append((fresh_at or datetime.now(UTC)).isoformat())
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


def _settlement_unit_for_payload_city(payload: dict[str, object]) -> str:
    """Authoritative settlement unit for the payload's city.

    The unit is CARRIED from the city settlement contract (``SettlementSemantics`` — the same
    authority p_raw uses), never inferred from the market label or blindly defaulted to 'F'.
    market_events has no unit column, so defaulting a missing payload unit to 'F' silently
    mislabelled every Celsius-city bin and failed closed on EVENT_BOUND_MARKET_TOPOLOGY_INVALID
    ('… is Celsius but unit=F'). Falls back to an explicit payload unit only when the city is
    unknown, then 'F'. The Bin label cross-check remains the fail-closed guard if config and
    market label ever disagree. Data-provenance law (Fitz #4): authority over default.
    """
    city_name = _nonnull(payload.get("city"))
    if city_name:
        try:
            from src.config import runtime_cities_by_name
            from src.contracts.settlement_semantics import SettlementSemantics

            city_obj = runtime_cities_by_name().get(city_name)
            if city_obj is not None:
                return SettlementSemantics.for_city(city_obj).measurement_unit
        except Exception:
            pass
    return _nonnull(payload.get("unit") or payload.get("temperature_unit") or "F")


def _bin_from_market_event(row: dict[str, Any], payload: dict[str, object]) -> Bin:
    label = _nonnull(row.get("range_label") or row.get("outcome") or payload.get("bin_label") or payload.get("outcome_label"))
    low = row.get("range_low")
    high = row.get("range_high")
    unit = _settlement_unit_for_payload_city(payload)
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
    from src.contracts.executable_market_snapshot import fee_rate_fraction_from_details
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
        # No-submit/cached path must NEVER live-fetch the wallet (contract:
        # tests/engine/test_event_reactor_no_bypass.py::
        # test_no_submit_default_bankroll_path_does_not_live_fetch_wallet). A cold cache fails
        # CLOSED → KELLY_PROOF_MISSING. Reliability is the cycle-warm's responsibility:
        # _edli_event_reactor_cycle calls bankroll_provider.current() once per reactor cycle to
        # populate cached(); the prior self-heal that called current() here re-introduced a
        # per-decision live wallet fetch and is removed (#45).
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


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_default(value: object, default: float) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


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


def _native_costs_by_candidate_direction(
    family: Any,
    snapshot_rows: list[dict[str, Any]],
) -> dict[tuple[str, str], tuple[dict[str, Any] | None, "ExecutionPrice | None", float, float | None, str | None]]:
    """Return cost tuple per (condition_id, direction) for all candidates × buy directions.

    Value tuple: (quote_book_dict, execution_price, max_size_at_price, slippage_bps, source_kind)
    Only index [1] (ExecutionPrice) is consumed by downstream callers.
    """
    rows_by_condition = _snapshot_rows_by_condition(snapshot_rows)
    result: dict[tuple[str, str], tuple[dict[str, Any] | None, Any, float, float | None, str | None]] = {}
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        if not condition_id:
            continue
        row = rows_by_condition.get(condition_id)
        for token_id, direction in (
            (str(candidate.yes_token_id or ""), "buy_yes"),
            (str(candidate.no_token_id or ""), "buy_no"),
        ):
            source_kind = _native_cost_source_for_direction(direction)
            if row is None or not token_id:
                result[(condition_id, direction)] = (None, None, 0.0, None, source_kind)
                continue
            try:
                execution_price, _p_fill, _c95 = _execution_price_from_snapshot(
                    row, selected_token_id=token_id, direction=direction
                )
                book = _native_quote_book_from_snapshot_row(row)
                max_size = float(book.min_order_size)
            except Exception:
                result[(condition_id, direction)] = (None, None, 0.0, None, source_kind)
                continue
            result[(condition_id, direction)] = (None, execution_price, max_size, None, source_kind)
    return result


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


def _native_cost_source_for_direction(direction: str | None) -> str | None:
    if direction in {"buy_yes", "buy_no"}:
        return "native_orderbook_ask"
    if direction in {"sell_yes", "sell_no"}:
        return "native_orderbook_bid"
    return None


def _calibration_model_row(conn: sqlite3.Connection, *, model_key: object) -> dict[str, Any] | None:
    if not model_key or not _table_exists(conn, "platt_models"):
        return None
    cur = conn.execute("SELECT * FROM platt_models WHERE model_key = ? LIMIT 1", (str(model_key),))
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


def _evidence_clock_from_rows(rows: list[dict[str, Any]]) -> EvidenceClock:
    if not rows:
        raise ValueError("TOPOLOGY_CLOCK_MISSING")
    clocks = [_evidence_clock_from_topology_row(row) for row in rows]
    return EvidenceClock(
        source_available_at=max(clock.source_available_at for clock in clocks),
        agent_received_at=max(clock.agent_received_at for clock in clocks),
        persisted_at=max(clock.persisted_at for clock in clocks),
    )


def _evidence_clock_from_topology_row(row: dict[str, Any]) -> EvidenceClock:
    source_time = _first_parseable_utc(
        row,
        ("discovered_at", "captured_at", "available_at", "gamma_updated_at", "created_at"),
    )
    agent_time = _first_parseable_utc(
        row,
        ("received_at", "scanned_at", "captured_at", "created_at"),
    )
    persisted_time = _first_parseable_utc(
        row,
        ("persisted_at", "updated_at", "created_at"),
    )
    if source_time is None or agent_time is None or persisted_time is None:
        raise ValueError("TOPOLOGY_CLOCK_MISSING")
    return EvidenceClock(source_time, agent_time, persisted_time)


def _first_parseable_utc(row: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    for key in keys:
        if row.get(key) in (None, ""):
            continue
        parsed = _parse_utc(row.get(key))
        if parsed is not None:
            return parsed
    return None


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
) -> tuple[str | None, str | None]:
    """Return ``(reason, elected_snapshot_id)`` — see _executable_forecast_reader_authority_block_reason."""
    if event.event_type not in {"FORECAST_SNAPSHOT_READY", "DAY0_EXTREME_UPDATED"}:
        return None, None
    source_run_id = _nonnull(snapshot.get("source_run_id") or _payload(event).get("source_run_id"))
    if not source_run_id:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_id_missing", None
    source_run_table = _authority_table_ref(conn, "source_run")
    coverage_table = _authority_table_ref(conn, "source_run_coverage")
    if source_run_table is None or coverage_table is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_authority_missing", None
    source_run = _row_by_id(conn, source_run_table, "source_run_id", source_run_id)
    if source_run is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_missing", None
    coverage = _coverage_row_for_snapshot(
        conn,
        coverage_table,
        source_run_id=source_run_id,
        family=family,
        snapshot=snapshot,
    )
    if coverage is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:coverage_missing", None
    return _executable_forecast_reader_authority_block_reason(
        conn,
        snapshot=snapshot,
        source_run=source_run,
        coverage=coverage,
        event=event,
        family=family,
        allow_latest=allow_latest,
        decision_time=decision_time,
    )


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
) -> tuple[str | None, str | None]:
    """Revalidate forecast eligibility through the canonical executable reader.

    Returns ``(reason, elected_snapshot_id)``. On success ``reason`` is None and
    ``elected_snapshot_id`` is the snapshot the canonical reader ELECTS as the
    executable forecast for this scope — which may differ from the event's causal
    (trigger) snapshot when the causal cycle's source_run is still re-ingesting
    members (captured_at advances past the decision moment) and the reader's
    causality gate drops it in favour of the freshest fully-captured
    FULL_CONTRIBUTOR. The caller computes inference on the elected row;
    causal_snapshot_id remains provenance only. On block, ``reason`` is the
    BLOCKED reason code and elected id is None.
    """

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
            return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:scope_incomplete", None
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
        return f"FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:{exc}", None
    if not result.ok or result.bundle is None:
        return f"FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:{result.reason_code}", None
    # SINGLE SNAPSHOT AUTHORITY: honour the reader's elected executable snapshot rather than
    # asserting it equals the reactor's causal-pinned selection. The causal snapshot triggers
    # the event but its source_run may still be re-ingesting members (captured_at advances past
    # the decision moment), so the causality gate legitimately drops it and the reader elects
    # the freshest fully-captured FULL_CONTRIBUTOR (often an earlier cycle). The prior
    # assertion produced a permanent FORECAST_READER_SNAPSHOT_MISMATCH leak (decision_events=0)
    # whenever the causal cycle was still ingesting. Elected snapshot = executable authority;
    # causal_snapshot_id stays provenance only.
    return None, _nonnull(result.bundle.snapshot.snapshot_id)


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
    the quote gate. The family universe comes from market_events, not from the
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
                "SELECT 1 FROM forecasts.sqlite_master WHERE type='table' AND name='market_events'"
            ).fetchone()
            if exists is not None:
                return "forecasts.market_events"
    except Exception:
        pass
    if _table_exists(conn, "market_events"):
        return "market_events"
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
