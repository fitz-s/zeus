"""Materialize replacement forecast posterior rows into forecast DB.

# Created: 2026-06-08
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md (the probability chain
#   §1d-§1e fused-N-direct + settlement sigma floor); FIX 1/FIX 2/FIX 5 (operator-reviewed
#   2026-06-09): explicit replacement_q_mode authority, settlement-sigma-floor coherence in the
#   fused-q path, and capture-status provenance. 2026-06-09 (q_lcb materialization): real per-bin
#   q_lcb_json/q_ucb_json on the fused path via fused-center parameter-uncertainty bootstrap
#   (root-cause /tmp/candidate_missing_rootcause.md — NULL bounds force the Wilson-over-AIFS-votes
#   fallback that under-certifies below ask and discards every candidate). 2026-06-13 (q_ucb
#   symmetry): the soft-anchor (CAPTURE_MISSING) fallback now emits a GENUINE Wilson UPPER bound
#   alongside its lower twin (same inputs/z), so EVERY materialized posterior carries BOTH bounds
#   when possible. Only fused-Normal rows with certified bootstrap bounds and a live runtime policy
#   are stamped LIVE_AUTHORITY; Wilson/fallback rows remain diagnostic (no fabricated edge).
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
from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION, LOW_DATA_VERSION
from src.data.replacement_forecast_readiness import (
    PRODUCT_ID,
    READY_STATUS,
    SOURCE_ID,
    STRATEGY_KEY,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_runtime_policy import (
    DIAGNOSTIC_ONLY_STATUS,
    LIVE_AUTHORITY_STATUS,
    REQUIRED_FLAGS,
    resolve_replacement_forecast_runtime_policy,
)
from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
from src.contracts.availability_time import proof_of_possession_available_at
from src.state.readiness_repo import write_readiness_state
from src.state.source_run_repo import get_source_run
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import (
    AifsTemperatureBin,
    OpenMeteoIfs9AifsSoftAnchorResearchResult,
    build_openmeteo_ifs9_aifs_soft_anchor_result,
)
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
REPLACEMENT_Q_MODE_BAYES_PRECISION_FUSION_CAPTURE_MISSING = "BAYES_PRECISION_FUSION_CAPTURE_MISSING"
REPLACEMENT_Q_MODE_FUSED_Q_BUILD_FAILED = "FUSED_Q_BUILD_FAILED"
# PR#403 FIX (2026-06-09) — fused-q succeeded but the bounds failed. DISTINCT from
# FUSED_Q_BUILD_FAILED (the point q is fine; only the bounds are absent). The fused-Normal
# q point is STILL written to the DB (shadow materialization completes for accrual), but
# live eligibility is killed. Without this a FULL/PARTIAL row with NULL q_lcb_json would be
# live-eligible, letting buy_yes fall back to Wilson-over-AIFS-votes — exactly the two-measures
# disease (fused-Normal q point + legacy LCB authority) that the Milan incident root-caused.
REPLACEMENT_Q_MODE_FUSED_NORMAL_BOUNDS_MISSING = "FUSED_NORMAL_BOUNDS_MISSING"
# AIFS DROPPED (operator directive 2026-06-17 "drop aifs") — the fused CENTER materialized q purely
# from N(mu*, sigma) (zero AIFS pull) but the certified fused-q SHAPE/BOUNDS bootstrap did not run
# (flag-off / fused-q build failure / predictive_sigma thin). This REPLACES the old cold fail-closed
# fallback (the 0.8-AIFS soft-anchor q). Honest-shadow only: the live gate licenses solely
# FUSED_NORMAL_{FULL,PARTIAL} with the certified bootstrap basis, which this mode never carries, so a
# fused-center-only row is materialized for accrual/diagnosis but is NOT live-tradeable. Diagnosably
# distinct from FUSED_Q_BUILD_FAILED (which produced NO center q) and from the legacy
# SOFT_ANCHOR_FALLBACK (which no longer pulls toward the cold AIFS mean).
REPLACEMENT_Q_MODE_FUSED_CENTER_ONLY_NORMAL = "FUSED_CENTER_ONLY_NORMAL"

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
    # AIFS DROPPED AS HARD DEPENDENCY (operator directive 2026-06-17 "drop aifs"). The LIVE q is the
    # multi-model BAYES_PRECISION_FUSION fused Normal (q_shape=fused_normal_direct), which carries NO
    # AIFS dependency. AIFS, when present, is recorded as provenance / cross-check ONLY and NEVER
    # gates materialization; when absent (None) the fused path materializes the posterior without it.
    # Kept in required FIELD POSITION (no default) so the dataclass field order is unchanged — the
    # value may now be None. The AIFS soft-anchor result + its cold 0.8 anchor pull are no longer the
    # fail-closed fallback (see _insert_posterior); the fallback is a fused-center-only Normal or skip.
    aifs_extraction: AifsSampledLocalDayExtraction | None
    aifs_source_run_id: str | None
    aifs_source_available_at: datetime | str | None
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
    aifs/openmeteo roles for free the day they begin recording ``source_run`` rows. Reading
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


def _replacement_trade_authority_status(
    *,
    replacement_q_mode: str,
    q_lcb_map: Mapping[str, float] | None,
    q_ucb_map: Mapping[str, float] | None,
    q_lcb_basis: str | None,
) -> str:
    """Return row-level live authority only for the exact live q carrier."""
    live_q_carrier = (
        replacement_q_mode in {REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL, REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL}
        and q_lcb_map is not None
        and q_ucb_map is not None
        and q_lcb_basis == _QLCB_BASIS
    )
    if not live_q_carrier:
        return DIAGNOSTIC_ONLY_STATUS
    try:
        from src.config import settings  # noqa: PLC0415

        feature_flags = settings["feature_flags"]
        flags = {key: bool(feature_flags.get(key, False)) for key in REQUIRED_FLAGS}
        policy = resolve_replacement_forecast_runtime_policy(
            flags,
            promotion_evidence=None,
            capital_objective_evidence=None,
        )
    except Exception:
        return DIAGNOSTIC_ONLY_STATUS
    if policy.status != LIVE_AUTHORITY_STATUS or not policy.trade_authority_enabled:
        return DIAGNOSTIC_ONLY_STATUS
    return LIVE_AUTHORITY_STATUS


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


def _ensure_forecast_posteriors_live_authority_check(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA legacy_alter_table=ON")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'forecast_posteriors'"
    ).fetchone()
    if row is None:
        return
    create_sql = str(row[0] if not isinstance(row, sqlite3.Row) else row["sql"])
    if "LIVE_AUTHORITY" in create_sql:
        return
    new_check = "CHECK (trade_authority_status IN ('DIAGNOSTIC_ONLY', 'LIVE_AUTHORITY'))"
    old_checks = (
        "CHECK (trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY'))",
        "CHECK (trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY', 'LIVE_AUTHORITY'))",
    )
    old_check = next((check for check in old_checks if check in create_sql), None)
    if old_check is None:
        raise RuntimeError("forecast_posteriors trade_authority_status CHECK shape is not migratable")
    legacy_table = "forecast_posteriors__pre_live_authority_check"
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (legacy_table,),
    ).fetchone():
        raise RuntimeError(f"legacy migration table already exists: {legacy_table}")
    column_names = [
        str(row[1] if not isinstance(row, sqlite3.Row) else row["name"])
        for row in conn.execute("PRAGMA table_info(forecast_posteriors)").fetchall()
    ]
    if not column_names:
        return
    quoted_columns = ", ".join(f'"{name}"' for name in column_names)
    conn.execute(f"ALTER TABLE forecast_posteriors RENAME TO {legacy_table}")
    conn.execute(create_sql.replace(old_check, new_check).replace("DEFAULT 'SHADOW_ONLY'", "DEFAULT 'DIAGNOSTIC_ONLY'"))
    selected_columns = ", ".join(
        (
            "CASE WHEN trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY') "
            "THEN 'DIAGNOSTIC_ONLY' ELSE trade_authority_status END AS trade_authority_status"
        )
        if name == "trade_authority_status"
        else f'"{name}"'
        for name in column_names
    )
    conn.execute(
        f"INSERT INTO forecast_posteriors ({quoted_columns}) "
        f"SELECT {selected_columns} FROM {legacy_table}"
    )
    conn.execute(f"DROP TABLE {legacy_table}")


def _ensure_diagnostic_only_trade_authority_check(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute("PRAGMA legacy_alter_table=ON")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if row is None:
        return
    create_sql = str(row[0] if not isinstance(row, sqlite3.Row) else row["sql"])
    normalized_create_sql = create_sql
    for parent_table in (
        "raw_forecast_artifacts",
        "deterministic_forecast_anchors",
        "forecast_posteriors",
        "replacement_shadow_decisions",
        "raw_model_forecasts",
    ):
        normalized_create_sql = normalized_create_sql.replace(
            f"{parent_table}__pre_diagnostic_authority_check",
            parent_table,
        )
    needs_fk_normalization = normalized_create_sql != create_sql
    if "trade_authority_status" not in create_sql:
        if needs_fk_normalization:
            _rebuild_table_preserving_rows(conn, table_name, normalized_create_sql)
        return
    if "DIAGNOSTIC_ONLY" in create_sql:
        if needs_fk_normalization:
            _rebuild_table_preserving_rows(conn, table_name, normalized_create_sql)
        return
    old_checks = (
        "CHECK (trade_authority_status IN ('SHADOW_ONLY'))",
        "CHECK (trade_authority_status IN ('SHADOW_VETO_ONLY'))",
        "CHECK (trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY'))",
        "CHECK (trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY', 'LIVE_AUTHORITY'))",
    )
    old_check = next((check for check in old_checks if check in create_sql), None)
    if old_check is None:
        raise RuntimeError(f"{table_name} trade_authority_status CHECK shape is not migratable")
    new_check = "CHECK (trade_authority_status IN ('DIAGNOSTIC_ONLY'))"
    if table_name == "forecast_posteriors" or "LIVE_AUTHORITY" in old_check:
        new_check = "CHECK (trade_authority_status IN ('DIAGNOSTIC_ONLY', 'LIVE_AUTHORITY'))"
    legacy_table = f"{table_name}__pre_diagnostic_authority_check"
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (legacy_table,),
    ).fetchone():
        raise RuntimeError(f"legacy migration table already exists: {legacy_table}")
    column_names = [
        str(row[1] if not isinstance(row, sqlite3.Row) else row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    ]
    if not column_names:
        return
    quoted_columns = ", ".join(f'"{name}"' for name in column_names)
    selected_columns = ", ".join(
        (
            "CASE WHEN trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY') "
            "THEN 'DIAGNOSTIC_ONLY' ELSE trade_authority_status END AS trade_authority_status"
        )
        if name == "trade_authority_status"
        else f'"{name}"'
        for name in column_names
    )
    conn.execute(f"ALTER TABLE {table_name} RENAME TO {legacy_table}")
    conn.execute(
        normalized_create_sql.replace(old_check, new_check)
        .replace("DEFAULT 'SHADOW_ONLY'", "DEFAULT 'DIAGNOSTIC_ONLY'")
        .replace("DEFAULT 'SHADOW_VETO_ONLY'", "DEFAULT 'DIAGNOSTIC_ONLY'")
    )
    conn.execute(
        f"INSERT INTO {table_name} ({quoted_columns}) "
        f"SELECT {selected_columns} FROM {legacy_table}"
    )
    conn.execute(f"DROP TABLE {legacy_table}")


def _rebuild_table_preserving_rows(
    conn: sqlite3.Connection,
    table_name: str,
    create_sql: str,
) -> None:
    legacy_table = f"{table_name}__pre_diagnostic_authority_check"
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (legacy_table,),
    ).fetchone():
        raise RuntimeError(f"legacy migration table already exists: {legacy_table}")
    column_names = [
        str(row[1] if not isinstance(row, sqlite3.Row) else row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    ]
    if not column_names:
        return
    quoted_columns = ", ".join(f'"{name}"' for name in column_names)
    conn.execute(f"ALTER TABLE {table_name} RENAME TO {legacy_table}")
    conn.execute(create_sql)
    conn.execute(
        f"INSERT INTO {table_name} ({quoted_columns}) "
        f"SELECT {quoted_columns} FROM {legacy_table}"
    )
    conn.execute(f"DROP TABLE {legacy_table}")


def _ensure_replacement_identity_columns(conn: sqlite3.Connection) -> None:
    """Keep old PR399 shadow DBs fail-closed instead of returning stale rows."""

    conn.execute("PRAGMA defer_foreign_keys=ON")
    # Only replacement-derived tables moved from the legacy shadow vocabulary to
    # DIAGNOSTIC_ONLY/LIVE_AUTHORITY. Raw artifacts and raw model captures remain
    # source-input evidence with SHADOW_ONLY semantics; rebuilding those parent
    # tables here can break child FKs before the current posterior is written.
    for table_name in (
        "deterministic_forecast_anchors",
        "forecast_posteriors",
        "replacement_shadow_decisions",
    ):
        _ensure_diagnostic_only_trade_authority_check(conn, table_name)
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
        _ensure_forecast_posteriors_live_authority_check(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_deterministic_forecast_anchors_identity_hash
            ON deterministic_forecast_anchors(anchor_identity_hash)
            WHERE anchor_identity_hash IS NOT NULL
        """
    )
    posterior_columns = _table_columns(conn, "forecast_posteriors")
    if {"city", "target_date", "temperature_metric", "bin_topology_hash", "computed_at"}.issubset(posterior_columns):
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
    # AIFS DROPPED (operator directive 2026-06-17): AIFS is now OPTIONAL. When the extraction is
    # absent (None) it contributes NO dependency-time row and NO AIFS_* validation reasons — the
    # fused path materializes the posterior without it. When it IS present (cross-check provenance),
    # we STILL validate its integrity so a present-but-corrupt AIFS payload is caught, but those
    # reasons are recorded the same as before. Either way AIFS is never the cold fallback (see
    # _insert_posterior). The baseline + openmeteo anchor remain HARD-REQUIRED (the fused center is
    # bridged from the OM9 anchor; the baseline is the b0 dependency).
    aifs_present = request.aifs_extraction is not None
    dependency_times = [
        ("baseline_b0", _to_utc(request.baseline_source_available_at, field_name="baseline_source_available_at")),
        ("openmeteo_ifs9_anchor", _to_utc(request.openmeteo_source_available_at, field_name="openmeteo_source_available_at")),
    ]
    if aifs_present and request.aifs_source_available_at is not None:
        dependency_times.append(
            ("aifs_sampled_2t", _to_utc(request.aifs_source_available_at, field_name="aifs_source_available_at"))
        )
    expected = expected_replacement_dependency_identity_by_role(metric)
    if not str(request.baseline_source_run_id or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_BASELINE_SOURCE_RUN_ID_MISSING")
    if not str(request.openmeteo_source_run_id or "").strip():
        reasons.append("REPLACEMENT_MATERIALIZATION_OPENMETEO_SOURCE_RUN_ID_MISSING")
    if request.baseline_data_version != expected["baseline_b0"].data_version:
        reasons.append("REPLACEMENT_MATERIALIZATION_BASELINE_DATA_VERSION_MISMATCH")
    if aifs_present:
        # Integrity cross-check ONLY (AIFS no longer gates the fused path). A present extraction
        # missing its source_run_id is still flagged so corrupt provenance is visible; an ABSENT
        # extraction emits nothing here.
        if not str(request.aifs_source_run_id or "").strip():
            reasons.append("REPLACEMENT_MATERIALIZATION_AIFS_SOURCE_RUN_ID_MISSING")
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
    # AIFS DROPPED (operator directive 2026-06-17): no AIFS extraction -> no AIFS artifact identity to
    # cross-check. The fused path does not depend on the AIFS raw artifact, so an absent extraction
    # is NOT a block (the prewrite gate already skips the AIFS_* reasons). Returns no reasons.
    if request.aifs_extraction is None:
        return ()
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
        "trade_authority_status": DIAGNOSTIC_ONLY_STATUS,
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
            DIAGNOSTIC_ONLY_STATUS,
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


def _replacement_fused_q_shape_enabled() -> bool:
    """Flag gate for the FUSED-Q SHAPE replacement (2026-06-09 AIFS-replacement experiment).

    When ``replacement_0_1_fused_q_shape_enabled`` is true AND the BAYES_PRECISION_FUSION fusion produced an
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

        return bool(settings["edli"].get("replacement_0_1_fused_q_shape_enabled", False))
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
    except Exception as exc:  # fail-soft: never block shadow materialization
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

        path = _SIGMA_SCALE_FIT_PATH
        if not os.path.isabs(path):
            repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            path = os.path.join(repo, _SIGMA_SCALE_FIT_PATH)
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

        edli_cfg = settings["edli"]
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
    # FUSED-Q SHAPE (2026-06-09 AIFS-replacement experiment): the PREDICTIVE sigma for building
    # q directly from N(mu*, sigma_pred) — sigma_pred^2 = fused.sd^2 + sigma_resid^2, where
    # sigma_resid is the walk-forward std of the fused-center residual series (common-date mean
    # of the instruments' de-biased residuals), conservatively floored. None when the residual
    # substrate is too thin AND no conservative default applies (caller falls back to the
    # AIFS-shape soft-anchor q).
    predictive_sigma_c: float | None = None
    # FIX 1 (2026-06-09; 2026-06-17 per-city): the K3 decorrelated-provider completeness verdict
    # computed INSIDE the fusion. True = every provider family EXPECTED AT THIS CITY is served
    # (-> FUSED_NORMAL_FULL); False = an expected family is absent (-> FUSED_NORMAL_PARTIAL). The
    # expected set is per-city domain-aware (3 for non-CONUS/non-NA after the coarse-global
    # removal, up to 5 in CONUS), NOT a flat 5. The materializer REUSES this; it never re-derives
    # a parallel provider check (single-builder).
    decorrelated_providers_complete: bool = False
    # FIX 5 (2026-06-09; 2026-06-17 per-city): capture-status provenance. count of the
    # EXPECTED-here decorrelated providers whose CURRENT value entered the fused set, and the
    # per-city expected count. Recording only.
    decorrelated_providers_served: int = 0
    decorrelated_providers_expected: int = 0
    # Task #32 follow-up (brand law): per-instrument serving provenance for every model that
    # entered the fused set — which endpoint served its CURRENT value (served_via), the served
    # row id/cycle/capture stamp/age, and its lead bucket. A previous_runs substitution (a
    # provider whose selected cycle has no single_runs row, e.g. JMA at 06Z-cadence cycles) is
    # therefore RECORDED in the posterior provenance, never silent.
    current_value_serving: Mapping[str, Mapping[str, object]] | None = None


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
    """Flag-gated BAYES_PRECISION_FUSION-Bayes multi-model fusion override (the_path replacement_0_1_bayes_precision_fusion).

    Returns the fused (anchor_value_c, anchor_sigma_c) that REPLACE the single OM9 9km anchor
    center/spread in the soft-anchor construction, ONLY when ``replacement_0_1_bayes_precision_fusion_enabled``
    is true AND at least one decorrelated extra survives the fail-soft capture. Returns None when
    the flag is OFF (default) OR all extras are absent -> the existing single-anchor path runs
    BYTE-IDENTICALLY. This is the ONE place the flag is read; the fusion itself is the ported
    proof C1 (src/forecast/bayes_precision_fusion.py — no parallel fusion).

    LAYERING (BAYES_PRECISION_FUSION_SPEC.md §6 integration): the override is computed from the ALREADY
    EB-bias-corrected anchor center (so it composes AFTER the EB bias layer); it replaces only
    the anchor center/spread; the AIFS member-vote prior + member-vote smoothing + the downstream
    q_lcb settlement floor + EMOS + bin integration are all UNCHANGED. FAIL-SOFT / FAIL-CLOSED:
    any error, missing config, or zero surviving extras -> None (never raises, never blocks).
    """
    try:
        from src.config import runtime_cities_by_name, settings  # noqa: PLC0415

        edli_cfg = settings["edli"]
        if not bool(edli_cfg.get("replacement_0_1_bayes_precision_fusion_enabled", False)):
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
        # fuse_bayes_precision_posterior reach T2_BAYES once n_train>=MIN_TRAIN (else EQUAL_WEIGHT). Fail-soft:
        # the provider NEVER raises (returns {} on any error) -> anchor fallback / equal-weight.
        history_provider = getattr(_replacement_bayes_precision_fusion_override, "_history_provider", None)
        if history_provider is None and conn is not None:
            from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider  # noqa: PLC0415

            history_provider = BayesPrecisionFusionHistoryProvider(conn)

        # BLOCKER 5: the CURRENT values feeding the traded q come from the PERSISTED single_runs
        # rows the download job wrote — NEVER a network fetch inside the q path. Read them by
        # (city, metric, target_date, lead, source_cycle_time) on the SAME connection so the q is
        # reconstructable to the exact persisted inputs. If the current capture is MISSING (the
        # download did not run / failed), fall back to the single-anchor posterior (return None)
        # WITH a logged reason — never silently network-fetch.
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
            )
            persisted_current = {
                m: (s.value_c, s.raw_model_forecast_id) for m, s in served_current.items()
            }

        # An explicitly-assigned _live_fetch is honored ONLY as a per-model override seam for
        # models WITHOUT a persisted current row (legacy/test injection). It is never consulted
        # when the persisted row exists. It does NOT defeat the missing-capture gate: when the
        # persisted capture is entirely absent the q path falls back to single-anchor regardless,
        # because B5 forbids building the traded q from any non-persisted current value.
        injected_live_fetch = getattr(_replacement_bayes_precision_fusion_override, "_live_fetch", None)

        if conn is not None and not persisted_current:
            # Missing current capture on the live path -> single-anchor fallback + logged reason.
            # NEVER a network fetch in the q path (the persisted download is the sole q source).
            import logging  # noqa: PLC0415
            logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                "replacement_0_1 BAYES_PRECISION_FUSION fusion: persisted current single_runs capture MISSING for "
                "%s %s %s lead=%s cycle=%s -> single-anchor fallback (no network fetch in q path)",
                request.city, metric, target_date, lead_days, source_cycle_iso,
            )
            return None

        # ARRIVAL GUARD inputs (C1-AVAIL-CLOCK, 2026-06-16): the honest per-model availability is
        # PROOF OF POSSESSION = the served row's captured_at, routed through the canonical producer
        # (no nominal — captured_at is the real possession wall-clock). Models with no served row are
        # absent from the map -> the capture's guard fail-OPENs (admits) them. decision_utc is the
        # materialization decision instant (computed_at). Shadow-q-staged: expected to exclude ~0 in
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

        # v3 rule 5 (grid representativeness) deploy flag: when ON, the capture stamps each
        # instrument's sigma_repr^2 (native-cell d_eff/delta_z) and the fusion ADDS it to the
        # Sigma diagonal (down-weighting coarse/offset cells). Default OFF -> byte-identical.
        grid_repr_enabled = bool(
            edli_cfg.get("replacement_0_1_grid_representativeness_enabled", False)
        )
        capture = capture_bayes_precision_instruments(
            city=request.city, metric=metric, latitude=lat, longitude=lon,
            timezone_name=tz_name,
            run=_to_utc(request.source_cycle_time, field_name="source_cycle_time"),
            target_local_date=target_local_date, lead_days=lead_days,
            anchor_z_corrected=float(anchor_value_corrected_c),
            history_provider=history_provider, live_fetch=_persisted_then_injected_fetch,
            decision_utc=computed_at,
            model_available_at=model_available_at,
            apply_grid_representativeness=grid_repr_enabled,
        )
        if not capture.has_extras:
            # K3 ANTIBODY (2026-06-09): all multi-model extras absent. We only reach here when
            # replacement_0_1_bayes_precision_fusion_enabled is True, so ZERO extras is a WIRING failure (e.g.
            # the lead-calendar mismatch that silently reverted ALL fusion to cold soft-anchor for
            # ~30h) — NOT a benign inert path. Make it LOUD so a repeat can never hide as a
            # transient drop. (Behaviour unchanged: still single-anchor fallback.)
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 BAYES_PRECISION_FUSION fusion fired with ZERO multi-model extras (flag ON) -> "
                    "single-anchor fallback for %s %s %s cycle=%s. Check the single_runs capture "
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
            anchor_sigma_repr_sq=capture.anchor_sigma_repr_sq,
        )

        used_models = tuple(fused.used_models)
        # K3 ANTIBODY (2026-06-09; 2026-06-17 domain-aware): surface a STRUCTURALLY-incomplete
        # decorrelated set LOUDLY — but ONLY for a family that is EXPECTED at this city. The
        # declared decorrelated PROVIDERS are NCEP / DWD-ICON / CMC / JMA / UKMO. 2026-06-17
        # COARSE-GLOBAL REMOVAL: NCEP (gfs_hrrr/ncep_nbm, CONUS) and CMC (gem_hrdps, N-America)
        # lost their global fallbacks, so OUTSIDE those nest domains they are STRUCTURALLY ABSENT,
        # not a transient drop — a non-CONUS city legitimately fuses {DWD,JMA,UKMO} and is
        # COMPLETE. expected_provider_families_for_city(lat,lon) is THE per-city expected set
        # (domain-gated via the SAME polygon the download + selection use), replacing the old
        # flat /5 count; missing = expected_for_city − served. The warning now fires only for an
        # EXPECTED-but-absent family (a genuine transient drop), never a structurally-absent one.
        # SINGLE-AUTHORITY provider-family mapping (Task #32): the model->decorrelated-provider
        # family map lives in replacement_fusion_upgrade_trigger.DECORRELATED_PROVIDER_FAMILIES so
        # the fusion's served/missing determination and the upgrade trigger's served/capturable
        # comparison can never drift on what counts as a provider, NOR on what is expected where.
        from src.data.replacement_fusion_upgrade_trigger import (  # noqa: PLC0415
            DECORRELATED_PROVIDER_FAMILIES,
            decorrelated_provider_families_of,
            expected_provider_families_for_city,
        )

        _served_families = decorrelated_provider_families_of(set(used_models))
        # Per-city AND per-LEAD: the nests are lead-capped, so the expected set must be evaluated
        # at the SAME city-local lead the fusion serves (lead_days, computed above) — NOT lead 0,
        # which would over-expect NCEP/CMC at far lead and false-flag PARTIAL (2026-06-17 critic fix).
        _expected_families = expected_provider_families_for_city(lat, lon, lead_days)
        _missing_providers = [
            f"{fam}/{'|'.join(DECORRELATED_PROVIDER_FAMILIES[fam])}"
            for fam in sorted(_expected_families)
            if fam not in _served_families
        ]
        # FIX 1/FIX 5 (2026-06-09; 2026-06-17 per-city): the SINGLE K3 completeness verdict reused
        # by the q-mode + capture-status provenance. Expected = the families servable AT THIS CITY;
        # served = expected − missing. This is the ONLY provider-count determination — the q-mode
        # FULL/PARTIAL split and the FIX-5 capture_status both read it (no parallel re-derivation).
        _decorrelated_expected = len(_expected_families)
        _decorrelated_served = _decorrelated_expected - len(_missing_providers)
        _decorrelated_complete = not _missing_providers
        if _missing_providers:
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 BAYES_PRECISION_FUSION fusion decorrelated-provider INCOMPLETE for %s %s: served "
                    "%d/%d expected-here, missing %s (used=%s). An EXPECTED-but-absent provider (a "
                    "transient single_runs drop) must be resolved explicitly, not silently dropped.",
                    request.city, metric, _decorrelated_served, _decorrelated_expected,
                    _missing_providers, list(used_models),
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
        from src.data.bayes_precision_fusion_capture import (  # noqa: PLC0415
            OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME,
        )
        from src.forecast.bayes_precision_fusion_anchor_bridge import bridge_metadata  # noqa: PLC0415
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

        # Task #32 follow-up (brand law): per-instrument serving provenance for the FUSED set.
        # served_current is the single-authority serving map (read_current_instrument_values);
        # restricting to used_models keeps the record scoped to what actually entered the q. A
        # previous_runs substitution surfaces here as served_via="previous_runs" — never silent.
        _current_value_serving = {
            m: served_current[m].as_provenance()  # type: ignore[union-attr]
            for m in used_models
            if m in served_current
        } or None

        return _BayesPrecisionFusionFusionOverride(
            anchor_value_c=float(fused.mu),
            anchor_sigma_c=float(fused.sd),
            method=fused.method,
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
        )
    except Exception as exc:  # fail-soft: never break shadow materialization
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
# SINGLE AUTHORITY: the certified bootstrap basis string lives in cycle_policy (shared with the
# tradeable-grade coverage predicate the mask-and-starve antibody sites use). Re-exported as
# _QLCB_BASIS for the in-module call sites + existing tests that import it by this name.
_QLCB_BASIS = TRADEABLE_GRADE_QLCB_BASIS
_QLCB_SEED = 0x5EED_F09  # deterministic per-posterior rng (provenance-stable bounds)

# ---------------------------------------------------------------------------
# SOFT-ANCHOR Q_LCB FALLBACK (2026-06-12) — Wilson-over-AIFS-member-votes, PROMOTED into the
# materializer so NO posterior is ever born with a NULL q_lcb.
#
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: /tmp/qlcb_coverage_fix_report.md (root-cause: 100% of NULL q_lcb_json on the
#   06-12/06-13 surface are CAPTURE_MISSING soft-anchor rows — the BAYES_PRECISION_FUSION override
#   returned None because the persisted CURRENT single_runs capture was absent at lead+1, so the
#   fused-center bootstrap NEVER ran; ZERO NULLs come from the bootstrap raising). Single-authority
#   law (architecture census 2026-06-11): the SAME Wilson-over-AIFS-votes bound the live decision
#   path computed at read time (event_reactor_adapter._replacement_yes_lcb_for_bin / _wilson_lower_
#   bound) is now computed ONCE at materialization, killing the materializer-vs-decision twin
#   authority. The decision path still reads the bundle q_lcb first, so this is a no-behavior-change
#   PROMOTION for the live read; what changes is that the bound is now PERSISTED + provenance-stamped
#   with its OWN basis ("wilson_aifs_member_votes") instead of NULL.
#
# WHY NOT an analytic predictive-Normal bound here: measured (report Part 2). The soft-anchor
# Gaussian spread is anchor_sigma_c≈3.0C (wide vs ~1-2C settlement bins); a center-uncertainty
# bootstrap at that sigma collapses the per-bin 5th-percentile to ~0 on every bin (useless), and a
# tighter SEM-style center sigma UNDERCOVERS on the n=21 settled CAPTURE_MISSING cells (overconfident,
# unsafe). With n=21<min_n=30 there is INSUFFICIENT settled history to license any tighter bound, so
# the honest move is the member-vote Wilson bound (a real binomial lower bound on the AIFS support
# fraction) — NOT a fabricated Normal floor.
#
# HONESTY / NO-AUTO-PROMOTE: the basis string is DISTINCT from the certified bootstrap marker, so the
# calibration-credential reader (event_reactor_adapter._FUSED_BOOTSTRAP_QLCB_BASIS exact-match) does
# NOT treat it as the bootstrap basis — a CAPTURE_MISSING / SOFT_ANCHOR row is STILL not live-eligible
# (its q_mode is not in _REPLACEMENT_Q_MODE_LIVE_ELIGIBLE). This bound exists for shadow accrual,
# coverage measurement, and to make NULL q_lcb UNCONSTRUCTABLE — never to silently arm a degraded
# (no-current-capture) posterior. Bound is clipped to [0, q_point] per bin (a lower bound can never
# exceed the point mass).
# ---------------------------------------------------------------------------
_QLCB_SOFT_ANCHOR_BASIS = "wilson_aifs_member_votes"
_QLCB_WILSON_Z = 1.645  # one-sided 95% (matches the live decision-path Wilson z)


def _wilson_lower_bound(successes: float, trials: float, *, z: float = _QLCB_WILSON_Z) -> float:
    """One-sided Wilson lower bound for a binomial proportion (successes/trials).

    Byte-identical math to event_reactor_adapter._wilson_lower_bound (the live decision-path
    fallback this PROMOTES). z=1.645 -> ~95% one-sided. Returns 0.0 for trials<=0.
    """
    if trials <= 0.0:
        return 0.0
    successes = min(max(float(successes), 0.0), float(trials))
    p_hat = successes / float(trials)
    z2 = z * z
    denom = 1.0 + z2 / float(trials)
    center = p_hat + z2 / (2.0 * float(trials))
    margin = z * ((p_hat - (p_hat * p_hat) + z2 / (4.0 * float(trials))) / float(trials)) ** 0.5
    return max(0.0, min(1.0, (center - margin) / denom))


def _wilson_upper_bound(successes: float, trials: float, *, z: float = _QLCB_WILSON_Z) -> float:
    """One-sided Wilson UPPER bound for a binomial proportion (successes/trials).

    The exact symmetric counterpart of ``_wilson_lower_bound``: identical Wilson centre/denom,
    ``(center + margin) / denom`` instead of ``- margin``. z=1.645 -> ~95% one-sided (the SAME z
    the LCB uses, so the two bounds are the same-confidence two-sided interval ends, not a mixed
    pair). A genuine binomial bound on the AIFS support fraction — NOT a fabricated value and NOT a
    copy of the point or the LCB. Returns 1.0 for trials<=0 (no evidence -> widest honest upper).
    """
    if trials <= 0.0:
        return 1.0
    successes = min(max(float(successes), 0.0), float(trials))
    p_hat = successes / float(trials)
    z2 = z * z
    denom = 1.0 + z2 / float(trials)
    center = p_hat + z2 / (2.0 * float(trials))
    margin = z * ((p_hat - (p_hat * p_hat) + z2 / (4.0 * float(trials))) / float(trials)) ** 0.5
    return max(0.0, min(1.0, (center + margin) / denom))


def _build_soft_anchor_wilson_lcb(
    *,
    aifs_probabilities: Mapping[str, float],
    member_count: float,
    q_point: Mapping[str, float],
) -> dict[str, float]:
    """Per-bin Wilson-over-AIFS-member-votes q_lcb for the soft-anchor (no-fusion) path.

    For each bin: successes = aifs_prob(bin) * member_count, trials = member_count, then the
    one-sided Wilson lower bound — the SAME estimator the live decision path used at read time.
    Clipped to [0, q_point[bin]] (a lower bound can never exceed the point mass). Bins absent from
    the AIFS vote map get q_lcb = 0.0 (no support evidence -> honest zero lower bound).

    Raises on a non-finite member_count (caller fail-softs to NULL — never WORSE than status quo).
    """
    mc = float(member_count)
    if not (math.isfinite(mc) and mc > 0.0):
        raise ValueError(f"member_count must be positive-finite, got {member_count}")
    out: dict[str, float] = {}
    for bin_id, q_pt in q_point.items():
        prob = aifs_probabilities.get(bin_id)
        if prob is None:
            out[bin_id] = 0.0
            continue
        lb = _wilson_lower_bound(float(prob) * mc, mc)
        out[bin_id] = min(max(lb, 0.0), max(float(q_pt), 0.0))
    return out


def _build_soft_anchor_wilson_ucb(
    *,
    aifs_probabilities: Mapping[str, float],
    member_count: float,
    q_point: Mapping[str, float],
) -> dict[str, float]:
    """Per-bin Wilson-over-AIFS-member-votes q_ucb for the soft-anchor (no-fusion) path.

    The genuine one-sided Wilson UPPER bound — the EXACT symmetric counterpart of
    ``_build_soft_anchor_wilson_lcb`` from the IDENTICAL inputs (the AIFS member-vote support
    fraction × member_count, same z). This is the honest upper band of the soft-anchor support, so
    a CAPTURE_MISSING posterior (no fused inputs -> no fused-center bootstrap upper band) is still
    born with BOTH bounds instead of a half-bound. It is NOT a fabrication: not q_ucb=q_point, not a
    constant, not a copy of q_lcb. The carrier honesty is preserved upstream — the basis string
    stays ``wilson_aifs_member_votes`` (distinct from the certified fused bootstrap marker) and the
    q_mode is CAPTURE_MISSING, so this row remains structurally NON-live-eligible (the credential
    reader's exact-basis gate AND the q-mode gate both reject it). The bound exists for shadow
    accrual, coverage measurement, and so the tradeable-latest reader's both-bounds predicate is the
    SAME shape on every row (a fused row never falls out of the reader window behind a run of
    half-bounded CAPTURE_MISSING rows).

    Clipped to [q_point[bin], 1.0] (an upper bound can never sit below the point mass). Bins absent
    from the AIFS vote map get q_ucb clipped up from the no-evidence Wilson upper (successes=0), so
    they are still >= q_point — an honest wide upper where there is no vote support.

    Raises on a non-finite member_count (caller fail-softs to NULL — never WORSE than status quo).
    """
    mc = float(member_count)
    if not (math.isfinite(mc) and mc > 0.0):
        raise ValueError(f"member_count must be positive-finite, got {member_count}")
    out: dict[str, float] = {}
    for bin_id, q_pt in q_point.items():
        prob = aifs_probabilities.get(bin_id)
        successes = 0.0 if prob is None else float(prob) * mc
        ub = _wilson_upper_bound(successes, mc)
        out[bin_id] = max(min(ub, 1.0), min(max(float(q_pt), 0.0), 1.0))
    return out


def _family_rounding_rule(bins: Sequence["AifsTemperatureBin"]) -> str:
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


def _build_fused_q_bounds(
    *,
    mu_star: float,
    center_sigma_c: float,
    predictive_sigma_c: float,
    bins: Sequence["AifsTemperatureBin"],
    half_step: float,
    q_point: Mapping[str, float],
    n_draws: int = _QLCB_BOOTSTRAP_DRAWS,
    rounding_rule: str = "wmo_half_up",
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
    probs = np.clip(ndtr(z_high) - ndtr(z_low), 0.0, 1.0)  # (N, M) per-draw per-bin mass

    q_lcb_vec = np.percentile(probs, 5.0, axis=0)  # (M,)
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
        q_lcb_map[bin_id] = lcb
        q_ucb_map[bin_id] = ucb
        if return_samples:
            q_samples_map[bin_id] = [float(x) for x in probs[:, idx].tolist()]
    if return_samples:
        return q_lcb_map, q_ucb_map, q_samples_map
    return q_lcb_map, q_ucb_map


def _insert_posterior(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    *,
    metric: str,
    anchor_id: int,
) -> int:
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
    # activated, gate-passing HIGH cell, else None. ARTIFACT-GATED, not a shadow flag: when the
    # artifact is absent (current live state — gitignored generated file) the loader returns None
    # → bias_shift_c stays None → BYTE-IDENTICAL to today. It goes live the moment the operator
    # places the fitted artifact in state/ and restarts (same posture as the σ-floor artifact).
    # SIGN: δ_city = anchor − settlement; applied below as corrected = raw − δ_city, so a cold
    # anchor (δ<0) warms and a hot anchor (δ>0) cools; the corrected center feeds the fusion prior
    # → the de-bias propagates into the fused μ*. FAIL-SOFT: any error → None (family-level fallback).
    bias_shift_c: float | None
    try:
        from src.calibration.anchor_representativeness_debias import get_city_debias_c  # noqa: PLC0415

        bias_shift_c = get_city_debias_c(request.city, metric)
    except Exception:
        bias_shift_c = None
    # THE_PATH member-vote smoothing: flag-gated additive Laplace/Dirichlet alpha so the AIFS
    # member prior is strictly positive on every bin and the soft_anchor.py:197-198 zero-prior
    # -inf veto can never make a bin un-hittable. None when flag OFF -> byte-identical to today.
    member_vote_smoothing_alpha = _replacement_member_vote_smoothing_alpha()
    # BAYES_PRECISION_FUSION-Bayes fusion (flag-gated, default-OFF): replace the single OM9 9km anchor center/spread
    # with the multi-model Bayesian posterior. Computed from the EB-corrected anchor center so it
    # composes AFTER the EB bias layer; member-vote smoothing stays applied to the AIFS prior; the
    # downstream q_lcb floor + EMOS + bin integration are unchanged. None -> byte-identical path.
    raw_anchor_value_c = request.openmeteo_anchor.high_c if metric == "high" else request.openmeteo_anchor.low_c
    anchor_value_corrected_c = float(raw_anchor_value_c) - (0.0 if bias_shift_c is None else float(bias_shift_c))
    bayes_precision_fusion_override = _replacement_bayes_precision_fusion_override(
        request, metric=metric, anchor_value_corrected_c=anchor_value_corrected_c, conn=conn
    )
    # AIFS DROPPED AS HARD DEPENDENCY (operator directive 2026-06-17). The AIFS soft-anchor result is
    # built ONLY when an AIFS extraction is present, and even then it is used as a CROSS-CHECK /
    # provenance carrier and as the q-shape's bin key-set reference — NEVER as the live q (the fused
    # Normal overrides it) and NEVER as the fail-closed fallback (see the fused-q block below). When
    # AIFS is absent (None) result stays None and the posterior materializes purely from the
    # multi-model fused Normal. FAIL-SOFT: if the soft-anchor build itself raises (a corrupt present
    # AIFS payload), we do NOT abort the fused path — result drops to None and a warning is logged;
    # the fused Normal still materializes.
    result: OpenMeteoIfs9AifsSoftAnchorResearchResult | None = None
    if request.aifs_extraction is not None:
        try:
            result = build_openmeteo_ifs9_aifs_soft_anchor_result(
                aifs_extraction=request.aifs_extraction,
                openmeteo_anchor=request.openmeteo_anchor,
                metric=metric,
                bins=request.bins,
                config=SoftAnchorConfig(anchor_weight=request.anchor_weight, anchor_sigma_c=request.anchor_sigma_c),
                settlement_step_c=float(request.settlement_step_c),
                bias_shift_c=bias_shift_c,
                member_vote_smoothing_alpha=member_vote_smoothing_alpha,
                anchor_value_override_c=(bayes_precision_fusion_override.anchor_value_c if bayes_precision_fusion_override is not None else None),
                anchor_sigma_override_c=(bayes_precision_fusion_override.anchor_sigma_c if bayes_precision_fusion_override is not None else None),
            )
        except Exception as _sa_exc:
            result = None
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 AIFS soft-anchor cross-check build skipped "
                    "(AIFS is optional; fused Normal materializes without it): %s",
                    _sa_exc,
                )
            except Exception:
                pass
    target_date = _date_text(request.target_date)
    source_cycle_time = _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat()
    # C1-AVAIL-CLOCK (2026-06-16): the posterior's source_available_at is PROOF OF POSSESSION =
    # max over the contributing roles of each role's REAL download-complete wall-clock
    # (source_run.fetch_finished_at), falling back per-role to the request's source_available_at
    # when that role has no source_run row. max() because a FUSED posterior could not be
    # constructed before its LAST-arriving input landed — availability is gated by the slowest
    # dependency. The old max(request.*_source_available_at) used the cycle-time nominal-lag GUESS
    # (~8.4h early for the baseline) as each input; this recovers the honest possession time.
    # AIFS DROPPED (operator directive 2026-06-17): the AIFS role contributes a possession-time row
    # ONLY when an AIFS extraction is present (it is still a real download that landed). When absent
    # it is excluded from the max() — the fused posterior's possession time is gated by its REAL
    # contributing roles (baseline + OM9 anchor), not a phantom AIFS input.
    _possession_candidates = [
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
    ]
    if request.aifs_extraction is not None and request.aifs_source_available_at is not None:
        _possession_candidates.append(
            _role_possession_available_at(
                conn,
                source_run_id=request.aifs_source_run_id,
                request_source_available_at=request.aifs_source_available_at,
            )
        )
    available_at = max(_possession_candidates).isoformat()
    computed_at = _to_utc(request.computed_at, field_name="computed_at").isoformat()
    data_version = _data_version(metric)
    # AIFS DROPPED (operator directive 2026-06-17 "drop aifs"): the q SEED depends on whether a
    # multi-model fused override is being attempted.
    #   * Fused override PRESENT (the live posture): seed UNIFORM (max-entropy, honest, non-tradeable)
    #     so that if BOTH the certified fused-q shape AND the fused-center-only Normal fail to build,
    #     the row carries an honest uniform q — NEVER the cold 0.8-AIFS soft-anchor pull. The fused
    #     Normal (or the fused-center-only Normal) overrides this seed on every healthy/degraded path.
    #   * No fused override at all (legacy CAPTURE_MISSING — no current multi-model capture): seed the
    #     AIFS soft-anchor q when present (the legacy member-vote shape), else uniform. This path is
    #     already non-live-eligible by q_mode, and it does NOT introduce the cold soft-anchor as a
    #     fused-path FALLBACK — it is the legacy shape for cells with no fused inputs.
    _n_bins_seed = len(request.bins) or 1
    _uniform_seed = {b.bin_id: 1.0 / _n_bins_seed for b in request.bins}
    if bayes_precision_fusion_override is not None:
        q = dict(_uniform_seed)
        q_shape = "uniform_placeholder_pending_fused"
    elif result is not None:
        q = {key: float(value) for key, value in result.posterior.probabilities.items()}
        q_shape = "aifs_member_votes_soft_anchor"
    else:
        q = dict(_uniform_seed)
        q_shape = "uniform_placeholder_pending_fused"
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
    # bin away from center). Defaults defined here so the fail-closed / flag-off paths stay coherent.
    settlement_sigma_floor_catchall_capped: tuple[str, ...] = ()
    # Q_LCB / Q_UCB outputs. The certified bootstrap basis is present only when fused-q is built and
    # bound construction succeeds. If it fails, the soft-anchor Wilson fallback below may publish
    # lower/upper carrier bounds under its own non-live-eligible basis; if that fallback also fails,
    # q_lcb_json/q_ucb_json remain NULL.
    q_lcb_map: dict[str, float] | None = None
    q_ucb_map: dict[str, float] | None = None
    q_bootstrap_samples_by_bin: dict[str, list[float]] | None = None
    q_lcb_basis: str | None = None
    if bayes_precision_fusion_override is not None:
        # An override exists. Default mode while we attempt the fused-q build below.
        replacement_q_mode = REPLACEMENT_Q_MODE_SOFT_ANCHOR_FALLBACK
    if (
        bayes_precision_fusion_override is not None
        and bayes_precision_fusion_override.predictive_sigma_c is not None
        and _replacement_fused_q_shape_enabled()
    ):
        try:
            from src.calibration.emos import bin_probability_settlement  # noqa: PLC0415

            _half_step = float(request.settlement_step_c) / 2.0
            # Per-city settlement preimage: the bins declare the rounding rule (oracle_truncate
            # for Hong Kong, wmo_half_up otherwise). The integrator MUST consume it so HK's
            # asymmetric floor() preimage is used instead of the symmetric WMO one. Uniform
            # across the family (fail-loud if mixed).
            _rounding_rule = _family_rounding_rule(request.bins)
            # Wave-2 item 6 (2026-06-12): the settlement σ-floor is applied by PER-CELL DATA
            # AVAILABILITY, not a global flag (edli_settlement_sigma_floor_enabled / _required
            # merged + deleted). Look up the SAME floor the EMOS path uses (city|season|metric)
            # and widen: sigma_used = max(sigma_pred, floor). max() only WIDENS -> flatter q ->
            # fewer overconfident bets (it can never tighten). When the fitted floor exists for
            # the cell it applies; when it is absent/malformed for the cell the lookup returns
            # None and the floor is simply not applied (recorded, NEVER blocks shadow). One
            # construction rule, no knob.
            _sigma_pred = float(bayes_precision_fusion_override.predictive_sigma_c)
            _sigma_used = _sigma_pred
            replacement_sigma_basis = "fused_center_residual_std"
            # C3 CALIBRATION SURFACE (2026-06-12) — FITTED σ_pred scale (k) + uniform-mixture (w).
            # OPERATOR LAW 2026-06-12: the correction factor must be FITTED by math, never hand-set.
            # k and w are read from state/sigma_scale_fit.json (MLE over settled cells; only
            # scripts/fit_sigma_scale.py writes it). The artifact is keyed by SETTLEMENT UNIT family;
            # an unfitted family (e.g. F today, n=47<60) returns (1.0, 0.0) so the correction stays
            # INERT for it automatically. Evidence: C n=215 settled cells, fitted k≈1.58 + w≈0.28 brings
            # the mode-bin d=0 realized/expected ratio from ~0.51 to ~0.96 (see the calibration table in
            # the artifact and docs/operations/c3_sigma_calibration_surface_2026-06-12.md).
            # Contract: σ-scale applies BEFORE the floor (floor stays a lower bound on the scaled σ);
            # the uniform mixture w is applied to the FINAL normalized q below (after integration).
            # The C-only restriction is enforced by the artifact (F family unfitted → (1.0,0.0)); the
            # explicit unit gate is kept as defense-in-depth so k can never touch an F family.
            _city_unit = _city_settlement_unit_from_bins(request)
            _k, _uniform_w, _floor_steps = _replacement_sigma_scale_lookup(_city_unit)
            if _city_unit != "C":
                _k, _uniform_w = 1.0, 0.0  # defense-in-depth: only C families are corrected today
            if _k > 1.0:
                _sigma_pred = _sigma_pred * _k
                _sigma_used = _sigma_pred
                sigma_scale_k_applied = _k
            # ABSOLUTE σ-FLOOR in step units (σ-refit report 2026-06-13, task #69, GATE-2 fix).
            # σ_core = max(σ_impl·k, floor_steps·step) where step = request.settlement_step_c (the SAME
            # per-cell bin width the integrator's _half_step = step/2 derives from — reused, not
            # recomputed, so the floor is the SAME physical dispersion across C/F unit families). The
            # realized ring dispersion is ~constant in absolute (step) terms; the floor widens an
            # over-sharp forecast UP TO that dispersion and never narrows a forecast already wider
            # (max() only widens). STRICT BACKWARD COMPATIBILITY: the live artifact has NO floor_steps
            # key ⇒ _floor_steps == 0.0 ⇒ floor_value == 0.0 ⇒ max(σ_used, 0.0) == σ_used (UNCHANGED).
            # Applied unconditionally (NOT gated on _k>1) because the floor must be able to bind even at
            # k=1.0 (the refit's form is k=1.0 + absolute floor). m / the second-Normal is inert at w=0.
            # The floor widens _sigma_used ONLY and leaves _sigma_pred (the honest un-floored σ) intact,
            # exactly like the settlement σ-floor below — so the catch-all coherence cap (which caps
            # open-ended bins at their honest, un-floored mass) still bars the floor from inflating a
            # far catch-all (Paris >=26 incident invariant).
            if _floor_steps > 0.0:
                _floor_value = float(_floor_steps) * float(request.settlement_step_c)
                if math.isfinite(_floor_value) and _floor_value > _sigma_used:
                    _sigma_used = _floor_value
                    sigma_floor_steps_applied = float(_floor_steps)
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
            # CATCH-ALL EXEMPTION (2026-06-10, Paris >=26C incident /tmp/deep_verify_report.md
            # Verification A). The settlement sigma floor is calibrated on INTERIOR-bin settlement
            # dispersion and its contract is "max() only WIDENS -> flatter q -> fewer overconfident
            # bets". That contract HOLDS for interior bins (widening pulls mass AWAY from the modal
            # bin) but is VIOLATED on an OPEN-ENDED catch-all on the far side of the center: widening
            # dumps the whole outward Gaussian tail into the single open-ended bin, INFLATING its
            # mass (Paris >=26: 0.252 at predictive sigma 1.906 -> 0.384 at floored 4.326 — the exact
            # over-mass bin the wrong trade bought). RELATIONSHIP INVARIANT: a floor that may only
            # FLATTEN must never INCREASE any bin's mass. For open-ended (catch-all) bins we therefore
            # cap the floored mass at the UN-floored (predictive-sigma) mass: min(floored, unfloored).
            # This is monotone-conservative by construction and makes the inflation category
            # unconstructable regardless of the floor's magnitude. Interior / distinct-endpoint bins
            # keep the floored mass (the floor's intended interior flattening). When the floor did NOT
            # widen sigma (_sigma_used == _sigma_pred) the cap is a no-op (both masses identical).
            _catchall_capped_bins: list[str] = []

            # Honest (un-floored, predictive-sigma) mass per OPEN-ENDED catch-all bin. This is the
            # category-kill upper bound the catch-all must never exceed — for the floor AND, below,
            # the uniform mixture. Computed once at sigma_pred (the honest spread before any widening).
            _catchall_honest_mass: dict[str, float] = {}

            def _is_open_ended_bin(_b) -> bool:
                return (_b.lower_c is None) != (_b.upper_c is None)

            def _bin_mass(_b) -> float:
                _lo = None if _b.lower_c is None else float(_b.lower_c)
                _hi = None if _b.upper_c is None else float(_b.upper_c)
                _m = bin_probability_settlement(
                    mu=float(bayes_precision_fusion_override.anchor_value_c),
                    sigma=_sigma_used,
                    bin_low=_lo,
                    bin_high=_hi,
                    half_step=_half_step,
                    rounding_rule=_rounding_rule,
                )
                # Open-ended catch-all bin: exactly one bound is None. Cap floored mass at the
                # un-floored (predictive-sigma) mass so the floor can never inflate the tail.
                if _is_open_ended_bin(_b):
                    _m_unfloored = bin_probability_settlement(
                        mu=float(bayes_precision_fusion_override.anchor_value_c),
                        sigma=_sigma_pred,
                        bin_low=_lo,
                        bin_high=_hi,
                        half_step=_half_step,
                        rounding_rule=_rounding_rule,
                    )
                    _catchall_honest_mass[_b.bin_id] = float(_m_unfloored)
                    if _sigma_used > _sigma_pred and _m_unfloored < _m:
                        _catchall_capped_bins.append(_b.bin_id)
                        _m = _m_unfloored
                return _m

            _fused_q = {b.bin_id: _bin_mass(b) for b in request.bins}
            settlement_sigma_floor_catchall_capped = tuple(_catchall_capped_bins)
            if set(_fused_q) != set(q):
                raise ValueError(
                    f"fused-q bin keys != soft-anchor q keys ({sorted(_fused_q)[:3]}... vs "
                    f"{sorted(q)[:3]}...)"
                )
            _total = sum(_fused_q.values())
            if not (_total > 0.0 and math.isfinite(_total)):
                raise ValueError(f"fused-q mass not positive-finite: {_total}")
            q = {key: float(value) / _total for key, value in _fused_q.items()}
            # FITTED UNIFORM MIXTURE (2026-06-12, operator law) — applied at the SAME seam as k, to the
            # final normalized q: q_adj = (1-w)·q_normal_rescaled + w·uniform(1/n_bins). w lifts the flat
            # realized tails (d≥2) that a scaled Normal alone cannot match (the surface's flat d=0,1,2
            # curve). w comes from the SAME artifact family entry as k. C-only via the same artifact gate.
            # CATCH-ALL COHERENCE (relationship invariant, Paris >=26 incident): the SAME rule that bars
            # the floor from inflating an open-ended catch-all bars the uniform mixture from doing so —
            # after mixing, any open-ended catch-all is re-capped at its honest (predictive-sigma) mass
            # in NORMALIZED space, so neither correction can recreate the far-catch-all inflation
            # category. The mass removed by the cap is redistributed over the remaining bins (renorm).
            if _uniform_w > 0.0 and _k >= 1.0 and _city_unit == "C":
                _n_bins = len(q)
                if _n_bins > 0:
                    _u = 1.0 / _n_bins
                    _mixed = {key: (1.0 - _uniform_w) * val + _uniform_w * _u for key, val in q.items()}
                    _mtot = sum(_mixed.values())
                    if _mtot > 0.0 and math.isfinite(_mtot):
                        _q_mixed = {key: val / _mtot for key, val in _mixed.items()}
                        # Re-cap open-ended catch-all bins at their honest normalized mass (the same
                        # honest mass, normalized by the pre-mixture _total, that the floor cap used).
                        # CATEGORY-KILL FIX (2026-06-12, external review FINDING 1): the cap is an
                        # HONESTY constraint, not an artificial throttle — a capped open-ended bin must
                        # end EXACTLY at its honest mass, never above. The previous code capped, then
                        # renormalized ALL bins by _rtot; with _rtot < 1 after the cap (the common case,
                        # the cap removes mass) the divide RE-INFLATED the capped bin above its cap,
                        # resurrecting the Paris >=26 inflation category the cap exists to kill.
                        # CONSTRAINED REDISTRIBUTION: pin each capped open-ended bin at its honest mass
                        # and absorb the deficit/surplus ONLY across the UNCAPPED bins (proportionally).
                        # Capped bins are excluded from the renorm divisor so they stay exactly at cap.
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
                            # Scale uncapped bins so the whole vector sums to 1; capped bins untouched.
                            _scale = _residual / _uncapped_mass
                            q = {
                                _key: (_val if _key in _capped_now else _val * _scale)
                                for _key, _val in _q_mixed.items()
                            }
                            uniform_mixture_w_applied = _uniform_w
                            settlement_sigma_floor_catchall_capped = tuple(_catchall_capped_bins)
                        else:
                            # Degenerate: nothing capped (no-op cap), OR every bin is a capped
                            # open-ended bin / no uncapped mass to absorb the residual. With no uncapped
                            # bin to redistribute onto, plain renormalization is the only option (and is
                            # exactly the prior behavior — correct when no cap bit). DOCUMENTED tradeoff:
                            # in the all-capped degenerate case a capped bin may exceed its honest mass
                            # after the renorm divide; this is unavoidable when there is no other bin to
                            # carry the residual, and is mathematically distinct from the inflation bug
                            # (there it was a non-degenerate vector with uncapped bins available).
                            _rtot = sum(_q_mixed.values())
                            if _rtot > 0.0 and math.isfinite(_rtot):
                                q = {key: val / _rtot for key, val in _q_mixed.items()}
                                uniform_mixture_w_applied = _uniform_w
                                settlement_sigma_floor_catchall_capped = tuple(_catchall_capped_bins)
                        # POST-CONDITIONS (relationship invariant): in the non-degenerate path every
                        # capped open-ended bin sits at EXACTLY its honest mass (<= honest + 1e-9) and
                        # the total is 1.0 +/- 1e-9. Assert so a future refactor cannot silently
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
            q_shape = "fused_normal_direct"
            # Q_LCB / Q_UCB (2026-06-09) — fused-center parameter-uncertainty bootstrap. INDEPENDENT
            # fail-soft: a bound-construction error must NOT roll back the fused q point (that would
            # regress the q_shape gain). On error the certified bootstrap bounds are absent and a
            # loud WARNING is emitted; the later soft-anchor Wilson fallback may publish
            # non-certified carrier bounds under its own basis. replacement_q_mode/q_shape remain
            # diagnosable. The bounds use the SAME _sigma_used the point q integrates at
            # (settlement-floored if the floor applied) so q_lcb <= q_point <= q_ucb holds per bin;
            # center uncertainty is fused.sd (anchor_sigma_c), NOT sigma_resid (already inside
            # _sigma_used) — no double-count.
            try:
                _lcb_map, _ucb_map, _q_samples = _build_fused_q_bounds(
                    mu_star=float(bayes_precision_fusion_override.anchor_value_c),
                    center_sigma_c=float(bayes_precision_fusion_override.anchor_sigma_c),
                    predictive_sigma_c=_sigma_used,
                    bins=request.bins,
                    half_step=_half_step,
                    q_point=q,
                    rounding_rule=_rounding_rule,
                    return_samples=True,
                )
                q_lcb_map = _lcb_map
                q_ucb_map = _ucb_map
                q_bootstrap_samples_by_bin = _q_samples
                q_lcb_basis = _QLCB_BASIS
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
            # to FUSED_NORMAL_BOUNDS_MISSING — the point q is fine (shadow accrual continues) but the
            # live gate will reject this mode. This kills the two-measures disease: fused-Normal q
            # point + Wilson LCB authority = two incompatible regimes, exactly the Milan root cause.
            if q_lcb_map is None or q_ucb_map is None:
                replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_NORMAL_BOUNDS_MISSING
            elif bayes_precision_fusion_override.decorrelated_providers_complete:
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
            settlement_sigma_floor_catchall_capped = ()
            # The fused-q (incl. any σ-scale / uniform-mixture / σ-floor) was discarded → soft-anchor q
            # has none applied. Reset all three provenance fields so they cannot misreport on the
            # fallback q.
            sigma_scale_k_applied = None
            uniform_mixture_w_applied = None
            sigma_floor_steps_applied = None
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 fused-q shape skipped (fail-closed to fused-center-only / skip; "
                    "NEVER the cold 0.8-AIFS soft-anchor): %s",
                    _exc,
                )
            except Exception:
                pass
    # FUSED-CENTER-ONLY NORMAL FALLBACK (operator directive 2026-06-17 "drop aifs"). REPLACES the old
    # cold fail-closed fallback (the 0.8-AIFS soft-anchor q that pulled the center 80% toward the cold
    # AIFS mean). Reached when the live fused-q shape was NOT produced — flag-off, no fused override,
    # predictive_sigma None, or a fused-q build failure — i.e. q_shape is still the seeded
    # placeholder, NOT "fused_normal_direct". The contract: NEVER serve the AIFS soft-anchor pull as
    # the fallback. Instead, if a multi-model fused CENTER exists (override present) with a usable
    # spread, build q PURELY from N(mu*, sigma) over the SAME settlement bins (anchor_weight
    # effectively 0 — zero AIFS pull), using the SAME emos.bin_probability_settlement integrator the
    # live path uses. If even that is impossible (no override, or no finite spread), q is left at the
    # uniform seed and the row is recorded NON-tradeable (the live gate licenses only
    # FUSED_NORMAL_{FULL,PARTIAL} with the certified bootstrap basis, which this fallback never
    # carries). This fallback is HONEST-shadow only; it is intentionally not live-eligible.
    # FAIL-SOFT: any error leaves q at the prior value and logs — never a cold-AIFS or half shape.
    if q_shape != "fused_normal_direct" and bayes_precision_fusion_override is not None:
        try:
            _fc_mu = float(bayes_precision_fusion_override.anchor_value_c)
            # Spread: ONLY the predictive settlement sigma (sqrt(fused.sd^2 + sigma_resid^2)) is a
            # valid dispersion for a settlement bin Normal. We deliberately do NOT substitute
            # anchor_sigma_c (the fused CENTER uncertainty) when predictive_sigma_c is None — that
            # conflates center uncertainty with predictive settlement spread (the q point and q bounds
            # intentionally separate the two). When predictive_sigma_c is None (residual substrate too
            # thin) we leave q at the honest uniform seed (non-tradeable) rather than fabricate a
            # spread — "if even a fused-center Normal is impossible, do NOT serve a cold/wrong q".
            _fc_sigma_raw = bayes_precision_fusion_override.predictive_sigma_c
            _fc_sigma = float(_fc_sigma_raw) if _fc_sigma_raw is not None else None
            if _fc_sigma is not None and math.isfinite(_fc_sigma) and _fc_sigma > 0.0 and math.isfinite(_fc_mu):
                from src.calibration.emos import bin_probability_settlement  # noqa: PLC0415

                _fc_half_step = float(request.settlement_step_c) / 2.0
                _fc_rounding_rule = _family_rounding_rule(request.bins)
                _fc_q = {
                    b.bin_id: bin_probability_settlement(
                        mu=_fc_mu,
                        sigma=_fc_sigma,
                        bin_low=(None if b.lower_c is None else float(b.lower_c)),
                        bin_high=(None if b.upper_c is None else float(b.upper_c)),
                        half_step=_fc_half_step,
                        rounding_rule=_fc_rounding_rule,
                    )
                    for b in request.bins
                }
                _fc_total = sum(_fc_q.values())
                if _fc_total > 0.0 and math.isfinite(_fc_total):
                    q = {key: float(value) / _fc_total for key, value in _fc_q.items()}
                    q_shape = "fused_center_only_normal"
                    # Only the predictive settlement sigma reaches this block (the guard requires a
                    # finite _fc_sigma sourced solely from predictive_sigma_c), so the basis is the
                    # fused-center residual std — never the center-uncertainty sd.
                    replacement_sigma_basis = "fused_center_residual_std"
                    # Distinct mode: a fused CENTER materialized the q but the certified fused-q
                    # bootstrap shape/bounds did NOT (so the live gate still rejects it). Diagnosably
                    # different from FUSED_Q_BUILD_FAILED (no center q at all) and from the deliberate
                    # SOFT_ANCHOR_FALLBACK (which no longer pulls toward AIFS).
                    replacement_q_mode = REPLACEMENT_Q_MODE_FUSED_CENTER_ONLY_NORMAL
        except Exception as _fcexc:
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 fused-center-only Normal fallback skipped "
                    "(keeping uniform/soft seed, NEVER cold-AIFS): %s",
                    _fcexc,
                )
            except Exception:
                pass
    # SOFT-ANCHOR Q_LCB/Q_UCB FALLBACK (2026-06-12; q_ucb added 2026-06-13) — PROMOTE the
    # Wilson-over-AIFS-votes BOUNDS into the materializer so NO posterior is born with a NULL bound.
    # Reached ONLY when the fused-center bootstrap did not produce a bound (q_lcb_map is None):
    # flag-off, no BAYES_PRECISION_FUSION override (CAPTURE_MISSING — the persisted current capture
    # was absent), predictive_sigma None, or a fused-q build failure. The bounds are the SAME
    # estimator the live decision path computed at read time (single-authority law) — built from the
    # AIFS member-vote probabilities the soft-anchor posterior already carries — now computed ONCE
    # here with their OWN basis.
    #
    # Q_UCB ROOT FIX (2026-06-13): the soft-anchor path used to build ONLY the one-sided q_lcb and
    # leave q_ucb NULL, so 100% of CAPTURE_MISSING rows (the entire q_ucb-less population on the
    # 06-14 surface: 158/158, fingerprinted by replacement_q_mode=CAPTURE_MISSING +
    # q_lcb_basis=wilson_aifs_member_votes) were born HALF-BOUNDED. A fused row never exists on this
    # path (the fused inputs mu*/center_sigma are genuinely absent — no honest FUSED upper band can
    # be built), so the operator's "input genuinely missing -> mark non-tradeable, do NOT serve a
    # half-bound" rule governs: the row carries BOTH genuine Wilson bounds (the upper is the exact
    # symmetric counterpart of the lower, same inputs/z) and STAYS non-tradeable by basis + q_mode.
    # The bounds are built ATOMICALLY (both-or-neither): a half-bound is never written.
    #
    # FAIL-SOFT: any error leaves q_lcb_map AND q_ucb_map None (NULL written, status-quo Wilson
    # read-time fallback) — never WORSE. The DISTINCT basis means the calibration credential reader
    # does NOT treat this as the certified bootstrap basis, so a CAPTURE_MISSING / SOFT_ANCHOR row is
    # STILL not live-eligible (correct: n=21<min_n=30 settled CAPTURE_MISSING cells → insufficient
    # coverage to license).
    # AIFS DROPPED (operator directive 2026-06-17): the Wilson-over-AIFS-member-votes carrier bounds
    # exist ONLY when an AIFS soft-anchor result is present (they read its member-vote support). When
    # AIFS is absent (result is None) there is no member-vote substrate, so the bounds stay NULL and
    # the row is non-tradeable by basis — the correct honest posture for a fused-center-only shadow
    # row (the live gate licenses only the certified fused bootstrap basis anyway).
    #
    # AIFS DROPPED FROM THE FUSED FALLBACK (operator directive 2026-06-17): the Wilson-over-AIFS-votes
    # carrier bounds attach ONLY to the LEGACY no-fused-override path (bayes_precision_fusion_override
    # is None — a genuine CAPTURE_MISSING cell with no multi-model inputs, where the AIFS member-vote
    # support is the only honest bound substrate). On a FUSED path (override present) where the
    # certified bootstrap did not run (fused-center-only / fused-q build failed), bounds stay NULL —
    # we never decorate a fused-center row with cold-AIFS bounds. Non-tradeable either way.
    if q_lcb_map is None and result is not None and bayes_precision_fusion_override is None:
        try:
            _aifs_probs = dict(result.aifs_probabilities.probabilities)
            _member_count = float(len(result.aifs_probabilities.member_values_c)) or 51.0
            _soft_lcb = _build_soft_anchor_wilson_lcb(
                aifs_probabilities=_aifs_probs,
                member_count=_member_count,
                q_point=q,
            )
            _soft_ucb = _build_soft_anchor_wilson_ucb(
                aifs_probabilities=_aifs_probs,
                member_count=_member_count,
                q_point=q,
            )
            # Atomic both-or-neither: only publish once BOTH bounds built (q_ucb is the genuine
            # symmetric Wilson upper, never a half-bound). The bundle reader's both-bounds
            # tradeable-grade predicate then sees a consistent carrier shape on every row.
            q_lcb_map = _soft_lcb
            q_ucb_map = _soft_ucb
            q_lcb_basis = _QLCB_SOFT_ANCHOR_BASIS
        except Exception as _wexc:
            q_lcb_map = None
            q_ucb_map = None
            q_lcb_basis = None
            try:
                import logging  # noqa: PLC0415
                logging.getLogger("zeus.replacement_bayes_precision_fusion").warning(
                    "replacement_0_1 soft-anchor Wilson q_lcb/q_ucb fallback skipped "
                    "(fail-soft to NULL bounds, read-time Wilson unchanged): %s",
                    _wexc,
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
            }
        )
    posterior_config_hash = _json_hash(posterior_config)
    family_id = f"{request.city}:{target_date}:{metric}:{bin_topology_hash}"
    # FIX 5 (2026-06-09) — capture-status provenance (recording only; the FIX-1 live gate is the
    # enforcement point, and BAYES_PRECISION_FUSION_CAPTURE_MISSING already covers the dangerous no-override case).
    # Derived from the SAME K3 completeness verdict the fusion computed (no parallel re-derivation):
    #   FULL_CURRENT     — override present AND all 5 decorrelated providers' current values served.
    #   PARTIAL_CURRENT  — override present but the decorrelated set was INCOMPLETE (count present).
    #   STALE_HISTORY_ONLY — no fusion override at all (capture/fusion raised or current capture
    #                        missing -> the legacy single-anchor q; no current multi-model capture).
    # DB_READ_ERROR is reserved for an explicit DB read failure surfaced by the capture reader; the
    # override layer is fail-soft (returns None) so at this seam an absent override reads as
    # STALE_HISTORY_ONLY (the live gate rejects it via BAYES_PRECISION_FUSION_CAPTURE_MISSING regardless).
    if bayes_precision_fusion_override is None:
        capture_status = REPLACEMENT_CAPTURE_STATUS_STALE_HISTORY_ONLY
    elif bayes_precision_fusion_override.decorrelated_providers_complete:
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
    trade_authority_status = _replacement_trade_authority_status(
        replacement_q_mode=replacement_q_mode,
        q_lcb_map=q_lcb_map,
        q_ucb_map=q_ucb_map,
        q_lcb_basis=q_lcb_basis,
    )
    # AIFS DROPPED (operator directive 2026-06-17): the persisted anchor_value_c is the SERVED center.
    # With AIFS present it is the soft-anchor result's center; with AIFS absent it is the fused mu*
    # (the multi-model center the q was actually built on). aifs_identity / aifs_probabilities /
    # aifs_member_count are recorded ONLY when an AIFS cross-check was present (None / {} / 0 when
    # absent) — pure provenance, never read by the live gate.
    if result is not None:
        _prov_anchor_value_c = result.anchor_value_c
    elif bayes_precision_fusion_override is not None:
        _prov_anchor_value_c = float(bayes_precision_fusion_override.anchor_value_c)
    else:
        _prov_anchor_value_c = None
    _aifs_present = request.aifs_extraction is not None
    provenance_payload = {
        "anchor_weight": request.anchor_weight,
        "anchor_sigma_c": request.anchor_sigma_c,
        "anchor_value_c": _prov_anchor_value_c,
        # Synoptic (00/12Z) vs intermediate (06/18Z) model-cycle phase. The live gate reads
        # THIS tag (fail-closed to the source_cycle_time hour when absent on legacy rows).
        "cycle_phase": cycle_phase,
        # AIFS DROPPED: cross-check provenance only. Present block when an AIFS extraction was
        # supplied; None when AIFS was dropped for this cell (the fused Normal carried the q).
        "aifs_artifact_id": request.aifs_artifact_id,
        "aifs_present": _aifs_present,
        "aifs_identity": (
            {
                "identity_decision_valid": request.aifs_extraction.identity_decision_valid,
                "identity_reason_codes": list(request.aifs_extraction.identity_reason_codes),
                "identity_decision_hash": request.aifs_extraction.identity_decision_hash,
                "member_ids_hash": request.aifs_extraction.member_ids_hash,
                "step_hours_hash": request.aifs_extraction.step_hours_hash,
                "artifact_id": request.aifs_extraction.artifact_id,
                "raw_sha256": request.aifs_extraction.raw_sha256,
                "source_product_id": request.aifs_extraction.source_product_id,
            }
            if _aifs_present
            else None
        ),
        "openmeteo_anchor_artifact_id": request.anchor_artifact_id,
        "openmeteo_precision_guard": _precision_guard_payload(request.openmeteo_precision_guard),
        "aifs_probabilities": (dict(result.aifs_probabilities.probabilities) if result is not None else {}),
        "aifs_member_count": (len(result.aifs_probabilities.member_values_c) if result is not None else 0),
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
        # C3 calibration surface 2026-06-12 — FITTED σ scale + uniform-mixture provenance (一切可被溯源).
        # Both None when inert (artifact missing / family unfitted / k=1.0,w=0.0). Float applied values
        # when the correction fired. Source: state/sigma_scale_fit.json (MLE, operator law 2026-06-12).
        # Authority: docs/operations/c3_sigma_calibration_surface_2026-06-12.md
        "sigma_scale_k_applied": sigma_scale_k_applied,
        "uniform_mixture_w_applied": uniform_mixture_w_applied,
        # FITTED absolute σ-floor (step units) provenance (σ-refit report 2026-06-13, task #69). None
        # when inert (live artifact has no floor_steps key, or the floor did not bind); the applied
        # floor_steps when σ_core was lifted to floor_steps·step. Same artifact family entry as k/w.
        "sigma_floor_steps_applied": sigma_floor_steps_applied,
        # Catch-all exemption (2026-06-10): open-ended bins whose floored mass was capped at the
        # un-floored predictive-sigma mass (the floor may only flatten, never inflate a catch-all).
        "settlement_sigma_floor_catchall_capped": list(settlement_sigma_floor_catchall_capped),
        # FIX 5 (2026-06-09): capture-status provenance (recording only).
        "capture_status": capture_status,
        # Q_LCB / Q_UCB provenance. The role is BASIS-AWARE so the certified fused-center bootstrap
        # bound and the promoted soft-anchor Wilson-over-AIFS-votes bound never alias: only the
        # bootstrap basis carries the calibration credential (event_reactor_adapter basis-exact
        # match). The percentile vectors do NOT sum to 1 (expected for bounds; require_sum=False).
        # q_lcb_map is now NULL only on a true fail-soft (the Wilson fallback itself raised).
        "q_lcb_json_role": (
            "fused_center_bootstrap_lcb"
            if q_lcb_basis == _QLCB_BASIS
            else "wilson_aifs_member_votes_lcb"
            if q_lcb_basis == _QLCB_SOFT_ANCHOR_BASIS
            else "absent_no_calibrated_lcb_available"
        ),
        # q_ucb role is BASIS-AWARE, symmetric with q_lcb_json_role: the soft-anchor Wilson upper
        # bound (built alongside its lower twin when the fused-center bootstrap did not run) must
        # NOT be mislabeled as the certified bootstrap ucb. q_ucb is published only when q_lcb was
        # (atomic both-or-neither per path), so the basis fully determines the role.
        "q_ucb_json_role": (
            "fused_center_bootstrap_ucb"
            if (q_ucb_map is not None and q_lcb_basis == _QLCB_BASIS)
            else "wilson_aifs_member_votes_ucb"
            if (q_ucb_map is not None and q_lcb_basis == _QLCB_SOFT_ANCHOR_BASIS)
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
        "bin_topology": bin_topology_payload,
        "bin_topology_hash": bin_topology_hash,
        "dependency_hash": dependency_hash,
        "posterior_config_hash": posterior_config_hash,
        "family_id": family_id,
        "posterior_authority_status": trade_authority_status,
        "runtime_policy_status": trade_authority_status,
        "trade_authority_status": trade_authority_status,
        "training_allowed": False,
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
            "fusion_authority": trade_authority_status,
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
            trade_authority_status,
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
    # AIFS DROPPED (operator directive 2026-06-17 "drop aifs"): the AIFS dependency record is emitted
    # ONLY when an AIFS extraction is actually present (cross-check provenance lineage). When AIFS is
    # dropped (the live posture) it is omitted entirely — readiness no longer requires the
    # aifs_sampled_2t role (see build_replacement_forecast_readiness.required_roles), so a posterior
    # is born with no AIFS dependency at all. baseline + OM9 anchor + posterior remain the lineage.
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
    if request.aifs_extraction is not None and request.aifs_source_available_at is not None:
        dependencies.append(
            ReplacementForecastDependency(
                role="aifs_sampled_2t",
                source_id=AIFS_SOURCE_ID,
                product_id=AIFS_PRODUCT_ID,
                data_version=_aifs_data_version(metric),
                source_run_id=request.aifs_source_run_id,
                source_available_at=request.aifs_source_available_at,  # AVAIL-POSSESSION-EXEMPTED: forwards the request's per-role possession input into the dependency lineage record (passthrough of an already-determined value, not a fresh stamp)
                artifact_id=request.aifs_artifact_id,
            )
        )
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


def materialize_replacement_forecast_live_or_diagnostic(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
) -> ReplacementForecastMaterializeResult:
    """Write anchor, posterior, and readiness rows for replacement live/diagnostic use."""

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


def materialize_replacement_forecast_shadow(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
) -> ReplacementForecastMaterializeResult:
    """Compatibility wrapper for older callers; writes live/diagnostic rows."""

    return materialize_replacement_forecast_live_or_diagnostic(conn, request)
