"""Current-market coverage plan for replacement forecast materialization."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping

from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
from src.state.db import _connect


SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"


@dataclass(frozen=True)
class ReplacementForecastCurrentTargetPlanRow:
    city: str
    target_date: str
    temperature_metric: str
    market_bin_count: int
    posterior_count: int
    readiness_count: int
    aifs_manifest_count: int
    openmeteo_manifest_count: int
    baseline_source_run_id: str | None = None

    @property
    def covered(self) -> bool:
        return self.posterior_count > 0 and self.readiness_count > 0

    @property
    def can_seed(self) -> bool:
        return not self.covered and self.aifs_manifest_count > 0 and self.openmeteo_manifest_count > 0

    @property
    def missing_aifs_manifest(self) -> bool:
        return not self.covered and self.aifs_manifest_count <= 0

    @property
    def missing_openmeteo_manifest(self) -> bool:
        return not self.covered and self.openmeteo_manifest_count <= 0

    def as_dict(self) -> dict[str, object]:
        return {
            "city": self.city,
            "target_date": self.target_date,
            "temperature_metric": self.temperature_metric,
            "market_bin_count": self.market_bin_count,
            "posterior_count": self.posterior_count,
            "readiness_count": self.readiness_count,
            "aifs_manifest_count": self.aifs_manifest_count,
            "openmeteo_manifest_count": self.openmeteo_manifest_count,
            "baseline_source_run_id": self.baseline_source_run_id,
            "covered": self.covered,
            "can_seed": self.can_seed,
            "missing_aifs_manifest": self.missing_aifs_manifest,
            "missing_openmeteo_manifest": self.missing_openmeteo_manifest,
        }


@dataclass(frozen=True)
class ReplacementForecastCurrentTargetPlan:
    status: str
    reason_codes: tuple[str, ...]
    target_count: int
    covered_count: int
    missing_coverage_count: int
    can_seed_count: int
    missing_aifs_manifest_count: int
    missing_openmeteo_manifest_count: int
    rows: tuple[ReplacementForecastCurrentTargetPlanRow, ...]

    @property
    def ready(self) -> bool:
        return self.status == "CURRENT_TARGETS_COVERED"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "target_count": self.target_count,
            "covered_count": self.covered_count,
            "missing_coverage_count": self.missing_coverage_count,
            "can_seed_count": self.can_seed_count,
            "missing_aifs_manifest_count": self.missing_aifs_manifest_count,
            "missing_openmeteo_manifest_count": self.missing_openmeteo_manifest_count,
            "rows": [row.as_dict() for row in self.rows],
            "ready": self.ready,
        }


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    }


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _raw_artifact_metadata_column(columns: set[str]) -> str | None:
    if "product_metadata_json" in columns:
        return "product_metadata_json"
    if "artifact_metadata_json" in columns:
        return "artifact_metadata_json"
    return None


def _supports_source_run_targets(conn: sqlite3.Connection) -> bool:
    tables = _table_names(conn)
    if "source_run_coverage" not in tables:
        return False
    required = {
        "source_run_id",
        "source_id",
        "city",
        "target_local_date",
        "temperature_metric",
        "data_version",
        "computed_at",
    }
    return required.issubset(_columns(conn, "source_run_coverage"))


def _blocked_plan(reason_code: str) -> ReplacementForecastCurrentTargetPlan:
    return ReplacementForecastCurrentTargetPlan(
        status="BLOCKED",
        reason_codes=(reason_code,),
        target_count=0,
        covered_count=0,
        missing_coverage_count=0,
        can_seed_count=0,
        missing_aifs_manifest_count=0,
        missing_openmeteo_manifest_count=0,
        rows=(),
    )


def _status_from_counts(
    *,
    target_count: int,
    missing_coverage_count: int,
    can_seed_count: int,
    missing_aifs_manifest_count: int,
    missing_openmeteo_manifest_count: int,
) -> tuple[str, tuple[str, ...]]:
    if target_count <= 0:
        return "NO_CURRENT_TARGETS", ("REPLACEMENT_CURRENT_TARGET_PLAN_NO_CURRENT_TARGETS",)
    if missing_coverage_count <= 0:
        return "CURRENT_TARGETS_COVERED", ("REPLACEMENT_CURRENT_TARGET_PLAN_COVERED",)
    reasons: list[str] = ["REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_REPLACEMENT_COVERAGE"]
    if can_seed_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_HAS_SEEDABLE_TARGETS")
    if missing_aifs_manifest_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_AIFS_MANIFESTS")
    if missing_openmeteo_manifest_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_OPENMETEO_MANIFESTS")
    return "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE", tuple(reasons)


def build_replacement_forecast_current_target_plan(
    forecast_db: Path | str,
    *,
    limit: int | None = None,
    min_target_date: date | str | None = None,
    require_raw_artifacts: bool = True,
) -> ReplacementForecastCurrentTargetPlan:
    """Return current market targets and the replacement artifacts needed for them."""

    db_path = Path(forecast_db)
    minimum_target_date = (
        min_target_date.isoformat()
        if isinstance(min_target_date, date)
        else str(min_target_date or datetime.now(tz=timezone.utc).date().isoformat())
    )
    if not db_path.exists():
        return ReplacementForecastCurrentTargetPlan(
            status="BLOCKED",
            reason_codes=("REPLACEMENT_CURRENT_TARGET_PLAN_FORECAST_DB_MISSING",),
            target_count=0,
            covered_count=0,
            missing_coverage_count=0,
            can_seed_count=0,
            missing_aifs_manifest_count=0,
            missing_openmeteo_manifest_count=0,
            rows=(),
        )
    conn = _connect(db_path, write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = _table_names(conn)
        required = {"market_events", "forecast_posteriors", "readiness_state"}
        if require_raw_artifacts:
            required.add("raw_forecast_artifacts")
        if not required.issubset(tables):
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_REQUIRED_TABLE_MISSING")
        if not {"city", "target_date", "temperature_metric", "token_id", "range_label"}.issubset(_columns(conn, "market_events")):
            raise ValueError("market_events schema lacks current target columns")
        posterior_columns = _columns(conn, "forecast_posteriors")
        if not {"city", "target_date", "temperature_metric", "source_id", "data_version"}.issubset(posterior_columns):
            raise ValueError("forecast_posteriors schema lacks replacement coverage columns")
        readiness_columns = _columns(conn, "readiness_state")
        metadata_column = None
        if require_raw_artifacts:
            raw_artifact_columns = _columns(conn, "raw_forecast_artifacts")
            metadata_column = _raw_artifact_metadata_column(raw_artifact_columns)
            if metadata_column is None or not {"source_id", "data_version", "artifact_path"}.issubset(raw_artifact_columns):
                raise ValueError("raw_forecast_artifacts schema lacks manifest metadata columns")
        source_run_targets = _supports_source_run_targets(conn)
        posterior_source_run_clause = ""
        readiness_source_run_clause = ""
        if source_run_targets and "dependency_source_run_ids_json" not in posterior_columns:
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_SOURCE_RUN_DEPENDENCY_SCHEMA_MISSING")
        if source_run_targets and "dependency_json" not in readiness_columns:
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_SOURCE_RUN_DEPENDENCY_SCHEMA_MISSING")
        if source_run_targets:
            posterior_source_run_clause = """
                  AND json_extract(p.dependency_source_run_ids_json, '$.baseline_b0') = targets.baseline_source_run_id
            """
            readiness_source_run_clause = """
                  AND EXISTS (
                      SELECT 1
                      FROM json_each(r.dependency_json, '$.dependencies')
                      WHERE json_extract(value, '$.role') = 'baseline_b0'
                        AND json_extract(value, '$.source_run_id') = targets.baseline_source_run_id
                  )
            """
        sql_limit = "" if limit is None else f" LIMIT {int(limit)}"
        if source_run_targets:
            expected_high = expected_replacement_dependency_identity_by_role("high")["baseline_b0"]
            expected_low = expected_replacement_dependency_identity_by_role("low")["baseline_b0"]
            rows = conn.execute(
                f"""
                WITH ranked_coverage AS (
                    SELECT
                        c.city,
                        c.target_local_date AS target_date,
                        c.temperature_metric,
                        c.source_run_id AS baseline_source_run_id,
                        c.computed_at,
                        c.recorded_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY c.city, c.target_local_date, c.temperature_metric
                            ORDER BY
                                CASE WHEN c.completeness_status = 'COMPLETE' THEN 0 ELSE 1 END,
                                CASE WHEN c.readiness_status = 'LIVE_ELIGIBLE' THEN 0 ELSE 1 END,
                                c.computed_at DESC,
                                c.recorded_at DESC
                        ) AS rn
                    FROM source_run_coverage c
                    WHERE c.source_id = ?
                      AND c.target_local_date >= ?
                      AND (
                          (c.temperature_metric = 'high' AND c.data_version = ?)
                          OR (c.temperature_metric = 'low' AND c.data_version = ?)
                      )
                      AND EXISTS (
                          SELECT 1
                          FROM market_events m
                          WHERE m.city = c.city
                            AND m.target_date = c.target_local_date
                            AND m.temperature_metric = c.temperature_metric
                            AND m.token_id IS NOT NULL
                            AND m.token_id != ''
                            AND m.range_label IS NOT NULL
                            AND m.range_label != ''
                      )
                ),
                targets AS (
                    SELECT
                        rc.city,
                        rc.target_date,
                        rc.temperature_metric,
                        rc.baseline_source_run_id,
                        (
                            SELECT COUNT(*)
                            FROM market_events m
                            WHERE m.city = rc.city
                              AND m.target_date = rc.target_date
                              AND m.temperature_metric = rc.temperature_metric
                              AND m.token_id IS NOT NULL
                              AND m.token_id != ''
                              AND m.range_label IS NOT NULL
                              AND m.range_label != ''
                        ) AS market_bin_count
                    FROM ranked_coverage rc
                    WHERE rc.rn = 1
                )
                SELECT
                    targets.city,
                    targets.target_date,
                    targets.temperature_metric,
                    targets.baseline_source_run_id,
                    targets.market_bin_count,
                    (
                        SELECT COUNT(*)
                        FROM forecast_posteriors p
                        WHERE p.source_id = ?
                          AND p.training_allowed = 0
                          AND p.trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')
                          AND p.city = targets.city
                          AND p.target_date = targets.target_date
                          AND p.temperature_metric = targets.temperature_metric
                          {posterior_source_run_clause}
                    ) AS posterior_count,
                    (
                        SELECT COUNT(*)
                        FROM readiness_state r
                        WHERE r.strategy_key = ?
                          AND json_extract(r.provenance_json, '$.city') = targets.city
                          AND json_extract(r.provenance_json, '$.target_date') = targets.target_date
                          AND json_extract(r.provenance_json, '$.temperature_metric') = targets.temperature_metric
                          {readiness_source_run_clause}
                    ) AS readiness_count
                FROM targets
                ORDER BY targets.target_date DESC, targets.city, targets.temperature_metric
                {sql_limit}
                """,
                (
                    expected_high.source_id,
                    minimum_target_date,
                    expected_high.data_version,
                    expected_low.data_version,
                    SOURCE_ID,
                    SOURCE_ID,
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                WITH targets AS (
                    SELECT city, target_date, temperature_metric, COUNT(*) AS market_bin_count
                    FROM market_events
                    WHERE token_id IS NOT NULL
                      AND token_id != ''
                      AND range_label IS NOT NULL
                      AND range_label != ''
                      AND target_date >= ?
                    GROUP BY city, target_date, temperature_metric
                ),
                posteriors AS (
                    SELECT city, target_date, temperature_metric, COUNT(*) AS posterior_count
                    FROM forecast_posteriors
                    WHERE source_id = ?
                      AND training_allowed = 0
                      AND trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')
                    GROUP BY city, target_date, temperature_metric
                ),
                readiness AS (
                    SELECT
                        json_extract(provenance_json, '$.city') AS city,
                        json_extract(provenance_json, '$.target_date') AS target_date,
                        json_extract(provenance_json, '$.temperature_metric') AS temperature_metric,
                        COUNT(*) AS readiness_count
                    FROM readiness_state
                    WHERE strategy_key = ?
                    GROUP BY 1, 2, 3
                )
                SELECT
                    targets.city,
                    targets.target_date,
                    targets.temperature_metric,
                    NULL AS baseline_source_run_id,
                    targets.market_bin_count,
                    COALESCE(posteriors.posterior_count, 0) AS posterior_count,
                    COALESCE(readiness.readiness_count, 0) AS readiness_count
                FROM targets
                LEFT JOIN posteriors USING (city, target_date, temperature_metric)
                LEFT JOIN readiness USING (city, target_date, temperature_metric)
                ORDER BY targets.target_date DESC, targets.city, targets.temperature_metric
                {sql_limit}
                """,
                (minimum_target_date, SOURCE_ID, SOURCE_ID),
            ).fetchall()
        out: list[ReplacementForecastCurrentTargetPlanRow] = []
        for row in rows:
            metric = str(row["temperature_metric"])
            expected = expected_replacement_dependency_identity_by_role(metric)
            aifs_expected = expected["aifs_sampled_2t"]
            openmeteo_expected = expected["openmeteo_ifs9_anchor"]
            city = str(row["city"])
            target_date = str(row["target_date"])
            aifs_count = 0
            openmeteo_count = 0
            if require_raw_artifacts and metadata_column is not None:
                manifest_counts = conn.execute(
                    f"""
                    SELECT source_id, data_version, COUNT(*) AS count
                    FROM raw_forecast_artifacts
                    WHERE (
                        source_id = ? AND data_version = ?
                        OR source_id = ? AND data_version = ?
                    )
                      AND artifact_path IS NOT NULL
                      AND artifact_path != ''
                      AND (
                        json_extract({metadata_column}, '$.city') = ?
                        OR EXISTS (
                            SELECT 1
                            FROM json_each({metadata_column}, '$.cities')
                            WHERE value = ?
                        )
                      )
                      AND (
                        json_extract({metadata_column}, '$.target_date') = ?
                        OR EXISTS (
                            SELECT 1
                            FROM json_each({metadata_column}, '$.target_dates')
                            WHERE value = ?
                        )
                      )
                    GROUP BY source_id, data_version
                    """,
                    (
                        aifs_expected.source_id,
                        aifs_expected.data_version,
                        openmeteo_expected.source_id,
                        openmeteo_expected.data_version,
                        city,
                        city,
                        target_date,
                        target_date,
                    ),
                ).fetchall()
                for manifest in manifest_counts:
                    source_id = str(manifest["source_id"])
                    data_version = str(manifest["data_version"])
                    if source_id == aifs_expected.source_id and data_version == aifs_expected.data_version:
                        aifs_count += int(manifest["count"])
                    if source_id == openmeteo_expected.source_id and data_version == openmeteo_expected.data_version:
                        openmeteo_count += int(manifest["count"])
            out.append(
                ReplacementForecastCurrentTargetPlanRow(
                    city=city,
                    target_date=target_date,
                    temperature_metric=metric,
                    market_bin_count=int(row["market_bin_count"]),
                    posterior_count=int(row["posterior_count"]),
                    readiness_count=int(row["readiness_count"]),
                    aifs_manifest_count=aifs_count,
                    openmeteo_manifest_count=openmeteo_count,
                    baseline_source_run_id=row["baseline_source_run_id"],
                )
            )
    finally:
        conn.close()
    target_count = len(out)
    covered_count = sum(1 for row in out if row.covered)
    missing_coverage_count = target_count - covered_count
    can_seed_count = sum(1 for row in out if row.can_seed)
    missing_aifs_manifest_count = sum(1 for row in out if row.missing_aifs_manifest)
    missing_openmeteo_manifest_count = sum(1 for row in out if row.missing_openmeteo_manifest)
    status, reasons = _status_from_counts(
        target_count=target_count,
        missing_coverage_count=missing_coverage_count,
        can_seed_count=can_seed_count,
        missing_aifs_manifest_count=missing_aifs_manifest_count,
        missing_openmeteo_manifest_count=missing_openmeteo_manifest_count,
    )
    return ReplacementForecastCurrentTargetPlan(
        status=status,
        reason_codes=reasons,
        target_count=target_count,
        covered_count=covered_count,
        missing_coverage_count=missing_coverage_count,
        can_seed_count=can_seed_count,
        missing_aifs_manifest_count=missing_aifs_manifest_count,
        missing_openmeteo_manifest_count=missing_openmeteo_manifest_count,
        rows=tuple(out),
    )


def replacement_forecast_download_plan_from_current_targets(
    plan: ReplacementForecastCurrentTargetPlan,
) -> dict[str, object]:
    """Return a compact actionable download/materialization plan from coverage rows."""

    missing = [row for row in plan.rows if not row.covered]
    return {
        "status": plan.status,
        "reason_codes": list(plan.reason_codes),
        "aifs_download_targets": [
            row.as_dict() for row in missing if row.missing_aifs_manifest
        ],
        "openmeteo_download_targets": [
            row.as_dict() for row in missing if row.missing_openmeteo_manifest
        ],
        "seedable_targets": [
            row.as_dict() for row in missing if row.can_seed
        ],
    }
