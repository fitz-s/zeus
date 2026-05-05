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

import os
import sqlite3
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.calibration.forecast_calibration_domain import ForecastCalibrationDomain
from src.config import EntryForecastCalibrationPolicyId, EntryForecastConfig
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

POLICY_ECMWF_OPENDATA_USES_TIGGE_LOCALDAY_CAL_V1 = "ecmwf_open_data_uses_tigge_localday_cal_v1"

# Maps OpenData forecast data_version → TIGGE calibration data_version.
# Used by legacy evaluate_calibration_transfer_policy to resolve which
# Platt model family to apply when serving OpenData forecasts.
_TRANSFER_SOURCE_BY_OPENDATA_VERSION: dict[str, str] = {
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
    note: str = ""

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
    if os.environ.get("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "false").lower() == "true":
        # Flag is on — this legacy path should not be reached except via the
        # _with_evidence fallback.  If a caller still calls legacy directly,
        # emit a DeprecationWarning so operators can migrate the callsite.
        warnings.warn(
            "evaluate_calibration_transfer_policy called directly while "
            "ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true. live_promotion_approved "
            "is silently ignored by the evidence-gated path. Migrate caller to "
            "evaluate_calibration_transfer_policy_with_evidence.",
            DeprecationWarning,
            stacklevel=2,
        )
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


def evaluate_calibration_transfer_policy_with_evidence(
    *,
    config: EntryForecastConfig,
    source_id: str,
    target_source_id: str,
    source_cycle: str,
    target_cycle: str,
    horizon_profile: str,
    season: str,
    cluster: str,
    metric: str,
    platt_model_key: str,
    conn: sqlite3.Connection,
    now: datetime,
    staleness_days: int = 90,
) -> CalibrationTransferDecision:
    """DB-row-as-authority replacement for legacy string-mapping policy.

    Feature-flagged off by default (ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED).
    When flag is off, delegates to legacy evaluate_calibration_transfer_policy.

    Same-domain fast-path: source_id==target_source_id AND cycles match → LIVE_ELIGIBLE.
    Otherwise queries validated_calibration_transfers for matching row.
    Stale or missing → SHADOW_ONLY. status='TRANSFER_UNSAFE' → BLOCKED.
    `live_promotion_approved` flag is REMOVED — DB row is authority.

    Phase X.1 scaffold: OOS evaluator (X.2) writes rows; flag flip (X.3) is
    operator-gated. Until then this is a zero-risk pass-through.
    """
    flag = os.environ.get("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "false").lower()
    if flag != "true":
        # Feature flag off — delegate to legacy string-mapping policy.
        # The new function signature has no forecast_data_version; infer it
        # from metric so the legacy version-map resolves. Phase X.3 caller
        # update will replace this inference with an explicit argument.
        _fallback_dv = (
            ECMWF_OPENDATA_HIGH_DATA_VERSION
            if metric == "high"
            else ECMWF_OPENDATA_LOW_DATA_VERSION
        )
        return evaluate_calibration_transfer_policy(
            config=config,
            source_id=source_id,
            forecast_data_version=_fallback_dv,
        )

    policy_id = config.calibration_policy_id.value

    # Same-domain fast-path: no transfer occurs when source and target are
    # identical on both source identity and cycle.
    if source_id == target_source_id and source_cycle == target_cycle:
        return CalibrationTransferDecision(
            status="LIVE_ELIGIBLE",
            reason_codes=("CALIBRATION_TRANSFER_APPROVED",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="same_domain_no_transfer",
        )

    # Evidence query: look up validated_calibration_transfers row.
    row = conn.execute(
        """
        SELECT status, evaluated_at
          FROM validated_calibration_transfers
         WHERE target_source_id = ?
           AND target_cycle     = ?
           AND season           = ?
           AND cluster          = ?
           AND metric           = ?
           AND horizon_profile  = ?
           AND platt_model_key  = ?
         LIMIT 1
        """,
        (target_source_id, target_cycle, season, cluster, metric,
         horizon_profile, platt_model_key),
    ).fetchone()

    if row is None:
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_NO_EVIDENCE",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="no_evidence_row",
        )

    row_status, evaluated_at_str = row
    evaluated_at = datetime.fromisoformat(evaluated_at_str)
    # Make both timezone-aware or both naive for comparison.
    if now.tzinfo is not None and evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    elif now.tzinfo is None and evaluated_at.tzinfo is not None:
        now = now.replace(tzinfo=timezone.utc)

    if (now - evaluated_at) > timedelta(days=staleness_days):
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_STALE_EVIDENCE",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note=f"evidence_stale_>{staleness_days}d",
        )

    if row_status == "LIVE_ELIGIBLE":
        return CalibrationTransferDecision(
            status="LIVE_ELIGIBLE",
            reason_codes=("CALIBRATION_TRANSFER_APPROVED",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="db_row_live_eligible",
        )

    if row_status == "TRANSFER_UNSAFE":
        return CalibrationTransferDecision(
            status="BLOCKED",
            reason_codes=("CALIBRATION_TRANSFER_UNSAFE",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="db_row_transfer_unsafe",
        )

    # INSUFFICIENT_SAMPLE or same_domain_no_transfer treated as SHADOW_ONLY.
    return CalibrationTransferDecision(
        status="SHADOW_ONLY",
        reason_codes=("CALIBRATION_TRANSFER_INSUFFICIENT_SAMPLE",),
        policy_id=policy_id,
        forecast_data_version="",
        calibration_data_version=None,
        live_promotion_approved=False,
        note=f"db_row_status={row_status}",
    )
