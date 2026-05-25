"""EDLI no-submit decision compiler."""

from __future__ import annotations

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
            _validate_no_submit_parent_consistency(event, proof_bundle)
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


def _validate_no_submit_parent_consistency(event: OpportunityEvent, bundle: NoSubmitProofBundle) -> None:
    projection = bundle.no_submit_projection
    source = bundle.source_truth.payload
    topology = bundle.market_topology.payload
    family = bundle.family_closure.payload
    forecast = bundle.forecast_authority.payload
    calibration = bundle.calibration.payload
    model_config = bundle.model_config.payload
    belief = bundle.belief.payload
    executable = bundle.executable_snapshot.payload
    quote = bundle.quote_feasibility.payload
    cost = bundle.cost_model.payload
    candidate = bundle.candidate_evidence.payload
    protocol = bundle.testing_protocol.payload
    fdr = bundle.fdr.payload
    kelly = bundle.kelly_dry_run.payload
    risk = bundle.risk_level.payload

    if event.causal_snapshot_id:
        _require_equal("source_truth.causal_snapshot_id", source.get("causal_snapshot_id"), "event.causal_snapshot_id", event.causal_snapshot_id)
        _require_equal("forecast.snapshot_id", forecast.get("snapshot_id"), "event.causal_snapshot_id", event.causal_snapshot_id)
    _require_equal("market_topology.family_id", topology.get("family_id"), "family_closure.family_id", family.get("family_id"))
    _require_equal("market_topology.family_id", topology.get("family_id"), "candidate.family_id", candidate.get("family_id"))
    _require_equal("market_topology.family_id", topology.get("family_id"), "testing_protocol.family_id", protocol.get("family_id"))
    _require_equal("market_topology.family_id", topology.get("family_id"), "fdr.fdr_family_id", fdr.get("fdr_family_id"))
    _require_equal("candidate.selected_token_id", candidate.get("selected_token_id"), "quote.token_id", quote.get("token_id"))
    _require_equal("candidate.selected_token_id", candidate.get("selected_token_id"), "quote.selected_token_id", quote.get("selected_token_id"))
    _require_equal("candidate.selected_token_id", candidate.get("selected_token_id"), "cost.token_id", cost.get("token_id"))
    _require_equal("candidate.condition_id", candidate.get("condition_id"), "executable.condition_id", executable.get("condition_id"))
    _require_equal("candidate.condition_id", candidate.get("condition_id"), "quote.condition_id", quote.get("condition_id"))
    _require_equal("candidate.condition_id", candidate.get("condition_id"), "cost.condition_id", cost.get("condition_id"))
    _require_equal(
        "executable.selected_snapshot_id",
        executable.get("selected_snapshot_id"),
        "projection.executable_snapshot_id",
        projection.get("executable_snapshot_id"),
    )
    _require_equal("quote.native_side", quote.get("native_side"), "direction native side", _native_side_for_direction(candidate.get("direction")))
    _require_equal("quote.direction", quote.get("direction"), "candidate.direction", candidate.get("direction"))
    _require_equal("kelly.cost_basis_id", kelly.get("cost_basis_id"), "cost_model.cost_basis_id", cost.get("cost_basis_id"))
    _require_equal("kelly.execution_price_type", kelly.get("execution_price_type"), "cost_model.execution_price_type", cost.get("execution_price_type"))
    if forecast.get("snapshot_id") is not None:
        _require_equal("belief.forecast_snapshot_id", belief.get("forecast_snapshot_id"), "forecast.snapshot_id", forecast.get("snapshot_id"))
    _require_equal(
        "belief.calibrator_model_key",
        belief.get("calibrator_model_key"),
        "calibration.calibrator_model_key",
        calibration.get("calibrator_model_key"),
    )
    _require_equal("belief.bin_labels_hash", belief.get("bin_labels_hash"), "family.bin_labels_hash", family.get("bin_labels_hash"))
    _require_equal("fdr.edge_bootstrap_n", fdr.get("edge_bootstrap_n"), "model_config.edge_bootstrap_n", model_config.get("edge_bootstrap_n"))
    if calibration.get("raw_source_id") is not None and forecast.get("forecast_source_id") is not None:
        _require_equal("calibration.raw_source_id", calibration.get("raw_source_id"), "forecast.forecast_source_id", forecast.get("forecast_source_id"))
    if calibration.get("source_cycle") is not None and forecast.get("source_cycle_time") is not None:
        if str(calibration.get("source_cycle")) not in str(forecast.get("source_cycle_time")):
            raise ValueError("calibration.source_cycle does not match forecast.source_cycle_time")
    if calibration.get("horizon_profile") is not None and forecast.get("horizon_profile") is not None:
        _require_equal("calibration.horizon_profile", calibration.get("horizon_profile"), "forecast.horizon_profile", forecast.get("horizon_profile"))
    hypothesis_id = candidate.get("hypothesis_id") or candidate.get("identity")
    if hypothesis_id not in tuple(fdr.get("selected_hypotheses") or ()):
        raise ValueError("fdr.selected_hypotheses missing candidate hypothesis")
    risk_intent_id = risk.get("final_intent_id")
    if risk_intent_id is not None:
        _require_equal("risk.final_intent_id", risk_intent_id, "bundle.final_intent_id", bundle.final_intent_id)
    selected_token = candidate.get("selected_token_id")
    expected_intent = f"edli_intent:{event.event_id}:{selected_token}"
    if bundle.final_intent_id.startswith("edli_intent:") and bundle.final_intent_id != expected_intent:
        raise ValueError("final_intent_id does not match event and selected token")


def _require_equal(left_name: str, left: Any, right_name: str, right: Any) -> None:
    if left in (None, "") or right in (None, ""):
        raise ValueError(f"missing consistency field: {left_name if left in (None, '') else right_name}")
    if str(left) != str(right):
        raise ValueError(f"{left_name} != {right_name}: {left!r} != {right!r}")


def _native_side_for_direction(direction: Any) -> str | None:
    if direction == "buy_yes":
        return "YES_ASK"
    if direction == "buy_no":
        return "NO_ASK"
    if direction == "sell_yes":
        return "YES_BID"
    if direction == "sell_no":
        return "NO_BID"
    return None


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


def _parse_dt(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
