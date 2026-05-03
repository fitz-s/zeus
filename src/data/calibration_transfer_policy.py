"""Calibration transfer policy for Open Data live-entry forecasts."""

from __future__ import annotations

from dataclasses import dataclass

from src.config import EntryForecastCalibrationPolicyId, EntryForecastConfig
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

POLICY_ECMWF_OPENDATA_USES_TIGGE_LOCALDAY_CAL_V1 = "ecmwf_open_data_uses_tigge_localday_cal_v1"

_TRANSFER_SOURCE_BY_OPENDATA_VERSION = {
    ECMWF_OPENDATA_HIGH_DATA_VERSION: HIGH_LOCALDAY_MAX.data_version,
    ECMWF_OPENDATA_LOW_DATA_VERSION: LOW_LOCALDAY_MIN.data_version,
}


@dataclass(frozen=True)
class CalibrationTransferDecision:
    status: str
    reason_codes: tuple[str, ...]
    policy_id: str
    forecast_data_version: str
    calibration_data_version: str | None
    live_promotion_approved: bool

    @property
    def live_eligible(self) -> bool:
        return self.status == "LIVE_ELIGIBLE"


def evaluate_calibration_transfer_policy(
    *,
    config: EntryForecastConfig,
    source_id: str,
    forecast_data_version: str,
    live_promotion_approved: bool = False,
) -> CalibrationTransferDecision:
    policy_id = config.calibration_policy_id.value
    if policy_id != EntryForecastCalibrationPolicyId.ECMWF_OPEN_DATA_USES_TIGGE_LOCALDAY_CAL_V1.value:
        return CalibrationTransferDecision(
            status="BLOCKED",
            reason_codes=("CALIBRATION_TRANSFER_POLICY_UNKNOWN",),
            policy_id=policy_id,
            forecast_data_version=forecast_data_version,
            calibration_data_version=None,
            live_promotion_approved=live_promotion_approved,
        )
    if source_id != config.source_id:
        return CalibrationTransferDecision(
            status="BLOCKED",
            reason_codes=("CALIBRATION_TRANSFER_SOURCE_MISMATCH",),
            policy_id=policy_id,
            forecast_data_version=forecast_data_version,
            calibration_data_version=None,
            live_promotion_approved=live_promotion_approved,
        )
    calibration_data_version = _TRANSFER_SOURCE_BY_OPENDATA_VERSION.get(forecast_data_version)
    if calibration_data_version is None:
        return CalibrationTransferDecision(
            status="BLOCKED",
            reason_codes=("CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED",),
            policy_id=policy_id,
            forecast_data_version=forecast_data_version,
            calibration_data_version=None,
            live_promotion_approved=live_promotion_approved,
        )
    if not live_promotion_approved:
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_SHADOW_ONLY",),
            policy_id=policy_id,
            forecast_data_version=forecast_data_version,
            calibration_data_version=calibration_data_version,
            live_promotion_approved=False,
        )
    return CalibrationTransferDecision(
        status="LIVE_ELIGIBLE",
        reason_codes=("CALIBRATION_TRANSFER_APPROVED",),
        policy_id=policy_id,
        forecast_data_version=forecast_data_version,
        calibration_data_version=calibration_data_version,
        live_promotion_approved=True,
    )
