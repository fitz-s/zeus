"""Materialize replacement forecast shadow posterior rows into forecast DB."""

from __future__ import annotations

import json
import math
import sqlite3
import hashlib
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Mapping, Sequence

from src.data.ecmwf_aifs_sampled_2t_localday import (
    HIGH_DATA_VERSION as AIFS_HIGH_DATA_VERSION,
    LOW_DATA_VERSION as AIFS_LOW_DATA_VERSION,
    PRODUCT_ID as AIFS_PRODUCT_ID,
    SOURCE_ID as AIFS_SOURCE_ID,
    EXPECTED_AIFS_MEMBER_COUNT,
    expected_aifs_sample_steps_for_local_day,
    AifsSampledLocalDayExtraction,
)
from src.data.forecast_target_contract import compute_target_local_day_window_utc
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
    READY_STATUS,
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
        return self.status == READY_STATUS


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


def _json_hash(value: Mapping[str, object] | Sequence[object]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_replacement_identity_columns(conn: sqlite3.Connection) -> None:
    """Keep old PR399 shadow DBs fail-closed instead of returning stale rows."""

    anchor_columns = _table_columns(conn, "deterministic_forecast_anchors")
    if anchor_columns and "anchor_identity_hash" not in anchor_columns:
        conn.execute("ALTER TABLE deterministic_forecast_anchors ADD COLUMN anchor_identity_hash TEXT")
    posterior_columns = _table_columns(conn, "forecast_posteriors")
    for column in (
        "q_ucb_json",
        "family_id",
        "bin_topology_hash",
        "dependency_hash",
        "posterior_config_hash",
        "posterior_identity_hash",
    ):
        if posterior_columns and column not in posterior_columns:
            conn.execute(f"ALTER TABLE forecast_posteriors ADD COLUMN {column} TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_deterministic_forecast_anchors_identity_hash
            ON deterministic_forecast_anchors(anchor_identity_hash)
            WHERE anchor_identity_hash IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_forecast_posteriors_topology
            ON forecast_posteriors(city, target_date, temperature_metric, bin_topology_hash, computed_at)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_posteriors_identity_hash
            ON forecast_posteriors(posterior_identity_hash)
            WHERE posterior_identity_hash IS NOT NULL
        """
    )


def _bin_topology_payload(bins: Sequence[AifsTemperatureBin], *, settlement_step_c: float) -> list[dict[str, object]]:
    return [
        {
            "bin_id": item.bin_id,
            "lower_c": item.lower_c,
            "upper_c": item.upper_c,
            "center_c": item.center_c,
            "display_unit": item.display_unit,
            "settlement_unit": item.settlement_unit,
            "rounding_rule": item.rounding_rule,
            "settlement_step_c": float(settlement_step_c),
        }
        for item in bins
    ]


def _expected_om9_hourly_count(*, city_timezone: str, target_date: date | str) -> int:
    window = compute_target_local_day_window_utc(
        city_timezone=city_timezone,
        target_local_date=date.fromisoformat(target_date) if isinstance(target_date, str) else target_date,
    )
    seconds = (window.end_utc - window.start_utc).total_seconds()
    return int(seconds // 3600)


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
    request_source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time")
    target_date_value = date.fromisoformat(_date_text(request.target_date))
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
    if len(request.aifs_extraction.members) != EXPECTED_AIFS_MEMBER_COUNT:
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_MEMBER_COVERAGE_INCOMPLETE")
    if not request.aifs_extraction.identity_decision_valid:
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_GRIB_IDENTITY_INVALID")
    if request.aifs_extraction.identity_reason_codes != ("AIFS_GRIB_IDENTITY_VALID",):
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_GRIB_IDENTITY_REASON_MISMATCH")
    if not str(request.aifs_extraction.artifact_id or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_ARTIFACT_ID_MISSING")
    elif request.aifs_artifact_id is not None and int(request.aifs_extraction.artifact_id) != int(request.aifs_artifact_id):
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_ARTIFACT_ID_MISMATCH")
    if not str(request.aifs_extraction.raw_sha256 or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_RAW_SHA256_MISSING")
    if request.aifs_extraction.source_product_id != AIFS_PRODUCT_ID:
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_PRODUCT_ID_MISMATCH")
    if request.aifs_extraction.source_cycle_time is None:
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_SOURCE_CYCLE_TIME_MISSING")
    else:
        aifs_source_cycle_time = _to_utc(request.aifs_extraction.source_cycle_time, field_name="aifs_source_cycle_time")
        if aifs_source_cycle_time != request_source_cycle_time:
            reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_SOURCE_CYCLE_TIME_MISMATCH")
        expected_steps = expected_aifs_sample_steps_for_local_day(
            source_cycle_time=request.aifs_extraction.source_cycle_time,
            city_timezone=request.city_timezone,
            target_local_date=target_date_value,
        )
        for member in request.aifs_extraction.members:
            observed_steps = tuple(
                sorted(
                    int((valid_time - request.aifs_extraction.source_cycle_time).total_seconds() // 3600)
                    for valid_time in member.contributing_valid_times_utc
                )
            )
            if observed_steps != expected_steps:
                reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_STEP_COVERAGE_INCOMPLETE")
                break
    if request.openmeteo_anchor.source_cycle_time is None:
        reasons.append("REPLACEMENT_MATERIALIZATION_OM9_SOURCE_CYCLE_TIME_MISSING")
    else:
        openmeteo_source_cycle_time = _to_utc(request.openmeteo_anchor.source_cycle_time, field_name="openmeteo_source_cycle_time")
        if openmeteo_source_cycle_time != request_source_cycle_time:
            reasons.append("REPLACEMENT_MATERIALIZATION_OM9_SOURCE_CYCLE_TIME_MISMATCH")
    expected_om9_count = _expected_om9_hourly_count(
        city_timezone=request.city_timezone,
        target_date=request.target_date,
    )
    if request.openmeteo_anchor.sample_count != expected_om9_count:
        reasons.append("REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE")
    target_window = compute_target_local_day_window_utc(
        city_timezone=request.city_timezone,
        target_local_date=target_date_value,
    )
    if target_window.start_utc <= computed_at < target_window.end_utc:
        reasons.append("REPLACEMENT_MATERIALIZATION_DAY0_OBSERVED_EXTREME_REQUIRED")
    if any(source_available_at > computed_at for _, source_available_at in dependency_times):
        reasons.append("REPLACEMENT_MATERIALIZATION_DEPENDENCY_AFTER_COMPUTED_AT")
    if request.expires_at is not None and _to_utc(request.expires_at, field_name="expires_at") <= computed_at:
        reasons.append("REPLACEMENT_MATERIALIZATION_EXPIRY_NOT_AFTER_COMPUTED_AT")
    return tuple(reasons)


def _artifact_identity_block_reasons(conn: sqlite3.Connection, request: ReplacementForecastMaterializeRequest) -> tuple[str, ...]:
    reasons: list[str] = []
    if request.aifs_extraction.artifact_id is None:
        return ("REPLACEMENT_MATERIALIZATION_AIFS_ARTIFACT_ID_MISSING",)
    request_source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat()
    request_aifs_available_at = _to_utc(request.aifs_source_available_at, field_name="aifs_source_available_at").isoformat()
    row = conn.execute(
        """
        SELECT artifact_id, product_id, sha256, source_cycle_time, source_available_at
        FROM raw_forecast_artifacts
        WHERE artifact_id = ?
        """,
        (int(request.aifs_extraction.artifact_id),),
    ).fetchone()
    if row is None:
        return ("REPLACEMENT_MATERIALIZATION_AIFS_ARTIFACT_ROW_MISSING",)
    row_map = dict(row)
    if str(row_map.get("product_id") or "") != request.aifs_extraction.source_product_id:
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_ARTIFACT_PRODUCT_MISMATCH")
    if str(row_map.get("sha256") or "") != str(request.aifs_extraction.raw_sha256 or ""):
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_ARTIFACT_SHA256_MISMATCH")
    if _to_utc(str(row_map.get("source_cycle_time") or ""), field_name="aifs_artifact_source_cycle_time").isoformat() != request_source_cycle_time:
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_ARTIFACT_SOURCE_CYCLE_TIME_MISMATCH")
    if _to_utc(str(row_map.get("source_available_at") or ""), field_name="aifs_artifact_source_available_at").isoformat() != request_aifs_available_at:
        reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_ARTIFACT_SOURCE_AVAILABLE_AT_MISMATCH")
    return tuple(reasons)


def _insert_anchor(conn: sqlite3.Connection, request: ReplacementForecastMaterializeRequest, *, metric: str) -> int:
    anchor = request.openmeteo_anchor
    target_date = _date_text(request.target_date)
    source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat()
    source_available_at = _to_utc(request.openmeteo_source_available_at, field_name="openmeteo_source_available_at").isoformat()
    computed_at = _to_utc(request.computed_at, field_name="computed_at").isoformat()
    value_c = anchor.high_c if metric == "high" else anchor.low_c
    contributing_times = [item.isoformat() for item in anchor.contributing_valid_times_utc]
    provenance = {
        "city_timezone": request.city_timezone,
        "source_run_id": request.openmeteo_source_run_id,
        "measurement_policy": anchor.measurement_policy,
        "precision_guard": _precision_guard_payload(request.openmeteo_precision_guard),
        "role": "soft_spatial_anchor",
        "trade_authority_status": "SHADOW_ONLY",
        "training_allowed": False,
    }
    anchor_identity_hash = _json_hash(
        {
            "source_id": ANCHOR_SOURCE_ID,
            "product_id": ANCHOR_PRODUCT_ID,
            "data_version": _anchor_data_version(metric),
            "city": request.city,
            "target_date": target_date,
            "temperature_metric": metric,
            "source_cycle_time": source_cycle_time,
            "source_available_at": source_available_at,
            "captured_at": computed_at,
            "artifact_id": request.anchor_artifact_id,
            "source_run_id": request.openmeteo_source_run_id,
            "anchor_value_c": float(value_c),
            "contributing_times": contributing_times,
            "precision_metadata": provenance["precision_guard"],
        }
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO deterministic_forecast_anchors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, anchor_value_c, source_cycle_time,
            source_available_at, captured_at, artifact_id, model, native_grid,
            delivery_grid_resolution, interpolation_method,
            contributing_times_json, anchor_identity_hash, provenance_json,
            trade_authority_status, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            _json(contributing_times),
            anchor_identity_hash,
            _json(provenance),
            "SHADOW_ONLY",
            0,
        ),
    )
    row = conn.execute(
        """
        SELECT anchor_id FROM deterministic_forecast_anchors
        WHERE anchor_identity_hash = ?
        """,
        (anchor_identity_hash,),
    ).fetchone()
    if row is None:
        raise RuntimeError("replacement anchor materialization failed")
    return int(row[0] if not isinstance(row, sqlite3.Row) else row["anchor_id"])


def _replacement_eb_bias_shift_c(
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
) -> float | None:
    """Flag-gated per-city EB bias shift (degC) for the replacement_0_1 center.

    P2_BLEND.md §3,§4,§5. Returns the degC shift to subtract from the AIFS member votes
    and the OM9 anchor center BEFORE the zero-prior veto, or None when the flag is OFF or
    no VERIFIED promoted bias exists (FAIL-CLOSED). REUSES the already-built
    zeus-world.model_bias_ens via src/calibration/replacement_eb_bias (ONE-BUILDER — no
    parallel store). Self-calibrating: the shift is whatever the accruing per-city VERIFIED
    residuals currently say (no hardcoded magnitude).

    The bias row is keyed by the LIVE forecast product the bias was fit on
    (model_bias_ens.live_data_version = the OpenData ECMWF ENS product, the same ECMWF
    family as AIFS — P2_BLEND.md §1b), NOT the soft-anchor posterior data_version. The key
    + the cell unit + season come from config so this surface holds no magic constants. Any
    failure / missing config / missing row degrades to None (no correction). Never raises.
    """
    try:
        from src.config import runtime_cities_by_name, settings  # noqa: PLC0415

        edli_cfg = settings["edli_v1"]
        if not bool(edli_cfg.get("replacement_0_1_eb_bias_correction_enabled", False)):
            return None

        # live_data_version the promoted bias was fit on (OpenData ENS product family).
        # Product-keyed (HIGH vs LOW); resolved from config, fail-closed if absent.
        ldv_map = edli_cfg.get("replacement_0_1_eb_bias_live_data_version") or {}
        bias_ldv = ldv_map.get(metric) if isinstance(ldv_map, dict) else None
        if not bias_ldv:
            return None

        city_obj = runtime_cities_by_name().get(request.city)
        if city_obj is None:
            return None
        lat = float(getattr(city_obj, "lat", 90.0))
        settlement_unit = str(getattr(city_obj, "settlement_unit", "C"))

        from src.contracts.season import season_from_date  # noqa: PLC0415

        target_date = _date_text(request.target_date)
        season = season_from_date(target_date, lat=lat)
        month = int(str(target_date)[5:7])

        from src.calibration.replacement_eb_bias import resolve_replacement_eb_bias_shift_c  # noqa: PLC0415
        from src.state.db import get_world_connection  # noqa: PLC0415
        import contextlib  # noqa: PLC0415

        with contextlib.closing(get_world_connection()) as world_conn:
            return resolve_replacement_eb_bias_shift_c(
                world_conn,
                city=request.city,
                season=season,
                month=month,
                metric=metric,
                live_data_version=str(bias_ldv),
                settlement_unit=settlement_unit,
                # ITEM 2 anti-lookahead self-gate: the resolver serves the row only if its
                # training_cutoff is STRICTLY BEFORE this target_date (no external gate).
                target_date=target_date,
            )
    except Exception as exc:  # fail-closed: never break shadow materialization
        try:
            import logging  # noqa: PLC0415
            logging.getLogger("zeus.replacement_eb_bias").warning(
                "replacement_0_1 EB bias wiring skipped (fail-closed): %s", exc
            )
        except Exception:
            pass
        return None


def _replacement_member_vote_smoothing_alpha() -> float | None:
    """Flag-gated additive (Laplace/Dirichlet) smoothing alpha for the AIFS member-vote prior.

    THE_PATH member-vote smoothing. Returns the configured alpha (degC-free Dirichlet
    pseudo-count) ONLY when ``replacement_0_1_member_vote_smoothing_enabled`` is true, else
    None. None makes build_openmeteo_ifs9_aifs_soft_anchor_result reproduce the raw count/total
    member prior BYTE-IDENTICALLY (default-OFF). FAIL-CLOSED: any config error / missing key /
    non-positive or non-finite alpha -> None (no smoothing, construction proceeds with raw
    inputs). Never raises. This is the ONE place the flag is read; the smoothing itself reuses
    the existing soft-anchor fusion (no parallel posterior path).
    """
    try:
        from src.config import settings  # noqa: PLC0415
        from src.strategy.ecmwf_aifs_sampled_2t_probabilities import (  # noqa: PLC0415
            MEMBER_VOTE_SMOOTHING_ALPHA,
        )

        edli_cfg = settings["edli_v1"]
        if not bool(edli_cfg.get("replacement_0_1_member_vote_smoothing_enabled", False)):
            return None
        raw_alpha = edli_cfg.get("replacement_0_1_member_vote_smoothing_alpha", MEMBER_VOTE_SMOOTHING_ALPHA)
        alpha = float(raw_alpha)
        if not math.isfinite(alpha) or alpha <= 0.0:
            return None
        return alpha
    except Exception as exc:  # fail-closed: never break shadow materialization
        try:
            import logging  # noqa: PLC0415
            logging.getLogger("zeus.replacement_member_vote_smoothing").warning(
                "replacement_0_1 member-vote smoothing wiring skipped (fail-closed): %s", exc
            )
        except Exception:
            pass
        return None


@dataclass(frozen=True)
class _U0RFusionOverride:
    """The U0R fused center/spread that replace the single-anchor in the soft-anchor build,
    plus the F6 EMOS identity components (model_set_hash, resolution_mix_hash, lead_bucket)
    and provenance for the fused product."""

    anchor_value_c: float
    anchor_sigma_c: float
    method: str
    used_models: tuple[str, ...]
    model_set_hash: str
    resolution_mix_hash: str
    lead_bucket: str
    dropped_models: tuple[str, ...]
    excluded_regionals: tuple[str, ...]
    dropped_aliases: tuple[str, ...]


def _u0r_lead_bucket(lead_days: int) -> str:
    """F6 lead_bucket for the fused EMOS cell. Regional expert is lead<=1; group leads."""
    if lead_days <= 1:
        return "L1"
    if lead_days <= 3:
        return "L2_3"
    return "L4P"


def _replacement_u0r_fusion_override(
    request: "ReplacementForecastMaterializeRequest",
    *,
    metric: str,
    anchor_value_corrected_c: float,
    conn: "sqlite3.Connection | None" = None,
) -> _U0RFusionOverride | None:
    """Flag-gated U0R-Bayes multi-model fusion override (the_path replacement_0_1_u0r_fusion).

    Returns the fused (anchor_value_c, anchor_sigma_c) that REPLACE the single OM9 9km anchor
    center/spread in the soft-anchor construction, ONLY when ``replacement_0_1_u0r_fusion_enabled``
    is true AND at least one decorrelated extra survives the fail-soft capture. Returns None when
    the flag is OFF (default) OR all extras are absent -> the existing single-anchor path runs
    BYTE-IDENTICALLY. This is the ONE place the flag is read; the fusion itself is the ported
    proof C1 (src/forecast/u0r_bayes.py — no parallel fusion).

    LAYERING (U0R_BAYES_SPEC.md §6 integration): the override is computed from the ALREADY
    EB-bias-corrected anchor center (so it composes AFTER the EB bias layer); it replaces only
    the anchor center/spread; the AIFS member-vote prior + member-vote smoothing + the downstream
    q_lcb settlement floor + EMOS + bin integration are all UNCHANGED. FAIL-SOFT / FAIL-CLOSED:
    any error, missing config, or zero surviving extras -> None (never raises, never blocks).
    """
    try:
        from src.config import runtime_cities_by_name, settings  # noqa: PLC0415

        edli_cfg = settings["edli_v1"]
        if not bool(edli_cfg.get("replacement_0_1_u0r_fusion_enabled", False)):
            return None

        city_obj = runtime_cities_by_name().get(request.city)
        if city_obj is None:
            return None
        lat = float(getattr(city_obj, "lat"))
        lon = float(getattr(city_obj, "lon"))
        tz_name = str(getattr(city_obj, "timezone", request.city_timezone))

        target_date = _date_text(request.target_date)
        target_local_date = date.fromisoformat(target_date)
        computed_at = _to_utc(request.computed_at, field_name="computed_at")
        lead_days = max(0, (target_local_date - computed_at.date()).days)

        from src.data.u0r_multimodel_capture import capture_u0r_instruments  # noqa: PLC0415
        from src.forecast.u0r_bayes import fuse_u0r_posterior  # noqa: PLC0415

        # Optional injected seams (live wiring / tests). An explicitly-assigned
        # _history_provider attribute wins (tests inject a fixture). When none is assigned AND
        # the materialization connection is available, the LIVE default is the real walk-forward
        # history provider reading the PERSISTED previous-runs raw_model_forecasts JOINed to
        # VERIFIED settlement on the SAME zeus-forecasts.db connection (intra-DB, INV-37; no-leak
        # target_date<decision, IRON RULE #3). This assignment is THE switch that lets
        # fuse_u0r_posterior reach T2_BAYES once n_train>=MIN_TRAIN (else EQUAL_WEIGHT). Fail-soft:
        # the provider NEVER raises (returns {} on any error) -> anchor fallback / equal-weight.
        history_provider = getattr(_replacement_u0r_fusion_override, "_history_provider", None)
        if history_provider is None and conn is not None:
            from src.data.u0r_history_provider import U0RHistoryProvider  # noqa: PLC0415

            history_provider = U0RHistoryProvider(conn)
        live_fetch = getattr(_replacement_u0r_fusion_override, "_live_fetch", None)

        capture = capture_u0r_instruments(
            city=request.city, metric=metric, latitude=lat, longitude=lon,
            timezone_name=tz_name,
            run=_to_utc(request.source_cycle_time, field_name="source_cycle_time"),
            target_local_date=target_local_date, lead_days=lead_days,
            anchor_z_corrected=float(anchor_value_corrected_c),
            history_provider=history_provider, live_fetch=live_fetch,
        )
        if not capture.has_extras:
            # All extras absent -> keep the existing single-anchor posterior (byte-identical).
            return None

        fused = fuse_u0r_posterior(
            anchor_z=capture.anchor_z, anchor_tau0=capture.anchor_tau0,
            likelihood=capture.likelihood, disagree_var=capture.disagree_var,
            use_covariance=True,
        )

        used_models = tuple(fused.used_models)
        model_set_hash = _json_hash(sorted(used_models))
        # resolution_mix_hash captures which native grid resolutions entered the fused product
        # (anchor 0.1, globals ~0.25/seamless, regional 2km). Keyed by the deduped model set.
        resolution_mix_hash = _json_hash(
            {"models": sorted(used_models), "regional": sorted(fused.regional_models)}
        )
        return _U0RFusionOverride(
            anchor_value_c=float(fused.mu),
            anchor_sigma_c=float(fused.sd),
            method=fused.method,
            used_models=used_models,
            model_set_hash=model_set_hash,
            resolution_mix_hash=resolution_mix_hash,
            lead_bucket=_u0r_lead_bucket(lead_days),
            dropped_models=capture.dropped_models,
            excluded_regionals=capture.selection.excluded_regionals,
            dropped_aliases=capture.selection.dropped_aliases,
        )
    except Exception as exc:  # fail-soft: never break shadow materialization
        try:
            import logging  # noqa: PLC0415
            logging.getLogger("zeus.replacement_u0r_fusion").warning(
                "replacement_0_1 U0R fusion wiring skipped (fail-soft): %s", exc
            )
        except Exception:
            pass
        return None


def _insert_posterior(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    anchor_id: int,
) -> int:
    # P2_BLEND.md §3-§5: flag-gated per-city EB bias-correction of the center, applied
    # BEFORE the soft-anchor zero-prior veto (inside build_openmeteo_ifs9_aifs_soft_anchor_result).
    # None when flag OFF or no VERIFIED row -> byte-identical to today.
    bias_shift_c = _replacement_eb_bias_shift_c(request, metric=metric)
    # THE_PATH member-vote smoothing: flag-gated additive Laplace/Dirichlet alpha so the AIFS
    # member prior is strictly positive on every bin and the soft_anchor.py:197-198 zero-prior
    # -inf veto can never make a bin un-hittable. None when flag OFF -> byte-identical to today.
    member_vote_smoothing_alpha = _replacement_member_vote_smoothing_alpha()
    # U0R-Bayes fusion (flag-gated, default-OFF): replace the single OM9 9km anchor center/spread
    # with the multi-model Bayesian posterior. Computed from the EB-corrected anchor center so it
    # composes AFTER the EB bias layer; member-vote smoothing stays applied to the AIFS prior; the
    # downstream q_lcb floor + EMOS + bin integration are unchanged. None -> byte-identical path.
    raw_anchor_value_c = request.openmeteo_anchor.high_c if metric == "high" else request.openmeteo_anchor.low_c
    anchor_value_corrected_c = float(raw_anchor_value_c) - (0.0 if bias_shift_c is None else float(bias_shift_c))
    u0r_override = _replacement_u0r_fusion_override(
        request, metric=metric, anchor_value_corrected_c=anchor_value_corrected_c, conn=conn
    )
    result = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=request.aifs_extraction,
        openmeteo_anchor=request.openmeteo_anchor,
        metric=metric,
        bins=request.bins,
        config=SoftAnchorConfig(anchor_weight=request.anchor_weight, anchor_sigma_c=request.anchor_sigma_c),
        settlement_step_c=float(request.settlement_step_c),
        bias_shift_c=bias_shift_c,
        member_vote_smoothing_alpha=member_vote_smoothing_alpha,
        anchor_value_override_c=(u0r_override.anchor_value_c if u0r_override is not None else None),
        anchor_sigma_override_c=(u0r_override.anchor_sigma_c if u0r_override is not None else None),
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
    bin_topology_payload = _bin_topology_payload(request.bins, settlement_step_c=float(request.settlement_step_c))
    bin_topology_hash = _json_hash(bin_topology_payload)
    dependency_payload = {
        "baseline_b0": request.baseline_source_run_id,
        "aifs_sampled_2t": request.aifs_source_run_id,
        "openmeteo_ifs9_anchor": request.openmeteo_source_run_id,
    }
    dependency_hash = _json_hash(dependency_payload)
    posterior_config = {
        "posterior_method": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        "anchor_weight": float(request.anchor_weight),
        "anchor_sigma_c": float(request.anchor_sigma_c),
        "settlement_step_c": float(request.settlement_step_c),
    }
    if u0r_override is not None:
        # F6: the FUSED product gets its OWN EMOS cell identity (product + resolution_mix_hash +
        # model_set_hash + lead_bucket) so it never reuses the single-anchor EMOS cell. The fused
        # center/spread REPLACE the OM9 anchor, so posterior_config_hash diverges from the
        # single-anchor cell by construction.
        posterior_config.update(
            {
                "posterior_method": "the_path_u0r_fusion",
                "u0r_fusion_method": u0r_override.method,
                "u0r_product_id": "the_path_u0r_fusion_v1",
                "u0r_model_set_hash": u0r_override.model_set_hash,
                "u0r_resolution_mix_hash": u0r_override.resolution_mix_hash,
                "u0r_lead_bucket": u0r_override.lead_bucket,
                "u0r_anchor_value_c": float(u0r_override.anchor_value_c),
                "u0r_anchor_sigma_c": float(u0r_override.anchor_sigma_c),
            }
        )
    posterior_config_hash = _json_hash(posterior_config)
    family_id = f"{request.city}:{target_date}:{metric}:{bin_topology_hash}"
    provenance_payload = {
        "anchor_weight": request.anchor_weight,
        "anchor_sigma_c": request.anchor_sigma_c,
        "anchor_value_c": result.anchor_value_c,
        "aifs_artifact_id": request.aifs_artifact_id,
        "aifs_identity": {
            "identity_decision_valid": request.aifs_extraction.identity_decision_valid,
            "identity_reason_codes": list(request.aifs_extraction.identity_reason_codes),
            "identity_decision_hash": request.aifs_extraction.identity_decision_hash,
            "member_ids_hash": request.aifs_extraction.member_ids_hash,
            "step_hours_hash": request.aifs_extraction.step_hours_hash,
            "artifact_id": request.aifs_extraction.artifact_id,
            "raw_sha256": request.aifs_extraction.raw_sha256,
            "source_product_id": request.aifs_extraction.source_product_id,
        },
        "openmeteo_anchor_artifact_id": request.anchor_artifact_id,
        "openmeteo_precision_guard": _precision_guard_payload(request.openmeteo_precision_guard),
        "aifs_probabilities": dict(result.aifs_probabilities.probabilities),
        "aifs_member_count": len(result.aifs_probabilities.member_values_c),
        "q_point_json_role": "shadow_point_probability_only",
        "q_lcb_json_role": "absent_no_calibrated_lcb_available",
        "q_ucb_json_role": "absent_no_calibrated_ucb_available",
        "bin_topology": bin_topology_payload,
        "bin_topology_hash": bin_topology_hash,
        "dependency_hash": dependency_hash,
        "posterior_config_hash": posterior_config_hash,
        "family_id": family_id,
        "posterior_authority_status": "SHADOW_ONLY",
        "runtime_policy_status": "SHADOW_VETO_ONLY",
        "trade_authority_status": "SHADOW_ONLY",
        "training_allowed": False,
    }
    if u0r_override is not None:
        provenance_payload["u0r_fusion"] = {
            "method": u0r_override.method,
            "used_models": list(u0r_override.used_models),
            "model_set_hash": u0r_override.model_set_hash,
            "resolution_mix_hash": u0r_override.resolution_mix_hash,
            "lead_bucket": u0r_override.lead_bucket,
            "anchor_value_c": float(u0r_override.anchor_value_c),
            "anchor_sigma_c": float(u0r_override.anchor_sigma_c),
            "dropped_models": list(u0r_override.dropped_models),
            "excluded_regionals": list(u0r_override.excluded_regionals),
            "dropped_aliases": list(u0r_override.dropped_aliases),
            "fusion_authority": "SHADOW_ONLY",
        }
    posterior_identity_hash = _json_hash(
        {
            "source_id": SOURCE_ID,
            "product_id": PRODUCT_ID,
            "data_version": data_version,
            "city": request.city,
            "target_date": target_date,
            "temperature_metric": metric,
            "source_cycle_time": source_cycle_time,
            "source_available_at": available_at,
            "computed_at": computed_at,
            "q": q,
            "q_lcb": None,
            "q_ucb": None,
            "dependency_hash": dependency_hash,
            "bin_topology_hash": bin_topology_hash,
            "posterior_config_hash": posterior_config_hash,
            "anchor_id": anchor_id,
            "aifs_artifact_id": request.aifs_artifact_id,
            "anchor_artifact_id": request.anchor_artifact_id,
        }
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, q_ucb_json, posterior_method,
            aifs_source_run_id, openmeteo_anchor_id,
            dependency_source_run_ids_json, family_id, bin_topology_hash,
            dependency_hash, posterior_config_hash, posterior_identity_hash,
            provenance_json,
            trade_authority_status, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            None,
            None,
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            request.aifs_source_run_id,
            anchor_id,
            _json(dependency_payload),
            family_id,
            bin_topology_hash,
            dependency_hash,
            posterior_config_hash,
            posterior_identity_hash,
            _json(provenance_payload),
            "SHADOW_ONLY",
            0,
        ),
    )
    row = conn.execute(
        """
        SELECT posterior_id FROM forecast_posteriors
        WHERE posterior_identity_hash = ?
        """,
        (posterior_identity_hash,),
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
    _ensure_replacement_identity_columns(conn)
    prewrite_reasons = _prewrite_block_reasons(request)
    if prewrite_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=prewrite_reasons,
            posterior_id=None,
            anchor_id=None,
            readiness_id=None,
        )
    artifact_reasons = _artifact_identity_block_reasons(conn, request)
    if artifact_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=artifact_reasons,
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
