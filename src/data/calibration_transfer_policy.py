"""Calibration transfer policy for Open Data live-entry forecasts.

Houses ``evaluate_calibration_transfer_policy`` — the legacy string-mapping
policy used by ``entry_forecast_shadow.py`` and ``evaluator.py`` to gate
OpenData live-entry decisions on operator opt-in
(``live_promotion_approved=True``).

PR #55 introduced an OOS-evidence-based ``evaluate_calibration_transfer``
backed by a ``validated_calibration_transfers`` table — that approach was
replaced by PR #56's ``MarketPhaseEvidence`` + ``oracle_evidence_status``
stack on main, so the new function and its dataclass were removed during
the merge.  The legacy policy below remains the live-eligibility gate
until PR #56's evidence stack fully covers the OpenData transfer surface.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.calibration.forecast_calibration_domain import ForecastCalibrationDomain
from src.config import EntryForecastCalibrationPolicyId, EntryForecastConfig
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

POLICY_ECMWF_OPENDATA_USES_TIGGE_LOCALDAY_CAL_V1 = "ecmwf_open_data_uses_tigge_localday_cal_v1"

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
