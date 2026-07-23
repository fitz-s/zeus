"""Materialize replacement forecast posterior rows into forecast DB.

# Created: 2026-06-08
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md (the probability chain
#   §1d-§1e fused-N-direct + settlement sigma floor); FIX 1/FIX 2/FIX 5 (operator-reviewed
#   2026-06-09): explicit replacement_q_mode authority, settlement-sigma-floor coherence in the
#   fused-q path, and capture-status provenance. 2026-06-09 (q_lcb materialization): real per-bin
#   q_lcb_json/q_ucb_json on the fused path via fused-center parameter-uncertainty bootstrap
#   (root-cause /tmp/candidate_missing_rootcause.md — NULL bounds force legacy fallback bounds
#   that under-certify below ask and discard every candidate). 2026-06-13 (q_ucb
#   symmetry): the soft-anchor (CAPTURE_MISSING) fallback can compute a GENUINE Wilson UPPER bound
#   alongside its lower twin (same inputs/z), but non-live carriers must not enter
#   forecast_posteriors. Only fused-Normal rows with certified bootstrap bounds and a live runtime
#   policy are materialized as execution-authority posterior rows. 2026-06-24: ANCHOR_ONLY_CURRENT
#   is no longer a live carrier; missing BPF current inputs block materialization instead of
#   trading a single-anchor surrogate.
"""

from __future__ import annotations

import json
import math
import sqlite3
import hashlib
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from src.data.forecast_target_contract import compute_target_local_day_window_utc
from src.data.latency_metrics import emit_materialization_latency
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    ENSEMBLE_ANOMALY_TRANSPORT_SEMANTICS_REVISION,
    TRADEABLE_GRADE_QLCB_BASIS,
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
from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    LOW_DATA_VERSION,
)
from src.data.replacement_forecast_readiness import (
    LIVE_RUNTIME_LAYER,
    PRODUCT_ID,
    READY_STATUS,
    SOURCE_ID,
    STRATEGY_KEY,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
from src.contracts.availability_time import proof_of_possession_available_at
from src.state.readiness_repo import get_readiness_state_for_scope, write_readiness_state
from src.state.source_run_repo import get_source_run

UTC = timezone.utc


# ---------------------------------------------------------------------------
# FIX 1 (2026-06-09) — explicit replacement q-mode authority.
#
# A posterior row's `replacement_q_mode` is DERIVED at materialization time (never guessed)
# and recorded in provenance_json. The live gate (event_reactor_adapter) admits ONLY the two
# fused-Normal modes; every other mode is a deterministic no-submit. This kills the silent
# degradation category: a fused-q build failure differs from a certified current-evidence
# posterior by a fail-closed data-class label, never a second probability regime.
# ---------------------------------------------------------------------------
REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL = "FUSED_NORMAL_FULL"
REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL = "FUSED_NORMAL_PARTIAL"
REPLACEMENT_Q_MODE_BAYES_PRECISION_FUSION_CAPTURE_MISSING = "BAYES_PRECISION_FUSION_CAPTURE_MISSING"
REPLACEMENT_Q_MODE_FUSED_Q_BUILD_FAILED = "FUSED_Q_BUILD_FAILED"
# PR#403 FIX (2026-06-09) — fused-q succeeded but the bounds failed. DISTINCT from
# FUSED_Q_BUILD_FAILED (the point q is fine; only the bounds are absent). The fused-Normal
# q point is STILL written to the DB (blocked-candidate materialization completes for accrual), but
# live eligibility is killed. Without this a FULL/PARTIAL row with NULL q_lcb_json would be
# live-eligible, letting buy_yes fall back to legacy bounds — exactly the two-measures
# disease (fused-Normal q point + legacy LCB authority) that the Milan incident root-caused.
REPLACEMENT_Q_MODE_FUSED_NORMAL_BOUNDS_MISSING = "FUSED_NORMAL_BOUNDS_MISSING"
# FIX 5 — capture-status provenance (recording only; the live gate enforces via q_mode).
REPLACEMENT_CAPTURE_STATUS_FULL_CURRENT = "FULL_CURRENT"
REPLACEMENT_CAPTURE_STATUS_PARTIAL_CURRENT = "PARTIAL_CURRENT"
REPLACEMENT_CAPTURE_STATUS_STALE_HISTORY_ONLY = "STALE_HISTORY_ONLY"
REPLACEMENT_CAPTURE_STATUS_DB_READ_ERROR = "DB_READ_ERROR"
REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET = "REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"


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
    openmeteo_anchor: OpenMeteoIfs9LocalDayAnchor
    openmeteo_source_run_id: str | None
    openmeteo_source_available_at: datetime | str
    bins: Sequence[object]
    source_cycle_time: datetime | str
    computed_at: datetime | str
    expires_at: datetime | str | None = None
    anchor_artifact_id: int | None = None
    openmeteo_precision_guard: OpenMeteoIfs9PrecisionGuardResult | None = None
    anchor_weight: float = 0.80
    anchor_sigma_c: float = 3.00
    settlement_step_c: float = 1.0
    day0_observed_extreme_c: float | None = None
    day0_observed_extreme_source: str | None = None
    day0_observed_extreme_observation_time: datetime | str | None = None
    day0_observed_extreme_sample_count: int | None = None
    day0_observed_extreme_unit: str | None = None
    # Task #32 honest provenance: set to "instrument_set_expansion" when this materialization was
    # enqueued by the fusion-upgrade trigger (a re-materialization because a strictly-larger
    # decorrelated-provider set became capturable at the same cycle). None for a normal first
    # materialization. Threaded verbatim into provenance_json so the re-materialized posterior
    # records WHY it was produced.
    upgrade_trigger: str | None = None


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


@dataclass(frozen=True)
class PreparedReplacementForecastMaterialization:
    """Read-snapshot result awaiting a short, revalidated write transaction."""

    request: ReplacementForecastMaterializeRequest
    metric: str
    posterior: "_PosteriorComputeResult"
    anchor_id: int | None = None


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


def _role_possession_available_at(
    conn: sqlite3.Connection,
    *,
    source_run_id: str | None,
    request_source_available_at: datetime | str,
) -> datetime:
    """Honest per-role availability = PROOF OF POSSESSION (C1-AVAIL-CLOCK, 2026-06-16).

    Prefer the role's REAL download-complete wall-clock (``source_run.fetch_finished_at``) when a
    ``source_run`` row exists for the role's run-id on this forecasts connection; otherwise fall
    back to the request's per-role ``source_available_at`` (an EXISTING input, never a new guess).
    Either candidate is routed through the canonical producer (no nominal — the candidate is itself
    the possession time). ``forecast_posteriors.source_available_at`` is NOT NULL, so a non-null
    value is always returned.

    LIVE STATE (architect verdict 2026-06-16): only the baseline role currently writes a
    ``source_run`` row, so this upgrades baseline to true possession today and auto-upgrades the
    Open-Meteo role for free the day it begins recording ``source_run`` rows. Reading
    ``source_run`` here is the SAME conn/DB ``_insert_posterior`` already writes source_run rows on.
    """
    # source_run lookup degrades to None when the row OR the table is absent —
    # the repo reader (get_source_run) owns that tolerance now. A missing
    # source_run -> fall back to the request's EXISTING per-role
    # source_available_at (not a new guess); true possession resumes wherever
    # source_run exists (live zeus-forecasts.db).
    run = get_source_run(conn, source_run_id) if source_run_id else None
    fetch_finished_at = run.get("fetch_finished_at") if run else None
    possession = fetch_finished_at or request_source_available_at
    return _to_utc(
        proof_of_possession_available_at(possession),
        field_name="role_possession_available_at",
    )


def _posterior_source_available_at(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
) -> datetime:
    """Return the honest fused-posterior availability instant.

    The posterior cannot exist before the slowest contributing role is actually
    possessed. Request-level times are preflight hints; source_run rows, when
    present, are the stronger evidence because they record the completed fetch.
    """

    return max(
        _role_possession_available_at(
            conn,
            source_run_id=request.baseline_source_run_id,
            request_source_available_at=request.baseline_source_available_at,
        ),
        _role_possession_available_at(
            conn,
            source_run_id=request.openmeteo_source_run_id,
            request_source_available_at=request.openmeteo_source_available_at,
        ),
    )


def _request_with_materialization_clock(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
) -> ReplacementForecastMaterializeRequest:
    """Lift computed_at to the first instant the posterior could truly exist."""

    computed_at = _to_utc(request.computed_at, field_name="computed_at")
    source_available_at = _posterior_source_available_at(conn, request)
    if source_available_at <= computed_at:
        return request
    return replace(request, computed_at=source_available_at)


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


def _replacement_is_live_layer(
    *,
    replacement_q_mode: str,
    q_lcb_map: Mapping[str, float] | None,
    q_ucb_map: Mapping[str, float] | None,
    q_lcb_basis: str | None,
) -> bool:
    """True only for the exact live q carrier."""
    live_q_carrier = (
        replacement_q_mode in {
            REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
            REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL,
        }
        and q_lcb_map is not None
        and q_ucb_map is not None
        and q_lcb_basis == _QLCB_BASIS
    )
    if not live_q_carrier:
        return False
    return True


def _registered_source_clock_entry_ineligible(sources: Sequence[str]) -> tuple[str, ...]:
    """Registered source-clock members that are not allowed to feed live entry."""

    try:
        from src.data.forecast_source_registry import SOURCES, source_allows_role  # noqa: PLC0415
    except Exception:
        return ()
    blocked: list[str] = []
    for source in sources:
        source_id = str(source or "").strip()
        if not source_id:
            continue
        spec = SOURCES.get(source_id)
        if spec is None:
            continue
        if (
            spec.tier == "disabled"
            or not bool(spec.enabled_by_default)
            or spec.degradation_level != "OK"
            or not source_allows_role(spec, "entry_primary")
        ):
            blocked.append(source_id)
    return tuple(blocked)


def _anchor_data_version(metric: str) -> str:
    return ANCHOR_HIGH_DATA_VERSION if metric == "high" else ANCHOR_LOW_DATA_VERSION


def _json(value: Mapping[str, object] | Sequence[object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_hash(value: Mapping[str, object] | Sequence[object]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_observation_hourly_extrema_compatibility(conn: sqlite3.Connection) -> None:
    """Repair a legacy invalid view that blocks SQLite schema ALTERs."""

    columns = _table_columns(conn, "observation_instants")
    if not columns:
        return
    desired_view_sql = (
        "CREATE VIEW observation_hourly_extrema AS\n"
        "            SELECT\n"
        "                o.*,\n"
        "                o.running_max AS hour_bucket_max,\n"
        "                o.running_min AS hour_bucket_min\n"
        "            FROM observation_instants o"
    )
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='observation_hourly_extrema'"
    ).fetchone()
    view_sql = str(row[0] if row else "")
    has_v2 = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='observation_hourly_extrema_v2'"
    ).fetchone()
    if "running_min" in columns and view_sql == desired_view_sql and has_v2 is None:
        return
    if "running_min" not in columns:
        conn.execute("ALTER TABLE observation_instants ADD COLUMN running_min REAL")
    if has_v2 is not None:
        conn.execute("DROP VIEW observation_hourly_extrema_v2")
    if view_sql:
        conn.execute("DROP VIEW observation_hourly_extrema")
    conn.execute(desired_view_sql)


def _ensure_forecast_posteriors_runtime_layer(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "forecast_posteriors")
    if not columns:
        return
    _ensure_observation_hourly_extrema_compatibility(conn)
    if "runtime_layer" not in columns:
        conn.execute(
            """
            ALTER TABLE forecast_posteriors
            ADD COLUMN runtime_layer TEXT
                CHECK (runtime_layer IS NULL OR runtime_layer IN ('live'))
            """
        )
        columns.add("runtime_layer")
    has_non_live_rows = conn.execute(
        """
        SELECT 1
          FROM forecast_posteriors
         WHERE runtime_layer IS NULL
            OR runtime_layer != ?
         LIMIT 1
        """,
        (LIVE_RUNTIME_LAYER,),
    ).fetchone()
    if has_non_live_rows is not None:
        conn.execute(
            """
            DELETE FROM forecast_posteriors
             WHERE runtime_layer IS NULL
                OR runtime_layer != ?
            """,
            (LIVE_RUNTIME_LAYER,),
        )


def _ensure_replacement_identity_columns(conn: sqlite3.Connection) -> None:
    """Keep old PR399 DBs fail-closed instead of returning stale rows."""

    _ensure_forecast_posteriors_runtime_layer(conn)
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
    if posterior_columns:
        _ensure_forecast_posteriors_runtime_layer(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_deterministic_forecast_anchors_identity_hash
            ON deterministic_forecast_anchors(anchor_identity_hash)
            WHERE anchor_identity_hash IS NOT NULL
        """
    )
    posterior_columns = _table_columns(conn, "forecast_posteriors")
    if {
        "city",
        "target_date",
        "temperature_metric",
        "bin_topology_hash",
        "computed_at",
    }.issubset(posterior_columns):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_forecast_posteriors_topology
                ON forecast_posteriors(city, target_date, temperature_metric, bin_topology_hash, computed_at)
            """
        )
    if "posterior_identity_hash" in posterior_columns:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_posteriors_identity_hash
                ON forecast_posteriors(posterior_identity_hash)
                WHERE posterior_identity_hash IS NOT NULL
            """
        )


def _bin_topology_payload(bins: Sequence[object], *, settlement_step_c: float) -> list[dict[str, object]]:
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
    if not guard.passable_for_live_materialization:
        return ("OM9_PRECISION_GUARD_NOT_LIVE_PASS", *guard.reason_codes)
    return ()


def _day0_observed_extreme_c(request: ReplacementForecastMaterializeRequest) -> float | None:
    value = request.day0_observed_extreme_c
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _day0_absorbing_observed_extreme_c(
    request: ReplacementForecastMaterializeRequest,
) -> float | None:
    """Return only an observation that can logically truncate payoff support."""

    value = _day0_observed_extreme_c(request)
    if value is None:
        return None
    from src.events.day0_authority import (
        DAY0_ABSORBING_FINALITIES,
        day0_evidence_finality,
    )

    finality = day0_evidence_finality(
        {"settlement_source": request.day0_observed_extreme_source}
    )
    return value if finality in DAY0_ABSORBING_FINALITIES else None


def _target_local_day_has_started(
    request: ReplacementForecastMaterializeRequest,
    *,
    computed_at: datetime | None = None,
) -> bool:
    computed = computed_at or _to_utc(request.computed_at, field_name="computed_at")
    target_date_value = date.fromisoformat(_date_text(request.target_date))
    target_window = compute_target_local_day_window_utc(
        city_timezone=request.city_timezone,
        target_local_date=target_date_value,
    )
    return computed >= target_window.start_utc


def _local_hour_slot(value: datetime, *, city_timezone: str) -> datetime:
    tz = ZoneInfo(city_timezone)
    return value.astimezone(tz).replace(minute=0, second=0, microsecond=0)


def _expected_localday_hour_slots(*, city_timezone: str, target_date: date) -> tuple[datetime, ...]:
    window = compute_target_local_day_window_utc(
        city_timezone=city_timezone,
        target_local_date=target_date,
    )
    slots: list[datetime] = []
    cursor = window.start_utc
    while cursor < window.end_utc:
        slots.append(_local_hour_slot(cursor, city_timezone=city_timezone))
        cursor += timedelta(hours=1)
    return tuple(slots)


def _day0_remaining_center_delta_c(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    computed_at_utc: datetime,
) -> tuple[float, str | None, float | None]:
    """(delta_c >= 0, vector_id, hours_remaining) from the anchor family's hourly vector.

    Authority: T0-1 audit §7 2026-07-18. Post-peak the whole-day forecast center no
    longer describes the remaining-day extreme (served P(new extreme beyond obs)
    0.314 vs realized 0.070, 4.50x). SUB-HOURLY (operator directive 2026-07-18):
    the settlement source refreshes every few tens of minutes, so hourly
    quantization of the remaining window is too coarse — the true remaining
    window is the continuous [computed_at, end-of-local-day] (at 16:59 the
    16:00->17:00 segment still holds 1/60 of the hour). The hourly series is
    treated as piecewise-LINEAR; a piecewise-linear series attains its extremes
    at segment endpoints, so the exact continuous remaining extreme is the
    interpolated value AT computed_at plus every grid knot >= computed_at —
    identical to sampling a 5-minute (or any) fine grid, with no grid to build.
    HIGH: delta = max(whole series) - max(remaining). LOW: delta =
    min(remaining) - min(whole). Clamped >= 0. No remaining grid entries (day
    effectively over): the extreme can no longer move — the series value at the
    final grid point is the remaining value (delta = whole range, maximal
    shrink toward obs).
    hours_remaining = FRACTIONAL hours (last_grid_ts - max(computed_at,
    first_grid_ts)), >= 0.0. Null temps are gaps bridged by their valid
    neighbours; with no valid right neighbour the series ends at the last valid
    point. Fail-open (0.0, None, None) on any absence/error: vector missing,
    < 2 valid target-day entries, remaining grid slots exist but the valid series
    ends before computed_at, unparseable timestamps, timezone failure — absence
    must leave serving byte-identical.
    """
    try:
        params = (
            request.city,
            _date_text(request.target_date),
            computed_at_utc.isoformat(),
        )
        row = conn.execute(
            "SELECT vector_id, timezone_name, times_json, temps_c_json "
            "FROM day0_hourly_vectors "
            "WHERE model = 'ecmwf_ifs' AND city = ? AND target_date = ? "
            "AND captured_at <= ? ORDER BY captured_at DESC LIMIT 1",
            params,
        ).fetchone()
        if row is None:
            # Canonical requests and vectors share the configured city spelling,
            # so the indexed exact seek is the hot path. Keep a compatibility
            # fallback for old rows whose only mismatch is case.
            row = conn.execute(
                "SELECT vector_id, timezone_name, times_json, temps_c_json "
                "FROM day0_hourly_vectors "
                "WHERE model = 'ecmwf_ifs' AND lower(city) = lower(?) AND target_date = ? "
                "AND captured_at <= ? ORDER BY captured_at DESC LIMIT 1",
                params,
            ).fetchone()
        if row is None:
            return 0.0, None, None
        vector_id = str(row[0])
        tz = ZoneInfo(str(row[1]))
        times = json.loads(row[2])
        temps = json.loads(row[3])
        target = date.fromisoformat(_date_text(request.target_date))
        # (local-aware time, valid temp or None) for every grid entry on the target
        # local day. times_json entries are naive LOCAL as served; an aware entry is
        # converted. Null/non-finite temps stay as grid slots with value None so
        # "remaining hours exist but temps absent" is distinguishable from "day over".
        day_entries: list[tuple[datetime, float | None]] = []
        for raw_time, temp in zip(times, temps):
            moment = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            moment = moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)
            if moment.date() != target:
                continue
            value = None if temp is None else float(temp)
            if value is not None and not math.isfinite(value):
                value = None
            day_entries.append((moment, value))
        valid = sorted(
            ((when, value) for when, value in day_entries if value is not None),
            key=lambda item: item[0],
        )
        if len(valid) < 2:
            return 0.0, None, None
        remaining_grid_exists = any(when >= computed_at_utc for when, _ in day_entries)
        remaining_valid = [value for when, value in valid if when >= computed_at_utc]
        if remaining_grid_exists and not remaining_valid:
            return 0.0, None, None
        first_when, last_when = valid[0][0], valid[-1][0]
        if remaining_valid:
            # SUB-HOURLY: the remaining window is the continuous [computed_at, series
            # end] over the piecewise-linear series (null gaps bridged by their valid
            # neighbours). A linear segment attains its extremes at its endpoints, so
            # remaining = knots >= computed_at PLUS the interpolated value AT
            # computed_at (the partial segment's left endpoint) — exactly the 5-minute
            # fine-grid answer at every resolution, with no grid to build.
            if first_when < computed_at_utc:
                prev_when, prev_value = max(
                    ((when, value) for when, value in valid if when <= computed_at_utc),
                    key=lambda item: item[0],
                )
                next_when, next_value = min(
                    ((when, value) for when, value in valid if when >= computed_at_utc),
                    key=lambda item: item[0],
                )
                if next_when == prev_when:
                    interpolated = prev_value
                else:
                    fraction = (computed_at_utc - prev_when) / (next_when - prev_when)
                    interpolated = prev_value + fraction * (next_value - prev_value)
                remaining_valid.append(interpolated)
            hours_remaining = max(
                0.0,
                (last_when - max(computed_at_utc, first_when)).total_seconds() / 3600.0,
            )
            remaining_value = max(remaining_valid) if metric == "high" else min(remaining_valid)
        else:
            hours_remaining = 0.0
            remaining_value = valid[-1][1]
        whole_values = [value for _, value in valid]
        if metric == "high":
            delta = max(whole_values) - remaining_value
        elif metric == "low":
            delta = remaining_value - min(whole_values)
        else:
            return 0.0, None, None
        if not math.isfinite(delta):
            return 0.0, None, None
        return max(0.0, float(delta)), vector_id, hours_remaining
    except Exception:
        return 0.0, None, None


def _day0_observed_extreme_time(request: ReplacementForecastMaterializeRequest) -> datetime | None:
    value = request.day0_observed_extreme_observation_time
    if value is None:
        return None
    try:
        return _to_utc(value, field_name="day0_observed_extreme_observation_time")
    except ValueError:
        return None


def _om9_localday_hourly_coverage_ok(
    request: ReplacementForecastMaterializeRequest,
    *,
    expected_sample_count: int,
    computed_at: datetime,
) -> bool:
    anchor = request.openmeteo_anchor
    if anchor.sample_count == expected_sample_count:
        return True
    if not _target_local_day_has_started(request, computed_at=computed_at):
        return False
    if _day0_observed_extreme_c(request) is None:
        return False
    observed_at = _day0_observed_extreme_time(request)
    if observed_at is None:
        return False

    target_date_value = date.fromisoformat(_date_text(request.target_date))
    expected_slots = set(
        _expected_localday_hour_slots(
            city_timezone=request.city_timezone,
            target_date=target_date_value,
        )
    )
    covered_slots = {
        _local_hour_slot(item, city_timezone=request.city_timezone)
        for item in anchor.contributing_local_times
    }
    if not covered_slots.issubset(expected_slots):
        return False
    missing_slots = expected_slots - covered_slots
    if not missing_slots:
        return True

    observed_slot = _local_hour_slot(observed_at, city_timezone=request.city_timezone)
    if observed_slot.date() != target_date_value:
        return False
    return all(slot <= observed_slot for slot in missing_slots)


def _prewrite_block_reasons(request: ReplacementForecastMaterializeRequest) -> tuple[str, ...]:
    metric = _metric(request.temperature_metric)
    computed_at = _to_utc(request.computed_at, field_name="computed_at")
    request_source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time")
    reasons: list[str] = []
    dependency_times = [
        ("baseline_b0", _to_utc(request.baseline_source_available_at, field_name="baseline_source_available_at")),
        ("openmeteo_ifs9_anchor", _to_utc(request.openmeteo_source_available_at, field_name="openmeteo_source_available_at")),
    ]
    expected = expected_replacement_dependency_identity_by_role(metric)
    if not str(request.baseline_source_run_id or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_BASELINE_SOURCE_RUN_ID_MISSING")
    if not str(request.openmeteo_source_run_id or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_OPENMETEO_SOURCE_RUN_ID_MISSING")
    if request.baseline_data_version != expected["baseline_b0"].data_version:
        reasons.append("REPLACEMENT_MATERIALIZATION_BASELINE_DATA_VERSION_MISMATCH")
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
    if not _om9_localday_hourly_coverage_ok(
        request,
        expected_sample_count=expected_om9_count,
        computed_at=computed_at,
    ):
        reasons.append("REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE")
    if _target_local_day_has_started(request, computed_at=computed_at) and _day0_observed_extreme_c(request) is None:
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
    return tuple(reasons)


def _cycle_monotone_block_reasons(
    conn: sqlite3.Connection, request: ReplacementForecastMaterializeRequest, *, metric: str
) -> tuple[str, ...]:
    """MONOTONE CONSUMED-CYCLE ADVANCE (U5 step 2a, freshness investigation 2026-06-12).

    A family's posterior must never step BACKWARD onto a model cycle OLDER than the one its
    CURRENT (latest) posterior already consumed. The freshness investigation measured this as a
    real disease: ~14% of posteriors were born stale (an older anchor cycle consumed while a
    fresher one was already ingested) and 78 backward consumed-cycle transitions thrashed
    q_mean by ±2.5 °C across 267 live families (docs/evidence/freshness/2026-06-12). Belief
    drift is a STEP function on NEW cycles, so consuming an OLDER cycle is a self-inflicted
    staleness event with no upside.

    The consumed cycle is recorded as ``forecast_posteriors.source_cycle_time`` (the provenance
    field; no new column). The refusal is keyed on the SAME (source_id, city, target_date,
    temperature_metric) family identity the fusion-upgrade trigger and serving authority use, so
    the three sites can never disagree on family identity.

    EQUAL cycle is ALLOWED: re-materializing the SAME cycle is the legitimate same-cycle path
    (instrument-set expansion / fusion upgrade — Task #32). Only a STRICTLY older request cycle
    is refused. A typed BLOCKED reason makes the backward step unconstructable (it never writes a
    row), not a silent thrash. Fail-open ONLY on a read/schema error (the bounded-staleness gate
    in _prewrite_block_reasons remains the backstop) — a backward step is never *silently*
    admitted, but an unreadable DB must not wedge all materialization.
    """
    try:
        request_cycle = _to_utc(request.source_cycle_time, field_name="source_cycle_time")
    except Exception:
        return ()
    try:
        row = conn.execute(
            """
            SELECT source_cycle_time
            FROM forecast_posteriors
            WHERE source_id = ? AND city = ? AND target_date = ? AND temperature_metric = ?
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (SOURCE_ID, request.city, _date_text(request.target_date), metric),
        ).fetchone()
    except Exception:
        return ()
    if row is None:
        return ()
    consumed_iso = row[0] if not hasattr(row, "keys") else row["source_cycle_time"]
    if consumed_iso is None or not str(consumed_iso).strip():
        return ()
    try:
        consumed_cycle = _to_utc(str(consumed_iso), field_name="latest_posterior_source_cycle_time")
    except Exception:
        return ()
    if request_cycle < consumed_cycle:
        import logging  # noqa: PLC0415

        logging.getLogger("zeus.replacement_cycle_monotone").warning(
            "REFUSED backward consumed-cycle materialization for %s %s %s: request cycle %s is "
            "OLDER than the family's current posterior cycle %s (monotone-advance law). The "
            "backward step is unconstructable; the family keeps its fresher belief.",
            request.city,
            _date_text(request.target_date),
            metric,
            request_cycle.isoformat(),
            consumed_cycle.isoformat(),
        )
        return ("REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_REGRESSION",)
    return ()


def _insert_anchor(conn: sqlite3.Connection, request: ReplacementForecastMaterializeRequest, *, metric: str) -> int:
    anchor = request.openmeteo_anchor
    target_date = _date_text(request.target_date)
    source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat()
    # C1-AVAIL-CLOCK: anchor availability = proof of possession of the openmeteo
    # fetch — route through the canonical producer (auto-upgrades to the real
    # source_run.fetch_finished_at where present, else the request's existing
    # source_available_at; never a fabricated cycle stand-in). Same sanctioned
    # producer the posterior insert uses.
    source_available_at = _role_possession_available_at(
        conn,
        source_run_id=request.openmeteo_source_run_id,
        request_source_available_at=request.openmeteo_source_available_at,
    ).isoformat()
    computed_at = _to_utc(request.computed_at, field_name="computed_at").isoformat()
    value_c = anchor.high_c if metric == "high" else anchor.low_c
    contributing_times = [item.isoformat() for item in anchor.contributing_valid_times_utc]
    provenance = {
        "city_timezone": request.city_timezone,
        "source_run_id": request.openmeteo_source_run_id,
        "measurement_policy": anchor.measurement_policy,
        "precision_guard": _precision_guard_payload(request.openmeteo_precision_guard),
        "role": "soft_spatial_anchor",
        "training_allowed": False,
    }
    # Task #32: honest re-materialization provenance. Recorded ONLY when the trigger set it, so a
    # normal first materialization's provenance_json is byte-identical to before this change.
    if request.upgrade_trigger:
        provenance["upgrade_trigger"] = str(request.upgrade_trigger)
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
            training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _replacement_settlement_sigma_floor_lookup(
    request: "ReplacementForecastMaterializeRequest",
    *,
    metric: str,
) -> tuple[float | None, str | None]:
    """FIX 2 (2026-06-09) — resolve the SAME settlement sigma floor the EMOS path uses for this cell.

    Returns ``(floor_c, unavailable_reason)``:
      - ``(value, None)`` when a positive floor exists for the (city, season, metric) cell.
      - ``(None, reason)`` when the floor lookup is missing/malformed for the cell — recording-only,
        NEVER blocks blocked-candidate materialization. The reason is folded into provenance.

    Single-builder: this calls src.calibration.emos.settlement_sigma_floor (the SAME lookup the
    EMOS q-builder uses, keyed city|season|metric via emos_cell_key), with required=False so a
    missing cell returns None rather than raising. Season is derived from target_date + the city's
    config latitude (season_from_date(target_date, lat)). FAIL-SOFT throughout.
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
    except Exception as exc:  # fail-soft: never block blocked-candidate materialization
        return None, f"SETTLEMENT_SIGMA_FLOOR_LOOKUP_ERROR:{type(exc).__name__}"


_SIGMA_SCALE_FIT_PATH = "state/sigma_scale_fit.json"


def _replacement_sigma_scale_lookup(unit: str) -> tuple[float, float, float]:
    """C3 calibration surface — FITTED σ_pred scale (k) + uniform-mixture weight (w) + σ-floor (floor_steps).

    OPERATOR LAW (2026-06-12) "没有一个人可以在没有数学支持下决定一个 hard coded value": the σ-scale
    factor must be FITTED by math, never operator-picked or hardcoded. This reads the fitted artifact
    ``state/sigma_scale_fit.json`` (written ONLY by the σ-scale fitter — MLE over settled cells)
    and returns ``(k, w, floor_steps)`` for the given settlement unit family ('C' / 'F'):
      σ_core = max(σ_impl · k, floor_steps · step)   [step = per-cell bin width in settlement units]
      q_adjusted(bin) = (1 - w) · Normal(σ_core) + w · uniform(1/n_bins).

    ``floor_steps`` (σ-refit report 2026-06-13, task #69) is the GATE-2 fix: an ABSOLUTE σ-floor in
    step units that replaces the multiplicative widen — the realized ring dispersion is ~constant in
    absolute (step) terms (~1.8 steps in BOTH C and F families), so a floor widens over-sharp forecasts
    UP TO the realized dispersion and leaves already-wide forecasts alone (regime-aware → holdout-stationary).

    Returns ``(k, w, floor_steps)`` where:
      - artifact present AND family entry has fitted=True → (k, w, floor_steps), each field clamped.
      - ``floor_steps`` is ABSENT from the artifact (the current live state/sigma_scale_fit.json) → 0.0,
        so ``max(σ_impl·k, 0.0·step) = σ_impl·k`` is BYTE-IDENTICAL to pre-floor behavior. The live q
        does NOT change until the operator swaps the artifact for one carrying ``floor_steps``.
      - artifact missing, malformed, family absent, or family fitted=False (REFUSED, e.g. F when
        n<60) → (1.0, 0.0, 0.0) INERT (byte-identical to pre-scale behavior).

    Precedent: the settlement sigma floor artifact (#20) is read the same fail-soft way. The fit
    artifact's per-family ``fitted`` flag is the enable: a family is corrected ONLY when math licensed
    it. FAIL-SOFT: any error → (1.0, 0.0, 0.0). Never raises.
    """
    try:
        import os  # noqa: PLC0415

        # Resolve the σ-scale fit against the RUNTIME state dir (ZEUS_PRIMARY_ROOT/
        # state — the shared live state the fitter writes), like every other state
        # artifact, NOT relative to __file__ (the deployed code tree). The live
        # daemon runs CODE from zeus-live-main but STATE from /Users/leofitz/zeus/
        # state; resolving via __file__ made it read a STALE bundled copy (C k=1.0)
        # and silently drop the fitted k<1 sharpening, so the served forecast stayed
        # too flat (modal under-weighted → YES leaks to tails, NO on the predicted
        # bin). Dev/tests resolve to the same path, so they are unchanged.
        # (2026-06-23 severed-σ-scale fix.)
        from src.config import runtime_state_path  # noqa: PLC0415

        path = str(runtime_state_path("sigma_scale_fit.json"))
        if not os.path.exists(path):
            return 1.0, 0.0, 0.0
        with open(path, "r", encoding="utf-8") as fh:
            artifact = json.load(fh)
        fam = (artifact.get("families") or {}).get(str(unit).upper())
        if not isinstance(fam, dict) or not fam.get("fitted"):
            return 1.0, 0.0, 0.0
        k = float(fam.get("k", 1.0))
        w = float(fam.get("w", 0.0))
        # floor_steps ABSENT ⇒ 0.0 (strict backward compatibility: the live artifact has no such key,
        # so the floor term is inert and q is unchanged). A non-finite / negative value is also inert.
        floor_steps = float(fam.get("floor_steps", 0.0))
        if not (math.isfinite(k) and k > 0.0):
            k = 1.0
        if not (math.isfinite(w) and 0.0 <= w <= 1.0):
            w = 0.0
        if not (math.isfinite(floor_steps) and floor_steps >= 0.0):
            floor_steps = 0.0
        return k, w, floor_steps
    except Exception:
        return 1.0, 0.0, 0.0


def _effective_unit_sigma_scale(unit: str) -> tuple[float, float, float]:
    """The (k, w, floor_steps) the fused-q build applies for a settlement-unit family.

    The FITTED artifact is the SOLE licensing authority. ``_replacement_sigma_scale_lookup`` already
    returns inert ``(1.0, 0.0, 0.0)`` for any family the fitter REFUSED (``fitted=False`` — written
    when a family has < MIN_CELLS=60 settled cells), so a family is σ-scaled iff math licensed it by
    fitting it. There is NO hardcoded settlement-unit allow-list.

    2026-06-23 universal-correctness fix: the prior inline ``unit != "C"`` defense-in-depth gate
    forced EVERY non-Celsius family to ``(1.0, 0.0)`` regardless of the artifact. That was correct
    when F was unfitted (n=47 < 60), but the σ-scale fitter has since LICENSED F (n_cells=100,
    k=0.7322, w=0.0552; settled d=0 modal realized/expected ratio 1.424→1.090) — and the gate was
    SUPPRESSING that math-supported correction, leaving every US (Fahrenheit) city's served
    posterior too FLAT, so the modal (predicted) bin was under-weighted and buy_yes leaked to
    deep-OTM tails instead of landing on the predicted bin. The artifact's per-family ``fitted``
    flag now governs uniformly. OPERATOR LAW (2026-06-12) "no hardcoded value without math support"
    is satisfied: k is MLE-fitted (never hand-set), and a refused family still falls back to inert.
    """
    return _replacement_sigma_scale_lookup(unit)


# Created: 2026-06-29
# Authority basis: capital-gated per-city rho-mix serving (frontier-consult validated; fitter side done).
#   This REPLACES the prior UNSAFE per-city hard swap (which served any per-city (k,w) directly and
#   harmed ~40% of cities). The city candidate is now read SEPARATELY from the global pair and served via
#   a non-inferiority MIXTURE rho = 1-exp(-C/W). The fitter writes families[unit]["cities"][city] =
#   {k, w, k_raw, w_raw, n_cells, score_capital} and ONLY for cities with positive earned OOS capital.
def _replacement_city_candidate_lookup(unit: str, city: str | None) -> dict | None:
    """Return the per-city EB candidate ``{"k", "w", "score_capital"}`` for the family, or None.

    Reads the SAME fitted artifact ``state/sigma_scale_fit.json`` the global lookup uses, fail-soft. A
    candidate is returned ONLY when:
      - the family is fitted (a REFUSED family licenses nothing — no global scale, no city candidate), and
      - the city has an entry under ``families[unit]["cities"]`` carrying a FINITE, POSITIVE
        ``score_capital`` C (the prequential Bernoulli-log-score the city's (k,w) earned OVER global on
        rolling OOS splits). C ≤ 0, a missing ``score_capital`` key, an absent city, an artifact with no
        ``"cities"`` key, or no ``city`` argument ⇒ None ⇒ rho=0 ⇒ pure global serving (byte-identical to
        today). k/w are clamped exactly as the global lookup clamps them.

    NEVER hard-swaps: the caller mixes this candidate's q with the global q by rho. Any error → None.
    """
    if not city:
        return None
    try:
        import os  # noqa: PLC0415
        from src.config import runtime_state_path  # noqa: PLC0415

        path = str(runtime_state_path("sigma_scale_fit.json"))
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            artifact = json.load(fh)
        fam = (artifact.get("families") or {}).get(str(unit).upper())
        if not isinstance(fam, dict) or not fam.get("fitted"):
            return None
        cfam = (fam.get("cities") or {}).get(str(city))
        if not isinstance(cfam, dict):
            return None
        if "score_capital" not in cfam:
            return None  # cannot license a mix without earned capital (defensive; fitter always writes it)
        cap = float(cfam.get("score_capital"))
        if not (math.isfinite(cap) and cap > 0.0):
            return None  # C <= 0 ⇒ rho would be 0 ⇒ serve global; signal "no candidate"
        k = float(cfam.get("k", 1.0))
        w = float(cfam.get("w", 0.0))
        if not (math.isfinite(k) and k > 0.0):
            k = 1.0
        if not (math.isfinite(w) and 0.0 <= w <= 1.0):
            w = 0.0
        return {"k": k, "w": w, "score_capital": cap}
    except Exception:
        return None


def _city_settlement_unit_from_bins(request: "ReplacementForecastMaterializeRequest") -> str:
    """Return the settlement unit ('C' or 'F') for the city, derived from the request bins.

    Uses the first bin's ``settlement_unit`` field (the family is uniform — the bin topology
    validator enforces a single unit across all bins in a family). Falls back to 'C' on any
    error so the scale gate is safe: the C scale is only applied when the unit is positively
    identified as 'C', never speculatively.
    """
    try:
        bins = request.bins
        if bins:
            return str(bins[0].settlement_unit)
        return "C"
    except Exception:
        return "C"


def _build_sigma_repr_by_model(
    city: str,
    models: "Sequence[str]",
    *,
    anchor_model: str,
) -> dict[str, float]:
    """Option C — per-model grid-representativeness variance sigma_repr² (degC²) for the
    materializer EXIT center.

    Reads geometry via ``grid_representativeness_loader.sigma_repr_sq_for(city, model)``,
    which is FAIL-SOFT: a city/model/cell absent from config/grid_representativeness.json
    yields 0.0, so that member is byte-identical to today (no fabricated penalty). The
    returned dict is in **degC²** to match the materializer's degC² ``raw_m2`` basis (the
    shared helper adds repr in the same unit basis as raw_m2, no scaling). The ANCHOR
    member is keyed by the same ``anchor_model`` sentinel the center uses; the loader maps
    it to the anchor's OM grid cell when present (else 0.0). Only models with a positive
    finite repr are kept (0.0 entries are equivalent to absence and stay byte-identical).

    Entirely best-effort: any loader/engine error → empty dict → byte-identical center.
    """
    out: dict[str, float] = {}
    try:
        from src.forecast.grid_representativeness_loader import (  # noqa: PLC0415
            sigma_repr_sq_for,
        )
    except Exception:  # noqa: BLE001
        return out
    for _model in models:
        try:
            _v = float(sigma_repr_sq_for(str(city), str(_model)))
        except Exception:  # noqa: BLE001 — geometry is best-effort; absence == 0.0
            _v = 0.0
        if math.isfinite(_v) and _v > 0.0:
            out[str(_model)] = _v
    # The anchor sentinel may differ from the loader's anchor model name; both keys are
    # tried by the caller, so a 0.0 for an unmapped anchor is simply byte-identical.
    del anchor_model  # kept for call-site clarity / future anchor-cell mapping
    return out


@dataclass(frozen=True)
class _BayesPrecisionFusionFusionOverride:
    """The BAYES_PRECISION_FUSION fused center/spread that replace the single-anchor in the soft-anchor build,
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
    # FUSED-Q SHAPE: the PREDICTIVE sigma for building
    # q directly from N(mu*, sigma_pred) — sigma_pred^2 = fused.sd^2 + sigma_resid^2, where
    # sigma_resid is the walk-forward std of the fused-center residual series (common-date mean
    # of the instruments' de-biased residuals), conservatively floored. None when the residual
    # substrate is too thin AND no conservative default applies.
    predictive_sigma_c: float | None = None
    # FIX 1 (2026-06-09): the K3 decorrelated-provider completeness verdict computed INSIDE the
    # fusion (the same served/expected determination the materializer already logs). True =
    # all domain/lead-servable decorrelated providers served (-> FUSED_NORMAL_FULL); False = INCOMPLETE
    # (-> FUSED_NORMAL_PARTIAL). The materializer REUSES this; it never re-derives a parallel
    # provider check (single-builder).
    decorrelated_providers_complete: bool = False
    # FIX 5 (2026-06-09): capture-status provenance. Count of the domain/lead-servable
    # decorrelated providers whose CURRENT value entered the fused set for this cell.
    decorrelated_providers_served: int = 0
    decorrelated_providers_expected: int = 5
    # Task #32 follow-up (brand law): per-instrument serving provenance for every model that
    # entered the fused set — which endpoint served its CURRENT value (served_via), the served
    # row id/cycle/capture stamp/age, and its lead bucket. A previous_runs substitution (a
    # provider whose selected cycle has no single_runs row, e.g. JMA at 06Z-cadence cycles) is
    # therefore RECORDED in the posterior provenance, never silent.
    current_value_serving: Mapping[str, Mapping[str, object]] | None = None
    # Option C (2026-06-21): RAW-precision center basis provenance so the served center is
    # RECONSTRUCTIBLE even when geometry variances change the weights under the same model
    # set. Per-model {raw_m2, n, repr_m2 (degC²), denom, weight} + a precision_basis_hash
    # over the full basis (model→(raw_m2, n, repr_m2, weight)). repr_m2=0.0 for every model
    # when the grid table is absent ⇒ precision_center_basis is the same as before Option C.
    precision_center_basis: Mapping[str, Mapping[str, float]] | None = None
    precision_basis_hash: str | None = None
    # Low-n prior weighting (2026-06-28): models whose walk-forward VERIFIED settled
    # obs count was below MIN_SETTLED_N. These models are not excluded; their raw_m2
    # is EB-shrunk toward the equal-precision prior before center weighting.
    low_n_prior_weighted_models: tuple[str, ...] = ()
    # Source-clock vNext fixed city basket (2026-06-25): present when the per-city one-scheme
    # artifact supplied the served center. This is the live replacement upgrade surface.
    source_clock_one_scheme: Mapping[str, object] | None = None
    # Decision-time-only probability shape used by the live source-clock route.
    # This is intentionally distinct from historical residual calibration: the
    # within component comes from the latest causal ECMWF ENS members for this
    # target, while the between component comes from the same current provider
    # values that produced the center.
    current_evidence_shape: Mapping[str, object] | None = None
    # In-memory only: exact current members used to count settlement-preimage
    # hits for the executable ambiguity band. The persisted shape carries their
    # hash, not a duplicated 51-value payload.
    current_evidence_members_c: tuple[float, ...] | None = None


@dataclass(frozen=True)
class _CurrentEvidenceShape:
    """Current target-specific predictive shape with no historical residual input."""

    snapshot_id: int
    semantics_revision: str
    source_cycle_time: str
    source_available_at: str
    members_c: tuple[float, ...]
    member_values_hash: str
    member_count: int
    provider_count: int
    effective_provider_count: float
    ensemble_member_mean_c: float
    ensemble_center_delta_c: float
    ensemble_within_sigma_c: float
    provider_between_sigma_c: float
    predictive_sigma_c: float
    center_sigma_c: float
    shape_hash: str
    # Anomaly transport provenance (P2-B, 2026-07-17): shape_lag_hours is
    # carrier_cycle_time - source_cycle_time in hours; translation_applied is
    # true only when shape_lag_hours > 0 (ENS cycle older than the carrier).
    # ens_center_delta_raw_c is the PRE-translation mu_t - member_mean, kept for
    # research/regime-discordance only -- it is NEVER folded into sigma.
    shape_lag_hours: float
    translation_applied: bool
    ens_center_delta_raw_c: float
    # Between-spread freshest-coherent-cohort provenance (consult v2 (b), 2026-07-17):
    # populated ONLY when the ±3h cohort filter actually excluded a provider from the
    # between term (None otherwise). None fields are DROPPED from as_payload so every
    # pre-existing row's provenance payload stays byte-identical; they are NEVER part of
    # the shape_hash identity dict (the cohort-filtered between value itself already
    # distinguishes the shape).
    between_cohort_models: tuple[str, ...] | None = None
    between_cohort_excluded: tuple[str, ...] | None = None
    # Shape-age sigma term (consult P2-B full form, 2026-07-17): the fitted variance
    # gamma_g * shape_lag_hours/6 ADDED to the transported predictive variance — the
    # remaining risk of pricing with an aged shape after transport removed the center
    # error. None (payload-dropped) when the term is zero: transported branch with no
    # fitted artifact and every same-cycle row stay byte-identical. Same shape_hash
    # discipline as the cohort fields: never in the identity dict — the widened
    # predictive_sigma_c inside `identity` already distinguishes the shape.
    shape_age_sigma_term_c2: float | None = None

    def as_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("members_c")
        if payload.get("between_cohort_models") is None:
            payload.pop("between_cohort_models", None)
            payload.pop("between_cohort_excluded", None)
        if payload.get("shape_age_sigma_term_c2") is None:
            payload.pop("shape_age_sigma_term_c2", None)
        return payload


# Freshest-coherent-cohort window for the between term (consult v2 (b), 2026-07-17):
# providers within this many hours of the freshest provider cycle count as simultaneous.
# ±3h spans one 6h issuance cycle's half-width — models one full cycle behind are OUT.
BETWEEN_COHORT_WINDOW_HOURS = 3.0


def _current_evidence_shape_from_values(
    *,
    snapshot_id: int,
    source_cycle_time: str,
    source_available_at: str,
    members_c: Sequence[float],
    provider_values_c: Mapping[str, float],
    provider_weights: Mapping[str, float],
    center_c: float,
    carrier_cycle_time: str | datetime | None = None,
    provider_cycles: Mapping[str, str] | None = None,
    shape_age_gamma_c2_per_6h: float = 0.0,
) -> _CurrentEvidenceShape:
    """Compose current ensemble and provider disagreement without a fitted floor.

    The only distributional assumption is maximum-entropy Normal integration
    downstream from current observable second moments. The ensemble members
    measure within-model state uncertainty, their absolute mean displacement
    measures ENS/provider-center disagreement, and simultaneous provider
    centers measure between-model uncertainty. Independent components add in
    variance.

    ``carrier_cycle_time`` (P2-B anomaly transport, 2026-07-17): when supplied
    and newer than ``source_cycle_time`` (shape_lag_hours > 0), the ENS cycle
    is being reused stale and is licensed ONLY as a location-shape transport
    model (consult docs/evidence/upstream_physical_2026_07_17/
    consult_freshness_decoupling_verdict.txt P2-B): members are translated onto
    the fresh center (anomalies from the ONE coherent cycle, recentered), and
    the operational ensemble_center_delta is zeroed rather than folded into
    sigma -- carrying the raw center disagreement forward as squared
    uncertainty would double-count the new center. The pre-translation delta
    is kept as provenance-only ``ens_center_delta_raw_c``. Omitting
    ``carrier_cycle_time`` (legacy call sites) or passing the same cycle as
    ``source_cycle_time`` leaves today's same-cycle semantics untouched.

    ``provider_cycles`` (freshest-coherent-cohort, consult v2 (b), 2026-07-17):
    optional model -> served cycle ISO stamp. When supplied, the BETWEEN term is
    computed only over providers whose cycle is within ``BETWEEN_COHORT_WINDOW_HOURS``
    of the freshest provider cycle — cross-cycle displacement is staleness error
    (already priced as v_m(lag) variance in the center weights), not simultaneous
    model disagreement, and folding it into between would double-count it. The
    CENTER and its weights are untouched (stale providers still enter the center,
    downweighted, never excluded). FAIL-OPEN: ``provider_cycles`` absent, a
    provider's cycle missing/unparseable, or a coherent cohort of fewer than 2
    providers -> between over ALL providers, byte-identical to today.

    ``shape_age_gamma_c2_per_6h`` (consult P2-B full form, 2026-07-17): the fitted
    excess-variance slope from ``src.forecast.shape_age_sigma.gamma_for`` (degC² per 6h
    of shape lag; ``scripts/fit_shape_age_sigma.py``). On the TRANSPORTED branch only,
    ``gamma * shape_lag_hours/6`` is added to the predictive VARIANCE:
    sigma = sqrt(within² + between² + gamma*lag/6) — the remaining risk of pricing with
    an aged shape after transport removed the center error (deterministic staleness
    slopes measure center drift, not shape staleness, so this term has its own fit).
    CENTER_SIGMA deliberately excludes the term: the fit's residual is settle − FRESH
    fused center, so gamma prices excess settlement dispersion around a center whose
    own estimation error is unchanged by shape age — it is predictive width, not
    center uncertainty. gamma <= 0 / non-finite, or the same-cycle branch
    (shape_lag_hours <= 0), are byte-identical to today.
    """

    raw_members = tuple(float(value) for value in members_c)
    if len(raw_members) < 20 or any(not math.isfinite(value) for value in raw_members):
        raise ValueError("current ensemble requires at least 20 finite members")
    center = float(center_c)
    if not math.isfinite(center):
        raise ValueError("current provider center must be finite")

    weighted: list[tuple[str, float, float]] = []
    for model, raw_weight in provider_weights.items():
        if model not in provider_values_c:
            continue
        value = float(provider_values_c[model])
        weight = float(raw_weight)
        if math.isfinite(value) and math.isfinite(weight) and weight > 0.0:
            weighted.append((str(model), value, weight))
    if len(weighted) < 2:
        raise ValueError("current shape requires at least two weighted providers")
    weight_total = sum(weight for _, _, weight in weighted)
    normalized = tuple(
        (model, value, weight / weight_total) for model, value, weight in weighted
    )

    raw_member_mean = sum(raw_members) / len(raw_members)
    # Consult D_t = mu_t - Xbar_e: provenance-only, never operational.
    ens_center_delta_raw = center - raw_member_mean

    shape_lag_hours = 0.0
    if carrier_cycle_time is not None:
        carrier_dt = _to_utc(carrier_cycle_time, field_name="carrier_cycle_time")
        source_dt = _to_utc(source_cycle_time, field_name="source_cycle_time")
        shape_lag_hours = (carrier_dt - source_dt).total_seconds() / 3600.0
    translation_applied = shape_lag_hours > 0.0

    if translation_applied:
        # X'_j = center_c + (member_j - member_mean): anomalies from the one
        # coherent selected cycle, recentered on the fused center. This
        # preserves within-spread exactly (a pure shift), and these translated
        # values -- not the raw members -- are the operative sample for
        # downstream finite-evidence preimage hit counting.
        members = tuple(center + (value - raw_member_mean) for value in raw_members)
        member_mean = center
        ensemble_center_delta = 0.0
    else:
        members = raw_members
        member_mean = raw_member_mean
        ensemble_center_delta = raw_member_mean - center

    within = math.sqrt(
        sum((value - member_mean) ** 2 for value in members) / len(members)
    )
    # Freshest-coherent-cohort between (consult v2 (b)): simultaneous disagreement is
    # only measurable among providers speaking from (near-)the-same cycle; a stale
    # provider's displacement is issuance-lag error, already priced as v_m(lag) in the
    # center weights. Cohort = providers within BETWEEN_COHORT_WINDOW_HOURS of the
    # freshest parseable provider cycle; a provider with a missing/unparseable cycle is
    # INCLUDED (fail-open: absent provenance never shrinks the evidence basis). Weights
    # renormalized within the cohort so between stays a proper weighted spread. Fail-open
    # to ALL providers when no cycle parses or the coherent cohort is < 2.
    cohort = normalized
    between_cohort_models: tuple[str, ...] | None = None
    between_cohort_excluded: tuple[str, ...] | None = None
    if provider_cycles is not None:
        cycle_by_model: dict[str, datetime] = {}
        for model, _value, _weight in normalized:
            raw_cycle = provider_cycles.get(model)
            if raw_cycle is None:
                continue
            try:
                cycle_by_model[model] = _to_utc(
                    str(raw_cycle), field_name="provider_cycle"
                )
            except Exception:
                continue
        if cycle_by_model:
            freshest = max(cycle_by_model.values())
            coherent = tuple(
                (model, value, weight)
                for model, value, weight in normalized
                if model not in cycle_by_model
                or (freshest - cycle_by_model[model]).total_seconds() / 3600.0
                <= BETWEEN_COHORT_WINDOW_HOURS
            )
            if len(coherent) >= 2 and len(coherent) < len(normalized):
                cohort_total = sum(weight for _, _, weight in coherent)
                cohort = tuple(
                    (model, value, weight / cohort_total)
                    for model, value, weight in coherent
                )
                between_cohort_models = tuple(model for model, _, _ in coherent)
                between_cohort_excluded = tuple(
                    model
                    for model, _, _ in normalized
                    if model not in between_cohort_models
                )
    between = math.sqrt(
        sum(weight * (value - center) ** 2 for _, value, weight in cohort)
    )
    # These members remain absolute settlement-bin evidence downstream.  Their
    # displacement from the served center is therefore current disagreement,
    # not a location term that can be silently recentered out of the width --
    # except when shape_lag>0, where translation already recentered them and
    # ensemble_center_delta is 0.0 (this hypot() term drops out naturally).
    sigma = math.hypot(within, between, ensemble_center_delta)
    # Shape-age sigma term (consult P2-B): fitted remaining-risk variance of the aged,
    # transported shape. TRANSPORTED branch only; a zero/absent gamma leaves the sqrt
    # recomposition untaken so serving stays byte-identical (fail-open dormant).
    shape_age_sigma_term: float | None = None
    if translation_applied:
        try:
            gamma = float(shape_age_gamma_c2_per_6h)
        except (TypeError, ValueError):
            gamma = 0.0
        if math.isfinite(gamma) and gamma > 0.0:
            shape_age_sigma_term = gamma * shape_lag_hours / 6.0
            sigma = math.sqrt(sigma * sigma + shape_age_sigma_term)
    effective_providers = 1.0 / sum(weight * weight for _, _, weight in normalized)
    center_sigma = math.hypot(
        within / math.sqrt(len(members)),
        between / math.sqrt(effective_providers),
        ensemble_center_delta,
    )
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("current evidence predictive sigma must be positive")
    if not math.isfinite(center_sigma) or center_sigma <= 0.0:
        raise ValueError("current evidence center sigma must be positive")

    semantics_revision = (
        ENSEMBLE_ANOMALY_TRANSPORT_SEMANTICS_REVISION
        if translation_applied
        else CURRENT_EVIDENCE_SEMANTICS_REVISION
    )

    identity = {
        "semantics_revision": semantics_revision,
        "snapshot_id": int(snapshot_id),
        "source_cycle_time": str(source_cycle_time),
        "source_available_at": str(source_available_at),
        "member_count": len(members),
        "member_values_hash": _json_hash(list(members)),
        "providers": [
            {"model": model, "value_c": value, "weight": weight}
            for model, value, weight in normalized
        ],
        "center_c": center,
        "ensemble_member_mean_c": member_mean,
        "ensemble_center_delta_c": ensemble_center_delta,
        "ensemble_within_sigma_c": within,
        "provider_between_sigma_c": between,
        "predictive_sigma_c": sigma,
        "center_sigma_c": center_sigma,
        "shape_lag_hours": shape_lag_hours,
        "translation_applied": translation_applied,
        "ens_center_delta_raw_c": ens_center_delta_raw,
    }
    member_values_hash = str(identity["member_values_hash"])
    return _CurrentEvidenceShape(
        snapshot_id=int(snapshot_id),
        semantics_revision=semantics_revision,
        source_cycle_time=str(source_cycle_time),
        source_available_at=str(source_available_at),
        members_c=members,
        member_values_hash=member_values_hash,
        member_count=len(members),
        provider_count=len(normalized),
        effective_provider_count=effective_providers,
        ensemble_member_mean_c=member_mean,
        ensemble_center_delta_c=ensemble_center_delta,
        ensemble_within_sigma_c=within,
        provider_between_sigma_c=between,
        predictive_sigma_c=sigma,
        center_sigma_c=center_sigma,
        shape_hash=_json_hash(identity),
        shape_lag_hours=shape_lag_hours,
        translation_applied=translation_applied,
        ens_center_delta_raw_c=ens_center_delta_raw,
        # Cohort provenance intentionally OUTSIDE the `identity` dict above: when the
        # filter is inactive these are None (payload-dropped, shape_hash byte-identical
        # for every existing row); when active, the filtered `between` value inside
        # `identity` already changes the hash — stamping the model lists there too would
        # be redundant identity churn.
        between_cohort_models=between_cohort_models,
        between_cohort_excluded=between_cohort_excluded,
        # Same discipline: outside `identity` — a positive term already widens the
        # predictive_sigma_c inside `identity`, which changes the hash; a zero/absent
        # term is None (payload-dropped), keeping every pre-existing row byte-identical.
        shape_age_sigma_term_c2=shape_age_sigma_term,
    )


def _read_current_evidence_shape(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    provider_values_c: Mapping[str, float],
    provider_weights: Mapping[str, float],
    center_c: float,
    provider_cycles: Mapping[str, str] | None = None,
) -> _CurrentEvidenceShape | None:
    """Read the latest causal target-specific ECMWF ENS available at decision time.

    ``provider_cycles`` (optional, fail-open): model -> served cycle ISO stamp,
    threaded into the between-term freshest-coherent-cohort filter of
    ``_current_evidence_shape_from_values``. Omitting it is byte-identical to today.
    """

    try:
        decision_at = _to_utc(request.computed_at, field_name="computed_at").isoformat()
        carrier_cycle_dt = _to_utc(
            request.source_cycle_time, field_name="source_cycle_time"
        )
        carrier_cycle = carrier_cycle_dt.isoformat()
        # Same one-clock staleness law that governs posterior readiness
        # (replacement_source_cycle_max_age_hours) also bounds how old the ENS carrier row
        # may be to still call itself "current evidence" — an unbounded walk-back silently
        # launders a stale ENS cycle into decision-time current evidence.
        min_evidence_cycle = (
            carrier_cycle_dt
            - timedelta(hours=replacement_source_cycle_max_age_hours())
        ).isoformat()
        params = (
            request.city,
            _date_text(request.target_date),
            metric,
            carrier_cycle,
            min_evidence_cycle,
            decision_at,
        )
        query = """
            SELECT snapshot_id, members_json,
                   COALESCE(source_cycle_time, issue_time) AS evidence_cycle,
                   COALESCE(source_available_at, available_at) AS evidence_available_at,
                   members_unit
            FROM ensemble_snapshots
            WHERE {city_predicate}
              AND target_date = ?
              AND temperature_metric = ?
              AND source_id = 'ecmwf_open_data'
              AND model_version = 'ecmwf_ens'
              AND authority = 'VERIFIED'
              AND causality_status = 'OK'
              AND boundary_ambiguous = 0
              AND forecast_window_attribution_status = 'FULLY_INSIDE_TARGET_LOCAL_DAY'
              AND contributes_to_target_extrema = 1
              AND COALESCE(source_cycle_time, issue_time) <= ?
              AND COALESCE(source_cycle_time, issue_time) >= ?
              AND COALESCE(source_available_at, available_at) <= ?
            ORDER BY COALESCE(source_cycle_time, issue_time) DESC,
                     COALESCE(source_available_at, available_at) DESC,
                     snapshot_id DESC
            LIMIT 1
            """
        row = conn.execute(
            query.format(city_predicate="city = ?"),
            params,
        ).fetchone()
        if row is None:
            # ``lower(city)`` disables the live composite city index on the
            # multi-million-row snapshot table. Canonical identity uses the exact
            # seek; retain case-insensitive compatibility only after a miss.
            row = conn.execute(
                query.format(city_predicate="lower(city) = lower(?)"),
                params,
            ).fetchone()
        if row is None:
            return None
        # Boundary-quarantined members are persisted as null (leakage law: their boundary
        # value must never enter extrema) even on snapshots where the majority rule already
        # allows contributes_to_target_extrema=1. Skip nulls rather than let float(None)
        # raise — the existing `len(members) < 20` floor in
        # _current_evidence_shape_from_values is the correct fail-closed gate on the
        # resulting (possibly reduced) member count, not a blanket exception swallow.
        values = tuple(
            float(value) for value in json.loads(row[1]) if value is not None
        )
        members_unit = str(row[4] or "").strip().lower()
        if members_unit in {"degf", "f", "°f"}:
            values = tuple((value - 32.0) * 5.0 / 9.0 for value in values)
        elif members_unit not in {"degc", "c", "°c"}:
            return None
        # Fitted shape-age variance slope (consult P2-B): only bites on the transported
        # branch inside _current_evidence_shape_from_values. FAIL-OPEN: artifact absent
        # / import failure -> 0.0 -> byte-identical serving.
        try:
            from src.forecast.shape_age_sigma import gamma_for as _shape_age_gamma_for  # noqa: PLC0415

            shape_age_gamma = float(_shape_age_gamma_for(metric))
        except Exception:
            shape_age_gamma = 0.0
        return _current_evidence_shape_from_values(
            snapshot_id=int(row[0]),
            source_cycle_time=str(row[2]),
            source_available_at=str(row[3]),
            members_c=values,
            provider_values_c=provider_values_c,
            provider_weights=provider_weights,
            center_c=center_c,
            carrier_cycle_time=carrier_cycle,
            provider_cycles=provider_cycles,
            shape_age_gamma_c2_per_6h=shape_age_gamma,
        )
    except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError):
        return None


def served_predictive_sigma_c(sigma_realized_c: float, *, floor_c: float = 1.0) -> float:
    """Served POINT predictive width = realized walk-forward fused-center error, floored.

    Authority: frontier consult REQ-20260629-131502 + src/forecast/sigma_authority.py.
    ``sigma_realized_c`` is the walk-forward fused-center residual std -- the realized total forecast
    error of the served center -- and IS the point predictive sigma that feeds
    ``bin_probability_settlement``. The center posterior sd (fused.sd) is NOT added here: adding it
    double-counts center uncertainty on top of an already-complete realized error (served sigma ~3.0
    vs realized RMSE ~1.35; PIT mound chi2=218; 50%CI covers 82%). fused.sd is carried separately as
    ``anchor_sigma_c`` into the q_lcb/q_ucb center-uncertainty bootstrap, where it belongs. A
    non-finite / non-positive realized width falls back to the floor (defensive;
    ``bin_probability_settlement`` rejects a non-positive sigma).
    """
    try:
        s = float(sigma_realized_c)
    except (TypeError, ValueError):
        return float(floor_c)
    if not math.isfinite(s) or s <= 0.0:
        return float(floor_c)
    return max(float(floor_c), s)


@dataclass(frozen=True)
class _PosteriorComputeResult:
    """The pure (no-DB-write) product of the posterior compute.

    Contract: this struct is returned by ``_compute_posterior_payload`` ALWAYS
    (even when not live-eligible), and every field below is consumed by the
    live write path's INSERT, identity hash, or provenance payload.
    """

    live_eligible: bool
    # Point distribution + the certified bootstrap band (may be None when not live).
    q: dict[str, float]
    q_lcb_map: dict[str, float] | None
    q_ucb_map: dict[str, float] | None
    # The fused center (mu*) and predictive spread. None when fusion evidence
    # is missing and materialization is blocked.
    mu_star: float | None
    predictive_sigma_c: float | None
    # K3 provider completeness for provenance.
    decorrelated_providers_complete: bool
    decorrelated_providers_served: int
    decorrelated_providers_expected: int
    capture_status: str
    replacement_q_mode: str
    # The remaining values the INSERT + identity hash + provenance payload consume.
    data_version: str
    source_cycle_time: str
    available_at: str
    computed_at: str
    runtime_layer: str | None
    dependency_payload: dict[str, object]
    dependency_hash: str
    bin_topology_hash: str
    posterior_config_hash: str
    family_id: str
    provenance_payload: dict[str, object] | None


def _posterior_block_sub_reason_codes(result: "_PosteriorComputeResult") -> tuple[str, ...]:
    """Typed sub-reasons for a not-``live_eligible`` posterior compute.

    2026-07-13/14 incident: every failed materialization surfaced ONLY the
    catch-all ``REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET`` — 277 receipts
    across ~30h, none saying WHICH requirement failed (the real cause lived in a
    subprocess ``logging.warning`` no operator saw). These sub-codes derive
    exclusively from fields the compute ALREADY carries on ``_PosteriorComputeResult``
    (no new computation, no new DB read) so the queue receipt sidecar picks them up
    for free via ``reason_codes``.
    """
    codes: list[str] = [f"Q_MODE:{result.replacement_q_mode}"]
    codes.append(f"CAPTURE:{result.capture_status}")
    if result.predictive_sigma_c is None:
        codes.append("PREDICTIVE_SIGMA:MISSING")
    if result.q_lcb_map is None:
        codes.append("Q_LCB:MISSING")
    if result.q_ucb_map is None:
        codes.append("Q_UCB:MISSING")
    return tuple(codes)


def _read_persisted_current_capture(
    conn: "sqlite3.Connection",
    *,
    city: str,
    metric: str,
    target_date: str,
    lead_days: int,
    source_cycle_time_iso: str,
) -> dict[str, tuple[float, int]]:
    """BLOCKER 5 — read the PERSISTED current rows for this cycle ({model: (value_c, rid)}).

    SHAPE ADAPTER ONLY (Task #32 follow-up, 2026-06-11): the serving RULE — which endpoint
    serves each model's current value — lives in the SINGLE authority
    ``replacement_current_value_serving.read_current_instrument_values`` (registry member #10).
    The old gem_global-only previous_runs exception (edc598b440) is now one instance of the
    generalized 没有新的就用老的 rule: a provider absent from single_runs at the selected cycle
    (JMA at every 06Z-cadence cycle — it publishes 00/12Z only; gfs during an HTTP-400 outage)
    serves its previous_runs row at the SAME natural key, BRANDED served_via="previous_runs" in
    the fusion provenance, instead of being dropped. The substituted value is the SAME physical
    product the model's walk-forward de-bias history is fit on, so the lead-bucket residual
    variance already prices the older run — no manual down-weighting anywhere.

    LEAD_DAYS IS NOT A FILTER (2026-06-09 fix, preserved in the authority): the natural key
    (city, metric, target_date, source_cycle_time) uniquely identifies the forecast; lead_days
    is retained as a parameter for call-site compatibility only. Fail-soft: any DB error ->
    empty dict (missing capture; the caller falls back with a logged reason).
    """
    del lead_days  # not a filter (2026-06-09); kept for call-site/test compatibility
    from src.data.replacement_current_value_serving import (  # noqa: PLC0415
        read_current_instrument_values,
    )

    served = read_current_instrument_values(
        conn, city=city, metric=metric, target_date=target_date,
        source_cycle_time_iso=source_cycle_time_iso,
    )
    return {m: (s.value_c, s.raw_model_forecast_id) for m, s in served.items()}


def _bayes_precision_fusion_city_local_lead_days(
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


def _bayes_precision_fusion_lead_bucket(lead_days: int) -> str:
    """F6 lead_bucket for the fused EMOS cell. Regional expert is lead<=1; group leads."""
    if lead_days <= 1:
        return "L1"
    if lead_days <= 3:
        return "L2_3"
    return "L4P"


def _replacement_bayes_precision_fusion_override(
    request: "ReplacementForecastMaterializeRequest",
    *,
    metric: str,
    anchor_value_corrected_c: float,
    conn: "sqlite3.Connection | None" = None,
) -> _BayesPrecisionFusionFusionOverride | None:
    """Current BAYES_PRECISION_FUSION-Bayes multi-model fusion.

    Returns the fused (anchor_value_c, anchor_sigma_c) that REPLACE the single OM9 9km anchor
    center/spread in the soft-anchor construction when at least one decorrelated extra survives
    current-input validation. The fusion itself is the ported
    proof C1 (src/forecast/bayes_precision_fusion.py — no parallel fusion).

    LAYERING (BAYES_PRECISION_FUSION_SPEC.md §6 integration): the override is computed from the ALREADY
    EB-bias-corrected anchor center (so it composes AFTER the EB bias layer); it replaces only
    the anchor center/spread. Any missing current input, history, or source-clock shape returns
    ``None`` so the caller records a non-live result rather than serving another live regime.
    """
    try:
        from src.config import runtime_cities_by_name  # noqa: PLC0415

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
        lead_days = _bayes_precision_fusion_city_local_lead_days(
            computed_at=computed_at, target_local_date=target_local_date, tz_name=tz_name
        )

        from src.data.bayes_precision_fusion_capture import capture_bayes_precision_instruments  # noqa: PLC0415
        from src.forecast.bayes_precision_fusion import fuse_bayes_precision_posterior  # noqa: PLC0415

        # Optional injected seams (live wiring / tests). An explicitly-assigned
        # _history_provider attribute wins (tests inject a fixture). When none is assigned AND
        # the materialization connection is available, the LIVE default is the real walk-forward
        # history provider reading the PERSISTED previous-runs raw_model_forecasts JOINed to
        # VERIFIED settlement on the SAME zeus-forecasts.db connection (intra-DB, INV-37; no-leak
        # target_date<decision, IRON RULE #3). This assignment is THE switch that lets
        # fuse_bayes_precision_posterior reach T2_BAYES once n_train>=MIN_TRAIN. The provider
        # never raises, but an empty result is fail-closed below.
        history_provider = getattr(_replacement_bayes_precision_fusion_override, "_history_provider", None)
        if history_provider is None and conn is not None:
            from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider  # noqa: PLC0415

            history_provider = BayesPrecisionFusionHistoryProvider(conn)

        # BLOCKER 5: the CURRENT values feeding the traded q come from the PERSISTED single_runs
        # rows the download job wrote — NEVER a network fetch inside the q path. Read them by
        # (city, metric, target_date, lead, source_cycle_time) on the SAME connection so the q is
        # reconstructable to the exact persisted inputs. If the current capture is MISSING (the
        # download did not run / failed), block materialization with a logged reason — never
        # silently network-fetch or substitute a single-anchor posterior.
        source_cycle_iso = _to_utc(
            request.source_cycle_time, field_name="source_cycle_time"
        ).isoformat()
        # SINGLE-AUTHORITY current-value serving (Task #32 follow-up): the rich serving map
        # carries per-instrument served_via/served_cycle/age provenance (brand law — a
        # previous_runs substitution is recorded, never silent); persisted_current is its
        # (value, rid) view for the fetch seam below.
        from src.data.replacement_current_value_serving import (  # noqa: PLC0415
            read_current_instrument_values,
        )

        served_current: dict[str, object] = {}
        persisted_current: dict[str, tuple[float, int]] = {}
        if conn is not None:
            served_current = read_current_instrument_values(
                conn, city=request.city, metric=metric, target_date=target_date,
                source_cycle_time_iso=source_cycle_iso,
                # ADD-DATA (operator "加数据"): include station-calibrated sources (cwa_*/hko_*) at
                # their OWN provider cycle so they enter persisted_current -> the precision fusion
                # weights them at initial precision (raw_second_moment_weights) and the frozen-scheme
                # skip (_station_live_omitted below) serves that live fusion center.
                include_station_sources=True,
            )
            persisted_current = {
                m: (s.value_c, s.raw_model_forecast_id) for m, s in served_current.items()
            }

        # An explicitly-assigned _live_fetch is honored ONLY as a per-model override seam for
        # models WITHOUT a persisted current row (legacy/test injection). It is never consulted
        # when the persisted row exists. It does NOT defeat the missing-capture gate: when the
        # persisted capture is entirely absent the q path serves the current OM9 anchor through
        # the same settlement-preimage Normal + bootstrap-q_lcb carrier, because B5 forbids
        # building the traded q from any non-persisted network value.
        injected_live_fetch = getattr(_replacement_bayes_precision_fusion_override, "_live_fetch", None)

        if conn is not None and not persisted_current:
            # Missing current capture on the live path -> explicit block + logged reason.
            # NEVER a network fetch in the q path (the persisted download is the sole q source),
            # and no anchor-only surrogate is written as a live posterior.
            import logging  # noqa: PLC0415
            logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                "replacement_0_1 BAYES_PRECISION_FUSION fusion: persisted current single_runs capture MISSING for "
                "%s %s %s lead=%s cycle=%s -> live posterior blocked "
                "(no network fetch and no anchor-only live surrogate in q path)",
                request.city, metric, target_date, lead_days, source_cycle_iso,
            )
            return None

        # ARRIVAL GUARD inputs (C1-AVAIL-CLOCK, 2026-06-16): the honest per-model availability is
        # PROOF OF POSSESSION = the served row's captured_at, routed through the canonical producer
        # (no nominal — captured_at is the real possession wall-clock). Models with no served row are
        # absent from the map -> the capture's guard fail-OPENs (admits) them. decision_utc is the
        # materialization decision instant (computed_at). Expected to exclude ~0 in
        # production (extras' captured_at lands hours after the cycle, before any decision).
        model_available_at: dict[str, str] = {}
        for _m, _served in served_current.items():
            _captured = getattr(_served, "captured_at", None)
            if _captured:
                try:
                    model_available_at[_m] = proof_of_possession_available_at(_captured)
                except Exception:
                    # Unparseable capture stamp -> omit (fail-OPEN: the guard admits the model).
                    pass

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

        capture = capture_bayes_precision_instruments(
            city=request.city, metric=metric, latitude=lat, longitude=lon,
            timezone_name=tz_name,
            run=_to_utc(request.source_cycle_time, field_name="source_cycle_time"),
            target_local_date=target_local_date, lead_days=lead_days,
            anchor_z_corrected=float(anchor_value_corrected_c),
            history_provider=history_provider, live_fetch=_persisted_then_injected_fetch,
            decision_utc=computed_at,
            model_available_at=model_available_at,
        )
        if not capture.has_history:
            import logging  # noqa: PLC0415

            logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                "replacement_0_1 BAYES_PRECISION_FUSION history MISSING for %s %s %s -> "
                "live posterior blocked (no alternate authority)",
                request.city, metric, target_date,
            )
            return None
        if not capture.has_extras:
            # K3 ANTIBODY (2026-06-09): all multi-model extras absent. This is a wiring failure
            # (for example, a lead-calendar mismatch), not permission to revive the old anchor path.
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 BAYES_PRECISION_FUSION fusion has ZERO multi-model extras -> "
                    "live posterior blocked for %s %s %s cycle=%s. Check the single_runs capture "
                    "+ natural-key match.", request.city, metric, target_date,
                    _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat(),
                )
            except Exception:
                pass
            return None

        fused = fuse_bayes_precision_posterior(
            anchor_z=capture.anchor_z, anchor_tau0=capture.anchor_tau0,
            likelihood=capture.likelihood, disagree_var=capture.disagree_var,
            use_covariance=True,
        )

        # METHOD UNIFY 2026-06-18: replace T2 BLUE center (fused.mu) with the RAW diagonal
        # weighted mean — the SAME formula walk_forward_model_weights uses in the spine ENTRY.
        # Closes the #135 two-center split (RAW-entry vs T2-BLUE-exit) so forecast_posteriors
        # center == spine entry center for the same inputs.  Width (fused.sd) is UNCHANGED.
        #
        # Algorithm (mirrors walk_forward_model_weights exactly, via raw_second_moment_weights):
        #  1. For each instrument in capture.likelihood, compute raw_m2 = mean(r²) from
        #     train_residuals (in degC; bias² included — NO demeaning).
        #  2. Include the anchor as a regular member with its own raw_m2 (from
        #     capture.anchor_raw_m2_native).
        #  3. Compute weights via the shared raw_second_moment_weights helper (same unit
        #     scaling, same EB-shrink, same equal-weight fallback as the spine).
        #  4. mu_diagonal = Σ_m w_m · z_m, where z_m = ins.z (already RAW via _raw_instrument).
        from src.forecast.center import raw_precision_center as _raw_center  # noqa: PLC0415
        from src.forecast.model_selection import ANCHOR_MODEL as _ANCHOR  # noqa: PLC0415

        _raw_m2_and_n: dict[str, tuple[float | None, int]] = {}
        _z_by_model: dict[str, float] = {}
        for _ins in capture.likelihood:
            if _ins.train_residuals:
                _m2 = sum(r * r for r in _ins.train_residuals) / len(_ins.train_residuals)
                _raw_m2_and_n[_ins.model] = (_m2, _ins.n_train)
            else:
                _raw_m2_and_n[_ins.model] = (None, 0)
            _z_by_model[_ins.model] = float(_ins.z)
        _station_entry_models_added: tuple[str, ...] = ()
        try:
            from src.data.forecast_source_registry import (  # noqa: PLC0415
                SOURCES as _FORECAST_SOURCES,
                source_allows_role as _source_allows_role,
            )

            _added: list[str] = []
            for _m, (_value, _rid) in persisted_current.items():
                _m_str = str(_m)
                if _m_str in _z_by_model or not _m_str.startswith(("cwa_", "hko_")):
                    continue
                _spec = _FORECAST_SOURCES.get(_m_str)
                if _spec is None or not _source_allows_role(_spec, "entry_primary"):
                    continue
                try:
                    _z_by_model[_m_str] = float(_value)
                except (TypeError, ValueError):
                    continue
                # Station-calibrated source raw_m2 from its OWN walk-forward residual history — the
                # provider reads positive-lead single_runs for the named sources with no previous-runs
                # archive (Day0 stays cold-start unless time-aligned history becomes available), so the source
                # enters the precision center at its MEASURED precision (raw mean(r²), degC², the same
                # convention as the gridded instruments; downstream raw_second_moment_weights EB-shrinks
                # a thin history toward the equal prior). NEVER a flat equal weight: equal-weight would
                # dilute the settlement-station official forecast — structurally the most informative
                # source for its own city — down to a gridded member's level. No history yet -> (None,0)
                # cold-start (the shrink handles it), so a brand-new source is used immediately, never
                # gated on accumulating N days.
                _st_m2: float | None = None
                _st_n = 0
                if history_provider is not None:
                    try:
                        _sh = history_provider(
                            city=request.city, metric=metric, lead_days=lead_days,
                            target_date=target_local_date, models=[_m_str],
                        ).get(_m_str)
                        if _sh is not None and _sh.forecast_values:
                            _errs = [
                                (f - s)
                                for f, s in zip(_sh.forecast_values, _sh.settlement_values)
                            ]
                            if _errs:
                                _st_m2 = sum(e * e for e in _errs) / len(_errs)
                                _st_n = len(_errs)
                    except Exception:
                        _st_m2, _st_n = None, 0
                _raw_m2_and_n[_m_str] = (_st_m2, _st_n)
                _added.append(_m_str)
            _station_entry_models_added = tuple(sorted(_added))
        except Exception:
            _station_entry_models_added = ()
        # Include anchor as a MEMBER (not a separate Bayesian prior).
        if capture.anchor_z is not None:
            _raw_m2_and_n[_ANCHOR] = (capture.anchor_raw_m2_native, capture.anchor_raw_n_train)
            _z_by_model[_ANCHOR] = float(capture.anchor_z)
        # FITTED STALENESS VARIANCE (consult v2 (b), 2026-07-17): a member whose CURRENT
        # value is served from a cycle OLDER than the decision's selected cycle carries
        # measured extra error variance v_m(cycle-lag) — fitted walk-forward per
        # (model, metric, lead-bucket) by scripts/fit_model_staleness_variance.py. The lag
        # is CYCLE-lag (selected cycle − served row's cycle), NOT capture-lag
        # (ServedInstrumentValue.age_hours = captured_at − own cycle): the train_residuals
        # that price this member were queried at the decision lead, i.e. they assume the
        # selected cycle; a promptly-captured stale cycle has near-zero capture-lag but the
        # full issuance lag of unpriced error. v is degC² (train residuals are degC even for
        # F-cities — _serving_unit only scales the floor/shrink target), added to raw_m2
        # BEFORE the EB shrink/floor so thin histories keep their prior damping. Stale
        # members are DOWNWEIGHTED, never excluded (E4: exclusion measured +0.152C worse).
        # FAIL-OPEN: artifact absent / model unknown / anchor / None-m2 member / any error
        # -> v absent -> `_center_m2_and_n is _raw_m2_and_n` -> byte-identical weights.
        # The anchor is excluded because its z is the request-cycle OM9 anchor value, not a
        # served_current row — it has no cycle-lag by construction.
        _staleness_v_by_model: dict[str, float] = {}
        try:
            from src.forecast.staleness_variance import v_for as _staleness_v_for  # noqa: PLC0415

            _sel_cycle_dt = _to_utc(request.source_cycle_time, field_name="source_cycle_time")
            for _m, (_rm2, _rn) in _raw_m2_and_n.items():
                if _m == _ANCHOR or _rm2 is None:
                    continue
                _served = served_current.get(str(_m))
                if _served is None:
                    continue
                try:
                    _served_cycle_dt = _to_utc(
                        str(_served.served_cycle), field_name="served_cycle"  # type: ignore[union-attr]
                    )
                    _lag_h = (_sel_cycle_dt - _served_cycle_dt).total_seconds() / 3600.0
                except Exception:
                    continue
                _v = float(_staleness_v_for(str(_m), metric, _lag_h))
                if math.isfinite(_v) and _v > 0.0:
                    _staleness_v_by_model[str(_m)] = _v
        except Exception:
            _staleness_v_by_model = {}
        _center_m2_and_n = _raw_m2_and_n
        if _staleness_v_by_model:
            _center_m2_and_n = {
                _m: (
                    (_rm2 + _staleness_v_by_model[_m], _rn)
                    if _m in _staleness_v_by_model and _rm2 is not None
                    else (_rm2, _rn)
                )
                for _m, (_rm2, _rn) in _raw_m2_and_n.items()
            }
        # Serving unit (F-city vs C-city): read from the request bins (the degC residuals stored
        # in train_residuals are always in degC so the unit scaling only affects the floor/shrink
        # target — matching the spine's _u logic and the operator's f06d2176bc fix).
        _serving_unit = _city_settlement_unit_from_bins(request)
        # Option C (2026-06-21): per-model grid-representativeness variance sigma_repr²
        # (degC², from the persisted native-cell d_eff/delta_z table) threaded into the
        # RAW precision denominator that produces the served center. train_residuals are
        # degC, so _raw_m2_and_n here is degC²; repr must be supplied in the SAME degC²
        # basis (the helper does no scaling). The loader is fail-soft: a city/model absent
        # from config/grid_representativeness.json yields 0.0 -> byte-identical center for
        # that member (no fabricated penalty, no flag). Enters the MEAN weights ONLY —
        # predictive_sigma_c / anchor_sigma_c (fused.sd) are UNTOUCHED (no Kelly double-
        # count). This is LIVE-DIRECT: the warming is active wherever the table has a cell;
        # rollout is controlled by populating config/grid_representativeness.json + the
        # deploy commit, never by a dormant code flag (operator no-blocked law).
        _sigma_repr_by_model = _build_sigma_repr_by_model(
            request.city, list(_raw_m2_and_n.keys()), anchor_model=_ANCHOR
        )
        _weights, _mu_from_center = _raw_center(
            _center_m2_and_n, _z_by_model, unit=_serving_unit,
            repr_m2_by_model=_sigma_repr_by_model,
        )
        if _weights and _z_by_model:
            _mu_diagonal = float(_mu_from_center)
        elif _z_by_model:
            # No precision signal at all → equal-weight RAW mean (pure RAW, never T2 BLUE).
            _mu_diagonal = float(sum(_z_by_model.values()) / len(_z_by_model))
        else:
            # No instruments: unreachable after the has_extras guard above; use anchor RAW value.
            _mu_diagonal = float(anchor_value_corrected_c)

        # Option C provenance (§7): per-model RAW-precision basis so the served center is
        # reconstructible — raw_m2 (degC²), n, repr_m2 (degC²), and the final weight. The
        # effective denom is reconstructable from (raw_m2, n, repr_m2, unit); we persist the
        # weight directly (the one served quantity) + a hash over the basis. When the grid
        # table is absent, every repr_m2 is 0.0 and this is the same basis as before Option C.
        _precision_center_basis: dict[str, dict[str, float]] = {}
        for _m, (_rm2, _rn) in _raw_m2_and_n.items():
            _precision_center_basis[str(_m)] = {
                "raw_m2": (float("nan") if _rm2 is None else float(_rm2)),
                "n": float(int(_rn)),
                "repr_m2": float(_sigma_repr_by_model.get(str(_m), 0.0)),
                "weight": float(_weights.get(str(_m), 0.0)),
            }
            # Fitted staleness variance provenance (degC², added to raw_m2 in the center
            # denominator). Key present ONLY when the inflation actually fired for this
            # model, so the basis payload and hash stay byte-identical whenever the
            # artifact is absent or every member is cycle-fresh (fail-open invariant:
            # posterior_config_hash consumes this hash and must not churn on a no-op).
            if str(_m) in _staleness_v_by_model:
                _precision_center_basis[str(_m)]["staleness_m2"] = float(
                    _staleness_v_by_model[str(_m)]
                )
        _precision_basis_hash = _json_hash(
            {
                "unit": _serving_unit,
                "basis": {
                    k: [v["raw_m2"], v["n"], v["repr_m2"], v["weight"]]
                    + ([v["staleness_m2"]] if "staleness_m2" in v else [])
                    for k, v in sorted(_precision_center_basis.items())
                },
            }
        )

        # Low-n prior weighting provenance: models that entered the center through
        # EB shrink-to-equal because n < MIN_SETTLED_N. They are not excluded.
        from src.forecast.center import MIN_SETTLED_N as _MIN_SETTLED_N  # noqa: PLC0415
        _low_n_prior_weighted: tuple[str, ...] = tuple(
            sorted(
                str(_m)
                for _m, _v in _precision_center_basis.items()
                if int(_v["n"]) < _MIN_SETTLED_N
            )
        )

        _source_clock_payload: dict[str, object] | None = None
        _source_clock_used_models: tuple[str, ...] | None = None
        _source_clock_center_sigma_c: float | None = None
        _source_clock_predictive_sigma_c: float | None = None
        _source_clock_current_shape: _CurrentEvidenceShape | None = None
        _source_clock_shape_required = True
        _station_live_omitted = False
        _source_clock_current_value_serving: dict[str, Mapping[str, object]] = {}
        _source_clock_dep_ids: set[int] = set()
        try:
            from src.strategy.live_inference.source_clock_city_weights import (  # noqa: PLC0415
                GRID_AWARE_ARTIFACT_NAME,
                fixed_weight_center_from_values,
                scheme_for_city,
            )

            _scheme = scheme_for_city(request.city, metric=metric)
            # ADD-DATA (operator directive 2026-06-28 "加数据不禁数据"): a station-calibrated
            # source (cwa_*/hko_* family) that is LIVE in the precision fusion but absent from the
            # frozen grid_aware scheme must be ADDED, never banned by the frozen snapshot. When such
            # a source is present, skip the frozen scheme and serve the live fusion center
            # (_mu_diagonal computed above), which already added that source at its initial precision
            # weight via raw_second_moment_weights. Targeted: only cities with a live station source
            # leave the scheme; every other city serves its scheme byte-identically.
            _station_live_omitted = _scheme is not None and any(
                str(_m).startswith(("cwa_", "hko_")) and str(_m) not in _scheme.weights
                for _m in persisted_current
            )
            if _scheme is not None and not _station_live_omitted:
                _source_values: dict[str, float] = {
                    _ANCHOR: float(anchor_value_corrected_c),
                }
                for _m, (_value, _rid) in persisted_current.items():
                    try:
                        _source_values[str(_m)] = float(_value)
                    except (TypeError, ValueError):
                        continue
                _entry_ineligible_sources = _registered_source_clock_entry_ineligible(
                    _scheme.final_sources
                )
                _source_clock_center = None
                if _entry_ineligible_sources:
                    try:
                        import logging  # noqa: PLC0415

                        logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                            "source-clock one-scheme center skipped for %s %s %s: registered "
                            "sources are not entry_primary eligible: %s",
                            request.city,
                            metric,
                            target_date,
                            list(_entry_ineligible_sources),
                        )
                    except Exception:
                        pass
                else:
                    _source_clock_center = fixed_weight_center_from_values(
                        city=request.city,
                        values_c_by_source=_source_values,
                        metric=metric,
                    )
                    if _source_clock_center is None:
                        try:
                            import logging  # noqa: PLC0415

                            logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                                "source-clock one-scheme center skipped for %s %s %s: present "
                                "configured weight below floor (>75%% of basket missing)",
                                request.city,
                                metric,
                                target_date,
                            )
                        except Exception:
                            pass
                    elif _source_clock_center.missing_sources:
                        try:
                            import logging  # noqa: PLC0415

                            logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                                "source-clock one-scheme center renormalized for %s %s %s: missing "
                                "configured sources %s",
                                request.city,
                                metric,
                                target_date,
                                list(_source_clock_center.missing_sources),
                            )
                        except Exception:
                            pass
                if _source_clock_center is not None:
                    _source_clock_used_models = tuple(_source_clock_center.used_weights)
                    _mu_diagonal = float(_source_clock_center.mu_c)
                    _weights = dict(_source_clock_center.used_weights)
                    for _m in _source_clock_used_models:
                        _z_by_model[str(_m)] = float(_source_values[str(_m)])
                        if _m not in _raw_m2_and_n:
                            _raw_m2_and_n[str(_m)] = (None, 0)
                    _precision_center_basis = {}
                    for _m in _source_clock_used_models:
                        _rm2, _rn = _raw_m2_and_n.get(str(_m), (None, 0))
                        _precision_center_basis[str(_m)] = {
                            "raw_m2": (float("nan") if _rm2 is None else float(_rm2)),
                            "n": float(int(_rn)),
                            "repr_m2": float(_sigma_repr_by_model.get(str(_m), 0.0)),
                            "weight": float(_weights.get(str(_m), 0.0)),
                        }
                    _precision_basis_hash = _json_hash(
                        {
                            "unit": _serving_unit,
                            "basis": {
                                k: [v["raw_m2"], v["n"], v["repr_m2"], v["weight"]]
                                for k, v in sorted(_precision_center_basis.items())
                            },
                            "source_clock_one_scheme": True,
                        }
                    )

                    # Source-clock live width is current target-specific evidence.
                    # Historical residual width was immediately overwritten below,
                    # so querying it again only added DB work to the alpha path.
                    for _m in _source_clock_used_models:
                        if _m in served_current:
                            _source_clock_current_value_serving[_m] = served_current[_m].as_provenance()  # type: ignore[union-attr]
                            try:
                                _source_clock_dep_ids.add(int(served_current[_m].raw_model_forecast_id))  # type: ignore[union-attr]
                            except Exception:
                                pass
                    _source_clock_payload = {
                        "artifact": GRID_AWARE_ARTIFACT_NAME,
                        "configured_sources": list(_scheme.final_sources),
                        "configured_weights": dict(_source_clock_center.configured_weights),
                        "used_weights": dict(_source_clock_center.used_weights),
                        "missing_sources": list(_source_clock_center.missing_sources),
                        "renormalized": bool(_source_clock_center.renormalized),
                        "one_scheme_status": _source_clock_center.one_scheme_status,
                        "walkforward_pass": bool(_source_clock_center.walkforward_pass),
                        "sample_n": int(_scheme.sample_n),
                        "center_sigma_c": _source_clock_center_sigma_c,
                        "predictive_sigma_c": _source_clock_predictive_sigma_c,
                    }
                    _source_clock_current_shape = _read_current_evidence_shape(
                        conn,
                        request,
                        metric=metric,
                        provider_values_c={
                            model: float(_source_values[model])
                            for model in _source_clock_used_models
                            if model in _source_values
                        },
                        provider_weights={
                            str(model): float(weight)
                            for model, weight in _weights.items()
                            if model in _source_clock_used_models
                        },
                        center_c=float(_mu_diagonal),
                        # Freshest-coherent-cohort between (consult v2 (b)): served
                        # cycle stamps for the cohort filter. A model without a served
                        # row (e.g. the anchor) is simply absent — fail-open included.
                        provider_cycles={
                            str(_m): str(served_current[_m].served_cycle)  # type: ignore[union-attr]
                            for _m in _source_clock_used_models
                            if _m in served_current
                        },
                    )
                    # The live source-clock route has one shape authority: current
                    # target-specific evidence.  A missing/invalid ENS carrier is
                    # represented by predictive_sigma=None and therefore cannot
                    # materialize a live posterior; historical residual/floor
                    # values above are retained solely for offline historical
                    # analysis and are not a live probability authority.
                    if _source_clock_current_shape is None:
                        _source_clock_predictive_sigma_c = None
                    else:
                        _source_clock_center_sigma_c = (
                            _source_clock_current_shape.center_sigma_c
                        )
                        _source_clock_predictive_sigma_c = (
                            _source_clock_current_shape.predictive_sigma_c
                        )
                    _source_clock_payload.update(
                        {
                            "center_sigma_c": _source_clock_center_sigma_c,
                            "predictive_sigma_c": _source_clock_predictive_sigma_c,
                            "probability_shape_basis": (
                                "decision_time_current_ensemble_within_plus_provider_between"
                            ),
                            "current_evidence_shape": (
                                None
                                if _source_clock_current_shape is None
                                else _source_clock_current_shape.as_payload()
                            ),
                        }
                    )
            else:
                # A station-augmented center intentionally leaves frozen one-scheme
                # weights; a city without such a scheme uses the current fusion weights.
                # Both remain source-clock routes: build their width from the same
                # current ENS + simultaneous provider values, never historical width.
                _source_clock_used_models = tuple(str(model) for model in _weights)
                _source_clock_current_shape = _read_current_evidence_shape(
                    conn,
                    request,
                    metric=metric,
                    provider_values_c={
                        str(model): float(_z_by_model[model])
                        for model in _weights
                        if model in _z_by_model
                    },
                    provider_weights={
                        str(model): float(weight)
                        for model, weight in _weights.items()
                    },
                    center_c=float(_mu_diagonal),
                    # Freshest-coherent-cohort between (consult v2 (b)); same fail-open
                    # threading as the one-scheme branch above.
                    provider_cycles={
                        str(_m): str(served_current[_m].served_cycle)  # type: ignore[union-attr]
                        for _m in _source_clock_used_models
                        if _m in served_current
                    },
                )
                if _source_clock_current_shape is not None:
                    _source_clock_center_sigma_c = (
                        _source_clock_current_shape.center_sigma_c
                    )
                    _source_clock_predictive_sigma_c = (
                        _source_clock_current_shape.predictive_sigma_c
                    )
                for _m in _source_clock_used_models:
                    if _m in served_current:
                        _source_clock_current_value_serving[_m] = (
                            served_current[_m].as_provenance()  # type: ignore[union-attr]
                        )
                        try:
                            _source_clock_dep_ids.add(
                                int(served_current[_m].raw_model_forecast_id)  # type: ignore[union-attr]
                            )
                        except Exception:
                            pass
        except Exception as _source_clock_exc:
            try:
                import logging  # noqa: PLC0415

                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "source-clock one-scheme center failed closed for %s %s %s: %s",
                    request.city,
                    metric,
                    target_date,
                    _source_clock_exc,
                )
            except Exception:
                pass
            # A source-clock evaluation error is evidence loss, not permission
            # to resurrect the historical residual/floor probability regime.
            # Returning no override leaves the materialized row explicitly
            # non-live (CAPTURE_MISSING) and preserves blocked-candidate
            # observability without creating an alternate tradeable q.
            return None

        if _source_clock_current_shape is None:
            try:
                import logging  # noqa: PLC0415

                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 BAYES_PRECISION_FUSION current ENS shape MISSING for %s %s %s -> "
                    "live posterior blocked (no historical-width substitute)",
                    request.city,
                    metric,
                    target_date,
                )
            except Exception:
                pass
            return None

        used_models = _source_clock_used_models or tuple(
            dict.fromkeys((*tuple(fused.used_models), *_station_entry_models_added))
        )
        # K3 ANTIBODY (2026-06-09): surface a STRUCTURALLY-incomplete decorrelated set LOUDLY. The
        # 4 declared decorrelated PROVIDERS are NOAA(gfs) / DWD-ICON(one of icon_d2|icon_eu|
        # icon_global) / CMC(gem) / JMA(jma). gem_global's single_runs is unavailable at 06z/18z
        # cycles (12h cadence) so the ensemble silently ran as 3 -> a permanently-unservable model
        # must never masquerade as a transient drop. Log expected-vs-served providers per cell.
        # SINGLE-AUTHORITY provider-family mapping (Task #32): the model->decorrelated-provider
        # family map lives in replacement_fusion_upgrade_trigger.DECORRELATED_PROVIDER_FAMILIES so
        # the fusion's served/missing determination and the upgrade trigger's served/capturable
        # comparison can never drift on what counts as a provider. 2026-06-09 promotion: families
        # are rep-based — NBM is the NCEP rep in-CONUS, the UKV 2km nest the UKMO rep in the UK —
        # so each family is served when ANY of its members is in used_models.
        from src.data.replacement_fusion_upgrade_trigger import (  # noqa: PLC0415
            DECORRELATED_PROVIDER_FAMILIES,
            decorrelated_provider_families_of,
            expected_provider_families_for_city,
        )

        _served_families = decorrelated_provider_families_of(set(used_models))
        _expected_families = expected_provider_families_for_city(lat, lon, lead_days)
        _missing_providers = [
            f"{fam}/{'|'.join(DECORRELATED_PROVIDER_FAMILIES[fam])}"
            for fam in sorted(_expected_families)
            if fam not in _served_families
        ]
        # FIX 1/FIX 5 (2026-06-09): the SINGLE K3 completeness verdict reused by the q-mode +
        # capture-status provenance. Expected providers are domain/lead-aware: nest-only families
        # that cannot serve this city/lead are not phantom requirements.
        # This is the ONLY provider-count determination — the q-mode FULL/PARTIAL split and the
        # FIX-5 capture_status both read it (no parallel re-derivation).
        _decorrelated_expected = len(_expected_families)
        _decorrelated_served = len(_served_families & _expected_families)
        _decorrelated_complete = not _missing_providers
        if _source_clock_payload is not None and _source_clock_used_models is not None:
            _decorrelated_expected = len(_source_clock_payload.get("configured_sources", []) or [])
            _decorrelated_served = len(_source_clock_used_models)
            _decorrelated_complete = not bool(_source_clock_payload.get("missing_sources"))
            _missing_providers = [
                str(source)
                for source in (_source_clock_payload.get("missing_sources", []) or [])
            ]
        if _missing_providers:
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 BAYES_PRECISION_FUSION fusion decorrelated-provider INCOMPLETE for %s %s: served "
                    "%d/%d, missing %s (expected=%s used=%s). A servable provider that is absent "
                    "must be resolved explicitly, not silently dropped.",
                    request.city, metric, _decorrelated_served, _decorrelated_expected,
                    _missing_providers, sorted(_expected_families), list(used_models),
                )
            except Exception:
                pass
        model_set_hash = _json_hash(
            {
                "models": sorted(used_models),
                "source_clock_weights": (
                    None
                    if _source_clock_payload is None
                    else _source_clock_payload.get("used_weights")
                ),
            }
        )
        # resolution_mix_hash captures which native grid resolutions entered the fused product
        # (anchor 0.1, globals ~0.25/seamless, regional 2km). Keyed by the deduped model set.
        resolution_mix_hash = _json_hash(
            {
                "models": sorted(used_models),
                "regional": sorted(fused.regional_models),
                "source_clock_one_scheme": _source_clock_payload is not None,
            }
        )

        # BLOCKER 5: the raw_model_forecast_ids this q was fused from = the persisted current
        # single_runs rows consumed for the extras PLUS the persisted anchor current row (the
        # anchor center, though passed as anchor_z_corrected, is the persisted anchor product).
        # Sorted + de-duped for a deterministic provenance list.
        dep_ids = set(consumed_ids)
        # _ANCHOR already imported above in the METHOD UNIFY block.
        anchor_row = persisted_current.get(_ANCHOR)
        if anchor_row is not None:
            dep_ids.add(int(anchor_row[1]))
        dep_ids.update(_source_clock_dep_ids)
        raw_model_forecast_ids = tuple(sorted(dep_ids))

        # BLOCKER 3: declare the ifs025->ifs9 anchor bridge provenance (applied when the anchor
        # history product is the 0.25 feed, which is the only ECMWF previous-runs OM serves).
        from src.data.bayes_precision_fusion_capture import (  # noqa: PLC0415
            OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME,
        )
        from src.forecast.bayes_precision_fusion_anchor_bridge import bridge_metadata  # noqa: PLC0415
        anchor_bridge = bridge_metadata(
            stored_model_name=OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME
        )

        # FUSED-Q PREDICTIVE SIGMA (2026-06-09): sigma for the settlement VALUE, not the mean.
        # fused.sd is the posterior sd of mu* (V* + widenings) — far too tight as a predictive
        # spread (the fused-N experiment's tight-sigma caveat). The irreducible part is
        # measured from the walk-forward FUSED-CENTER residual series: per common target_date,
        # the mean of the instruments' de-biased residuals; its std IS the historical error of
        # the served fused center at this cell. sigma_pred = max(1.0C, sigma_resid) because the
        # realized residual already contains total point-forecast error. fused.sd is center
        # uncertainty and is carried separately as anchor_sigma_c into the q_lcb/q_ucb bootstrap.
        # Thin substrate (<5 common dates) -> conservative default sigma_resid = 1.5C.
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
        # DOUBLE-COUNT FIX (consult REQ-20260629-131502 + sigma_authority.py): the served POINT width
        # is the realized walk-forward fused-center error ALONE -- NOT sqrt(fused.sd^2 + sigma_resid^2),
        # which adds the center posterior sd on top of an already-complete realized error (served ~3.0
        # vs realized ~1.35). fused.sd is carried separately as anchor_sigma_c (below) into the
        # q_lcb/q_ucb center-uncertainty bootstrap, where center uncertainty belongs.
        predictive_sigma_c: float | None = served_predictive_sigma_c(
            _sigma_resid, floor_c=1.0
        )
        if _source_clock_shape_required:
            predictive_sigma_c = (
                None
                if _source_clock_predictive_sigma_c is None
                else float(_source_clock_predictive_sigma_c)
            )

        # Task #32 follow-up (brand law): per-instrument serving provenance for the FUSED set.
        # served_current is the single-authority serving map (read_current_instrument_values);
        # restricting to used_models keeps the record scoped to what actually entered the q. A
        # previous_runs substitution surfaces here as served_via="previous_runs" — never silent.
        _current_value_serving = (
            _source_clock_current_value_serving
            if _source_clock_current_value_serving
            else {
                m: served_current[m].as_provenance()  # type: ignore[union-attr]
                for m in used_models
                if m in served_current
            }
        ) or None

        return _BayesPrecisionFusionFusionOverride(
            anchor_value_c=_mu_diagonal,
            anchor_sigma_c=float(_source_clock_center_sigma_c if _source_clock_center_sigma_c is not None else fused.sd),
            method="SOURCE_CLOCK_FIXED_WEIGHT" if _source_clock_payload is not None else fused.method,
            used_models=used_models,
            model_set_hash=model_set_hash,
            resolution_mix_hash=resolution_mix_hash,
            lead_bucket=_bayes_precision_fusion_lead_bucket(lead_days),
            dropped_models=capture.dropped_models,
            excluded_regionals=capture.selection.excluded_regionals,
            dropped_aliases=capture.selection.dropped_aliases,
            raw_model_forecast_ids=raw_model_forecast_ids,
            anchor_bridge=anchor_bridge,
            predictive_sigma_c=predictive_sigma_c,
            decorrelated_providers_complete=_decorrelated_complete,
            decorrelated_providers_served=_decorrelated_served,
            decorrelated_providers_expected=_decorrelated_expected,
            current_value_serving=_current_value_serving,
            precision_center_basis=_precision_center_basis or None,
            precision_basis_hash=_precision_basis_hash,
            low_n_prior_weighted_models=_low_n_prior_weighted,
            source_clock_one_scheme=_source_clock_payload,
            current_evidence_shape=(
                None
                if _source_clock_current_shape is None
                else _source_clock_current_shape.as_payload()
            ),
            current_evidence_members_c=(
                None
                if _source_clock_current_shape is None
                else _source_clock_current_shape.members_c
            ),
        )
    except Exception as exc:  # fail-soft: never break blocked-candidate materialization
        try:
            import logging  # noqa: PLC0415
            logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                "replacement_0_1 BAYES_PRECISION_FUSION fusion wiring skipped (fail-soft): %s", exc
            )
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Q_LCB / Q_UCB MATERIALIZATION (2026-06-09) — fused-center parameter-uncertainty bootstrap.
#
# Created: 2026-06-09
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §1d-§1e (fused-N-direct q,
#   σ_pred = max(1.0C, σ_resid)); root-cause /tmp/candidate_missing_rootcause.md (the
#   live LCB falls back to legacy bounds when q_lcb_json is NULL -> under-certifies
#   below ask → every proof killed). This builds a REAL per-bin q_lcb/q_ucb consistent with the fused
#   posterior so the bundle q_lcb takes priority over the Wilson fallback (no downstream change).
#
# DESIGN (principled, not a fudge):
#   The fused posterior gives center μ* with posterior sd = fused.sd (anchor_sigma_c — the CENTER
#   uncertainty) and predictive spread σ_pred (predictive_sigma_c = max(1.0C, σ_resid)). The
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

# The global lower-CVaR selector requires at least 20 observations in the 5%
# tail. 400 coherent simplex draws are therefore the minimum certifiable carrier.
_QLCB_BOOTSTRAP_DRAWS = 400
# SINGLE AUTHORITY: the certified bootstrap basis string lives in cycle_policy (shared with the
# tradeable-grade coverage predicate the mask-and-starve antibody sites use). Re-exported as
# _QLCB_BASIS for the in-module call sites + existing tests that import it by this name.
_QLCB_BASIS = TRADEABLE_GRADE_QLCB_BASIS
_QLCB_SEED = 0x5EED_F09  # deterministic per-posterior rng (provenance-stable bounds)

# ---------------------------------------------------------------------------
# FAR-TAIL q_lcb HONESTY (2026-06-22) — forward-validated monotone lower-bound calibration.
#
# Authority: docs/evidence/live_order_pathology/2026-06-22_qlcb_lowerbound_honesty.md
# Forward-validated by qlcbHonest analysis:
#   - Far-tail YES bins (served q_point < ~0.05) have q_lcb ~0.07-0.10 in raw bootstrap
#     but realize ~0.006 frequency → the bootstrap centre-uncertainty draws place them as
#     too probable. Actual far-tail realized frequency is stationary near-zero across 3 dates.
#   - Flooring q_lcb at FAR_TAIL_LCB_FLOOR (0.003) = the realized far-tail frequency makes
#     far-tail YES bins self-reject at typical fill prices (~0.01): edge = 0.003 - 0.01 < 0.
#   - Kills 191 give-away admissions (188 losers / 3 winners), log-loss −0.22%, zero
#     winning-bin q_point blowup (q_point UNTOUCHED).
#   - Shoulder / mode / buy_no path: IDENTITY (byte-identical to prior behavior).
#
# IMPLEMENTATION: in _build_fused_q_bounds, after the per-bin bootstrap p5 quantile,
# for any bin where q_point[bin] < FAR_TAIL_Q_POINT_THRESH apply:
#   q_lcb[bin] = min(q_lcb[bin], FAR_TAIL_LCB_FLOOR)
# This is a monotone CEILING on q_lcb (can only decrease it), never zero (the floor is
# 0.003 > 0 so q_point is still > 0 for any winning bin → no -log blowup).
# ---------------------------------------------------------------------------
# Forward-validated threshold: q_point bins < this value are in the "far-tail YES" region.
FAR_TAIL_Q_POINT_THRESH: float = 0.05   # §evidence doc: "served q_point < ~0.05"
# Forward-validated floor: the realized far-tail frequency (~0.006 mean; 0.003 is conservative
# and ensures self-reject at the observed fill floor ~0.01 after cost).
FAR_TAIL_LCB_FLOOR: float = 0.003       # §evidence doc "realized far-tail floor (~0.003)"

# A 95% band uses the exact Clopper-Pearson upper bound from current member hits;
# its zero-hit value is the narrowest possible, even under the optimistic
# assumption that every member is independent. Current mean and variance also
# imply an exact one-sided Cantelli ambiguity bound for bins lying wholly on one
# side of the mean. These are current-evidence algebra, not fitted historical
# floors. They widen the coherent carrier used by both YES and NO.
_FINITE_EVIDENCE_BAND_ALPHA = 0.05


def _finite_evidence_binomial_ucb(
    hit_count: int,
    member_count: int,
    *,
    alpha: float = _FINITE_EVIDENCE_BAND_ALPHA,
    metric: str | None = None,
) -> float:
    """One-sided Clopper-Pearson UCB for current member hits, dependence-corrected.

    MEMBER-DEPENDENCE EFFECTIVE-N (2026-07-17, upstream_data_physical consult v2
    (f)): the ~51 ENS members share the model's synoptic state, so their
    settlement-preimage hit indicators are NOT independent binomial trials and
    the integer-n CP bound is overconfident. When a fitted member-dependence
    artifact is ACTIVE (state/ens_member_dependence/, written only by
    scripts/fit_ens_member_dependence.py), the bound applies the Kish design
    effect:

        n_eff = n / (1 + (n - 1) * rho),   k_eff = k * n_eff / n
        UCB   = betaincinv(k_eff + 1, n_eff - k_eff, 1 - alpha)

    the continuous generalization of the exact CP identity. Conservative-only:
    UCB is monotone non-decreasing in rho (numerically verified over k in 0..n,
    n in {2,5,10,51,200}, rho in [0.01, 1.0]; zero violations). Fail-open:
    artifact absent or rho == 0 takes the EXACT integer path (byte-identical to
    pre-artifact behavior); k == n stays 1.0. ``metric`` ('high'/'low') selects
    the per-metric fitted rho; None or unfitted maps to the MAX fitted rho
    (pooled conservative fallback — larger rho only widens).
    """

    k = int(hit_count)
    n = int(member_count)
    a = float(alpha)
    if n < 1 or k < 0 or k > n or not math.isfinite(a) or not 0.0 < a < 0.5:
        raise ValueError(
            "finite-evidence tail bound requires 0<=hits<=n, n>=1, alpha in (0, 0.5)"
        )
    if k == n:
        return 1.0
    from scipy.special import betaincinv  # noqa: PLC0415

    from src.forecast.ens_member_dependence import member_dependence_rho  # noqa: PLC0415

    rho = member_dependence_rho(metric)
    if rho <= 0.0:
        return float(betaincinv(k + 1, n - k, 1.0 - a))
    n_eff = n / (1.0 + (n - 1) * rho)
    k_eff = k * n_eff / n
    return float(betaincinv(k_eff + 1.0, n_eff - k_eff, 1.0 - a))


def _finite_evidence_zero_hit_ucb_floor(
    member_count: int,
    *,
    alpha: float = _FINITE_EVIDENCE_BAND_ALPHA,
    metric: str | None = None,
) -> float:
    """Smallest exact binomial UCB possible with ``member_count`` samples."""

    return _finite_evidence_binomial_ucb(0, member_count, alpha=alpha, metric=metric)


def _stress_coherent_samples_to_marginal_ucb_floors(
    samples: object,
    required_ucb: Sequence[float],
    *,
    alpha: float = _FINITE_EVIDENCE_BAND_ALPHA,
):
    """Encode finite-evidence marginal UCB floors in one coherent simplex."""

    import numpy as np  # noqa: PLC0415

    probs = np.asarray(samples, dtype=float).copy()
    floors = np.asarray(tuple(required_ucb), dtype=float)
    if (
        probs.ndim != 2
        or probs.shape[0] < 2
        or floors.shape != (probs.shape[1],)
        or not np.isfinite(probs).all()
        or not np.isfinite(floors).all()
        or (probs < 0.0).any()
        or (probs > 1.0).any()
        or (floors < 0.0).any()
        or (floors > 1.0).any()
        or not np.allclose(probs.sum(axis=1), 1.0, atol=1e-12)
        or not math.isfinite(float(alpha))
        or not 0.0 < float(alpha) < 0.5
    ):
        raise ValueError("finite-evidence stress requires a coherent simplex and valid UCB floors")
    raw_ucb = np.percentile(probs, 100.0 * (1.0 - float(alpha)), axis=0)
    targets = np.flatnonzero(raw_ucb < floors)
    stress_rows = int(math.floor(float(alpha) * probs.shape[0])) + 1
    if int(targets.size) * stress_rows > probs.shape[0]:
        raise ValueError("finite-evidence tail stress exceeds coherent bootstrap carrier")
    available = np.ones(probs.shape[0], dtype=bool)
    for target in targets.tolist():
        target_floor = float(floors[target])
        ranked = np.argsort(-probs[:, target], kind="stable")
        rows = ranked[available[ranked]][:stress_rows]
        if int(rows.size) != stress_rows:
            raise ValueError("finite-evidence tail stress rows unavailable")
        old = probs[rows, target]
        raise_mask = old < target_floor
        raise_rows = rows[raise_mask]
        if int(raise_rows.size):
            old_raise = old[raise_mask]
            scale = (1.0 - target_floor) / (1.0 - old_raise)
            probs[raise_rows, :] = probs[raise_rows, :] * scale[:, None]
            probs[raise_rows, target] = target_floor
        available[rows] = False
    if not np.isfinite(probs).all() or not np.allclose(
        probs.sum(axis=1), 1.0, atol=1e-12
    ):
        raise ValueError("finite-evidence tail stress broke simplex coherence")
    return np.ascontiguousarray(probs, dtype=np.float64)


def _current_evidence_tail_ucb_floors(
    *,
    mu_star: float,
    predictive_sigma_c: float,
    bins: Sequence[object],
    half_step: float,
    rounding_rule: str,
    members_c: Sequence[float],
    metric: str | None = None,
    day0_observed_extreme_c: float | None = None,
    day0_metric: str | None = None,
) -> dict[str, float]:
    """Per-bin UCB floors from members, moments, and evidenced center scenarios.

    ``metric`` selects the fitted member-dependence rho for the CP term (see
    ``_finite_evidence_binomial_ucb``); the Cantelli moment term is untouched.

    The provider center and the current ENS-member center are distinct observed
    estimates of the same settlement quantity. Folding their displacement into
    one wider Normal is valid for the served point q, but it can make an exact bin
    near either center look implausible under *every* bootstrap draw. Preserve
    both current-evidence worlds in the executable ambiguity band by integrating
    each center with the observed within-ensemble spread and taking their per-bin
    maximum as an additional UCB floor. This changes no point q and uses no
    historical residual, fitted mixture, market price, or constant floor.

    When ``day0_observed_extreme_c`` is set, bins the Day0 support transform makes
    settlement-IMPOSSIBLE (HIGH: upper preimage <= obs; LOW: lower preimage >= obs —
    the exact predicate ``_build_fused_q_bounds`` zeroes) get a 0.0 floor: the
    absorbed obs already removes them, so the forecast ambiguity band must not lift
    them off zero. The remaining POSSIBLE bins keep the member/moment/scenario floor.
    """

    from src.calibration.emos import bin_probability_settlement  # noqa: PLC0415
    from src.contracts.settlement_semantics import settlement_preimage_offsets  # noqa: PLC0415

    mu = float(mu_star)
    sigma = float(predictive_sigma_c)
    if not math.isfinite(mu) or not math.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("current-evidence tail bound requires finite mu and positive sigma")
    low_off, high_off = settlement_preimage_offsets(
        rounding_rule,
        half_step=half_step,
    )
    members = tuple(float(value) for value in members_c)
    if len(members) < 1 or any(not math.isfinite(value) for value in members):
        raise ValueError("current-evidence tail bound requires finite members")
    hit_counts = _current_evidence_member_hit_counts(
        bins=bins,
        half_step=half_step,
        rounding_rule=rounding_rule,
        members_c=members,
    )
    member_mean = sum(members) / len(members)
    within_sigma = math.sqrt(
        sum((value - member_mean) ** 2 for value in members) / len(members)
    )
    variance = sigma * sigma
    day0_obs = (
        None
        if day0_observed_extreme_c is None or not math.isfinite(float(day0_observed_extreme_c))
        else float(day0_observed_extreme_c)
    )
    day0_dir = str(day0_metric or "").lower() if day0_obs is not None else None

    def _component_mass(bin_: object, *, center: float) -> float:
        if within_sigma > 0.0:
            if day0_obs is not None:
                return _day0_conditioned_bin_probability(
                    metric=str(day0_dir),
                    observed_extreme_c=day0_obs,
                    mu=center,
                    sigma=within_sigma,
                    bin_low_c=bin_.lower_c,
                    bin_high_c=bin_.upper_c,
                    half_step=half_step,
                    rounding_rule=rounding_rule,
                )
            return bin_probability_settlement(
                center,
                within_sigma,
                bin_.lower_c,
                bin_.upper_c,
                half_step=half_step,
                rounding_rule=rounding_rule,
            )

        # A flat ensemble is a deterministic current scenario, not a reason to
        # invent epsilon variance. Apply the same Day0 absorbing transform, then
        # test its settlement preimage exactly.
        final = center
        if day0_obs is not None:
            if day0_dir == "high":
                final = max(day0_obs, center)
            elif day0_dir == "low":
                final = min(day0_obs, center)
            else:
                raise ValueError(
                    f"day0_metric must be high or low when Day0 evidence is set, got {day0_metric!r}"
                )
        low = None if bin_.lower_c is None else float(bin_.lower_c) + low_off
        high = None if bin_.upper_c is None else float(bin_.upper_c) + high_off
        return float((low is None or final >= low) and (high is None or final < high))

    floors: dict[str, float] = {}
    for bin_ in bins:
        low = None if bin_.lower_c is None else float(bin_.lower_c) + low_off
        high = None if bin_.upper_c is None else float(bin_.upper_c) + high_off
        # Day0 settlement-impossible bins carry no forecast-ambiguity floor (same
        # predicate _build_fused_q_bounds uses to zero them: HIGH highs<=obs, LOW lows>=obs).
        if day0_obs is not None:
            if day0_dir == "high" and high is not None and high <= day0_obs:
                floors[str(bin_.bin_id)] = 0.0
                continue
            if day0_dir == "low" and low is not None and low >= day0_obs:
                floors[str(bin_.bin_id)] = 0.0
                continue
        moment = 0.0
        if low is not None and low > mu:
            gap = low - mu
            moment = variance / (variance + gap * gap)
        elif high is not None and high < mu:
            gap = mu - high
            moment = variance / (variance + gap * gap)
        sample_ucb = _finite_evidence_binomial_ucb(
            hit_counts[str(bin_.bin_id)],
            len(members),
            metric=metric,
        )
        component = max(
            _component_mass(bin_, center=mu),
            _component_mass(bin_, center=member_mean),
        )
        floors[str(bin_.bin_id)] = max(sample_ucb, moment, component)
    return floors


def _current_evidence_member_hit_counts(
    *,
    bins: Sequence[object],
    half_step: float,
    rounding_rule: str,
    members_c: Sequence[float],
) -> dict[str, int]:
    """Count current members inside each exact settlement preimage."""

    from src.contracts.settlement_semantics import settlement_preimage_offsets  # noqa: PLC0415

    members = tuple(float(value) for value in members_c)
    if len(members) < 1 or any(not math.isfinite(value) for value in members):
        raise ValueError("current-evidence hit counts require finite members")
    low_off, high_off = settlement_preimage_offsets(
        rounding_rule,
        half_step=half_step,
    )
    hits: dict[str, int] = {}
    for bin_ in bins:
        low = None if bin_.lower_c is None else float(bin_.lower_c) + low_off
        high = None if bin_.upper_c is None else float(bin_.upper_c) + high_off
        hits[str(bin_.bin_id)] = sum(
            1
            for value in members
            if (low is None or value >= low) and (high is None or value < high)
        )
    return hits

# Live rows carry only certified fused-center bootstrap bounds. Degraded or
# missing-capture carriers are not written into the live posterior table.
def _family_rounding_rule(bins: Sequence[object]) -> str:
    """Return the single settlement rounding rule shared by a bin family.

    The market bin family is constructed with ONE rounding rule (the seed builder
    sets oracle_truncate for HKO, wmo_half_up otherwise, on every bin).  A mixed
    family is a provenance error — the q-integration preimage is a per-CITY
    property, not per-bin, so all bins MUST agree.  Fail loud rather than silently
    integrating part of a family under the wrong preimage.
    """
    rules = {str(getattr(b, "rounding_rule", "wmo_half_up")) for b in bins}
    if len(rules) != 1:
        raise ValueError(
            f"bin family mixes settlement rounding rules {sorted(rules)} — the "
            f"settlement preimage is a per-city property and must be uniform"
        )
    return next(iter(rules))


def _normal_cdf(*, mu: float, sigma: float, x: float) -> float:
    if x == -math.inf:
        return 0.0
    if x == math.inf:
        return 1.0
    return 0.5 * (1.0 + math.erf((float(x) - float(mu)) / (float(sigma) * math.sqrt(2.0))))


# Created: 2026-06-29
# Authority basis: capital-gated per-city rho-mix serving (frontier-consult validated; the fitter side —
#   scripts/fit_sigma_scale.py per-city "cities" {k,w,score_capital} layer — is already DONE). This is the
#   ONE shared q builder extracted from _compute_posterior_payload's fused-q construction. It MUST
#   reproduce the prior in-line q byte-identically when called with the GLOBAL family (k, w, floor_steps),
#   so a city with no earned capital (rho=0) serves exactly today's q.
def _build_scaled_normal_uniform_q(
    *,
    mu: float,
    sigma_pred: float,
    k: float,
    uniform_w: float,
    floor_steps: float,
    bins: "Sequence[object]",
    half_step: float,
    rounding_rule: str,
    day0_obs_extreme_c: float | None,
    settlement_step_c: float,
    settlement_sigma_floor_c: float | None,
    city_unit: str,
    metric: str = "high",
) -> tuple[dict[str, float], list[str], bool]:
    """Build the served settlement-bin q = ∫_bin[(1−w)·Normal(μ, σ·k) + w·Uniform], normalized.

    PURE module-level extraction of the fused-q construction body (k→σ scaling, the step-unit σ-floor
    and the settlement-σ-floor, the per-bin ``_bin_mass`` with the catch-all coherence cap, the
    normalization, and the uniform-w mixture + constrained redistribution + post-condition asserts).
    Returns ``(q, catchall_capped_bins, uniform_applied)`` where ``q`` sums to 1, ``catchall_capped_bins``
    lists the open-ended bins whose mass was capped at their honest (un-floored) value, and
    ``uniform_applied`` is True iff the uniform-w mixture actually fired (so the caller stamps
    ``uniform_mixture_w_applied`` exactly as the prior in-line code did).

    Every invariant of the in-line construction is preserved EXACTLY:
      - k applies BEFORE the floors; floors only WIDEN (``max()``) and leave ``sigma_pred`` (the honest
        un-floored width) intact.
      - the catch-all coherence cap pins each open-ended bin at its un-floored predictive-σ mass — the
        floor (and below, the uniform mixture) may only FLATTEN, never inflate a far catch-all (the
        Paris >=26 incident invariant).
      - day0 conditioning uses ``_day0_conditioned_bin_probability`` when ``day0_obs_extreme_c`` is not
        None, else ``bin_probability_settlement``.
      - the uniform-w gate stays EXACTLY ``if uniform_w > 0.0 and city_unit == "C"`` (the C-only gate is
        intentional — fixing F is a separate future release that alters the fallback and would
        invalidate the capital accounting).
      - the constrained-redistribution + post-condition asserts are byte-identical.

    PURITY: no side effects, no provenance mutation, no DB / config / artifact reads — every input is an
    explicit argument. The caller computes ``settlement_sigma_floor_c`` (the city|season|metric lookup,
    which is not pure) and passes it in, and stamps provenance from the inputs/outputs.
    """
    from src.calibration.emos import bin_probability_settlement  # noqa: PLC0415

    _sigma_pred = float(sigma_pred)
    _sigma_used = _sigma_pred
    # k applies BEFORE the floors (σ·k sharpens when k<1, widens when k>1). The k=1 no-op stays
    # byte-identical; a non-positive k is the inert no-op (a k<=0 σ is nonsensical).
    if k != 1.0 and k > 0.0:
        _sigma_pred = _sigma_pred * float(k)
        _sigma_used = _sigma_pred
    # ABSOLUTE σ-floor in step units: σ_core = max(σ_impl·k, floor_steps·step). max() only WIDENS and
    # leaves _sigma_pred intact so the catch-all cap still bars inflation. floor_steps==0.0 ⇒ inert.
    if floor_steps > 0.0:
        _floor_value = float(floor_steps) * float(settlement_step_c)
        if math.isfinite(_floor_value) and _floor_value > _sigma_used:
            _sigma_used = _floor_value
    # Settlement σ-floor (city|season|metric), looked up impurely by the caller and threaded in.
    if settlement_sigma_floor_c is not None and float(settlement_sigma_floor_c) > _sigma_used:
        _sigma_used = float(settlement_sigma_floor_c)

    _catchall_capped_bins: list[str] = []
    _catchall_honest_mass: dict[str, float] = {}

    def _is_open_ended_bin(_b) -> bool:
        return (_b.lower_c is None) != (_b.upper_c is None)

    def _bin_mass(_b) -> float:
        _lo = None if _b.lower_c is None else float(_b.lower_c)
        _hi = None if _b.upper_c is None else float(_b.upper_c)
        if day0_obs_extreme_c is not None:
            _m = _day0_conditioned_bin_probability(
                metric=metric,
                observed_extreme_c=day0_obs_extreme_c,
                mu=float(mu),
                sigma=_sigma_used,
                bin_low_c=_lo,
                bin_high_c=_hi,
                half_step=half_step,
                rounding_rule=rounding_rule,
            )
        else:
            _m = bin_probability_settlement(
                mu=float(mu),
                sigma=_sigma_used,
                bin_low=_lo,
                bin_high=_hi,
                half_step=half_step,
                rounding_rule=rounding_rule,
            )
        # Open-ended catch-all bin: exactly one bound is None. Cap floored mass at the un-floored
        # (predictive-sigma) mass so the floor can never inflate the tail.
        if _is_open_ended_bin(_b):
            if day0_obs_extreme_c is not None:
                _m_unfloored = _day0_conditioned_bin_probability(
                    metric=metric,
                    observed_extreme_c=day0_obs_extreme_c,
                    mu=float(mu),
                    sigma=_sigma_pred,
                    bin_low_c=_lo,
                    bin_high_c=_hi,
                    half_step=half_step,
                    rounding_rule=rounding_rule,
                )
            else:
                _m_unfloored = bin_probability_settlement(
                    mu=float(mu),
                    sigma=_sigma_pred,
                    bin_low=_lo,
                    bin_high=_hi,
                    half_step=half_step,
                    rounding_rule=rounding_rule,
                )
            _catchall_honest_mass[_b.bin_id] = float(_m_unfloored)
            if _sigma_used > _sigma_pred and _m_unfloored < _m:
                _catchall_capped_bins.append(_b.bin_id)
                _m = _m_unfloored
        return _m

    _fused_q = {b.bin_id: _bin_mass(b) for b in bins}
    _total = sum(_fused_q.values())
    if not (_total > 0.0 and math.isfinite(_total)):
        raise ValueError(f"fused-q mass not positive-finite: {_total}")
    q = {key: float(value) / _total for key, value in _fused_q.items()}
    # Whether the uniform mixture ACTUALLY fired (so the caller stamps uniform_mixture_w_applied exactly
    # as the prior in-line code did — only when a renormalized mixed q was produced, never in the
    # degenerate no-eligible-bins / non-finite-total cases).
    _uniform_applied = False
    # FITTED UNIFORM MIXTURE — applied to the final normalized q: q_adj = (1-w)·q_normal + w·uniform.
    # The C-only gate is INTENTIONAL and unchanged (fixing F is a separate future release).
    if uniform_w > 0.0 and city_unit == "C":
        _uniform_eligible_bins = (
            [key for key, val in q.items() if float(val) > 0.0]
            if day0_obs_extreme_c is not None
            else list(q)
        )
        _n_bins = len(_uniform_eligible_bins)
        if _n_bins > 0:
            _u = 1.0 / _n_bins
            _eligible = set(_uniform_eligible_bins)
            _mixed = {
                key: (1.0 - uniform_w) * val + uniform_w * (_u if key in _eligible else 0.0)
                for key, val in q.items()
            }
            _mtot = sum(_mixed.values())
            if _mtot > 0.0 and math.isfinite(_mtot):
                _q_mixed = {key: val / _mtot for key, val in _mixed.items()}
                # Re-cap open-ended catch-all bins at their honest normalized mass (same honest mass,
                # normalized by the pre-mixture _total, the floor cap used). CONSTRAINED REDISTRIBUTION:
                # pin each capped bin at its honest mass and absorb the residual ONLY across the uncapped
                # bins, so the renorm divide can never re-inflate a capped bin (the Paris >=26 category).
                _honest_norm_by_bin: dict[str, float] = {}
                _capped_now: set[str] = set()
                for _bid, _honest in _catchall_honest_mass.items():
                    _honest_norm = float(_honest) / _total
                    _honest_norm_by_bin[_bid] = _honest_norm
                    if _q_mixed.get(_bid, 0.0) > _honest_norm:
                        _q_mixed[_bid] = _honest_norm
                        _capped_now.add(_bid)
                        if _bid not in _catchall_capped_bins:
                            _catchall_capped_bins.append(_bid)
                _capped_mass = sum(_q_mixed[_b] for _b in _capped_now)
                _uncapped_mass = sum(
                    _val for _key, _val in _q_mixed.items() if _key not in _capped_now
                )
                _residual = 1.0 - _capped_mass  # mass the uncapped bins must carry
                if (
                    _capped_now
                    and _uncapped_mass > 0.0
                    and math.isfinite(_uncapped_mass)
                    and _residual >= 0.0
                ):
                    _scale = _residual / _uncapped_mass
                    q = {
                        _key: (_val if _key in _capped_now else _val * _scale)
                        for _key, _val in _q_mixed.items()
                    }
                    _uniform_applied = True
                else:
                    # Degenerate: nothing capped (no-op), OR every bin is a capped open-ended bin / no
                    # uncapped mass to absorb the residual. Plain renormalization is the only option and
                    # is exactly the prior behavior when no cap bit.
                    _rtot = sum(_q_mixed.values())
                    if _rtot > 0.0 and math.isfinite(_rtot):
                        q = {key: val / _rtot for key, val in _q_mixed.items()}
                        _uniform_applied = True
                # POST-CONDITIONS: in the non-degenerate path every capped open-ended bin sits at EXACTLY
                # its honest mass and the total is 1.0 ± 1e-9. Assert so a refactor cannot silently
                # reintroduce the renorm re-inflation.
                if _capped_now and _uncapped_mass > 0.0 and _residual >= 0.0:
                    for _bid in _capped_now:
                        assert q[_bid] <= _honest_norm_by_bin[_bid] + 1e-9, (
                            f"capped open-ended bin {_bid} re-inflated above honest mass: "
                            f"{q[_bid]} > {_honest_norm_by_bin[_bid]}"
                        )
                    assert abs(sum(q.values()) - 1.0) <= 1e-9, (
                        f"constrained-redistribution mass drift: {sum(q.values())}"
                    )
    return q, _catchall_capped_bins, _uniform_applied


# Created: 2026-06-29
# Authority basis: capital-gated per-city rho-mix serving — the NON-INFERIORITY weight. rho is a
#   CALIBRATION WEIGHT derived AUTOMATICALLY from earned out-of-sample score capital C (never a manual
#   flag / cap / allowlist). C<=0 (or city absent) ⇒ rho=0 ⇒ pure global ⇒ byte-identical to today.
def _city_rho_from_capital(score_capital: float, n_eligible_bins: int) -> float:
    """rho = 1 − exp(−C / W); C ≤ 0 or W ≤ 0 ⇒ 0.0.

    ``score_capital`` (C) is the prequential Bernoulli-log-score the city's EB (k,w) earned OVER global
    on rolling OOS splits (written by the fitter). ``n_eligible_bins`` (W) is the number of eligible
    Bernoulli bin terms in this family batch — the SAME bin set q is built over. A city saturates toward
    rho=1 only after earning many batches' worth of capital; a city that barely cleared zero serves a
    light city blend over the proven global.
    """
    try:
        C = float(score_capital)
        W = int(n_eligible_bins)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(C) or C <= 0.0 or W <= 0:
        return 0.0
    return 1.0 - math.exp(-C / float(W))


def _mix_q_by_rho(
    q_global: "Mapping[str, float]",
    q_city: "Mapping[str, float]",
    rho: float,
    *,
    renormalize: bool = True,
) -> dict[str, float]:
    """Serve q_mix(bin) = (1−rho)·q_global[bin] + rho·q_city[bin] over the union of bin keys.

    ``renormalize=True`` (the served POINT q) divides by the total so it sums to 1 defensively; the two
    inputs already sum to ~1 so this is a no-op up to FP. ``renormalize=False`` (the q_lcb/q_ucb bound
    CARRIERS, which do NOT each sum to 1) keeps the raw convex combination — the bound carriers are
    intentionally not a probability simplex.
    """
    r = float(rho)
    keys = list(q_global) if set(q_global) == set(q_city) else sorted(set(q_global) | set(q_city))
    mixed = {
        b: (1.0 - r) * float(q_global.get(b, 0.0)) + r * float(q_city.get(b, 0.0))
        for b in keys
    }
    if renormalize:
        tot = sum(mixed.values())
        if tot > 0.0 and math.isfinite(tot):
            return {b: v / tot for b, v in mixed.items()}
    return mixed


def _mix_q_samples_by_rho(
    global_samples: "Mapping[str, Sequence[float]]",
    city_samples: "Mapping[str, Sequence[float]]",
    rho: float,
) -> dict[str, list[float]]:
    """Mix two coherent draw matrices with the same city weight as served q.

    Each mapping is column-oriented (bin -> draws).  Row ``i`` in both carriers
    represents the same seeded center draw, so the served draw is the pointwise
    convex combination.  Convex combinations of two simplexes remain a simplex;
    validating that invariant here prevents a city-mixed point q from being scored
    against the unrelated global-only bootstrap distribution.
    """

    keys = tuple(global_samples)
    if not keys or set(keys) != set(city_samples):
        raise ValueError("global/city bootstrap sample bins must match exactly")
    lengths = {
        len(global_samples[key]) for key in keys
    } | {
        len(city_samples[key]) for key in keys
    }
    if len(lengths) != 1 or next(iter(lengths), 0) < 2:
        raise ValueError("global/city bootstrap sample counts must match and be >= 2")
    r = float(rho)
    if not math.isfinite(r) or not 0.0 <= r <= 1.0:
        raise ValueError("city bootstrap mixture rho must lie in [0, 1]")

    n_draws = next(iter(lengths))
    out = {key: [] for key in keys}
    for draw_idx in range(n_draws):
        global_row = [float(global_samples[key][draw_idx]) for key in keys]
        city_row = [float(city_samples[key][draw_idx]) for key in keys]
        if (
            not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in global_row)
            or not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in city_row)
            or not math.isclose(sum(global_row), 1.0, rel_tol=0.0, abs_tol=1e-9)
            or not math.isclose(sum(city_row), 1.0, rel_tol=0.0, abs_tol=1e-9)
        ):
            raise ValueError("global/city bootstrap draws must each be probability simplexes")
        mixed_row = [
            (1.0 - r) * global_value + r * city_value
            for global_value, city_value in zip(global_row, city_row)
        ]
        total = sum(mixed_row)
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("mixed bootstrap draw left the probability simplex")
        for key, value in zip(keys, mixed_row):
            out[key].append(float(value / total))
    return out


def _day0_conditioned_bin_probability(
    *,
    metric: str,
    observed_extreme_c: float,
    mu: float,
    sigma: float,
    bin_low_c: float | None,
    bin_high_c: float | None,
    half_step: float,
    rounding_rule: str,
) -> float:
    from src.forecast.day0_conditioner import (  # noqa: PLC0415
        day0_bin_preimage_native,
        probability_high_day0_bin,
        probability_low_day0_bin,
    )

    lo, hi = day0_bin_preimage_native(
        bin_low_c,
        bin_high_c,
        rounding_rule=rounding_rule,
        half_step=half_step,
    )

    def _cdf(x: float) -> float:
        return _normal_cdf(mu=mu, sigma=sigma, x=x)

    if metric == "high":
        return probability_high_day0_bin(float(observed_extreme_c), lo, hi, _cdf)
    if metric == "low":
        return probability_low_day0_bin(float(observed_extreme_c), lo, hi, _cdf)
    raise ValueError(f"metric must be high or low, got {metric!r}")


def _build_fused_q_bounds(
    *,
    mu_star: float,
    center_sigma_c: float,
    predictive_sigma_c: float,
    bins: Sequence[object],
    half_step: float,
    q_point: Mapping[str, float],
    n_draws: int = _QLCB_BOOTSTRAP_DRAWS,
    rounding_rule: str = "wmo_half_up",
    day0_observed_extreme_c: float | None = None,
    day0_metric: str | None = None,
    evidence_members_c: Sequence[float] | None = None,
    return_samples: bool = False,
) -> tuple[dict[str, float], dict[str, float]] | tuple[
    dict[str, float], dict[str, float], dict[str, list[float]]
]:
    """Vectorized fused-center parameter-uncertainty bootstrap for per-bin q_lcb / q_ucb.

    Draws ``n_draws`` centers μ_i ~ N(μ*, center_sigma_c) and integrates every settlement bin
    via the ONE integrator's preimage math (bin_probability_settlement, replicated vectorized
    over the (draws × bins) grid with scipy.special.ndtr). Returns (q_lcb_map, q_ucb_map) where
    q_lcb[bin] = 5th percentile and q_ucb[bin] = 95th percentile of the per-bin probability across
    draws, clipped so q_lcb ≤ q_point ≤ q_ucb per bin and q_lcb ≥ 0.
    When ``return_samples`` is true, also return the per-bin draw vectors so the
    live decision adapter can compute empirical edge p-values directly.

    PATH-A COHERENCE (2026-06-18 FINAL no-blocked execution flow §5): each draw's row is
    renormalized to the probability simplex BEFORE the marginal quantile — the IDENTICAL
    renormalize-then-quantile transform ``src/probability/joint_q_band.build_joint_q_band``
    (the q_lcb AUTHORITY) performs. This makes the modal-collapse defect of the old raw
    per-bin-percentile Path B unconstructable: a tight modal spike most draws agree on keeps
    a high q_lcb. This bound is no longer an INDEPENDENT q_lcb method — it applies the same
    coherent transform as the authority, so the persisted forecast_posteriors q_lcb can never
    carry a collapsed value.

    Raises on any construction failure. The caller fail-softs the certified bootstrap bounds, then
    may promote non-certified Wilson member-vote bounds under their own basis.
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

    # Per-bin integration bounds in absolute Celsius via the SETTLEMENT PREIMAGE of the
    # declared rounding rule (the SAME single contract source bin_probability_settlement uses).
    # wmo_half_up -> symmetric (-half_step, +half_step) [standard cities, byte-identical to the
    # historical path]; oracle_truncate/floor -> asymmetric (0, +2·half_step) [Hong Kong]. None
    # shoulder -> -inf / +inf via cdf 0.0 / 1.0.
    from src.contracts.settlement_semantics import settlement_preimage_offsets  # noqa: PLC0415

    _low_off, _high_off = settlement_preimage_offsets(rounding_rule, half_step=half_step)
    lows = np.array(
        [(-np.inf if b.lower_c is None else float(b.lower_c) + _low_off) for b in bins],
        dtype=float,
    )  # (M,)
    highs = np.array(
        [(np.inf if b.upper_c is None else float(b.upper_c) + _high_off) for b in bins],
        dtype=float,
    )  # (M,)

    # Standardized z = (bound - mu_i) / sigma over the (N draws × M bins) grid. ndtr is the vectorized
    # standard-normal CDF; -inf -> 0.0, +inf -> 1.0 are handled by ndtr natively.
    z_low = (lows[None, :] - mu_draws[:, None]) / sigma  # (N, M)
    z_high = (highs[None, :] - mu_draws[:, None]) / sigma  # (N, M)
    cdf_low = ndtr(z_low)
    cdf_high = ndtr(z_high)
    day0_obs = None if day0_observed_extreme_c is None else float(day0_observed_extreme_c)
    if day0_obs is not None and math.isfinite(day0_obs):
        metric = str(day0_metric or "").lower()
        probs = np.zeros_like(cdf_high)
        if metric == "high":
            below = highs <= day0_obs
            straddles = (lows <= day0_obs) & (day0_obs < highs)
            ordinary = ~(below | straddles)
            probs[:, straddles] = cdf_high[:, straddles]
            probs[:, ordinary] = cdf_high[:, ordinary] - cdf_low[:, ordinary]
        elif metric == "low":
            above = lows >= day0_obs
            straddles = (lows < day0_obs) & (day0_obs <= highs)
            ordinary = ~(above | straddles)
            probs[:, straddles] = 1.0 - cdf_low[:, straddles]
            probs[:, ordinary] = cdf_high[:, ordinary] - cdf_low[:, ordinary]
        else:
            raise ValueError(f"day0_metric must be high or low when day0 observed extreme is set, got {day0_metric!r}")
        probs = np.clip(probs, 0.0, 1.0)
    else:
        probs = np.clip(cdf_high - cdf_low, 0.0, 1.0)  # (N, M) per-draw per-bin mass

    # PATH-A COHERENCE (2026-06-18 FINAL no-blocked execution flow §5): renormalize EACH
    # draw's row to the probability simplex BEFORE taking the marginal quantile — the
    # EXACT transformation src/probability/joint_q_band.build_joint_q_band performs
    # (q_k = q_k / q_k.sum() per draw, then quantile along axis 0). Without this the
    # per-bin 5th percentile is taken over RAW masses that do NOT sum to 1, and a narrow
    # high-belief MODAL bin's q_lcb COLLAPSES to ~0 because the handful of draws whose
    # spike landed one bin over drive its low quantile toward 0 (the modal-collapse
    # defect this module's Path B used to ship). Per-row renormalization makes the
    # marginal quantiles quantiles of COHERENT joint distributions, so a tight modal
    # spike most draws agree on keeps a high q_lcb. build_joint_q_band is the q_lcb
    # AUTHORITY; this vectorized materializer bound applies the IDENTICAL renormalize-
    # then-quantile transform so the persisted forecast_posteriors q_lcb can never
    # carry the collapsed Path-B value. Over the standard-bin grid the open-tail bins
    # (lower_c/upper_c None) carry the residual mass so each row already sums to ~1; the
    # explicit renormalize is the structural guarantee (a degenerate all-zero row — no
    # finite bin captured the draw — is left as-is rather than divided by zero).
    _row_sums = probs.sum(axis=1, keepdims=True)  # (N, 1)
    _safe = _row_sums[:, 0] > 0.0
    if np.any(_safe):
        probs[_safe, :] = probs[_safe, :] / _row_sums[_safe, :]

    # FINITE CURRENT-EVIDENCE AMBIGUITY BAND. Center bootstrap alone conditions on a
    # Normal family and can make a far-bin q_ucb arbitrarily close to zero. With
    # N observed members, exact settlement-preimage hits give a Clopper-Pearson
    # UCB (zero hits still licenses 1-alpha^(1/N)); trusting only the current mean
    # and variance also licenses the one-sided Cantelli ambiguity mass
    # sigma^2/(sigma^2+gap^2). It can also erase provider-vs-ENS center
    # disagreement by turning it into one wider Normal. Keep both observed
    # centers as within-spread probability scenarios. Use the largest per-bin
    # value from the member, moment, and component-preserving terms.
    # Encode it inside coherent simplex rows so lower-CVaR and scalar bounds see
    # one probability world. On Day0 the absorbed obs removes the IMPOSSIBLE bins
    # (their floor is masked to 0 inside the floor helper); the remaining POSSIBLE
    # bins are still finite current evidence and keep the member/moment/scenario floor.
    if evidence_members_c is not None:
        finite_floors = _current_evidence_tail_ucb_floors(
            mu_star=mu_star,
            predictive_sigma_c=predictive_sigma_c,
            bins=bins,
            half_step=half_step,
            rounding_rule=rounding_rule,
            members_c=evidence_members_c,
            # day0_metric doubles as the market metric on the non-day0 path
            # (both materializer call sites always pass the family metric);
            # None (e.g. direct test calls) => pooled/max-rho conservative
            # fallback inside the dependence loader.
            metric=day0_metric,
            day0_observed_extreme_c=day0_obs,
            day0_metric=day0_metric,
        )
        required_ucb = np.array([finite_floors[bin_id] for bin_id in bin_ids])
        probs = _stress_coherent_samples_to_marginal_ucb_floors(
            probs,
            required_ucb,
        )

    q_lcb_vec = np.percentile(probs, 5.0, axis=0)  # (M,) marginal quantile of coherent rows
    q_ucb_vec = np.percentile(probs, 95.0, axis=0)  # (M,)

    q_lcb_map: dict[str, float] = {}
    q_ucb_map: dict[str, float] = {}
    q_samples_map: dict[str, list[float]] = {}
    for idx, bin_id in enumerate(bin_ids):
        q_pt = float(q_point.get(bin_id, 0.0))
        lcb = float(q_lcb_vec[idx])
        ucb = float(q_ucb_vec[idx])
        if not (math.isfinite(lcb) and math.isfinite(ucb)):
            raise ValueError(f"non-finite q-bound for bin {bin_id}: lcb={lcb} ucb={ucb}")
        # Defensive ordering clips: q_lcb in [0, q_point], q_ucb >= q_point.
        lcb = min(max(lcb, 0.0), max(q_pt, 0.0))
        ucb = max(ucb, q_pt)
        # FAR-TAIL q_lcb HONESTY (2026-06-22) — legacy/non-source-clock only.
        # Authority: docs/evidence/live_order_pathology/2026-06-22_qlcb_lowerbound_honesty.md
        # For bins where the served q_point < FAR_TAIL_Q_POINT_THRESH (0.05), the raw
        # bootstrap p5 quantile is ~0.07-0.10 due to centre-uncertainty draws but the
        # realized frequency is ~0.003. Cap q_lcb at FAR_TAIL_LCB_FLOOR (0.003) so these
        # bins self-reject at typical fill prices. q_point is NEVER modified. The
        # source-clock route instead derives both sides solely from the current
        # coherent carrier above; historical floors are forbidden on that route.
        # Identity for q_point >= 0.05.
        if evidence_members_c is None and q_pt < FAR_TAIL_Q_POINT_THRESH:
            lcb = min(lcb, FAR_TAIL_LCB_FLOOR)
        q_lcb_map[bin_id] = lcb
        q_ucb_map[bin_id] = ucb
        if return_samples:
            q_samples_map[bin_id] = [float(x) for x in probs[:, idx].tolist()]
    if return_samples:
        return q_lcb_map, q_ucb_map, q_samples_map
    return q_lcb_map, q_ucb_map


def _compute_posterior_payload(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    anchor_id: int,
) -> _PosteriorComputeResult:
    """Pure (no-DB-write) posterior compute extracted from ``_insert_posterior``.

    Runs the SAME canonical multi-model Bayes-precision fusion + fused-q shape +
    certified bootstrap bounds the write path always ran, and returns the result
    as a ``_PosteriorComputeResult``. It performs ZERO ``forecast_posteriors``
    writes — only the read paths inside the fusion override (persisted single_runs
    + walk-forward history) touch ``conn``, all read-only. The boundary is exact:
    everything that historically lived in ``_insert_posterior`` BEFORE the INSERT
    (the value build + the ``not live_layer -> return`` gate + the provenance
    payload assembly) is here; the INSERT itself stays in ``_insert_posterior``.

    Live-eligibility: the historical write path did ``if not live_layer: return
    None`` (no row, no provenance). Here we instead return the struct with
    ``live_eligible=False`` so the read-only monitor caller can tell "fresh but
    not live-eligible" from "compute blocked"; ``_insert_posterior`` maps
    ``not live_eligible -> None`` to keep the write contract byte-identical.
    """
    # Wave-2 item 7 (2026-06-12): the per-city EB bias-correction of the center was
    # DELETED — settlement-refuted as a wrong-set over-correction (2026-06-09 wiring
    # audit, commit ff7f33dd5b) because it was fit on the thin live single_runs anchor
    # (~6 settled dates → n_prior 1–4/city → overfit, net-WORSE per percity_corrected_oos.md).
    #
    # law-8 foundation fix (2026-06-14, cold_bias_metadata_root.md): the ROOT is a per-city
    # 9km grid-cell-vs-settlement-station REPRESENTATIVENESS offset (Tokyo −2.18°C … Karachi
    # +2.48°C, two-sign, lead-stable, raw-anchor-resident). It is correctable ONLY by a per-city
    # de-bias, and SAFE only when fit on the FULL previous_runs history (n=23..890/city) with an
    # activation guard (n>=n_min) + EB shrink toward 0 by SE + a do-no-harm walk-forward gate.
    # The fitted, auditable artifact state/anchor_representativeness_debias.json carries δ_city;
    # the loader (src/calibration/anchor_representativeness_debias.py) returns δ_city ONLY for an
    # activated, gate-passing HIGH cell, else None. ARTIFACT-GATED, not a blocked flag: when the
    # artifact is absent (current live state — gitignored generated file) the loader returns None
    # → bias_shift_c stays None → BYTE-IDENTICAL to today. It goes live the moment the operator
    # places the fitted artifact in state/ and restarts (same posture as the σ-floor artifact).
    # SIGN: δ_city = anchor − settlement; applied below as corrected = raw − δ_city, so a cold
    # anchor (δ<0) warms and a hot anchor (δ>0) cools; the corrected center feeds the fusion prior
    # → the de-bias propagates into the fused μ*. FAIL-SOFT: any error → None (family-level fallback).
    # RAW NO-DE-BIAS LAW (2026-06-18 FINAL no-blocked execution flow §3-§4; operator
    # "NO fitted forward per-city de-bias"): the consumed posterior center is RAW. The
    # per-city representativeness de-bias (``get_city_debias_c`` → δ_city) is a FITTED
    # FORWARD PER-CITY shift on μ — forbidden under the RAW law. It is forced to None
    # here (fail-closed: even were the artifact placed in state/, the consumed center
    # stays RAW), so ``anchor_value_corrected_c == raw_anchor_value_c`` (zero shift) and
    # the fused μ* the materializer writes to forecast_posteriors is the RAW diagonal
    # center — the SAME RAW belief the spine entry serves. The q_lcb empirical
    # reliability guard (decision layer) — NOT a center de-bias — is what makes RAW
    # honest. (Removing this RAW pin re-enables a forbidden forward per-city de-bias.)
    bias_shift_c: float | None = None
    # BAYES_PRECISION_FUSION-Bayes fusion (flag-gated, default-OFF): replace the single OM9 9km anchor center/spread
    # with the multi-model Bayesian posterior. Computed from the EB-corrected anchor center so it
    # composes AFTER the EB bias layer; downstream EMOS + bin integration are unchanged.
    raw_anchor_value_c = request.openmeteo_anchor.high_c if metric == "high" else request.openmeteo_anchor.low_c
    anchor_value_corrected_c = float(raw_anchor_value_c) - (0.0 if bias_shift_c is None else float(bias_shift_c))
    bayes_precision_fusion_override = _replacement_bayes_precision_fusion_override(
        request, metric=metric, anchor_value_corrected_c=anchor_value_corrected_c, conn=conn
    )
    target_date = _date_text(request.target_date)
    source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat()
    # C1-AVAIL-CLOCK (2026-06-16): the posterior's source_available_at is PROOF OF POSSESSION =
    # max over the contributing roles of each role's REAL download-complete wall-clock
    # (source_run.fetch_finished_at), falling back per-role to the request's source_available_at
    # when that role has no source_run row. max() because a FUSED posterior could not be
    # constructed before its LAST-arriving input landed — availability is gated by the slowest
    # dependency. The old max(request.*_source_available_at) used the cycle-time nominal-lag GUESS
    # (~8.4h early for the baseline) as each input; this recovers the honest possession time.
    available_at = _posterior_source_available_at(conn, request).isoformat()
    computed_at = _to_utc(request.computed_at, field_name="computed_at").isoformat()
    if (
        bayes_precision_fusion_override is not None
        and bayes_precision_fusion_override.current_evidence_shape is not None
    ):
        evidence_available_at = _to_utc(
            str(
                bayes_precision_fusion_override.current_evidence_shape[
                    "source_available_at"
                ]
            ),
            field_name="current_evidence_shape.source_available_at",
        )
        available_at = max(
            _to_utc(available_at, field_name="posterior_source_available_at"),
            evidence_available_at,
        ).isoformat()
    data_version = _data_version(metric)
    _n_bins_seed = len(request.bins) or 1
    q = {b.bin_id: 1.0 / _n_bins_seed for b in request.bins}
    q_shape = "uniform_placeholder_pending_fused"
    # FUSED-Q SHAPE (2026-06-09, flag-gated): build q DIRECTLY from N(mu*, sigma_pred) over the
    # SAME settlement bins, replacing the old member-vote shape. The experiment showed the old
    # shape assigns EXACTLY ZERO to the winning bin on 28% of settled cells (vote-support
    # truncation) — a Normal makes that category unconstructable. Uses the ONE settlement bin
    # integrator (emos.bin_probability_settlement, the same preimage math as the live analytic
    # vector). Bin bounds are CELSIUS (lower_c/upper_c) and so are mu*/sigma_pred; half_step =
    # settlement_step_c/2 (the C-scaled rounding half-width). FAIL-CLOSED: key-set mismatch or
    # any error -> keep the soft-anchor q (loud warning), never a silent half-shape.
    # FIX 1/FIX 2 (2026-06-09): derive the EXPLICIT q-mode + record the settlement sigma-floor
    # coherence in provenance. These are derived from the SAME determinations the fused-q path
    # already makes (no parallel re-derivation). Defaults cover the no-override branches.
    replacement_q_mode = REPLACEMENT_Q_MODE_BAYES_PRECISION_FUSION_CAPTURE_MISSING
    settlement_sigma_floor_applied = False
    settlement_sigma_floor_c: float | None = None
    floor_unavailable_reason: str | None = None
    replacement_sigma_basis: str | None = None
    # C3 calibration surface 2026-06-12 — FITTED σ_pred scale provenance. None when scaling is not
    # applied (artifact missing / family unfitted / k=1.0); float applied value when scaling fires.
    sigma_scale_k_applied: float | None = None
    # FITTED uniform-mixture weight provenance. None when no mixture applied (artifact missing /
    # family unfitted / w=0.0); float applied w when the mixture fires. Both come from the SAME
    # state/sigma_scale_fit.json family entry (MLE-fitted, operator law 2026-06-12).
    uniform_mixture_w_applied: float | None = None
    # FITTED absolute σ-floor (step units) provenance (σ-refit report 2026-06-13, task #69). None when
    # the floor is inert (artifact has no floor_steps key — the current live state — or the floor did
    # not bind because the forecast was already wider); the applied floor_steps (e.g. 1.80) when it
    # widened σ to floor_steps·step. From the SAME state/sigma_scale_fit.json family entry as k/w.
    sigma_floor_steps_applied: float | None = None
    # Catch-all (open-ended bin) sigma-floor exemption (2026-06-10, Paris >=26C incident). Records
    # which open-ended bins had their floored mass capped at the un-floored predictive-sigma mass
    # so the floor could not inflate the tail. Empty tuple when no cap bit (no floor / no open-ended
    # bin away from center). Defaults keep fail-closed compute results coherent.
    settlement_sigma_floor_catchall_capped: tuple[str, ...] = ()
    # CAPITAL-GATED PER-CITY rho-MIX provenance (2026-06-29). The served q is a non-inferiority MIXTURE
    # q_serve = (1-rho)*q_global + rho*q_city with rho = 1-exp(-C/W), C = the city's earned OOS score
    # capital, W = the eligible Bernoulli bin count. Defaults = NOT applied (rho=0 => pure global =>
    # byte-identical to today). Stamped in provenance so the served weight is reconstructible.
    city_calibration_layer_applied: bool = False
    city_calibration_rho: float | None = None
    city_score_capital: float | None = None
    city_k_eb: float | None = None
    city_w_eb: float | None = None
    # Q_LCB / Q_UCB outputs. The certified bootstrap basis is present only when fused-q is built and
    # bound construction succeeds. If it fails, carrier bounds may remain present under their own
    # non-live-eligible basis; if that also fails, q_lcb_json/q_ucb_json remain NULL.
    q_lcb_map: dict[str, float] | None = None
    q_ucb_map: dict[str, float] | None = None
    q_bootstrap_samples_by_bin: dict[str, list[float]] | None = None
    q_lcb_basis: str | None = None
    # FAR-TAIL HONESTY PROVENANCE (2026-06-22): count of bins whose q_lcb was capped by the
    # far-tail honesty (q_point < FAR_TAIL_Q_POINT_THRESH). 0 = no far-tail bins / cap did
    # not fire (identity). Stamped in provenance_payload as a plain fact of the live value.
    _far_tail_honesty_count: int = 0
    _finite_evidence_members_c: tuple[float, ...] | None = None
    _finite_evidence_member_count: int | None = None
    _finite_evidence_ucb_floor: float | None = None
    _finite_evidence_member_hits_by_bin: dict[str, int] | None = None
    _finite_evidence_ucb_floor_by_bin: dict[str, float] | None = None
    _finite_evidence_tail_bin_count: int = 0
    # Member-dependence effective-n provenance (2026-07-17). None when the fitted
    # rho artifact is absent or rho=0 (exact integer CP identity — byte-identical
    # pre-artifact behavior); the applied (rho, n_eff) when the correction fired.
    _finite_evidence_member_rho_applied: float | None = None
    _finite_evidence_member_n_eff: float | None = None
    # T0-1 remaining-window Day0 center correction (audit §7 2026-07-18). Inert
    # (0.0/None) when non-Day0 or the anchor-family hourly vector did not license
    # a shift; stamped in provenance only when the delta fired (delta > 0).
    _day0_center_delta_c: float = 0.0
    _day0_center_vector_id: str | None = None
    _day0_center_hours_remaining: float | None = None
    if (
        bayes_precision_fusion_override is not None
        and bayes_precision_fusion_override.predictive_sigma_c is not None
    ):
        try:
            from src.calibration.emos import bin_probability_settlement  # noqa: PLC0415

            _half_step = float(request.settlement_step_c) / 2.0
            # Per-city settlement preimage: the bins declare the rounding rule (oracle_truncate
            # for Hong Kong, wmo_half_up otherwise). The integrator MUST consume it so HK's
            # asymmetric floor() preimage is used instead of the symmetric WMO one. Uniform
            # across the family (fail-loud if mixed).
            _rounding_rule = _family_rounding_rule(request.bins)
            _day0_obs_extreme_c = (
                _day0_absorbing_observed_extreme_c(request)
                if _target_local_day_has_started(request)
                else None
            )
            # Wave-2 item 6 (2026-06-12): the settlement σ-floor is applied by PER-CELL DATA
            # AVAILABILITY, not a global flag (edli_settlement_sigma_floor_enabled / _required
            # merged + deleted). Look up the SAME floor the EMOS path uses (city|season|metric)
            # and widen: sigma_used = max(sigma_pred, floor). max() only WIDENS -> flatter q ->
            # fewer overconfident bets (it can never tighten). When the fitted floor exists for
            # the cell it applies; when it is absent/malformed for the cell the lookup returns
            # None and the floor is simply not applied (recorded, NEVER blocks blocked). One
            # construction rule, no knob.
            _sigma_pred_raw = float(bayes_precision_fusion_override.predictive_sigma_c)
            _current_shape = bayes_precision_fusion_override.current_evidence_shape
            if _current_shape is not None:
                _finite_evidence_members_c = tuple(
                    float(value)
                    for value in (
                        bayes_precision_fusion_override.current_evidence_members_c or ()
                    )
                )
                _finite_evidence_member_count = int(_current_shape["member_count"])
                if len(_finite_evidence_members_c) != _finite_evidence_member_count:
                    raise ValueError(
                        "source-clock current member values do not match member_count"
                    )
                _finite_evidence_ucb_floor = _finite_evidence_zero_hit_ucb_floor(
                    _finite_evidence_member_count, metric=metric
                )
                from src.forecast.ens_member_dependence import (  # noqa: PLC0415
                    member_dependence_rho,
                )

                _member_rho = member_dependence_rho(metric)
                if _member_rho > 0.0:
                    _finite_evidence_member_rho_applied = _member_rho
                    _finite_evidence_member_n_eff = _finite_evidence_member_count / (
                        1.0 + (_finite_evidence_member_count - 1) * _member_rho
                    )
            replacement_sigma_basis = (
                "decision_time_current_ensemble_within_plus_provider_between"
                if _current_shape is not None
                else "fused_center_residual_std"
            )
            # C3 CALIBRATION SURFACE (2026-06-12) — FITTED σ_pred scale (k) + uniform-mixture (w).
            # OPERATOR LAW 2026-06-12: the correction factor must be FITTED by math, never hand-set.
            # k and w are read from state/sigma_scale_fit.json (MLE over settled cells; only
            # scripts/fit_sigma_scale.py writes it). The artifact is keyed by SETTLEMENT UNIT family;
            # an unfitted family (e.g. F today, n=47<60) returns (1.0, 0.0) so the correction stays
            # INERT for it automatically. Per-family licensing is the artifact's job (a REFUSED family →
            # (1.0,0.0,0.0) inert from the lookup), so NO hardcoded settlement-unit allow-list here.
            _city_unit = _city_settlement_unit_from_bins(request)
            if _current_shape is not None:
                # Historical k/w/floors would change a decision-time-only shape
                # into a second probability regime. They are exactly neutral here.
                _k, _uniform_w, _floor_steps = 1.0, 0.0, 0.0
            else:
                _k, _uniform_w, _floor_steps = _effective_unit_sigma_scale(
                    _city_unit
                )
            _mu_anchor = float(bayes_precision_fusion_override.anchor_value_c)
            # T0-1 remaining-window Day0 CENTER correction (audit §7 2026-07-18). Post-peak
            # the whole-day center over-disperses the remaining-day extreme; shift the Day0
            # center toward the remaining-window value: HIGH mu - delta, LOW mu + delta.
            # Applied HERE, before ANY consumer, so the point q, the finite-evidence floors,
            # and the bootstrap bounds integrate ONE corrected center (one probability world).
            # Deliberately NOT re-maxed with obs: the day0 support transform absorbs mass
            # below obs itself — mu below obs is exactly the intended post-peak collapse.
            # Non-Day0: the delta machinery never runs (byte-identical).
            if _day0_obs_extreme_c is not None:
                (
                    _day0_center_delta_c,
                    _day0_center_vector_id,
                    _day0_center_hours_remaining,
                ) = _day0_remaining_center_delta_c(
                    conn,
                    request,
                    metric=metric,
                    computed_at_utc=_to_utc(request.computed_at, field_name="computed_at"),
                )
                if _day0_center_delta_c > 0.0:
                    if metric == "high":
                        _mu_anchor -= _day0_center_delta_c
                    else:
                        _mu_anchor += _day0_center_delta_c
            # The settlement σ-floor (city|season|metric) lookup is IMPURE (config + season) and is read
            # ONCE here, then threaded into the pure q builder for BOTH the global and the city carriers
            # (same physical dispersion). It sets the floor provenance fields exactly as before.
            if _current_shape is not None:
                _floor_c, _floor_reason = None, None
            else:
                _floor_c, _floor_reason = _replacement_settlement_sigma_floor_lookup(
                    request, metric=metric
                )
            if _floor_c is not None:
                settlement_sigma_floor_c = float(_floor_c)
                settlement_sigma_floor_applied = True
            else:
                floor_unavailable_reason = _floor_reason

            def _resolve_sigma_used(_k_in: float, _floor_steps_in: float) -> float:
                # The SAME σ ladder the pure builder applies (k → step-floor → settlement-floor). Used to
                # feed _build_fused_q_bounds the EXACT predictive σ each carrier's point q integrates at,
                # so q_lcb ≤ q_point ≤ q_ucb holds per bin. max() only ever widens.
                _s = _sigma_pred_raw
                if _k_in != 1.0 and _k_in > 0.0:
                    _s = _s * float(_k_in)
                if _floor_steps_in > 0.0:
                    _fv = float(_floor_steps_in) * float(request.settlement_step_c)
                    if math.isfinite(_fv) and _fv > _s:
                        _s = _fv
                if _floor_c is not None and float(_floor_c) > _s:
                    _s = float(_floor_c)
                return _s

            # GLOBAL q — the proven family pair. Byte-identical to the prior in-line construction (the
            # pure builder is a verbatim extraction). Its k/floor provenance describes the GLOBAL layer.
            _sigma_used = _resolve_sigma_used(_k, _floor_steps)
            if _finite_evidence_member_count is not None:
                _finite_evidence_member_hits_by_bin = _current_evidence_member_hit_counts(
                    bins=request.bins,
                    half_step=_half_step,
                    rounding_rule=_rounding_rule,
                    members_c=_finite_evidence_members_c or (),
                )
                _finite_evidence_ucb_floor_by_bin = _current_evidence_tail_ucb_floors(
                    mu_star=_mu_anchor,
                    predictive_sigma_c=_sigma_used,
                    bins=request.bins,
                    half_step=_half_step,
                    rounding_rule=_rounding_rule,
                    members_c=_finite_evidence_members_c,
                    metric=metric,
                    day0_observed_extreme_c=_day0_obs_extreme_c,
                    day0_metric=metric,
                )
            # k provenance: stamped iff the scale fired (k != 1.0, k > 0.0) — the k=1 no-op stays None.
            _sigma_after_k = _sigma_pred_raw * _k if (_k != 1.0 and _k > 0.0) else _sigma_pred_raw
            if _k != 1.0 and _k > 0.0:
                sigma_scale_k_applied = _k
            # step-floor provenance: stamped iff floor_steps·step actually WIDENED σ over σ_after_k (the
            # same `> _sigma_used` test the in-line code used, evaluated against σ_after_k before the
            # settlement floor). floor_steps absent ⇒ 0.0 ⇒ inert ⇒ None.
            if _floor_steps > 0.0:
                _floor_value = float(_floor_steps) * float(request.settlement_step_c)
                if math.isfinite(_floor_value) and _floor_value > _sigma_after_k:
                    sigma_floor_steps_applied = float(_floor_steps)
            q_global, _capped_global, _uniform_applied_global = _build_scaled_normal_uniform_q(
                mu=_mu_anchor,
                sigma_pred=_sigma_pred_raw,
                k=_k,
                uniform_w=_uniform_w,
                floor_steps=_floor_steps,
                bins=request.bins,
                half_step=_half_step,
                rounding_rule=_rounding_rule,
                day0_obs_extreme_c=_day0_obs_extreme_c,
                settlement_step_c=float(request.settlement_step_c),
                settlement_sigma_floor_c=settlement_sigma_floor_c,
                city_unit=_city_unit,
                metric=metric,
            )
            if set(q_global) != set(q):
                raise ValueError(
                    f"fused-q bin keys != soft-anchor q keys ({sorted(q_global)[:3]}... vs "
                    f"{sorted(q)[:3]}...)"
                )
            # GLOBAL-layer provenance: the uniform-w and catch-all-cap describe the global build. These
            # are byte-identical to today when no city candidate fires (rho=0). uniform_mixture_w_applied
            # is stamped ONLY when the mixture actually fired (exactly the prior in-line semantics).
            if _uniform_applied_global:
                uniform_mixture_w_applied = _uniform_w
            settlement_sigma_floor_catchall_capped = tuple(_capped_global)

            # CAPITAL-GATED PER-CITY rho-MIX (2026-06-29). The city candidate (k_eb, w_eb, score_capital)
            # is read SEPARATELY from the global pair (NO hard swap — the prior swap harmed ~40% of
            # cities). When the city earned POSITIVE out-of-sample score capital C, serve the NON-
            # INFERIORITY mixture q_serve = (1-rho)*q_global + rho*q_city with rho = 1-exp(-C/W). W is the
            # eligible Bernoulli bin count over the SAME bin set q is built over (day0 uses the same
            # `> 0` eligibility the uniform mixture uses). rho is a CALIBRATION WEIGHT derived AUTOMATICALLY
            # from capital — never a manual flag / cap / allowlist. C<=0 or no candidate ⇒ rho=0 ⇒ q is
            # exactly q_global (byte-identical to today). The city carrier's σ ladder is computed at the
            # city (k_eb, floor) so its bounds integrate at the matching predictive width.
            _city_cand = (
                None
                if _current_shape is not None
                else _replacement_city_candidate_lookup(
                    _city_unit, getattr(request, "city", None)
                )
            )
            _city_sigma_used: float | None = None
            _city_rho: float = 0.0
            q = q_global
            if _city_cand is not None:
                _k_eb = float(_city_cand["k"])
                _w_eb = float(_city_cand["w"])
                _cap = float(_city_cand["score_capital"])
                # W = eligible bin count over THIS family batch (the bin set q is built over). For day0
                # use the same `> 0` eligibility the uniform-mixture pedestal uses; otherwise all bins.
                _eligible_for_W = (
                    [b for b in q_global if float(q_global[b]) > 0.0]
                    if _day0_obs_extreme_c is not None
                    else list(q_global)
                )
                _W = len(_eligible_for_W)
                _city_rho = _city_rho_from_capital(_cap, _W)
                if _city_rho > 0.0:
                    q_city, _capped_city, _ = _build_scaled_normal_uniform_q(
                        mu=_mu_anchor,
                        sigma_pred=_sigma_pred_raw,
                        k=_k_eb,
                        uniform_w=_w_eb,
                        floor_steps=_floor_steps,
                        bins=request.bins,
                        half_step=_half_step,
                        rounding_rule=_rounding_rule,
                        day0_obs_extreme_c=_day0_obs_extreme_c,
                        settlement_step_c=float(request.settlement_step_c),
                        settlement_sigma_floor_c=settlement_sigma_floor_c,
                        city_unit=_city_unit,
                        metric=metric,
                    )
                    if set(q_city) == set(q_global):
                        q = _mix_q_by_rho(q_global, q_city, _city_rho, renormalize=True)
                        _city_sigma_used = _resolve_sigma_used(_k_eb, _floor_steps)
                        city_calibration_layer_applied = True
                        city_calibration_rho = float(_city_rho)
                        city_score_capital = _cap
                        city_k_eb = _k_eb
                        city_w_eb = _w_eb
            q_shape = (
                "fused_day0_conditioned_normal"
                if _day0_obs_extreme_c is not None
                else "fused_normal_direct"
            )
            # Q_LCB / Q_UCB (2026-06-09) — fused-center parameter-uncertainty bootstrap. INDEPENDENT
            # fail-soft: a bound-construction error must NOT roll back the fused q point (that would
            # regress the q_shape gain). On error the certified bootstrap bounds are absent and a
            # loud WARNING is emitted; the later soft-anchor Wilson fallback may publish
            # non-certified carrier bounds under its own basis. replacement_q_mode/q_shape remain
            # diagnosable. The bounds use the SAME _sigma_used the point q integrates at
            # (settlement-floored if the floor applied) so q_lcb <= q_point <= q_ucb holds per bin;
            # center uncertainty is fused.sd (anchor_sigma_c), NOT sigma_resid (already inside
            # _sigma_used) — no double-count.
            #
            # CAPITAL-GATED rho-MIX BOUNDS (2026-06-29): the persisted bounds must reflect the SERVED
            # mixture, never leave q_lcb on pure-global while the q point is mixed. CHOICE — carrier-
            # level mixing: build the GLOBAL bound carriers (at the global σ, q_point=q_global) and, when
            # a city mix fires, the CITY bound carriers (at the city σ, q_point=q_city), then mix each by
            # the SAME rho: q_lcb_serve = (1-rho)*q_lcb_global + rho*q_lcb_city (and q_ucb likewise),
            # renormalize=False (bounds are NOT a simplex). The mixed bounds are then re-clipped to the
            # SERVED q per bin (q_lcb ≤ q_point ≤ q_ucb) and the far-tail honesty is re-applied against
            # the served q, so the persisted bounds are coherent with the served point. rho=0 ⇒ only the
            # global carriers are built and the result is byte-identical to today. The bootstrap SAMPLE
            # substrate is mixed row-by-row with that same rho, so point q, bounds, empirical
            # edge confidence, and downstream CVaR all describe one probability
            # world.  Serving global-only draws beside a city-mixed q is forbidden.
            try:
                _lcb_g, _ucb_g, _samples_g = _build_fused_q_bounds(
                    mu_star=_mu_anchor,
                    center_sigma_c=float(bayes_precision_fusion_override.anchor_sigma_c),
                    predictive_sigma_c=_sigma_used,
                    bins=request.bins,
                    half_step=_half_step,
                    q_point=q_global,
                    rounding_rule=_rounding_rule,
                    day0_observed_extreme_c=_day0_obs_extreme_c,
                    day0_metric=metric,
                    evidence_members_c=_finite_evidence_members_c,
                    return_samples=True,
                )
                if _city_sigma_used is not None and _city_rho > 0.0:
                    _lcb_c, _ucb_c, _samples_c = _build_fused_q_bounds(
                        mu_star=_mu_anchor,
                        center_sigma_c=float(bayes_precision_fusion_override.anchor_sigma_c),
                        predictive_sigma_c=_city_sigma_used,
                        bins=request.bins,
                        half_step=_half_step,
                        q_point=q_city,
                        rounding_rule=_rounding_rule,
                        day0_observed_extreme_c=_day0_obs_extreme_c,
                        day0_metric=metric,
                        evidence_members_c=_finite_evidence_members_c,
                        return_samples=True,
                    )
                    _lcb_map = _mix_q_by_rho(_lcb_g, _lcb_c, _city_rho, renormalize=False)
                    _ucb_map = _mix_q_by_rho(_ucb_g, _ucb_c, _city_rho, renormalize=False)
                    # Re-clip the mixed bounds to the SERVED q per bin and re-apply far-tail honesty, so
                    # q_lcb ≤ q_point ≤ q_ucb holds against the served point (each carrier was clipped to
                    # its OWN q_point; the convex mix needs a final clip to the served q).
                    for _bid in list(_lcb_map):
                        _qpt = float(q.get(_bid, 0.0))
                        _lo = min(max(_lcb_map[_bid], 0.0), max(_qpt, 0.0))
                        if (
                            _finite_evidence_member_count is None
                            and _qpt < FAR_TAIL_Q_POINT_THRESH
                        ):
                            _lo = min(_lo, FAR_TAIL_LCB_FLOOR)
                        _lcb_map[_bid] = _lo
                        _ucb_map[_bid] = max(
                            _ucb_map.get(_bid, _qpt),
                            _qpt,
                            (_finite_evidence_ucb_floor_by_bin or {}).get(_bid, 0.0),
                        )
                    q_bootstrap_samples_by_bin = _mix_q_samples_by_rho(
                        _samples_g,
                        _samples_c,
                        _city_rho,
                    )
                else:
                    _lcb_map, _ucb_map = _lcb_g, _ucb_g
                    q_bootstrap_samples_by_bin = _samples_g
                q_lcb_map = _lcb_map
                q_ucb_map = _ucb_map
                q_lcb_basis = _QLCB_BASIS
                # FAR-TAIL HONESTY PROVENANCE (2026-06-22): count how many bins had their
                # q_lcb capped by the far-tail honesty (q_point < FAR_TAIL_Q_POINT_THRESH
                # and raw lcb > FAR_TAIL_LCB_FLOOR → capped to FAR_TAIL_LCB_FLOOR).
                # This is a plain fact of the LIVE value: True (non-zero count) when at
                # least one far-tail bin was capped; False / 0 when the data had no far-
                # tail bins (identity for all bins). Recorded in provenance_payload below.
                # We re-derive from the final q_lcb_map + SERVED q_point dict: a bin was capped
                # iff its q_lcb == FAR_TAIL_LCB_FLOOR AND q_point < FAR_TAIL_Q_POINT_THRESH
                # (the cap is min(lcb, FLOOR) so equality holds when the floor bit). A bin
                # where q_point < THRESH but lcb was already ≤ FLOOR before the cap is also
                # counted (the floor was effectively applied).
                if _finite_evidence_member_count is None:
                    _far_tail_honesty_count = sum(
                        1 for _bid, _lcb in _lcb_map.items()
                        if float(q.get(_bid, 1.0)) < FAR_TAIL_Q_POINT_THRESH
                        and _lcb <= FAR_TAIL_LCB_FLOOR + 1e-12
                    )
                if _finite_evidence_ucb_floor_by_bin is not None:
                    _finite_evidence_tail_bin_count = sum(
                        1
                        for _bid in _ucb_map
                        if float(q.get(_bid, 1.0))
                        < _finite_evidence_ucb_floor_by_bin[_bid]
                    )
            except Exception as _qexc:
                q_lcb_map = None
                q_ucb_map = None
                q_bootstrap_samples_by_bin = None
                q_lcb_basis = None
                try:
                    import logging  # noqa: PLC0415
                    logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                        "replacement_0_1 q_lcb/q_ucb bootstrap skipped "
                        "(fail-soft to non-certified Wilson fallback when available): %s",
                        _qexc,
                    )
                except Exception:
                    pass
            # FIX 1 — FULL vs PARTIAL. The fused Normal is the constructed shape either way (so the
            # live gate admits both); PARTIAL records a degraded fusion (the K3 decorrelated-provider
            # set was INCOMPLETE — reuses the override's verdict, not a parallel check). Wave-2 item 6
            # (2026-06-12): the former settlement-floor-REQUIRED mode-degrade (edli_settlement_sigma_
            # floor_required, permanently false) is DELETED — a missing per-cell floor never degrades
            # the mode; floor application is purely data-availability driven above.
            # PR#403 FIX (2026-06-09): bounds required for live eligibility. FUSED_NORMAL_FULL/PARTIAL
            # now REQUIRES both q_lcb_map and q_ucb_map successfully built. Bounds failure degrades
            # to FUSED_NORMAL_BOUNDS_MISSING — the point q is fine (blocked-candidate accrual continues) but the
            # live gate will reject this mode. This kills the two-measures disease: fused-Normal q
            # point + Wilson LCB authority = two incompatible regimes, exactly the Milan root cause.
            if q_lcb_map is None or q_ucb_map is None:
                replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_NORMAL_BOUNDS_MISSING
            elif bayes_precision_fusion_override.decorrelated_providers_complete:
                replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL
            else:
                replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL
        except Exception as _exc:
            # FIX 1 — the fused-q construction itself raised and fails CLOSED.
            # The mode records that a fused-q was attempted and failed, so the live gate rejects
            # it with a specific failure mode.
            replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_Q_BUILD_FAILED
            settlement_sigma_floor_applied = False
            settlement_sigma_floor_c = None
            replacement_sigma_basis = None
            settlement_sigma_floor_catchall_capped = ()
            # The fused-q (incl. any sigma-scale / uniform-mixture / sigma-floor / city rho-mix) was
            # discarded. Reset every calibration-layer provenance field so none misreports on the
            # retained non-live q.
            sigma_scale_k_applied = None
            uniform_mixture_w_applied = None
            sigma_floor_steps_applied = None
            city_calibration_layer_applied = False
            city_calibration_rho = None
            city_score_capital = None
            city_k_eb = None
            city_w_eb = None
            # T0-1: the corrected Day0 center was discarded with the fused q — the
            # delta must not stamp provenance on a q it did not shape.
            _day0_center_delta_c = 0.0
            _day0_center_vector_id = None
            _day0_center_hours_remaining = None
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 fused-q shape skipped (fail-closed to fused-center-only / skip): %s",
                    _exc,
                )
            except Exception:
                pass
    bin_topology_payload = _bin_topology_payload(request.bins, settlement_step_c=float(request.settlement_step_c))
    bin_topology_hash = _json_hash(bin_topology_payload)
    dependency_payload = {
        "baseline_b0": request.baseline_source_run_id,
        "openmeteo_ifs9_anchor": request.openmeteo_source_run_id,
    }
    if (
        bayes_precision_fusion_override is not None
        and bayes_precision_fusion_override.current_evidence_shape is not None
    ):
        dependency_payload["current_ensemble_snapshot"] = int(
            bayes_precision_fusion_override.current_evidence_shape["snapshot_id"]
        )
        dependency_payload["current_evidence_shape_hash"] = str(
            bayes_precision_fusion_override.current_evidence_shape["shape_hash"]
        )
    dependency_hash = _json_hash(dependency_payload)
    posterior_config = {
        "posterior_method": "openmeteo_ecmwf_ifs9_bayes_fusion",
        "anchor_weight": float(request.anchor_weight),
        "anchor_sigma_c": float(request.anchor_sigma_c),
        "settlement_step_c": float(request.settlement_step_c),
    }
    _posterior_day0_observed_extreme_c = (
        _day0_absorbing_observed_extreme_c(request)
        if _target_local_day_has_started(request)
        else None
    )
    _posterior_day0_provisional_extreme_c = (
        _day0_observed_extreme_c(request)
        if _target_local_day_has_started(request)
        and _posterior_day0_observed_extreme_c is None
        else None
    )
    if _posterior_day0_observed_extreme_c is not None:
        posterior_config.update(
            {
                "day0_conditioning": True,
                "day0_observed_extreme_c": float(_posterior_day0_observed_extreme_c),
                "day0_observed_extreme_source": str(request.day0_observed_extreme_source or ""),
                "day0_observed_extreme_observation_time": (
                    None
                    if request.day0_observed_extreme_observation_time is None
                    else str(request.day0_observed_extreme_observation_time)
                ),
            }
        )
    elif _posterior_day0_provisional_extreme_c is not None:
        posterior_config["day0_provisional_observation"] = {
            "observed_extreme_c": float(_posterior_day0_provisional_extreme_c),
            "source": str(request.day0_observed_extreme_source or ""),
            "observation_time": (
                None
                if request.day0_observed_extreme_observation_time is None
                else str(request.day0_observed_extreme_observation_time)
            ),
            "support_truncation": False,
        }
    if bayes_precision_fusion_override is not None:
        # F6: the FUSED product gets its OWN EMOS cell identity (product + resolution_mix_hash +
        # model_set_hash + lead_bucket) so it never reuses the single-anchor EMOS cell. The fused
        # center/spread REPLACE the OM9 anchor, so posterior_config_hash diverges from the
        # single-anchor cell by construction.
        posterior_config.update(
            {
                "posterior_method": "the_path_bayes_precision_fusion",
                "bayes_precision_fusion_method": bayes_precision_fusion_override.method,
                "bayes_precision_fusion_product_id": "the_path_bayes_precision_fusion_v1",
                "bayes_precision_fusion_model_set_hash": bayes_precision_fusion_override.model_set_hash,
                "bayes_precision_fusion_resolution_mix_hash": bayes_precision_fusion_override.resolution_mix_hash,
                "bayes_precision_fusion_lead_bucket": bayes_precision_fusion_override.lead_bucket,
                "bayes_precision_fusion_anchor_value_c": float(bayes_precision_fusion_override.anchor_value_c),
                "bayes_precision_fusion_anchor_sigma_c": float(bayes_precision_fusion_override.anchor_sigma_c),
                # Option C (§7): the RAW-precision basis hash makes the served center
                # reconstructible even when geometry variances change the weights under the
                # same model set (model_set_hash alone does not prove the precision basis).
                # None when no override basis was computed.
                "bayes_precision_fusion_precision_basis_hash": (
                    bayes_precision_fusion_override.precision_basis_hash
                ),
                "current_evidence_shape_hash": (
                    None
                    if bayes_precision_fusion_override.current_evidence_shape is None
                    else str(
                        bayes_precision_fusion_override.current_evidence_shape[
                            "shape_hash"
                        ]
                    )
                ),
            }
        )
    posterior_config_hash = _json_hash(posterior_config)
    family_id = f"{request.city}:{target_date}:{metric}:{bin_topology_hash}"
    # FIX 5 (2026-06-09) — capture-status provenance (recording only; the FIX-1 live gate is the
    # enforcement point, and BAYES_PRECISION_FUSION_CAPTURE_MISSING already covers the dangerous no-override case).
    # Derived from the SAME K3 completeness verdict the fusion computed (no parallel re-derivation):
    #   FULL_CURRENT     — override present AND all 5 decorrelated providers' current values served.
    #   PARTIAL_CURRENT  — override present but the decorrelated set was INCOMPLETE (count present).
    #   STALE_HISTORY_ONLY — no current live carrier could be built.
    # DB_READ_ERROR is reserved for an explicit DB read failure surfaced by the capture reader; the
    # override layer is fail-soft (returns None) so at this seam an absent override reads as
    # STALE_HISTORY_ONLY (the live gate rejects it via BAYES_PRECISION_FUSION_CAPTURE_MISSING regardless).
    if bayes_precision_fusion_override is None:
        capture_status = REPLACEMENT_CAPTURE_STATUS_STALE_HISTORY_ONLY
    elif bayes_precision_fusion_override.decorrelated_providers_complete:
        capture_status = REPLACEMENT_CAPTURE_STATUS_FULL_CURRENT
    else:
        capture_status = REPLACEMENT_CAPTURE_STATUS_PARTIAL_CURRENT
    # CYCLE-PHASE PROVENANCE. 00/06/12/18Z are live runtime cycles; this tag is provenance only,
    # never a live/experiment switch.
    cycle_phase = classify_cycle_phase(_to_utc(request.source_cycle_time, field_name="source_cycle_time"))
    live_layer = _replacement_is_live_layer(
        replacement_q_mode=replacement_q_mode,
        q_lcb_map=q_lcb_map,
        q_ucb_map=q_ucb_map,
        q_lcb_basis=q_lcb_basis,
    )
    # Shared provider/center provenance carried on the result either way so a
    # read-only caller can audit belief width / honesty even when not live.
    _mu_star = (
        float(bayes_precision_fusion_override.anchor_value_c)
        if bayes_precision_fusion_override is not None
        else None
    )
    _pred_sigma = (
        bayes_precision_fusion_override.predictive_sigma_c
        if bayes_precision_fusion_override is not None
        else None
    )
    _prov_complete = bool(
        bayes_precision_fusion_override.decorrelated_providers_complete
    ) if bayes_precision_fusion_override is not None else False
    _prov_served = int(
        bayes_precision_fusion_override.decorrelated_providers_served
    ) if bayes_precision_fusion_override is not None else 0
    _prov_expected = int(
        bayes_precision_fusion_override.decorrelated_providers_expected
    ) if bayes_precision_fusion_override is not None else 0
    if not live_layer:
        # Historical write path returned None here (no row, no provenance). The
        # read path needs the computed values to decide; return the struct flagged
        # not-eligible. ``_insert_posterior`` maps this to None (byte-identical).
        return _PosteriorComputeResult(
            live_eligible=False,
            q=q,
            q_lcb_map=q_lcb_map,
            q_ucb_map=q_ucb_map,
            mu_star=_mu_star,
            predictive_sigma_c=(None if _pred_sigma is None else float(_pred_sigma)),
            decorrelated_providers_complete=_prov_complete,
            decorrelated_providers_served=_prov_served,
            decorrelated_providers_expected=_prov_expected,
            capture_status=capture_status,
            replacement_q_mode=replacement_q_mode,
            data_version=data_version,
            source_cycle_time=source_cycle_time,
            available_at=available_at,
            computed_at=computed_at,
            runtime_layer=None,
            dependency_payload=dependency_payload,
            dependency_hash=dependency_hash,
            bin_topology_hash=bin_topology_hash,
            posterior_config_hash=posterior_config_hash,
            family_id=family_id,
            provenance_payload=None,
        )
    runtime_layer = LIVE_RUNTIME_LAYER
    if bayes_precision_fusion_override is not None:
        _prov_anchor_value_c = float(bayes_precision_fusion_override.anchor_value_c)
    else:
        _prov_anchor_value_c = None
    provenance_payload = {
        "anchor_weight": request.anchor_weight,
        "anchor_sigma_c": request.anchor_sigma_c,
        "anchor_value_c": _prov_anchor_value_c,
        "runtime_layer": runtime_layer,
        "cycle_phase": cycle_phase,
        "openmeteo_anchor_artifact_id": request.anchor_artifact_id,
        "openmeteo_precision_guard": _precision_guard_payload(request.openmeteo_precision_guard),
        "q_point_json_role": "live_point_probability",
        "q_shape": q_shape,
        # FIX 1 (2026-06-09): explicit q-mode authority — the live gate reads THIS, not the q_shape
        # string. FUSED_NORMAL_{FULL,PARTIAL} are live-eligible; every other mode is no-submit.
        "replacement_q_mode": replacement_q_mode,
        # FIX 2 (2026-06-09): settlement sigma-floor coherence in the fused-q path.
        "settlement_sigma_floor_applied": settlement_sigma_floor_applied,
        "settlement_sigma_floor_c": settlement_sigma_floor_c,
        "settlement_sigma_floor_unavailable_reason": floor_unavailable_reason,
        "replacement_sigma_basis": replacement_sigma_basis,
        # C3 calibration surface 2026-06-12 — FITTED σ scale + uniform-mixture provenance (一切可被溯源).
        # Both None when inert (artifact missing / family unfitted / k=1.0,w=0.0). Float applied values
        # when the correction fired. Source: state/sigma_scale_fit.json (MLE, operator law 2026-06-12).
        # Authority: docs/archive/2026-Q2/operations_historical/c3_sigma_calibration_surface_2026-06-12.md
        "sigma_scale_k_applied": sigma_scale_k_applied,
        "uniform_mixture_w_applied": uniform_mixture_w_applied,
        # FITTED absolute σ-floor (step units) provenance (σ-refit report 2026-06-13, task #69). None
        # when inert (live artifact has no floor_steps key, or the floor did not bind); the applied
        # floor_steps when σ_core was lifted to floor_steps·step. Same artifact family entry as k/w.
        "sigma_floor_steps_applied": sigma_floor_steps_applied,
        # Catch-all exemption (2026-06-10): open-ended bins whose floored mass was capped at the
        # un-floored predictive-sigma mass (the floor may only flatten, never inflate a catch-all).
        "settlement_sigma_floor_catchall_capped": list(settlement_sigma_floor_catchall_capped),
        # CAPITAL-GATED PER-CITY rho-MIX provenance (2026-06-29). The served q is a non-inferiority
        # mixture q_serve = (1-rho)*q_global + rho*q_city with rho = 1-exp(-C/W) (C = the city's earned
        # OOS score capital, W = the eligible Bernoulli bin count). False/None when no city candidate
        # fired (rho=0 ⇒ pure global ⇒ byte-identical to today). When applied, rho/C and the served city
        # (k_eb, w_eb) make the mixture reconstructible. Source: state/sigma_scale_fit.json cities layer.
        "city_calibration_layer_applied": city_calibration_layer_applied,
        "city_calibration_rho": city_calibration_rho,
        "city_score_capital": city_score_capital,
        "city_k_eb": city_k_eb,
        "city_w_eb": city_w_eb,
        # FIX 5 (2026-06-09): capture-status provenance (recording only).
        "capture_status": capture_status,
        # FAR-TAIL q_lcb HONESTY provenance (2026-06-22): plain fact of the live value.
        # True when >= 1 far-tail bin (q_point < FAR_TAIL_Q_POINT_THRESH) had its q_lcb
        # capped at FAR_TAIL_LCB_FLOOR. The count is also stored for telemetry.
        # Authority: docs/evidence/live_order_pathology/2026-06-22_qlcb_lowerbound_honesty.md
        "q_lcb_far_tail_honesty_applied": _far_tail_honesty_count > 0,
        "q_lcb_far_tail_honesty_bin_count": _far_tail_honesty_count,
        # Current finite-evidence ambiguity: exact settlement-preimage member
        # hits produce a Clopper-Pearson UCB, then current moment ambiguity may
        # widen it. This is written into the coherent carrier, so BUY NO consumes
        # the exact complement instead of an unearned near-one probability.
        "finite_evidence_tail_band_applied": _finite_evidence_tail_bin_count > 0,
        "finite_evidence_member_count": _finite_evidence_member_count,
        "finite_evidence_member_hits_by_bin": _finite_evidence_member_hits_by_bin,
        # Member-dependence effective-n provenance (2026-07-17, consult v2 (f)):
        # the fitted ICC rho and the Kish n_eff = n/(1+(n-1)rho) applied inside the
        # Clopper-Pearson bound. Both None when the artifact is absent or rho=0
        # (exact integer CP — byte-identical pre-artifact behavior).
        "finite_evidence_member_rho_applied": _finite_evidence_member_rho_applied,
        "finite_evidence_member_n_eff": _finite_evidence_member_n_eff,
        "finite_evidence_zero_hit_ucb_floor": _finite_evidence_ucb_floor,
        "finite_evidence_tail_ucb_floor_by_bin": _finite_evidence_ucb_floor_by_bin,
        "finite_evidence_tail_bin_count": _finite_evidence_tail_bin_count,
        # Q_LCB / Q_UCB provenance. The role is BASIS-AWARE so the certified fused-center bootstrap
        # bound and any legacy fallback bound never alias: only the
        # bootstrap basis carries the calibration credential (event_reactor_adapter basis-exact
        # match). The percentile vectors do NOT sum to 1 (expected for bounds; require_sum=False).
        # q_lcb_map is now NULL only on a true fail-soft (the Wilson fallback itself raised).
        "q_lcb_json_role": (
            "fused_center_bootstrap_lcb"
            if q_lcb_basis == _QLCB_BASIS
            else "absent_no_calibrated_lcb_available"
        ),
        # q_ucb role is BASIS-AWARE, symmetric with q_lcb_json_role: the soft-anchor Wilson upper
        # bound (built alongside its lower twin when the fused-center bootstrap did not run) must
        # NOT be mislabeled as the certified bootstrap ucb. q_ucb is published only when q_lcb was
        # (atomic both-or-neither per path), so the basis fully determines the role.
        "q_ucb_json_role": (
            "fused_center_bootstrap_ucb"
            if (q_ucb_map is not None and q_lcb_basis == _QLCB_BASIS)
            else "absent_no_calibrated_ucb_available"
        ),
        "q_lcb_basis": q_lcb_basis,
        # bootstrap_draws is meaningful ONLY for the bootstrap basis; the Wilson member-vote bound
        # is analytic (no draws) -> None.
        "q_lcb_bootstrap_draws": (_QLCB_BOOTSTRAP_DRAWS if q_lcb_basis == _QLCB_BASIS else None),
        # Empirical edge-confidence substrate. The live adapter computes
        # p_value = (1 + count(q_side_draw - native_cost <= 0)) / (1 + draws)
        # from these exact draws instead of laundering the robust LCB pass/fail
        # gate as a fake {0,1} FDR p-value. Present only for the fused-center
        # bootstrap basis.
        "q_bootstrap_samples_by_bin": (
            q_bootstrap_samples_by_bin if q_lcb_basis == _QLCB_BASIS else None
        ),
        "q_bootstrap_samples_basis": (
            "served_rho_mixed_simplex_v2"
            if q_lcb_basis == _QLCB_BASIS and city_calibration_layer_applied
            else (
                "global_simplex_current_finite_moment_evidence_v3"
                if (
                    q_lcb_basis == _QLCB_BASIS
                    and _finite_evidence_tail_bin_count > 0
                )
                else "global_simplex_v1"
                if q_lcb_basis == _QLCB_BASIS
                else None
            )
        ),
        "q_bootstrap_samples_hash": (
            _json_hash(q_bootstrap_samples_by_bin)
            if q_lcb_basis == _QLCB_BASIS and q_bootstrap_samples_by_bin is not None
            else None
        ),
        "bin_topology": bin_topology_payload,
        "bin_topology_hash": bin_topology_hash,
        "dependency_hash": dependency_hash,
        "posterior_config_hash": posterior_config_hash,
        "family_id": family_id,
        "runtime_policy_status": runtime_layer,
        "training_allowed": False,
    }
    if _posterior_day0_observed_extreme_c is not None:
        provenance_payload["day0_conditioning"] = {
            "active": True,
            "metric": metric,
            "observed_extreme_c": float(_posterior_day0_observed_extreme_c),
            "source": request.day0_observed_extreme_source,
            "observation_time": (
                None
                if request.day0_observed_extreme_observation_time is None
                else str(request.day0_observed_extreme_observation_time)
            ),
            "sample_count": request.day0_observed_extreme_sample_count,
            "unit": request.day0_observed_extreme_unit,
            "conditioned_random_variable": (
                "max(observed_high_so_far, remaining_distribution)"
                if metric == "high"
                else "min(observed_low_so_far, remaining_distribution)"
            ),
        }
        # T0-1 remaining-window center correction (audit §7 2026-07-18): stamped ONLY
        # when the delta fired; absent = inert (byte-identical provenance otherwise).
        if _day0_center_delta_c > 0.0:
            provenance_payload["day0_remaining_center_delta_c"] = float(_day0_center_delta_c)
            provenance_payload["day0_remaining_vector_id"] = _day0_center_vector_id
            provenance_payload["day0_remaining_hours"] = _day0_center_hours_remaining
    elif _posterior_day0_provisional_extreme_c is not None:
        provenance_payload["day0_provisional_observation"] = {
            "active": True,
            "metric": metric,
            "observed_extreme_c": float(_posterior_day0_provisional_extreme_c),
            "source": request.day0_observed_extreme_source,
            "observation_time": (
                None
                if request.day0_observed_extreme_observation_time is None
                else str(request.day0_observed_extreme_observation_time)
            ),
            "sample_count": request.day0_observed_extreme_sample_count,
            "unit": request.day0_observed_extreme_unit,
            "support_truncation": False,
        }
    # Task #32: honest re-materialization provenance ON THE POSTERIOR. The first threading
    # placed this only on the anchor provenance dict — but the anchor INSERT is OR-IGNOREd on a
    # same-cycle re-materialization (the existing anchor row wins), so the note never surfaced.
    # The posterior row is the artifact the upgrade actually produces; the note belongs here.
    if request.upgrade_trigger:
        provenance_payload["upgrade_trigger"] = str(request.upgrade_trigger)
    if bayes_precision_fusion_override is not None:
        provenance_payload["bayes_precision_fusion"] = {
            "method": bayes_precision_fusion_override.method,
            "used_models": list(bayes_precision_fusion_override.used_models),
            "model_set_hash": bayes_precision_fusion_override.model_set_hash,
            "resolution_mix_hash": bayes_precision_fusion_override.resolution_mix_hash,
            "lead_bucket": bayes_precision_fusion_override.lead_bucket,
            "anchor_value_c": float(bayes_precision_fusion_override.anchor_value_c),
            "anchor_sigma_c": float(bayes_precision_fusion_override.anchor_sigma_c),
            "predictive_sigma_c": (
                None if bayes_precision_fusion_override.predictive_sigma_c is None
                else float(bayes_precision_fusion_override.predictive_sigma_c)
            ),
            "dropped_models": list(bayes_precision_fusion_override.dropped_models),
            "excluded_regionals": list(bayes_precision_fusion_override.excluded_regionals),
            "dropped_aliases": list(bayes_precision_fusion_override.dropped_aliases),
            # BLOCKER 5: the persisted current rows this traded q was fused from (reconstructable).
            "raw_model_forecast_ids": list(bayes_precision_fusion_override.raw_model_forecast_ids),
            # BLOCKER 3: the ifs025->ifs9 anchor bridge provenance applied to the anchor prior.
            "anchor_bridge": dict(bayes_precision_fusion_override.anchor_bridge) if bayes_precision_fusion_override.anchor_bridge else None,
            # FIX 1/FIX 5 (2026-06-09): the K3 decorrelated-provider completeness verdict (the SAME
            # determination that drives replacement_q_mode FULL vs PARTIAL + capture_status).
            "decorrelated_providers_complete": bool(bayes_precision_fusion_override.decorrelated_providers_complete),
            "decorrelated_providers_served": int(bayes_precision_fusion_override.decorrelated_providers_served),
            "decorrelated_providers_expected": int(bayes_precision_fusion_override.decorrelated_providers_expected),
            # Task #32 follow-up (brand law): per-instrument serving record — which endpoint
            # served each fused model's CURRENT value (served_via), the served row id / cycle /
            # capture stamp / age_hours, and its lead bucket. A previous_runs substitution (a
            # provider structurally unpublished on this cycle's single_runs, e.g. JMA at 06Z)
            # is RECORDED here, never silent.
            "current_value_serving": (
                {m: dict(v) for m, v in bayes_precision_fusion_override.current_value_serving.items()}
                if bayes_precision_fusion_override.current_value_serving
                else None
            ),
            "source_clock_one_scheme": (
                dict(bayes_precision_fusion_override.source_clock_one_scheme)
                if bayes_precision_fusion_override.source_clock_one_scheme
                else None
            ),
            "current_evidence_shape": (
                dict(bayes_precision_fusion_override.current_evidence_shape)
                if bayes_precision_fusion_override.current_evidence_shape
                else None
            ),
            # Low-n prior weighting provenance: these models still entered the center,
            # but their raw second moment was shrunk toward the equal-precision prior.
            "low_n_prior_weighted_models": list(bayes_precision_fusion_override.low_n_prior_weighted_models),
            "runtime_layer": runtime_layer,
        }
    return _PosteriorComputeResult(
        live_eligible=True,
        q=q,
        q_lcb_map=q_lcb_map,
        q_ucb_map=q_ucb_map,
        mu_star=_mu_star,
        predictive_sigma_c=(None if _pred_sigma is None else float(_pred_sigma)),
        decorrelated_providers_complete=_prov_complete,
        decorrelated_providers_served=_prov_served,
        decorrelated_providers_expected=_prov_expected,
        capture_status=capture_status,
        replacement_q_mode=replacement_q_mode,
        data_version=data_version,
        source_cycle_time=source_cycle_time,
        available_at=available_at,
        computed_at=computed_at,
        runtime_layer=runtime_layer,
        dependency_payload=dependency_payload,
        dependency_hash=dependency_hash,
        bin_topology_hash=bin_topology_hash,
        posterior_config_hash=posterior_config_hash,
        family_id=family_id,
        provenance_payload=provenance_payload,
    )


def _write_posterior_row(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    anchor_id: int,
    result: "_PosteriorComputeResult",
) -> int:
    """INSERT a live-eligible ``_PosteriorComputeResult`` into ``forecast_posteriors``.

    Extracted from ``_insert_posterior`` so ``materialize_replacement_forecast_live``
    can hold the compute ``result`` (needed for typed BLOCKED sub-reasons) without
    computing it twice. Caller MUST have already checked ``result.live_eligible``.
    """
    target_date = _date_text(request.target_date)
    data_version = result.data_version
    source_cycle_time = result.source_cycle_time
    available_at = result.available_at
    computed_at = result.computed_at
    q = result.q
    q_lcb_map = result.q_lcb_map
    q_ucb_map = result.q_ucb_map
    dependency_payload = result.dependency_payload
    dependency_hash = result.dependency_hash
    bin_topology_hash = result.bin_topology_hash
    posterior_config_hash = result.posterior_config_hash
    family_id = result.family_id
    provenance_payload = result.provenance_payload
    runtime_layer = result.runtime_layer
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
            "q_bootstrap_samples_hash": provenance_payload.get(
                "q_bootstrap_samples_hash"
            ),
            "q_bootstrap_samples_basis": provenance_payload.get(
                "q_bootstrap_samples_basis"
            ),
            "dependency_hash": dependency_hash,
            "bin_topology_hash": bin_topology_hash,
            "posterior_config_hash": posterior_config_hash,
            "anchor_id": anchor_id,
            "anchor_artifact_id": request.anchor_artifact_id,
        }
    )
    try:
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, product_id, data_version, city, target_date,
                temperature_metric, source_cycle_time, source_available_at,
                computed_at, q_json, q_lcb_json, q_ucb_json, posterior_method,
                openmeteo_anchor_id,
                dependency_source_run_ids_json, family_id, bin_topology_hash,
                dependency_hash, posterior_config_hash, posterior_identity_hash,
                provenance_json,
                runtime_layer, training_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                anchor_id,
                _json(dependency_payload),
                family_id,
                bin_topology_hash,
                dependency_hash,
                posterior_config_hash,
                posterior_identity_hash,
                _json(provenance_payload),
                runtime_layer,
                0,
            ),
        )
    except sqlite3.IntegrityError as exc:
        row = conn.execute(
            """
            SELECT posterior_id FROM forecast_posteriors
            WHERE posterior_identity_hash = ?
            """,
            (posterior_identity_hash,),
        ).fetchone()
        if row is not None:
            return int(row[0] if not isinstance(row, sqlite3.Row) else row["posterior_id"])
        raise RuntimeError(f"forecast_posteriors insert rejected: {exc}") from exc
    row = conn.execute(
        """
        SELECT posterior_id FROM forecast_posteriors
        WHERE posterior_identity_hash = ?
        """,
        (posterior_identity_hash,),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "forecast_posteriors insert returned no row for posterior_identity_hash="
            f"{posterior_identity_hash}"
        )
    new_posterior_id = int(row[0] if not isinstance(row, sqlite3.Row) else row["posterior_id"])
    # W0.2 input->q_version latency metric (measure only, no gate). Only the
    # fresh-insert success path reaches here — the IntegrityError dedup branch
    # above returns earlier and does not double-count an existing row's latency.
    emit_materialization_latency(
        family_id=family_id,
        city=request.city,
        target_date=target_date,
        temperature_metric=metric,
        source_cycle_time=source_cycle_time,
        source_available_at=available_at,
        computed_at=computed_at,
        posterior_id=new_posterior_id,
    )
    return new_posterior_id


def _build_readiness(
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    posterior_id: int,
    anchor_id: int,
):
    expected = expected_replacement_dependency_identity_by_role(metric)
    computed_at = _to_utc(request.computed_at, field_name="computed_at")
    # SINGLE freshness authority (operator directive 2026-06-11, RULE-1 twin-clock
    # incident): readiness expiry derives from the cycle's staleness bound, never a
    # second guessed clock (the old computed_at+3h killed lawful 26h-old data live).
    from src.data.replacement_forecast_cycle_policy import (  # noqa: PLC0415
        replacement_readiness_expires_at,
    )

    expires_at = (
        _to_utc(request.expires_at, field_name="expires_at")
        if request.expires_at is not None
        else replacement_readiness_expires_at(
            _to_utc(request.source_cycle_time, field_name="source_cycle_time")
        )
    )
    dependencies: list[ReplacementForecastDependency] = [
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id=expected["baseline_b0"].source_id,
            product_id=expected["baseline_b0"].product_id,
            data_version=request.baseline_data_version,
            source_run_id=request.baseline_source_run_id,
            source_available_at=request.baseline_source_available_at,  # AVAIL-POSSESSION-EXEMPTED: forwards the request's per-role possession input into the dependency lineage record (passthrough of an already-determined value, not a fresh stamp)
        ),
    ]
    dependencies.extend(
        [
            ReplacementForecastDependency(
                role="openmeteo_ifs9_anchor",
                source_id=ANCHOR_SOURCE_ID,
                product_id=ANCHOR_PRODUCT_ID,
                data_version=_anchor_data_version(metric),
                source_run_id=request.openmeteo_source_run_id,
                source_available_at=request.openmeteo_source_available_at,  # AVAIL-POSSESSION-EXEMPTED: forwards the request's per-role possession input into the dependency lineage record (passthrough of an already-determined value, not a fresh stamp)
                artifact_id=request.anchor_artifact_id,
                anchor_id=anchor_id,
            ),
            ReplacementForecastDependency(
                role="soft_anchor_posterior",
                source_id=SOURCE_ID,
                product_id=PRODUCT_ID,
                data_version=_data_version(metric),
                source_run_id=f"posterior:{posterior_id}",
                source_available_at=computed_at,  # AVAIL-POSSESSION-EXEMPTED: derived posterior artifact — computed_at IS its availability instant (DERIVED_JUSTIFIED), not fetched-data possession
                posterior_id=posterior_id,
            ),
        ]
    )
    return build_replacement_forecast_readiness(
        city=request.city,
        target_date=request.target_date,
        temperature_metric=metric,
        decision_time=computed_at,
        computed_at=computed_at,
        expires_at=expires_at,
        dependencies=tuple(dependencies),
    )


def _bound_posterior_id(source_run_id: object) -> int | None:
    """Parse the ``posterior:<id>`` binding a readiness cert records in ``source_run_id``."""
    if not isinstance(source_run_id, str) or not source_run_id.startswith("posterior:"):
        return None
    try:
        return int(source_run_id.split(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _posterior_serving_key(
    conn: sqlite3.Connection, posterior_id: int
) -> tuple[datetime, datetime] | None:
    """Return a posterior's ``(source_cycle_time, computed_at)`` serving-order key, or None.

    This is the exact key serving orders by (event_reactor/staleness_cancel:
    ``source_cycle_time DESC, computed_at DESC``). None on any read/parse gap so the guard
    fails open.
    """
    try:
        row = conn.execute(
            "SELECT source_cycle_time, computed_at FROM forecast_posteriors WHERE posterior_id = ?",
            (posterior_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    cycle_raw = row[0] if not hasattr(row, "keys") else row["source_cycle_time"]
    computed_raw = row[1] if not hasattr(row, "keys") else row["computed_at"]
    if cycle_raw is None or computed_raw is None:
        return None
    try:
        return (
            _to_utc(str(cycle_raw), field_name="source_cycle_time"),
            _to_utc(str(computed_raw), field_name="computed_at"),
        )
    except Exception:
        return None


def _serving_key_strictly_newer(
    existing: tuple[datetime, datetime], incoming: tuple[datetime, datetime]
) -> bool:
    """True iff ``existing`` sorts STRICTLY ahead of ``incoming`` on the serving key
    ``(source_cycle_time DESC, computed_at DESC)``. EQUAL is NOT strictly newer (fail-open)."""
    existing_cycle, existing_computed = existing
    incoming_cycle, incoming_computed = incoming
    if existing_cycle > incoming_cycle:
        return True
    if existing_cycle == incoming_cycle and existing_computed > incoming_computed:
        return True
    return False


def _readiness_cert_cycle_regression_reasons(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    incoming_posterior_id: int,
) -> tuple[str, ...]:
    """CERTIFICATE-BOUNDARY monotone guard — belt-and-suspenders to _cycle_monotone_block_reasons.

    The readiness certificate is upserted last-writer-wins on ``scope_key`` (readiness_repo
    ``ON CONFLICT(scope_key) DO UPDATE``) with NO timestamp comparison. The upstream cycle
    guards refuse a STRICTLY-OLDER model *cycle* but not an EQUAL cycle committed OUT of
    computed_at order — the crash-only race where a SIGKILL'd orphan materialize child commits
    an older-COMPUTED posterior concurrently with a lock-stealing new daemon. Serving binds to
    the cert's posterior_id and orders families by (source_cycle_time DESC, computed_at DESC);
    this refuses to REGRESS the cert onto a posterior the incumbent certified one is STRICTLY
    newer than on that same key.

    FAIL-OPEN on every gap (no incumbent cert, unparseable binding, missing/unreadable posterior
    timestamps, or EQUAL keys): the guard ONLY blocks a strict regression and is NEVER the sole
    gate that can darken a scope. A forward-or-equal advance proceeds byte-identically to today.
    """
    try:
        expected = expected_replacement_dependency_identity_by_role(metric)["soft_anchor_posterior"]
        incumbent = get_readiness_state_for_scope(
            conn,
            scope_type="strategy",
            city_id=request.city_id,
            city_timezone=request.city_timezone,
            target_local_date=request.target_date,
            temperature_metric=metric,
            physical_quantity=expected.physical_quantity,
            observation_field=expected.observation_field,
            data_version=_data_version(metric),
            strategy_key=STRATEGY_KEY,
            source_id=SOURCE_ID,
            track="soft_anchor_posterior",
        )
    except Exception:
        return ()
    if incumbent is None:
        return ()
    incumbent_posterior_id = _bound_posterior_id(incumbent.get("source_run_id"))
    if incumbent_posterior_id is None or incumbent_posterior_id == incoming_posterior_id:
        return ()
    incumbent_key = _posterior_serving_key(conn, incumbent_posterior_id)
    incoming_key = _posterior_serving_key(conn, incoming_posterior_id)
    if incumbent_key is None or incoming_key is None:
        return ()
    if _serving_key_strictly_newer(incumbent_key, incoming_key):
        import logging  # noqa: PLC0415

        logging.getLogger("zeus.replacement_readiness_cert_monotone").warning(
            "REFUSED readiness-cert regression for %s %s %s: incumbent posterior %s "
            "(cycle=%s computed=%s) is STRICTLY newer than incoming posterior %s "
            "(cycle=%s computed=%s). Cert keeps the fresher binding (crash-steal race guard).",
            request.city,
            _date_text(request.target_date),
            metric,
            incumbent_posterior_id,
            incumbent_key[0].isoformat(),
            incumbent_key[1].isoformat(),
            incoming_posterior_id,
            incoming_key[0].isoformat(),
            incoming_key[1].isoformat(),
        )
        return ("READINESS_CERT_CYCLE_REGRESSION",)
    return ()


def _validated_replacement_forecast_request(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
) -> ReplacementForecastMaterializeResult | tuple[ReplacementForecastMaterializeRequest, str]:
    """Apply the read-only guards and return the honest materialization clock."""

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
    artifact_reasons = _artifact_identity_block_reasons(conn, request)
    if artifact_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=artifact_reasons,
            posterior_id=None,
            anchor_id=None,
            readiness_id=None,
        )
    # MONOTONE CONSUMED-CYCLE ADVANCE (U5 step 2a): refuse a request whose cycle is OLDER than the
    # family's current posterior cycle. Placed after artifact identity (request is structurally
    # valid) and before the value-building precision/insert path so a backward step never writes a
    # row. See _cycle_monotone_block_reasons.
    monotone_reasons = _cycle_monotone_block_reasons(conn, request, metric=metric)
    if monotone_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=monotone_reasons,
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
    request = _request_with_materialization_clock(conn, request)
    prewrite_reasons = _prewrite_block_reasons(request)
    if prewrite_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=prewrite_reasons,
            posterior_id=None,
            anchor_id=None,
            readiness_id=None,
        )
    return request, metric


def prepare_replacement_forecast_live(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
) -> ReplacementForecastMaterializeResult | PreparedReplacementForecastMaterialization:
    """Compute one family without writing or requiring the SQLite writer lock."""

    validated = _validated_replacement_forecast_request(conn, request)
    if isinstance(validated, ReplacementForecastMaterializeResult):
        return validated
    request, metric = validated
    posterior = _compute_posterior_payload(conn, request, metric=metric, anchor_id=-1)
    return PreparedReplacementForecastMaterialization(
        request=request,
        metric=metric,
        posterior=posterior,
    )


def write_prepared_replacement_forecast_live(
    conn: sqlite3.Connection,
    prepared: PreparedReplacementForecastMaterialization,
) -> ReplacementForecastMaterializeResult:
    """Persist a prepared family after the caller revalidates its DB snapshot."""

    request = prepared.request
    metric = prepared.metric
    monotone_reasons = _cycle_monotone_block_reasons(conn, request, metric=metric)
    if monotone_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=monotone_reasons,
            posterior_id=None,
            anchor_id=None,
            readiness_id=None,
        )
    anchor_id = prepared.anchor_id
    if anchor_id is None:
        anchor_id = _insert_anchor(conn, request, metric=metric)
    posterior = prepared.posterior
    if not posterior.live_eligible:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=(
                (REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET,)
                + _posterior_block_sub_reason_codes(posterior)
            ),
            posterior_id=None,
            anchor_id=anchor_id,
            readiness_id=None,
        )
    posterior_id = _write_posterior_row(
        conn, request, metric=metric, anchor_id=anchor_id, result=posterior
    )
    readiness = _build_readiness(request, metric=metric, posterior_id=posterior_id, anchor_id=anchor_id)
    # CERTIFICATE-BOUNDARY monotone guard (belt-and-suspenders to _cycle_monotone_block_reasons
    # at the value-build step): the cert upsert is last-writer-wins on scope_key, so a same-cycle
    # posterior committed OUT of computed_at order (crash-steal race) could otherwise REGRESS the
    # serving cert. Refuse only a STRICT regression on (source_cycle_time, computed_at); fail-open
    # otherwise so a lawful forward advance is byte-identical to today. The posterior row already
    # written stays (it sorts BELOW the certified one and serving binds to the cert, not newest).
    cert_regression_reasons = _readiness_cert_cycle_regression_reasons(
        conn, request, metric=metric, incoming_posterior_id=posterior_id
    )
    if cert_regression_reasons:
        return ReplacementForecastMaterializeResult(
            status="BLOCKED",
            reason_codes=cert_regression_reasons,
            posterior_id=posterior_id,
            anchor_id=anchor_id,
            readiness_id=None,
        )
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


def materialize_replacement_forecast_live(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
) -> ReplacementForecastMaterializeResult:
    """Write anchor, posterior, and readiness rows for replacement live use."""

    _ensure_replacement_identity_columns(conn)
    validated = _validated_replacement_forecast_request(conn, request)
    if isinstance(validated, ReplacementForecastMaterializeResult):
        return validated
    request, metric = validated
    anchor_id = _insert_anchor(conn, request, metric=metric)
    posterior = _compute_posterior_payload(
        conn,
        request,
        metric=metric,
        anchor_id=anchor_id,
    )
    return write_prepared_replacement_forecast_live(
        conn,
        PreparedReplacementForecastMaterialization(
            request=request,
            metric=metric,
            posterior=posterior,
            anchor_id=anchor_id,
        ),
    )
