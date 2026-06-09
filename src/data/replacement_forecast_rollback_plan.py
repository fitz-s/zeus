"""Rollback plan payloads for replacement forecast shadow/veto integration.

The functions here produce operator-readable plans only. They do not write
control_plane.json, mutate config/settings.json, delete rows, or touch DB state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    ReplacementForecastRuntimePolicy,
)


SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
PRODUCT_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1"
DEPENDENCY_SOURCE_IDS = ("ecmwf_aifs_ens", "openmeteo_ecmwf_ifs_9km", SOURCE_ID)
ROLLBACK_MODE = "DISABLE_REPLACEMENT_FORECAST_SHADOW_VETO"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"
_DISABLE_FLAGS = {
    SHADOW_FLAG: False,
    VETO_FLAG: False,
    TRADE_AUTHORITY_FLAG: False,
    KELLY_INCREASE_FLAG: False,
    DIRECTION_FLIP_FLAG: False,
}


def _utc_text(value: datetime | str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("generated_at must be a timezone-aware datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


@dataclass(frozen=True)
class ReplacementForecastRollbackPlan:
    mode: str
    generated_at: str
    reason: str
    current_policy_status: str
    feature_flag_updates: Mapping[str, bool]
    source_ids_to_pause: tuple[str, ...]
    product_ids_to_quarantine: tuple[str, ...]
    shadow_tables_to_preserve: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    verification_commands: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.mode != ROLLBACK_MODE:
            raise ValueError("replacement forecast rollback plan mode mismatch")
        if not self.reason:
            raise ValueError("rollback reason is required")
        if dict(self.feature_flag_updates) != _DISABLE_FLAGS:
            raise ValueError("rollback plan must disable every replacement forecast feature flag")
        for source_id in self.source_ids_to_pause:
            _reject_alias(source_id, field_name="source_id")
        for product_id in self.product_ids_to_quarantine:
            _reject_alias(product_id, field_name="product_id")
        if "delete_shadow_rows" not in self.prohibited_actions:
            raise ValueError("rollback plan must prohibit deleting shadow rows")
        if "write_settlement_truth" not in self.prohibited_actions:
            raise ValueError("rollback plan must prohibit settlement truth writes")

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "generated_at": self.generated_at,
            "reason": self.reason,
            "current_policy_status": self.current_policy_status,
            "feature_flag_updates": dict(self.feature_flag_updates),
            "source_ids_to_pause": list(self.source_ids_to_pause),
            "product_ids_to_quarantine": list(self.product_ids_to_quarantine),
            "shadow_tables_to_preserve": list(self.shadow_tables_to_preserve),
            "prohibited_actions": list(self.prohibited_actions),
            "verification_commands": list(self.verification_commands),
        }


def build_replacement_forecast_rollback_plan(
    *,
    current_policy: ReplacementForecastRuntimePolicy,
    reason: str,
    generated_at: datetime | str,
    additional_source_ids_to_pause: Sequence[str] = (),
) -> ReplacementForecastRollbackPlan:
    """Build a rollback plan without executing any control-plane side effect."""

    if not isinstance(current_policy, ReplacementForecastRuntimePolicy):
        raise TypeError("current_policy must be ReplacementForecastRuntimePolicy")
    reason_text = str(reason or "").strip()
    if not reason_text:
        raise ValueError("rollback reason is required")
    source_ids = tuple(dict.fromkeys((*DEPENDENCY_SOURCE_IDS, *(str(item) for item in additional_source_ids_to_pause if str(item).strip()))))
    for source_id in source_ids:
        _reject_alias(source_id, field_name="source_id")
    return ReplacementForecastRollbackPlan(
        mode=ROLLBACK_MODE,
        generated_at=_utc_text(generated_at),
        reason=reason_text,
        current_policy_status=current_policy.status,
        feature_flag_updates=dict(_DISABLE_FLAGS),
        source_ids_to_pause=source_ids,
        product_ids_to_quarantine=(PRODUCT_ID,),
        shadow_tables_to_preserve=(
            "raw_forecast_artifacts",
            "deterministic_forecast_anchors",
            "forecast_posteriors",
            "replacement_shadow_decisions",
        ),
        prohibited_actions=(
            "delete_shadow_rows",
            "write_settlement_truth",
            "enable_trade_authority",
            "increase_kelly",
            "flip_direction",
            "initiate_replacement_trade",
        ),
        verification_commands=(
            "python3 -m py_compile src/data/replacement_forecast_runtime_policy.py src/engine/replacement_forecast_reactor_hook.py",
            "pytest -q tests/test_replacement_forecast_runtime_policy.py tests/test_replacement_forecast_reactor_hook.py tests/test_replacement_forecast_rollback_plan.py",
            "python3 scripts/check_schema_fingerprint.py",
        ),
    )
