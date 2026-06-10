"""Materialize replacement forecast shadow posterior rows into forecast DB.

# Created: 2026-06-08
# Last reused or audited: 2026-06-09
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md (the probability chain
#   §1d-§1e fused-N-direct + settlement sigma floor); FIX 1/FIX 2/FIX 5 (operator-reviewed
#   2026-06-09): explicit replacement_q_mode authority, settlement-sigma-floor coherence in the
#   fused-q path, and capture-status provenance. 2026-06-09 (q_lcb materialization): real per-bin
#   q_lcb_json/q_ucb_json on the fused path via fused-center parameter-uncertainty bootstrap
#   (root-cause /tmp/candidate_missing_rootcause.md — NULL bounds force the Wilson-over-AIFS-votes
#   fallback that under-certifies below ask and discards every candidate).
"""

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
from src.data.replacement_forecast_cycle_policy import (
    classify_cycle_phase,
    cycle_age_exceeds_bound,
    replacement_source_cycle_max_age_hours,
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


# ---------------------------------------------------------------------------
# FIX 1 (2026-06-09) — explicit replacement q-mode authority.
#
# A posterior row's `replacement_q_mode` is DERIVED at materialization time (never guessed)
# and recorded in provenance_json. The live gate (event_reactor_adapter) admits ONLY the two
# fused-Normal modes; every other mode is a deterministic no-submit. This kills the silent
# degradation category: with all flags on, a row that fell back to the legacy member-vote
# soft-anchor q (fusion None / fused-q build failed / flag off) used to differ ONLY by a
# WARNING log + a q_shape string — live EDLI could size Kelly under a different probability
# regime than the release evidence assumes. The mode is a fail-closed data-class label.
# ---------------------------------------------------------------------------
REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL = "FUSED_NORMAL_FULL"
REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL = "FUSED_NORMAL_PARTIAL"
REPLACEMENT_Q_MODE_SOFT_ANCHOR_FALLBACK = "SOFT_ANCHOR_FALLBACK"
REPLACEMENT_Q_MODE_U0R_CAPTURE_MISSING = "U0R_CAPTURE_MISSING"
REPLACEMENT_Q_MODE_FUSED_Q_BUILD_FAILED = "FUSED_Q_BUILD_FAILED"
# PR#403 FIX (2026-06-09) — fused-q succeeded but the bounds failed. DISTINCT from
# FUSED_Q_BUILD_FAILED (the point q is fine; only the bounds are absent). The fused-Normal
# q point is STILL written to the DB (shadow materialization completes for accrual), but
# live eligibility is killed. Without this a FULL/PARTIAL row with NULL q_lcb_json would be
# live-eligible, letting buy_yes fall back to Wilson-over-AIFS-votes — exactly the two-measures
# disease (fused-Normal q point + legacy LCB authority) that the Milan incident root-caused.
REPLACEMENT_Q_MODE_FUSED_NORMAL_BOUNDS_MISSING = "FUSED_NORMAL_BOUNDS_MISSING"

# FIX 5 — capture-status provenance (recording only; the live gate enforces via q_mode).
REPLACEMENT_CAPTURE_STATUS_FULL_CURRENT = "FULL_CURRENT"
REPLACEMENT_CAPTURE_STATUS_PARTIAL_CURRENT = "PARTIAL_CURRENT"
REPLACEMENT_CAPTURE_STATUS_STALE_HISTORY_ONLY = "STALE_HISTORY_ONLY"
REPLACEMENT_CAPTURE_STATUS_DB_READ_ERROR = "DB_READ_ERROR"


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
    # BOUNDED STALENESS (operator directive 2026-06-10) — fail-closed at materialization.
    # Re-materializing the SAME persisted source cycle re-stamps computed_at and grants a
    # fresh 3h readiness TTL. Unbounded, that launders an arbitrarily-old cycle into "current"
    # trading inputs forever (exactly what the manual 12Z recovery does ONCE — it must not be
    # repeatable indefinitely). Cap (computed_at - source_cycle_time) at the SAME horizon the
    # live-admission belt-and-suspenders gate uses (replacement_forecast_cycle_policy: 30h,
    # within the empirical max healthy cycle age of 28.8h). Expired-but-rematerializable: the
    # SAME cycle is allowed only WHILE within this bound. Refusing here means a too-stale cycle
    # never even gets re-stamped, so the live gate is never the sole line of defence.
    if cycle_age_exceeds_bound(computed_at, request_source_cycle_time):
        reasons.append("REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_TOO_STALE")
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
        # The INSERT OR IGNORE above can be a no-op when an anchor for the same
        # UNIQUE natural key (source_id, product_id, data_version, city,
        # target_date, temperature_metric, source_cycle_time) already exists from
        # a prior run. The identity hash, however, folds in the per-run captured_at
        # (computed_at), so a re-run produces a different hash and the hash lookup
        # above misses. Fall back to the natural key to return the existing anchor
        # idempotently instead of crashing the whole materialization.
        row = conn.execute(
            """
            SELECT anchor_id FROM deterministic_forecast_anchors
            WHERE source_id = ?
              AND product_id = ?
              AND data_version = ?
              AND city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND source_cycle_time = ?
            """,
            (
                ANCHOR_SOURCE_ID,
                ANCHOR_PRODUCT_ID,
                _anchor_data_version(metric),
                request.city,
                target_date,
                metric,
                source_cycle_time,
            ),
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


def _replacement_fused_q_shape_enabled() -> bool:
    """Flag gate for the FUSED-Q SHAPE replacement (2026-06-09 AIFS-replacement experiment).

    When ``replacement_0_1_fused_q_shape_enabled`` is true AND the U0R fusion produced an
    override with a predictive sigma, the posterior q is built DIRECTLY from
    N(mu*, sigma_pred) via the ONE settlement bin integrator (bin_probability_settlement) —
    fully replacing the AIFS member-vote shape. Experiment verdict (n=39 settled cells,
    /tmp/aifs_replacement_experiment.md): the AIFS shape put EXACTLY ZERO probability on the
    winning bin in 11/39 cells (member votes truncate support; the soft-anchor can only shift
    that mass, never create coverage) — LogLoss 11.07 vs fused-N 1.51, hit 25.6% vs 46.2%.
    A Normal has full support: the zero-coverage CATEGORY is unconstructable under this shape.
    FAIL-CLOSED: any config error -> False (AIFS-shape q, today's behavior).
    """
    try:
        from src.config import settings  # noqa: PLC0415

        return bool(settings["edli_v1"].get("replacement_0_1_fused_q_shape_enabled", False))
    except Exception:
        return False


def _edli_settlement_sigma_floor_enabled() -> bool:
    """FIX 2 (2026-06-09) — flag gate for the settlement sigma floor in the FUSED-Q path.

    Mirrors the EMOS path's `edli_settlement_sigma_floor_enabled`. When true the fused-q sigma is
    floored at the SAME empirical settlement dispersion floor the EMOS path uses
    (src/calibration/emos.settlement_sigma_floor) so the operator-facing "floor enabled" claim
    actually protects the strategy of record. FAIL-CLOSED: any config error -> False (no floor).
    """
    try:
        from src.config import settings  # noqa: PLC0415

        return bool(settings["edli_v1"].get("edli_settlement_sigma_floor_enabled", False))
    except Exception:
        return False


def _edli_settlement_sigma_floor_required() -> bool:
    """FIX 2 (2026-06-09) — whether an AVAILABLE settlement floor is REQUIRED to keep FULL mode.

    Mirrors `edli_settlement_sigma_floor_required` (current config: false). When false a fused-N
    q built WITHOUT an available floor stays FUSED_NORMAL_FULL (the floor only widens when present);
    when true a missing floor degrades the q-mode to FUSED_NORMAL_PARTIAL (live gate still admits,
    but the receipt shows the degraded mode). This NEVER invents a new blocking lane beyond the
    flag semantics. FAIL-CLOSED: any config error -> False (do not require a floor).
    """
    try:
        from src.config import settings  # noqa: PLC0415

        return bool(settings["edli_v1"].get("edli_settlement_sigma_floor_required", False))
    except Exception:
        return False


def _replacement_settlement_sigma_floor_lookup(
    request: "ReplacementForecastMaterializeRequest",
    *,
    metric: str,
) -> tuple[float | None, str | None]:
    """FIX 2 (2026-06-09) — resolve the SAME settlement sigma floor the EMOS path uses for this cell.

    Returns ``(floor_c, unavailable_reason)``:
      - ``(value, None)`` when a positive floor exists for the (city, season, metric) cell.
      - ``(None, reason)`` when the floor lookup is missing/malformed for the cell — recording-only,
        NEVER blocks shadow materialization. The reason is folded into provenance.

    Single-builder: this calls src.calibration.emos.settlement_sigma_floor (the SAME lookup the
    EMOS q-builder uses, keyed city|season|metric via emos_cell_key), with required=False so a
    missing cell returns None rather than raising. Season is derived from target_date + the city's
    config latitude (the same derivation _replacement_eb_bias_shift_c uses). FAIL-SOFT throughout.
    """
    try:
        from src.config import runtime_cities_by_name  # noqa: PLC0415
        from src.contracts.season import season_from_date  # noqa: PLC0415
        from src.calibration.emos import settlement_sigma_floor  # noqa: PLC0415

        city_obj = runtime_cities_by_name().get(request.city)
        lat = float(getattr(city_obj, "lat", 90.0)) if city_obj is not None else 90.0
        target_date = _date_text(request.target_date)
        season = season_from_date(target_date, lat=lat)
        floor_c = settlement_sigma_floor(request.city, season, str(metric).lower(), required=False)
        if floor_c is None:
            return None, f"SETTLEMENT_SIGMA_FLOOR_ABSENT:{request.city}|{season}|{str(metric).lower()}"
        floor_value = float(floor_c)
        if not (math.isfinite(floor_value) and floor_value > 0.0):
            return None, f"SETTLEMENT_SIGMA_FLOOR_NON_POSITIVE:{floor_value}"
        return floor_value, None
    except Exception as exc:  # fail-soft: never block shadow materialization
        return None, f"SETTLEMENT_SIGMA_FLOOR_LOOKUP_ERROR:{type(exc).__name__}"


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
    # BLOCKER 5: the persisted current single_runs rows this q was fused from (reconstructable).
    raw_model_forecast_ids: tuple[int, ...] = ()
    # BLOCKER 3: the ifs025->ifs9 anchor bridge provenance applied to the anchor prior.
    anchor_bridge: Mapping[str, object] | None = None
    # FUSED-Q SHAPE (2026-06-09 AIFS-replacement experiment): the PREDICTIVE sigma for building
    # q directly from N(mu*, sigma_pred) — sigma_pred^2 = fused.sd^2 + sigma_resid^2, where
    # sigma_resid is the walk-forward std of the fused-center residual series (common-date mean
    # of the instruments' de-biased residuals), conservatively floored. None when the residual
    # substrate is too thin AND no conservative default applies (caller falls back to the
    # AIFS-shape soft-anchor q).
    predictive_sigma_c: float | None = None
    # FIX 1 (2026-06-09): the K3 decorrelated-provider completeness verdict computed INSIDE the
    # fusion (the same "served %d/5" determination the materializer already logs). True =
    # all 5 declared decorrelated providers served (-> FUSED_NORMAL_FULL); False = INCOMPLETE
    # (-> FUSED_NORMAL_PARTIAL). The materializer REUSES this; it never re-derives a parallel
    # provider check (single-builder).
    decorrelated_providers_complete: bool = False
    # FIX 5 (2026-06-09): capture-status provenance. count of the 5 decorrelated providers whose
    # CURRENT value entered the fused set for this cell, and the count expected (5). Recording only.
    decorrelated_providers_served: int = 0
    decorrelated_providers_expected: int = 5


def _read_persisted_current_capture(
    conn: "sqlite3.Connection",
    *,
    city: str,
    metric: str,
    target_date: str,
    lead_days: int,
    source_cycle_time_iso: str,
) -> dict[str, tuple[float, int]]:
    """BLOCKER 5 — read the PERSISTED current single_runs rows for this cycle.

    Returns {model: (forecast_value_c, raw_model_forecast_id)} for the single_runs rows the
    download job persisted for THIS (city, metric, target_date, source_cycle_time). The q path
    consumes THESE rows (never a network fetch), so the traded q is reconstructable to the exact
    persisted inputs (model, params, url hash, source_available_at). Empty dict -> the current
    capture is missing for this cycle (the caller blocks / falls back with a reason).
    Fail-soft: any DB error -> empty dict (treated as missing capture, never raises).

    LEAD_DAYS IS NOT A FILTER (2026-06-09 fix): (city, metric, target_date, source_cycle_time)
    already uniquely identifies the forecast, and lead_days is a DERIVED field = target - cycle.
    The download persists lead_days on the cycle/UTC calendar; this reader previously re-derived
    it from ``computed_at`` on the CITY-LOCAL calendar (BLOCKER 6) — a different reference time
    AND a different calendar — so the leads disagreed (e.g. Wuhan: download lead=2 from cycle
    06-08, reader lead=1 from computed_at 06-09 local) and this read returned EMPTY for ~all live
    cells, silently disabling the ENTIRE multi-model fusion (0/598 posteriors fused -> cold
    soft-anchor fallback). Matching on the natural key (no lead filter) makes that
    download/materialize lead-calendar mismatch unconstructable. ``lead_days`` is retained as a
    parameter for call-site compatibility but is no longer used to filter.

    K2 gem_global DECLARED EXCEPTION (2026-06-09, curl-verified): the open-meteo single-runs API
    does not serve cmc_gem_gdps_15km AT ALL (even cadence-valid 00z runs return
    modelRunUnavailable), so gem_global can never have a single_runs row. Its current value is
    served from its previous_runs row at the SAME natural key — the SAME GDPS product its
    walk-forward de-bias history is fit on (source-identical; the ECMWF anchor needs an
    ifs025->ifs9 bridge precisely because its history product != live product — gem has no such
    mismatch). The exception is scoped to gem_global ONLY: any other model missing its
    single_runs row stays missing/LOUD (no silent endpoint masking of a broken capture).
    """
    try:
        rows = conn.execute(
            """
            SELECT raw_model_forecast_id, model, forecast_value_c
            FROM raw_model_forecasts
            WHERE city = ? AND metric = ? AND target_date = ?
              AND source_cycle_time = ? AND endpoint = 'single_runs'
            ORDER BY model, lead_days, raw_model_forecast_id
            """,
            (city, metric, target_date, source_cycle_time_iso),
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, tuple[float, int]] = {}
    for row in rows:
        try:
            rid = int(row[0] if not isinstance(row, sqlite3.Row) else row["raw_model_forecast_id"])
            model = row[1] if not isinstance(row, sqlite3.Row) else row["model"]
            value = float(row[2] if not isinstance(row, sqlite3.Row) else row["forecast_value_c"])
        except Exception:
            continue
        # First row per model wins (deterministic ORDER BY); a model is captured once per cycle.
        out.setdefault(model, (value, rid))
    if "gem_global" not in out:
        try:
            gem_rows = conn.execute(
                """
                SELECT raw_model_forecast_id, forecast_value_c
                FROM raw_model_forecasts
                WHERE city = ? AND metric = ? AND target_date = ?
                  AND source_cycle_time = ? AND endpoint = 'previous_runs'
                  AND model = 'gem_global'
                ORDER BY lead_days, raw_model_forecast_id
                """,
                (city, metric, target_date, source_cycle_time_iso),
            ).fetchall()
        except Exception:
            gem_rows = []
        for row in gem_rows:
            try:
                rid = int(row[0] if not isinstance(row, sqlite3.Row) else row["raw_model_forecast_id"])
                value = float(row[1] if not isinstance(row, sqlite3.Row) else row["forecast_value_c"])
            except Exception:
                continue
            out["gem_global"] = (value, rid)
            break
    return out


def _u0r_city_local_lead_days(
    *, computed_at: datetime, target_local_date: date, tz_name: str
) -> int:
    """BLOCKER 6 — lead in the CITY-LOCAL calendar, never the UTC calendar.

    computed_at is UTC; the decision date for the lead bucket / regional eligibility / sigma is
    the city-local date of that instant. Using computed_at.date() (UTC) is off-by-one across
    timezones (Tokyo: 2026-06-03T16:30Z is local 06-04 -> a 06-04 target is lead 0, not 1).
    Floors at 0 (a target before the local decision date is lead 0). Falls back to the UTC date
    only if tz_name is unresolvable (defensive; the caller always passes the city timezone).
    """
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        computed_local_date = computed_at.astimezone(ZoneInfo(tz_name)).date()
    except Exception:
        computed_local_date = computed_at.date()
    return max(0, (target_local_date - computed_local_date).days)


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
        # BLOCKER 6: lead in the CITY-LOCAL date (tz_name), NOT the UTC date. Cross-timezone the
        # UTC date is off-by-one -> wrong lead bucket / regional eligibility / sigma.
        lead_days = _u0r_city_local_lead_days(
            computed_at=computed_at, target_local_date=target_local_date, tz_name=tz_name
        )

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

        # BLOCKER 5: the CURRENT values feeding the traded q come from the PERSISTED single_runs
        # rows the download job wrote — NEVER a network fetch inside the q path. Read them by
        # (city, metric, target_date, lead, source_cycle_time) on the SAME connection so the q is
        # reconstructable to the exact persisted inputs. If the current capture is MISSING (the
        # download did not run / failed), fall back to the single-anchor posterior (return None)
        # WITH a logged reason — never silently network-fetch.
        source_cycle_iso = _to_utc(
            request.source_cycle_time, field_name="source_cycle_time"
        ).isoformat()
        persisted_current: dict[str, tuple[float, int]] = {}
        if conn is not None:
            persisted_current = _read_persisted_current_capture(
                conn, city=request.city, metric=metric, target_date=target_date,
                lead_days=lead_days, source_cycle_time_iso=source_cycle_iso,
            )

        # An explicitly-assigned _live_fetch is honored ONLY as a per-model override seam for
        # models WITHOUT a persisted current row (legacy/test injection). It is never consulted
        # when the persisted row exists. It does NOT defeat the missing-capture gate: when the
        # persisted capture is entirely absent the q path falls back to single-anchor regardless,
        # because B5 forbids building the traded q from any non-persisted current value.
        injected_live_fetch = getattr(_replacement_u0r_fusion_override, "_live_fetch", None)

        if conn is not None and not persisted_current:
            # Missing current capture on the live path -> single-anchor fallback + logged reason.
            # NEVER a network fetch in the q path (the persisted download is the sole q source).
            import logging  # noqa: PLC0415
            logging.getLogger("zeus.replacement_u0r_fusion").warning(
                "replacement_0_1 U0R fusion: persisted current single_runs capture MISSING for "
                "%s %s %s lead=%s cycle=%s -> single-anchor fallback (no network fetch in q path)",
                request.city, metric, target_date, lead_days, source_cycle_iso,
            )
            return None

        consumed_ids: list[int] = []

        def _persisted_then_injected_fetch(*, model, **_kwargs):
            hit = persisted_current.get(model)
            if hit is not None:
                value, rid = hit
                consumed_ids.append(int(rid))
                return float(value)
            # No persisted current row for this model: consult the injected seam if present
            # (legacy/test seam, e.g. conn-less unit tests of the capture), else the model is
            # simply absent (fail-soft drop).
            if injected_live_fetch is not None:
                return injected_live_fetch(model=model, **_kwargs)
            return None

        capture = capture_u0r_instruments(
            city=request.city, metric=metric, latitude=lat, longitude=lon,
            timezone_name=tz_name,
            run=_to_utc(request.source_cycle_time, field_name="source_cycle_time"),
            target_local_date=target_local_date, lead_days=lead_days,
            anchor_z_corrected=float(anchor_value_corrected_c),
            history_provider=history_provider, live_fetch=_persisted_then_injected_fetch,
        )
        if not capture.has_extras:
            # K3 ANTIBODY (2026-06-09): all multi-model extras absent. We only reach here when
            # replacement_0_1_u0r_fusion_enabled is True, so ZERO extras is a WIRING failure (e.g.
            # the lead-calendar mismatch that silently reverted ALL fusion to cold soft-anchor for
            # ~30h) — NOT a benign inert path. Make it LOUD so a repeat can never hide as a
            # transient drop. (Behaviour unchanged: still single-anchor fallback.)
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_u0r_fusion").warning(
                    "replacement_0_1 U0R fusion fired with ZERO multi-model extras (flag ON) -> "
                    "single-anchor fallback for %s %s %s cycle=%s. Check the single_runs capture "
                    "+ natural-key match.", request.city, metric, target_date,
                    _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat(),
                )
            except Exception:
                pass
            return None

        fused = fuse_u0r_posterior(
            anchor_z=capture.anchor_z, anchor_tau0=capture.anchor_tau0,
            likelihood=capture.likelihood, disagree_var=capture.disagree_var,
            use_covariance=True,
        )

        used_models = tuple(fused.used_models)
        # K3 ANTIBODY (2026-06-09): surface a STRUCTURALLY-incomplete decorrelated set LOUDLY. The
        # 4 declared decorrelated PROVIDERS are NOAA(gfs) / DWD-ICON(one of icon_d2|icon_eu|
        # icon_global) / CMC(gem) / JMA(jma). gem_global's single_runs is unavailable at 06z/18z
        # cycles (12h cadence) so the ensemble silently ran as 3 -> a permanently-unservable model
        # must never masquerade as a transient drop. Log expected-vs-served providers per cell.
        _missing_providers = []
        # 2026-06-09 promotion: provider families are rep-based — NBM is the NCEP rep in-CONUS
        # (replacing gfs_global) and the UKV 2km nest is the UKMO rep in the UK, so each family
        # check accepts ANY of its members. 5 declared decorrelated providers since the
        # ukmo_global promotion.
        if not any(m in used_models for m in ("gfs_global", "ncep_nbm_conus")):
            _missing_providers.append("NCEP/gfs_global|nbm")
        if not any(m in used_models for m in ("icon_d2", "icon_eu", "icon_global")):
            _missing_providers.append("DWD/icon")
        if "gem_global" not in used_models:
            _missing_providers.append("CMC/gem_global")
        if "jma_seamless" not in used_models:
            _missing_providers.append("JMA/jma_seamless")
        if not any(
            m in used_models
            for m in ("ukmo_global_deterministic_10km", "ukmo_uk_deterministic_2km")
        ):
            _missing_providers.append("UKMO/global|uk2km")
        # FIX 1/FIX 5 (2026-06-09): the SINGLE K3 completeness verdict reused by the q-mode +
        # capture-status provenance. 5 declared decorrelated providers; served = 5 - missing.
        # This is the ONLY provider-count determination — the q-mode FULL/PARTIAL split and the
        # FIX-5 capture_status both read it (no parallel re-derivation).
        _decorrelated_expected = 5
        _decorrelated_served = _decorrelated_expected - len(_missing_providers)
        _decorrelated_complete = not _missing_providers
        if _missing_providers:
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_u0r_fusion").warning(
                    "replacement_0_1 U0R fusion decorrelated-provider INCOMPLETE for %s %s: served "
                    "%d/5, missing %s (used=%s). A structurally-unservable provider (e.g. gem 12h-"
                    "cadence single_runs) must be resolved explicitly, not silently dropped.",
                    request.city, metric, _decorrelated_served, _missing_providers,
                    list(used_models),
                )
            except Exception:
                pass
        model_set_hash = _json_hash(sorted(used_models))
        # resolution_mix_hash captures which native grid resolutions entered the fused product
        # (anchor 0.1, globals ~0.25/seamless, regional 2km). Keyed by the deduped model set.
        resolution_mix_hash = _json_hash(
            {"models": sorted(used_models), "regional": sorted(fused.regional_models)}
        )

        # BLOCKER 5: the raw_model_forecast_ids this q was fused from = the persisted current
        # single_runs rows consumed for the extras PLUS the persisted anchor current row (the
        # anchor center, though passed as anchor_z_corrected, is the persisted anchor product).
        # Sorted + de-duped for a deterministic provenance list.
        dep_ids = set(consumed_ids)
        from src.forecast.model_selection import ANCHOR_MODEL as _ANCHOR  # noqa: PLC0415
        anchor_row = persisted_current.get(_ANCHOR)
        if anchor_row is not None:
            dep_ids.add(int(anchor_row[1]))
        raw_model_forecast_ids = tuple(sorted(dep_ids))

        # BLOCKER 3: declare the ifs025->ifs9 anchor bridge provenance (applied when the anchor
        # history product is the 0.25 feed, which is the only ECMWF previous-runs OM serves).
        from src.data.u0r_multimodel_capture import (  # noqa: PLC0415
            OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME,
        )
        from src.forecast.u0r_anchor_bridge import bridge_metadata  # noqa: PLC0415
        anchor_bridge = bridge_metadata(
            stored_model_name=OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME
        )

        # FUSED-Q PREDICTIVE SIGMA (2026-06-09): sigma for the settlement VALUE, not the mean.
        # fused.sd is the posterior sd of mu* (V* + widenings) — far too tight as a predictive
        # spread (the AIFS-replacement experiment's tight-sigma caveat). The irreducible part is
        # measured from the walk-forward FUSED-CENTER residual series: per common target_date,
        # the mean of the instruments' de-biased residuals; its std IS the historical error of
        # an equal-weight fused center at this cell. sigma_pred = sqrt(fused.sd^2 + sigma_resid^2),
        # floored at 1.0C (conservative: settlement-graded fused-center MAE ran 0.85-1.31C at
        # real leads; never narrower than the evidence). Thin substrate (<5 common dates) ->
        # conservative default sigma_resid = 1.5C.
        _date_sets = [set(ins.residuals_by_date) for ins in capture.likelihood if ins.residuals_by_date]
        _sigma_resid = 1.5
        if _date_sets:
            _common = sorted(set.intersection(*_date_sets)) if len(_date_sets) > 1 else sorted(_date_sets[0])
            if len(_common) >= 5:
                _series = [
                    sum(ins.residuals_by_date[d] for ins in capture.likelihood if ins.residuals_by_date) /
                    max(1, sum(1 for ins in capture.likelihood if ins.residuals_by_date))
                    for d in _common
                ]
                import statistics  # noqa: PLC0415
                try:
                    _sigma_resid = float(statistics.stdev(_series))
                except statistics.StatisticsError:
                    _sigma_resid = 1.5
        predictive_sigma_c = max(1.0, (float(fused.sd) ** 2 + _sigma_resid ** 2) ** 0.5)

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
            raw_model_forecast_ids=raw_model_forecast_ids,
            anchor_bridge=anchor_bridge,
            predictive_sigma_c=predictive_sigma_c,
            decorrelated_providers_complete=_decorrelated_complete,
            decorrelated_providers_served=_decorrelated_served,
            decorrelated_providers_expected=_decorrelated_expected,
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


# ---------------------------------------------------------------------------
# Q_LCB / Q_UCB MATERIALIZATION (2026-06-09) — fused-center parameter-uncertainty bootstrap.
#
# Created: 2026-06-09
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §1d-§1e (fused-N-direct q,
#   σ_pred = sqrt(fused.sd² + σ_resid²)); root-cause /tmp/candidate_missing_rootcause.md (the
#   live LCB authority falls back to Wilson-over-AIFS-votes when q_lcb_json is NULL → under-certifies
#   below ask → every proof killed). This builds a REAL per-bin q_lcb/q_ucb consistent with the fused
#   posterior so the bundle q_lcb takes priority over the Wilson fallback (no downstream change).
#
# DESIGN (principled, not a fudge):
#   The fused posterior gives center μ* with posterior sd = fused.sd (anchor_sigma_c — the CENTER
#   uncertainty) and predictive spread σ_pred (predictive_sigma_c = sqrt(fused.sd² + σ_resid²)). The
#   q POINT vector integrates N(μ*, σ_pred). The q_lcb bound is a PARAMETER-uncertainty bootstrap:
#   draw μ_i ~ N(μ*, fused.sd) — the center uncertainty ONLY (we do NOT re-add σ_resid here; that
#   would double-count the residual spread already inside σ_pred). For each draw, integrate the SAME
#   settlement bins via the ONE integrator (bin_probability_settlement, same half_step / Celsius
#   bounds as the q build). Per-bin 5th percentile across draws = q_lcb, 95th = q_ucb.
#
#   CENTER-ONLY justification: σ is a single fused predictive spread with no principled per-cell
#   spread-uncertainty estimate available at this seam. Jittering σ would require an arbitrary
#   variance-of-variance; center-only is conservative (it exposes the tail fragility that matters —
#   a far-tail bin's probability collapses fast as μ wanders) and honest. Basis recorded as
#   "fused_center_bootstrap_p05".
#
#   Percentile vectors do NOT sum to 1 — that is EXPECTED and correct for bounds (the bundle reader
#   reads them with require_sum=False). Defensive clips: q_lcb ≥ 0 (trivial) and q_lcb ≤ q_point per
#   bin (a per-bin 5th percentile can never legitimately exceed the point mass; clip if rng noise
#   nudges it). q_ucb ≥ q_point per bin symmetrically.
# ---------------------------------------------------------------------------

_QLCB_BOOTSTRAP_DRAWS = 200
_QLCB_BASIS = "fused_center_bootstrap_p05"
_QLCB_SEED = 0x5EED_F09  # deterministic per-posterior rng (provenance-stable bounds)


def _build_fused_q_bounds(
    *,
    mu_star: float,
    center_sigma_c: float,
    predictive_sigma_c: float,
    bins: Sequence["AifsTemperatureBin"],
    half_step: float,
    q_point: Mapping[str, float],
    n_draws: int = _QLCB_BOOTSTRAP_DRAWS,
) -> tuple[dict[str, float], dict[str, float]]:
    """Vectorized fused-center parameter-uncertainty bootstrap for per-bin q_lcb / q_ucb.

    Draws ``n_draws`` centers μ_i ~ N(μ*, center_sigma_c) and integrates every settlement bin
    via the ONE integrator's preimage math (bin_probability_settlement, replicated vectorized
    over the (draws × bins) grid with scipy.special.ndtr). Returns (q_lcb_map, q_ucb_map) where
    q_lcb[bin] = 5th percentile and q_ucb[bin] = 95th percentile of the per-bin probability across
    draws, clipped so q_lcb ≤ q_point ≤ q_ucb per bin and q_lcb ≥ 0.

    Raises on any construction failure (caller fail-softs to NULL — Wilson fallback, status quo).
    """
    import numpy as np  # noqa: PLC0415
    from scipy.special import ndtr  # noqa: PLC0415

    if not (math.isfinite(mu_star) and math.isfinite(center_sigma_c) and math.isfinite(predictive_sigma_c)):
        raise ValueError("non-finite mu*/center_sigma/predictive_sigma for q-bound bootstrap")
    if predictive_sigma_c <= 0.0:
        raise ValueError(f"predictive_sigma must be positive, got {predictive_sigma_c}")
    if center_sigma_c < 0.0:
        raise ValueError(f"center_sigma must be non-negative, got {center_sigma_c}")
    if n_draws < 2:
        raise ValueError(f"n_draws must be >= 2, got {n_draws}")

    bin_ids = [b.bin_id for b in bins]
    if not bin_ids:
        raise ValueError("no bins for q-bound bootstrap")

    rng = np.random.default_rng(_QLCB_SEED)
    # Center draws μ_i ~ N(μ*, center_sigma). center_sigma may be ~0 for a near-certain center;
    # the draws then collapse to μ* and the bounds equal the point (correct — no center uncertainty).
    mu_draws = rng.normal(loc=float(mu_star), scale=float(center_sigma_c), size=int(n_draws))  # (N,)
    sigma = float(predictive_sigma_c)

    # Per-bin integration bounds in absolute Celsius (preimage expansion by ±half_step), matching
    # bin_probability_settlement exactly. None shoulder -> -inf / +inf via cdf 0.0 / 1.0.
    lows = np.array(
        [(-np.inf if b.lower_c is None else float(b.lower_c) - half_step) for b in bins],
        dtype=float,
    )  # (M,)
    highs = np.array(
        [(np.inf if b.upper_c is None else float(b.upper_c) + half_step) for b in bins],
        dtype=float,
    )  # (M,)

    # Standardized z = (bound - mu_i) / sigma over the (N draws × M bins) grid. ndtr is the vectorized
    # standard-normal CDF; -inf -> 0.0, +inf -> 1.0 are handled by ndtr natively.
    z_low = (lows[None, :] - mu_draws[:, None]) / sigma  # (N, M)
    z_high = (highs[None, :] - mu_draws[:, None]) / sigma  # (N, M)
    probs = np.clip(ndtr(z_high) - ndtr(z_low), 0.0, 1.0)  # (N, M) per-draw per-bin mass

    q_lcb_vec = np.percentile(probs, 5.0, axis=0)  # (M,)
    q_ucb_vec = np.percentile(probs, 95.0, axis=0)  # (M,)

    q_lcb_map: dict[str, float] = {}
    q_ucb_map: dict[str, float] = {}
    for idx, bin_id in enumerate(bin_ids):
        q_pt = float(q_point.get(bin_id, 0.0))
        lcb = float(q_lcb_vec[idx])
        ucb = float(q_ucb_vec[idx])
        if not (math.isfinite(lcb) and math.isfinite(ucb)):
            raise ValueError(f"non-finite q-bound for bin {bin_id}: lcb={lcb} ucb={ucb}")
        # Defensive ordering clips: q_lcb in [0, q_point], q_ucb >= q_point.
        lcb = min(max(lcb, 0.0), max(q_pt, 0.0))
        ucb = max(ucb, q_pt)
        q_lcb_map[bin_id] = lcb
        q_ucb_map[bin_id] = ucb
    return q_lcb_map, q_ucb_map


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
    q_shape = "aifs_member_votes_soft_anchor"
    # FUSED-Q SHAPE (2026-06-09, flag-gated): build q DIRECTLY from N(mu*, sigma_pred) over the
    # SAME settlement bins, replacing the AIFS member-vote shape. The experiment showed the AIFS
    # shape assigns EXACTLY ZERO to the winning bin on 28% of settled cells (vote-support
    # truncation) — a Normal makes that category unconstructable. Uses the ONE settlement bin
    # integrator (emos.bin_probability_settlement, the same preimage math as the live analytic
    # vector). Bin bounds are CELSIUS (lower_c/upper_c) and so are mu*/sigma_pred; half_step =
    # settlement_step_c/2 (the C-scaled rounding half-width). FAIL-CLOSED: key-set mismatch or
    # any error -> keep the soft-anchor q (loud warning), never a silent half-shape.
    # FIX 1/FIX 2 (2026-06-09): derive the EXPLICIT q-mode + record the settlement sigma-floor
    # coherence in provenance. These are derived from the SAME determinations the fused-q path
    # already makes (no parallel re-derivation). Defaults cover the no-override branches.
    replacement_q_mode = REPLACEMENT_Q_MODE_U0R_CAPTURE_MISSING
    settlement_sigma_floor_applied = False
    settlement_sigma_floor_c: float | None = None
    floor_unavailable_reason: str | None = None
    replacement_sigma_basis: str | None = None
    # Q_LCB / Q_UCB bootstrap outputs (NULL unless a fused-q is built AND the bound construction
    # succeeds). FAIL-SOFT: any failure leaves these None -> q_lcb_json/q_ucb_json written as NULL,
    # which the live side treats exactly as today (Wilson fallback) -> never WORSE than status quo.
    q_lcb_map: dict[str, float] | None = None
    q_ucb_map: dict[str, float] | None = None
    q_lcb_basis: str | None = None
    if u0r_override is not None:
        # An override exists. Default mode while we attempt the fused-q build below.
        replacement_q_mode = REPLACEMENT_Q_MODE_SOFT_ANCHOR_FALLBACK
    if (
        u0r_override is not None
        and u0r_override.predictive_sigma_c is not None
        and _replacement_fused_q_shape_enabled()
    ):
        try:
            from src.calibration.emos import bin_probability_settlement  # noqa: PLC0415

            _half_step = float(request.settlement_step_c) / 2.0
            # FIX 2 — settlement sigma floor coherence in the fused-q path. The EMOS path floors
            # sigma at the empirical settlement dispersion floor (edli_settlement_sigma_floor_enabled);
            # the fused-q path previously did NOT consult it, so "floor enabled" silently did not
            # protect the strategy of record. Look up the SAME floor (city|season|metric) and widen:
            # sigma_used = max(sigma_pred, floor). max() only WIDENS -> flatter q -> fewer overconfident
            # bets (it can never tighten). Missing/malformed floor -> recorded, NEVER blocks shadow.
            _sigma_pred = float(u0r_override.predictive_sigma_c)
            _sigma_used = _sigma_pred
            replacement_sigma_basis = "fused_center_residual_std"
            if _edli_settlement_sigma_floor_enabled():
                _floor_c, _floor_reason = _replacement_settlement_sigma_floor_lookup(
                    request, metric=metric
                )
                if _floor_c is not None:
                    settlement_sigma_floor_c = float(_floor_c)
                    if float(_floor_c) > _sigma_used:
                        _sigma_used = float(_floor_c)
                    settlement_sigma_floor_applied = True
                else:
                    floor_unavailable_reason = _floor_reason
            _fused_q = {
                b.bin_id: bin_probability_settlement(
                    mu=float(u0r_override.anchor_value_c),
                    sigma=_sigma_used,
                    bin_low=(None if b.lower_c is None else float(b.lower_c)),
                    bin_high=(None if b.upper_c is None else float(b.upper_c)),
                    half_step=_half_step,
                )
                for b in request.bins
            }
            if set(_fused_q) != set(q):
                raise ValueError(
                    f"fused-q bin keys != soft-anchor q keys ({sorted(_fused_q)[:3]}... vs "
                    f"{sorted(q)[:3]}...)"
                )
            _total = sum(_fused_q.values())
            if not (_total > 0.0 and math.isfinite(_total)):
                raise ValueError(f"fused-q mass not positive-finite: {_total}")
            q = {key: float(value) / _total for key, value in _fused_q.items()}
            q_shape = "fused_normal_direct"
            # Q_LCB / Q_UCB (2026-06-09) — fused-center parameter-uncertainty bootstrap. INDEPENDENT
            # fail-soft: a bound-construction error must NOT roll back the fused q point (that would
            # regress the q_shape gain). On error: q_lcb/q_ucb stay NULL (Wilson fallback, status quo)
            # + loud WARNING; replacement_q_mode/q_shape unaffected. The bounds use the SAME _sigma_used
            # the point q integrates at (settlement-floored if the floor applied) so q_lcb ≤ q_point ≤
            # q_ucb holds per bin; center uncertainty is fused.sd (anchor_sigma_c), NOT σ_resid (already
            # inside _sigma_used) — no double-count.
            try:
                _lcb_map, _ucb_map = _build_fused_q_bounds(
                    mu_star=float(u0r_override.anchor_value_c),
                    center_sigma_c=float(u0r_override.anchor_sigma_c),
                    predictive_sigma_c=_sigma_used,
                    bins=request.bins,
                    half_step=_half_step,
                    q_point=q,
                )
                q_lcb_map = _lcb_map
                q_ucb_map = _ucb_map
                q_lcb_basis = _QLCB_BASIS
            except Exception as _qexc:
                q_lcb_map = None
                q_ucb_map = None
                q_lcb_basis = None
                try:
                    import logging  # noqa: PLC0415
                    logging.getLogger("zeus.replacement_u0r_fusion").warning(
                        "replacement_0_1 q_lcb/q_ucb bootstrap skipped (fail-soft to NULL, "
                        "Wilson fallback unchanged): %s",
                        _qexc,
                    )
                except Exception:
                    pass
            # FIX 1 — FULL vs PARTIAL. The fused Normal is the constructed shape either way (so the
            # live gate admits both); PARTIAL records a degraded fusion. PARTIAL when EITHER the K3
            # decorrelated-provider set was INCOMPLETE (reuses the override's verdict, not a parallel
            # check) OR (per flag semantics) a settlement floor was REQUIRED but unavailable for this
            # cell. With the current config (edli_settlement_sigma_floor_required=false) a missing
            # floor does NOT degrade the mode.
            _floor_required_but_missing = (
                _edli_settlement_sigma_floor_required() and not settlement_sigma_floor_applied
            )
            # PR#403 FIX (2026-06-09): bounds required for live eligibility. FUSED_NORMAL_FULL/PARTIAL
            # now REQUIRES both q_lcb_map and q_ucb_map successfully built. Bounds failure degrades
            # to FUSED_NORMAL_BOUNDS_MISSING — the point q is fine (shadow accrual continues) but the
            # live gate will reject this mode. This kills the two-measures disease: fused-Normal q
            # point + Wilson LCB authority = two incompatible regimes, exactly the Milan root cause.
            if q_lcb_map is None or q_ucb_map is None:
                replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_NORMAL_BOUNDS_MISSING
            elif u0r_override.decorrelated_providers_complete and not _floor_required_but_missing:
                replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL
            else:
                replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL
        except Exception as _exc:
            # FIX 1 — the fused-q construction itself raised and fails CLOSED to the soft-anchor q.
            # This is DISTINCT from flag-off / predictive_sigma None (SOFT_ANCHOR_FALLBACK): the
            # mode records that a fused-q was attempted and failed, so the live gate rejects it with
            # a mode that is diagnosably different from a deliberate fallback.
            replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_Q_BUILD_FAILED
            settlement_sigma_floor_applied = False
            settlement_sigma_floor_c = None
            replacement_sigma_basis = None
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_u0r_fusion").warning(
                    "replacement_0_1 fused-q shape skipped (fail-closed to soft-anchor q): %s",
                    _exc,
                )
            except Exception:
                pass
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
    # FIX 5 (2026-06-09) — capture-status provenance (recording only; the FIX-1 live gate is the
    # enforcement point, and U0R_CAPTURE_MISSING already covers the dangerous no-override case).
    # Derived from the SAME K3 completeness verdict the fusion computed (no parallel re-derivation):
    #   FULL_CURRENT     — override present AND all 5 decorrelated providers' current values served.
    #   PARTIAL_CURRENT  — override present but the decorrelated set was INCOMPLETE (count present).
    #   STALE_HISTORY_ONLY — no fusion override at all (capture/fusion raised or current capture
    #                        missing -> the legacy single-anchor q; no current multi-model capture).
    # DB_READ_ERROR is reserved for an explicit DB read failure surfaced by the capture reader; the
    # override layer is fail-soft (returns None) so at this seam an absent override reads as
    # STALE_HISTORY_ONLY (the live gate rejects it via U0R_CAPTURE_MISSING regardless).
    if u0r_override is None:
        capture_status = REPLACEMENT_CAPTURE_STATUS_STALE_HISTORY_ONLY
    elif u0r_override.decorrelated_providers_complete:
        capture_status = REPLACEMENT_CAPTURE_STATUS_FULL_CURRENT
    else:
        capture_status = REPLACEMENT_CAPTURE_STATUS_PARTIAL_CURRENT
    # CYCLE-PHASE PROVENANCE (operator cycle-physics directive 2026-06-10). 00/12Z are the
    # full synoptic cycles; 06/18Z are intermediate cycles whose skill/bias differ. The
    # de-bias + fusion weights are trained on ~99% 00Z-cycle history, so an intermediate-cycle
    # posterior applies a synoptic-fit bias correction across cycle phase. We TAG the phase so
    # the live bundle reader can hold intermediate-phase posteriors to shadow-only by default
    # (production stays alive in dead zones; live trading waits for a settlement-graded license).
    cycle_phase = classify_cycle_phase(_to_utc(request.source_cycle_time, field_name="source_cycle_time"))
    provenance_payload = {
        "anchor_weight": request.anchor_weight,
        "anchor_sigma_c": request.anchor_sigma_c,
        "anchor_value_c": result.anchor_value_c,
        # Synoptic (00/12Z) vs intermediate (06/18Z) model-cycle phase. The live gate reads
        # THIS tag (fail-closed to the source_cycle_time hour when absent on legacy rows).
        "cycle_phase": cycle_phase,
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
        "q_shape": q_shape,
        # FIX 1 (2026-06-09): explicit q-mode authority — the live gate reads THIS, not the q_shape
        # string. FUSED_NORMAL_{FULL,PARTIAL} are live-eligible; every other mode is no-submit.
        "replacement_q_mode": replacement_q_mode,
        # FIX 2 (2026-06-09): settlement sigma-floor coherence in the fused-q path.
        "settlement_sigma_floor_applied": settlement_sigma_floor_applied,
        "settlement_sigma_floor_c": settlement_sigma_floor_c,
        "settlement_sigma_floor_unavailable_reason": floor_unavailable_reason,
        "replacement_sigma_basis": replacement_sigma_basis,
        # FIX 5 (2026-06-09): capture-status provenance (recording only).
        "capture_status": capture_status,
        # Q_LCB / Q_UCB provenance (2026-06-09). When the fused-center bootstrap succeeded these
        # carry the populated-bound role + basis; otherwise the absent-role (Wilson fallback) as
        # before. The percentile vectors do NOT sum to 1 (expected for bounds; bundle reader uses
        # require_sum=False).
        "q_lcb_json_role": (
            "fused_center_bootstrap_lcb"
            if q_lcb_map is not None
            else "absent_no_calibrated_lcb_available"
        ),
        "q_ucb_json_role": (
            "fused_center_bootstrap_ucb"
            if q_ucb_map is not None
            else "absent_no_calibrated_ucb_available"
        ),
        "q_lcb_basis": q_lcb_basis,
        "q_lcb_bootstrap_draws": (_QLCB_BOOTSTRAP_DRAWS if q_lcb_map is not None else None),
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
            "predictive_sigma_c": (
                None if u0r_override.predictive_sigma_c is None
                else float(u0r_override.predictive_sigma_c)
            ),
            "dropped_models": list(u0r_override.dropped_models),
            "excluded_regionals": list(u0r_override.excluded_regionals),
            "dropped_aliases": list(u0r_override.dropped_aliases),
            # BLOCKER 5: the persisted current rows this traded q was fused from (reconstructable).
            "raw_model_forecast_ids": list(u0r_override.raw_model_forecast_ids),
            # BLOCKER 3: the ifs025->ifs9 anchor bridge provenance applied to the anchor prior.
            "anchor_bridge": dict(u0r_override.anchor_bridge) if u0r_override.anchor_bridge else None,
            # FIX 1/FIX 5 (2026-06-09): the K3 decorrelated-provider completeness verdict (the SAME
            # determination that drives replacement_q_mode FULL vs PARTIAL + capture_status).
            "decorrelated_providers_complete": bool(u0r_override.decorrelated_providers_complete),
            "decorrelated_providers_served": int(u0r_override.decorrelated_providers_served),
            "decorrelated_providers_expected": int(u0r_override.decorrelated_providers_expected),
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
            "q_lcb": q_lcb_map,
            "q_ucb": q_ucb_map,
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
            (None if q_lcb_map is None else _json(q_lcb_map)),
            (None if q_ucb_map is None else _json(q_ucb_map)),
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
