"""Shadow event payload enrichment for replacement forecast provenance."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping

from src.data.replacement_forecast_bundle_reader import ReplacementForecastPosteriorBundle
from src.data.replacement_forecast_readiness import READY_STATUS, ReplacementForecastReadinessDecision


_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastEventPayload:
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        replacement = self.payload.get("replacement_forecast")
        if not isinstance(replacement, Mapping):
            raise ValueError("replacement_forecast payload section is required")
        for key in ("source_id", "product_id", "data_version", "strategy_key"):
            value = str(replacement.get(key) or "")
            if not value:
                raise ValueError(f"replacement_forecast.{key} is required")
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"replacement_forecast.{key} must use full product identity")
        if replacement.get("trade_authority_status") not in {"SHADOW_ONLY", "SHADOW_VETO_ONLY"}:
            raise ValueError("replacement forecast event payload must remain shadow/veto only")
        if replacement.get("training_allowed") is not False:
            raise ValueError("replacement forecast event payload must not enable training authority")

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _payload_dict(base_payload: object | Mapping[str, Any]) -> dict[str, Any]:
    if dataclasses.is_dataclass(base_payload):
        return dataclasses.asdict(base_payload)
    if isinstance(base_payload, Mapping):
        return dict(base_payload)
    raise TypeError("base_payload must be a dataclass or mapping")


def _dependency_source_run_ids(readiness: ReplacementForecastReadinessDecision) -> dict[str, str | None]:
    dependencies = readiness.dependency_json.get("dependencies")
    if not isinstance(dependencies, list):
        return {}
    result: dict[str, str | None] = {}
    for item in dependencies:
        if isinstance(item, Mapping) and item.get("role"):
            result[str(item["role"])] = None if item.get("source_run_id") is None else str(item.get("source_run_id"))
    return result


def _dependency_object_ids(readiness: ReplacementForecastReadinessDecision) -> dict[str, dict[str, int]]:
    dependencies = readiness.dependency_json.get("dependencies")
    if not isinstance(dependencies, list):
        return {}
    result: dict[str, dict[str, int]] = {}
    for item in dependencies:
        if not isinstance(item, Mapping) or not item.get("role"):
            continue
        role_ids: dict[str, int] = {}
        for key in ("artifact_id", "anchor_id", "posterior_id"):
            value = item.get(key)
            if value is not None:
                role_ids[key] = int(value)
        result[str(item["role"])] = role_ids
    return result


def _dependency_diagnostics(readiness: ReplacementForecastReadinessDecision) -> dict[str, list[str]]:
    dependency_json = readiness.dependency_json
    return {
        "missing_roles": list(dependency_json.get("missing_roles") or []),
        "unavailable_roles": list(dependency_json.get("unavailable_roles") or []),
        "blocked_roles": list(dependency_json.get("blocked_roles") or []),
        "identity_mismatch_roles": list(dependency_json.get("identity_mismatch_roles") or []),
    }


def build_replacement_forecast_event_payload(
    *,
    base_payload: object | Mapping[str, Any],
    replacement_bundle: ReplacementForecastPosteriorBundle,
    readiness: ReplacementForecastReadinessDecision,
) -> ReplacementForecastEventPayload:
    """Attach replacement product/dependency identity without changing baseline FSR payloads."""

    if readiness.status != READY_STATUS:
        raise ValueError("replacement event payload requires SHADOW_ONLY readiness")
    if replacement_bundle.trade_authority_status not in {"SHADOW_ONLY", "SHADOW_VETO_ONLY"}:
        raise ValueError("replacement event payload requires shadow/veto-only bundle authority")
    if replacement_bundle.source_id != readiness.source_id or replacement_bundle.product_id != readiness.product_id:
        raise ValueError("replacement bundle and readiness product identity mismatch")

    payload = _payload_dict(base_payload)
    payload["replacement_forecast"] = {
        "source_id": replacement_bundle.source_id,
        "product_id": replacement_bundle.product_id,
        "data_version": replacement_bundle.data_version,
        "strategy_key": readiness.strategy_key,
        "posterior_id": replacement_bundle.posterior_id,
        "readiness_id": readiness.readiness_id,
        "readiness_status": readiness.status,
        "readiness_reason_codes": list(readiness.reason_codes),
        "trade_authority_status": replacement_bundle.trade_authority_status,
        "training_allowed": False,
        "baseline_source_run_id": replacement_bundle.baseline_source_run_id,
        "dependency_source_run_ids": _dependency_source_run_ids(readiness),
        "dependency_object_ids": _dependency_object_ids(readiness),
        "dependency_diagnostics": _dependency_diagnostics(readiness),
        "source_available_at": replacement_bundle.source_available_at,
        "computed_at": replacement_bundle.computed_at,
        "authority_limits": {
            "can_increase_q_lcb": False,
            "can_increase_kelly": False,
            "can_flip_direction": False,
            "can_initiate_trade": False,
        },
    }
    return ReplacementForecastEventPayload(payload)
