# Lifecycle: created=2026-04-16; last_reviewed=2026-05-08; last_reused=2026-05-08
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

    # Isolated write (requires --db plus --no-dry-run --force):
    python scripts/rebuild_calibration_pairs_v2.py --db /tmp/calibration_stage.db --no-dry-run --force

    # Single city (development):
    python scripts/rebuild_calibration_pairs_v2.py --dry-run --city NYC --n-mc 1000

SAFETY GATES:
- ``--dry-run`` is the default. ``--no-dry-run`` alone does not write — ``--force``
  is required in addition.
- Write mode refuses the canonical shared world DB; pass an explicit isolated
  staging DB with ``--db`` before promotion evidence is produced.
- Delete is keyed on ``bin_source='canonical_v2'`` equality; legacy rows are never
  touched.
- Each (city, metric) bucket runs inside one SAVEPOINT and commits
  independently to bound writer-lock duration.
- Live writes mark the current rebuild scope ``in_progress`` in ``zeus_meta``
  before bucket commits and mark it ``complete`` only after all post-write gates
  pass. Consumers must refuse a rebuilt scope unless the complete sentinel is
  present for that exact scope.
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
from src.calibration.metric_specs import CalibrationMetricSpec, METRIC_SPECS
from src.calibration.store import add_calibration_pair_v2
from src.config import City, calibration_batch_rebuild_n_mc, cities_by_name
from src.contracts.season import season_from_date
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
from src.state.db import init_schema
from src.state.db_writer_lock import (  # noqa: E402
    BulkChunker,
    bulk_lock_with_chunker,
)
from src.state.schema.v2_schema import apply_v2_schema
from src.types.market import validate_bin_topology
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity
from scripts.verify_truth_surfaces import (
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
REBUILD_COMPLETE_META_PREFIX = "calibration_pairs_v2_rebuild_complete"

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


def _scope_part(value: object | None) -> str:
    text = "" if value is None else str(value).strip()
    return text or "all"


def _rebuild_complete_sentinel_key(
    *,
    spec: CalibrationMetricSpec,
    city_filter: str | None,
    start_date: str | None,
    end_date: str | None,
    data_version_filter: str | None,
    cycle_filter: str | None,
    source_id_filter: str | None,
    horizon_profile_filter: str | None,
    n_mc: int | None,
) -> str:
    """Return the exact zeus_meta key for a rebuild completion scope."""

    return ":".join(
        [
            REBUILD_COMPLETE_META_PREFIX,
            f"metric={spec.identity.temperature_metric}",
            f"bin_source={CANONICAL_BIN_SOURCE_V2}",
            f"city={_scope_part(city_filter)}",
            f"start={_scope_part(start_date)}",
            f"end={_scope_part(end_date)}",
            f"data_version={_scope_part(data_version_filter)}",
            f"cycle={_scope_part(cycle_filter)}",
            f"source_id={_scope_part(source_id_filter)}",
            f"horizon={_scope_part(horizon_profile_filter)}",
            f"n_mc={_scope_part(n_mc)}",
        ]
    )


def _rebuild_sentinel_expected_scope(
    *,
    city_filter: str | None,
    start_date: str | None,
    end_date: str | None,
    data_version_filter: str | None,
    cycle_filter: str | None,
    source_id_filter: str | None,
    horizon_profile_filter: str | None,
    n_mc: int | None,
) -> dict[str, object | None]:
    return {
        "city": city_filter,
        "start_date": start_date,
        "end_date": end_date,
        "data_version": data_version_filter,
        "cycle": cycle_filter,
        "source_id": source_id_filter,
        "horizon_profile": horizon_profile_filter,
        "n_mc": n_mc,
    }


def _rebuild_sentinel_scope_from_key(key: str) -> dict[str, object | None] | None:
    parts = key.split(":")
    if len(parts) != 11 or parts[0] != REBUILD_COMPLETE_META_PREFIX:
        return None
    values: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            return None
        name, value = part.split("=", 1)
        values[name] = value
    required = {
        "metric",
        "bin_source",
        "city",
        "start",
        "end",
        "data_version",
        "cycle",
        "source_id",
        "horizon",
        "n_mc",
    }
    if set(values) != required:
        return None

    def _unpart(value: str) -> str | None:
        return None if value == "all" else value

    return {
        "temperature_metric": values["metric"],
        "bin_source": values["bin_source"],
        "city": _unpart(values["city"]),
        "start_date": _unpart(values["start"]),
        "end_date": _unpart(values["end"]),
        "data_version": _unpart(values["data_version"]),
        "cycle": _unpart(values["cycle"]),
        "source_id": _unpart(values["source_id"]),
        "horizon_profile": _unpart(values["horizon"]),
        "n_mc": _unpart(values["n_mc"]),
    }


def _scope_scalar_overlaps(left: object | None, right: object | None) -> bool:
    left_part = _scope_part(left)
    right_part = _scope_part(right)
    return left_part == "all" or right_part == "all" or left_part == right_part


def _date_range_overlaps(
    left_start: object | None,
    left_end: object | None,
    right_start: object | None,
    right_end: object | None,
) -> bool:
    left_start_s = None if _scope_part(left_start) == "all" else str(left_start)
    left_end_s = None if _scope_part(left_end) == "all" else str(left_end)
    right_start_s = None if _scope_part(right_start) == "all" else str(right_start)
    right_end_s = None if _scope_part(right_end) == "all" else str(right_end)
    if left_start_s is not None and right_end_s is not None and left_start_s > right_end_s:
        return False
    if right_start_s is not None and left_end_s is not None and right_start_s > left_end_s:
        return False
    return True


def _rebuild_sentinel_scope_overlaps(
    sentinel_scope: dict[str, object | None],
    expected_scope: dict[str, object | None],
    *,
    include_n_mc: bool = True,
) -> bool:
    return (
        _scope_scalar_overlaps(sentinel_scope.get("city"), expected_scope.get("city"))
        and _scope_scalar_overlaps(
            sentinel_scope.get("data_version"), expected_scope.get("data_version")
        )
        and _scope_scalar_overlaps(sentinel_scope.get("cycle"), expected_scope.get("cycle"))
        and _scope_scalar_overlaps(
            sentinel_scope.get("source_id"), expected_scope.get("source_id")
        )
        and _scope_scalar_overlaps(
            sentinel_scope.get("horizon_profile"), expected_scope.get("horizon_profile")
        )
        and (
            not include_n_mc
            or _scope_part(sentinel_scope.get("n_mc")) == _scope_part(expected_scope.get("n_mc"))
        )
        and _date_range_overlaps(
            sentinel_scope.get("start_date"),
            sentinel_scope.get("end_date"),
            expected_scope.get("start_date"),
            expected_scope.get("end_date"),
        )
    )


def _load_rebuild_sentinel_payload(raw_value: object, *, key: str) -> dict[str, object]:
    try:
        payload = json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid rebuild_complete sentinel payload for {key}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid rebuild_complete sentinel payload for {key}")
    return payload


def _validate_rebuild_sentinel_payload(
    payload: dict[str, object],
    *,
    key: str,
    spec: CalibrationMetricSpec,
    expected_scope: dict[str, object | None],
) -> None:
    if payload.get("temperature_metric") != spec.identity.temperature_metric:
        raise RuntimeError(f"rebuild_complete sentinel payload metric mismatch for {key}")
    if payload.get("bin_source") != CANONICAL_BIN_SOURCE_V2:
        raise RuntimeError(f"rebuild_complete sentinel payload bin_source mismatch for {key}")
    metric_identity = payload.get("metric_identity")
    if not isinstance(metric_identity, dict):
        raise RuntimeError(f"rebuild_complete sentinel payload metric_identity missing for {key}")
    expected_identity = {
        "physical_quantity": spec.identity.physical_quantity,
        "observation_field": spec.identity.observation_field,
        "temperature_metric": spec.identity.temperature_metric,
    }
    for field, expected in expected_identity.items():
        if metric_identity.get(field) != expected:
            raise RuntimeError(
                f"rebuild_complete sentinel payload metric_identity.{field} mismatch for {key}"
            )
    scope = payload.get("scope")
    if not isinstance(scope, dict):
        raise RuntimeError(f"rebuild_complete sentinel payload scope missing for {key}")
    for field, expected in expected_scope.items():
        if _scope_part(scope.get(field)) != _scope_part(expected):
            raise RuntimeError(
                f"rebuild_complete sentinel payload scope.{field} mismatch for {key}"
            )


def _rebuild_sentinel_payload(
    *,
    status: str,
    spec: CalibrationMetricSpec,
    stats: RebuildStatsV2,
    city_filter: str | None,
    start_date: str | None,
    end_date: str | None,
    data_version_filter: str | None,
    cycle_filter: str | None,
    source_id_filter: str | None,
    horizon_profile_filter: str | None,
    n_mc: int | None,
) -> dict[str, object]:
    return {
        "status": status,
        "completed": status == "complete",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "temperature_metric": spec.identity.temperature_metric,
        "metric_identity": {
            "physical_quantity": spec.identity.physical_quantity,
            "observation_field": spec.identity.observation_field,
            "temperature_metric": spec.identity.temperature_metric,
        },
        "bin_source": CANONICAL_BIN_SOURCE_V2,
        "scope": {
            "city": city_filter,
            "start_date": start_date,
            "end_date": end_date,
            "data_version": data_version_filter,
            "cycle": cycle_filter,
            "source_id": source_id_filter,
            "horizon_profile": horizon_profile_filter,
            "n_mc": n_mc,
        },
        "stats": stats.as_dict(),
    }


def _write_rebuild_status_sentinel(
    conn: sqlite3.Connection,
    *,
    status: str,
    spec: CalibrationMetricSpec,
    stats: RebuildStatsV2,
    city_filter: str | None,
    start_date: str | None,
    end_date: str | None,
    data_version_filter: str | None,
    cycle_filter: str | None,
    source_id_filter: str | None,
    horizon_profile_filter: str | None,
    n_mc: int | None,
) -> str:
    """Persist current rebuild status for the exact scope in zeus_meta.

    This deliberately overwrites any previous ``complete`` sentinel with
    ``in_progress`` before the first bucket commit.  Otherwise a crash during a
    later rebuild could leave an old complete row beside a partial new corpus.
    """

    if status not in {"in_progress", "complete"}:
        raise ValueError(f"invalid rebuild sentinel status: {status!r}")
    key = _rebuild_complete_sentinel_key(
        spec=spec,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
        n_mc=n_mc,
    )
    payload = _rebuild_sentinel_payload(
        status=status,
        spec=spec,
        stats=stats,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
        n_mc=n_mc,
    )
    conn.execute(
        """
        INSERT INTO zeus_meta (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, json.dumps(payload, sort_keys=True)),
    )
    return key


def assert_rebuild_complete_sentinel(
    conn: sqlite3.Connection,
    *,
    spec: CalibrationMetricSpec,
    city_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    data_version_filter: str | None = None,
    cycle_filter: str | None = None,
    source_id_filter: str | None = None,
    horizon_profile_filter: str | None = None,
    n_mc: int | None = None,
) -> dict[str, object]:
    """Return the complete sentinel payload or fail closed for this scope."""

    key = _rebuild_complete_sentinel_key(
        spec=spec,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
        n_mc=n_mc,
    )
    row = conn.execute(
        "SELECT value FROM zeus_meta WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing rebuild_complete sentinel for {key}")
    raw_value = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    payload = _load_rebuild_sentinel_payload(raw_value, key=key)
    expected_scope = _rebuild_sentinel_expected_scope(
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
        n_mc=n_mc,
    )
    _validate_rebuild_sentinel_payload(
        payload,
        key=key,
        spec=spec,
        expected_scope=expected_scope,
    )
    if payload.get("status") != "complete" or payload.get("completed") is not True:
        raise RuntimeError(
            f"rebuild_complete sentinel for {key} is not complete: "
            f"{payload.get('status')!r}"
        )
    return payload


def assert_no_overlapping_incomplete_rebuild_sentinel(
    conn: sqlite3.Connection,
    *,
    spec: CalibrationMetricSpec,
    city_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    data_version_filter: str | None = None,
    cycle_filter: str | None = None,
    source_id_filter: str | None = None,
    horizon_profile_filter: str | None = None,
    n_mc: int | None = None,
) -> None:
    """Fail closed if any overlapping rebuild scope is not complete."""

    expected_scope = _rebuild_sentinel_expected_scope(
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
        n_mc=n_mc,
    )
    key_prefix = (
        f"{REBUILD_COMPLETE_META_PREFIX}:"
        f"metric={spec.identity.temperature_metric}:"
        f"bin_source={CANONICAL_BIN_SOURCE_V2}:"
    )
    rows = conn.execute(
        "SELECT key, value FROM zeus_meta WHERE key LIKE ?",
        (f"{key_prefix}%",),
    ).fetchall()
    for row in rows:
        key = row["key"] if isinstance(row, sqlite3.Row) else row[0]
        raw_value = row["value"] if isinstance(row, sqlite3.Row) else row[1]
        key_scope = _rebuild_sentinel_scope_from_key(str(key))
        if key_scope is None:
            raise RuntimeError(f"invalid rebuild_complete sentinel key: {key}")
        if key_scope.get("temperature_metric") != spec.identity.temperature_metric:
            continue
        if key_scope.get("bin_source") != CANONICAL_BIN_SOURCE_V2:
            continue
        if not _rebuild_sentinel_scope_overlaps(
            key_scope,
            expected_scope,
            include_n_mc=False,
        ):
            continue
        payload = _load_rebuild_sentinel_payload(raw_value, key=str(key))
        _validate_rebuild_sentinel_payload(
            payload,
            key=str(key),
            spec=spec,
            expected_scope={
                "city": key_scope.get("city"),
                "start_date": key_scope.get("start_date"),
                "end_date": key_scope.get("end_date"),
                "data_version": key_scope.get("data_version"),
                "cycle": key_scope.get("cycle"),
                "source_id": key_scope.get("source_id"),
                "horizon_profile": key_scope.get("horizon_profile"),
                "n_mc": key_scope.get("n_mc"),
            },
        )
        is_complete = payload.get("status") == "complete" and payload.get("completed") is True
        if is_complete:
            if not _rebuild_sentinel_scope_overlaps(
                key_scope,
                expected_scope,
                include_n_mc=True,
            ):
                continue
            continue
        if not is_complete:
            raise RuntimeError(
                f"overlapping rebuild_complete sentinel for {key} is not complete: "
                f"{payload.get('status')!r}"
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


def _resolve_isolated_calibration_write_db_path(
    db_path: str | Path | None,
    *,
    script_name: str,
) -> Path:
    """Resolve and validate the write-mode DB target for calibration bulk jobs."""
    from src.state.db import ZEUS_WORLD_DB_PATH  # noqa: PLC0415

    if db_path is None:
        raise RuntimeError(
            f"{script_name} write mode requires --db pointing at an isolated "
            "staging calibration DB; refusing to default to the canonical "
            "shared world DB."
        )
    resolved = Path(db_path).expanduser().resolve()
    shared_world = Path(ZEUS_WORLD_DB_PATH).expanduser().resolve()
    same_physical_file = False
    if resolved.exists() and shared_world.exists():
        try:
            same_physical_file = resolved.samefile(shared_world)
        except OSError:
            same_physical_file = False
    if resolved == shared_world or same_physical_file:
        raise RuntimeError(
            f"{script_name} write mode refuses the canonical shared world DB "
            f"({shared_world}); use an isolated staging calibration DB and a "
            "separate operator-approved promotion path."
        )
    return resolved


def _pre_compute_snapshot_v2(
    conn: sqlite3.Connection,
    snapshot: sqlite3.Row,
    city: City,
    *,
    spec: CalibrationMetricSpec,
    stats: RebuildStatsV2,
) -> Optional[dict]:
    """Run all pre-MC validation gates for one snapshot in main process.

    Returns a survivor payload ``{"member_maxes": list[float],
    "settlement_value": float}`` if every gate passes, otherwise updates the
    appropriate ``stats`` counter and returns None. Gate ORDER mirrors
    ``_process_snapshot_v2`` exactly so RebuildStatsV2 counters match the
    sequential path under both --workers=1 and --workers>1.
    """
    target_date = snapshot["target_date"]
    data_version = snapshot["data_version"] or ""

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
        return None

    obs = _fetch_verified_observation(conn, city.name, target_date, spec=spec)
    if obs is None:
        stats.snapshots_no_observation += 1
        return None

    member_maxes = np.asarray(json.loads(snapshot["members_json"]), dtype=float)
    try:
        validate_members_unit_plausible(member_maxes, city)
    except UnitProvenanceError as e:
        stats.snapshots_unit_rejected += 1
        print(f"  UNIT-REJECT {city.name}/{target_date}: {e}")
        return None

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
        return None

    try:
        validate_members_vs_observation(member_maxes, city, settlement_value)
    except UnitProvenanceError as e:
        stats.snapshots_unit_rejected += 1
        print(f"  UNIT-VS-OBS-REJECT {city.name}/{target_date}: {e}")
        return None

    return {
        "member_maxes": [float(x) for x in member_maxes],
        "settlement_value": float(settlement_value),
    }


def _write_snapshot_pairs_v2(
    conn: sqlite3.Connection,
    snapshot: sqlite3.Row,
    city: City,
    *,
    spec: CalibrationMetricSpec,
    p_raw_vec,
    settlement_value: float,
    bin_labels: Optional[list[str]] = None,
    winning_bin_label: Optional[str] = None,
    stats: RebuildStatsV2,
) -> None:
    """Write calibration_pairs_v2 rows for one MC-computed snapshot in main.

    ``p_raw_vec`` is the MC output (list[float] or np.ndarray).
    ``settlement_value`` is the validated obs value already produced by
    ``_pre_compute_snapshot_v2``; the caller must pass it through (we do NOT
    re-fetch obs here — that would double the DB round-trips per snapshot).

    When invoked from the parallel path, ``bin_labels`` and
    ``winning_bin_label`` are supplied by the worker; sequentially they are
    None and we recompute locally from the grid + settlement_value.

    Mirrors the post-MC body of the legacy ``_process_snapshot_v2``.
    """
    target_date = snapshot["target_date"]
    data_version = snapshot["data_version"] or ""
    source = ""  # ensemble_snapshots_v2 has no source column; INV-15 gates on data_version prefix

    grid = grid_for_city(city)
    bins = grid.as_bins()
    if bin_labels is not None and [b.label for b in bins] != list(bin_labels):
        raise RuntimeError(
            f"_write_snapshot_pairs_v2: bin_labels mismatch for "
            f"{city.name}/{target_date}: worker={bin_labels!r}, "
            f"main={[b.label for b in bins]!r}"
        )
    if winning_bin_label is not None:
        winning_bin = next((b for b in bins if b.label == winning_bin_label), None)
        if winning_bin is None:
            raise RuntimeError(
                f"_write_snapshot_pairs_v2: unknown winning_bin_label "
                f"{winning_bin_label!r} for {city.name}/{target_date}"
            )
    else:
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
    """Sequential path: pre-compute gates → MC → write, byte-identical to legacy.

    This is the workers=1 path. It composes the same primitives that the
    parallel path uses (``_pre_compute_snapshot_v2`` and
    ``_write_snapshot_pairs_v2``) so behavior cannot drift between the two.
    """
    survivor = _pre_compute_snapshot_v2(conn, snapshot, city, spec=spec, stats=stats)
    if survivor is None:
        return

    member_maxes = np.asarray(survivor["member_maxes"], dtype=float)
    grid = grid_for_city(city)
    bins = grid.as_bins()
    sem = SettlementSemantics.for_city(city)
    p_raw_vec = p_raw_vector_from_maxes(
        member_maxes,
        city,
        sem,
        bins,
        n_mc=n_mc,
        rng=rng,
    )
    _write_snapshot_pairs_v2(
        conn,
        snapshot,
        city,
        spec=spec,
        p_raw_vec=p_raw_vec,
        settlement_value=survivor["settlement_value"],
        bin_labels=None,
        winning_bin_label=None,
        stats=stats,
    )


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
    db_path: Optional[Path] = None,
    workers: int = 1,
    mc_seed_base: Optional[int] = None,
    chunker: Optional[BulkChunker] = None,
) -> RebuildStatsV2:
    """Run the v2 rebuild end-to-end, sharded per (city, metric) bucket.

    T1E: Each city is processed in its own SAVEPOINT + commit, bounding the
    writer-lock-hold duration to one city's write volume rather than the full
    rebuild. Replaces the previous monolithic outer SAVEPOINT design.
    """
    effective_n_mc = (
        int(n_mc) if n_mc is not None else calibration_batch_rebuild_n_mc()
    )
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
    print(f"n_mc per snapshot: {effective_n_mc}")

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

    sentinel_key = _write_rebuild_status_sentinel(
        conn,
        status="in_progress",
        spec=spec,
        stats=stats,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
        n_mc=effective_n_mc,
    )
    conn.commit()
    print(f"Rebuild sentinel: {sentinel_key} -> in_progress")

    if workers and workers > 1:
        from scripts._rebuild_calibration_pairs_v2_parallel import (  # noqa: PLC0415
            run_parallel_rebuild,
        )
        print(
            f"Parallel rebuild: workers={workers}, "
            f"cities={len(city_buckets)}, seed_base={mc_seed_base}"
        )
        # Compute-in-workers + write-in-main: the main conn STAYS open and is
        # the only writer. Workers receive serializable payloads only and never
        # touch sqlite. ``stats`` is mutated in place so all RebuildStatsV2
        # counters end with the same shape as the sequential path.
        run_parallel_rebuild(
            conn,
            dict(city_buckets),
            spec,
            workers=workers,
            start_date=start_date,
            end_date=end_date,
            data_version_filter=data_version_filter,
            cycle_filter=cycle_filter,
            source_id_filter=source_id_filter,
            horizon_profile_filter=horizon_profile_filter,
            n_mc=effective_n_mc,
            seed_base=mc_seed_base,
            stats=stats,
        )
    else:
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
                        n_mc=effective_n_mc,
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
            # K3 (2026-05-12): cooperative LIVE-yield at per-bucket chunk boundary.
            # When BULK rebuild is running under bulk_lock_with_chunker, this
            # probes the LIVE flock non-blocking and releases-then-reacquires
            # the bulk fcntl if a LIVE writer is mid-transaction. No-op when
            # chunker is None (preserves legacy callers + parallel path).
            if chunker is not None:
                chunker.yield_if_live_contended()

    # Post-all-cities validation.
    #
    # Hard-failure threshold (2026-05-12 revision): missing_city is ALWAYS a
    # hard failure (city registry corruption / dropped city). unit_rejected
    # is RATE-limited: small numbers of unit rejections are acceptable
    # outliers (extreme-weather days where the ensemble was confidently
    # wrong; see calibration_bins.py docstring for the floor revision).
    # We abort only if unit rejections exceed max(100, 1% of eligible) which
    # would indicate a systematic data corruption rather than scattered
    # extreme-weather misses. The previous "any unit rejection aborts" gate
    # was too tight for the natural rejection rate (~0.01% on 147k snapshots).
    unit_reject_cap = max(100, len(eligible) // 100)
    if missing_city_count:
        stats.refused = True
        raise RuntimeError(
            f"Refusing v2 rebuild: missing_city={missing_city_count} "
            "(city registry corruption); some buckets may have already "
            "committed — inspect calibration_pairs_v2."
        )
    if stats.snapshots_unit_rejected > unit_reject_cap:
        stats.refused = True
        raise RuntimeError(
            f"Refusing v2 rebuild: unit_rejected={stats.snapshots_unit_rejected} "
            f"exceeds cap={unit_reject_cap} (max(100, 1% of {len(eligible)} eligible)); "
            "this indicates systematic unit-contamination, not scattered extreme-weather "
            "outliers — inspect calibration_pairs_v2."
        )
    if stats.snapshots_unit_rejected:
        print(
            f"  WARN: {stats.snapshots_unit_rejected} snapshots unit-rejected "
            f"(within cap={unit_reject_cap}); rebuild proceeds. Inspect log for "
            f"specific cases (UNIT-VS-OBS-REJECT lines)."
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

    sentinel_key = _write_rebuild_status_sentinel(
        conn,
        status="complete",
        spec=spec,
        stats=stats,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        data_version_filter=data_version_filter,
        cycle_filter=cycle_filter,
        source_id_filter=source_id_filter,
        horizon_profile_filter=horizon_profile_filter,
        n_mc=effective_n_mc,
    )
    conn.commit()
    print(f"Rebuild sentinel: {sentinel_key} -> complete")

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
    db_path: Optional[Path] = None,
    workers: int = 1,
    mc_seed_base: Optional[int] = None,
    chunker: Optional[BulkChunker] = None,
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
            db_path=db_path,
            workers=workers,
            mc_seed_base=mc_seed_base,
            chunker=chunker,
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
        help=(
            "Monte Carlo iterations per snapshot "
            "(default: calibration_batch_rebuild_n_mc() = 1,000)."
        ),
    )
    parser.add_argument(
        "--workers", dest="workers", type=int, default=1,
        help=(
            "Number of city-level worker processes (default: 1 = sequential). "
            "Only takes effect on the live write path; --dry-run remains "
            "single-process for read-only safety."
        ),
    )
    parser.add_argument(
        "--mc-seed-base", dest="mc_seed_base", type=int, default=None,
        help=(
            "Optional integer seed base for Monte Carlo RNG. Workers derive "
            "their seed as (seed_base + worker_index) for reproducibility."
        ),
    )
    args = parser.parse_args()

    write_db_path: Path | None = None
    if not args.dry_run:
        try:
            write_db_path = _resolve_isolated_calibration_write_db_path(
                args.db_path,
                script_name="rebuild_calibration_pairs_v2.py",
            )
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        try:
            _assert_rebuild_preflight_ready(write_db_path)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    # M3 (PR #93 critic): --dry-run is read-only. Open with mode=ro URI,
    # skip schema init, skip PRAGMA journal_mode=WAL, and skip the bulk
    # writer lock entirely (no writers compete with a read-only opener).
    if args.dry_run:
        if args.db_path:
            uri_path = Path(args.db_path).resolve().as_uri().replace("file://", "file:")
            conn = sqlite3.connect(f"{uri_path}?mode=ro", uri=True)
        else:
            from src.state.db import ZEUS_WORLD_DB_PATH  # noqa: PLC0415
            uri_path = Path(ZEUS_WORLD_DB_PATH).resolve().as_uri().replace("file://", "file:")
            conn = sqlite3.connect(f"{uri_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 600000")
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

    # K3 retrofit (2026-05-12): bulk_lock_with_chunker replaces the bare
    # db_writer_lock(BULK) wrap. The chunker probes the LIVE flock at every
    # per-(city, metric) bucket commit and releases-then-reacquires the bulk
    # fcntl when a LIVE writer is mid-transaction. Preserves PR #86's
    # write-path lock invariant while adding cooperative LIVE-yield semantics.
    assert write_db_path is not None
    conn = sqlite3.connect(write_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 600000")
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    apply_v2_schema(conn)

    try:
        with bulk_lock_with_chunker(
            write_db_path,
            conn,
            caller_module="scripts.rebuild_calibration_pairs_v2",
        ) as chunker:
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
                    db_path=write_db_path,
                    workers=args.workers,
                    mc_seed_base=args.mc_seed_base,
                    chunker=chunker,
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
