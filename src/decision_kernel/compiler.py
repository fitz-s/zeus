"""EDLI no-submit decision compiler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from src.decision_kernel import claims
from src.decision_kernel.authority import DECISION_KERNEL_AUTHORITY_ID, DECISION_KERNEL_AUTHORITY_VERSION
from src.decision_kernel.certificate import DecisionCertificate, ParentEdge, build_certificate
from src.decision_kernel.certificates.no_submit import build_no_submit_decision_certificate
from src.decision_kernel.ledger import CompileFailure
from src.decision_kernel.modes import CertificateMode
from src.events.opportunity_event import OpportunityEvent

CompileStatus = Literal["VERIFIED", "REJECTED", "REVIEW_REQUIRED"]


@dataclass(frozen=True)
class NoSubmitCompileResult:
    status: CompileStatus
    no_submit_certificate: DecisionCertificate | None
    certificates: tuple[DecisionCertificate, ...]
    failures: tuple[CompileFailure, ...]


class DecisionCompiler:
    """Compile EDLI event/receipt evidence into a certificate DAG.

    The current PR332 adapter still constructs the typed no-submit receipt.
    This compiler demotes that receipt to projection input and emits verified
    certificate authority rows around it.
    """

    def compile_no_submit(
        self,
        event: OpportunityEvent,
        *,
        decision_time: datetime,
        mode: Literal["NO_SUBMIT", "REPLAY_COUNTERFACTUAL"] = "NO_SUBMIT",
        receipt: Any | None = None,
    ) -> NoSubmitCompileResult:
        decision_time = _utc(decision_time)
        if receipt is None:
            failure = CompileFailure(
                event_id=event.event_id,
                decision_time=decision_time,
                mode=mode,
                claim_type="no_submit_dry_run_decision",
                stage="NO_SUBMIT_COMPILER",
                reason_code="NO_SUBMIT_RECEIPT_REQUIRED_FOR_TRANSITION_COMPILER",
                reason_detail="PR332 transition compiler requires typed receipt projection input.",
            )
            return NoSubmitCompileResult("REJECTED", None, (), (failure,))
        if mode != "NO_SUBMIT":
            failure = CompileFailure(
                event_id=event.event_id,
                decision_time=decision_time,
                mode=mode,
                claim_type="no_submit_dry_run_decision",
                stage="CLOCK_MODE",
                reason_code="REPLAY_COUNTERFACTUAL_NOT_PROMOTABLE_TO_NO_SUBMIT",
            )
            return NoSubmitCompileResult("REJECTED", None, (), (failure,))

        source_available_at = _parse_dt(event.available_at)
        agent_received_at = _parse_dt(event.received_at)
        persisted_at = _parse_dt(event.created_at)
        if persisted_at > decision_time:
            persisted_at = agent_received_at
        common_times = {
            "source_available_at": source_available_at,
            "agent_received_at": agent_received_at,
            "persisted_at": persisted_at,
        }

        clock = build_certificate(
            certificate_type=claims.CLOCK_MODE,
            semantic_key=f"clock:{event.event_id}:{decision_time.isoformat()}",
            claim_type="clock_mode",
            mode="NO_SUBMIT",
            decision_time=decision_time,
            payload={
                "mode": "NO_SUBMIT",
                "decision_time": decision_time,
                "clock_source": "reactor_decision_time",
                "agent_runtime_id": "edli_event_reactor",
                "live_persist_required": True,
            },
            authority_id=DECISION_KERNEL_AUTHORITY_ID,
            authority_version=DECISION_KERNEL_AUTHORITY_VERSION,
            algorithm_id="decision_kernel.clock",
            algorithm_version="v1",
            **common_times,
        )
        causal = build_certificate(
            certificate_type=claims.CAUSAL_EVENT,
            semantic_key=f"event:{event.event_id}",
            claim_type="causal_event",
            mode="NO_SUBMIT",
            decision_time=decision_time,
            payload={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "entity_key": event.entity_key,
                "source": event.source,
                "observed_at": event.observed_at,
                "available_at": event.available_at,
                "received_at": event.received_at,
                "causal_snapshot_id": event.causal_snapshot_id,
                "payload_hash": event.payload_hash,
            },
            authority_id="zeus.events.opportunity_event",
            authority_version="v1",
            algorithm_id="decision_kernel.causal_event",
            algorithm_version="v1",
            parent_edges=(edge("clock_mode", clock),),
            parent_certificates=(clock,),
            **common_times,
        )
        candidate = _simple_parent(
            claims.CANDIDATE_EVIDENCE,
            "candidate_evidence",
            event,
            receipt,
            decision_time,
            (clock, causal),
            common_times,
        )
        protocol = build_certificate(
            certificate_type=claims.TESTING_PROTOCOL,
            semantic_key=f"testing_protocol:{event.event_id}",
            claim_type="testing_protocol",
            mode="NO_SUBMIT",
            decision_time=decision_time,
            payload={
                "testing_protocol_id": getattr(receipt, "fdr_family_id", None) or f"event:{event.event_id}",
                "family_id": getattr(receipt, "fdr_family_id", None),
                "event_trigger_type": event.event_type,
                "look_index": 1,
                "max_looks": 1,
                "alpha_spending_rule": "FIXED_WINDOW_BH",
                "optional_stopping_valid": True,
                "sibling_hypothesis_count": getattr(receipt, "fdr_hypothesis_count", 0),
                "predeclared_at": event.available_at,
            },
            authority_id=DECISION_KERNEL_AUTHORITY_ID,
            authority_version=DECISION_KERNEL_AUTHORITY_VERSION,
            algorithm_id="decision_kernel.testing_protocol.transition",
            algorithm_version="v1",
            parent_edges=(edge("clock_mode", clock), edge("candidate_evidence", candidate)),
            parent_certificates=(clock, candidate),
            **common_times,
        )
        fdr = _simple_parent(claims.FDR, "fdr", event, receipt, decision_time, (protocol, candidate), common_times)
        kelly = _simple_parent(claims.KELLY_DRY_RUN, "kelly_dry_run", event, receipt, decision_time, (candidate,), common_times)
        risk = _simple_parent(claims.RISK_LEVEL, "risk_level", event, receipt, decision_time, (candidate,), common_times)
        no_submit_mode = _simple_parent(claims.NO_SUBMIT_MODE, "no_submit_mode", event, receipt, decision_time, (clock,), common_times)
        parents = (clock, causal, candidate, protocol, fdr, kelly, risk, no_submit_mode)
        no_submit = build_no_submit_decision_certificate(
            semantic_key=f"no_submit:{event.event_id}:{getattr(receipt, 'final_intent_id', '')}",
            decision_time=decision_time,
            parent_edges=(
                edge("clock_mode", clock),
                edge("causal_event", causal),
                edge("candidate_evidence", candidate),
                edge("testing_protocol", protocol),
                edge("fdr", fdr),
                edge("kelly_dry_run", kelly),
                edge("risk_level", risk),
                edge("no_submit_mode", no_submit_mode),
            ),
            parents=parents,
            payload={
                "event_id": event.event_id,
                "final_intent_id": getattr(receipt, "final_intent_id", None),
                "side_effect_status": getattr(receipt, "side_effect_status", None),
                "proof_accepted": bool(getattr(receipt, "proof_accepted", False)),
                "submitted": False,
                "quote_edge_bound": getattr(receipt, "trade_score", None),
                "conditional_edge_given_fill": getattr(receipt, "trade_score", None),
                "actionable_trade_score": 0.0,
                "no_submit_verified": True,
                "receipt_projection": _receipt_projection(receipt),
            },
            source_available_at=source_available_at,
            agent_received_at=agent_received_at,
            persisted_at=persisted_at,
        )
        return NoSubmitCompileResult("VERIFIED", no_submit, parents + (no_submit,), ())


def edge(role: str, cert: DecisionCertificate) -> ParentEdge:
    return ParentEdge(role=role, certificate_hash=cert.certificate_hash, certificate_type=cert.certificate_type)


def _simple_parent(
    certificate_type: str,
    claim_type: str,
    event: OpportunityEvent,
    receipt: Any,
    decision_time: datetime,
    parents: tuple[DecisionCertificate, ...],
    times: dict[str, datetime],
) -> DecisionCertificate:
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=f"{claim_type}:{event.event_id}:{getattr(receipt, 'final_intent_id', '')}",
        claim_type=claim_type,
        mode="NO_SUBMIT",
        decision_time=decision_time,
        payload={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "causal_snapshot_id": event.causal_snapshot_id,
            "receipt_projection": _receipt_projection(receipt),
        },
        authority_id=DECISION_KERNEL_AUTHORITY_ID,
        authority_version=DECISION_KERNEL_AUTHORITY_VERSION,
        algorithm_id=f"decision_kernel.{claim_type}.transition",
        algorithm_version="v1",
        parent_edges=tuple(edge(parent.certificate_type.lower(), parent) for parent in parents),
        parent_certificates=parents,
        **times,
    )


def _receipt_projection(receipt: Any) -> dict[str, Any]:
    fields = (
        "event_id",
        "causal_snapshot_id",
        "family_id",
        "candidate_id",
        "condition_id",
        "token_id",
        "direction",
        "q_live",
        "q_lcb_5pct",
        "c_fee_adjusted",
        "c_cost_95pct",
        "p_fill_lcb",
        "trade_score",
        "fdr_pass",
        "fdr_family_id",
        "fdr_hypothesis_count",
        "kelly_pass",
        "kelly_decision_id",
        "risk_decision_id",
        "final_intent_id",
        "side_effect_status",
        "proof_accepted",
    )
    return {field: getattr(receipt, field, None) for field in fields}


def _parse_dt(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
