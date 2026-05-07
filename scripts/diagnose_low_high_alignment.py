#!/usr/bin/env python3
# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Lifecycle: created=2026-05-07; last_reviewed=2026-05-07; last_reused=2026-05-07
# Authority basis: docs/operations/current_state.md planning-lock evidence.
# Purpose: Read-only LOW/HIGH alignment before-after diagnostic; derived context only.
# Reuse: Re-check snapshot/platt schema and current LOW Law 1 before relying on output.
"""Report current LOW/HIGH calibration asymmetry and bounded recovery upside.

This script is deliberately read-only.  It does not relax LOW Law 1, rebuild
calibration pairs, refit Platt models, or authorize live entries.  Its job is
to make the asymmetry measurable before changing live behavior:

* snapshot training eligibility by metric/source
* LOW boundary-rejection loss by city/source
* active Platt maturity by metric
* quarantined negative-A Platt buckets
* HIGH/LOW active-domain coverage
* a bounded "after" scenario that shows the maximum LOW rows recoverable from
  boundary rejections once contract-outcome/window evidence exists

The "after" scenario is an upper-bound diagnostic, not a live-trading claim.
Without persisted forecast-window-to-contract evidence, safe live recovery is
zero beyond the current baseline.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DEFAULT_DB_PATH = REPO_ROOT / "state" / "zeus-world.db"
REQUIRED_TABLES = (
    "ensemble_snapshots_v2",
    "platt_models_v2",
)

CONTRACT_OBJECT_REQUIRED_FIELDS = (
    "city",
    "target_local_date",
    "temperature_metric",
    "observation_field",
    "settlement_source_type",
    "settlement_station_id",
    "settlement_unit",
    "settlement_rounding_policy",
    "bin_grid_id",
    "bin_schema_version",
    "forecast_source_id",
    "forecast_issue_time",
    "forecast_window_start_utc",
    "forecast_window_end_utc",
    "forecast_window_start_local",
    "forecast_window_end_local",
    "forecast_window_attribution_status",
    "contributes_to_target_extrema",
    "forecast_window_block_reasons_json",
    "causality_status",
)

NO_REGRESSION_GATES = (
    "derived_report_only_no_db_writes",
    "hard_zero_for_causality_or_unknown_contract_object",
    "no_adjacent_day_low_into_target_day_training",
    "no_high_low_observation_field_flip",
    "no_fallback_without_contract_bin_compatibility",
    "trade_drift_required_before_live_promotion",
)


def _low_recovery_data_version_policy() -> dict[str, Any]:
    from src.contracts.ensemble_snapshot_provenance import (
        ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
    )

    return {
        "recovery_versions": [
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        ],
        "metric_axis": "low",
        "pair_rebuild_requirement": (
            "recovery data_versions must carry FULLY_INSIDE_TARGET_LOCAL_DAY "
            "contract-window evidence before calibration-pair writes"
        ),
        "live_promotion": "not_authorized_by_this_report",
    }


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    if db_path.stat().st_size == 0:
        raise ValueError(f"DB is empty: {db_path}")
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_for_tests(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _require_tables(conn: sqlite3.Connection) -> None:
    missing = [table for table in REQUIRED_TABLES if not _table_exists(conn, table)]
    if missing:
        raise RuntimeError("missing required tables: " + ", ".join(missing))


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _contract_evidence_schema_gap(conn: sqlite3.Connection) -> dict[str, Any]:
    columns = _table_columns(conn, "ensemble_snapshots_v2")
    aliases = {
        "target_local_date": "target_date",
        "forecast_issue_time": "issue_time",
        "forecast_source_id": "source_id",
    }
    present: list[str] = []
    missing: list[str] = []
    alias_satisfied: dict[str, str] = {}
    for field in CONTRACT_OBJECT_REQUIRED_FIELDS:
        if field in columns:
            present.append(field)
            continue
        alias = aliases.get(field)
        if alias and alias in columns:
            present.append(field)
            alias_satisfied[field] = alias
            continue
        missing.append(field)
    return {
        "table": "ensemble_snapshots_v2",
        "present_required_fields": present,
        "missing_required_fields": missing,
        "alias_satisfied_fields": alias_satisfied,
        "contract_outcome_ready": not missing,
    }


def _snapshot_eligibility(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(temperature_metric, 'unknown') AS metric,
            CASE
                WHEN COALESCE(data_version, '') LIKE 'tigge_%' THEN 'tigge_mars'
                WHEN COALESCE(data_version, '') LIKE 'ecmwf_opendata_%' THEN 'ecmwf_open_data'
                ELSE 'unknown'
            END AS source_family,
            COUNT(*) AS total_snapshots,
            SUM(CASE WHEN training_allowed = 1 AND causality_status = 'OK' THEN 1 ELSE 0 END)
                AS baseline_training_snapshots,
            SUM(CASE WHEN causality_status = 'REJECTED_BOUNDARY_AMBIGUOUS' THEN 1 ELSE 0 END)
                AS boundary_rejected_snapshots,
            SUM(CASE WHEN training_allowed = 0 AND causality_status = 'OK' THEN 1 ELSE 0 END)
                AS ok_but_not_training_snapshots
        FROM ensemble_snapshots_v2
        GROUP BY metric, source_family
        ORDER BY metric, source_family
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        total = int(item["total_snapshots"])
        baseline = int(item["baseline_training_snapshots"] or 0)
        boundary = int(item["boundary_rejected_snapshots"] or 0)
        item["baseline_training_rate"] = _pct(baseline, total)
        item["boundary_rejection_rate"] = _pct(boundary, total)
        item["contract_recovery_upper_bound_snapshots"] = baseline + boundary
        item["contract_recovery_upper_bound_rate"] = _pct(baseline + boundary, total)
        item["safe_after_without_window_metadata_snapshots"] = baseline
        item["safe_after_without_window_metadata_rate"] = _pct(baseline, total)
        result.append(item)
    return result


def _low_city_boundary_loss(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            city,
            CASE
                WHEN COALESCE(data_version, '') LIKE 'tigge_%' THEN 'tigge_mars'
                WHEN COALESCE(data_version, '') LIKE 'ecmwf_opendata_%' THEN 'ecmwf_open_data'
                ELSE 'unknown'
            END AS source_family,
            COUNT(*) AS total_snapshots,
            SUM(CASE WHEN training_allowed = 1 AND causality_status = 'OK' THEN 1 ELSE 0 END)
                AS baseline_training_snapshots,
            SUM(CASE WHEN causality_status = 'REJECTED_BOUNDARY_AMBIGUOUS' THEN 1 ELSE 0 END)
                AS boundary_rejected_snapshots
        FROM ensemble_snapshots_v2
        WHERE temperature_metric = 'low'
        GROUP BY city, source_family
        HAVING total_snapshots > 0
        ORDER BY
            CAST(baseline_training_snapshots AS REAL) / total_snapshots ASC,
            boundary_rejected_snapshots DESC,
            city ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        total = int(item["total_snapshots"])
        baseline = int(item["baseline_training_snapshots"] or 0)
        boundary = int(item["boundary_rejected_snapshots"] or 0)
        item["baseline_training_rate"] = _pct(baseline, total)
        item["boundary_rejection_rate"] = _pct(boundary, total)
        item["contract_recovery_upper_bound_snapshots"] = baseline + boundary
        item["contract_recovery_upper_bound_rate"] = _pct(baseline + boundary, total)
        result.append(item)
    return result


def _platt_maturity(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(temperature_metric, 'legacy_or_unknown') AS metric,
            COALESCE(authority, 'unknown') AS authority,
            COALESCE(is_active, 0) AS is_active,
            CASE
                WHEN COALESCE(n_samples, 0) < 15 THEN 'lt15_no_platt'
                WHEN COALESCE(n_samples, 0) < 50 THEN 'n15_49_low'
                WHEN COALESCE(n_samples, 0) < 150 THEN 'n50_149_adequate'
                ELSE 'n150_plus_high'
            END AS maturity_bucket,
            COUNT(*) AS model_count,
            MIN(COALESCE(n_samples, 0)) AS min_n_samples,
            MAX(COALESCE(n_samples, 0)) AS max_n_samples
        FROM platt_models_v2
        GROUP BY metric, authority, is_active, maturity_bucket
        ORDER BY metric, authority, is_active DESC, maturity_bucket
        """
    ).fetchall()
    return _rows_to_dicts(rows)


def _quarantined_negative_a(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            model_key,
            temperature_metric,
            cluster,
            season,
            cycle,
            source_id,
            horizon_profile,
            data_version,
            n_samples,
            param_A,
            authority,
            is_active
        FROM platt_models_v2
        WHERE COALESCE(is_active, 0) = 1
          AND authority = 'QUARANTINED'
          AND param_A < 0
        ORDER BY temperature_metric, cluster, season, cycle, model_key
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return _rows_to_dicts(rows)


def _active_domain_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        WITH active AS (
            SELECT
                cluster,
                season,
                COALESCE(cycle, '') AS cycle,
                COALESCE(source_id, '') AS source_id,
                COALESCE(horizon_profile, '') AS horizon_profile,
                COALESCE(data_version, '') AS data_version,
                COALESCE(input_space, '') AS input_space,
                temperature_metric
            FROM platt_models_v2
            WHERE COALESCE(is_active, 0) = 1
              AND authority = 'VERIFIED'
        ),
        domains AS (
            SELECT
                cluster,
                season,
                cycle,
                source_id,
                horizon_profile,
                data_version,
                input_space,
                MAX(CASE WHEN temperature_metric = 'high' THEN 1 ELSE 0 END) AS has_high,
                MAX(CASE WHEN temperature_metric = 'low' THEN 1 ELSE 0 END) AS has_low
            FROM active
            GROUP BY cluster, season, cycle, source_id, horizon_profile, data_version, input_space
        )
        SELECT
            COUNT(*) AS total_active_domains,
            SUM(CASE WHEN has_high = 1 AND has_low = 1 THEN 1 ELSE 0 END) AS paired_high_low_domains,
            SUM(CASE WHEN has_high = 1 AND has_low = 0 THEN 1 ELSE 0 END) AS high_only_domains,
            SUM(CASE WHEN has_high = 0 AND has_low = 1 THEN 1 ELSE 0 END) AS low_only_domains
        FROM domains
        """
    ).fetchone()
    return dict(row) if row is not None else {}


def _persisted_low_window_evidence(conn: sqlite3.Connection) -> dict[str, Any]:
    columns = _table_columns(conn, "ensemble_snapshots_v2")
    required = {
        "forecast_window_attribution_status",
        "contributes_to_target_extrema",
        "forecast_window_block_reasons_json",
    }
    missing = sorted(required - columns)
    if missing:
        return {
            "schema_ready": False,
            "missing_fields": missing,
            "contract_proven_training_candidates": 0,
            "deterministic_reassignment_candidates": 0,
            "ambiguous_blocked": 0,
        }

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS low_rows,
            SUM(CASE
                WHEN forecast_window_attribution_status = 'FULLY_INSIDE_TARGET_LOCAL_DAY'
                 AND COALESCE(contributes_to_target_extrema, 0) = 1
                 AND COALESCE(forecast_window_block_reasons_json, '') = '[]'
                THEN 1 ELSE 0 END) AS contract_proven_training_candidates,
            SUM(CASE
                WHEN forecast_window_attribution_status IN (
                    'DETERMINISTICALLY_PREVIOUS_LOCAL_DAY',
                    'DETERMINISTICALLY_NEXT_LOCAL_DAY'
                )
                THEN 1 ELSE 0 END) AS deterministic_reassignment_candidates,
            SUM(CASE
                WHEN forecast_window_attribution_status = 'AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY'
                THEN 1 ELSE 0 END) AS ambiguous_blocked,
            SUM(CASE
                WHEN forecast_window_attribution_status IS NULL
                  OR forecast_window_attribution_status = ''
                  OR forecast_window_attribution_status = 'UNKNOWN'
                THEN 1 ELSE 0 END) AS missing_or_unknown_window_evidence
        FROM ensemble_snapshots_v2
        WHERE temperature_metric = 'low'
        """
    ).fetchone()
    result = dict(row) if row is not None else {}
    for key in (
        "low_rows",
        "contract_proven_training_candidates",
        "deterministic_reassignment_candidates",
        "ambiguous_blocked",
        "missing_or_unknown_window_evidence",
    ):
        result[key] = int(result.get(key) or 0)
    result["schema_ready"] = True
    result["missing_fields"] = []
    result["note"] = (
        "training candidates still require observation/bin pair rebuild and "
        "must remain shadow until promotion gates pass"
    )
    return result


def build_report(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    generated_at: str,
    city_limit: int = 20,
    quarantine_limit: int = 50,
) -> dict[str, Any]:
    _require_tables(conn)
    snapshot_rows = _snapshot_eligibility(conn)
    low_rows = [row for row in snapshot_rows if row["metric"] == "low"]
    low_total = sum(int(row["total_snapshots"]) for row in low_rows)
    low_baseline = sum(int(row["baseline_training_snapshots"] or 0) for row in low_rows)
    low_boundary = sum(int(row["boundary_rejected_snapshots"] or 0) for row in low_rows)

    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "db_path": db_path,
        "derived_context_only": True,
        "live_behavior_changed": False,
        "before_current_baseline": {
            "snapshot_eligibility": snapshot_rows,
            "low_city_boundary_loss_top": _low_city_boundary_loss(conn, city_limit),
            "platt_maturity": _platt_maturity(conn),
            "active_domain_coverage": _active_domain_coverage(conn),
            "quarantined_negative_a_active": _quarantined_negative_a(conn, quarantine_limit),
        },
        "after_contract_recovery_candidate": {
            "safe_without_persisted_window_metadata": {
                "additional_training_snapshots": 0,
                "reason": "contract-outcome/window evidence is not persisted in current baseline",
            },
            "upper_bound_if_all_boundary_rejections_became_contract_proven": {
                "low_total_snapshots": low_total,
                "low_baseline_training_snapshots": low_baseline,
                "low_boundary_rejected_snapshots": low_boundary,
                "low_upper_bound_training_snapshots": low_baseline + low_boundary,
                "low_baseline_training_rate": _pct(low_baseline, low_total),
                "low_upper_bound_training_rate": _pct(low_baseline + low_boundary, low_total),
                "warning": (
                    "upper bound only; ambiguous cross-midnight aggregate minima must remain blocked "
                    "unless contract-bin outcome identity is proven"
                ),
            },
            "persisted_low_window_evidence": _persisted_low_window_evidence(conn),
            "low_recovery_data_version_policy": _low_recovery_data_version_policy(),
            "contract_object_required_fields": list(CONTRACT_OBJECT_REQUIRED_FIELDS),
            "contract_evidence_schema_gap": _contract_evidence_schema_gap(conn),
            "no_regression_gates": list(NO_REGRESSION_GATES),
        },
    }


def run_report(db_path: Path, *, city_limit: int, quarantine_limit: int) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    with _connect_read_only(db_path) as conn:
        return build_report(
            conn,
            db_path=str(db_path),
            generated_at=generated_at,
            city_limit=city_limit,
            quarantine_limit=quarantine_limit,
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only LOW/HIGH alignment before-after diagnostic report.",
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--city-limit", type=int, default=20)
    parser.add_argument("--quarantine-limit", type=int, default=50)
    parser.add_argument("--stdout", action="store_true", help="Accepted for compatibility; stdout is always used.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = run_report(
        args.db_path,
        city_limit=args.city_limit,
        quarantine_limit=args.quarantine_limit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
