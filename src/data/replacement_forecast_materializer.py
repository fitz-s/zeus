"""Materialize replacement forecast shadow posterior rows into forecast DB."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Mapping, Sequence

from src.data.ecmwf_aifs_sampled_2t_localday import (
    HIGH_DATA_VERSION as AIFS_HIGH_DATA_VERSION,
    LOW_DATA_VERSION as AIFS_LOW_DATA_VERSION,
    PRODUCT_ID as AIFS_PRODUCT_ID,
    SOURCE_ID as AIFS_SOURCE_ID,
    AifsSampledLocalDayExtraction,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import (
    HIGH_DATA_VERSION as ANCHOR_HIGH_DATA_VERSION,
    LOW_DATA_VERSION as ANCHOR_LOW_DATA_VERSION,
    PRODUCT_ID as ANCHOR_PRODUCT_ID,
    SOURCE_ID as ANCHOR_SOURCE_ID,
    OpenMeteoIfs9LocalDayAnchor,
)
from src.data.openmeteo_ecmwf_ifs9_precision_guard import OpenMeteoIfs9PrecisionGuardResult
from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION, LOW_DATA_VERSION
from src.data.replacement_forecast_readiness import (
    PRODUCT_ID,
    SOURCE_ID,
    STRATEGY_KEY,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
from src.state.readiness_repo import write_readiness_state
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin, build_openmeteo_ifs9_aifs_soft_anchor_result
from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import SoftAnchorConfig


UTC = timezone.utc


@dataclass(frozen=True)
class ReplacementForecastMaterializeRequest:
    city: str
    city_id: str
    city_timezone: str
    target_date: date | str
    temperature_metric: str
    baseline_source_run_id: str
    baseline_data_version: str
    baseline_source_available_at: datetime | str
    aifs_extraction: AifsSampledLocalDayExtraction
    aifs_source_run_id: str
    aifs_source_available_at: datetime | str
    openmeteo_anchor: OpenMeteoIfs9LocalDayAnchor
    openmeteo_source_run_id: str | None
    openmeteo_source_available_at: datetime | str
    bins: Sequence[AifsTemperatureBin]
    source_cycle_time: datetime | str
    computed_at: datetime | str
    expires_at: datetime | str | None = None
    anchor_artifact_id: int | None = None
    aifs_artifact_id: int | None = None
    openmeteo_precision_guard: OpenMeteoIfs9PrecisionGuardResult | None = None
    anchor_weight: float = 0.80
    anchor_sigma_c: float = 3.00
    settlement_step_c: float = 1.0


@dataclass(frozen=True)
class ReplacementForecastMaterializeResult:
    status: str
    reason_codes: tuple[str, ...]
    posterior_id: int | None
    anchor_id: int | None
    readiness_id: str | None

    @property
    def ok(self) -> bool:
        return self.status == "SHADOW_ONLY"


def _to_utc(value: datetime | str, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _date_text(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        date.fromisoformat(value)
        return value
    raise ValueError("target_date must be date or ISO date string")


def _metric(value: str) -> str:
    if value not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
    return value


def _data_version(metric: str) -> str:
    return HIGH_DATA_VERSION if metric == "high" else LOW_DATA_VERSION


def _anchor_data_version(metric: str) -> str:
    return ANCHOR_HIGH_DATA_VERSION if metric == "high" else ANCHOR_LOW_DATA_VERSION


def _aifs_data_version(metric: str) -> str:
    return AIFS_HIGH_DATA_VERSION if metric == "high" else AIFS_LOW_DATA_VERSION


def _json(value: Mapping[str, object] | Sequence[object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _precision_guard_payload(guard: OpenMeteoIfs9PrecisionGuardResult) -> dict[str, object]:
    return {
        "status": guard.status,
        "reason_codes": list(guard.reason_codes),
        "elevation_delta_m": guard.elevation_delta_m,
        "high_risk_bucket": guard.high_risk_bucket,
        "metadata": asdict(guard.metadata),
    }


def _precision_guard_block_reason(
    request: ReplacementForecastMaterializeRequest,
) -> tuple[str, ...]:
    guard = request.openmeteo_precision_guard
    if guard is None:
        return ("OM9_PRECISION_GUARD_REQUIRED_FOR_MATERIALIZATION",)
    if not guard.passable_for_shadow_veto:
        return ("OM9_PRECISION_GUARD_BLOCKED_MATERIALIZATION", *guard.reason_codes)
    return ()


def _prewrite_block_reasons(request: ReplacementForecastMaterializeRequest) -> tuple[str, ...]:
    metric = _metric(request.temperature_metric)
    computed_at = _to_utc(request.computed_at, field_name="computed_at")
    reasons: list[str] = []
    dependency_times = (
        ("baseline_b0", _to_utc(request.baseline_source_available_at, field_name="baseline_source_available_at")),
        ("aifs_sampled_2t", _to_utc(request.aifs_source_available_at, field_name="aifs_source_available_at")),
        ("openmeteo_ifs9_anchor", _to_utc(request.openmeteo_source_available_at, field_name="openmeteo_source_available_at")),
    )
    expected = expected_replacement_dependency_identity_by_role(metric)
    if not str(request.baseline_source_run_id or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_BASELINE_SOURCE_RUN_ID_MISSING")
    if not str(request.aifs_source_run_id or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_SOURCE_RUN_ID_MISSING")
    if not str(request.openmeteo_source_run_id or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_OPENMETEO_SOURCE_RUN_ID_MISSING")
    if request.baseline_data_version != expected["baseline_b0"].data_version:
        reasons.append("REPLACEMENT_MATERIALIZATION_BASELINE_DATA_VERSION_MISMATCH")
    if any(source_available_at > computed_at for _, source_available_at in dependency_times):
        reasons.append("REPLACEMENT_MATERIALIZATION_DEPENDENCY_AFTER_COMPUTED_AT")
    if request.expires_at is not None and _to_utc(request.expires_at, field_name="expires_at") <= computed_at:
        reasons.append("REPLACEMENT_MATERIALIZATION_EXPIRY_NOT_AFTER_COMPUTED_AT")
    return tuple(reasons)


def _insert_anchor(conn: sqlite3.Connection, request: ReplacementForecastMaterializeRequest, *, metric: str) -> int:
    anchor = request.openmeteo_anchor
    target_date = _date_text(request.target_date)
    source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat()
    source_available_at = _to_utc(request.openmeteo_source_available_at, field_name="openmeteo_source_available_at").isoformat()
    computed_at = _to_utc(request.computed_at, field_name="computed_at").isoformat()
    value_c = anchor.high_c if metric == "high" else anchor.low_c
    conn.execute(
        """
        INSERT INTO deterministic_forecast_anchors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, anchor_value_c, source_cycle_time,
            source_available_at, captured_at, artifact_id, model, native_grid,
            delivery_grid_resolution, interpolation_method,
            contributing_times_json, provenance_json,
            trade_authority_status, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, product_id, data_version, city, target_date, temperature_metric, source_cycle_time)
        DO UPDATE SET
            anchor_value_c = excluded.anchor_value_c,
            source_available_at = excluded.source_available_at,
            captured_at = excluded.captured_at,
            artifact_id = excluded.artifact_id,
            contributing_times_json = excluded.contributing_times_json,
            provenance_json = excluded.provenance_json
        """,
        (
            ANCHOR_SOURCE_ID,
            ANCHOR_PRODUCT_ID,
            _anchor_data_version(metric),
            request.city,
            target_date,
            metric,
            float(value_c),
            source_cycle_time,
            source_available_at,
            computed_at,
            request.anchor_artifact_id,
            anchor.model,
            "openmeteo_single_runs_ecmwf_ifs_9km",
            "9km/0.1_degree",
            "openmeteo_api_point_interpolation",
            _json([item.isoformat() for item in anchor.contributing_valid_times_utc]),
            _json(
                {
                    "city_timezone": request.city_timezone,
                    "source_run_id": request.openmeteo_source_run_id,
                    "measurement_policy": anchor.measurement_policy,
                    "precision_guard": _precision_guard_payload(request.openmeteo_precision_guard),
                    "role": "soft_spatial_anchor",
                    "trade_authority_status": "SHADOW_ONLY",
                    "training_allowed": False,
                }
            ),
            "SHADOW_ONLY",
            0,
        ),
    )
    row = conn.execute(
        """
        SELECT anchor_id FROM deterministic_forecast_anchors
        WHERE source_id = ? AND product_id = ? AND data_version = ?
          AND city = ? AND target_date = ? AND temperature_metric = ?
          AND source_cycle_time = ?
        """,
        (ANCHOR_SOURCE_ID, ANCHOR_PRODUCT_ID, _anchor_data_version(metric), request.city, target_date, metric, source_cycle_time),
    ).fetchone()
    if row is None:
        raise RuntimeError("replacement anchor materialization failed")
    return int(row[0] if not isinstance(row, sqlite3.Row) else row["anchor_id"])


def _insert_posterior(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    anchor_id: int,
) -> int:
    result = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=request.aifs_extraction,
        openmeteo_anchor=request.openmeteo_anchor,
        metric=metric,
        bins=request.bins,
        config=SoftAnchorConfig(anchor_weight=request.anchor_weight, anchor_sigma_c=request.anchor_sigma_c),
        settlement_step_c=float(request.settlement_step_c),
    )
    target_date = _date_text(request.target_date)
    source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat()
    available_at = max(
        _to_utc(request.baseline_source_available_at, field_name="baseline_source_available_at"),
        _to_utc(request.aifs_source_available_at, field_name="aifs_source_available_at"),
        _to_utc(request.openmeteo_source_available_at, field_name="openmeteo_source_available_at"),
    ).isoformat()
    computed_at = _to_utc(request.computed_at, field_name="computed_at").isoformat()
    data_version = _data_version(metric)
    q = {key: float(value) for key, value in result.posterior.probabilities.items()}
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, posterior_method,
            aifs_source_run_id, openmeteo_anchor_id,
            dependency_source_run_ids_json, provenance_json,
            trade_authority_status, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, product_id, data_version, city, target_date, temperature_metric, source_cycle_time)
        DO UPDATE SET
            source_available_at = excluded.source_available_at,
            computed_at = excluded.computed_at,
            q_json = excluded.q_json,
            q_lcb_json = excluded.q_lcb_json,
            aifs_source_run_id = excluded.aifs_source_run_id,
            openmeteo_anchor_id = excluded.openmeteo_anchor_id,
            dependency_source_run_ids_json = excluded.dependency_source_run_ids_json,
            provenance_json = excluded.provenance_json,
            trade_authority_status = excluded.trade_authority_status,
            training_allowed = excluded.training_allowed
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            data_version,
            request.city,
            target_date,
            metric,
            source_cycle_time,
            available_at,
            computed_at,
            _json(q),
            _json(q),
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            request.aifs_source_run_id,
            anchor_id,
            _json(
                {
                    "baseline_b0": request.baseline_source_run_id,
                    "aifs_sampled_2t": request.aifs_source_run_id,
                    "openmeteo_ifs9_anchor": request.openmeteo_source_run_id,
                }
            ),
            _json(
                {
                    "anchor_weight": request.anchor_weight,
                    "anchor_sigma_c": request.anchor_sigma_c,
                    "anchor_value_c": result.anchor_value_c,
                    "aifs_artifact_id": request.aifs_artifact_id,
                    "openmeteo_anchor_artifact_id": request.anchor_artifact_id,
                    "openmeteo_precision_guard": _precision_guard_payload(request.openmeteo_precision_guard),
                    "aifs_probabilities": dict(result.aifs_probabilities.probabilities),
                    "aifs_member_count": len(result.aifs_probabilities.member_values_c),
                    "q_lcb_json_role": "shadow_point_probability_capped_downstream",
                    "trade_authority_status": "SHADOW_VETO_ONLY",
                    "training_allowed": False,
                }
            ),
            "SHADOW_VETO_ONLY",
            0,
        ),
    )
    row = conn.execute(
        """
        SELECT posterior_id FROM forecast_posteriors
        WHERE source_id = ? AND product_id = ? AND data_version = ?
          AND city = ? AND target_date = ? AND temperature_metric = ?
          AND source_cycle_time = ?
        """,
        (SOURCE_ID, PRODUCT_ID, data_version, request.city, target_date, metric, source_cycle_time),
    ).fetchone()
    if row is None:
        raise RuntimeError("replacement posterior materialization failed")
    return int(row[0] if not isinstance(row, sqlite3.Row) else row["posterior_id"])


def _build_readiness(
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    posterior_id: int,
    anchor_id: int,
):
    expected = expected_replacement_dependency_identity_by_role(metric)
    computed_at = _to_utc(request.computed_at, field_name="computed_at")
    expires_at = (
        _to_utc(request.expires_at, field_name="expires_at")
        if request.expires_at is not None
        else computed_at + timedelta(hours=3)
    )
    return build_replacement_forecast_readiness(
        city=request.city,
        target_date=request.target_date,
        temperature_metric=metric,
        decision_time=computed_at,
        computed_at=computed_at,
        expires_at=expires_at,
        dependencies=(
            ReplacementForecastDependency(
                role="baseline_b0",
                source_id=expected["baseline_b0"].source_id,
                product_id=expected["baseline_b0"].product_id,
                data_version=request.baseline_data_version,
                source_run_id=request.baseline_source_run_id,
                source_available_at=request.baseline_source_available_at,
            ),
            ReplacementForecastDependency(
                role="aifs_sampled_2t",
                source_id=AIFS_SOURCE_ID,
                product_id=AIFS_PRODUCT_ID,
                data_version=_aifs_data_version(metric),
                source_run_id=request.aifs_source_run_id,
                source_available_at=request.aifs_source_available_at,
                artifact_id=request.aifs_artifact_id,
            ),
            ReplacementForecastDependency(
                role="openmeteo_ifs9_anchor",
                source_id=ANCHOR_SOURCE_ID,
                product_id=ANCHOR_PRODUCT_ID,
                data_version=_anchor_data_version(metric),
                source_run_id=request.openmeteo_source_run_id,
                source_available_at=request.openmeteo_source_available_at,
                artifact_id=request.anchor_artifact_id,
                anchor_id=anchor_id,
            ),
            ReplacementForecastDependency(
                role="soft_anchor_posterior",
                source_id=SOURCE_ID,
                product_id=PRODUCT_ID,
                data_version=_data_version(metric),
                source_run_id=f"posterior:{posterior_id}",
                source_available_at=computed_at,
                posterior_id=posterior_id,
            ),
        ),
    )


def materialize_replacement_forecast_shadow(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
) -> ReplacementForecastMaterializeResult:
    """Write anchor, posterior, and readiness rows for replacement shadow/veto."""

    metric = _metric(request.temperature_metric)
    prewrite_reasons = _prewrite_block_reasons(request)
    if prewrite_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=prewrite_reasons,
            posterior_id=None,
            anchor_id=None,
            readiness_id=None,
        )
    precision_block_reasons = _precision_guard_block_reason(request)
    if precision_block_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=precision_block_reasons,
            posterior_id=None,
            anchor_id=None,
            readiness_id=None,
        )
    anchor_id = _insert_anchor(conn, request, metric=metric)
    posterior_id = _insert_posterior(conn, request, metric=metric, anchor_id=anchor_id)
    readiness = _build_readiness(request, metric=metric, posterior_id=posterior_id, anchor_id=anchor_id)
    expected = expected_replacement_dependency_identity_by_role(metric)["soft_anchor_posterior"]
    write_readiness_state(
        conn,
        readiness_id=readiness.readiness_id,
        scope_type="strategy",
        status=readiness.status,
        computed_at=request.computed_at,
        city_id=request.city_id,
        city=request.city,
        city_timezone=request.city_timezone,
        target_local_date=request.target_date,
        metric=metric,
        temperature_metric=metric,
        physical_quantity=expected.physical_quantity,
        observation_field=expected.observation_field,
        data_version=_data_version(metric),
        source_id=SOURCE_ID,
        track="soft_anchor_posterior",
        source_run_id=f"posterior:{posterior_id}",
        strategy_key=STRATEGY_KEY,
        reason_codes_json=list(readiness.reason_codes),
        expires_at=readiness.expires_at,
        dependency_json=readiness.dependency_json,
        provenance_json=readiness.provenance_json,
    )
    return ReplacementForecastMaterializeResult(
        status=readiness.status,
        reason_codes=readiness.reason_codes,
        posterior_id=posterior_id,
        anchor_id=anchor_id,
        readiness_id=readiness.readiness_id,
    )
