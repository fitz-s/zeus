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
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.calibration.forecast_calibration_domain import ForecastCalibrationDomain
from src.config import (
    EntryForecastCalibrationPolicyId,
    EntryForecastConfig,
    calibration_maturity_thresholds,
)
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

POLICY_ECMWF_OPENDATA_USES_TIGGE_LOCALDAY_CAL_V1 = "ecmwf_open_data_uses_tigge_localday_cal_v1"
MIN_TRANSFER_EVIDENCE_PAIRS = 200
_, _, MIN_SOURCE_PLATT_SAMPLES = calibration_maturity_thresholds()
MIN_TRANSFER_LEAD_DAYS = 1.0
MAX_TRANSFER_LEAD_DAYS = 7.0
TRANSFER_OOS_HOLDOUT_FRACTION = 0.2

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


def _finite_transfer_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _finite_brier(value: object) -> float | None:
    result = _finite_transfer_float(value)
    if result is None or not (0.0 <= result <= 1.0):
        return None
    return result


def _finite_brier_threshold(value: object) -> float | None:
    result = _finite_transfer_float(value)
    if result is None or not (0.0 <= result <= 1.0):
        return None
    return result


def _finite_probability(value: object) -> float | None:
    result = _finite_transfer_float(value)
    if result is None or not (0.0 < result < 1.0):
        return None
    return result


def _parse_evidence_time(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _nonempty_transfer_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def select_time_blocked_transfer_pairs(
    pairs: list[dict],
    *,
    holdout_fraction: float = TRANSFER_OOS_HOLDOUT_FRACTION,
) -> list[dict]:
    """Select the chronological OOS cohort for calibration-transfer evidence.

    The split unit is decision_group_id, ordered by forecast availability time.
    Rows without decision_group_id or parseable forecast_available_at cannot
    prove OOS time basis and are excluded from the evidence cohort.
    """
    if not (0.0 < holdout_fraction < 1.0):
        raise ValueError("holdout_fraction must be between 0 and 1")

    groups: dict[str, dict] = {}
    for pair in pairs:
        group_id = _nonempty_transfer_text(pair.get("decision_group_id"))
        forecast_available_at = _parse_evidence_time(pair.get("forecast_available_at"))
        target_date = _nonempty_transfer_text(pair.get("target_date"))
        if group_id is None or forecast_available_at is None or target_date is None:
            continue
        forecast_available_at = forecast_available_at.astimezone(timezone.utc)
        group = groups.setdefault(
            group_id,
            {
                "forecast_available_at": forecast_available_at,
                "target_date": target_date,
                "rows": [],
            },
        )
        if (
            group["forecast_available_at"] != forecast_available_at
            or group["target_date"] != target_date
        ):
            continue
        group["rows"].append(pair)

    if not groups:
        return []

    sorted_groups = sorted(
        groups.items(),
        key=lambda item: (
            item[1]["forecast_available_at"],
            item[1]["target_date"],
            item[0],
        ),
    )
    holdout_group_count = max(1, math.ceil(len(sorted_groups) * holdout_fraction))
    selected_group_ids = {
        group_id for group_id, _ in sorted_groups[-holdout_group_count:]
    }

    selected_pairs: list[dict] = []
    for group_id, group in sorted_groups:
        if group_id not in selected_group_ids:
            continue
        selected_pairs.extend(
            sorted(
                group["rows"],
                key=lambda pair: (
                    _finite_transfer_float(pair.get("pair_id")) or 0.0,
                    str(pair.get("range_label") or ""),
                ),
            )
        )
    return selected_pairs


def _transfer_economics_valid(
    *,
    n_pairs: object,
    brier_source: object,
    brier_target: object,
    brier_diff: object,
    brier_diff_threshold: object,
) -> bool:
    try:
        sample_count = int(n_pairs)
    except (TypeError, ValueError):
        return False
    if sample_count < MIN_TRANSFER_EVIDENCE_PAIRS:
        return False
    source = _finite_brier(brier_source)
    target = _finite_brier(brier_target)
    diff = _finite_transfer_float(brier_diff)
    threshold = _finite_brier_threshold(brier_diff_threshold)
    if source is None or target is None or diff is None:
        return False
    if threshold is None:
        return False
    if not (-1.0 <= diff <= 1.0):
        return False
    return math.isclose(diff, target - source, rel_tol=1e-9, abs_tol=1e-9)


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _logit(p: float) -> float:
    p = max(1e-7, min(1.0 - 1e-7, p))
    return math.log(p / (1.0 - p))


def _apply_platt(p_raw: float, lead_days: float, A: float, B: float, C: float) -> float:
    return _sigmoid(A * _logit(p_raw) + B * lead_days + C)


def _brier_score(predictions: list[float], outcomes: list[int]) -> float | None:
    if not predictions:
        return None
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / len(predictions)


def source_platt_transfer_evidence_valid(
    conn: sqlite3.Connection,
    *,
    platt_model_key: str,
    source_id: str,
    source_cycle: str,
    horizon_profile: str,
    season: str,
    cluster: str,
    metric: str,
    brier_source: object,
    evaluated_at: object,
) -> bool:
    """Validate that a transfer row still points at a mature source Platt model."""
    expected_brier = _finite_brier(brier_source)
    if expected_brier is None:
        return False
    evidence_time = _parse_evidence_time(evaluated_at)
    if evidence_time is None:
        return False
    evidence_time = evidence_time.astimezone(timezone.utc)
    try:
        row = conn.execute(
            """
            SELECT n_samples, brier_insample, authority, input_space,
                   source_id, cycle, horizon_profile, season, cluster,
                   temperature_metric, fitted_at, recorded_at, is_active,
                   param_A, param_B, param_C
              FROM platt_models_v2
             WHERE model_key = ?
             LIMIT 1
            """,
            (platt_model_key,),
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    try:
        n_samples = int(row[0])
    except (TypeError, ValueError):
        return False
    if n_samples < MIN_SOURCE_PLATT_SAMPLES:
        return False
    current_brier = _finite_brier(row[1])
    if current_brier is None or not math.isclose(
        current_brier,
        expected_brier,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        return False
    fitted_at = _parse_evidence_time(row[10])
    recorded_at = _parse_evidence_time(row[11])
    if fitted_at is None or recorded_at is None:
        return False
    if fitted_at.astimezone(timezone.utc) > evidence_time:
        return False
    if recorded_at.astimezone(timezone.utc) > evidence_time:
        return False
    try:
        is_active = int(row[12])
    except (TypeError, ValueError):
        return False
    if (
        _finite_transfer_float(row[13]) is None
        or _finite_transfer_float(row[14]) is None
        or _finite_transfer_float(row[15]) is None
    ):
        return False
    return (
        is_active == 1
        and row[2] == "VERIFIED"
        and row[3] == "raw_probability"
        and _nonempty_transfer_text(row[4]) == source_id
        and _nonempty_transfer_text(row[5]) == source_cycle
        and _nonempty_transfer_text(row[6]) == horizon_profile
        and _nonempty_transfer_text(row[7]) == season
        and _nonempty_transfer_text(row[8]) == cluster
        and _nonempty_transfer_text(row[9]) == metric
    )


def target_transfer_cohort_evidence_valid(
    conn: sqlite3.Connection,
    *,
    target_source_id: str,
    target_cycle: str,
    horizon_profile: str,
    season: str,
    cluster: str,
    metric: str,
    platt_model_key: str,
    n_pairs: object,
    brier_source: object,
    brier_target: object,
    brier_diff: object,
    evaluated_at: object,
) -> bool:
    """Recompute the target held-out cohort behind a transfer evidence row."""
    evidence_time = _parse_evidence_time(evaluated_at)
    if evidence_time is None:
        return False
    evidence_time = evidence_time.astimezone(timezone.utc)
    try:
        expected_n_pairs = int(n_pairs)
    except (TypeError, ValueError):
        return False
    if expected_n_pairs < MIN_TRANSFER_EVIDENCE_PAIRS:
        return False
    expected_source = _finite_brier(brier_source)
    expected_target = _finite_brier(brier_target)
    expected_diff = _finite_transfer_float(brier_diff)
    if expected_source is None or expected_target is None or expected_diff is None:
        return False
    try:
        model = conn.execute(
            """
            SELECT param_A, param_B, param_C
              FROM platt_models_v2
             WHERE model_key = ?
               AND is_active = 1
             LIMIT 1
            """,
            (platt_model_key,),
        ).fetchone()
    except sqlite3.Error:
        return False
    if model is None:
        return False
    A = _finite_transfer_float(model[0])
    B = _finite_transfer_float(model[1])
    C = _finite_transfer_float(model[2])
    if A is None or B is None or C is None:
        return False
    try:
        rows = conn.execute(
            """
            SELECT pair_id, p_raw, lead_days, outcome, recorded_at,
                   forecast_available_at, target_date, decision_group_id,
                   range_label
              FROM calibration_pairs_v2
             WHERE source_id           = ?
               AND cycle               = ?
               AND season              = ?
               AND cluster             = ?
               AND temperature_metric  = ?
               AND horizon_profile     = ?
               AND training_allowed    = 1
               AND causality_status    = 'OK'
               AND authority           = 'VERIFIED'
               AND TRIM(source_id)     <> ''
               AND TRIM(cycle)         <> ''
               AND TRIM(season)        <> ''
               AND TRIM(cluster)       <> ''
               AND TRIM(horizon_profile) <> ''
               AND p_raw IS NOT NULL
               AND p_raw > 0.0
               AND p_raw < 1.0
               AND lead_days IS NOT NULL
               AND lead_days >= 1.0
               AND lead_days <= 7.0
               AND outcome IN (0, 1)
               AND decision_group_id IS NOT NULL
               AND TRIM(decision_group_id) <> ''
             GROUP BY pair_id, p_raw, lead_days, outcome, recorded_at,
                      forecast_available_at, target_date, decision_group_id,
                      range_label
             ORDER BY forecast_available_at, target_date, pair_id
            """,
            (target_source_id, target_cycle, season, cluster, metric, horizon_profile),
        ).fetchall()
    except sqlite3.Error:
        return False
    candidate_pairs: list[dict] = []
    for row in rows:
        p_raw = _finite_probability(row[1])
        lead_days = _finite_transfer_float(row[2])
        outcome = int(row[3]) if row[3] in (0, 1) else None
        recorded_at = _parse_evidence_time(row[4])
        if (
            p_raw is None
            or lead_days is None
            or not (MIN_TRANSFER_LEAD_DAYS <= lead_days <= MAX_TRANSFER_LEAD_DAYS)
            or outcome is None
            or recorded_at is None
            or recorded_at.astimezone(timezone.utc) > evidence_time
        ):
            return False
        candidate_pairs.append(
            {
                "pair_id": row[0],
                "p_raw": p_raw,
                "lead_days": lead_days,
                "outcome": outcome,
                "recorded_at": row[4],
                "forecast_available_at": row[5],
                "target_date": row[6],
                "decision_group_id": row[7],
                "range_label": row[8],
            }
        )
    held_out_pairs = select_time_blocked_transfer_pairs(candidate_pairs)
    predictions = [
        _apply_platt(pair["p_raw"], pair["lead_days"], A, B, C)
        for pair in held_out_pairs
    ]
    outcomes = [pair["outcome"] for pair in held_out_pairs]
    if len(predictions) != expected_n_pairs:
        return False
    computed_target = _brier_score(predictions, outcomes)
    if computed_target is None or not math.isclose(
        computed_target,
        expected_target,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        return False
    return math.isclose(
        computed_target - expected_source,
        expected_diff,
        rel_tol=1e-9,
        abs_tol=1e-9,
    )


def evaluate_calibration_transfer_policy(
    *,
    config: EntryForecastConfig,
    source_id: str,
    forecast_data_version: str,
    live_promotion_approved: bool = False,
) -> CalibrationTransferDecision:
    policy_id = config.calibration_policy_id.value
    if os.environ.get("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "false").lower() == "true":
        # Flag is on — this legacy path should not be reached except via the
        # _with_evidence fallback. If a caller still calls legacy directly,
        # refuse to honor live_promotion_approved: the evidence row is the
        # authority once the OOS gate is active.
        warnings.warn(
            "evaluate_calibration_transfer_policy called directly while "
            "ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true. live_promotion_approved "
            "is ignored; direct legacy calls fail closed. Migrate caller to "
            "evaluate_calibration_transfer_policy_with_evidence.",
            DeprecationWarning,
            stacklevel=2,
        )
        return CalibrationTransferDecision(
            status="BLOCKED",
            reason_codes=("CALIBRATION_TRANSFER_LEGACY_PATH_DISABLED",),
            policy_id=policy_id,
            forecast_data_version=forecast_data_version,
            calibration_data_version=None,
            live_promotion_approved=False,
            note="legacy_disabled_by_oos_evidence_gate",
        )
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
    target_source_id: Optional[str],
    source_cycle: Optional[str],
    target_cycle: Optional[str],
    horizon_profile: Optional[str],
    season: Optional[str],
    cluster: Optional[str],
    metric: str,
    platt_model_key: Optional[str],
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

    source_id_s = _nonempty_transfer_text(source_id)
    target_source_id_s = _nonempty_transfer_text(target_source_id)
    source_cycle_s = _nonempty_transfer_text(source_cycle)
    target_cycle_s = _nonempty_transfer_text(target_cycle)
    horizon_profile_s = _nonempty_transfer_text(horizon_profile)
    season_s = _nonempty_transfer_text(season)
    cluster_s = _nonempty_transfer_text(cluster)
    metric_s = _nonempty_transfer_text(metric)
    platt_model_key_s = _nonempty_transfer_text(platt_model_key)

    # Same-domain fast-path: no transfer occurs when source and target are
    # identical on both source identity and cycle.
    # Guard: if route keys are None/empty (pre-forecast readiness write), the
    # equality comparison would spuriously fire the same-domain path for
    # unresolved routes. Reject unresolved route keys as INSUFFICIENT_INFO →
    # SHADOW_ONLY so the readiness writer never marks them LIVE_ELIGIBLE.
    if (
        source_id_s is None
        or target_source_id_s is None
        or source_cycle_s is None
        or target_cycle_s is None
    ):
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_INSUFFICIENT_ROUTE_INFO",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="none_route_keys_insufficient_info",
        )
    if source_id_s == target_source_id_s and source_cycle_s == target_cycle_s:
        return CalibrationTransferDecision(
            status="LIVE_ELIGIBLE",
            reason_codes=("CALIBRATION_TRANSFER_APPROVED",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            # DB row is the authority when flag is on; live_promotion_approved
            # is set True on LIVE_ELIGIBLE so the readiness writer's gate
            # (which checks this flag) passes correctly. (arch doc 2026-05-05
            # §"live_promotion_approved flag is REMOVED — DB row is authority")
            live_promotion_approved=True,
            note="same_domain_no_transfer",
        )

    if (
        horizon_profile_s is None
        or season_s is None
        or cluster_s is None
        or metric_s is None
        or platt_model_key_s is None
    ):
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_INSUFFICIENT_ROUTE_INFO",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="none_route_keys_insufficient_info",
        )

    # Evidence query: look up validated_calibration_transfers row.
    row = conn.execute(
        """
        SELECT status, evaluated_at, n_pairs, brier_source, brier_target,
               brier_diff, brier_diff_threshold
          FROM validated_calibration_transfers
         WHERE policy_id        = ?
           AND source_id        = ?
           AND target_source_id = ?
           AND source_cycle     = ?
           AND target_cycle     = ?
           AND season           = ?
           AND cluster          = ?
           AND metric           = ?
           AND horizon_profile  = ?
            AND platt_model_key  = ?
         LIMIT 1
        """,
        (
            policy_id,
            source_id_s,
            target_source_id_s,
            source_cycle_s,
            target_cycle_s,
            season_s,
            cluster_s,
            metric_s,
            horizon_profile_s,
            platt_model_key_s,
        ),
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

    (
        row_status,
        evaluated_at_str,
        n_pairs,
        brier_source,
        brier_target,
        brier_diff,
        brier_diff_threshold,
    ) = row
    if not _transfer_economics_valid(
        n_pairs=n_pairs,
        brier_source=brier_source,
        brier_target=brier_target,
        brier_diff=brier_diff,
        brier_diff_threshold=brier_diff_threshold,
    ):
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_INVALID_EVIDENCE",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="invalid_evidence_row",
        )
    resolved_brier_diff = _finite_transfer_float(brier_diff)
    resolved_brier_diff_threshold = _finite_brier_threshold(brier_diff_threshold)
    if (
        resolved_brier_diff is None
        or resolved_brier_diff_threshold is None
        or resolved_brier_diff > resolved_brier_diff_threshold
    ):
        return CalibrationTransferDecision(
            status="BLOCKED",
            reason_codes=("CALIBRATION_TRANSFER_UNSAFE",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="db_row_transfer_unsafe_by_economics",
        )
    evaluated_at = _parse_evidence_time(evaluated_at_str)
    if evaluated_at is None:
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_INVALID_EVIDENCE_TIME",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="invalid_evidence_time",
        )
    # Make both timezone-aware or both naive for comparison.
    if now.tzinfo is not None and evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    elif now.tzinfo is None and evaluated_at.tzinfo is not None:
        now = now.replace(tzinfo=timezone.utc)

    if evaluated_at > now:
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_FUTURE_EVIDENCE",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="future_evidence_time",
        )

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

    if not source_platt_transfer_evidence_valid(
        conn,
        platt_model_key=platt_model_key_s,
        source_id=source_id_s,
        source_cycle=source_cycle_s,
        horizon_profile=horizon_profile_s,
        season=season_s,
        cluster=cluster_s,
        metric=metric_s,
        brier_source=brier_source,
        evaluated_at=evaluated_at_str,
    ):
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_INVALID_SOURCE_MODEL_EVIDENCE",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="invalid_source_platt_evidence",
        )

    if not target_transfer_cohort_evidence_valid(
        conn,
        target_source_id=target_source_id_s,
        target_cycle=target_cycle_s,
        horizon_profile=horizon_profile_s,
        season=season_s,
        cluster=cluster_s,
        metric=metric_s,
        platt_model_key=platt_model_key_s,
        n_pairs=n_pairs,
        brier_source=brier_source,
        brier_target=brier_target,
        brier_diff=brier_diff,
        evaluated_at=evaluated_at_str,
    ):
        return CalibrationTransferDecision(
            status="SHADOW_ONLY",
            reason_codes=("CALIBRATION_TRANSFER_INVALID_TARGET_COHORT_EVIDENCE",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            live_promotion_approved=False,
            note="invalid_target_cohort_evidence",
        )

    if row_status == "LIVE_ELIGIBLE":
        return CalibrationTransferDecision(
            status="LIVE_ELIGIBLE",
            reason_codes=("CALIBRATION_TRANSFER_APPROVED",),
            policy_id=policy_id,
            forecast_data_version="",
            calibration_data_version=None,
            # DB row is the authority; live_promotion_approved=True so the
            # readiness writer's gate passes. (arch doc 2026-05-05
            # §"live_promotion_approved flag is REMOVED — DB row is authority")
            live_promotion_approved=True,
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
