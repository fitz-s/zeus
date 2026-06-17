"""EDLI no-submit decision compiler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Literal

from src.decision_kernel import claims
from src.decision_kernel.authority import DECISION_KERNEL_AUTHORITY_ID, DECISION_KERNEL_AUTHORITY_VERSION
from src.decision_kernel.certificate import DecisionCertificate, ParentEdge, build_certificate
from src.decision_kernel.certificates.no_submit import build_no_submit_decision_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.ledger import CompileFailure
from src.decision_kernel.verifier import (
    ALT_CREDENTIAL_CALIBRATION_AUTHORITIES,
    APPROVED_CALIBRATION_AUTHORITIES,
    ENSEMBLE_MEMBERS_JSON_SOURCE,
    POSTERIOR_MEMBERS_JSON_SOURCE,
    POSTERIOR_MIN_DECORRELATED_MODELS,
    REQUIRED_POSTERIOR_FORECAST_VALIDATIONS,
    calibration_maturity_too_low,
)
from src.events.opportunity_event import OpportunityEvent

CompileStatus = Literal["VERIFIED", "REJECTED", "REVIEW_REQUIRED"]
FORECAST_LIVE_ELIGIBLE_STATUS = "LIVE_ELIGIBLE"
FORECAST_READER_STATUS_ALIASES = {
    "LIVE_ELIGIBLE": FORECAST_LIVE_ELIGIBLE_STATUS,
    "OK": FORECAST_LIVE_ELIGIBLE_STATUS,
    "EXECUTABLE_FORECAST_READY": FORECAST_LIVE_ELIGIBLE_STATUS,
    "VERIFIED": FORECAST_LIVE_ELIGIBLE_STATUS,
}
REQUIRED_FORECAST_VALIDATIONS = frozenset(
    {
        "source_run_completeness_status",
        "coverage_completeness_status",
        "coverage_readiness_status",
        "required_steps_observed",
        "expected_members_observed",
        "causality_status_ok",
        "authority_verified",
        "available_at_not_future",
    }
)


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
            source_available_at=decision_time,  # AVAIL-POSSESSION-EXEMPTED: structural decision-time cert (NO_SUBMIT_MODE record generated AT decision_time, wraps no external source); field consumed only by verifier no-future-leakage check (<=decision_time), cert hash, max_parent_* monotonicity — never a freshness gate or q. decision_time is the only honest anchor.
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
            _validate_no_submit_parent_consistency(event, proof_bundle, decision_time=decision_time)
            no_submit = build_no_submit_decision_certificate(
                semantic_key=f"no_submit:{event.event_id}:{proof_bundle.final_intent_id}",
                decision_time=decision_time,
                parent_edges=tuple(edge(_role_for(parent.certificate_type), parent) for parent in parents),
                parents=parents,
                payload={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    # FORECAST no-submit scope = BOTH forecast-decision event types. EDLI_REDECISION_PENDING
                    # (the price-driven re-decision lane introduced 2026-06-12) re-decides a forecast family
                    # through THIS forecast compile path (forecast/calibration/belief parents above), so its
                    # no-submit decision_source is "forecast", not "day0_or_other". The prior FSR-only label
                    # made every EDLI_REDECISION no-submit fail verify_no_submit_decision ("unsupported
                    # decision_source") -> certificate REJECTED -> receipt NEVER written. That silently killed
                    # the edli_no_submit_receipts claim stream on 2026-06-12T12:12 (the day the redecision lane
                    # became the dominant forecast-decision path), starving the q_lcb_settlement_coverage_gate
                    # of its per-day claimed-q_lcb input -> proven NO over-confidence (78% claimed vs 60%
                    # realized) ran uncorrected into live sizing. Mirrors reactor._FORECAST_DECISION_EVENT_TYPES.
                    "decision_source": (
                        "forecast"
                        if event.event_type in ("FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING")
                        else "day0_or_other"
                    ),
                    "final_intent_id": proof_bundle.final_intent_id,
                    "side_effect_status": "NO_SUBMIT",
                    "proof_accepted": bool(proof_bundle.no_submit_projection.get("proof_accepted")),
                    "submitted": False,
                    "quote_edge_bound": proof_bundle.pre_trade_evidence.payload.get("quote_edge_bound"),
                    "conditional_edge_given_fill": proof_bundle.pre_trade_evidence.payload.get("conditional_edge_given_fill"),
                    "actionable_trade_score": 0.0,
                    "no_submit_verified": True,
                    "projection_hash": proof_bundle.no_submit_projection.get("projection_hash"),
                    "executable_snapshot_id": proof_bundle.no_submit_projection.get("executable_snapshot_id"),
                    "generated_at_decision_time": True,
                    "header_persisted_at_semantics": "decision_kernel_generated_at_decision_time",
                    "db_created_at_may_follow_header_persisted_at": True,
                },
                source_available_at=decision_time,  # AVAIL-POSSESSION-EXEMPTED: structural decision-time cert (NO_SUBMIT decision generated AT decision_time per generated_at_decision_time payload flag, wraps no external source); field consumed only by verifier no-future-leakage check (<=decision_time), cert hash, max_parent_* monotonicity — never a freshness gate or q. decision_time is the only honest anchor.
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
            source_available_at=decision_time,  # AVAIL-POSSESSION-EXEMPTED: structural decision-time cert (CLOCK_MODE record; clock_source=reactor_decision_time, wraps no external source); field consumed only by verifier no-future-leakage check (<=decision_time), cert hash, max_parent_* monotonicity — never a freshness gate or q. decision_time is the only honest anchor.
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


def _validate_no_submit_parent_consistency(event: OpportunityEvent, bundle: NoSubmitProofBundle, *, decision_time: datetime) -> None:
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

    _require_equal("projection.event_id", projection.get("event_id"), "event.event_id", event.event_id)
    _require_equal("projection.final_intent_id", projection.get("final_intent_id"), "bundle.final_intent_id", bundle.final_intent_id)
    if projection.get("side_effect_status") != "NO_SUBMIT":
        raise ValueError("projection.side_effect_status must be NO_SUBMIT")
    if projection.get("submitted") is not False:
        raise ValueError("projection.submitted must be false")
    if projection.get("proof_accepted") is not True:
        raise ValueError("projection.proof_accepted must be true")
    if event.causal_snapshot_id:
        # Two distinct snapshot chains, per the single-snapshot reader-elect authority
        # (event_reactor_adapter._forecast_snapshot_row_for_event): the CAUSAL chain is the
        # event's trigger snapshot (provenance only); the EXECUTABLE-AUTHORITY chain is the
        # reader-ELECTED snapshot on which inference was actually computed. When the causal
        # cycle's source_run is still re-ingesting members, the reader's causality gate drops
        # the causal snapshot and elects the freshest fully-captured FULL_CONTRIBUTOR, so the
        # elected id legitimately differs from the causal id. Asserting forecast.snapshot_id ==
        # event.causal_snapshot_id here contradicted that fix and produced a permanent
        # FORECAST_READER_SNAPSHOT_MISMATCH leak (no receipts). We bind each chain to its own
        # identity instead:
        #   causal provenance      : source_truth.{causal_snapshot_id,snapshot_id} == event.causal
        #   executable authority   : source_truth.derived_from_snapshot_id == forecast.snapshot_id
        #                            (asserted below at the FORECAST_SNAPSHOT_READY block) and
        #                            belief.forecast_snapshot_id == forecast.snapshot_id (later).
        _require_equal("source_truth.causal_snapshot_id", source.get("causal_snapshot_id"), "event.causal_snapshot_id", event.causal_snapshot_id)
        _require_equal("source_truth.snapshot_id", source.get("snapshot_id"), "event.causal_snapshot_id", event.causal_snapshot_id)
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        _validate_source_truth_payload(source)
        _require_equal("source_truth.completeness_status", source.get("completeness_status"), "COMPLETE", "COMPLETE")
        if source.get("required_fields_present") is not True:
            raise ValueError("source_truth.required_fields_present must be true")
        if source.get("required_steps_present") is not True:
            raise ValueError("source_truth.required_steps_present must be true")
        # WAVE-1 W1-T3: dual-chain source_run binding (gated; legacy single-chain
        # equality preserved when the flag is OFF or derived_from_source_run_id absent).
        bind_source_run_chains(source, forecast)
        event_payload = _event_payload_dict(event)
        if event_payload.get("source_run_id") not in (None, ""):
            _require_equal(
                "source_truth.source_run_id",
                source.get("source_run_id"),
                "event.payload.source_run_id",
                event_payload.get("source_run_id"),
            )
        _require_equal("source_truth.source_id", source.get("source_id"), "forecast.forecast_source_id", forecast.get("forecast_source_id"))
        _require_equal("source_truth.payload_hash", source.get("payload_hash"), "event.payload_hash", event.payload_hash)
        _require_equal("source_truth.event_source", source.get("event_source"), "event.source", event.source)
        _require_equal("source_truth.derived_from_certificate_type", source.get("derived_from_certificate_type"), "ForecastAuthorityCertificate", claims.FORECAST_AUTHORITY)
        _require_equal("source_truth.derived_from_snapshot_id", source.get("derived_from_snapshot_id"), "forecast.snapshot_id", forecast.get("snapshot_id"))
        _require_equal(
            "source_truth.derived_from_reader_status",
            normalize_forecast_reader_status(source.get("derived_from_reader_status")),
            "forecast.reader_status",
            normalize_forecast_reader_status(forecast.get("reader_status")),
        )
        _require_equal(
            "source_truth.source_status",
            normalize_forecast_reader_status(source.get("source_status")),
            "forecast.reader_status",
            normalize_forecast_reader_status(forecast.get("reader_status")),
        )
        _require_equal("source_truth.source_authority_id", source.get("source_authority_id"), "forecast.reader_authority", forecast.get("reader_authority"))
    _validate_forecast_authority_payload(forecast)
    _validate_calibration_payload(calibration, model_config, forecast, decision_time=decision_time)
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
    _require_equal(
        "model_config.calibrator_model_key",
        model_config.get("calibrator_model_key"),
        "calibration.calibrator_model_key",
        calibration.get("calibrator_model_key"),
    )
    _require_equal(
        "belief.calibrator_model_hash",
        belief.get("calibrator_model_hash"),
        "calibration.model_hash",
        calibration.get("model_hash"),
    )
    _require_equal(
        "model_config.calibrator_model_hash",
        model_config.get("calibrator_model_hash"),
        "calibration.model_hash",
        calibration.get("model_hash"),
    )
    for field in ("p_cal_vector_hash", "p_live_vector_hash"):
        if belief.get(field) in (None, ""):
            raise ValueError(f"belief.{field} missing")
    _require_equal("belief.p_cal_hash", belief.get("p_cal_hash"), "belief.p_cal_vector_hash", belief.get("p_cal_vector_hash"))
    _require_equal("belief.p_live_hash", belief.get("p_live_hash"), "belief.p_live_vector_hash", belief.get("p_live_vector_hash"))
    _require_equal("belief.bin_labels_hash", belief.get("bin_labels_hash"), "family.bin_labels_hash", family.get("bin_labels_hash"))
    _require_equal("belief.members_json_hash", belief.get("members_json_hash"), "forecast.members_json_hash", forecast.get("members_json_hash"))
    _require_equal("forecast.bin_labels_hash", forecast.get("bin_labels_hash"), "family.bin_labels_hash", family.get("bin_labels_hash"))
    _require_equal("forecast.members_extrema_metric_identity", forecast.get("members_extrema_metric_identity"), "family.metric", family.get("metric"))
    _require_equal("forecast.target_local_date", forecast.get("target_local_date"), "family.target_date", family.get("target_date"))
    _validate_unit_authority(forecast, belief, family)
    _validate_cost_sources(quote, cost, candidate)
    _require_equal("fdr.edge_bootstrap_n", fdr.get("edge_bootstrap_n"), "model_config.edge_bootstrap_n", model_config.get("edge_bootstrap_n"))
    if calibration.get("raw_source_id") is not None and forecast.get("forecast_source_id") is not None:
        _require_equal("calibration.raw_source_id", calibration.get("raw_source_id"), "forecast.forecast_source_id", forecast.get("forecast_source_id"))
    if calibration.get("source_cycle") is not None and forecast.get("source_cycle_time") is not None:
        if str(calibration.get("source_cycle")) not in str(forecast.get("source_cycle_time")):
            raise ValueError("calibration.source_cycle does not match forecast.source_cycle_time")
    # Enforced equality (matches verifier._verify_forecast_no_submit_semantic_consistency): the
    # forecast authority DERIVES horizon_profile from its cycle exactly as the calibrator lookup
    # does, so both are always populated for a live forecast. A None on either side is a real
    # provenance gap (no derivable horizon stratum) and must fail closed, never be skipped.
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


def _validate_source_truth_payload(source: dict[str, Any]) -> None:
    status = normalize_forecast_reader_status(source.get("source_status"))
    if status != FORECAST_LIVE_ELIGIBLE_STATUS:
        raise ValueError("source_truth.source_status is not verified")
    reason = source.get("source_reason_code")
    if reason not in (None, "", "OK"):
        raise ValueError("source_truth.source_reason_code must be empty for verified no-submit")
    for field in ("source_authority_id", "snapshot_id", "source_run_id", "payload_hash"):
        if source.get(field) in (None, ""):
            raise ValueError(f"source_truth.{field} missing")


def _validate_posterior_forecast_authority_payload(forecast: dict[str, Any]) -> None:
    """EQUALLY-STRICT compiler validation for a posterior-provenance FORECAST_AUTHORITY
    (mx2t3 carrier-decouple GATE-1 C). Mirrors verifier._validate_posterior_forecast_authority_payload:
    coverage COMPLETE/LIVE_ELIGIBLE, posterior identity + members hash present, the decorrelated
    model-count floor (>= the spine's own >=3-member floor), and the posterior applied-validations
    set. Reads a DIFFERENT certified completeness authority; does NOT weaken the ensemble gates."""
    for field in (
        "coverage_readiness_status",
        "coverage_completeness_status",
        "temperature_metric",
        "members_json_source",
        "members_json_hash",
        "posterior_identity_hash",
        "source_cycle_time",
        "source_run_id",
        "forecast_source_id",
    ):
        if forecast.get(field) in (None, ""):
            raise ValueError(f"forecast.{field} missing (posterior)")
    if forecast.get("coverage_readiness_status") != "LIVE_ELIGIBLE":
        raise ValueError("forecast.coverage_readiness_status must be LIVE_ELIGIBLE")
    if forecast.get("coverage_completeness_status") != "COMPLETE":
        raise ValueError("forecast.coverage_completeness_status must be COMPLETE")
    try:
        expected_models = int(forecast.get("expected_members"))
        observed_models = int(forecast.get("observed_members"))
    except (TypeError, ValueError):
        raise ValueError("forecast.expected/observed decorrelated model count missing")
    if observed_models < expected_models:
        raise ValueError("forecast.observed_members below expected_members (posterior)")
    if observed_models < POSTERIOR_MIN_DECORRELATED_MODELS:
        raise ValueError(
            f"forecast.observed_members below posterior decorrelated-model floor "
            f"({POSTERIOR_MIN_DECORRELATED_MODELS})"
        )
    applied_validations = {str(item) for item in tuple(forecast.get("applied_validations") or ())}
    if not applied_validations:
        raise ValueError("forecast.applied_validations missing (posterior)")
    missing = REQUIRED_POSTERIOR_FORECAST_VALIDATIONS - applied_validations
    if missing:
        raise ValueError(
            f"forecast.applied_validations missing required posterior validations: {sorted(missing)}"
        )


def _validate_forecast_authority_payload(forecast: dict[str, Any]) -> None:
    status = normalize_forecast_reader_status(forecast.get("reader_status"))
    if status != FORECAST_LIVE_ELIGIBLE_STATUS:
        raise ValueError("forecast.reader_status is not live eligible")
    reason = forecast.get("reader_reason_code")
    if reason not in (None, "", "OK"):
        raise ValueError("forecast.reader_reason_code must be empty for verified no-submit")
    # mx2t3 carrier-decouple (GATE-1 C): posterior-provenance authority is validated by the
    # equally-strict posterior invariant set (model-count completeness, not ensemble member/step
    # floors); the ensemble branch below is UNCHANGED for ensemble provenance.
    if forecast.get("members_json_source") == POSTERIOR_MEMBERS_JSON_SOURCE:
        _validate_posterior_forecast_authority_payload(forecast)
        return
    if forecast.get("coverage_readiness_status") != "LIVE_ELIGIBLE":
        raise ValueError("forecast.coverage_readiness_status must be LIVE_ELIGIBLE")
    if forecast.get("coverage_completeness_status") != "COMPLETE":
        raise ValueError("forecast.coverage_completeness_status must be COMPLETE")
    source_run_completeness = str(forecast.get("source_run_completeness_status") or "")
    if source_run_completeness not in {"COMPLETE", "PARTIAL"}:
        raise ValueError("forecast.source_run_completeness_status must be COMPLETE or PARTIAL")
    if source_run_completeness == "PARTIAL":
        source_run_status = str(forecast.get("source_run_status") or "")
        if source_run_status not in {"SUCCESS", "PARTIAL"}:
            raise ValueError("forecast.source_run_status must be SUCCESS or PARTIAL for PARTIAL source_run")
    required_steps = {str(item) for item in (forecast.get("required_steps") or ())}
    observed_steps = {str(item) for item in (forecast.get("observed_steps") or ())}
    if not required_steps:
        raise ValueError("forecast.required_steps missing")
    if not required_steps.issubset(observed_steps):
        raise ValueError("forecast.observed_steps missing required_steps")
    expected_members = _optional_int(forecast.get("expected_members"))
    observed_members = _optional_int(forecast.get("observed_members"))
    if expected_members is None:
        raise ValueError("forecast.expected_members missing")
    if observed_members is None:
        raise ValueError("forecast.observed_members missing")
    if observed_members < expected_members:
        raise ValueError("forecast.observed_members below expected_members")
    applied_validations = {str(item) for item in tuple(forecast.get("applied_validations") or ())}
    if not applied_validations:
        raise ValueError("forecast.applied_validations missing")
    missing_validations = REQUIRED_FORECAST_VALIDATIONS - applied_validations
    if missing_validations:
        raise ValueError(f"forecast.applied_validations missing required validations: {sorted(missing_validations)}")
    _require_equal(
        "forecast.members_extrema_metric_identity",
        forecast.get("members_extrema_metric_identity"),
        "forecast.temperature_metric",
        forecast.get("temperature_metric") or forecast.get("metric"),
    )
    if forecast.get("members_json_source") in (None, ""):
        raise ValueError("forecast.members_json_source missing")
    if forecast.get("members_json_hash") in (None, ""):
        raise ValueError("forecast.members_json_hash missing")
    if forecast.get("members_extrema_transform") != _expected_members_extrema_transform(forecast.get("temperature_metric")):
        raise ValueError("forecast.members_extrema_transform mismatch")
    for field in ("target_local_date", "city_timezone", "bin_labels_hash"):
        if forecast.get(field) in (None, ""):
            raise ValueError(f"forecast.{field} missing")
    if forecast.get("local_date_window_hash") in (None, ""):
        raise ValueError("forecast.local_date_window_hash missing")


def _validate_calibration_payload(
    calibration: dict[str, Any],
    model_config: dict[str, Any],
    forecast: dict[str, Any],
    *,
    decision_time: datetime,
) -> None:
    authority = calibration.get("authority")
    if authority in (None, ""):
        raise ValueError("calibration.authority missing")
    if str(authority) not in APPROVED_CALIBRATION_AUTHORITIES:
        raise ValueError("calibration.authority is not approved")
    maturity = _optional_int(calibration.get("maturity_level"))
    if maturity is None:
        raise ValueError("calibration.maturity_level missing")
    # K1.3: ONE shared maturity rule — calibration_maturity_too_low from the verifier
    # module (single constant + single predicate; the divergent-twin-tuple incident
    # CERT BRIDGE 2026-06-10 is the reason this must never be a local formula again).
    if calibration_maturity_too_low(maturity, authority):
        raise ValueError("calibration.maturity_level too low")
    input_space = calibration.get("input_space")
    expected_input_space = model_config.get("calibration_input_space")
    if input_space in (None, ""):
        raise ValueError("calibration.input_space missing")
    if expected_input_space in (None, ""):
        raise ValueError("model_config.calibration_input_space missing")
    _require_equal("calibration.input_space", input_space, "model_config.calibration_input_space", expected_input_space)
    if not any(calibration.get(field) not in (None, "") for field in ("model_available_at", "recorded_at", "fitted_at")):
        raise ValueError("calibration model clock missing")
    for field in ("training_cutoff", "model_available_at", "recorded_at", "fitted_at"):
        when = _optional_dt(calibration.get(field))
        if when is not None and when > decision_time:
            raise ValueError(f"calibration.{field} after decision_time")
    # Enforced equality (see _validate_no_submit_parent_consistency + verifier): the forecast
    # authority derives horizon_profile from its cycle exactly as the calibrator lookup does, so a
    # None on either side is a real provenance gap and must fail closed rather than be skipped.
    _require_equal("calibration.horizon_profile", calibration.get("horizon_profile"), "forecast.horizon_profile", forecast.get("horizon_profile"))


def _validate_unit_authority(forecast: dict[str, Any], belief: dict[str, Any], family: dict[str, Any]) -> None:
    unit = forecast.get("unit")
    if unit not in {"F", "C"}:
        raise ValueError("forecast.unit missing or unsupported")
    if forecast.get("unit_authority_source") not in {
        "ensemble_snapshots.settlement_unit",
        "ensemble_snapshots.members_unit",
        "city_config.settlement_unit",
    }:
        raise ValueError("forecast.unit_authority_source missing")
    bin_units = tuple(str(item) for item in (family.get("bin_units") or ()))
    if not bin_units:
        raise ValueError("family.bin_units missing")
    for bin_unit in bin_units:
        _require_equal("family.bin_unit", bin_unit, "forecast.unit", unit)
    _require_equal("belief.unit", belief.get("unit"), "forecast.unit", unit)
    _require_equal(
        "belief.unit_authority_source",
        belief.get("unit_authority_source"),
        "forecast.unit_authority_source",
        forecast.get("unit_authority_source"),
    )


def _validate_cost_sources(quote: dict[str, Any], cost: dict[str, Any], candidate: dict[str, Any]) -> None:
    expected_cost_source = _expected_cost_source_for_direction(candidate.get("direction"))
    for label, payload in (("quote", quote), ("cost", cost)):
        if payload.get("forbidden_cost_source") is not False:
            raise ValueError(f"{label}.forbidden_cost_source must be false")
        _require_equal(f"{label}.cost_source", payload.get("cost_source"), "direction cost_source", expected_cost_source)
        _require_equal(
            f"{label}.quote_source_kind",
            payload.get("quote_source_kind"),
            "executable native book",
            "executable_market_snapshot_native_book",
        )


def _expected_cost_source_for_direction(direction: Any) -> str:
    if direction in {"buy_yes", "buy_no"}:
        return "native_orderbook_ask"
    if direction in {"sell_yes", "sell_no"}:
        return "native_orderbook_bid"
    raise ValueError("candidate.direction unsupported for cost source")


def _expected_members_extrema_transform(metric: Any) -> str:
    if metric == "high":
        return "daily_max"
    if metric == "low":
        return "daily_min"
    raise ValueError("forecast.temperature_metric unsupported for members extrema transform")


def normalize_forecast_reader_status(status: Any, reason_code: Any = None) -> str | None:
    if reason_code not in (None, "", "OK", "LIVE_ELIGIBLE", "EXECUTABLE_FORECAST_READY"):
        return None
    raw = str(status or "").strip().upper()
    return FORECAST_READER_STATUS_ALIASES.get(raw)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _utc(value)
    return _parse_dt(str(value))


def _require_equal(left_name: str, left: Any, right_name: str, right: Any) -> None:
    if left in (None, "") or right in (None, ""):
        raise ValueError(f"missing consistency field: {left_name if left in (None, '') else right_name}")
    if str(left) != str(right):
        raise ValueError(f"{left_name} != {right_name}: {left!r} != {right!r}")


def _event_payload_dict(event: OpportunityEvent) -> dict[str, Any]:
    payload = event.payload_json
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str) or payload == "":
        return {}
    parsed = json.loads(payload)
    return parsed if isinstance(parsed, dict) else {}


def _dual_chain_source_run_enabled() -> bool:
    """Read edli.edli_source_run_dual_chain_enabled (default OFF in code).

    WAVE-1 W1-T3. FAIL-CLOSED to the legacy single-chain binding: any
    config-access error → False. Shadow-safe — the relaxation is inert until the
    operator flips the flag in live config.
    """
    try:
        from src.config import settings

        return bool(settings["edli"].get("edli_source_run_dual_chain_enabled", False))
    except Exception:  # noqa: BLE001 — config glitch must never relax the cert silently
        return False


def bind_source_run_chains(source: dict[str, Any], forecast: dict[str, Any]) -> None:
    """Bind the source_run identity across the causal + executable chains.

    WAVE-1 W1-T3. The cert historically asserted a SINGLE cross-chain equality
    ``source_truth.source_run_id == forecast.source_run_id``. When the causal
    cycle's run (e.g. 00Z) is still re-ingesting members, the reader legitimately
    elects a fresher fully-captured run (e.g. 12Z) for inference, so the causal
    run and the executable (forecast) run differ — and 11 benign advances died
    at NO_SUBMIT_CERTIFICATE.

    With ``edli_source_run_dual_chain_enabled`` ON AND the adapter having stamped
    ``source_truth.derived_from_source_run_id`` (the reader-elected executable
    run), we bind BOTH chains independently:
      - executable chain: derived_from_source_run_id == forecast.source_run_id
      - causal chain:      source_truth.source_run_id is the event's causal run
                           (NOT required to equal the forecast run); its causal
                           integrity is separately asserted via
                           source_truth.causal_snapshot_id / payload_hash.

    A FABRICATED forecast whose source_run_id differs from the reader-elected
    derived_from_source_run_id STILL FAILS — causal integrity is not weakened.

    When the flag is OFF (default) OR derived_from_source_run_id is absent, the
    legacy single-chain equality is enforced — byte-identical to pre-W1-T3.
    """
    derived = source.get("derived_from_source_run_id")
    if _dual_chain_source_run_enabled() and derived not in (None, ""):
        # Executable chain binds to the reader-elected run.
        _require_equal(
            "source_truth.derived_from_source_run_id",
            derived,
            "forecast.source_run_id",
            forecast.get("source_run_id"),
        )
        # Causal chain: source_truth.source_run_id must be present (the causal
        # run) but is NOT bound to the forecast run.
        if source.get("source_run_id") in (None, ""):
            raise ValueError("missing consistency field: source_truth.source_run_id")
        return
    # Legacy single-chain equality.
    _require_equal(
        "source_truth.source_run_id",
        source.get("source_run_id"),
        "forecast.source_run_id",
        forecast.get("source_run_id"),
    )


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
