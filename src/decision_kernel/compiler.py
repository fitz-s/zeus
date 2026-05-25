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
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.ledger import CompileFailure
from src.events.opportunity_event import OpportunityEvent

CompileStatus = Literal["VERIFIED", "REJECTED", "REVIEW_REQUIRED"]


@dataclass(frozen=True)
class EvidenceClock:
    source_available_at: datetime
    agent_received_at: datetime
    persisted_at: datetime


@dataclass(frozen=True)
class AuthorityEvidence:
    certificate_type: str
    claim_type: str
    semantic_suffix: str
    payload: dict[str, Any]
    clock: EvidenceClock
    authority_id: str
    authority_version: str = DECISION_KERNEL_AUTHORITY_VERSION
    algorithm_id: str = "decision_kernel.transition_evidence"
    algorithm_version: str = "v1"


@dataclass(frozen=True)
class NoSubmitProofBundle:
    """Typed authority evidence consumed by the no-submit compiler."""

    final_intent_id: str
    source_truth: AuthorityEvidence
    market_topology: AuthorityEvidence
    family_closure: AuthorityEvidence
    forecast_authority: AuthorityEvidence
    calibration: AuthorityEvidence
    model_config: AuthorityEvidence
    belief: AuthorityEvidence
    executable_snapshot: AuthorityEvidence
    quote_feasibility: AuthorityEvidence
    cost_model: AuthorityEvidence
    pre_trade_evidence: AuthorityEvidence
    candidate_evidence: AuthorityEvidence
    testing_protocol: AuthorityEvidence
    fdr: AuthorityEvidence
    kelly_dry_run: AuthorityEvidence
    risk_level: AuthorityEvidence
    no_submit_projection: dict[str, Any]


@dataclass(frozen=True)
class NoSubmitCompileResult:
    status: CompileStatus
    no_submit_certificate: DecisionCertificate | None
    certificates: tuple[DecisionCertificate, ...]
    failures: tuple[CompileFailure, ...]


class DecisionCompiler:
    """Compile typed no-submit authority evidence into a certificate DAG."""

    def compile_no_submit(
        self,
        event: OpportunityEvent,
        *,
        decision_time: datetime,
        mode: Literal["NO_SUBMIT", "REPLAY_COUNTERFACTUAL"] = "NO_SUBMIT",
        proof_bundle: NoSubmitProofBundle | None = None,
    ) -> NoSubmitCompileResult:
        decision_time = _utc(decision_time)
        if mode != "NO_SUBMIT":
            return self._rejected(
                event,
                decision_time=decision_time,
                mode=mode,
                stage="CLOCK_MODE",
                reason_code="REPLAY_COUNTERFACTUAL_NOT_PROMOTABLE_TO_NO_SUBMIT",
            )
        event_clock = self._event_clock(event)
        if event_clock.persisted_at > decision_time:
            return self._rejected(
                event,
                decision_time=decision_time,
                mode=mode,
                stage="CLOCK_MODE",
                reason_code="EVENT_PERSISTED_AFTER_DECISION_TIME",
                reason_detail=f"persisted_at={event_clock.persisted_at.isoformat()}",
            )
        if proof_bundle is None:
            return self._rejected(
                event,
                decision_time=decision_time,
                mode=mode,
                stage="NO_SUBMIT_COMPILER",
                reason_code="NO_SUBMIT_PROOF_BUNDLE_REQUIRED",
                reason_detail="Compiler requires typed authority evidence, not a receipt projection.",
            )

        clock = self._clock_certificate(event, decision_time=decision_time)
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
                "created_at": event.created_at,
                "causal_snapshot_id": event.causal_snapshot_id,
                "payload_hash": event.payload_hash,
            },
            authority_id="zeus.events.opportunity_event",
            authority_version="v1",
            algorithm_id="decision_kernel.causal_event",
            algorithm_version="v1",
            parent_edges=(edge("clock_mode", clock),),
            parent_certificates=(clock,),
            source_available_at=event_clock.source_available_at,
            agent_received_at=event_clock.agent_received_at,
            persisted_at=event_clock.persisted_at,
        )
        source_truth = self._authority_certificate(event, decision_time, proof_bundle.source_truth, (clock, causal))
        topology = self._authority_certificate(event, decision_time, proof_bundle.market_topology, (source_truth,))
        family = self._authority_certificate(event, decision_time, proof_bundle.family_closure, (topology,))
        forecast = self._authority_certificate(event, decision_time, proof_bundle.forecast_authority, (source_truth, family))
        calibration = self._authority_certificate(event, decision_time, proof_bundle.calibration, (forecast,))
        model_config = self._authority_certificate(event, decision_time, proof_bundle.model_config, (forecast, calibration))
        belief = self._authority_certificate(event, decision_time, proof_bundle.belief, (forecast, calibration, model_config, family))
        executable = self._authority_certificate(event, decision_time, proof_bundle.executable_snapshot, (topology,))
        quote = self._authority_certificate(event, decision_time, proof_bundle.quote_feasibility, (topology, executable))
        cost = self._authority_certificate(event, decision_time, proof_bundle.cost_model, (quote,))
        pre_trade = self._authority_certificate(event, decision_time, proof_bundle.pre_trade_evidence, (belief, quote, cost, source_truth))
        candidate = self._authority_certificate(event, decision_time, proof_bundle.candidate_evidence, (pre_trade, family))
        protocol = self._authority_certificate(event, decision_time, proof_bundle.testing_protocol, (family, candidate))
        fdr = self._authority_certificate(event, decision_time, proof_bundle.fdr, (protocol, candidate))
        kelly = self._authority_certificate(event, decision_time, proof_bundle.kelly_dry_run, (belief, quote, cost))
        risk = self._authority_certificate(event, decision_time, proof_bundle.risk_level, (candidate,))
        no_submit_mode = build_certificate(
            certificate_type=claims.NO_SUBMIT_MODE,
            semantic_key=f"no_submit_mode:{event.event_id}:{proof_bundle.final_intent_id}",
            claim_type="no_submit_mode",
            mode="NO_SUBMIT",
            decision_time=decision_time,
            payload={"event_id": event.event_id, "side_effect_status": "NO_SUBMIT"},
            authority_id=DECISION_KERNEL_AUTHORITY_ID,
            authority_version=DECISION_KERNEL_AUTHORITY_VERSION,
            algorithm_id="decision_kernel.no_submit_mode",
            algorithm_version="v1",
            parent_edges=(edge("clock_mode", clock),),
            parent_certificates=(clock,),
            source_available_at=decision_time,
            agent_received_at=decision_time,
            persisted_at=decision_time,
        )
        parents = (
            clock,
            causal,
            source_truth,
            topology,
            family,
            forecast,
            calibration,
            model_config,
            belief,
            executable,
            quote,
            cost,
            pre_trade,
            candidate,
            protocol,
            fdr,
            kelly,
            risk,
            no_submit_mode,
        )
        try:
            no_submit = build_no_submit_decision_certificate(
                semantic_key=f"no_submit:{event.event_id}:{proof_bundle.final_intent_id}",
                decision_time=decision_time,
                parent_edges=tuple(edge(_role_for(parent.certificate_type), parent) for parent in parents),
                parents=parents,
                payload={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "decision_source": "forecast" if event.event_type == "FORECAST_SNAPSHOT_READY" else "day0_or_other",
                    "final_intent_id": proof_bundle.final_intent_id,
                    "side_effect_status": "NO_SUBMIT",
                    "proof_accepted": bool(proof_bundle.no_submit_projection.get("proof_accepted")),
                    "submitted": False,
                    "quote_edge_bound": proof_bundle.pre_trade_evidence.payload.get("quote_edge_bound"),
                    "conditional_edge_given_fill": proof_bundle.pre_trade_evidence.payload.get("conditional_edge_given_fill"),
                    "actionable_trade_score": 0.0,
                    "no_submit_verified": True,
                    "projection_hash": proof_bundle.no_submit_projection.get("projection_hash"),
                },
                source_available_at=decision_time,
                agent_received_at=decision_time,
                persisted_at=decision_time,
            )
        except (CertificateVerificationError, ValueError) as exc:
            return self._rejected(
                event,
                decision_time=decision_time,
                mode=mode,
                stage="NO_SUBMIT_CERTIFICATE",
                reason_code="NO_SUBMIT_CERTIFICATE_REJECTED",
                reason_detail=str(exc),
            )
        return NoSubmitCompileResult("VERIFIED", no_submit, parents + (no_submit,), ())

    def _rejected(
        self,
        event: OpportunityEvent,
        *,
        decision_time: datetime,
        mode: str,
        stage: str,
        reason_code: str,
        reason_detail: str | None = None,
    ) -> NoSubmitCompileResult:
        failure = CompileFailure(
            event_id=event.event_id,
            decision_time=decision_time,
            mode=mode,
            claim_type="no_submit_dry_run_decision",
            stage=stage,
            reason_code=reason_code,
            reason_detail=reason_detail,
        )
        return NoSubmitCompileResult("REJECTED", None, (), (failure,))

    def _event_clock(self, event: OpportunityEvent) -> EvidenceClock:
        return EvidenceClock(
            source_available_at=_parse_dt(event.available_at),
            agent_received_at=_parse_dt(event.received_at),
            persisted_at=_parse_dt(event.created_at),
        )

    def _clock_certificate(self, event: OpportunityEvent, *, decision_time: datetime) -> DecisionCertificate:
        return build_certificate(
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
            source_available_at=decision_time,
            agent_received_at=decision_time,
            persisted_at=decision_time,
        )

    def _authority_certificate(
        self,
        event: OpportunityEvent,
        decision_time: datetime,
        evidence: AuthorityEvidence,
        parents: tuple[DecisionCertificate, ...],
    ) -> DecisionCertificate:
        return build_certificate(
            certificate_type=evidence.certificate_type,
            semantic_key=f"{evidence.semantic_suffix}:{event.event_id}:{evidence.payload.get('identity', '')}",
            claim_type=evidence.claim_type,
            mode="NO_SUBMIT",
            decision_time=decision_time,
            payload=evidence.payload,
            authority_id=evidence.authority_id,
            authority_version=evidence.authority_version,
            algorithm_id=evidence.algorithm_id,
            algorithm_version=evidence.algorithm_version,
            parent_edges=tuple(edge(_role_for(parent.certificate_type), parent) for parent in parents),
            parent_certificates=parents,
            source_available_at=evidence.clock.source_available_at,
            agent_received_at=evidence.clock.agent_received_at,
            persisted_at=evidence.clock.persisted_at,
        )


def build_transition_proof_bundle_from_receipt(
    event: OpportunityEvent,
    receipt: Any,
    *,
    decision_time: datetime,
) -> NoSubmitProofBundle:
    """Build typed authority evidence from the current PR332 receipt projection.

    This keeps the compiler API proof-bundle-first while the surrounding
    adapter is migrated to emit bundle evidence directly.
    """
    decision_time = _utc(decision_time)
    payload = _payload_dict(event)
    event_clock = EvidenceClock(
        source_available_at=_parse_dt(event.available_at),
        agent_received_at=_parse_dt(event.received_at),
        persisted_at=_parse_dt(event.created_at),
    )
    decision_clock = EvidenceClock(decision_time, decision_time, decision_time)
    quote_seen_at = _dt_from_payload(payload, "quote_seen_at") or decision_time
    quote_clock = EvidenceClock(
        source_available_at=quote_seen_at,
        agent_received_at=_dt_from_payload(payload, "quote_received_at") or decision_time,
        persisted_at=_dt_from_payload(payload, "quote_persisted_at") or decision_time,
    )
    calibration_clock = EvidenceClock(
        source_available_at=_dt_from_payload(payload, "calibration_available_at") or decision_time,
        agent_received_at=_dt_from_payload(payload, "calibration_received_at") or decision_time,
        persisted_at=_dt_from_payload(payload, "calibration_persisted_at") or decision_time,
    )
    projection = _receipt_projection(receipt)
    projection["projection_hash"] = _stable_projection_hash(projection)
    final_intent_id = str(getattr(receipt, "final_intent_id", "") or "")
    family_id = getattr(receipt, "family_id", None) or payload.get("family_id") or event.entity_key
    return NoSubmitProofBundle(
        final_intent_id=final_intent_id,
        source_truth=AuthorityEvidence(
            claims.SOURCE_TRUTH,
            "source_truth",
            "source_truth",
            {
                "identity": event.source,
                "event_source": event.source,
                "source_status": getattr(receipt, "source_status", None),
                "event_available_at": event.available_at,
                "event_received_at": event.received_at,
            },
            event_clock,
            "zeus.events.source_truth_gate",
            algorithm_id="decision_kernel.source_truth.from_event_gate",
        ),
        market_topology=AuthorityEvidence(
            claims.MARKET_TOPOLOGY,
            "market_topology",
            "market_topology",
            {
                "identity": family_id,
                "family_id": family_id,
                "condition_id": getattr(receipt, "condition_id", None),
                "token_id": getattr(receipt, "token_id", None),
                "city": getattr(receipt, "city", None),
                "target_date": getattr(receipt, "target_date", None),
                "metric": getattr(receipt, "metric", None),
            },
            event_clock,
            "zeus.forecasts.market_events_v2",
        ),
        family_closure=AuthorityEvidence(
            claims.FAMILY_CLOSURE,
            "family_closure",
            "family_closure",
            {
                "identity": family_id,
                "family_id": family_id,
                "family_complete": getattr(receipt, "family_complete", None),
                "fdr_hypothesis_count": getattr(receipt, "fdr_hypothesis_count", 0),
            },
            event_clock,
            "zeus.events.candidate_binding",
        ),
        forecast_authority=AuthorityEvidence(
            claims.FORECAST_AUTHORITY,
            "forecast_authority",
            "forecast_authority",
            {
                "identity": event.causal_snapshot_id,
                "snapshot_id": event.causal_snapshot_id,
                "reader": "canonical_executable_forecast_reader",
                "reader_status": "VERIFIED",
                "reader_reason_code": None,
                "applied_validations": tuple(payload.get("applied_validations", ())),
            },
            event_clock,
            "zeus.data.executable_forecast_reader",
        ),
        calibration=AuthorityEvidence(
            claims.CALIBRATION,
            "calibration",
            "calibration",
            {
                "identity": f"{getattr(receipt, 'city', None)}:{getattr(receipt, 'target_date', None)}:{getattr(receipt, 'metric', None)}",
                "calibration_authority": "world_platt_models_v2",
                "model_version_hash": payload.get("calibration_model_version_hash"),
            },
            calibration_clock,
            "zeus.calibration.platt",
        ),
        model_config=AuthorityEvidence(
            claims.MODEL_CONFIG,
            "model_config",
            "model_config",
            {"identity": "edli_v1", "config_scope": "edli_v1", "model_config_hash": payload.get("model_config_hash")},
            decision_clock,
            "zeus.config.settings",
        ),
        belief=AuthorityEvidence(
            claims.BELIEF,
            "belief",
            "belief",
            {
                "identity": getattr(receipt, "candidate_id", None),
                "q_live": getattr(receipt, "q_live", None),
                "q_lcb_5pct": getattr(receipt, "q_lcb_5pct", None),
                "belief_source": "forecast_authority_plus_calibration",
            },
            decision_clock,
            "zeus.strategy.live_inference",
        ),
        executable_snapshot=AuthorityEvidence(
            claims.EXECUTABLE_SNAPSHOT,
            "executable_snapshot",
            "executable_snapshot",
            {
                "identity": getattr(receipt, "executable_snapshot_id", None),
                "executable_snapshot_id": getattr(receipt, "executable_snapshot_id", None),
                "condition_id": getattr(receipt, "condition_id", None),
                "token_id": getattr(receipt, "token_id", None),
            },
            quote_clock,
            "zeus.trade.executable_market_snapshots",
        ),
        quote_feasibility=AuthorityEvidence(
            claims.QUOTE_FEASIBILITY,
            "quote_feasibility",
            "quote_feasibility",
            {
                "identity": getattr(receipt, "token_id", None),
                "condition_id": getattr(receipt, "condition_id", None),
                "token_id": getattr(receipt, "token_id", None),
                "direction": getattr(receipt, "direction", None),
                "native_quote_available": getattr(receipt, "native_quote_available", None),
                "c_fee_adjusted": getattr(receipt, "c_fee_adjusted", None),
                "c_cost_95pct": getattr(receipt, "c_cost_95pct", None),
                "p_fill_lcb": getattr(receipt, "p_fill_lcb", None),
                "fill_claim": False,
            },
            quote_clock,
            "zeus.strategy.live_inference.executable_cost",
        ),
        cost_model=AuthorityEvidence(
            claims.COST_MODEL,
            "cost_model",
            "cost_model",
            {
                "identity": getattr(receipt, "kelly_cost_basis_id", None),
                "execution_price_type": getattr(receipt, "kelly_execution_price_type", None),
                "price_fee_deducted": getattr(receipt, "kelly_price_fee_deducted", None),
                "cost_basis_id": getattr(receipt, "kelly_cost_basis_id", None),
            },
            quote_clock,
            "zeus.strategy.live_inference.executable_cost",
        ),
        pre_trade_evidence=AuthorityEvidence(
            claims.PRE_TRADE_EVIDENCE,
            "pre_trade_evidence",
            "pre_trade_evidence",
            {
                "identity": getattr(receipt, "candidate_id", None),
                "quote_edge_bound": getattr(receipt, "trade_score", None),
                "conditional_edge_given_fill": getattr(receipt, "trade_score", None),
                "actionable_trade_score": 0.0,
            },
            decision_clock,
            "zeus.strategy.live_inference.trade_score",
        ),
        candidate_evidence=AuthorityEvidence(
            claims.CANDIDATE_EVIDENCE,
            "candidate_evidence",
            "candidate_evidence",
            {
                "identity": getattr(receipt, "candidate_id", None),
                "candidate_id": getattr(receipt, "candidate_id", None),
                "bin_label": getattr(receipt, "bin_label", None),
                "outcome_label": getattr(receipt, "outcome_label", None),
            },
            decision_clock,
            "zeus.events.decision_engine",
        ),
        testing_protocol=AuthorityEvidence(
            claims.TESTING_PROTOCOL,
            "testing_protocol",
            "testing_protocol",
            {
                "identity": getattr(receipt, "fdr_family_id", None),
                "testing_protocol_id": getattr(receipt, "fdr_family_id", None) or f"event:{event.event_id}",
                "family_id": getattr(receipt, "fdr_family_id", None),
                "event_trigger_type": event.event_type,
                "look_index": 1,
                "max_looks": 1,
                "alpha_spending_rule": "FIXED_WINDOW_BH",
                "optional_stopping_valid": True,
                "sibling_hypothesis_count": getattr(receipt, "fdr_hypothesis_count", 0),
            },
            decision_clock,
            "zeus.events.fdr_protocol",
        ),
        fdr=AuthorityEvidence(
            claims.FDR,
            "fdr",
            "fdr",
            {
                "identity": getattr(receipt, "fdr_family_id", None),
                "fdr_pass": getattr(receipt, "fdr_pass", None),
                "fdr_family_id": getattr(receipt, "fdr_family_id", None),
                "fdr_hypothesis_count": getattr(receipt, "fdr_hypothesis_count", 0),
            },
            decision_clock,
            "zeus.events.money_path_adapters.fdr",
        ),
        kelly_dry_run=AuthorityEvidence(
            claims.KELLY_DRY_RUN,
            "kelly_dry_run",
            "kelly_dry_run",
            {
                "identity": getattr(receipt, "kelly_decision_id", None),
                "kelly_decision_id": getattr(receipt, "kelly_decision_id", None),
                "kelly_pass": getattr(receipt, "kelly_pass", None),
                "kelly_size_usd": getattr(receipt, "kelly_size_usd", None),
                "execution_price_type": getattr(receipt, "kelly_execution_price_type", None),
                "price_fee_deducted": getattr(receipt, "kelly_price_fee_deducted", None),
            },
            decision_clock,
            "zeus.events.money_path_adapters.kelly",
        ),
        risk_level=AuthorityEvidence(
            claims.RISK_LEVEL,
            "risk_level",
            "risk_level",
            {
                "identity": getattr(receipt, "risk_decision_id", None),
                "risk_decision_id": getattr(receipt, "risk_decision_id", None),
                "risk_level": "GREEN",
            },
            decision_clock,
            "zeus.riskguard",
        ),
        no_submit_projection=projection,
    )


def edge(role: str, cert: DecisionCertificate) -> ParentEdge:
    return ParentEdge(role=role, certificate_hash=cert.certificate_hash, certificate_type=cert.certificate_type)


def _role_for(certificate_type: str) -> str:
    role = certificate_type
    if role.endswith("Certificate"):
        role = role[: -len("Certificate")]
    out = []
    for index, char in enumerate(role):
        if char.isupper() and index:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


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


def _payload_dict(event: OpportunityEvent) -> dict[str, Any]:
    try:
        parsed = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stable_projection_hash(projection: dict[str, Any]) -> str:
    from src.decision_kernel.canonicalization import stable_hash

    return stable_hash(projection)


def _dt_from_payload(payload: dict[str, Any], key: str) -> datetime | None:
    value = payload.get(key)
    if value in {None, ""}:
        return None
    return _parse_dt(str(value))


def _parse_dt(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
