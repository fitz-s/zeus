# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §10, §12, §13.
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.compiler import AuthorityEvidence, EvidenceClock, NoSubmitProofBundle
from src.events.opportunity_event import OpportunityEvent


def build_test_no_submit_proof_bundle(
    event: OpportunityEvent,
    receipt: Any,
    *,
    decision_time: datetime,
) -> NoSubmitProofBundle:
    """Build a typed no-submit authority fixture without production receipt fallback."""

    decision_time = _utc(decision_time)
    event_clock = EvidenceClock(
        source_available_at=_parse_dt(event.available_at),
        agent_received_at=_parse_dt(event.received_at),
        persisted_at=_parse_dt(event.created_at),
    )
    decision_clock = EvidenceClock(decision_time, decision_time, decision_time)
    quote_clock = EvidenceClock(decision_time, decision_time, decision_time)
    event_payload = _payload_dict(event)
    family_id = str(getattr(receipt, "family_id", None) or "family-1")
    condition_id = str(getattr(receipt, "condition_id", None) or "condition-1")
    token_id = str(getattr(receipt, "token_id", None) or "yes-1")
    snapshot_id = str(getattr(receipt, "executable_snapshot_id", None) or "snapshot-exec-1")
    cost_basis_id = str(getattr(receipt, "kelly_cost_basis_id", None) or "cost-1")
    direction = str(getattr(receipt, "direction", None) or "buy_yes")
    hypothesis_id = f"{family_id}:{token_id}"
    final_intent_id = str(getattr(receipt, "final_intent_id", None) or f"edli_intent:{event.event_id}:{token_id}")
    bin_labels_hash = stable_hash(("70-71F",))
    model_config_hash = stable_hash({"edge_bootstrap_n": 1000})
    projection = {
        "event_id": event.event_id,
        "final_intent_id": final_intent_id,
        "side_effect_status": "NO_SUBMIT",
        "proof_accepted": True,
        "submitted": False,
        "executable_snapshot_id": snapshot_id,
    }
    projection["projection_hash"] = stable_hash(projection)
    return NoSubmitProofBundle(
        final_intent_id=final_intent_id,
        source_truth=AuthorityEvidence(
            claims.SOURCE_TRUTH,
            "source_truth",
            "source_truth",
            {
                "identity": event.source,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "source_status": "MATCH",
                "causal_snapshot_id": event.causal_snapshot_id,
                "snapshot_id": event.causal_snapshot_id,
                "completeness_status": event_payload.get("completeness_status"),
                "required_fields_present": event_payload.get("required_fields_present"),
                "required_steps_present": event_payload.get("required_steps_present"),
                "source_id": event_payload.get("source_id"),
                "source_run_id": event_payload.get("source_run_id"),
                "payload_hash": event.payload_hash,
                "available_at": event.available_at,
                "received_at": event.received_at,
            },
            event_clock,
            "test.source_truth",
        ),
        market_topology=AuthorityEvidence(
            claims.MARKET_TOPOLOGY,
            "market_topology",
            "market_topology",
            {"identity": family_id, "family_id": family_id, "condition_ids": (condition_id,)},
            decision_clock,
            "test.market_topology",
        ),
        family_closure=AuthorityEvidence(
            claims.FAMILY_CLOSURE,
            "family_closure",
            "family_closure",
            {"identity": family_id, "family_id": family_id, "condition_ids": (condition_id,), "bin_labels_hash": bin_labels_hash},
            decision_clock,
            "test.family_closure",
        ),
        forecast_authority=AuthorityEvidence(
            claims.FORECAST_AUTHORITY,
            "forecast_authority",
            "forecast_authority",
            {
                "identity": event.causal_snapshot_id,
                "snapshot_id": event.causal_snapshot_id,
                "forecast_source_id": "opendata",
                "source_cycle_time": "2026-05-25T00:00:00+00:00",
                "horizon_profile": "default",
                "reader_status": "LIVE_ELIGIBLE",
                "reader_reason_code": None,
                "coverage_readiness_status": "LIVE_ELIGIBLE",
                "coverage_completeness_status": "COMPLETE",
                "source_run_completeness_status": "COMPLETE",
                "required_steps": (0,),
                "observed_steps": (0,),
                "expected_members": 51,
                "observed_members": 51,
                "applied_validations": ("test_authority_validation",),
            },
            event_clock,
            "test.forecast_authority",
        ),
        calibration=AuthorityEvidence(
            claims.CALIBRATION,
            "calibration",
            "calibration",
            {
                "identity": "model-1",
                "calibrator_model_key": "model-1",
                "raw_source_id": "opendata",
                "source_cycle": "00",
                "horizon_profile": "default",
                "model_hash": "model-hash-1",
                "authority": "VERIFIED",
                "maturity_level": 1,
                "input_space": "width_normalized_density",
                "training_cutoff": "2026-05-01T00:00:00+00:00",
                "model_available_at": "2026-05-01T00:00:00+00:00",
            },
            decision_clock,
            "test.calibration",
        ),
        model_config=AuthorityEvidence(
            claims.MODEL_CONFIG,
            "model_config",
            "model_config",
            {
                "identity": "edli_v1",
                "edge_bootstrap_n": 1000,
                "market_analysis_config_hash": model_config_hash,
                "calibration_input_space": "width_normalized_density",
            },
            decision_clock,
            "test.model_config",
        ),
        belief=AuthorityEvidence(
            claims.BELIEF,
            "belief",
            "belief",
            {
                "identity": hypothesis_id,
                "forecast_snapshot_id": event.causal_snapshot_id,
                "calibrator_model_key": "model-1",
                "bin_labels_hash": bin_labels_hash,
                "p_cal_hash": "p-cal-hash-1",
                "p_live_hash": "p-live-hash-1",
                "market_analysis_config_hash": model_config_hash,
                "bootstrap_n": 1000,
            },
            event_clock,
            "test.belief",
        ),
        executable_snapshot=AuthorityEvidence(
            claims.EXECUTABLE_SNAPSHOT,
            "executable_snapshot",
            "executable_snapshot",
            {
                "identity": snapshot_id,
                "selected_snapshot_id": snapshot_id,
                "condition_id": condition_id,
                "token_id": token_id,
            },
            quote_clock,
            "test.executable_snapshot",
        ),
        quote_feasibility=AuthorityEvidence(
            claims.QUOTE_FEASIBILITY,
            "quote_feasibility",
            "quote_feasibility",
            {
                "identity": hypothesis_id,
                "condition_id": condition_id,
                "token_id": token_id,
                "selected_token_id": token_id,
                "direction": direction,
                "native_side": "YES_ASK",
                "execution_price_type": "ExecutionPrice",
            },
            quote_clock,
            "test.quote_feasibility",
        ),
        cost_model=AuthorityEvidence(
            claims.COST_MODEL,
            "cost_model",
            "cost_model",
            {
                "identity": cost_basis_id,
                "cost_basis_id": cost_basis_id,
                "condition_id": condition_id,
                "token_id": token_id,
                "execution_price_type": "ExecutionPrice",
            },
            quote_clock,
            "test.cost_model",
        ),
        pre_trade_evidence=AuthorityEvidence(
            claims.PRE_TRADE_EVIDENCE,
            "pre_trade_evidence",
            "pre_trade_evidence",
            {"identity": hypothesis_id, "quote_edge_bound": 0.1, "actionable_trade_score": 0.0},
            decision_clock,
            "test.pre_trade_evidence",
        ),
        candidate_evidence=AuthorityEvidence(
            claims.CANDIDATE_EVIDENCE,
            "candidate_evidence",
            "candidate_evidence",
            {
                "identity": hypothesis_id,
                "candidate_id": "candidate-1",
                "family_id": family_id,
                "condition_id": condition_id,
                "selected_token_id": token_id,
                "direction": direction,
                "hypothesis_id": hypothesis_id,
            },
            decision_clock,
            "test.candidate_evidence",
        ),
        testing_protocol=AuthorityEvidence(
            claims.TESTING_PROTOCOL,
            "testing_protocol",
            "testing_protocol",
            {"identity": family_id, "testing_protocol_id": f"test:{family_id}", "family_id": family_id},
            decision_clock,
            "test.testing_protocol",
        ),
        fdr=AuthorityEvidence(
            claims.FDR,
            "fdr",
            "fdr",
            {
                "identity": family_id,
                "fdr_family_id": family_id,
                "selected_hypotheses": (hypothesis_id,),
                "fdr_hypothesis_count": 2,
                "edge_bootstrap_n": 1000,
            },
            decision_clock,
            "test.fdr",
        ),
        kelly_dry_run=AuthorityEvidence(
            claims.KELLY_DRY_RUN,
            "kelly_dry_run",
            "kelly_dry_run",
            {
                "identity": "kelly-1",
                "kelly_decision_id": "kelly-1",
                "cost_basis_id": cost_basis_id,
                "execution_price_type": "ExecutionPrice",
            },
            decision_clock,
            "test.kelly",
        ),
        risk_level=AuthorityEvidence(
            claims.RISK_LEVEL,
            "risk_level",
            "risk_level",
            {"identity": "risk-1", "risk_decision_id": "risk-1", "risk_level": "GREEN", "final_intent_id": final_intent_id},
            decision_clock,
            "test.risk",
        ),
        no_submit_projection=projection,
    )


def _parse_dt(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _payload_dict(event: OpportunityEvent) -> dict[str, Any]:
    try:
        parsed = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
