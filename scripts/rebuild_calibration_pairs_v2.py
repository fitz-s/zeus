# Lifecycle: created=2026-04-16; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Rebuild metric-aware calibration_pairs_v2 behind dry-run and preflight gates.
# Reuse: Inspect architecture/script_manifest.yaml and active packet receipt before live writes.

"""Rebuild calibration_pairs_v2 from ensemble_snapshots_v2 (high track).

Phase 4C — reads high-track canonical snapshots from ``ensemble_snapshots_v2``
and writes ``calibration_pairs_v2`` rows via ``add_calibration_pair_v2`` with
``metric_identity=HIGH_LOCALDAY_MAX``.

This script is the v2 successor to ``rebuild_calibration_pairs_canonical.py``.
Key differences from the legacy script:

- Source table: ``ensemble_snapshots_v2`` (not ``ensemble_snapshots``)
- Eligibility filter: ``temperature_metric='high'``, ``training_allowed=1``,
  ``causality_status='OK'``, ``authority='VERIFIED'``
- Write function: ``add_calibration_pair_v2(metric_identity=HIGH_LOCALDAY_MAX)``
- Target table: ``calibration_pairs_v2`` (never touches legacy ``calibration_pairs``)
- INV-15 enforced structurally inside ``add_calibration_pair_v2``
- ``assert_data_version_allowed`` called on every snapshot before processing

USAGE:

    # Dry-run (default, safe):
    python scripts/rebuild_calibration_pairs_v2.py

    # Live write (requires --no-dry-run --force):
    python scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force

    # Single city (development):
    python scripts/rebuild_calibration_pairs_v2.py --dry-run --city NYC --n-mc 1000

SAFETY GATES:
- ``--dry-run`` is the default. ``--no-dry-run`` alone does not write — ``--force``
  is required in addition.
- Delete is keyed on ``bin_source='canonical_v2'`` equality; legacy rows are never
  touched.
- Entire rebuild runs inside one SAVEPOINT; any exception rolls back.
- Quarantined snapshots (``is_quarantined(data_version)``) are skipped and counted.
- ``>30%`` no-observation ratio → abort.
- Zero pairs written → abort.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# T1E sentinel check: must happen before any DB connection is opened.
# Path is resolved relative to the script's own location so cwd doesn't matter.
_SENTINEL_PATH = Path(__file__).parent.parent / ".zeus" / "rebuild_lock.do_not_run_during_live"


def _check_live_sentinel() -> None:
    """Raise SystemExit(1) if the live-rebuild sentinel file exists.

    Called at module load (below). Isolated as a function so tests can patch
    it out while still importing the module's rebuild_v2 / rebuild_all_v2.
    The check fires before any sqlite3.connect call.
    """
    if _SENTINEL_PATH.exists():
        print(
            f"ERROR: Live-rebuild sentinel exists at {_SENTINEL_PATH}. "
            "Remove the sentinel before running rebuild during a non-live window.",
            file=sys.stderr,
        )
        sys.exit(1)


_check_live_sentinel()

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.decision_group import compute_id
from src.calibration.manager import season_from_date
from src.calibration.metric_specs import CalibrationMetricSpec, METRIC_SPECS
from src.calibration.store import add_calibration_pair_v2
from src.config import City, cities_by_name
from src.contracts.calibration_bins import (
    UnitProvenanceError,
    grid_for_city,
    validate_members_unit_plausible,
    validate_members_vs_observation,
)
from src.contracts.ensemble_snapshot_provenance import (
    DataVersionQuarantinedError,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
    assert_data_version_allowed,
    is_quarantined,
)
from src.contracts.settlement_semantics import SettlementSemantics
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.state.db import get_world_connection, init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.market import validate_bin_topology
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity
from scripts.verify_truth_surfaces import (
    SHARED_DB,
    build_calibration_pair_rebuild_preflight_report,
)


def iter_training_snapshots(conn: sqlite3.Connection, spec: CalibrationMetricSpec):
    return conn.execute(
        """
        SELECT *
        FROM ensemble_snapshots_v2
        WHERE temperature_metric = ?
          AND data_version = ?
          AND training_allowed = 1
          AND causality_status = 'OK'
          AND authority = 'VERIFIED'
        ORDER BY target_date, city, available_at
        """,
        (spec.identity.temperature_metric, spec.allowed_data_version),
    ).fetchall()


CANONICAL_BIN_SOURCE_V2 = "canonical_v2"

MIN_TRAINING_DATE = "2024-01-01"


@dataclass
class RebuildStatsV2:
    snapshots_scanned: int = 0
    snapshots_eligible: int = 0
    snapshots_quarantined: int = 0
    snapshots_contract_evidence_rejected: int = 0
    snapshots_no_observation: int = 0
    snapshots_unit_rejected: int = 0
    snapshots_processed: int = 0
    refused: bool = False
    pairs_written: int = 0
    pre_delete_v2_pairs: int = 0
    per_city: dict[str, int] = field(default_factory=dict)
    contract_evidence_rejection_reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "snapshots_scanned": self.snapshots_scanned,
            "snapshots_eligible": self.snapshots_eligible,
            "snapshots_quarantined": self.snapshots_quarantined,
            "snapshots_contract_evidence_rejected": self.snapshots_contract_evidence_rejected,
            "snapshots_no_observation": self.snapshots_no_observation,
            "snapshots_unit_rejected": self.snapshots_unit_rejected,
            "snapshots_processed": self.snapshots_processed,
            "refused": self.refused,
            "pairs_written": self.pairs_written,
            "pre_delete_v2_pairs": self.pre_delete_v2_pairs,
            "per_city": dict(self.per_city),
            "contract_evidence_rejection_reasons": dict(self.contract_evidence_rejection_reasons),
        }


def _row_value(row: sqlite3.Row, column: str) -> object | None:
    try:
        return row[column]
    except (IndexError, KeyError):
        return None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    columns: set[str] = set()
    for row in conn.execute(f"PRAGMA table_info({table_name})"):
        try:
            columns.add(str(row["name"]))
        except (TypeError, IndexError):
            columns.add(str(row[1]))
    return columns


def _append_optional_column_filter(
    where_parts: list[str],
    params: list[object],
    *,
    column: str,
    value: str | None,
    default: str,
    columns: set[str],
) -> None:
    if value is None:
        return
    if column in columns:
        where_parts.append(f"{column} = ?")
        params.append(value)
    elif value != default:
        where_parts.append("1 = 0")


def _snapshot_cycle_expr() -> str:
    return "substr(issue_time, 12, 2)"


def _snapshot_source_id_expr(columns: set[str]) -> str:
    source_column = (
        "NULLIF(TRIM(source_id), '')"
        if "source_id" in columns
        else "NULL"
    )
    return (
        f"COALESCE({source_column}, "
        "CASE WHEN data_version LIKE 'ecmwf_opendata_%' "
        "THEN 'ecmwf_open_data' ELSE 'tigge_mars' END)"
    )


def _snapshot_horizon_profile_expr() -> str:
    return (
        "CASE WHEN substr(issue_time, 12, 2) IN ('00', '12') "
        "THEN 'full' ELSE 'short' END"
    )


def _as_nonempty_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decode_reason_list(value: object | None) -> list[str]:
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return ["invalid_forecast_window_block_reasons_json"]
    if not isinstance(parsed, list):
        return ["invalid_forecast_window_block_reasons_json"]
    return [str(item) for item in parsed if str(item).strip()]


_LOW_CONTRACT_EVIDENCE_MARKER_FIELDS = (
    "settlement_unit",
    "settlement_rounding_policy",
    "bin_grid_id",
    "bin_schema_version",
    "forecast_window_start_utc",
    "forecast_window_end_utc",
    "forecast_window_start_local",
    "forecast_window_end_local",
    "forecast_window_attribution_status",
    "contributes_to_target_extrema",
    "forecast_window_block_reasons_json",
)

_LOW_CONTRACT_EVIDENCE_REQUIRED_FIELDS = (
    "observation_field",
    *_LOW_CONTRACT_EVIDENCE_MARKER_FIELDS,
)


_LOW_CONTRACT_EVIDENCE_REQUIRED_DATA_VERSIONS = frozenset({
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
})


def _low_contract_evidence_rejection(
    snapshot: sqlite3.Row,
    *,
    spec: CalibrationMetricSpec,
) -> str | None:
    """Return a LOW pair-rebuild block reason, preserving legacy rows.

    Old LOW rows have no persisted contract/window evidence; those stay on the
    legacy path until a new recovery data version is introduced.  If a row does
    carry the new shadow evidence, it must prove the same contract-bin outcome
    before pair generation.  This blocks accidental adjacent-day LOW training
    even if a row was mistakenly marked ``training_allowed=1`` upstream.
    """
    if spec.identity.temperature_metric != "low":
        return None

    data_version = str(_row_value(snapshot, "data_version") or "")
    evidence_present = any(
        _as_nonempty_text(_row_value(snapshot, field)) is not None
        for field in _LOW_CONTRACT_EVIDENCE_MARKER_FIELDS
    )
    if not evidence_present:
        if data_version in _LOW_CONTRACT_EVIDENCE_REQUIRED_DATA_VERSIONS:
            return "missing_low_contract_evidence_for_required_data_version"
        return None

    missing = [
        field
        for field in _LOW_CONTRACT_EVIDENCE_REQUIRED_FIELDS
        if _as_nonempty_text(_row_value(snapshot, field)) is None
    ]
    if missing:
        return "missing_low_contract_evidence:" + ",".join(missing)

    if _row_value(snapshot, "observation_field") != spec.identity.observation_field:
        return "low_observation_field_mismatch"

    status = _as_nonempty_text(_row_value(snapshot, "forecast_window_attribution_status"))
    if status != "FULLY_INSIDE_TARGET_LOCAL_DAY":
        return f"low_window_not_target_full:{status or 'UNKNOWN'}"

    try:
        contributes = int(_row_value(snapshot, "contributes_to_target_extrema"))
    except (TypeError, ValueError):
        return "low_window_contributes_to_target_extrema_invalid"
    if contributes != 1:
        return "low_window_does_not_contribute_to_target_extrema"

    block_reasons = _decode_reason_list(_row_value(snapshot, "forecast_window_block_reasons_json"))
    if block_reasons:
        return "low_window_block_reasons_present:" + ",".join(block_reasons)

    return None


def _fetch_eligible_snapshots_v2(
    conn: sqlite3.Connection,
    city_filter: Optional[str],
    spec: "CalibrationMetricSpec | None" = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    data_version_filter: Optional[str] = None,
    cycle_filter: Optional[str] = None,
    source_id_filter: Optional[str] = None,
    horizon_profile_filter: Optional[str] = None,
) -> list[sqlite3.Row]:
    """Pull eligible snapshots from ensemble_snapshots_v2 for the given spec."""
    if spec is None:
        raise ValueError(
            "_fetch_eligible_snapshots_v2 requires an explicit CalibrationMetricSpec; "
            "implicit HIGH default would hide HIGH/LOW recovery routing mistakes."
        )
    if data_version_filter and not spec.allows_data_version(data_version_filter):
        raise DataVersionQuarantinedError(
            f"rebuild_calibration_pairs_v2: --data-version={data_version_filter!r} "
            f"is not allowed for {spec.identity.temperature_metric} spec "
            f"{spec.allowed_data_versions!r}."
        )
    track = spec.identity.temperature_metric
    columns = _table_columns(conn, "ensemble_snapshots_v2")
    params: list = [track, MIN_TRAINING_DATE]
    where = (
        "WHERE temperature_metric = ? "
        "AND training_allowed = 1 "
        "AND causality_status = 'OK' "
        "AND authority = 'VERIFIED' "
        "AND members_json IS NOT NULL "
        "AND target_date >= ?"
    )
    if city_filter:
        where += " AND city = ?"
        params.append(city_filter)
    if start_date:
        where += " AND target_date >= ?"
        params.append(start_date)
    if end_date:
        where += " AND target_date <= ?"
        params.append(end_date)
    if data_version_filter:
        where += " AND data_version = ?"
        params.append(data_version_filter)
    if cycle_filter:
        where += f" AND {_snapshot_cycle_expr()} = ?"
        params.append(cycle_filter)
    if source_id_filter:
        where += f" AND {_snapshot_source_id_expr(columns)} = ?"
        params.append(source_id_filter)
    if horizon_profile_filter:
        where += f" AND {_snapshot_horizon_profile_expr()} = ?"
        params.append(horizon_profile_filter)
    sql = f"""
        SELECT *
        FROM ensemble_snapshots_v2
        {where}
        ORDER BY city, target_date, lead_hours
    """
    return conn.execute(sql, tuple(params)).fetchall()


def _fetch_verified_observation(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    *,
    spec: CalibrationMetricSpec,
) -> Optional[sqlite3.Row]:
    """One VERIFIED metric-specific observation per (city, target_date).

    Phase 7A CRITICAL-1 fix: column dispatch by spec.identity.temperature_metric
    ("high" → high_temp column; "low" → low_temp column). Return shape aliases
    the metric-specific column to ``observed_value`` so callers are uniform.
    The derived column name is safe against SQL injection because it comes from
    a dataclass ``Literal["high", "low"]``, not user input.
    """
    obs_column = "high_temp" if spec.identity.temperature_metric == "high" else "low_temp"
    return conn.execute(
        f"""
        SELECT city, target_date, {obs_column} AS observed_value, unit, authority, source
        FROM observations
        WHERE city = ? AND target_date = ? AND authority = 'VERIFIED'
          AND {obs_column} IS NOT NULL
        ORDER BY source DESC
        LIMIT 1
        """,
        (city, target_date),
    ).fetchone()


def _scoped_pair_predicate(
    *,
    conn: sqlite3.Connection,
    spec: CalibrationMetricSpec,
    city_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    data_version_filter: Optional[str] = None,
    cycle_filter: Optional[str] = None,
    source_id_filter: Optional[str] = None,
    horizon_profile_filter: Optional[str] = None,
) -> tuple[str, list]:
    columns = _table_columns(conn, "calibration_pairs_v2")
    where_parts = ["bin_source = ?", "temperature_metric = ?"]
    params: list = [CANONICAL_BIN_SOURCE_V2, spec.identity.temperature_metric]
    if city_filter:
        where_parts.append("city = ?")
        params.append(city_filter)
    if start_date:
        where_parts.append("target_date >= ?")
        params.append(start_date)
    if end_date:
        where_parts.append("target_date <= ?")
        params.append(end_date)
    if data_version_filter:
        where_parts.append("data_version = ?")
        params.append(data_version_filter)
    _append_optional_column_filter(
        where_parts, params, column="cycle", value=cycle_filter,
        default="00", columns=columns,
    )
    _append_optional_column_filter(
        where_parts, params, column="source_id", value=source_id_filter,
        default="tigge_mars", columns=columns,
    )
    _append_optional_column_filter(
        where_parts, params, column="horizon_profile", value=horizon_profile_filter,
        default="full", columns=columns,
    )
    return " AND ".join(where_parts), params


def _collect_pre_delete_count(
    conn: sqlite3.Connection,
    *,
    spec: CalibrationMetricSpec,
    city_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    data_version_filter: Optional[str] = None,
    cycle_filter: Optional[str] = None,
    source_id_filter: Optional[str] = None,
    horizon_profile_filter: Optional[str] = None,
) -> int:
    where, params = _scoped_pair_predicate(
        conn=conn,
        spec=spec,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
    )
    return conn.execute(
        f"SELECT COUNT(*) FROM calibration_pairs_v2 WHERE {where}",
        tuple(params),
    ).fetchone()[0]


def _delete_canonical_v2_slice(
    conn: sqlite3.Connection,
    *,
    spec: CalibrationMetricSpec,
    city_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    data_version_filter: Optional[str] = None,
    cycle_filter: Optional[str] = None,
    source_id_filter: Optional[str] = None,
    horizon_profile_filter: Optional[str] = None,
) -> None:
    where, params = _scoped_pair_predicate(
        conn=conn,
        spec=spec,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
    )
    conn.execute(
        f"DELETE FROM calibration_pairs_v2 WHERE {where}",
        tuple(params),
    )


def _assert_rebuild_preflight_ready(db_path: Path) -> None:
    report = build_calibration_pair_rebuild_preflight_report(db_path)
    if not report["ready"]:
        blocker_codes = sorted({item["code"] for item in report["blockers"]})
        raise RuntimeError(
            "Refusing live v2 rebuild: calibration-pair rebuild preflight is "
            f"{report['status']} ({', '.join(blocker_codes)})"
        )


def _process_snapshot_v2(
    conn: sqlite3.Connection,
    snapshot: sqlite3.Row,
    city: City,
    *,
    spec: CalibrationMetricSpec,
    n_mc: Optional[int],
    rng: np.random.Generator,
    stats: RebuildStatsV2,
) -> None:
    """Match one v2 snapshot to its observation and write calibration_pairs_v2 rows."""
    target_date = snapshot["target_date"]
    data_version = snapshot["data_version"] or ""
    source = ""  # ensemble_snapshots_v2 has no source column; INV-15 gates on data_version prefix

    # Per-spec cross-check: write-time defense against cross-metric contamination (R-AU).
    if not spec.allows_data_version(data_version):
        raise DataVersionQuarantinedError(
            f"rebuild_calibration_pairs_v2: snapshot data_version={data_version!r} "
            f"does not match spec.allowed_data_versions={spec.allowed_data_versions!r}. "
            "Cross-metric contamination refused."
        )

    # Quarantine guard (belt-and-suspenders: eligibility query already filters
    # training_allowed=1, but data_version quarantine is a write-time contract)
    assert_data_version_allowed(data_version, context="rebuild_calibration_pairs_v2")

    contract_evidence_rejection = _low_contract_evidence_rejection(snapshot, spec=spec)
    if contract_evidence_rejection is not None:
        stats.snapshots_contract_evidence_rejected += 1
        stats.contract_evidence_rejection_reasons[contract_evidence_rejection] = (
            stats.contract_evidence_rejection_reasons.get(contract_evidence_rejection, 0) + 1
        )
        print(
            f"  CONTRACT-EVIDENCE-REJECT {city.name}/{target_date}: "
            f"{contract_evidence_rejection}"
        )
        return

    obs = _fetch_verified_observation(conn, city.name, target_date, spec=spec)
    if obs is None:
        stats.snapshots_no_observation += 1
        return

    member_maxes = np.asarray(json.loads(snapshot["members_json"]), dtype=float)
    try:
        validate_members_unit_plausible(member_maxes, city)
    except UnitProvenanceError as e:
        stats.snapshots_unit_rejected += 1
        print(f"  UNIT-REJECT {city.name}/{target_date}: {e}")
        return

    grid = grid_for_city(city)
    bins = grid.as_bins()
    validate_bin_topology(bins)
    sem = SettlementSemantics.for_city(city)
    try:
        settlement_value = sem.assert_settlement_value(
            float(obs["observed_value"]),
            context="rebuild_calibration_pairs_v2",
        )
    except Exception as e:
        stats.snapshots_unit_rejected += 1
        print(f"  SETTLEMENT-REJECT {city.name}/{target_date}: {e}")
        return

    try:
        validate_members_vs_observation(member_maxes, city, settlement_value)
    except UnitProvenanceError as e:
        stats.snapshots_unit_rejected += 1
        print(f"  UNIT-VS-OBS-REJECT {city.name}/{target_date}: {e}")
        return

    p_raw_vec = p_raw_vector_from_maxes(
        member_maxes,
        city,
        sem,
        bins,
        n_mc=n_mc,
        rng=rng,
    )
    winning_bin = grid.bin_for_value(settlement_value)

    season = season_from_date(target_date, lat=city.lat)
    lead_days = float(snapshot["lead_hours"]) / 24.0
    available_at = snapshot["available_at"]
    decision_group_id = compute_id(
        city.name,
        target_date,
        snapshot["issue_time"],
        data_version,
    )

    # Phase 2.6 (2026-05-04, critic-opus BLOCKER 2): derive cycle / source_id
    # / horizon_profile from the snapshot row so rebuilt rows land in the
    # correct stratified Platt bucket. Without these args, add_calibration_pair_v2
    # falls into its schema-default branch ('00','tigge_mars','full'), silently
    # contaminating any OpenData-tagged historical snapshot with TIGGE labels.
    _rb_cycle: Optional[str] = None
    _rb_source_id: Optional[str] = None
    _rb_horizon_profile: Optional[str] = None
    try:
        _it = snapshot["issue_time"]
        if isinstance(_it, str) and len(_it) >= 13:
            _rb_cycle = _it[11:13]
        from src.calibration.forecast_calibration_domain import (
            derive_source_id_from_data_version,
        )
        _rb_source_id = derive_source_id_from_data_version(data_version)
        if _rb_cycle is not None:
            _rb_horizon_profile = "full" if _rb_cycle in ("00", "12") else "short"
    except (KeyError, ImportError, AttributeError, TypeError):
        # Best-effort: leave None so writer falls into schema-default branch.
        # We don't want a stratification derivation hiccup to crash the whole
        # rebuild — the writer's schema defaults still produce well-formed rows.
        _rb_cycle = None
        _rb_source_id = None
        _rb_horizon_profile = None

    pairs_this_snapshot = 0
    for b, p in zip(bins, p_raw_vec):
        outcome = 1 if b is winning_bin else 0
        add_calibration_pair_v2(
            conn,
            city=city.name,
            target_date=target_date,
            range_label=b.label,
            p_raw=float(p),
            outcome=outcome,
            lead_days=lead_days,
            season=season,
            cluster=city.cluster,
            forecast_available_at=available_at,
            metric_identity=spec.identity,
            training_allowed=True,
            data_version=data_version,
            source=source,
            settlement_value=settlement_value,
            decision_group_id=decision_group_id,
            bin_source=CANONICAL_BIN_SOURCE_V2,
            authority="VERIFIED",
            causality_status="OK",
            snapshot_id=snapshot["snapshot_id"],
            city_obj=city,
            cycle=_rb_cycle,
            source_id=_rb_source_id,
            horizon_profile=_rb_horizon_profile,
        )
        pairs_this_snapshot += 1

    stats.snapshots_processed += 1
    stats.pairs_written += pairs_this_snapshot
    stats.per_city[city.name] = stats.per_city.get(city.name, 0) + pairs_this_snapshot


def _dry_run_evaluate_snapshot_v2(
    conn: sqlite3.Connection,
    snapshot: sqlite3.Row,
    city: City,
    *,
    spec: CalibrationMetricSpec,
    stats: RebuildStatsV2,
) -> None:
    """Evaluate rebuild gates without writing calibration_pairs_v2 rows."""
    target_date = snapshot["target_date"]
    data_version = snapshot["data_version"] or ""

    if not spec.allows_data_version(data_version):
        raise DataVersionQuarantinedError(
            f"rebuild_calibration_pairs_v2 dry-run: snapshot data_version={data_version!r} "
            f"does not match spec.allowed_data_versions={spec.allowed_data_versions!r}."
        )
    assert_data_version_allowed(data_version, context="rebuild_calibration_pairs_v2.dry_run")

    contract_evidence_rejection = _low_contract_evidence_rejection(snapshot, spec=spec)
    if contract_evidence_rejection is not None:
        stats.snapshots_contract_evidence_rejected += 1
        stats.contract_evidence_rejection_reasons[contract_evidence_rejection] = (
            stats.contract_evidence_rejection_reasons.get(contract_evidence_rejection, 0) + 1
        )
        return

    obs = _fetch_verified_observation(conn, city.name, target_date, spec=spec)
    if obs is None:
        stats.snapshots_no_observation += 1
        return

    member_maxes = np.asarray(json.loads(snapshot["members_json"]), dtype=float)
    try:
        validate_members_unit_plausible(member_maxes, city)
        sem = SettlementSemantics.for_city(city)
        settlement_value = sem.assert_settlement_value(
            float(obs["observed_value"]),
            context="rebuild_calibration_pairs_v2.dry_run",
        )
        validate_members_vs_observation(member_maxes, city, settlement_value)
    except Exception:
        stats.snapshots_unit_rejected += 1
        return

    grid = grid_for_city(city)
    bins = grid.as_bins()
    validate_bin_topology(bins)
    stats.snapshots_processed += 1
    stats.pairs_written += len(bins)
    stats.per_city[city.name] = stats.per_city.get(city.name, 0) + len(bins)


def rebuild_v2(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    force: bool,
    spec: CalibrationMetricSpec,
    city_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    data_version_filter: Optional[str] = None,
    cycle_filter: Optional[str] = None,
    source_id_filter: Optional[str] = None,
    horizon_profile_filter: Optional[str] = None,
    n_mc: Optional[int] = None,
    rng: Optional[np.random.Generator] = None,
) -> RebuildStatsV2:
    """Run the v2 rebuild end-to-end, sharded per (city, metric) bucket.

    T1E: Each city is processed in its own SAVEPOINT + commit, bounding the
    writer-lock-hold duration to one city's write volume rather than the full
    rebuild. Replaces the previous monolithic outer SAVEPOINT design.
    """
    if rng is None:
        rng = np.random.default_rng()

    stats = RebuildStatsV2()

    print("=" * 70)
    print(f"CALIBRATION PAIRS V2 REBUILD ({spec.identity.temperature_metric} track, {CANONICAL_BIN_SOURCE_V2})")
    print("=" * 70)
    print(f"Mode:              {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    if city_filter:
        print(f"City filter:       {city_filter}")
    if start_date or end_date:
        print(f"Date filter:       {start_date or '-inf'}..{end_date or '+inf'}")
    if data_version_filter:
        print(f"Data version:      {data_version_filter}")
    if cycle_filter:
        print(f"Cycle filter:      {cycle_filter}")
    if source_id_filter:
        print(f"Source filter:     {source_id_filter}")
    if horizon_profile_filter:
        print(f"Horizon filter:    {horizon_profile_filter}")
    print(f"Bin source tag:    {CANONICAL_BIN_SOURCE_V2!r}")
    print(f"MetricIdentity:    {spec.identity}")
    print(f"n_mc per snapshot: {n_mc or 'default (ensemble_n_mc())'}")

    snapshots = _fetch_eligible_snapshots_v2(
        conn,
        city_filter=city_filter,
        spec=spec,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
    )
    stats.snapshots_scanned = len(snapshots)

    eligible: list[sqlite3.Row] = []
    for snap in snapshots:
        dv = snap["data_version"] or ""
        if is_quarantined(dv):
            stats.snapshots_quarantined += 1
            print(f"  QUARANTINED snapshot_id={snap['snapshot_id']} data_version={dv!r}")
            continue
        eligible.append(snap)
    stats.snapshots_eligible = len(eligible)

    print()
    print(f"Snapshots scanned:    {stats.snapshots_scanned}")
    print(f"  quarantined:        {stats.snapshots_quarantined}")
    print(f"  eligible:           {stats.snapshots_eligible}")

    stats.pre_delete_v2_pairs = _collect_pre_delete_count(
        conn,
        spec=spec,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
    )
    print(f"Existing canonical_v2 pairs (will delete): {stats.pre_delete_v2_pairs}")

    if dry_run:
        for snap in eligible:
            city = cities_by_name.get(snap["city"])
            if city is None:
                continue
            _dry_run_evaluate_snapshot_v2(
                conn,
                snap,
                city,
                spec=spec,
                stats=stats,
            )
        print()
        print("[dry-run] no DB changes made.")
        _print_rebuild_estimate_v2(eligible)
        _print_rebuild_gate_stats(stats)
        return stats

    if not force:
        raise RuntimeError(
            "--no-dry-run requires --force for the destructive delete path."
        )
    if not eligible:
        stats.refused = True
        raise RuntimeError(
            "Refusing live v2 rebuild: no eligible snapshots. "
            "Check that 4B ingest has populated ensemble_snapshots_v2."
        )

    # T1E: Group snapshots by city and process each city in its own bounded
    # SAVEPOINT + commit. This limits writer-lock-hold duration to one city's
    # write volume. Unknown-city snapshots are counted but do not abort the
    # entire rebuild (they are skipped per the existing soft-skip policy).
    from collections import defaultdict as _defaultdict
    city_buckets: dict[str, list] = _defaultdict(list)
    missing_city_count = 0
    for snap in eligible:
        city_name = snap["city"]
        if cities_by_name.get(city_name) is None:
            missing_city_count += 1
            continue
        city_buckets[city_name].append(snap)

    if missing_city_count:
        print(f"  WARN: {missing_city_count} snapshots had unknown city, will be skipped")

    # Hard-failure policy: unknown-city snapshots still abort (structural integrity).
    if missing_city_count:
        stats.refused = True
        raise RuntimeError(
            f"Refusing v2 rebuild: {missing_city_count} snapshots had unknown city; rolling back."
        )

    start = time.monotonic()
    for city_name, city_snaps in sorted(city_buckets.items()):
        city = cities_by_name[city_name]
        city_unit_rejected = 0

        # Per-(city, metric) SAVEPOINT — bounded transaction duration.
        conn.execute("SAVEPOINT v2_rebuild_bucket")
        try:
            # Delete the slice for this city+metric only.
            _delete_canonical_v2_slice(
                conn,
                spec=spec,
                city_filter=city_name,
                start_date=start_date,
                end_date=end_date,
                data_version_filter=data_version_filter,
                cycle_filter=cycle_filter,
                source_id_filter=source_id_filter,
                horizon_profile_filter=horizon_profile_filter,
            )
            for snap in city_snaps:
                _process_snapshot_v2(
                    conn, snap, city,
                    spec=spec,
                    n_mc=n_mc,
                    rng=rng,
                    stats=stats,
                )
                if stats.snapshots_processed % 500 == 0 and stats.snapshots_processed > 0:
                    elapsed = time.monotonic() - start
                    rate = stats.snapshots_processed / max(elapsed, 1e-6)
                    print(
                        f"  progress: {stats.snapshots_processed}/{len(eligible)} "
                        f"({rate:.1f} snap/s)"
                    )

            city_unit_rejected = stats.snapshots_unit_rejected
            conn.execute("RELEASE SAVEPOINT v2_rebuild_bucket")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT v2_rebuild_bucket")
            conn.execute("RELEASE SAVEPOINT v2_rebuild_bucket")
            raise

        # Commit after each (city, metric) bucket — bounded writer-lock hold.
        conn.commit()

    # Post-all-cities validation.
    hard_failures = missing_city_count + stats.snapshots_unit_rejected
    if hard_failures:
        stats.refused = True
        raise RuntimeError(
            f"Refusing v2 rebuild: {hard_failures} hard failures "
            f"(missing_city={missing_city_count}, "
            f"unit_rejected={stats.snapshots_unit_rejected}); "
            "some buckets may have already committed — inspect calibration_pairs_v2."
        )

    no_obs_ratio = stats.snapshots_no_observation / max(len(eligible), 1)
    if no_obs_ratio > 0.30:
        stats.refused = True
        raise RuntimeError(
            f"Refusing v2 rebuild: "
            f"{stats.snapshots_no_observation}/{len(eligible)} "
            f"({no_obs_ratio:.1%}) had no matching observation. "
            f"Expected <30%. Check WU/HKO backfill coverage."
        )

    if stats.pairs_written == 0:
        stats.refused = True
        raise RuntimeError(
            "Refusing v2 rebuild: zero pairs written; rolling back."
        )

    print()
    print("=" * 70)
    print("V2 REBUILD COMPLETE")
    print("=" * 70)
    print(f"Snapshots processed:     {stats.snapshots_processed}")
    print(f"  no matching obs:       {stats.snapshots_no_observation}")
    print(f"  unit/settlement reject:{stats.snapshots_unit_rejected}")
    print(f"Pairs written:           {stats.pairs_written}")
    if stats.per_city:
        print("Per-city pair counts:")
        for cname, n in sorted(stats.per_city.items()):
            print(f"  {cname:20s}  {n}")

    return stats


def rebuild_all_v2(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    force: bool,
    city_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    data_version_filter: Optional[str] = None,
    temperature_metric: str = "all",
    cycle_filter: Optional[str] = None,
    source_id_filter: Optional[str] = None,
    horizon_profile_filter: Optional[str] = None,
    n_mc: Optional[int] = None,
    rng: Optional[np.random.Generator] = None,
) -> dict[str, RebuildStatsV2]:
    """Rebuild calibration_pairs_v2 for all METRIC_SPECS.

    T1E: Each metric spec is processed via rebuild_v2, which commits per
    (city, metric) bucket. No outer SAVEPOINT — each bucket is independently
    atomic. A metric-level failure does not roll back previously committed
    city buckets from prior metrics; operators should inspect the DB on failure.
    Returns per-metric stats dict keyed by temperature_metric string.
    """
    per_metric: dict[str, RebuildStatsV2] = {}

    specs = [
        spec for spec in METRIC_SPECS
        if temperature_metric == "all" or spec.identity.temperature_metric == temperature_metric
    ]
    for spec in specs:
        stats = rebuild_v2(
            conn,
            dry_run=dry_run,
            force=force,
            spec=spec,
            city_filter=city_filter,
            start_date=start_date,
            end_date=end_date,
            data_version_filter=data_version_filter,
            cycle_filter=cycle_filter,
            source_id_filter=source_id_filter,
            horizon_profile_filter=horizon_profile_filter,
            n_mc=n_mc,
            rng=rng,
        )
        per_metric[spec.identity.temperature_metric] = stats

    return per_metric


def _print_rebuild_estimate_v2(eligible: list[sqlite3.Row]) -> None:
    from src.contracts.calibration_bins import C_CANONICAL_GRID, F_CANONICAL_GRID
    n_bins_f = F_CANONICAL_GRID.n_bins
    n_bins_c = C_CANONICAL_GRID.n_bins
    f_count = c_count = unknown_count = 0
    for snap in eligible:
        city = cities_by_name.get(snap["city"])
        if city is None:
            unknown_count += 1
            continue
        if city.settlement_unit == "F":
            f_count += 1
        elif city.settlement_unit == "C":
            c_count += 1
    approx = f_count * n_bins_f + c_count * n_bins_c
    print()
    print("Estimated live-write rowcount (calibration_pairs_v2):")
    print(f"  F-unit snapshots: {f_count} × {n_bins_f} bins = {f_count * n_bins_f}")
    print(f"  C-unit snapshots: {c_count} × {n_bins_c} bins = {c_count * n_bins_c}")
    print(f"  Total pairs:      {approx}")
    if unknown_count:
        print(f"  unknown-city snapshots (would be skipped): {unknown_count}")


def _print_rebuild_gate_stats(stats: RebuildStatsV2) -> None:
    print()
    print("Dry-run gate evaluation:")
    print(f"  contract-evidence rejected: {stats.snapshots_contract_evidence_rejected}")
    print(f"  no matching obs:            {stats.snapshots_no_observation}")
    print(f"  unit/settlement rejected:   {stats.snapshots_unit_rejected}")
    print(f"  snapshots passing gates:    {stats.snapshots_processed}")
    print(f"  estimated written pairs:    {stats.pairs_written}")
    if stats.contract_evidence_rejection_reasons:
        print("  contract-evidence reasons:")
        for reason, count in sorted(stats.contract_evidence_rejection_reasons.items()):
            print(f"    {count:6d}  {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild calibration_pairs_v2 from ensemble_snapshots_v2 (high track).",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Preview only — do not write to DB (default).",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Execute the rebuild. Must be combined with --force.",
    )
    parser.add_argument(
        "--force", dest="force", action="store_true", default=False,
        help="Required in addition to --no-dry-run to authorize destructive delete.",
    )
    parser.add_argument(
        "--city", dest="city", default=None,
        help="Limit rebuild to a single city name.",
    )
    parser.add_argument(
        "--start-date", dest="start_date", default=None,
        help="Limit rebuild to snapshots on/after YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end-date", dest="end_date", default=None,
        help="Limit rebuild to snapshots on/before YYYY-MM-DD.",
    )
    parser.add_argument(
        "--temperature-metric",
        dest="temperature_metric",
        choices=("high", "low", "all"),
        default="all",
        help="Metric track to rebuild (default: all).",
    )
    parser.add_argument(
        "--db", dest="db_path", default=None,
        help="Path to the world DB (default: production zeus-world.db).",
    )
    parser.add_argument(
        "--data-version",
        dest="data_version",
        default=None,
        help="Limit rebuild/delete scope to one ensemble snapshot data_version.",
    )
    parser.add_argument("--cycle", dest="cycle", default=None, help="Limit rebuild/delete scope to one UTC cycle bucket, e.g. 00 or 12.")
    parser.add_argument("--source-id", dest="source_id", default=None, help="Limit rebuild/delete scope to one forecast source bucket.")
    parser.add_argument("--horizon-profile", dest="horizon_profile", default=None, help="Limit rebuild/delete scope to one horizon profile bucket.")
    parser.add_argument(
        "--n-mc", dest="n_mc", type=int, default=None,
        help="Monte Carlo iterations per snapshot (default: ensemble_n_mc() = 10,000).",
    )
    args = parser.parse_args()

    db_path_for_preflight = Path(args.db_path) if args.db_path else SHARED_DB
    if not args.dry_run:
        try:
            _assert_rebuild_preflight_ready(db_path_for_preflight)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    if args.dry_run:
        # Dry-run: read-only URI connection — no WAL pragma, no schema mutation.
        db_path_str = args.db_path if args.db_path else str(SHARED_DB)
        conn = sqlite3.connect(f"file:{db_path_str}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 600000")
    elif args.db_path:
        conn = sqlite3.connect(args.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 600000")
        conn.execute("PRAGMA journal_mode=WAL")
        init_schema(conn)
        apply_v2_schema(conn)
    else:
        conn = get_world_connection()
        conn.execute("PRAGMA busy_timeout = 600000")
        init_schema(conn)
        apply_v2_schema(conn)

    try:
        per_metric = rebuild_all_v2(
            conn,
            dry_run=args.dry_run,
            force=args.force,
            city_filter=args.city,
            start_date=args.start_date,
            end_date=args.end_date,
            data_version_filter=args.data_version,
            temperature_metric=args.temperature_metric,
            cycle_filter=args.cycle,
            source_id_filter=args.source_id,
            horizon_profile_filter=args.horizon_profile,
            n_mc=args.n_mc,
        )
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    any_refused = any(s.refused for s in per_metric.values())
    return 1 if any_refused else 0


if __name__ == "__main__":
    sys.exit(main())
