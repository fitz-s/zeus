"""Runtime switch admission for replacement forecast integration."""

from __future__ import annotations

from dataclasses import dataclass

from src.data.replacement_forecast_live_switch_surface import ReplacementForecastLiveSwitchReport
from src.data.replacement_forecast_readiness import READY_STATUS, ReplacementForecastReadinessDecision
from src.data.replacement_forecast_refit_gate import ReplacementForecastRefitDecision
from src.data.replacement_forecast_runtime_policy import (
    BLOCKED_STATUS,
    LIVE_AUTHORITY_STATUS,
    SAFE_DEFAULT_STATUS,
    ReplacementForecastCapitalObjectiveEvidence,
    ReplacementForecastRuntimePolicy,
)


SWITCH_DISABLED = "DISABLED"
SWITCH_BLOCKED = "BLOCKED"
SWITCH_LIVE_AUTHORITY = "LIVE_AUTHORITY"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


@dataclass(frozen=True)
class ReplacementForecastSwitchDecisionInput:
    runtime_policy: ReplacementForecastRuntimePolicy
    live_switch_report: ReplacementForecastLiveSwitchReport
    readiness: ReplacementForecastReadinessDecision | None = None
    refit_decision: ReplacementForecastRefitDecision | None = None
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_policy, ReplacementForecastRuntimePolicy):
            raise TypeError("runtime_policy must be ReplacementForecastRuntimePolicy")
        if not isinstance(self.live_switch_report, ReplacementForecastLiveSwitchReport):
            raise TypeError("live_switch_report must be ReplacementForecastLiveSwitchReport")
        if self.readiness is not None and not isinstance(self.readiness, ReplacementForecastReadinessDecision):
            raise TypeError("readiness must be ReplacementForecastReadinessDecision")
        if self.refit_decision is not None and not isinstance(self.refit_decision, ReplacementForecastRefitDecision):
            raise TypeError("refit_decision must be ReplacementForecastRefitDecision")
        if self.capital_objective_evidence is not None and not isinstance(self.capital_objective_evidence, ReplacementForecastCapitalObjectiveEvidence):
            raise TypeError("capital_objective_evidence must be ReplacementForecastCapitalObjectiveEvidence")


@dataclass(frozen=True)
class ReplacementForecastSwitchDecision:
    status: str
    reason_codes: tuple[str, ...]
    can_read_live_posterior: bool
    can_apply_reactor_hook: bool
    can_initiate_trade: bool
    can_increase_kelly: bool
    can_flip_direction: bool
    readiness_id: str | None

    def __post_init__(self) -> None:
        if self.status not in {SWITCH_DISABLED, SWITCH_BLOCKED, SWITCH_LIVE_AUTHORITY}:
            raise ValueError("invalid replacement switch decision status")
        for reason in self.reason_codes:
            _reject_alias(str(reason), field_name="reason_codes")
        if self.status != SWITCH_LIVE_AUTHORITY and (self.can_initiate_trade or self.can_increase_kelly or self.can_flip_direction):
            raise ValueError("only live-authority switch decisions can grant live trade authority")
    @property
    def blocked(self) -> bool:
        return self.status == SWITCH_BLOCKED

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "can_read_live_posterior": self.can_read_live_posterior,
            "can_apply_reactor_hook": self.can_apply_reactor_hook,
            "can_initiate_trade": self.can_initiate_trade,
            "can_increase_kelly": self.can_increase_kelly,
            "can_flip_direction": self.can_flip_direction,
            "readiness_id": self.readiness_id,
        }


def evaluate_replacement_forecast_switch_decision(
    request: ReplacementForecastSwitchDecisionInput,
) -> ReplacementForecastSwitchDecision:
    """Compose replacement runtime gates into one daemon-facing admission verdict."""

    if not isinstance(request, ReplacementForecastSwitchDecisionInput):
        raise TypeError("request must be ReplacementForecastSwitchDecisionInput")
    policy = request.runtime_policy
    live_switch = request.live_switch_report
    readiness = request.readiness

    if policy.status == SAFE_DEFAULT_STATUS:
        return ReplacementForecastSwitchDecision(
            status=SWITCH_DISABLED,
            reason_codes=policy.reason_codes,
            can_read_live_posterior=False,
            can_apply_reactor_hook=False,
            can_initiate_trade=False,
            can_increase_kelly=False,
            can_flip_direction=False,
            readiness_id=None,
        )

    # Runtime-policy LIVE_AUTHORITY from the flag ladder is necessary for
    # can_initiate_trade, but not sufficient to turn an arbitrary posterior row
    # into live probability authority. The live bundle reader still requires a
    # row-level LIVE_AUTHORITY carrier with fused q and certified bootstrap bounds.
    reasons: list[str] = []
    if policy.status == BLOCKED_STATUS:
        reasons.extend(policy.reason_codes)
    simple_switch_reasons = tuple(
        reason
        for reason in live_switch.reason_codes
        if reason != "REPLACEMENT_SWITCH_TRADE_AUTHORITY_NOT_SIMPLE_SWITCH"
    )
    if simple_switch_reasons and live_switch.status not in {"SIMPLE_SWITCH_READY", "LIVE_AUTHORITY_READY"}:
        reasons.extend(simple_switch_reasons)
    if readiness is None:
        reasons.append("REPLACEMENT_SWITCH_READINESS_MISSING")
    elif readiness.status != READY_STATUS:
        reasons.extend(readiness.reason_codes)

    if reasons:
        return ReplacementForecastSwitchDecision(
            status=SWITCH_BLOCKED,
            reason_codes=tuple(reasons),
            can_read_live_posterior=False,
            can_apply_reactor_hook=False,
            can_initiate_trade=False,
            can_increase_kelly=False,
            can_flip_direction=False,
            readiness_id=getattr(readiness, "readiness_id", None),
        )

    if policy.status == LIVE_AUTHORITY_STATUS:
        return ReplacementForecastSwitchDecision(
            status=SWITCH_LIVE_AUTHORITY,
            reason_codes=("REPLACEMENT_SWITCH_LIVE_AUTHORITY_ADMITTED",),
            can_read_live_posterior=True,
            can_apply_reactor_hook=True,
            can_initiate_trade=policy.can_initiate_trade,
            can_increase_kelly=policy.can_increase_kelly,
            can_flip_direction=policy.can_flip_direction,
            readiness_id=readiness.readiness_id if readiness is not None else None,
        )
    return ReplacementForecastSwitchDecision(
        status=SWITCH_BLOCKED,
        reason_codes=("REPLACEMENT_SWITCH_POLICY_UNSUPPORTED",),
        can_read_live_posterior=False,
        can_apply_reactor_hook=False,
        can_initiate_trade=False,
        can_increase_kelly=False,
        can_flip_direction=False,
        readiness_id=getattr(readiness, "readiness_id", None),
    )
