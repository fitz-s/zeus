"""SQLite store for calibration pairs and Platt models.

Provides CRUD operations for calibration_pairs and platt_models tables.
All writes include proper timestamps. All reads enforce available_at constraint.

K4: get_pairs_for_bucket now defaults to authority_filter='VERIFIED' so all
callers get only provenance-verified pairs by default. Pass
authority_filter='any' to bypass (diagnostic / rebuild use only).
If the authority column is missing (pre-migration DB), the filter is skipped
so existing callers are not broken.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Optional

import numpy as np

from src.architecture.decorators import capability, protects
from src.state.schema_introspection import has_columns

if TYPE_CHECKING:
    from src.config import City
    from src.types.metric_identity import MetricIdentity

# INV-15: sources whose rows are canonical training data.
# All other sources produce runtime-only observations; training_allowed is
# forced to False regardless of what the caller passes.
_TRAINING_ALLOWED_SOURCES = frozenset({"tigge", "ecmwf_ens"})
_CALIBRATION_READ_TABLES = frozenset({
    "calibration_pairs",
    "platt_models",
    "platt_models_v2",
})


def _qualified_calibration_read_table(conn: sqlite3.Connection, table_name: str) -> str:
    """Return the authoritative read table for calibration runtime lookups.

    Live cycle connections are trade DB handles with the world DB attached as
    ``world``. Legacy/bootstrap left empty calibration tables in the trade DB,
    so unqualified reads can silently hit ``main.platt_models_v2`` and miss
    the populated authoritative rows in ``world.platt_models_v2``. Prefer the
    attached world table whenever it exists; plain world-DB and test
    connections continue to read their main schema.
    """
    if table_name not in _CALIBRATION_READ_TABLES:
        raise ValueError(f"unsupported calibration read table: {table_name!r}")
    try:
        attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    except sqlite3.Error as exc:
        raise RuntimeError("unable to enumerate attached databases for calibration read") from exc
    if "world" not in attached:
        return table_name
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM world.sqlite_master
            WHERE name = ? AND type IN ('table', 'view')
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"attached world DB is unavailable for calibration read table {table_name!r}"
        ) from exc
    if row is not None:
        return f"world.{table_name}"
    return table_name


def _table_info(conn: sqlite3.Connection, table_ref: str) -> list[sqlite3.Row]:
    if table_ref.startswith("world."):
        table_name = table_ref.removeprefix("world.")
        if table_name not in _CALIBRATION_READ_TABLES:
            raise ValueError(f"unsupported calibration read table: {table_name!r}")
        return conn.execute(f"PRAGMA world.table_info({table_name})").fetchall()
    if table_ref not in _CALIBRATION_READ_TABLES:
        raise ValueError(f"unsupported calibration read table: {table_ref!r}")
    return conn.execute(f"PRAGMA table_info({table_ref})").fetchall()


def _v2_table_has_stratification(conn: sqlite3.Connection, table_ref: str) -> bool:
    """True iff platt_models_v2 has cycle/source_id/horizon_profile columns.

    Thin wrapper around ``has_columns`` (PROPOSALS_2026-05-04 P2 — moved
    out of inline form during PR #59).  ``table_ref`` may be qualified
    (``"world.platt_models_v2"``); split into bare-table-name + attached
    DB before delegation.

    The migration script ``migrate_phase2_cycle_stratification.py``
    adds these columns via ALTER, so pre-migration DBs and several
    test fixtures lack them.  The loader degrades gracefully — skips
    the WHERE filters and returns None for the bucket_* fields —
    instead of raising OperationalError on legacy callers.
    """
    if table_ref.startswith("world."):
        return has_columns(
            conn, table_ref.removeprefix("world."),
            "cycle", "source_id", "horizon_profile",
            attached="world",
        )
    return has_columns(
        conn, table_ref, "cycle", "source_id", "horizon_profile",
    )


def _v2_pairs_table_has_stratification(conn: sqlite3.Connection) -> bool:
    """True iff calibration_pairs_v2 has cycle/source_id/horizon_profile columns.

    Used by add_calibration_pair_v2 to choose between the migrated INSERT
    form (with stratification columns) and the legacy form.  Pre-migration
    fixtures lack the columns so legacy form is the safe fallback.
    """
    return has_columns(
        conn, "calibration_pairs_v2",
        "cycle", "source_id", "horizon_profile",
    )


def infer_bin_width_from_label(range_label: str) -> float | None:
    """Infer finite bin width from a stored range label.

    Returns:
    - finite width for point/range bins
    - None for shoulders or unparseable labels
    """
    label = (range_label or "").strip()

    # Shoulder low/high
    if re.search(r"\u00b0[FfCc]\s+or\s+(below|lower)$", label):
        return None
    if re.search(r"\u00b0[FfCc]\s+or\s+(higher|above|more)$", label):
        return None

    # Interior range like 39-40\u00b0F
    m = re.search(r"(-?\d+\.?\d*)\s*[-\u2013]\s*(-?\d+\.?\d*)\s*\u00b0?[FfCc]?", label)
    if m:
        low = float(m.group(1))
        high = float(m.group(2))
        return max(1.0, high - low + 1.0)

    # Point bin like 10\u00b0C
    m = re.search(r"(-?\d+\.?\d*)\s*\u00b0[Cc]$", label)
    if m:
        return 1.0

    return None


def add_calibration_pair(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    range_label: str,
    p_raw: float,
    outcome: int,
    lead_days: float,
    season: str,
    cluster: str,
    forecast_available_at: str,
    settlement_value: Optional[float] = None,
    decision_group_id: Optional[str] = None,
    bias_corrected: bool = False,
    *,
    bin_source: str = "legacy",
    authority: str = "UNVERIFIED",
    city_obj: "City",
) -> None:
    """Insert a calibration pair (one per bin per settled market).

    Spec §8.1: Harvester generates 11 pairs per settlement (1 outcome=1, 10 outcome=0).
    settlement_value is stored for audit only — defensive round to integer per contract.

    2026-04-14 refactor: ``bin_source`` defaults to ``"legacy"`` so existing
    callers (market-bin-derived harvester path, generate_calibration_pairs.py)
    are unchanged. The new canonical-grid rebuild script passes
    ``bin_source="canonical_v1"`` to mark rows it owns, which the destructive
    DELETE path in that script targets by equality match.

    city_obj: City for SettlementSemantics dispatch (HKO oracle_truncate).
    Required (P10E strict). Use SettlementSemantics.for_city(city_obj).
    """
    if settlement_value is not None:
        from src.contracts.settlement_semantics import SettlementSemantics
        round_fn = SettlementSemantics.for_city(city_obj).round_values
        settlement_value = round_fn([float(settlement_value)])[0]
    if decision_group_id is None or not str(decision_group_id).strip():
        raise ValueError(
            "decision_group_id is required; use "
            "src.calibration.decision_group.compute_id() to generate it"
        )
    conn.execute("""
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days,
         season, cluster, forecast_available_at, settlement_value,
         decision_group_id, bias_corrected, bin_source, authority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (city, target_date, range_label, p_raw, outcome, lead_days,
          season, cluster, forecast_available_at, settlement_value,
          decision_group_id, int(bool(bias_corrected)), bin_source, authority))


def _resolve_training_allowed(source: str, data_version: str, requested: bool) -> bool:
    """INV-15: enforce source whitelist on training_allowed.

    Two-signal check: both data_version prefix AND explicit source (if provided)
    must be whitelisted. If either is non-whitelisted, training_allowed is forced
    to False. The whitelist covers canonical TIGGE and ecmwf_ens sources only.
    """
    # Normalize: strip whitespace and lowercase so "TIGGE_" or " tigge_..." don't bypass.
    dv_norm = (data_version or "").strip().lower()
    src_norm = (source or "").strip().lower()
    # Check data_version prefix against lowercase whitelist entries
    dv_ok = any(dv_norm.startswith(s) for s in _TRAINING_ALLOWED_SOURCES) if dv_norm else False
    # Check explicit source (empty string = not provided, skip check)
    src_ok = (src_norm in _TRAINING_ALLOWED_SOURCES) if src_norm else True
    if not (dv_ok and src_ok):
        return False
    return requested


@capability("calibration_rebuild")
def add_calibration_pair_v2(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    range_label: str,
    p_raw: float,
    outcome: int,
    lead_days: float,
    season: str,
    cluster: str,
    forecast_available_at: str,
    *,
    metric_identity: "MetricIdentity",
    training_allowed: bool,
    data_version: str,
    source: str = "",
    settlement_value: Optional[float] = None,
    decision_group_id: Optional[str] = None,
    bias_corrected: bool = False,
    bin_source: str = "canonical_v1",
    authority: str = "VERIFIED",
    causality_status: str = "OK",
    snapshot_id: Optional[int] = None,
    city_obj: "City",
    cycle: Optional[str] = None,
    source_id: Optional[str] = None,
    horizon_profile: Optional[str] = None,
) -> None:
    """Insert a calibration pair into calibration_pairs_v2.

    Requires metric_identity (4A.3 — no legacy default). INV-15: training_allowed
    is silently forced to False if source is not in the canonical whitelist
    (tigge, ecmwf_ens). Pass source= explicitly from the ingest path.

    city_obj: City for SettlementSemantics dispatch (HKO oracle_truncate).
    Required (P10E strict). Use SettlementSemantics.for_city(city_obj).
    """
    if settlement_value is not None:
        from src.contracts.settlement_semantics import SettlementSemantics
        round_fn = SettlementSemantics.for_city(city_obj).round_values
        settlement_value = round_fn([float(settlement_value)])[0]
    if decision_group_id is None or not str(decision_group_id).strip():
        raise ValueError(
            "decision_group_id is required; use "
            "src.calibration.decision_group.compute_id() to generate it"
        )
    effective_training_allowed = _resolve_training_allowed(source, data_version, training_allowed)
    # Phase 2.6 (2026-05-04): cycle/source_id/horizon_profile are stratification
    # axes added by the Phase 2 schema migration. When the caller provides them
    # we INSERT explicitly; when None we let the schema defaults apply
    # ('00','tigge_mars','full') so legacy callers that haven't been migrated
    # yet still produce well-formed rows. The migration script backfilled
    # historical rows from snapshot_id linkage; this writer is the new-row
    # path.
    #
    # Codex P1 #6 collateral (2026-05-04): degrade gracefully when the
    # calibration_pairs_v2 schema lacks the cycle/source_id/horizon_profile
    # columns (test fixtures that build the schema directly).  Pre-fix,
    # passing any non-None stratification kwarg with a pre-migration
    # schema raised OperationalError; now we route to the legacy INSERT
    # form so legacy callers and partial-schema fixtures keep working.
    _has_strat_pairs = _v2_pairs_table_has_stratification(conn)
    if cycle is None and source_id is None and horizon_profile is None or not _has_strat_pairs:
        conn.execute("""
            INSERT INTO calibration_pairs_v2
            (city, target_date, temperature_metric, observation_field, range_label,
             p_raw, outcome, lead_days, season, cluster, forecast_available_at,
             settlement_value, decision_group_id, bias_corrected, authority,
             bin_source, data_version, training_allowed, causality_status, snapshot_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            city, target_date,
            metric_identity.temperature_metric,
            metric_identity.observation_field,
            range_label, p_raw, outcome, lead_days, season, cluster,
            forecast_available_at, settlement_value, decision_group_id,
            int(bool(bias_corrected)), authority, bin_source, data_version,
            int(effective_training_allowed), causality_status, snapshot_id,
        ))
    else:
        conn.execute("""
            INSERT INTO calibration_pairs_v2
            (city, target_date, temperature_metric, observation_field, range_label,
             p_raw, outcome, lead_days, season, cluster, forecast_available_at,
             settlement_value, decision_group_id, bias_corrected, authority,
             bin_source, data_version, training_allowed, causality_status, snapshot_id,
             cycle, source_id, horizon_profile)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            city, target_date,
            metric_identity.temperature_metric,
            metric_identity.observation_field,
            range_label, p_raw, outcome, lead_days, season, cluster,
            forecast_available_at, settlement_value, decision_group_id,
            int(bool(bias_corrected)), authority, bin_source, data_version,
            int(effective_training_allowed), causality_status, snapshot_id,
            cycle if cycle is not None else "00",
            source_id if source_id is not None else "tigge_mars",
            horizon_profile if horizon_profile is not None else "full",
        ))


def _has_authority_column(conn: sqlite3.Connection) -> bool:
    """Check whether calibration_pairs has the authority column.

    Used to gracefully handle pre-migration DBs in tests and production
    until migrate_add_authority_column.py has been run.
    """
    table = _qualified_calibration_read_table(conn, "calibration_pairs")
    rows = _table_info(conn, table)
    return any(row[1] == "authority" for row in rows)


def get_pairs_for_bucket(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    authority_filter: str = 'VERIFIED',
    bin_source_filter: str | None = None,
    *,
    metric: Literal["high", "low"] | None = None,
) -> list[dict]:
    """Get calibration pairs for a bucket (cluster \u00d7 season).

    K4: authority_filter defaults to 'VERIFIED' so all callers get only
    provenance-verified pairs by default. Pass authority_filter='any' to
    bypass the filter (diagnostic / rebuild use only).

    If the authority column is absent (pre-migration DB), the filter is
    silently skipped so existing callers are not broken by the schema gap.

    `metric` makes the legacy-table HIGH-only convention structural
    (PR #19 follow-up; see docs/operations/task_2026-04-26_full_data_midstream_fix_plan).
    The legacy `calibration_pairs` schema has no `temperature_metric`
    column; per Phase 9C L3 commentary in `manager.py`, "LOW has never
    existed in legacy". Passing `metric="low"` is therefore a
    category-error and raises NotImplementedError, pointing at the v2 API.

    NOTE on error swallowing (post-review observation): `NotImplementedError`
    is `RuntimeError`-derived and would be caught by `except Exception` at
    existing call sites (evaluator.py:1029 + monitor_refresh.py:181/375).
    Today only `manager.py` reaches this function with an explicit `metric`
    value (always "high" per slice A2), so the error pathway is unreachable
    in production. A future caller passing `metric="low"` from one of those
    sites would silently fall through to "empty pairs" rather than raising
    visibly. Add an explicit `if metric == "low": raise` guard at any new
    call site if you want loud failure.

    Returns list of dicts with keys: p_raw, lead_days, outcome, range_label,
    decision_group_id.
    """
    if metric == "low":
        raise NotImplementedError(
            "get_pairs_for_bucket reads legacy `calibration_pairs`, which "
            "is HIGH-only by Phase 9C L3 convention (no temperature_metric "
            "column). LOW reads must use calibration_pairs_v2 via the v2 "
            "lookup API (load_platt_model_v2 / dedicated v2 readers)."
        )
    table = _qualified_calibration_read_table(conn, "calibration_pairs")
    if authority_filter == 'any':
        bin_clause = "AND bin_source = ?" if bin_source_filter is not None else ""
        params = (
            (cluster, season, bin_source_filter)
            if bin_source_filter is not None
            else (cluster, season)
        )
        rows = conn.execute(f"""
            SELECT p_raw, lead_days, outcome, range_label, decision_group_id
            FROM {table}
            WHERE cluster = ? AND season = ?
            {bin_clause}
            ORDER BY target_date
        """, params).fetchall()
    elif not _has_authority_column(conn):
        # M7 fix: pre-migration DB without authority column.
        # If caller requests UNVERIFIED, return empty list to prevent false-positive
        # blocks (returning all rows would look like contamination to the evaluator).
        # If caller requests VERIFIED (default), also return empty -- no verified data
        # can exist on a pre-migration DB.
        return []
    else:
        bin_clause = "AND bin_source = ?" if bin_source_filter is not None else ""
        params = (
            (cluster, season, authority_filter, bin_source_filter)
            if bin_source_filter is not None
            else (cluster, season, authority_filter)
        )
        rows = conn.execute(f"""
            SELECT p_raw, lead_days, outcome, range_label, decision_group_id
            FROM {table}
            WHERE cluster = ? AND season = ? AND authority = ?
            {bin_clause}
            ORDER BY target_date
        """, params).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        item["bin_width"] = infer_bin_width_from_label(item.get("range_label", ""))
        result.append(item)
    return result


def get_pairs_count(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    authority_filter: str = "VERIFIED",
    *,
    metric: Literal["high", "low"] | None = None,
) -> int:
    """Count calibration pairs in a bucket.

    K4.5 H5 fix: filters by authority='VERIFIED' by default.
    Pass authority_filter='any' to count all rows (diagnostics only).

    `metric` enforces the legacy-table HIGH-only convention
    (see `get_pairs_for_bucket` docstring). `metric="low"` raises
    immediately because legacy `calibration_pairs` has no
    `temperature_metric` column.
    """
    if metric == "low":
        raise NotImplementedError(
            "get_pairs_count reads legacy `calibration_pairs`, which is "
            "HIGH-only. For LOW counts use the calibration_pairs_v2 API."
        )
    table = _qualified_calibration_read_table(conn, "calibration_pairs")
    if authority_filter == "any" or not _has_authority_column(conn):
        return conn.execute(f"""
            SELECT COUNT(*) FROM {table}
            WHERE cluster = ? AND season = ?
        """, (cluster, season)).fetchone()[0]
    return conn.execute(f"""
        SELECT COUNT(*) FROM {table}
        WHERE cluster = ? AND season = ? AND authority = ?
    """, (cluster, season, authority_filter)).fetchone()[0]


def get_decision_group_count(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    authority_filter: str = "VERIFIED",
    *,
    metric: Literal["high", "low"] | None = None,
) -> int:
    """Count independent decision groups in a calibration bucket.

    `metric` enforces the legacy-table HIGH-only convention
    (see `get_pairs_for_bucket` docstring). `metric="low"` raises
    immediately because legacy `calibration_pairs` has no
    `temperature_metric` column.
    """
    if metric == "low":
        raise NotImplementedError(
            "get_decision_group_count reads legacy `calibration_pairs`, "
            "which is HIGH-only. For LOW counts use the calibration_pairs_v2 API."
        )
    table = _qualified_calibration_read_table(conn, "calibration_pairs")
    if authority_filter == "any" or not _has_authority_column(conn):
        row = conn.execute(f"""
            SELECT COUNT(DISTINCT decision_group_id) FROM {table}
            WHERE cluster = ? AND season = ? AND decision_group_id IS NOT NULL
        """, (cluster, season)).fetchone()
    else:
        row = conn.execute(f"""
            SELECT COUNT(DISTINCT decision_group_id) FROM {table}
            WHERE cluster = ? AND season = ? AND authority = ?
              AND decision_group_id IS NOT NULL
        """, (cluster, season, authority_filter)).fetchone()
    return int(row[0] or 0)


def canonical_pairs_ready_for_refit(conn: sqlite3.Connection) -> bool:
    """Check whether VERIFIED calibration pairs are exclusively canonical."""
    table = _qualified_calibration_read_table(conn, "calibration_pairs")
    row = conn.execute(f"""
        SELECT
            SUM(CASE WHEN authority = 'VERIFIED'
                      AND bin_source = 'canonical_v1'
                      AND decision_group_id IS NOT NULL
                      AND decision_group_id != ''
                     THEN 1 ELSE 0 END) AS canonical_rows,
            SUM(CASE WHEN authority = 'VERIFIED'
                      AND (bin_source != 'canonical_v1'
                           OR decision_group_id IS NULL
                           OR decision_group_id = '')
                     THEN 1 ELSE 0 END) AS unsafe_rows
        FROM {table}
    """).fetchone()
    canonical_rows = int(row["canonical_rows"] or 0) if row else 0
    unsafe_rows = int(row["unsafe_rows"] or 0) if row else 0
    return canonical_rows > 0 and unsafe_rows == 0


def save_platt_model(
    conn: sqlite3.Connection,
    bucket_key: str,
    A: float,
    B: float,
    C: float,
    bootstrap_params: list[tuple[float, float, float]],
    n_samples: int,
    brier_insample: Optional[float] = None,
    input_space: str = "raw_probability",
    authority: str = "VERIFIED",
) -> None:
    """Save a fitted Platt model.

    Uses INSERT OR REPLACE to handle refits on the UNIQUE(bucket_key) constraint.
    authority defaults to 'VERIFIED': this function writes a freshly fitted,
    trusted model. Pass authority='UNVERIFIED' only for diagnostic/test data.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO platt_models
        (bucket_key, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active, input_space, authority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, (
        bucket_key, A, B, C,
        json.dumps(bootstrap_params),
        n_samples, brier_insample, now, input_space, authority
    ))


@capability("calibration_persistence_write", lease=True)
@protects("INV-15", "INV-21")
def save_platt_model_v2(
    conn: sqlite3.Connection,
    *,
    metric_identity: "MetricIdentity",
    cluster: str,
    season: str,
    data_version: str,
    param_A: float,
    param_B: float,
    bootstrap_params: list,
    n_samples: int,
    param_C: float = 0.0,
    brier_insample: Optional[float] = None,
    input_space: str = "raw_probability",
    authority: str = "VERIFIED",
    cycle: str = "00",
    source_id: str = "tigge_mars",
    horizon_profile: str = "full",
) -> None:
    """Save a fitted Platt model to platt_models_v2.

    Requires metric_identity (4A.4 — no legacy default). Derives model_key
    from (temperature_metric, cluster, season, data_version, cycle, source_id,
    horizon_profile, input_space). Phase 2 (2026-05-04): cycle, source_id,
    horizon_profile added per may4math.md Finding 1 / DESIGN_PHASE2.
    Defaults match legacy 00z TIGGE archive for backward compat with un-migrated
    callers; production callers MUST pass explicit values from the calibration
    pair source.
    """
    model_key = (
        f"{metric_identity.temperature_metric}:{cluster}:{season}"
        f":{data_version}:{cycle}:{source_id}:{horizon_profile}:{input_space}"
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO platt_models_v2
        (model_key, temperature_metric, cluster, season, data_version,
         input_space, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active, authority,
         cycle, source_id, horizon_profile)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
    """, (
        model_key,
        metric_identity.temperature_metric,
        cluster, season, data_version, input_space,
        param_A, param_B, param_C,
        json.dumps(bootstrap_params),
        n_samples, brier_insample, now, authority,
        cycle, source_id, horizon_profile,
    ))


def deactivate_model_v2(
    conn: sqlite3.Connection,
    *,
    metric_identity: "MetricIdentity",
    cluster: str,
    season: str,
    data_version: str,
    input_space: str = "raw_probability",
    cycle: str = "00",
    source_id: str = "tigge_mars",
    horizon_profile: str = "full",
) -> int:
    """Delete the existing platt_models_v2 row for a bucket before refit.

    Returns the number of rows deleted (0 or 1). Called by refit_platt_v2.py
    before save_platt_model_v2. Deletion (not soft-deactivation) is required
    because UNIQUE(model_key) means the old row must be removed before the
    new INSERT can succeed with the same key.

    Phase 2 (2026-05-04): cycle, source_id, horizon_profile added to model_key
    per DESIGN_PHASE2_PLATT_CYCLE_STRATIFICATION.md.
    Phase 2 fix (2026-05-06): use 9-tuple column WHERE instead of reconstructed
    model_key string — matches both legacy-format-keyed and new-format-keyed rows
    by column values, not by key string reconstruction.
    """
    result = conn.execute(
        """
        DELETE FROM platt_models_v2
        WHERE temperature_metric = ?
          AND cluster = ?
          AND season = ?
          AND data_version = ?
          AND input_space = ?
          AND cycle = ?
          AND source_id = ?
          AND horizon_profile = ?
          AND is_active = 1
        """,
        (
            metric_identity.temperature_metric,
            cluster, season, data_version, input_space,
            cycle, source_id, horizon_profile,
        ),
    )
    return result.rowcount


def load_platt_model(
    conn: sqlite3.Connection,
    bucket_key: str,
) -> Optional[dict]:
    """Load a fitted Platt model. Returns None if not found, inactive, or not VERIFIED."""
    table = _qualified_calibration_read_table(conn, "platt_models")
    row = conn.execute(f"""
        SELECT param_A, param_B, param_C, bootstrap_params_json,
               n_samples, brier_insample, fitted_at, input_space
        FROM {table}
        WHERE bucket_key = ? AND is_active = 1 AND authority = 'VERIFIED'
    """, (bucket_key,)).fetchone()

    if row is None:
        return None

    # Legacy table has no Phase 2 stratification columns — bucket_* are
    # None so evaluator's transfer gate falls back to the cross-domain
    # rejection path when an OpenData forecast lands here.
    return {
        "A": row["param_A"],
        "B": row["param_B"],
        "C": row["param_C"],
        "bootstrap_params": json.loads(row["bootstrap_params_json"]),
        "n_samples": row["n_samples"],
        "brier_insample": row["brier_insample"],
        "fitted_at": row["fitted_at"],
        "input_space": row["input_space"] or "raw_probability",
        "bucket_cycle": None,
        "bucket_source_id": None,
        "bucket_horizon_profile": None,
        "bucket_data_version": None,
    }


def load_platt_model_v2(
    conn: sqlite3.Connection,
    *,
    temperature_metric: str,
    cluster: str,
    season: str,
    data_version: Optional[str] = None,
    input_space: str = "width_normalized_density",
    frozen_as_of: Optional[str] = None,
    model_key: Optional[str] = None,
    cycle: Optional[str] = None,
    source_id: Optional[str] = None,
    horizon_profile: Optional[str] = None,
) -> Optional[dict]:
    """Load a fitted Platt model from platt_models_v2 (Phase 9C L3 CRITICAL fix).

    Read-side counterpart to save_platt_model_v2 (P5). Pre-P9C: get_calibrator
    read exclusively from legacy platt_models table, bypassing metric
    discrimination — a LOW candidate would silently receive a HIGH Platt
    model. This function closes that gap at the read seam.

    2026-04-30 (post-architect-audit BLOCKER #1 fix): added explicit
    ``data_version`` filter. The UNIQUE constraint on platt_models_v2 includes
    data_version (v2_schema.py:302) and save_platt_model_v2 keys on it
    (store.py:445-452), so multiple data_versions per (metric, cluster, season)
    can coexist. Pre-fix, the SELECT picked ``ORDER BY fitted_at DESC LIMIT 1``
    — newest wins regardless of data_version. That made model selection
    invariant-by-coincidence: today only ``tigge_mx2t6_local_calendar_day_max_v1``
    (HIGH) and ``tigge_mn2t6_local_calendar_day_min_v1`` (LOW) are present, so
    the lookup was correct, but a future training surface that introduces a
    new data_version (e.g., a v2 metric upgrade) would silently shift runtime
    to the new fit without any explicit migration. The metric's expected
    data_version lives in MetricIdentity (metric_identity.py:78-90); callers
    pass ``MetricIdentity.data_version`` here.

    2026-05-03 (F1 forward-fix from RERUN_PLAN_v2.md §5): added
    ``frozen_as_of`` + ``model_key`` parameters. Without one of these, a future
    mass-refit silently takes over live serving the moment new rows land.
    ``frozen_as_of`` adds ``AND recorded_at <= ?`` so the loader cannot pick
    rows recorded after the operator-blessed snapshot. ``model_key`` overrides
    all match filters (still requires is_active=1, authority='VERIFIED') for
    explicit per-bucket pin. Both default to None → legacy behavior preserved
    for tests and unwired tooling. Production callers should thread the
    config-pinned values from
    ``src.calibration.manager.get_calibration_pin_config``.

    Filters by (temperature_metric, cluster, season[, data_version], input_space) +
    is_active=1 + authority='VERIFIED'. Returns None if no matching row exists —
    caller (get_calibrator) falls back to legacy or on-the-fly fit.

    Args:
        conn: SQLite connection (must have platt_models_v2 table applied).
        temperature_metric: "high" | "low" — matches CHECK constraint at
            v2_schema.py:229-230.
        cluster: per K3, equals the city name (one-cluster-per-city).
        season: e.g. "DJF", "MAM", "JJA", "SON" (hemisphere-flipped already).
        data_version: optional. When provided, restricts the lookup to rows
            matching this exact data_version — the canonical contract per
            INV-15 / Phase 9C metric scoping. Pass MetricIdentity.data_version
            (or its constant from metric_identity.py) to avoid coincidental
            cross-version selection. ``None`` preserves legacy behavior for
            tests and tooling that have not yet threaded the metric.
        input_space: defaults to "width_normalized_density" (canonical post-P9
            space); legacy input_space="raw_probability" is legal but stale.
        frozen_as_of: optional ISO-formatted timestamp (e.g. "2026-05-03 12:00:00").
            When provided, only rows with ``recorded_at <= frozen_as_of`` are
            returned. Use to pin live serving to an operator-blessed generation
            so future mass-refits don't silently take over (F1 forward-fix).
        model_key: optional explicit model_key pin. When provided, the loader
            matches that exact row (still gated by is_active=1 and authority=
            'VERIFIED'); all other discriminator filters are ignored. Use for
            per-(city, metric, cluster, season) pinning when a specific blessed
            calibrator must be locked.

    Returns:
        Same dict shape as load_platt_model, or None.
    """
    table = _qualified_calibration_read_table(conn, "platt_models_v2")

    # Codex P1 review #6 (2026-05-04): SELECT includes the bucket identity
    # columns (cycle, source_id, horizon_profile, data_version) so callers
    # can construct a ForecastCalibrationDomain from the row that was
    # actually loaded.  The evaluator's calibration-transfer gate uses this
    # to detect exact-match between forecast and the loaded calibrator
    # bucket — pre-fix the gate hardcoded calibrator_domain to TIGGE and
    # produced spurious SHADOW_ONLY rejections when an OpenData calibrator
    # was actually selected.
    #
    # Stratification columns are added by migrate_phase2_cycle_stratification
    # (cycle/source_id/horizon_profile) — pre-migration DBs don't have them,
    # and several tests construct the schema directly without running the
    # migration.  Detect at runtime and degrade gracefully so legacy
    # callers/fixtures keep working; gate logic in the evaluator
    # interprets a missing bucket_source_id as "fell back to legacy" and
    # still rejects cross-domain.
    _strat_cols = _v2_table_has_stratification(conn, table)
    if _strat_cols:
        _v2_select_cols = (
            "param_A, param_B, param_C, bootstrap_params_json, "
            "n_samples, brier_insample, fitted_at, input_space, "
            "cycle, source_id, horizon_profile, data_version, model_key"
        )
    else:
        _v2_select_cols = (
            "param_A, param_B, param_C, bootstrap_params_json, "
            "n_samples, brier_insample, fitted_at, input_space, "
            "data_version, model_key"
        )

    # Explicit model_key pin — bypasses match filters, still gated by auth/active
    if model_key is not None:
        row = conn.execute(
            f"""
            SELECT {_v2_select_cols}
            FROM {table}
            WHERE model_key = ?
              AND is_active = 1
              AND authority = 'VERIFIED'
            LIMIT 1
            """,
            (model_key,),
        ).fetchone()
    elif data_version is not None:
        # Phase 2 (2026-05-04): cycle/source_id/horizon_profile must filter the
        # SELECT explicitly when the caller has them.  Pre-Copilot-#2-fix the
        # SELECT silently omitted those filters when None and picked the
        # newest row by fitted_at, which meant a 12z OpenData call with one
        # of the three keys missing could load the schema-default 00z TIGGE
        # bucket.
        #
        # Copilot review #2 (2026-05-04): apply explicit policy on missing
        # stratification keys.
        #   * For OpenData data_version (ecmwf_opendata_*), there is NO
        #     legitimate schema default — fail closed (ValueError) so the
        #     caller learns about the missing key instead of getting silently
        #     wrong calibration.
        #   * For TIGGE data_version (tigge_*) — backward compat — apply the
        #     legacy schema defaults (cycle='00', source_id='tigge_mars',
        #     horizon_profile='full') so un-migrated tests/tools keep working.
        if data_version.startswith("ecmwf_opendata_") and (
            cycle is None or source_id is None or horizon_profile is None
        ):
            raise ValueError(
                "load_platt_model_v2: OpenData data_version "
                f"{data_version!r} requires all three stratification keys "
                f"(cycle, source_id, horizon_profile); got "
                f"cycle={cycle!r}, source_id={source_id!r}, "
                f"horizon_profile={horizon_profile!r}. "
                "Caller must thread Phase 2 keys via "
                "derive_phase2_keys_from_ens_result(ens_result)."
            )
        if cycle is None:
            cycle = "00"
        if source_id is None:
            source_id = "tigge_mars"
        if horizon_profile is None:
            horizon_profile = "full"
        params: list = [temperature_metric, cluster, season, data_version, input_space]
        extra_filters: list[str] = []
        if _strat_cols:
            extra_filters.extend([
                "AND cycle = ?",
                "AND source_id = ?",
                "AND horizon_profile = ?",
            ])
            params.extend([cycle, source_id, horizon_profile])
        frozen_clause = ""
        if frozen_as_of is not None:
            frozen_clause = "AND recorded_at <= ?"
            params.append(frozen_as_of)
        row = conn.execute(
            f"""
            SELECT {_v2_select_cols}
            FROM {table}
            WHERE temperature_metric = ?
              AND cluster = ?
              AND season = ?
              AND data_version = ?
              AND input_space = ?
              AND is_active = 1
              AND authority = 'VERIFIED'
              {' '.join(extra_filters)}
              {frozen_clause}
            ORDER BY fitted_at DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    else:
        params = [temperature_metric, cluster, season, input_space]
        frozen_clause = ""
        if frozen_as_of is not None:
            frozen_clause = "AND recorded_at <= ?"
            params.append(frozen_as_of)
        row = conn.execute(
            f"""
            SELECT {_v2_select_cols}
            FROM {table}
            WHERE temperature_metric = ?
              AND cluster = ?
              AND season = ?
              AND input_space = ?
              AND is_active = 1
              AND authority = 'VERIFIED'
              {frozen_clause}
            ORDER BY fitted_at DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()

    if row is None:
        return None

    # Codex P1 #6 (2026-05-04): expose the loaded bucket's identity so callers
    # can construct an exact-match ForecastCalibrationDomain instead of
    # hardcoding (tigge_mars/00/full).  When the stratification columns are
    # absent (pre-migration DB, or test fixture without ALTER), bucket_*
    # are None — evaluator interprets that as legacy fallback and still
    # rejects cross-domain.
    def _row_field(key: str) -> Optional[str]:
        try:
            return row[key]
        except (IndexError, KeyError):
            return None

    return {
        "A": row["param_A"],
        "B": row["param_B"],
        "C": row["param_C"],
        "bootstrap_params": json.loads(row["bootstrap_params_json"]),
        "n_samples": row["n_samples"],
        "brier_insample": row["brier_insample"],
        "fitted_at": row["fitted_at"],
        "input_space": row["input_space"] or "raw_probability",
        "bucket_cycle": _row_field("cycle") if _strat_cols else None,
        "bucket_source_id": _row_field("source_id") if _strat_cols else None,
        "bucket_horizon_profile": _row_field("horizon_profile") if _strat_cols else None,
        "bucket_data_version": _row_field("data_version"),
        "model_key": _row_field("model_key"),
    }


def deactivate_model(conn: sqlite3.Connection, bucket_key: str) -> None:
    """Mark a model as inactive (for refit/replacement)."""
    conn.execute("""
        UPDATE platt_models SET is_active = 0
        WHERE bucket_key = ?
    """, (bucket_key,))


# =====================================================================
# CALIBRATION_HARDENING packet — BATCH 1 read-only listing functions
# =====================================================================
# Per round3_verdict.md §1 #2 (FOURTH edge packet) + ULTIMATE_PLAN.md §4 #2
# (Extended Platt parameter monitoring). PATH A bucket-snapshot framing per
# CALIBRATION_HARDENING boot §1 KEY OPEN QUESTION #1 (PATH B decision-log
# JOIN attribution deferred; PATH C writer extension out-of-scope per
# dispatch).
#
# K1 contract: pure SELECT, no INSERT/UPDATE/DELETE, no JSON persistence.
# Sibling-coherent with EO/AD canonical-read additions (
# query_authoritative_settlement_rows precedent at src/state/db.py:3429).
# Critic-harness 27th cycle should pay particular attention to this surface
# since src/calibration/store.py is HIGH-MEDIUM per src/calibration/AGENTS.md
# L18 (persistence module on the active routing path).


def list_active_platt_models_v2(conn: sqlite3.Connection) -> list[dict]:
    """List all currently-active platt_models_v2 rows.

    K1-compliant pure-SELECT lister — counterpart to single-bucket
    load_platt_model_v2 (L515). Returns one dict per (temperature_metric,
    cluster, season, data_version, input_space) bucket where is_active=1
    AND authority='VERIFIED'. Inactive + UNVERIFIED + QUARANTINED rows are
    excluded so the result reflects only what the live evaluator would
    actually consult via load_platt_model_v2.

    Returns: list of dicts, each carrying the full v2 row shape for
    parameter monitoring. Empty list when no active VERIFIED rows exist
    (including when the platt_models_v2 table has not yet been created on
    a pre-migration DB — the SELECT returns 0 rows in either case).

    Used by src.state.calibration_observation.compute_platt_parameter_snapshot_per_bucket
    (CALIBRATION_HARDENING BATCH 1) to enumerate Platt parameter
    trajectories without re-reading the canonical surface row-by-row.
    """
    try:
        table = _qualified_calibration_read_table(conn, "platt_models_v2")
        rows = conn.execute(
            f"""
            SELECT temperature_metric, cluster, season, data_version,
                   input_space, model_key, param_A, param_B, param_C,
                   bootstrap_params_json, n_samples, brier_insample,
                   fitted_at, authority
            FROM {table}
            WHERE is_active = 1 AND authority = 'VERIFIED'
            ORDER BY temperature_metric, cluster, season, fitted_at DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        # Pre-migration DB without platt_models_v2 — same posture as
        # _has_authority_column at L197 (graceful empty for missing table).
        return []
    out: list[dict] = []
    for row in rows:
        out.append({
            "temperature_metric": row["temperature_metric"],
            "cluster": row["cluster"],
            "season": row["season"],
            "data_version": row["data_version"],
            "input_space": row["input_space"] or "raw_probability",
            "model_key": row["model_key"],
            "param_A": row["param_A"],
            "param_B": row["param_B"],
            "param_C": row["param_C"],
            "bootstrap_params": json.loads(row["bootstrap_params_json"]),
            "n_samples": row["n_samples"],
            "brier_insample": row["brier_insample"],
            "fitted_at": row["fitted_at"],
            "authority": row["authority"],
        })
    return out


def list_active_platt_models_legacy(conn: sqlite3.Connection) -> list[dict]:
    """List all currently-active legacy platt_models rows.

    K1-compliant pure-SELECT lister — counterpart to single-bucket
    load_platt_model (L488). Returns one dict per bucket_key where
    is_active=1 AND authority='VERIFIED'. Mirrors load_platt_model's read
    filter (L497) so what shows up here is what the legacy lookup path
    would actually serve.

    The legacy table has NO temperature_metric column (per Phase 9C L3
    convention "LOW has never existed in legacy" cited at get_pairs_for_bucket
    L228), NO data_version, NO input_space-as-key. bucket_key is
    `f"{cluster}_{season}"` per src/calibration/manager.py:73 (bucket_key
    helper). Both legacy and v2 readers exist because manager.py's get_calibrator
    falls back v2→legacy (L42-62 dedup pattern); both surfaces remain
    observable to the operator until full cutover.

    Used by src.state.calibration_observation.compute_platt_parameter_snapshot_per_bucket
    in conjunction with list_active_platt_models_v2 for full coverage.
    Each result dict carries an explicit `source: 'legacy'` tag downstream.
    """
    try:
        table = _qualified_calibration_read_table(conn, "platt_models")
        rows = conn.execute(
            f"""
            SELECT bucket_key, param_A, param_B, param_C,
                   bootstrap_params_json, n_samples, brier_insample,
                   fitted_at, input_space, authority
            FROM {table}
            WHERE is_active = 1 AND authority = 'VERIFIED'
            ORDER BY bucket_key, fitted_at DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    for row in rows:
        out.append({
            "bucket_key": row["bucket_key"],
            "param_A": row["param_A"],
            "param_B": row["param_B"],
            "param_C": row["param_C"],
            "bootstrap_params": json.loads(row["bootstrap_params_json"]),
            "n_samples": row["n_samples"],
            "brier_insample": row["brier_insample"],
            "fitted_at": row["fitted_at"],
            "input_space": row["input_space"] or "raw_probability",
            "authority": row["authority"],
        })
    return out
