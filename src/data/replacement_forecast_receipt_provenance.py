"""Receipt provenance for replacement forecast attribution.

This module intentionally builds a payload only. It does not write receipts,
settlements, training rows, or live trading state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.data.replacement_forecast_guardrail_report import ReplacementForecastGuardrailReport
from src.data.replacement_forecast_readiness import READY_STATUS, ReplacementForecastReadinessDecision


SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
PRODUCT_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1"
STRATEGY_KEY = SOURCE_ID
RECEIPT_ROLE = "forecast_attribution_only"
SETTLEMENT_AUTHORITY_STATUS = "NO_SETTLEMENT_AUTHORITY"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"
_ALLOWED_TRADE_AUTHORITY_STATUS = {"DIAGNOSTIC_ONLY", "LIVE_AUTHORITY"}
_REQUIRED_DEPENDENCY_ROLES = ("baseline_b0", "aifs_sampled_2t", "openmeteo_ifs9_anchor", "soft_anchor_posterior")
_FORBIDDEN_SETTLEMENT_KEYS = {
    "settlement_value",
    "settlement_outcome",
    "settled_value",
    "resolved_value",
    "truth_authority",
    "settlement_source",
}


def _mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return value


def _read_attr(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _require_text(value: object, *, field_name: str) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{field_name} is required")
    if _FORBIDDEN_TRANSCRIPT_ALIAS in text.lower():
        raise ValueError(f"{field_name} must use full replacement product identity")
    return text


def _reject_settlement_truth_payload(payload: Mapping[str, Any]) -> None:
    for key in payload:
        if key in _FORBIDDEN_SETTLEMENT_KEYS:
            raise ValueError(f"replacement receipt provenance cannot carry settlement truth field: {key}")


def _dependencies_by_role(readiness: ReplacementForecastReadinessDecision) -> dict[str, Mapping[str, Any]]:
    dependencies = readiness.dependency_json.get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("readiness dependency_json.dependencies must be a list")
    by_role: dict[str, Mapping[str, Any]] = {}
    for item in dependencies:
        if not isinstance(item, Mapping) or not item.get("role"):
            continue
        by_role[str(item["role"])] = item
    missing = [role for role in _REQUIRED_DEPENDENCY_ROLES if role not in by_role]
    if missing:
        raise ValueError("replacement receipt provenance requires all dependency roles")
    return by_role


def _dependency_source_run_ids(by_role: Mapping[str, Mapping[str, Any]]) -> dict[str, str | None]:
    return {
        role: None if by_role[role].get("source_run_id") is None else str(by_role[role].get("source_run_id"))
        for role in _REQUIRED_DEPENDENCY_ROLES
    }


def _source_available_at_max(by_role: Mapping[str, Mapping[str, Any]]) -> str:
    values = [str(by_role[role].get("source_available_at") or "") for role in _REQUIRED_DEPENDENCY_ROLES]
    if any(not value for value in values):
        raise ValueError("all replacement dependencies require source_available_at")
    return max(values)


def _guardrail_summary(guardrail_report: ReplacementForecastGuardrailReport | Mapping[str, Any] | None) -> dict[str, Any]:
    if guardrail_report is None:
        return {
            "guardrail_report_status": "NOT_EVALUATED",
            "guardrail_promotion_allowed": False,
            "unresolved_regression_clusters": [],
            "net_delta_after_cost_pnl": None,
        }
    report = guardrail_report.as_dict() if isinstance(guardrail_report, ReplacementForecastGuardrailReport) else dict(_mapping(guardrail_report, field_name="guardrail_report"))
    return {
        "guardrail_report_status": str(report.get("status") or "UNKNOWN"),
        "guardrail_promotion_allowed": bool(report.get("status") == "PASS"),
        "unresolved_regression_clusters": list(report.get("unresolved_regression_clusters") or []),
        "net_delta_after_cost_pnl": report.get("net_delta_after_cost_pnl"),
    }


@dataclass(frozen=True)
class ReplacementForecastReceiptProvenance:
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        _reject_settlement_truth_payload(self.payload)
        for key in ("source_id", "product_id", "strategy_key", "receipt_role", "settlement_authority_status"):
            if not self.payload.get(key):
                raise ValueError(f"{key} is required")
        if self.payload["source_id"] != SOURCE_ID or self.payload["product_id"] != PRODUCT_ID:
            raise ValueError("replacement receipt provenance product identity mismatch")
        if self.payload["receipt_role"] != RECEIPT_ROLE:
            raise ValueError("replacement receipt provenance must be forecast attribution only")
        if self.payload["settlement_authority_status"] != SETTLEMENT_AUTHORITY_STATUS:
            raise ValueError("replacement receipt provenance must not carry settlement authority")
        if self.payload.get("training_allowed") is not False:
            raise ValueError("replacement receipt provenance must not enable training")
        if self.payload.get("promotion_allowed") is not False:
            raise ValueError("replacement receipt provenance cannot authorize promotion")
        if self.payload.get("trade_authority_status") not in _ALLOWED_TRADE_AUTHORITY_STATUS:
            raise ValueError("replacement receipt provenance trade authority status is invalid")
        for key in ("source_id", "product_id", "strategy_key"):
            _require_text(self.payload[key], field_name=key)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def build_replacement_forecast_receipt_provenance(
    *,
    veto_decision: object | Mapping[str, Any],
    readiness: ReplacementForecastReadinessDecision,
    guardrail_report: ReplacementForecastGuardrailReport | Mapping[str, Any] | None = None,
    extra_provenance: Mapping[str, Any] | None = None,
) -> ReplacementForecastReceiptProvenance:
    """Build the receipt tag payload for replacement forecast attribution.

    The returned payload is explicitly non-authoritative for settlement, model
    training, promotion, trade initiation, direction flips, Kelly increases, and
    q_lcb increases.
    """

    if not isinstance(readiness, ReplacementForecastReadinessDecision):
        raise TypeError("readiness must be ReplacementForecastReadinessDecision")
    if readiness.status != READY_STATUS:
        raise ValueError("replacement receipt provenance requires READY readiness")
    trade_authority_status = _require_text(_read_attr(veto_decision, "trade_authority_status"), field_name="trade_authority_status")
    if trade_authority_status not in _ALLOWED_TRADE_AUTHORITY_STATUS:
        raise ValueError("replacement receipt provenance trade authority status is invalid")
    product_id = _require_text(_read_attr(veto_decision, "product_id"), field_name="product_id")
    if product_id != PRODUCT_ID:
        raise ValueError("replacement receipt provenance product identity mismatch")

    by_role = _dependencies_by_role(readiness)
    extra_payload = dict(extra_provenance or {})
    _reject_settlement_truth_payload(extra_payload)
    summary = _guardrail_summary(guardrail_report)
    payload: dict[str, Any] = {
        "source_id": SOURCE_ID,
        "product_id": PRODUCT_ID,
        "strategy_key": STRATEGY_KEY,
        "posterior_id": int(_read_attr(veto_decision, "posterior_id")),
        "readiness_id": readiness.readiness_id,
        "receipt_role": RECEIPT_ROLE,
        "settlement_authority_status": SETTLEMENT_AUTHORITY_STATUS,
        "trade_authority_status": trade_authority_status,
        "training_allowed": False,
        "promotion_allowed": False,
        "baseline_source_run_id": str(readiness.dependency_json.get("baseline_source_run_id") or by_role["baseline_b0"].get("source_run_id") or ""),
        "dependency_source_run_ids": _dependency_source_run_ids(by_role),
        "source_available_at_max": _source_available_at_max(by_role),
        "market_snapshot_id": _require_text(_read_attr(veto_decision, "market_snapshot_id"), field_name="market_snapshot_id"),
        "condition_id": _require_text(_read_attr(veto_decision, "condition_id"), field_name="condition_id"),
        "token_id": _require_text(_read_attr(veto_decision, "token_id"), field_name="token_id"),
        "decision_time": _require_text(_read_attr(veto_decision, "decision_time"), field_name="decision_time"),
        "veto_applied": bool(_read_attr(veto_decision, "veto")),
        "veto_reasons": list(_read_attr(veto_decision, "reasons") or ()),
        "allowed_direction": _require_text(_read_attr(veto_decision, "allowed_direction"), field_name="allowed_direction"),
        "allowed_q_lcb": float(_read_attr(veto_decision, "allowed_q_lcb")),
        "allowed_kelly_fraction": float(_read_attr(veto_decision, "allowed_kelly_fraction")),
        "baseline_direction": _require_text(_read_attr(veto_decision, "baseline_direction"), field_name="baseline_direction"),
        "baseline_q_lcb": float(_read_attr(veto_decision, "baseline_q_lcb")),
        "baseline_kelly_fraction": float(_read_attr(veto_decision, "baseline_kelly_fraction")),
        "authority_limits": {
            "can_increase_q_lcb": False,
            "can_increase_kelly": False,
            "can_flip_direction": False,
            "can_initiate_trade": trade_authority_status == "LIVE_AUTHORITY",
            "can_settle_market": False,
            "can_train_model": False,
        },
        "guardrail_report_status": summary["guardrail_report_status"],
        "guardrail_promotion_allowed": summary["guardrail_promotion_allowed"],
        "unresolved_regression_clusters": summary["unresolved_regression_clusters"],
        "net_delta_after_cost_pnl": summary["net_delta_after_cost_pnl"],
        "extra_provenance": extra_payload,
    }
    return ReplacementForecastReceiptProvenance(payload)
