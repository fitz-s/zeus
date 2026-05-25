"""Decision certificate verifier rules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from src.decision_kernel import claims
from src.decision_kernel.certificate import DecisionCertificate, certificate_hash_for
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.modes import ALLOWED_MODES, is_live_like


def verify_certificate(
    cert: DecisionCertificate,
    parents: Iterable[DecisionCertificate] = (),
    *,
    decision_time: datetime | None = None,
) -> None:
    header = cert.header
    if header.mode not in ALLOWED_MODES:
        raise CertificateVerificationError(f"invalid certificate mode: {header.mode}")
    expected_decision_time = _utc(decision_time) if decision_time is not None else header.decision_time
    if header.decision_time != expected_decision_time:
        raise CertificateVerificationError("certificate decision_time does not match verifier decision_time")
    _verify_parent_edges(cert, tuple(parents))
    if certificate_hash_for(header) != header.certificate_hash:
        raise CertificateVerificationError("certificate hash mismatch")
    _verify_time_filtration(cert)
    _verify_generated_certificate_semantics(cert)


def verify_no_submit_decision(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.NO_SUBMIT_DECISION:
        raise CertificateVerificationError("expected NoSubmitDecisionCertificate")
    if cert.header.mode != "NO_SUBMIT":
        raise CertificateVerificationError("no-submit decision must use NO_SUBMIT mode")
    _forbid_no_submit_payload(cert)
    parent_types = {parent.certificate_type for parent in parent_tuple}
    required = claims.NO_SUBMIT_REQUIRED_TYPES
    if cert.payload.get("decision_source") == "forecast":
        required = claims.NO_SUBMIT_FORECAST_REQUIRED_TYPES
    missing = required - parent_types
    if missing:
        raise CertificateVerificationError(f"no-submit decision missing parents: {sorted(missing)}")
    forbidden = claims.NO_SUBMIT_FORBIDDEN_TYPES & parent_types
    if forbidden:
        raise CertificateVerificationError(f"no-submit decision has forbidden parents: {sorted(forbidden)}")
    _verify_no_submit_generated_time_semantics(cert)
    if cert.payload.get("decision_source") == "forecast":
        _verify_forecast_no_submit_semantic_consistency(cert, parent_tuple)


def verify_actionable_trade(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.ACTIONABLE_TRADE:
        raise CertificateVerificationError("expected ActionableTradeCertificate")
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.ACTIONABLE_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"actionable trade missing parents: {sorted(missing)}")
    _forbid_public_market_channel_fill(parent_tuple)


def verify_execution_command(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.EXECUTION_COMMAND:
        raise CertificateVerificationError("expected ExecutionCommandCertificate")
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.EXECUTION_COMMAND_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"execution command missing parents: {sorted(missing)}")


def assert_market_channel_not_fill(cert: DecisionCertificate) -> None:
    if (
        cert.certificate_type == claims.FILL
        and cert.payload.get("source_kind") == claims.PUBLIC_MARKET_CHANNEL_SOURCE
    ):
        raise CertificateVerificationError("public market-channel data cannot produce FillCertificate")
    if (
        cert.certificate_type == claims.FILL_FEASIBILITY
        and cert.payload.get("source_kind") == claims.PUBLIC_MARKET_CHANNEL_SOURCE
    ):
        raise CertificateVerificationError("public market-channel data cannot produce FillFeasibilityEvidence")


def _verify_parent_edges(cert: DecisionCertificate, parents: tuple[DecisionCertificate, ...]) -> None:
    seen_roles: set[str] = set()
    parent_by_hash = {parent.certificate_hash: parent for parent in parents}
    for edge in cert.header.parent_edges:
        if edge.role in seen_roles and edge.required:
            raise CertificateVerificationError(f"duplicate required parent role: {edge.role}")
        seen_roles.add(edge.role)
        parent = parent_by_hash.get(edge.certificate_hash)
        if parent is None:
            raise CertificateVerificationError(f"missing parent for role {edge.role}")
        if parent.certificate_type != edge.certificate_type:
            raise CertificateVerificationError(f"parent type mismatch for role {edge.role}")


def _verify_time_filtration(cert: DecisionCertificate) -> None:
    decision_time = cert.header.decision_time
    for name, value in (
        ("source_available_at", cert.header.source_available_at),
        ("agent_received_at", cert.header.agent_received_at),
        ("persisted_at", cert.header.persisted_at),
        ("max_parent_source_available_at", cert.header.max_parent_source_available_at),
        ("max_parent_agent_received_at", cert.header.max_parent_agent_received_at),
        ("max_parent_persisted_at", cert.header.max_parent_persisted_at),
    ):
        if value is None:
            continue
        if value > decision_time:
            if name.endswith("persisted_at") and cert.header.mode == "REPLAY_COUNTERFACTUAL":
                continue
            raise CertificateVerificationError(f"{name} after decision_time")
    if is_live_like(cert.header.mode):
        required = (
            cert.header.source_available_at,
            cert.header.agent_received_at,
            cert.header.persisted_at,
        )
        if any(value is None for value in required):
            raise CertificateVerificationError("live/no-submit certificate missing filtration timestamp")


def _forbid_no_submit_payload(cert: DecisionCertificate) -> None:
    if cert.payload.get("submitted") is True:
        raise CertificateVerificationError("NO_SUBMIT certificate cannot set submitted=true")
    if cert.payload.get("proof_accepted") is not True:
        raise CertificateVerificationError("NO_SUBMIT decision requires proof_accepted=true")
    for key in ("action_score", "actionable_trade_score", "actionable_executable_trade_score"):
        value = cert.payload.get(key)
        if value is not None and float(value) > 0.0:
            raise CertificateVerificationError(f"NO_SUBMIT certificate cannot carry positive {key}")
    if cert.payload.get("execution_command_id"):
        raise CertificateVerificationError("NO_SUBMIT certificate cannot carry execution command")


def _verify_generated_certificate_semantics(cert: DecisionCertificate) -> None:
    if (
        cert.payload.get("generated_at_decision_time") is True
        and cert.certificate_type != claims.NO_SUBMIT_DECISION
    ):
        raise CertificateVerificationError("generated_at_decision_time is only allowed for generated decision certificates")


def _verify_no_submit_generated_time_semantics(cert: DecisionCertificate) -> None:
    if cert.payload.get("generated_at_decision_time") is not True:
        raise CertificateVerificationError("NO_SUBMIT decision requires generated_at_decision_time=true")
    if cert.payload.get("header_persisted_at_semantics") != "decision_kernel_generated_at_decision_time":
        raise CertificateVerificationError("NO_SUBMIT decision missing generated header persisted_at semantics")
    if cert.payload.get("db_created_at_may_follow_header_persisted_at") is not True:
        raise CertificateVerificationError("NO_SUBMIT decision must declare db_created_at may follow header persisted_at")
    if cert.header.persisted_at != cert.header.decision_time:
        raise CertificateVerificationError("generated NO_SUBMIT decision persisted_at must equal decision_time")


def _verify_forecast_no_submit_semantic_consistency(
    cert: DecisionCertificate,
    parents: tuple[DecisionCertificate, ...],
) -> None:
    parent = _parents_by_type(parents)
    causal = _required_parent_payload(parent, claims.CAUSAL_EVENT)
    source = _required_parent_payload(parent, claims.SOURCE_TRUTH)
    topology = _required_parent_payload(parent, claims.MARKET_TOPOLOGY)
    family = _required_parent_payload(parent, claims.FAMILY_CLOSURE)
    forecast = _required_parent_payload(parent, claims.FORECAST_AUTHORITY)
    calibration = _required_parent_payload(parent, claims.CALIBRATION)
    model_config = _required_parent_payload(parent, claims.MODEL_CONFIG)
    belief = _required_parent_payload(parent, claims.BELIEF)
    executable = _required_parent_payload(parent, claims.EXECUTABLE_SNAPSHOT)
    quote = _required_parent_payload(parent, claims.QUOTE_FEASIBILITY)
    cost = _required_parent_payload(parent, claims.COST_MODEL)
    candidate = _required_parent_payload(parent, claims.CANDIDATE_EVIDENCE)
    fdr = _required_parent_payload(parent, claims.FDR)
    kelly = _required_parent_payload(parent, claims.KELLY_DRY_RUN)
    risk = _required_parent_payload(parent, claims.RISK_LEVEL)

    _require_equal("no_submit.event_id", cert.payload.get("event_id"), "causal.event_id", causal.get("event_id"))
    _require_equal("source_truth.event_id", source.get("event_id"), "causal.event_id", causal.get("event_id"))
    _require_equal("source_truth.causal_snapshot_id", source.get("causal_snapshot_id"), "causal.causal_snapshot_id", causal.get("causal_snapshot_id"))
    _require_equal("source_truth.snapshot_id", source.get("snapshot_id"), "forecast.snapshot_id", forecast.get("snapshot_id"))
    _require_equal("source_truth.source_run_id", source.get("source_run_id"), "forecast.source_run_id", forecast.get("source_run_id"))
    _require_equal("source_truth.source_id", source.get("source_id"), "forecast.forecast_source_id", forecast.get("forecast_source_id"))
    _require_equal("source_truth.payload_hash", source.get("payload_hash"), "causal.payload_hash", causal.get("payload_hash"))
    _require_equal("source_truth.event_source", source.get("event_source"), "causal.source", causal.get("source"))
    _require_equal(
        "source_truth.source_status",
        _normalize_forecast_status(source.get("source_status")),
        "forecast.reader_status",
        _normalize_forecast_status(forecast.get("reader_status")),
    )
    _require_equal("source_truth.source_authority_id", source.get("source_authority_id"), "forecast.reader_authority", forecast.get("reader_authority"))
    _require_equal("source_truth.derived_from_certificate_type", source.get("derived_from_certificate_type"), "ForecastAuthorityCertificate", claims.FORECAST_AUTHORITY)
    _require_equal("source_truth.derived_from_snapshot_id", source.get("derived_from_snapshot_id"), "forecast.snapshot_id", forecast.get("snapshot_id"))
    _require_equal(
        "source_truth.derived_from_reader_status",
        _normalize_forecast_status(source.get("derived_from_reader_status")),
        "forecast.reader_status",
        _normalize_forecast_status(forecast.get("reader_status")),
    )

    _require_equal("market_topology.family_id", topology.get("family_id"), "family_closure.family_id", family.get("family_id"))
    _require_equal("family_closure.family_id", family.get("family_id"), "fdr.fdr_family_id", fdr.get("fdr_family_id"))
    _require_equal("candidate.family_id", candidate.get("family_id"), "family_closure.family_id", family.get("family_id"))
    _require_equal("candidate.selected_token_id", candidate.get("selected_token_id"), "quote.token_id", quote.get("token_id"))
    _require_equal("candidate.selected_token_id", candidate.get("selected_token_id"), "quote.selected_token_id", quote.get("selected_token_id"))
    _require_equal("candidate.selected_token_id", candidate.get("selected_token_id"), "cost.token_id", cost.get("token_id"))
    _require_equal("candidate.condition_id", candidate.get("condition_id"), "executable.condition_id", executable.get("condition_id"))
    _require_equal("candidate.condition_id", candidate.get("condition_id"), "quote.condition_id", quote.get("condition_id"))
    _require_equal("candidate.condition_id", candidate.get("condition_id"), "cost.condition_id", cost.get("condition_id"))
    if candidate.get("hypothesis_id") not in tuple(fdr.get("selected_hypotheses") or ()):
        raise CertificateVerificationError("fdr.selected_hypotheses missing candidate hypothesis_id")
    _require_equal("kelly.cost_basis_id", kelly.get("cost_basis_id"), "cost.cost_basis_id", cost.get("cost_basis_id"))
    _require_equal("belief.forecast_snapshot_id", belief.get("forecast_snapshot_id"), "forecast.snapshot_id", forecast.get("snapshot_id"))
    _require_equal("belief.calibrator_model_key", belief.get("calibrator_model_key"), "calibration.calibrator_model_key", calibration.get("calibrator_model_key"))
    _require_equal("model_config.calibrator_model_key", model_config.get("calibrator_model_key"), "calibration.calibrator_model_key", calibration.get("calibrator_model_key"))
    _require_equal("belief.calibrator_model_hash", belief.get("calibrator_model_hash"), "calibration.model_hash", calibration.get("model_hash"))
    _require_equal("model_config.calibrator_model_hash", model_config.get("calibrator_model_hash"), "calibration.model_hash", calibration.get("model_hash"))
    _require_equal("belief.p_cal_hash", belief.get("p_cal_hash"), "belief.p_cal_vector_hash", belief.get("p_cal_vector_hash"))
    _require_equal("belief.p_live_hash", belief.get("p_live_hash"), "belief.p_live_vector_hash", belief.get("p_live_vector_hash"))
    for field in ("p_cal_vector_hash", "p_live_vector_hash"):
        if belief.get(field) in (None, ""):
            raise CertificateVerificationError(f"belief.{field} missing")
    _require_equal("belief.bin_labels_hash", belief.get("bin_labels_hash"), "family.bin_labels_hash", family.get("bin_labels_hash"))
    _require_equal("risk.final_intent_id", risk.get("final_intent_id"), "no_submit.final_intent_id", cert.payload.get("final_intent_id"))


def _parents_by_type(parents: tuple[DecisionCertificate, ...]) -> dict[str, DecisionCertificate]:
    result: dict[str, DecisionCertificate] = {}
    for parent in parents:
        result.setdefault(parent.certificate_type, parent)
    return result


def _required_parent_payload(parents: dict[str, DecisionCertificate], certificate_type: str) -> dict:
    parent = parents.get(certificate_type)
    if parent is None:
        raise CertificateVerificationError(f"missing semantic parent: {certificate_type}")
    return parent.payload


def _require_equal(left_name: str, left: object, right_name: str, right: object) -> None:
    if left != right:
        raise CertificateVerificationError(f"{left_name} != {right_name}: {left!r} != {right!r}")


def _normalize_forecast_status(status: object) -> str | None:
    raw = str(status or "").strip().upper()
    if raw in {"LIVE_ELIGIBLE", "OK", "EXECUTABLE_FORECAST_READY", "VERIFIED"}:
        return "LIVE_ELIGIBLE"
    return None


def _forbid_public_market_channel_fill(parents: tuple[DecisionCertificate, ...]) -> None:
    for parent in parents:
        assert_market_channel_not_fill(parent)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
