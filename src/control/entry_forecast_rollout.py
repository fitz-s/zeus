"""Entry-forecast rollout gate for canary/live promotion."""

from __future__ import annotations

from dataclasses import dataclass

from src.config import EntryForecastConfig, EntryForecastRolloutMode
from src.data.live_entry_status import LiveEntryForecastStatus


@dataclass(frozen=True)
class EntryForecastPromotionEvidence:
    operator_approval_id: str | None
    g1_evidence_id: str | None
    status_snapshot: LiveEntryForecastStatus
    calibration_promotion_approved: bool = False
    canary_success_evidence_id: str | None = None


@dataclass(frozen=True)
class EntryForecastRolloutDecision:
    status: str
    reason_codes: tuple[str, ...]

    @property
    def may_submit_live_orders(self) -> bool:
        return self.status == "LIVE_ELIGIBLE"

    @property
    def may_run_canary(self) -> bool:
        return self.status in {"CANARY_ELIGIBLE", "LIVE_ELIGIBLE"}


def evaluate_entry_forecast_rollout_gate(
    *,
    config: EntryForecastConfig,
    evidence: EntryForecastPromotionEvidence | None,
) -> EntryForecastRolloutDecision:
    if config.rollout_mode is EntryForecastRolloutMode.BLOCKED:
        return EntryForecastRolloutDecision("BLOCKED", ("ENTRY_FORECAST_ROLLOUT_BLOCKED",))
    if config.rollout_mode is EntryForecastRolloutMode.SHADOW:
        return EntryForecastRolloutDecision("SHADOW_ONLY", ("ENTRY_FORECAST_SHADOW_MODE",))
    if evidence is None:
        return EntryForecastRolloutDecision("BLOCKED", ("ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING",))

    reasons: list[str] = []
    if not evidence.operator_approval_id:
        reasons.append("ENTRY_FORECAST_OPERATOR_APPROVAL_MISSING")
    if not evidence.g1_evidence_id:
        reasons.append("ENTRY_FORECAST_G1_EVIDENCE_MISSING")
    if not evidence.calibration_promotion_approved:
        reasons.append("CALIBRATION_TRANSFER_APPROVAL_MISSING")
    if evidence.status_snapshot.status != "LIVE_ELIGIBLE":
        reasons.extend(evidence.status_snapshot.blockers or ("ENTRY_FORECAST_STATUS_NOT_READY",))

    if config.rollout_mode is EntryForecastRolloutMode.LIVE and not evidence.canary_success_evidence_id:
        reasons.append("ENTRY_FORECAST_CANARY_SUCCESS_MISSING")

    if reasons:
        return EntryForecastRolloutDecision("BLOCKED", tuple(sorted(set(reasons))))
    if config.rollout_mode is EntryForecastRolloutMode.CANARY:
        return EntryForecastRolloutDecision("CANARY_ELIGIBLE", ("ENTRY_FORECAST_CANARY_APPROVED",))
    if config.rollout_mode is EntryForecastRolloutMode.LIVE:
        return EntryForecastRolloutDecision("LIVE_ELIGIBLE", ("ENTRY_FORECAST_LIVE_APPROVED",))
    return EntryForecastRolloutDecision("BLOCKED", ("ENTRY_FORECAST_ROLLOUT_MODE_UNKNOWN",))
