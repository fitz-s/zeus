"""Config switch planner for replacement forecast shadow/veto activation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    REQUIRED_FLAGS,
    ReplacementForecastCapitalObjectiveEvidence,
    ReplacementForecastPromotionEvidence,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


TARGET_SHADOW_VETO_FLAGS = {
    SHADOW_FLAG: True,
    VETO_FLAG: True,
    TRADE_AUTHORITY_FLAG: False,
    KELLY_INCREASE_FLAG: False,
    DIRECTION_FLIP_FLAG: False,
}
TARGET_LIVE_AUTHORITY_FLAGS = {
    SHADOW_FLAG: True,
    VETO_FLAG: True,
    TRADE_AUTHORITY_FLAG: True,
    KELLY_INCREASE_FLAG: True,
    DIRECTION_FLIP_FLAG: True,
}
TARGET_SHADOW_MATERIALIZATION_CONFIG = {
    "forecast_db": "state/zeus-forecasts.db",
    "raw_manifest_dir": "state/replacement_forecast_shadow/raw_manifests",
    "request_dir": "state/replacement_forecast_shadow/requests",
    "processed_dir": "state/replacement_forecast_shadow/processed",
    "failed_dir": "state/replacement_forecast_shadow/failed",
    "seed_dir": "state/replacement_forecast_shadow/seeds",
    "seed_processed_dir": "state/replacement_forecast_shadow/seeds_processed",
    "seed_failed_dir": "state/replacement_forecast_shadow/seeds_failed",
    "refit_handoff_path": "state/replacement_forecast_shadow/refit_handoff.json",
    "promotion_evidence_path": "state/replacement_forecast_shadow/promotion_evidence.json",
    "materialization_interval_min": 5,
    "seed_discovery_limit_per_cycle": 10,
    "seed_limit_per_cycle": 10,
    "materialization_limit_per_cycle": 10,
}


@dataclass(frozen=True)
class ReplacementForecastConfigSwitchPlan:
    status: str
    reason_codes: tuple[str, ...]
    current_flags: Mapping[str, bool]
    target_flags: Mapping[str, bool]
    current_shadow_config: Mapping[str, Any]
    target_shadow_config: Mapping[str, Any]
    json_patch: tuple[Mapping[str, Any], ...]
    policy_status_after: str

    @property
    def ok(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "current_flags": dict(self.current_flags),
            "target_flags": dict(self.target_flags),
            "current_shadow_config": dict(self.current_shadow_config),
            "target_shadow_config": dict(self.target_shadow_config),
            "json_patch": [dict(item) for item in self.json_patch],
            "policy_status_after": self.policy_status_after,
        }


def _feature_flags(settings_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if "feature_flags" not in settings_payload:
        return {}
    flags = settings_payload.get("feature_flags")
    if not isinstance(flags, Mapping):
        raise ValueError("settings payload must contain feature_flags object")
    return flags


def _shadow_config(settings_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = settings_payload.get("replacement_forecast_shadow")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("settings payload replacement_forecast_shadow must be an object")
    return raw


def build_replacement_forecast_config_switch_plan(
    settings_payload: Mapping[str, Any],
) -> ReplacementForecastConfigSwitchPlan:
    """Plan the exact safe config change for shadow/veto use only."""

    return _build_replacement_forecast_config_switch_plan(
        settings_payload,
        target_flags=TARGET_SHADOW_VETO_FLAGS,
        target_policy_status="SHADOW_VETO_ONLY",
        promotion_evidence=None,
        capital_objective_evidence=None,
        dangerous_flags_allowed=False,
    )


def build_replacement_forecast_live_authority_config_switch_plan(
    settings_payload: Mapping[str, Any],
    *,
    promotion_evidence: ReplacementForecastPromotionEvidence,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
) -> ReplacementForecastConfigSwitchPlan:
    """Plan the direct new-data live-authority switch."""

    return _build_replacement_forecast_config_switch_plan(
        settings_payload,
        target_flags=TARGET_LIVE_AUTHORITY_FLAGS,
        target_policy_status="LIVE_AUTHORITY",
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=capital_objective_evidence,
        dangerous_flags_allowed=True,
    )


def _build_replacement_forecast_config_switch_plan(
    settings_payload: Mapping[str, Any],
    *,
    target_flags: Mapping[str, bool],
    target_policy_status: str,
    promotion_evidence: ReplacementForecastPromotionEvidence | None,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None,
    dangerous_flags_allowed: bool,
) -> ReplacementForecastConfigSwitchPlan:
    """Plan the exact replacement forecast config change for one authority tier."""

    flags = _feature_flags(settings_payload)
    reasons: list[str] = []
    patch_notes: list[str] = []
    if any(key not in flags for key in REQUIRED_FLAGS):
        patch_notes.append("REPLACEMENT_CONFIG_FLAGS_WILL_BE_ADDED")
    shadow_config = _shadow_config(settings_payload)
    if any(shadow_config.get(key) != value for key, value in TARGET_SHADOW_MATERIALIZATION_CONFIG.items()):
        patch_notes.append("REPLACEMENT_CONFIG_SHADOW_MATERIALIZATION_WILL_BE_ADDED")
    current: dict[str, bool] = {}
    for key in REQUIRED_FLAGS:
        if key not in flags:
            continue
        value = flags[key]
        if not isinstance(value, bool):
            reasons.append("REPLACEMENT_CONFIG_FLAG_NOT_BOOL")
            continue
        current[key] = value
    target = dict(target_flags)
    policy = None
    try:
        policy = resolve_replacement_forecast_runtime_policy(
            target,
            promotion_evidence=promotion_evidence,
            capital_objective_evidence=capital_objective_evidence,
        )
    except Exception:
        reasons.append("REPLACEMENT_CONFIG_TARGET_POLICY_INVALID")
    if policy is not None and policy.status != target_policy_status:
        reasons.append(f"REPLACEMENT_CONFIG_TARGET_NOT_{target_policy_status}")
        reasons.extend(policy.reason_codes)
    if not dangerous_flags_allowed and (
        target[TRADE_AUTHORITY_FLAG] or target[KELLY_INCREASE_FLAG] or target[DIRECTION_FLIP_FLAG]
    ):
        reasons.append("REPLACEMENT_CONFIG_DANGEROUS_TARGET_FLAG")

    patch = tuple(
        [
            *(
                {
                    "op": "replace" if key in flags else "add",
                    "path": f"/feature_flags/{key}",
                    "value": target[key],
                    "current_value": flags.get(key),
                }
                for key in REQUIRED_FLAGS
                if flags.get(key) != target[key]
            ),
            *(
                {
                    "op": "replace" if key in shadow_config else "add",
                    "path": f"/replacement_forecast_shadow/{key}",
                    "value": value,
                    "current_value": shadow_config.get(key),
                }
                for key, value in TARGET_SHADOW_MATERIALIZATION_CONFIG.items()
                if shadow_config.get(key) != value
            ),
        ]
    )
    status = "READY" if not reasons else "BLOCKED"
    return ReplacementForecastConfigSwitchPlan(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons or patch_notes or ["REPLACEMENT_CONFIG_SHADOW_VETO_PATCH_READY"])),
        current_flags=current,
        target_flags=target,
        current_shadow_config=dict(shadow_config),
        target_shadow_config=dict(TARGET_SHADOW_MATERIALIZATION_CONFIG),
        json_patch=patch,
        policy_status_after=policy.status if policy is not None else "BLOCKED",
    )


def read_replacement_forecast_config_switch_plan(settings_path: Path | str) -> ReplacementForecastConfigSwitchPlan:
    payload = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("settings JSON must decode to an object")
    return build_replacement_forecast_config_switch_plan(payload)


def apply_replacement_forecast_config_switch(settings_path: Path | str) -> ReplacementForecastConfigSwitchPlan:
    path = Path(settings_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("settings JSON must decode to an object")
    plan = build_replacement_forecast_config_switch_plan(payload)
    if not plan.ok:
        raise ValueError(",".join(plan.reason_codes))
    flags = payload.setdefault("feature_flags", {})
    if not isinstance(flags, dict):
        raise ValueError("feature_flags must be an object")
    flags.update(TARGET_SHADOW_VETO_FLAGS)
    shadow_config = payload.setdefault("replacement_forecast_shadow", {})
    if not isinstance(shadow_config, dict):
        raise ValueError("replacement_forecast_shadow must be an object")
    shadow_config.update(TARGET_SHADOW_MATERIALIZATION_CONFIG)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return plan


def apply_replacement_forecast_live_authority_config_switch(
    settings_path: Path | str,
    *,
    promotion_evidence: ReplacementForecastPromotionEvidence,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
) -> ReplacementForecastConfigSwitchPlan:
    path = Path(settings_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("settings JSON must decode to an object")
    plan = build_replacement_forecast_live_authority_config_switch_plan(
        payload,
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=capital_objective_evidence,
    )
    if not plan.ok:
        raise ValueError(",".join(plan.reason_codes))
    flags = payload.setdefault("feature_flags", {})
    if not isinstance(flags, dict):
        raise ValueError("feature_flags must be an object")
    flags.update(TARGET_LIVE_AUTHORITY_FLAGS)
    shadow_config = payload.setdefault("replacement_forecast_shadow", {})
    if not isinstance(shadow_config, dict):
        raise ValueError("replacement_forecast_shadow must be an object")
    shadow_config.update(TARGET_SHADOW_MATERIALIZATION_CONFIG)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return plan
