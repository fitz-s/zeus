"""Calibration transfer policy for Open Data live-entry forecasts.

Phase 2.5 (2026-05-04, may4math.md F2 + critic-opus BLOCKER 3):
The legacy ``evaluate_calibration_transfer_policy`` is a string-mapping policy
that returns LIVE_ELIGIBLE solely on operator opt-in (``live_promotion_approved
=True``) — no statistical evidence requirement. That is no longer sufficient for
unlock per the math tribunal.

The new ``evaluate_calibration_transfer`` consults
``validated_calibration_transfers`` (DB table populated by OOS holdout
experiments) and returns LIVE_ELIGIBLE only when:
    1. forecast_domain == calibrator_domain (exact match), OR
    2. a row exists in validated_calibration_transfers with matching
       (train_domain, test_domain) AND meeting freshness/authority criteria.

Otherwise SHADOW_ONLY (no validated evidence) or BLOCK (categorically invalid).

The legacy ``evaluate_calibration_transfer_policy`` is preserved as a
backward-compat shim for callers that haven't been migrated yet.
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


@dataclass(frozen=True)
class CalibrationTransferEvidence:
    """Result of evaluate_calibration_transfer (Phase 2.5 API).

    Three statuses:
        'LIVE_ELIGIBLE' — domains match exactly OR validated_transfers has a
                          matching row with passing OOS evidence
        'SHADOW_ONLY'  — no validated evidence; forecast may run shadow but not live
        'BLOCK'        — categorically invalid (e.g., 06z entry on full-horizon)

    matched_transfer_id is None when LIVE_ELIGIBLE via exact-match;
    populated with the transfer row's primary key when via validated_transfers.
    """

    status: str
    reason_codes: tuple[str, ...]
    forecast_domain: ForecastCalibrationDomain
    calibrator_domain: ForecastCalibrationDomain
    matched_transfer_id: Optional[str]
    evaluated_at: str

    @property
    def live_eligible(self) -> bool:
        return self.status == "LIVE_ELIGIBLE"


def evaluate_calibration_transfer(
    conn: sqlite3.Connection,
    *,
    forecast_domain: ForecastCalibrationDomain,
    calibrator_domain: ForecastCalibrationDomain,
    require_unexpired: bool = True,
    minimum_authority: str = "VERIFIED",
) -> CalibrationTransferEvidence:
    """Evaluate whether a forecast may use a Platt model trained in another domain.

    Resolution order:
        1. Categorical-invalid check (e.g., 06z entry) → BLOCK
        2. Exact domain match → LIVE_ELIGIBLE (no transfer evidence needed)
        3. validated_calibration_transfers row exists with matching
           (train, test) domains, authority>=minimum_authority, and not
           expired (if require_unexpired) → LIVE_ELIGIBLE with
           matched_transfer_id
        4. Otherwise → SHADOW_ONLY

    Args:
        conn: SQLite connection (must have validated_calibration_transfers table
              applied; if missing, treats as no validated rows = SHADOW_ONLY).
        forecast_domain: domain of the live forecast (test side).
        calibrator_domain: domain of the Platt model being considered (train side).
        require_unexpired: when True, validated_transfers row must have
            expires_at IS NULL OR expires_at > now (UTC).
        minimum_authority: required ``authority`` column value (default
            'VERIFIED'; pass 'UNVERIFIED' to allow shadow-tier validations).
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Categorical invalid
    if forecast_domain.is_categorically_invalid():
        return CalibrationTransferEvidence(
            status="BLOCK",
            reason_codes=("FORECAST_DOMAIN_CATEGORICALLY_INVALID",),
            forecast_domain=forecast_domain,
            calibrator_domain=calibrator_domain,
            matched_transfer_id=None,
            evaluated_at=now_iso,
        )
    if calibrator_domain.is_categorically_invalid():
        return CalibrationTransferEvidence(
            status="BLOCK",
            reason_codes=("CALIBRATOR_DOMAIN_CATEGORICALLY_INVALID",),
            forecast_domain=forecast_domain,
            calibrator_domain=calibrator_domain,
            matched_transfer_id=None,
            evaluated_at=now_iso,
        )

    # 2. Exact match — no transfer needed
    if forecast_domain.matches(calibrator_domain):
        return CalibrationTransferEvidence(
            status="LIVE_ELIGIBLE",
            reason_codes=("DOMAIN_EXACT_MATCH",),
            forecast_domain=forecast_domain,
            calibrator_domain=calibrator_domain,
            matched_transfer_id=None,
            evaluated_at=now_iso,
        )

    # 3. validated_calibration_transfers lookup
    try:
        conn.execute("SELECT 1 FROM validated_calibration_transfers LIMIT 0")
    except sqlite3.OperationalError:
        return CalibrationTransferEvidence(
            status="SHADOW_ONLY",
            reason_codes=("VALIDATED_TRANSFERS_TABLE_MISSING",),
            forecast_domain=forecast_domain,
            calibrator_domain=calibrator_domain,
            matched_transfer_id=None,
            evaluated_at=now_iso,
        )

    expiry_clause = ""
    params: list = [
        # train (calibrator)
        calibrator_domain.source_id,
        calibrator_domain.cycle_hour_utc,
        calibrator_domain.horizon_profile,
        calibrator_domain.data_version,
        calibrator_domain.metric,
        calibrator_domain.season,
        # test (forecast)
        forecast_domain.source_id,
        forecast_domain.cycle_hour_utc,
        forecast_domain.horizon_profile,
        forecast_domain.data_version,
        forecast_domain.metric,
        forecast_domain.season,
        minimum_authority,
    ]
    if require_unexpired:
        expiry_clause = "AND (expires_at IS NULL OR expires_at > ?)"
        params.append(now_iso)

    row = conn.execute(
        f"""
        SELECT transfer_id
        FROM validated_calibration_transfers
        WHERE train_source_id = ? AND train_cycle_hour_utc = ?
          AND train_horizon_profile = ? AND train_data_version = ?
          AND train_metric = ? AND train_season = ?
          AND test_source_id = ? AND test_cycle_hour_utc = ?
          AND test_horizon_profile = ? AND test_data_version = ?
          AND test_metric = ? AND test_season = ?
          AND authority = ?
          {expiry_clause}
        ORDER BY validated_at DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()

    if row is not None:
        transfer_id = row[0] if not hasattr(row, "keys") else row["transfer_id"]
        return CalibrationTransferEvidence(
            status="LIVE_ELIGIBLE",
            reason_codes=("VALIDATED_TRANSFER_MATCH",),
            forecast_domain=forecast_domain,
            calibrator_domain=calibrator_domain,
            matched_transfer_id=str(transfer_id),
            evaluated_at=now_iso,
        )

    return CalibrationTransferEvidence(
        status="SHADOW_ONLY",
        reason_codes=("NO_VALIDATED_TRANSFER",),
        forecast_domain=forecast_domain,
        calibrator_domain=calibrator_domain,
        matched_transfer_id=None,
        evaluated_at=now_iso,
    )


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
