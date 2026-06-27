"""Decision certificate verifier rules."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Iterable

from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import DecisionCertificate, certificate_hash_for
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.modes import ALLOWED_MODES, is_live_like

FORECAST_LIVE_ELIGIBLE_STATUS = "LIVE_ELIGIBLE"
FORECAST_ACTIONABLE_EVENT_TYPES = frozenset(
    {"FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING"}
)
# mx2t3 carrier-decouple (GATE-1 C): the members_json_source value a posterior-provenance
# FORECAST_AUTHORITY carries when belief is sourced from the multi-model raw_model_forecasts
# fusion (via forecast_posteriors) instead of the cold ensemble_snapshots daily extrema. A cert
# carrying this value is validated by the EQUALLY-STRICT posterior invariant set below — it is a
# DIFFERENT certified completeness authority (the materializer's decorrelated-model + topology
# gates), NOT a relaxation of the ensemble gates.
ENSEMBLE_MEMBERS_JSON_SOURCE = "ensemble_snapshots.daily_extrema"
POSTERIOR_MEMBERS_JSON_SOURCE = "raw_model_forecasts.multimodel"
# Posterior-provenance applied-validations: the posterior-appropriate analogue of
# REQUIRED_FORECAST_VALIDATIONS. The model-count completeness replaces the ensemble member/step
# floors; causality + authority + freshness are unchanged.
REQUIRED_POSTERIOR_FORECAST_VALIDATIONS = frozenset(
    {
        "posterior_complete_by_construction",
        "decorrelated_model_count_floor",
        "causality_status_ok",
        "authority_verified",
        "available_at_not_future",
    }
)
# The minimum decorrelated model count a posterior-provenance forecast authority must carry (the
# SAME floor the spine member producer enforces: fewer than 3 decorrelated members fails closed).
POSTERIOR_MIN_DECORRELATED_MODELS = 3
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
IDENTITY_FALLBACK_CALIBRATION_AUTHORITY = "IDENTITY_FALLBACK_NO_PLATT_BUCKET"
# CERT BRIDGE (2026-06-10, funnel #1 unlock) — first-class replacement-chain calibration
# authority: fused-center bootstrap bounds (q_lcb_basis=fused_center_bootstrap_p05) licensed
# by the settlement-backward coverage verdict. A live-admissible authority (the live gate
# _assert_event_bound_calibration_live_admitted lets it through), so the certificate must
# round-trip it through verification. Its UNEVALUATED sibling
# (FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED) is INTENTIONALLY excluded — like IDENTITY_FALLBACK
# it is evidence-only and the live gate rejects it; it is not minted onto an admitted live
# certificate, so it is not in the approved set.
FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY = "FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE"
DAY0_OBSERVATION_CALIBRATION_AUTHORITY = "DAY0_LIVE_OBSERVATION_HARD_FACT"
# K1.3 (consolidated overhaul 2026-06-11): the ALT-credential carve-out is ONE constant +
# ONE predicate, consumed by BOTH the verifier and the compiler. History: the carve-out
# existed as two independent tuples (verifier + compiler); when FUSED_BOOTSTRAP was added
# to the verifier only, every replacement-chain certificate was falsely rejected at compile
# time with "maturity_level too low" (53/h). Pinned by
# tests/decision_kernel/test_k1_shared_authority_predicates.py.
ALT_CREDENTIAL_CALIBRATION_AUTHORITIES = frozenset(
    {
        IDENTITY_FALLBACK_CALIBRATION_AUTHORITY,
        FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY,
        DAY0_OBSERVATION_CALIBRATION_AUTHORITY,
    }
)


def calibration_maturity_too_low(maturity: int, authority: object) -> bool:
    """ONE shared maturity rule (K1.3). True = reject.

    The maturity_level>3 guard protects REAL Platt models (a placeholder maturity means
    the model never matured past fitting). IDENTITY_FALLBACK and FUSED_BOOTSTRAP are
    alternative calibration authorities whose q never passes through Platt; they carry
    maturity_level=4 as a placeholder, so the guard must not apply to them.
    """
    return int(maturity) > 3 and str(authority) not in ALT_CREDENTIAL_CALIBRATION_AUTHORITIES


APPROVED_CALIBRATION_AUTHORITIES = frozenset(
    {
        "VERIFIED",
        "LIVE",
        "APPROVED",
        IDENTITY_FALLBACK_CALIBRATION_AUTHORITY,
        FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY,
        DAY0_OBSERVATION_CALIBRATION_AUTHORITY,
    }
)
ALLOWED_COST_SOURCES = frozenset({"native_orderbook_ask", "native_orderbook_bid"})
ALLOWED_QUOTE_SOURCE_KINDS = frozenset({"executable_market_snapshot_native_book"})


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
    decision_source = cert.payload.get("decision_source")
    if decision_source != "forecast":
        raise CertificateVerificationError(
            f"unsupported no-submit decision_source for forecast no-submit scope: {decision_source!r}"
        )
    parent_types = {parent.certificate_type for parent in parent_tuple}
    required = claims.NO_SUBMIT_FORECAST_REQUIRED_TYPES
    missing = required - parent_types
    if missing:
        raise CertificateVerificationError(f"no-submit decision missing parents: {sorted(missing)}")
    forbidden = claims.NO_SUBMIT_FORBIDDEN_TYPES & parent_types
    if forbidden:
        raise CertificateVerificationError(f"no-submit decision has forbidden parents: {sorted(forbidden)}")
    _verify_no_submit_generated_time_semantics(cert)
    _verify_forecast_no_submit_semantic_consistency(cert, parent_tuple)


def verify_actionable_trade(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.ACTIONABLE_TRADE:
        raise CertificateVerificationError("expected ActionableTradeCertificate")
    if cert.header.mode != "LIVE":
        raise CertificateVerificationError("actionable trade must use LIVE mode")
    _verify_actionable_payload(cert)
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.ACTIONABLE_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"actionable trade missing parents: {sorted(missing)}")
    event_type = cert.payload.get("event_type")
    if event_type in FORECAST_ACTIONABLE_EVENT_TYPES:
        source_required = {claims.FORECAST_AUTHORITY, claims.CALIBRATION}
    elif event_type == "DAY0_EXTREME_UPDATED":
        source_required = {claims.DAY0_AUTHORITY, claims.ABSORBING_BOUNDARY}
    else:
        raise CertificateVerificationError(f"unsupported actionable event_type: {event_type!r}")
    missing_source = source_required - parent_types
    if missing_source:
        raise CertificateVerificationError(f"actionable trade missing source parents: {sorted(missing_source)}")
    _forbid_public_market_channel_fill(parent_tuple)
    _verify_actionable_parent_consistency(cert, parent_tuple)


def verify_execution_command(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.EXECUTION_COMMAND:
        raise CertificateVerificationError("expected ExecutionCommandCertificate")
    if cert.header.mode != "LIVE":
        raise CertificateVerificationError("execution command must use LIVE mode")
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.EXECUTION_COMMAND_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"execution command missing parents: {sorted(missing)}")
    _verify_execution_command_payload(cert, parent_tuple)


def verify_final_intent(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.FINAL_INTENT:
        raise CertificateVerificationError("expected FinalIntentCertificate")
    if cert.header.mode != "LIVE":
        raise CertificateVerificationError("final intent must use LIVE mode")
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.FINAL_INTENT_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"final intent missing parents: {sorted(missing)}")
    _verify_final_intent_payload(cert, parent_tuple)


def verify_executor_expressibility(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.EXECUTOR_EXPRESSIBILITY:
        raise CertificateVerificationError("expected ExecutorExpressibilityCertificate")
    if cert.header.mode != "LIVE":
        raise CertificateVerificationError("executor expressibility must use LIVE mode")
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.EXECUTOR_EXPRESSIBILITY_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"executor expressibility missing parents: {sorted(missing)}")
    _verify_executor_expressibility_payload(cert, parent_tuple)


def verify_execution_receipt(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.EXECUTION_RECEIPT:
        raise CertificateVerificationError("expected ExecutionReceiptCertificate")
    if cert.header.mode != "LIVE":
        raise CertificateVerificationError("execution receipt must use LIVE mode")
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.EXECUTION_RECEIPT_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"execution receipt missing parents: {sorted(missing)}")
    _verify_execution_receipt_payload(cert, parent_tuple)


def verify_live_cap_transition(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.LIVE_CAP_TRANSITION:
        raise CertificateVerificationError("expected LiveCapTransitionCertificate")
    if cert.header.mode != "LIVE":
        raise CertificateVerificationError("live cap transition must use LIVE mode")
    _verify_live_cap_transition_payload(cert, parent_tuple)


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


def _verify_actionable_payload(cert: DecisionCertificate) -> None:
    payload = cert.payload
    if payload.get("submitted") is True:
        raise CertificateVerificationError("actionable trade cannot be submitted before execution command")
    if payload.get("execution_command_id") not in (None, ""):
        raise CertificateVerificationError("actionable trade cannot carry execution_command_id")
    if payload.get("side_effect_status") != "ACTIONABLE_NOT_SUBMITTED":
        raise CertificateVerificationError("actionable trade side_effect_status must be ACTIONABLE_NOT_SUBMITTED")
    for field in ("action_score", "trade_score", "p_fill_lcb"):
        if _finite_float(payload.get(field), field) <= 0.0:
            raise CertificateVerificationError(f"actionable {field} must be positive")
    for field in ("q_live", "q_lcb_5pct"):
        value = _finite_float(payload.get(field), field)
        if value < 0.0 or value > 1.0:
            raise CertificateVerificationError(f"actionable {field} must be in [0, 1]")
    for field in ("c_fee_adjusted", "c_cost_95pct"):
        value = _finite_float(payload.get(field), field)
        if value <= 0.0 or value >= 1.0:
            raise CertificateVerificationError(f"actionable {field} must be in (0, 1)")
    if payload.get("native_quote_available") is not True:
        raise CertificateVerificationError("actionable native_quote_available must be true")
    required = (
        "event_id",
        "causal_snapshot_id",
        "family_id",
        "candidate_id",
        "condition_id",
        "token_id",
        "direction",
        "executable_snapshot_id",
        "fdr_family_id",
        "kelly_decision_id",
        "risk_decision_id",
        "live_cap_usage_id",
        "final_intent_id",
        "strategy_key",
    )
    for field in required:
        if payload.get(field) in (None, ""):
            raise CertificateVerificationError(f"actionable {field} missing")


def _verify_actionable_parent_consistency(
    cert: DecisionCertificate,
    parents: tuple[DecisionCertificate, ...],
) -> None:
    parent = _parents_by_type(parents)
    payload = cert.payload
    causal = _required_parent_payload(parent, claims.CAUSAL_EVENT)
    topology = _required_parent_payload(parent, claims.MARKET_TOPOLOGY)
    family = _required_parent_payload(parent, claims.FAMILY_CLOSURE)
    executable = _required_parent_payload(parent, claims.EXECUTABLE_SNAPSHOT)
    quote = _required_parent_payload(parent, claims.QUOTE_FEASIBILITY)
    cost = _required_parent_payload(parent, claims.COST_MODEL)
    candidate = _required_parent_payload(parent, claims.CANDIDATE_EVIDENCE)
    fdr = _required_parent_payload(parent, claims.FDR)
    kelly = _required_parent_payload(parent, claims.KELLY_DRY_RUN)
    risk = _required_parent_payload(parent, claims.RISK_LEVEL)
    live_cap = _required_parent_payload(parent, claims.LIVE_CAP)

    _require_equal("actionable.event_id", payload.get("event_id"), "causal.event_id", causal.get("event_id"))
    _require_equal(
        "actionable.causal_snapshot_id",
        payload.get("causal_snapshot_id"),
        "causal.causal_snapshot_id",
        causal.get("causal_snapshot_id"),
    )
    for name, other in (
        ("topology.family_id", topology.get("family_id")),
        ("family.family_id", family.get("family_id")),
        ("fdr.fdr_family_id", fdr.get("fdr_family_id")),
    ):
        _require_equal("actionable.family_id", payload.get("family_id"), name, other)
    _require_equal("actionable.fdr_family_id", payload.get("fdr_family_id"), "fdr.fdr_family_id", fdr.get("fdr_family_id"))
    _require_equal("candidate.family_id", candidate.get("family_id"), "actionable.family_id", payload.get("family_id"))
    _require_equal("actionable.condition_id", payload.get("condition_id"), "candidate.condition_id", candidate.get("condition_id"))
    _require_equal("actionable.condition_id", payload.get("condition_id"), "executable.condition_id", executable.get("condition_id"))
    _require_equal("actionable.condition_id", payload.get("condition_id"), "quote.condition_id", quote.get("condition_id"))
    _require_equal("actionable.condition_id", payload.get("condition_id"), "cost.condition_id", cost.get("condition_id"))
    candidate_token = candidate.get("selected_token_id", candidate.get("token_id"))
    for name, other in (
        ("candidate.selected_token_id", candidate_token),
        ("executable.token_id", executable.get("token_id")),
        ("quote.token_id", quote.get("token_id")),
        ("cost.token_id", cost.get("token_id")),
    ):
        _require_equal("actionable.token_id", payload.get("token_id"), name, other)
    for name, other in (
        ("candidate.direction", candidate.get("direction")),
        ("quote.direction", quote.get("direction")),
        ("cost.direction", cost.get("direction")),
    ):
        if other not in (None, ""):
            _require_equal("actionable.direction", payload.get("direction"), name, other)
    _require_equal("actionable.executable_snapshot_id", payload.get("executable_snapshot_id"), "executable.executable_snapshot_id", executable.get("executable_snapshot_id", executable.get("selected_snapshot_id")))
    if candidate.get("hypothesis_id") not in tuple(fdr.get("selected_hypotheses") or ()):
        raise CertificateVerificationError("fdr.selected_hypotheses missing candidate hypothesis_id")
    _require_equal("kelly.cost_basis_id", kelly.get("cost_basis_id"), "cost.cost_basis_id", cost.get("cost_basis_id"))
    if kelly.get("passed") is not True:
        raise CertificateVerificationError("kelly.passed must be true")
    if risk.get("passed") is not True:
        raise CertificateVerificationError("risk.passed must be true")
    _require_equal("actionable.kelly_decision_id", payload.get("kelly_decision_id"), "kelly.kelly_decision_id", kelly.get("kelly_decision_id"))
    _require_equal("actionable.risk_decision_id", payload.get("risk_decision_id"), "risk.risk_decision_id", risk.get("risk_decision_id"))
    _require_equal("live_cap.event_id", live_cap.get("event_id"), "actionable.event_id", payload.get("event_id"))
    _require_equal("live_cap.usage_id", live_cap.get("usage_id"), "actionable.live_cap_usage_id", payload.get("live_cap_usage_id"))
    if live_cap.get("reservation_status") != "RESERVED":
        raise CertificateVerificationError("live_cap.reservation_status must be RESERVED")
    if live_cap.get("stale_book_directional_strategy") is True:
        raise CertificateVerificationError("stale-book directional strategy parent is forbidden")
    _validate_cost_sources(quote, cost, {"direction": payload.get("direction")})


def _verify_execution_command_payload(
    cert: DecisionCertificate,
    parents: tuple[DecisionCertificate, ...],
) -> None:
    payload = cert.payload
    if payload.get("submitted") is not False:
        raise CertificateVerificationError("execution command must set submitted=false before executor call")
    if payload.get("venue_order_id") not in (None, ""):
        raise CertificateVerificationError("execution command cannot carry venue_order_id before submit")
    parent = _parents_by_type(parents)
    actionable_cert = parent.get(claims.ACTIONABLE_TRADE)
    if actionable_cert is None:
        raise CertificateVerificationError("execution command missing actionable parent")
    actionable = actionable_cert.payload
    final_intent = _required_parent_payload(parent, claims.FINAL_INTENT)
    expressibility = _required_parent_payload(parent, claims.EXECUTOR_EXPRESSIBILITY)
    live_cap = _required_parent_payload(parent, claims.LIVE_CAP)
    pre_submit = _required_parent_payload(parent, claims.PRE_SUBMIT_REVALIDATION)
    if expressibility.get("passed") is not True:
        raise CertificateVerificationError("executor expressibility must pass")
    if live_cap.get("reservation_status") != "RESERVED":
        raise CertificateVerificationError("live cap reservation must be RESERVED")
    for field in ("event_id", "condition_id", "token_id", "direction", "final_intent_id", "strategy_key"):
        _require_equal(f"execution_command.{field}", payload.get(field), f"actionable.{field}", actionable.get(field))
    _require_equal(
        "execution_command.actionable_certificate_hash",
        payload.get("actionable_certificate_hash"),
        "actionable.certificate_hash",
        actionable_cert.certificate_hash,
    )
    _require_equal("final_intent.final_intent_id", final_intent.get("final_intent_id"), "actionable.final_intent_id", actionable.get("final_intent_id"))
    _require_equal("final_intent.token_id", final_intent.get("token_id"), "actionable.token_id", actionable.get("token_id"))
    _require_equal("final_intent.condition_id", final_intent.get("condition_id"), "actionable.condition_id", actionable.get("condition_id"))
    _require_equal("final_intent.strategy_key", final_intent.get("strategy_key"), "actionable.strategy_key", actionable.get("strategy_key"))
    _require_equal("expressibility.strategy_key", expressibility.get("strategy_key"), "final_intent.strategy_key", final_intent.get("strategy_key"))
    _require_equal("live_cap.usage_id", live_cap.get("usage_id"), "actionable.live_cap_usage_id", actionable.get("live_cap_usage_id"))
    _verify_pre_submit_revalidation_for_command(payload, pre_submit, final_intent, live_cap)
    size = _finite_float(payload.get("size"), "execution command size")
    min_order_size = _finite_float(payload.get("min_order_size"), "execution command min_order_size")
    limit_price = _finite_float(payload.get("limit_price"), "execution command limit_price")
    tick_size = _finite_float(payload.get("tick_size"), "execution command tick_size")
    if size <= 0:
        raise CertificateVerificationError("execution command size must be positive")
    if size < min_order_size:
        raise CertificateVerificationError("execution command size below min_order_size")
    # 2026-06-08: the tiny_live notional cap is DELETED. Order size is governed
    # solely by structural fractional-Kelly sizing; there is no max_notional_usd
    # ceiling to verify here. The order<=reserved integrity guard still runs on
    # the FINAL_INTENT (see _verify_final_intent_payload).
    if limit_price <= 0.0 or limit_price >= 1.0:
        raise CertificateVerificationError("execution command limit_price must be in (0, 1)")
    if tick_size <= 0.0:
        raise CertificateVerificationError("execution command tick_size must be positive")
    if not _is_tick_aligned(limit_price, tick_size):
        raise CertificateVerificationError("execution command limit_price not tick-aligned")
    for field in ("executor_name", "execution_command_id", "idempotency_key"):
        if payload.get(field) in (None, ""):
            raise CertificateVerificationError(f"execution command {field} missing")
    _assert_order_type_tuple_coherent(
        payload,
        surface="execution command",
        post_only_key=("post_only", "maker"),
        require_executor_order_type=False,
    )
    if "neg_risk" in actionable and payload.get("neg_risk") != actionable.get("neg_risk"):
        raise CertificateVerificationError("execution command neg_risk mismatch")


def _verify_pre_submit_revalidation_for_command(
    command: dict,
    pre_submit: dict,
    final_intent: dict,
    live_cap: dict,
) -> None:
    for field in (
        "event_id",
        "event_type",
        "final_intent_id",
        "strategy_key",
        "condition_id",
        "token_id",
        "side",
        "direction",
        "order_type",
        "time_in_force",
        "post_only",
        "limit_price",
        "min_order_size",
        "neg_risk",
    ):
        _require_equal(f"pre_submit.{field}", pre_submit.get(field), f"execution_command.{field}", command.get(field))
    # tick_size: normalize both sides to Decimal before comparison — the pre-submit
    # cert carries a float (0.01) while the execution command cert stores a Decimal
    # string ("0.01"). Strict equality 0.01 != "0.01" would always fail. Both sides
    # are coerced via Decimal(str(...)) so 0.01 == "0.01" == Decimal("0.01").
    _ps_tick = pre_submit.get("tick_size")
    _cmd_tick = command.get("tick_size")
    try:
        _ps_tick_d = Decimal(str(_ps_tick)) if _ps_tick is not None else None
        _cmd_tick_d = Decimal(str(_cmd_tick)) if _cmd_tick is not None else None
    except InvalidOperation:
        _ps_tick_d = None
        _cmd_tick_d = None
    if _ps_tick_d != _cmd_tick_d:
        raise CertificateVerificationError(
            f"pre_submit.tick_size != execution_command.tick_size: {_ps_tick!r} != {_cmd_tick!r}"
        )
    _require_equal(
        "pre_submit.live_cap_usage_id",
        pre_submit.get("live_cap_usage_id"),
        "live_cap.usage_id",
        live_cap.get("usage_id"),
    )
    _require_equal(
        "execution_command.aggregate_pre_submit_event_hash",
        command.get("aggregate_pre_submit_event_hash"),
        "pre_submit.aggregate_event_hash",
        pre_submit.get("aggregate_event_hash"),
    )
    if not pre_submit.get("aggregate_event_hash"):
        raise CertificateVerificationError("pre-submit revalidation aggregate_event_hash missing")
    if not command.get("aggregate_execution_command_event_hash"):
        raise CertificateVerificationError("execution command aggregate_execution_command_event_hash missing")
    # would_cross_book must be false for post-only MAKER orders (a crossing post-only
    # would take, violating maker intent / venue post-only rejection). A TAKER
    # (FOK/FAK, post_only is False) is designed to cross to fill immediately, so a
    # crossing book is expected and must not be rejected here.
    if pre_submit.get("post_only") is not False:  # True or missing/None → maker-or-unknown → enforce
        if pre_submit.get("would_cross_book") is not False:
            raise CertificateVerificationError("pre-submit revalidation would_cross_book must be false")
    if pre_submit.get("tick_aligned") is not True:
        raise CertificateVerificationError("pre-submit revalidation tick_aligned must be true")
    if pre_submit.get("size_ok") is not True:
        raise CertificateVerificationError("pre-submit revalidation size_ok must be true")
    for status_field in ("heartbeat_status", "user_ws_status", "venue_connectivity_status", "balance_allowance_status"):
        if pre_submit.get(status_field) != "OK":
            raise CertificateVerificationError(f"pre-submit revalidation {status_field} must be OK")
    for provenance_field in (
        "book_authority_id",
        "book_captured_at",
        "heartbeat_authority_id",
        "heartbeat_checked_at",
        "user_ws_authority_id",
        "user_ws_checked_at",
        "venue_connectivity_authority_id",
        "venue_connectivity_checked_at",
        "balance_allowance_authority_id",
        "balance_allowance_checked_at",
    ):
        if not str(pre_submit.get(provenance_field) or "").strip():
            raise CertificateVerificationError(f"pre-submit revalidation {provenance_field} missing")
    quote_age_ms = _finite_float(pre_submit.get("quote_age_ms"), "pre-submit quote_age_ms")
    max_quote_age_ms = _finite_float(pre_submit.get("max_quote_age_ms", quote_age_ms), "pre-submit max_quote_age_ms")
    if quote_age_ms > max_quote_age_ms:
        raise CertificateVerificationError("pre-submit revalidation quote_age_ms exceeds max_quote_age_ms")
    q_live = _probability_float(pre_submit.get("q_live"), "pre-submit q_live")
    q_lcb = _probability_float(pre_submit.get("q_lcb_5pct"), "pre-submit q_lcb_5pct")
    if q_lcb > q_live:
        raise CertificateVerificationError("pre-submit revalidation q_lcb_5pct exceeds q_live")
    limit_price = _finite_float(pre_submit.get("limit_price"), "pre-submit limit_price")
    expected_edge = _finite_float(pre_submit.get("expected_edge"), "pre-submit expected_edge")
    size = _finite_float(pre_submit.get("size"), "pre-submit size")
    min_expected_profit_usd = _finite_float(
        pre_submit.get("min_expected_profit_usd"), "pre-submit min_expected_profit_usd"
    )
    min_submit_edge_density = _finite_float(
        pre_submit.get("min_submit_edge_density"), "pre-submit min_submit_edge_density"
    )
    if expected_edge <= 0.0:
        raise CertificateVerificationError("pre-submit revalidation expected_edge must be positive")
    submit_edge = q_lcb - limit_price
    if submit_edge <= 0.0:
        raise CertificateVerificationError("pre-submit revalidation submit q_lcb-minus-limit must be positive")
    if expected_edge > submit_edge + 1e-6:
        raise CertificateVerificationError("pre-submit revalidation expected_edge exceeds submit edge")
    if size <= 0.0:
        raise CertificateVerificationError("pre-submit revalidation size must be positive")
    if submit_edge * size + 1e-9 < min_expected_profit_usd:
        raise CertificateVerificationError("pre-submit revalidation expected profit below strategy floor")
    if submit_edge / limit_price + 1e-9 < min_submit_edge_density:
        raise CertificateVerificationError("pre-submit revalidation submit edge density below strategy floor")
    _verify_pre_submit_qkernel_economics(pre_submit, q_live=q_live, q_lcb=q_lcb)


def _verify_final_intent_payload(
    cert: DecisionCertificate,
    parents: tuple[DecisionCertificate, ...],
) -> None:
    payload = cert.payload
    parent = _parents_by_type(parents)
    actionable_cert = parent.get(claims.ACTIONABLE_TRADE)
    if actionable_cert is None:
        raise CertificateVerificationError("final intent missing actionable parent")
    actionable = actionable_cert.payload
    if payload.get("submitted") is True:
        raise CertificateVerificationError("final intent cannot be submitted before execution command")
    if payload.get("venue_order_id") not in (None, ""):
        raise CertificateVerificationError("final intent cannot carry venue_order_id before submit")
    _require_equal(
        "final_intent.actionable_certificate_hash",
        payload.get("actionable_certificate_hash"),
        "actionable.certificate_hash",
        actionable_cert.certificate_hash,
    )
    for field in (
        "event_id",
        "family_id",
        "candidate_id",
        "condition_id",
        "token_id",
        "direction",
        "final_intent_id",
        "executable_snapshot_id",
    ):
        _require_equal(f"final_intent.{field}", payload.get(field), f"actionable.{field}", actionable.get(field))
    _require_equal(
        "final_intent.strategy_key",
        payload.get("strategy_key"),
        "actionable.strategy_key",
        actionable.get("strategy_key"),
    )
    if payload.get("strategy_key") in (None, ""):
        raise CertificateVerificationError("final intent strategy_key missing")
    limit_price = _finite_float(payload.get("limit_price"), "final intent limit_price")
    size = _finite_float(payload.get("size"), "final intent size")
    notional = _finite_float(payload.get("notional_usd"), "final intent notional_usd")
    if size <= 0:
        raise CertificateVerificationError("final intent size must be positive")
    if limit_price <= 0.0 or limit_price >= 1.0:
        raise CertificateVerificationError("final intent limit_price must be in (0, 1)")
    if notional <= 0:
        raise CertificateVerificationError("final intent notional_usd must be positive")
    # Integrity guard (NOT a cap): the order notional must not exceed the
    # Kelly-sized notional that was reserved for this event. This runs
    # unconditionally now that the tiny_live cap-enabled flag is deleted — it is a
    # cert-chain consistency check (order size matches the reservation), not a
    # dollar limit.
    #
    # FLOAT ROUND-TRIP TOLERANCE (live 2026-06-11, Amsterdam 20:26/20:56Z +
    # Lucknow 21:38Z dead-letters): the maker share sizing is
    # size = reserved/price (desired_shares_for_reserved_notional, float
    # contract) and the intent notional is size*price — IEEE754 makes
    # (r/p)*p exceed r by ~1 ULP (~1e-15 relative) for a large fraction of
    # (r, p) pairs, so the strict > comparison hard-killed correctly-sized
    # maker intents at random. The guard's intent is "order matches the
    # reservation", not "bit-exact float equality": a relative 1e-9 tolerance
    # (six orders of magnitude above the ULP noise, ~$1e-8 on a $15 order)
    # passes the round-trip artifact while any MATERIAL excess still raises.
    reserved_notional = actionable.get("live_cap_reserved_notional_usd")
    if reserved_notional is not None:
        reserved_f = _finite_float(reserved_notional, "actionable live_cap_reserved_notional_usd")
        if notional > reserved_f * (1.0 + 1e-9) + 1e-12:
            raise CertificateVerificationError("final intent notional_usd exceeds live cap reserved notional")
    _assert_order_type_tuple_coherent(payload, surface="final intent")
    if payload.get("source") != "existing_final_intent_builder":
        raise CertificateVerificationError("final intent source must be existing_final_intent_builder")
    # WALL #1 (2026-06-01): passive_maker_context is a MAKER-ONLY executor-native field.
    # A taker FOK/FAK carries no maker context (the cert builder emits None for taker);
    # requiring it for taker was the verifier-layer instance of the same maker-only
    # coupling that produced the dominant live wall. Derive the mode from the (already
    # coherence-checked) order-type tuple and require the maker context iff maker.
    _is_taker_intent = (
        payload.get("order_type") in _TAKER_ORDER_TYPES
        or payload.get("time_in_force") in _TAKER_TIF
    )
    required_executor_native_fields = [
        "executable_snapshot_hash",
        "cost_basis_hash",
        "cost_basis_id",
        "decision_source_context",
    ]
    if not _is_taker_intent:
        required_executor_native_fields.append("passive_maker_context")
    for field in required_executor_native_fields:
        if payload.get(field) in (None, "", {}):
            raise CertificateVerificationError(f"final intent missing executor-native field: {field}")
    expected_cost_basis_id = "cost_basis:" + str(payload["cost_basis_hash"])[:16]
    if payload["cost_basis_id"] != expected_cost_basis_id:
        raise CertificateVerificationError("final intent cost_basis_id mismatch")


def _verify_executor_expressibility_payload(
    cert: DecisionCertificate,
    parents: tuple[DecisionCertificate, ...],
) -> None:
    payload = cert.payload
    parent = _parents_by_type(parents)
    final_intent = _required_parent_payload(parent, claims.FINAL_INTENT)
    executable = _required_parent_payload(parent, claims.EXECUTABLE_SNAPSHOT)
    live_cap = _required_parent_payload(parent, claims.LIVE_CAP)
    if payload.get("can_express") is not True:
        raise CertificateVerificationError("executor expressibility can_express must be true")
    if payload.get("passed") is not True:
        raise CertificateVerificationError("executor expressibility passed must be true")
    if payload.get("reason_code") not in (None, "", "OK"):
        raise CertificateVerificationError("executor expressibility reason_code must be empty or OK")
    if payload.get("executor_native_intent_hash") in (None, ""):
        raise CertificateVerificationError("executor expressibility requires executor_native_intent_hash")
    for field in ("final_intent_id", "token_id", "condition_id", "direction", "order_type", "time_in_force"):
        _require_equal(f"executor_expressibility.{field}", payload.get(field), f"final_intent.{field}", final_intent.get(field))
    if executable.get("condition_id") not in (None, ""):
        _require_equal("executor_expressibility.condition_id", payload.get("condition_id"), "executable.condition_id", executable.get("condition_id"))
    if executable.get("token_id") not in (None, ""):
        _require_equal("executor_expressibility.token_id", payload.get("token_id"), "executable.token_id", executable.get("token_id"))
    if "neg_risk" in executable and payload.get("neg_risk") != executable.get("neg_risk"):
        raise CertificateVerificationError("executor expressibility neg_risk mismatch")
    _require_equal("live_cap.usage_id", live_cap.get("usage_id"), "final_intent.live_cap_usage_id", final_intent.get("live_cap_usage_id"))
    if live_cap.get("reservation_status") != "RESERVED":
        raise CertificateVerificationError("executor expressibility live cap must be RESERVED")
    tick_size = _finite_float(payload.get("tick_size"), "executor expressibility tick_size")
    limit_price = _finite_float(payload.get("limit_price"), "executor expressibility limit_price")
    size = _finite_float(payload.get("size"), "executor expressibility size")
    min_order_size = _finite_float(payload.get("min_order_size"), "executor expressibility min_order_size")
    if tick_size <= 0:
        raise CertificateVerificationError("executor expressibility tick_size must be positive")
    if not _is_tick_aligned(limit_price, tick_size):
        raise CertificateVerificationError("executor expressibility limit_price not tick-aligned")
    if size < min_order_size:
        raise CertificateVerificationError("executor expressibility size below min_order_size")
    _assert_order_type_tuple_coherent(
        payload,
        surface="executor expressibility",
        require_executor_order_type=False,
    )


def _verify_execution_receipt_payload(
    cert: DecisionCertificate,
    parents: tuple[DecisionCertificate, ...],
) -> None:
    payload = cert.payload
    parent = _parents_by_type(parents)
    command = _required_parent_payload(parent, claims.EXECUTION_COMMAND)
    for field in ("event_id", "final_intent_id", "execution_command_id", "idempotency_key"):
        _require_equal(f"execution_receipt.{field}", payload.get(field), f"execution_command.{field}", command.get(field))
    status = payload.get("status")
    allowed = {
        "NOT_SUBMITTED_DRY_RUN",
        "SUBMIT_DISABLED",
        "SUBMITTED",
        "ACCEPTED",
        "RESTING",
        "REJECTED",
        "TIMEOUT_UNKNOWN",
        "PRE_SUBMIT_ERROR",
        "POST_SUBMIT_UNKNOWN",
    }
    if status not in allowed:
        raise CertificateVerificationError(f"execution receipt status unsupported: {status!r}")
    if status in {"SUBMIT_DISABLED", "NOT_SUBMITTED_DRY_RUN"}:
        if payload.get("venue_order_id") not in (None, ""):
            raise CertificateVerificationError("execution receipt dry status cannot carry venue_order_id")
        if payload.get("submit_started_at") not in (None, "") or payload.get("submit_finished_at") not in (None, ""):
            raise CertificateVerificationError("execution receipt dry status cannot carry submit timestamps")
    if status in {"SUBMITTED", "ACCEPTED", "RESTING"}:
        if payload.get("submit_started_at") in (None, "") or payload.get("submit_finished_at") in (None, ""):
            raise CertificateVerificationError("execution receipt submitted status requires submit timestamps")
    if status in {"TIMEOUT_UNKNOWN", "POST_SUBMIT_UNKNOWN"} and payload.get("reconciliation_followup_required") is not True:
        raise CertificateVerificationError(f"execution receipt {status} requires reconciliation follow-up")
    if status == "POST_SUBMIT_UNKNOWN":
        if payload.get("venue_call_started") is not True:
            raise CertificateVerificationError("execution receipt POST_SUBMIT_UNKNOWN requires venue_call_started=true")
        if payload.get("side_effect_known") is not False:
            raise CertificateVerificationError("execution receipt POST_SUBMIT_UNKNOWN requires side_effect_known=false")


def _verify_live_cap_transition_payload(
    cert: DecisionCertificate,
    parents: tuple[DecisionCertificate, ...],
) -> None:
    payload = cert.payload
    parent = _parents_by_type(parents)
    live_cap = _required_parent_payload(parent, claims.LIVE_CAP)
    receipt_cert = parent.get(claims.EXECUTION_RECEIPT)
    if receipt_cert is None:
        raise CertificateVerificationError("live cap transition missing ExecutionReceiptCertificate parent")
    receipt = receipt_cert.payload
    for field in ("event_id", "final_intent_id", "execution_command_id"):
        expected = live_cap.get(field) if field == "event_id" else receipt.get(field)
        _require_equal(f"live_cap_transition.{field}", payload.get(field), f"parent.{field}", expected)
    _require_equal("live_cap_transition.usage_id", payload.get("usage_id"), "live_cap.usage_id", live_cap.get("usage_id"))
    _require_equal(
        "live_cap_transition.execution_receipt_hash",
        payload.get("execution_receipt_hash"),
        "execution_receipt.certificate_hash",
        receipt_cert.certificate_hash,
    )
    if payload.get("from_status") != "RESERVED":
        raise CertificateVerificationError("live cap transition from_status must be RESERVED")
    to_status = payload.get("to_status")
    if to_status not in {"RELEASED", "CONSUMED", "PENDING_RECONCILE"}:
        raise CertificateVerificationError(f"live cap transition status unsupported: {to_status!r}")
    receipt_status = receipt.get("status")
    expected_by_receipt = {
        "SUBMIT_DISABLED": "RELEASED",
        "NOT_SUBMITTED_DRY_RUN": "RELEASED",
        "REJECTED": "RELEASED",
        "PRE_SUBMIT_ERROR": "RELEASED",
        "POST_SUBMIT_UNKNOWN": "PENDING_RECONCILE",
        "SUBMITTED": "CONSUMED",
        "TIMEOUT_UNKNOWN": "PENDING_RECONCILE",
    }
    expected_to_status = expected_by_receipt.get(str(receipt_status))
    if expected_to_status is None:
        raise CertificateVerificationError(f"live cap transition receipt status unsupported: {receipt_status!r}")
    if to_status != expected_to_status:
        raise CertificateVerificationError("live cap transition status does not match execution receipt")
    projection_status = payload.get("projection_status")
    if to_status == "PENDING_RECONCILE":
        if projection_status != "RESERVED":
            raise CertificateVerificationError("pending reconcile keeps live cap projection RESERVED")
    elif projection_status != to_status:
        raise CertificateVerificationError("live cap transition projection_status mismatch")
    if not payload.get("transition_reason"):
        raise CertificateVerificationError("live cap transition requires transition_reason")
    if not payload.get("aggregate_cap_transition_event_hash"):
        raise CertificateVerificationError("live cap transition aggregate_cap_transition_event_hash missing")


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
    # source_truth.snapshot_id is the CAUSAL trigger snapshot (provenance), NOT the reader-elected
    # executable snapshot. The reader may elect a snapshot that differs from the causal one when the
    # causal cycle's source_run is still re-ingesting members (see
    # event_reactor_adapter._forecast_snapshot_row_for_event). The executable-authority binding is
    # carried by source_truth.derived_from_snapshot_id == forecast.snapshot_id (below) and
    # belief.forecast_snapshot_id == forecast.snapshot_id; conflating snapshot_id with the elected id
    # here re-introduces the FORECAST_READER_SNAPSHOT_MISMATCH leak.
    _require_equal("source_truth.snapshot_id", source.get("snapshot_id"), "causal.causal_snapshot_id", causal.get("causal_snapshot_id"))
    # WAVE-1 W1-T3: dual-chain source_run binding (gated; legacy single-chain
    # equality preserved when the flag is OFF or derived_from_source_run_id absent).
    _bind_source_run_chains(source, forecast)
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
    _require_equal("belief.members_json_hash", belief.get("members_json_hash"), "forecast.members_json_hash", forecast.get("members_json_hash"))
    _require_equal("forecast.bin_labels_hash", forecast.get("bin_labels_hash"), "family.bin_labels_hash", family.get("bin_labels_hash"))
    _require_equal("forecast.members_extrema_metric_identity", forecast.get("members_extrema_metric_identity"), "family.metric", family.get("metric"))
    _require_equal("forecast.target_local_date", forecast.get("target_local_date"), "family.target_date", family.get("target_date"))
    _require_equal("risk.final_intent_id", risk.get("final_intent_id"), "no_submit.final_intent_id", cert.payload.get("final_intent_id"))
    _verify_no_submit_projection_hash(cert)
    _validate_forecast_authority_payload(forecast)
    _validate_calibration_payload(calibration, model_config, forecast, decision_time=cert.header.decision_time)
    _validate_unit_authority(forecast, belief, family)
    _validate_cost_sources(quote, cost, candidate)


def _verify_no_submit_projection_hash(cert: DecisionCertificate) -> None:
    projection = {
        "event_id": cert.payload.get("event_id"),
        "final_intent_id": cert.payload.get("final_intent_id"),
        "side_effect_status": cert.payload.get("side_effect_status"),
        "proof_accepted": cert.payload.get("proof_accepted"),
        "submitted": cert.payload.get("submitted"),
        "executable_snapshot_id": cert.payload.get("executable_snapshot_id"),
    }
    expected_hash = stable_hash(projection)
    if cert.payload.get("projection_hash") != expected_hash:
        raise CertificateVerificationError("no-submit projection_hash mismatch")


def _validate_forecast_authority_payload(forecast: dict) -> None:
    status = _normalize_forecast_status(forecast.get("reader_status"))
    if status != FORECAST_LIVE_ELIGIBLE_STATUS:
        raise CertificateVerificationError("forecast.reader_status is not live eligible")
    reason = forecast.get("reader_reason_code")
    if reason not in (None, "", "OK"):
        raise CertificateVerificationError("forecast.reader_reason_code must be empty for verified forecast")
    # mx2t3 carrier-decouple (GATE-1 C): a posterior-provenance forecast authority carries
    # raw_model_forecasts multi-model fusion belief (via forecast_posteriors), NOT ensemble daily
    # extrema. It has no ensemble member array / step coverage; its completeness is the
    # materializer's decorrelated-model + topology gates. Validate it with the EQUALLY-STRICT
    # posterior invariant set and return — the ensemble branch below is UNCHANGED for ensemble
    # provenance. This reads a DIFFERENT certified authority, it does NOT weaken the ensemble gates.
    if forecast.get("members_json_source") == POSTERIOR_MEMBERS_JSON_SOURCE:
        _validate_posterior_forecast_authority_payload(forecast)
        return
    required_scalars = (
        "coverage_readiness_status",
        "coverage_completeness_status",
        "source_run_completeness_status",
        "expected_members",
        "observed_members",
        "members_extrema_metric_identity",
        "temperature_metric",
        "members_json_source",
        "members_json_hash",
        "members_extrema_transform",
        "target_local_date",
        "city_timezone",
        "local_date_window_hash",
        "bin_labels_hash",
    )
    for field in required_scalars:
        if forecast.get(field) in (None, ""):
            raise CertificateVerificationError(f"forecast.{field} missing")
    if forecast.get("coverage_readiness_status") != "LIVE_ELIGIBLE":
        raise CertificateVerificationError("forecast.coverage_readiness_status is not LIVE_ELIGIBLE")
    if forecast.get("coverage_completeness_status") != "COMPLETE":
        raise CertificateVerificationError("forecast.coverage_completeness_status is not COMPLETE")
    source_run_completeness = str(forecast.get("source_run_completeness_status") or "")
    if source_run_completeness not in {"COMPLETE", "PARTIAL"}:
        raise CertificateVerificationError("forecast.source_run_completeness_status is not COMPLETE or PARTIAL")
    if source_run_completeness == "PARTIAL":
        source_run_status = str(forecast.get("source_run_status") or "")
        if source_run_status not in {"SUCCESS", "PARTIAL"}:
            raise CertificateVerificationError("forecast.source_run_status is not SUCCESS or PARTIAL for PARTIAL source_run")
    required_steps = tuple(forecast.get("required_steps") or ())
    observed_steps = tuple(forecast.get("observed_steps") or ())
    if not required_steps:
        raise CertificateVerificationError("forecast.required_steps missing")
    if not set(required_steps).issubset(set(observed_steps)):
        raise CertificateVerificationError("forecast.observed_steps missing required steps")
    if int(forecast.get("observed_members")) < int(forecast.get("expected_members")):
        raise CertificateVerificationError("forecast.observed_members below expected_members")
    validations = {str(item) for item in tuple(forecast.get("applied_validations") or ())}
    if not validations:
        raise CertificateVerificationError("forecast.applied_validations missing")
    missing = REQUIRED_FORECAST_VALIDATIONS - validations
    if missing:
        raise CertificateVerificationError(f"forecast.applied_validations missing required validations: {sorted(missing)}")
    if forecast.get("members_extrema_metric_identity") != forecast.get("temperature_metric"):
        raise CertificateVerificationError("forecast.members_extrema_metric_identity mismatch")
    if forecast.get("members_json_source") != ENSEMBLE_MEMBERS_JSON_SOURCE:
        raise CertificateVerificationError("forecast.members_json_source is not authoritative daily extrema")
    expected_transform = _expected_members_extrema_transform(forecast.get("temperature_metric"))
    if forecast.get("members_extrema_transform") != expected_transform:
        raise CertificateVerificationError("forecast.members_extrema_transform mismatch")


def _validate_posterior_forecast_authority_payload(forecast: dict) -> None:
    """EQUALLY-STRICT validation for a posterior-provenance FORECAST_AUTHORITY (mx2t3 decouple).

    The posterior carries multi-model raw_model_forecasts fusion belief, NOT ensemble daily
    extrema, so the ensemble-only floors (51-member array, hourly step coverage) do not apply.
    The posterior's completeness is a DIFFERENT certified authority — the materializer's
    decorrelated-model + topology gates — and is enforced here with NO LESS rigor:
      * coverage/readiness COMPLETE + LIVE_ELIGIBLE (same as ensemble);
      * a stable posterior identity (posterior_identity_hash) AND members_json_hash present;
      * the decorrelated model count (expected==observed==count) AND count >= the floor the spine
        member producer itself enforces (>=3 decorrelated members);
      * the posterior-appropriate applied_validations set (model-count completeness replaces the
        ensemble member/step floors; causality + authority + freshness unchanged).
    """
    for field in (
        "coverage_readiness_status",
        "coverage_completeness_status",
        "temperature_metric",
        "members_extrema_metric_identity",
        "members_json_source",
        "members_json_hash",
        "members_extrema_transform",
        "target_local_date",
        "city_timezone",
        "local_date_window_hash",
        "bin_labels_hash",
        "posterior_identity_hash",
        "source_cycle_time",
        "source_run_id",
        "forecast_source_id",
    ):
        if forecast.get(field) in (None, ""):
            raise CertificateVerificationError(f"forecast.{field} missing (posterior)")
    if forecast.get("coverage_readiness_status") != "LIVE_ELIGIBLE":
        raise CertificateVerificationError("forecast.coverage_readiness_status is not LIVE_ELIGIBLE")
    if forecast.get("coverage_completeness_status") != "COMPLETE":
        raise CertificateVerificationError("forecast.coverage_completeness_status is not COMPLETE")
    try:
        expected_models = int(forecast.get("expected_members"))
        observed_models = int(forecast.get("observed_members"))
    except (TypeError, ValueError):
        raise CertificateVerificationError("forecast.expected/observed decorrelated model count missing")
    if observed_models < expected_models:
        raise CertificateVerificationError("forecast.observed_members below expected_members (posterior)")
    if observed_models < POSTERIOR_MIN_DECORRELATED_MODELS:
        raise CertificateVerificationError(
            f"forecast.observed_members below posterior decorrelated-model floor "
            f"({POSTERIOR_MIN_DECORRELATED_MODELS})"
        )
    validations = {str(item) for item in tuple(forecast.get("applied_validations") or ())}
    if not validations:
        raise CertificateVerificationError("forecast.applied_validations missing (posterior)")
    missing = REQUIRED_POSTERIOR_FORECAST_VALIDATIONS - validations
    if missing:
        raise CertificateVerificationError(
            f"forecast.applied_validations missing required posterior validations: {sorted(missing)}"
        )
    if forecast.get("members_extrema_metric_identity") != forecast.get("temperature_metric"):
        raise CertificateVerificationError("forecast.members_extrema_metric_identity mismatch")
    expected_transform = _expected_members_extrema_transform(forecast.get("temperature_metric"))
    if forecast.get("members_extrema_transform") != expected_transform:
        raise CertificateVerificationError("forecast.members_extrema_transform mismatch")


def _validate_calibration_payload(
    calibration: dict,
    model_config: dict,
    forecast: dict,
    *,
    decision_time: datetime,
) -> None:
    authority = calibration.get("authority")
    if authority not in APPROVED_CALIBRATION_AUTHORITIES:
        raise CertificateVerificationError("calibration.authority is not approved")
    maturity = calibration.get("maturity_level")
    if maturity in (None, ""):
        raise CertificateVerificationError("calibration.maturity_level missing")
    # K1.3: ONE shared maturity rule — see calibration_maturity_too_low (module level).
    if calibration_maturity_too_low(int(maturity), authority):
        raise CertificateVerificationError("calibration.maturity_level too low for live/no-submit")
    input_space = calibration.get("input_space")
    expected_input_space = model_config.get("calibration_input_space")
    if input_space in (None, ""):
        raise CertificateVerificationError("calibration.input_space missing")
    if expected_input_space in (None, ""):
        raise CertificateVerificationError("model_config.calibration_input_space missing")
    if input_space != expected_input_space:
        raise CertificateVerificationError("calibration.input_space != model_config.calibration_input_space")
    _require_equal(
        "calibration.horizon_profile",
        calibration.get("horizon_profile"),
        "forecast.horizon_profile",
        forecast.get("horizon_profile"),
    )
    for field in ("training_cutoff", "model_available_at"):
        parsed = _parse_dt(calibration.get(field))
        if parsed is None:
            raise CertificateVerificationError(f"calibration.{field} missing")
        if parsed > decision_time:
            raise CertificateVerificationError(f"calibration.{field} after decision_time")


def _validate_unit_authority(forecast: dict, belief: dict, family: dict) -> None:
    unit = forecast.get("unit")
    if unit not in {"F", "C"}:
        raise CertificateVerificationError("forecast.unit missing or unsupported")
    if belief.get("unit") != unit:
        raise CertificateVerificationError("belief.unit != forecast.unit")
    units = tuple(family.get("bin_units") or ())
    if unit not in units:
        raise CertificateVerificationError("forecast.unit not present in family.bin_units")
    if forecast.get("unit_authority_source") in (None, ""):
        raise CertificateVerificationError("forecast.unit_authority_source missing")
    if belief.get("unit_authority_source") != forecast.get("unit_authority_source"):
        raise CertificateVerificationError("belief.unit_authority_source != forecast.unit_authority_source")


def _validate_cost_sources(quote: dict, cost: dict, candidate: dict) -> None:
    expected_cost_source = _expected_cost_source_for_direction(candidate.get("direction"))
    for label, payload in (("quote", quote), ("cost", cost)):
        if payload.get("forbidden_cost_source") is not False:
            raise CertificateVerificationError(f"{label}.forbidden_cost_source must be false")
        if payload.get("cost_source") not in ALLOWED_COST_SOURCES:
            raise CertificateVerificationError(f"{label}.cost_source is not native orderbook")
        if payload.get("cost_source") != expected_cost_source:
            raise CertificateVerificationError(f"{label}.cost_source does not match direction")
        if payload.get("quote_source_kind") not in ALLOWED_QUOTE_SOURCE_KINDS:
            raise CertificateVerificationError(f"{label}.quote_source_kind is not executable native book")


def _expected_cost_source_for_direction(direction: object) -> str:
    if direction in {"buy_yes", "buy_no"}:
        return "native_orderbook_ask"
    if direction in {"sell_yes", "sell_no"}:
        return "native_orderbook_bid"
    raise CertificateVerificationError("candidate.direction unsupported for cost source")


def _finite_float(value: object, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CertificateVerificationError(f"{field_name} must be finite") from exc
    if not math.isfinite(parsed):
        raise CertificateVerificationError(f"{field_name} must be finite")
    return parsed


def _probability_float(value: object, field_name: str) -> float:
    parsed = _finite_float(value, field_name)
    if parsed < 0.0 or parsed > 1.0:
        raise CertificateVerificationError(f"{field_name} must be in [0, 1]")
    return parsed


def _native_curve_side_for_direction(direction: object) -> str | None:
    normalized = str(direction or "").strip().lower()
    if normalized.endswith("_yes"):
        return "YES"
    if normalized.endswith("_no"):
        return "NO"
    return None


def _verify_pre_submit_qkernel_economics(
    pre_submit: dict,
    *,
    q_live: float,
    q_lcb: float,
) -> None:
    economics = pre_submit.get("qkernel_execution_economics")
    if economics in (None, ""):
        raise CertificateVerificationError("pre-submit qkernel_execution_economics missing")
    if not isinstance(economics, dict):
        raise CertificateVerificationError("pre-submit qkernel_execution_economics must be object")
    route_id = str(economics.get("route_id") or "").upper()
    route_type = str(economics.get("route_type") or "").lower()
    if route_type != "direct" and not route_id.startswith("DIRECT_"):
        return
    route = economics.get("route") if isinstance(economics.get("route"), dict) else {}
    native_side = _native_curve_side_for_direction(pre_submit.get("direction"))
    qkernel_side = str(route.get("side") or economics.get("side") or "").upper()
    if qkernel_side and native_side is not None and qkernel_side != native_side:
        raise CertificateVerificationError("pre-submit qkernel side must match submit direction")
    payoff_q_point = _probability_float(
        economics.get("payoff_q_point"), "pre-submit qkernel payoff_q_point"
    )
    payoff_q_lcb = _probability_float(
        economics.get("payoff_q_lcb"), "pre-submit qkernel payoff_q_lcb"
    )
    if payoff_q_point > q_live + 1e-6:
        raise CertificateVerificationError("pre-submit qkernel payoff_q_point exceeds q_live")
    if payoff_q_lcb > q_lcb + 1e-6:
        raise CertificateVerificationError("pre-submit qkernel payoff_q_lcb exceeds q_lcb_5pct")


def _is_tick_aligned(price: float, tick_size: float) -> bool:
    try:
        price_decimal = Decimal(str(price))
        tick_decimal = Decimal(str(tick_size))
        if tick_decimal <= 0:
            return False
        return price_decimal.remainder_near(tick_decimal) == 0
    except (InvalidOperation, ValueError):
        return False


_MAKER_ORDER_TYPES = {"LIMIT", "GTC_LIMIT", "POST_ONLY_LIMIT"}
_MAKER_TIF = {"GTC", "GTD"}
_TAKER_ORDER_TYPES = {"FOK_LIMIT", "FAK_LIMIT"}
_TAKER_TIF = {"FOK", "FAK"}


def _assert_order_type_tuple_coherent(
    payload: dict,
    *,
    surface: str,
    post_only_key: tuple[str, ...] = ("post_only",),
    require_executor_order_type: bool = True,
) -> None:
    """Authorize a maker OR taker order-type tuple, never a mixed one.

    The governor-decided mode is the authority (Fitz #4 provenance): the cert
    builder is the sole emitter of a taker tuple, and it only emits one for
    order_mode == "TAKER". Here we re-derive the mode from the tuple and demand
    it be internally consistent — a maker tuple is post-only GTC/GTD; a taker
    tuple is FOK/FAK marketable with post_only/maker_intent False. Any mixed
    tuple (e.g. post_only=True with time_in_force=FOK) is rejected, so widening
    to taker can NEVER blanket-allow a malformed passive order.
    """
    order_type = payload.get("order_type")
    tif = payload.get("time_in_force")
    post_only = None
    for key in post_only_key:
        if key in payload:
            post_only = payload.get(key)
            break
    maker_intent = payload.get("maker_intent", post_only)

    is_taker = (order_type in _TAKER_ORDER_TYPES) or (tif in _TAKER_TIF)
    if is_taker:
        if order_type not in _TAKER_ORDER_TYPES:
            raise CertificateVerificationError(f"{surface} taker order_type unsupported")
        if tif not in _TAKER_TIF:
            raise CertificateVerificationError(f"{surface} taker time_in_force unsupported")
        if post_only is not False:
            raise CertificateVerificationError(f"{surface} taker order must have post_only=False")
        if maker_intent not in (False, None):
            raise CertificateVerificationError(f"{surface} taker order must have maker_intent=False")
        if require_executor_order_type and payload.get("executor_order_type") not in _TAKER_TIF:
            raise CertificateVerificationError(f"{surface} taker executor_order_type unsupported")
        return
    if order_type not in _MAKER_ORDER_TYPES:
        raise CertificateVerificationError(f"{surface} order_type unsupported")
    if tif not in _MAKER_TIF:
        raise CertificateVerificationError(f"{surface} time_in_force unsupported")
    if post_only is not True or (maker_intent is not True and "maker_intent" in payload):
        raise CertificateVerificationError(f"{surface} must preserve passive maker executor law")
    if require_executor_order_type and payload.get("executor_order_type") not in _MAKER_TIF:
        raise CertificateVerificationError(f"{surface} executor_order_type unsupported")


def _expected_members_extrema_transform(metric: object) -> str:
    if metric == "high":
        return "daily_max"
    if metric == "low":
        return "daily_min"
    raise CertificateVerificationError("forecast.temperature_metric unsupported for members extrema transform")


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


def _bind_source_run_chains(source: dict, forecast: dict) -> None:
    """Verifier-side mirror of compiler.bind_source_run_chains (WAVE-1 W1-T3).

    Uses the SAME flag reader (compiler._dual_chain_source_run_enabled) so the
    compiler and verifier cannot disagree on whether the dual-chain relaxation is
    in effect. Raises CertificateVerificationError (verifier's error type) rather
    than ValueError. See compiler.bind_source_run_chains for the full rationale.
    """
    from src.decision_kernel.compiler import _dual_chain_source_run_enabled

    derived = source.get("derived_from_source_run_id")
    if _dual_chain_source_run_enabled() and derived not in (None, ""):
        # Executable chain binds to the reader-elected run.
        _require_equal(
            "source_truth.derived_from_source_run_id",
            derived,
            "forecast.source_run_id",
            forecast.get("source_run_id"),
        )
        if source.get("source_run_id") in (None, ""):
            raise CertificateVerificationError(
                "source_truth.source_run_id missing (causal chain)"
            )
        return
    _require_equal(
        "source_truth.source_run_id",
        source.get("source_run_id"),
        "forecast.source_run_id",
        forecast.get("source_run_id"),
    )


def _normalize_forecast_status(status: object) -> str | None:
    raw = str(status or "").strip().upper()
    if raw in {"LIVE_ELIGIBLE", "OK", "EXECUTABLE_FORECAST_READY", "VERIFIED"}:
        return "LIVE_ELIGIBLE"
    return None


def _parse_dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _utc(value)
    try:
        return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _forbid_public_market_channel_fill(parents: tuple[DecisionCertificate, ...]) -> None:
    for parent in parents:
        assert_market_channel_not_fill(parent)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
