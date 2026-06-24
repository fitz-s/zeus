"""Current-market coverage plan for replacement forecast materialization."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfoNotFoundError

from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql
from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
from src.engine.time_context import has_city_local_day_started
from src.state.db import _connect


SOURCE_ID = "openmeteo_ecmwf_ifs9_bayes_fusion"


@dataclass(frozen=True)
class ReplacementForecastCurrentTargetPlanRow:
    city: str
    target_date: str
    temperature_metric: str
    market_bin_count: int
    posterior_count: int
    readiness_count: int
    openmeteo_manifest_count: int
    baseline_source_run_id: str | None = None
    openmeteo_source_run_id: str | None = None
    day0_observed_extreme_required: bool = False

    @property
    def covered(self) -> bool:
        return self.posterior_count > 0 and self.readiness_count > 0

    @property
    def can_seed(self) -> bool:
        # Live seeding needs the OM9 anchor plus already-captured fusion rows.
        # Removed model families are not completeness requirements here.
        return (
            not self.covered
            and not self.day0_observed_extreme_required
            and self.openmeteo_manifest_count > 0
        )

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
            "openmeteo_manifest_count": self.openmeteo_manifest_count,
            "baseline_source_run_id": self.baseline_source_run_id,
            "openmeteo_source_run_id": self.openmeteo_source_run_id,
            "day0_observed_extreme_required": self.day0_observed_extreme_required,
            "covered": self.covered,
            "can_seed": self.can_seed,
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
    missing_openmeteo_manifest_count: int
    day0_observed_extreme_required_count: int
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
            "missing_openmeteo_manifest_count": self.missing_openmeteo_manifest_count,
            "day0_observed_extreme_required_count": self.day0_observed_extreme_required_count,
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


def _json_object(text: object) -> dict[str, object]:
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _row_value(row: sqlite3.Row, key: str) -> object | None:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _openmeteo_source_run_id(metadata: Mapping[str, object]) -> str | None:
    value = metadata.get("source_run_id")
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _path_from_metadata_path(
    path_text: object,
    *,
    base_dir: Path,
) -> Path | None:
    if path_text is None or not str(path_text).strip():
        return None
    path = Path(str(path_text))
    if not path.is_absolute():
        path = base_dir / path
    return path


def _openmeteo_payload_covers_target_local_day(
    metadata: Mapping[str, object],
    *,
    artifact_path: str,
    city_timezone: str | None,
    target_date: str,
) -> bool:
    """Return whether an explicit Open-Meteo payload has target-local-day samples.

    ``raw_forecast_artifacts`` rows can point at a manifest whose metadata says a
    target date is in horizon while the on-disk payload is a clipped partial
    response. That false positive makes the downloader skip the fresh cycle and
    lets the materializer fail later with "insufficient Open-Meteo hourly
    samples inside target local day". Only explicit ``openmeteo_payload_json``
    payloads are checked here so old fixture/dummy artifacts keep their legacy
    existence-only semantics.
    """

    if not city_timezone:
        return True
    payload_path = _path_from_metadata_path(
        metadata.get("openmeteo_payload_json"),
        base_dir=Path(artifact_path).parent,
    )
    if payload_path is None:
        return True
    if not payload_path.exists():
        return False
    try:
        from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
            extract_openmeteo_ecmwf_ifs9_localday_anchor,
        )

        wanted = date.fromisoformat(str(target_date).strip())
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    try:
        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            payload,
            city_timezone=city_timezone,
            target_local_date=wanted,
            min_hourly_samples=1,
            require_full_localday=False,
        )
    except Exception:
        return False
    return True


def _openmeteo_manifest_coverage(
    conn: sqlite3.Connection,
    *,
    raw_artifact_columns: set[str],
    metadata_column: str | None,
    source_id: str,
    data_version: str,
    city: str,
    target_date: str,
    city_timezone: str | None = None,
    required_source_cycle_time: str | None = None,
) -> tuple[int, str | None]:
    if metadata_column is None:
        return 0, None
    optional_columns = [
        col
        for col in ("source_cycle_time", "source_available_at", "captured_at", "recorded_at")
        if col in raw_artifact_columns
    ]
    select_optional = "".join(f", {col}" for col in optional_columns)
    cycle_predicates: list[str] = []
    cycle_params: list[str] = []
    if required_source_cycle_time:
        if "source_cycle_time" in raw_artifact_columns:
            cycle_predicates.append("source_cycle_time = ?")
            cycle_params.append(required_source_cycle_time)
        cycle_predicates.append(f"json_extract({metadata_column}, '$.source_cycle_time') = ?")
        cycle_params.append(required_source_cycle_time)
    cycle_clause = ""
    if cycle_predicates:
        cycle_clause = f" AND ({' OR '.join(cycle_predicates)})"
    rows = conn.execute(
        f"""
        SELECT artifact_path, {metadata_column} AS metadata_json{select_optional}
        FROM raw_forecast_artifacts
        WHERE source_id = ?
          AND data_version = ?
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
          {cycle_clause}
        """,
        (
            source_id,
            data_version,
            city,
            city,
            target_date,
            target_date,
            *cycle_params,
        ),
    ).fetchall()
    candidates: list[tuple[tuple[str, str, str, str], str | None]] = []
    for manifest in rows:
        artifact_path = str(manifest["artifact_path"] or "")
        if not artifact_path or not os.path.exists(artifact_path):
            continue
        metadata = _json_object(manifest["metadata_json"])
        if not _openmeteo_payload_covers_target_local_day(
            metadata,
            artifact_path=artifact_path,
            city_timezone=city_timezone,
            target_date=target_date,
        ):
            continue
        source_run_id = _openmeteo_source_run_id(metadata)
        source_cycle_time = str(
            _row_value(manifest, "source_cycle_time")
            or metadata.get("source_cycle_time")
            or ""
        )
        source_available_at = str(
            _row_value(manifest, "source_available_at")
            or metadata.get("source_available_at")
            or metadata.get("requested_source_available_at")
            or ""
        )
        captured_at = str(
            _row_value(manifest, "captured_at")
            or metadata.get("captured_at")
            or _row_value(manifest, "recorded_at")
            or ""
        )
        candidates.append(
            (
                (source_cycle_time, source_available_at, captured_at, artifact_path),
                source_run_id,
            )
        )
    if not candidates:
        return 0, None
    latest = max(candidates, key=lambda item: item[0])
    return len(candidates), latest[1]


def _replacement_coverage_counts_for_dependencies(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    baseline_source_run_id: str | None,
    openmeteo_source_run_id: str | None,
    posterior_tradeable_grade_clause: str,
    readiness_status_clause: str,
) -> tuple[int, int]:
    if not baseline_source_run_id or not openmeteo_source_run_id:
        return 0, 0
    posterior_count = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM forecast_posteriors p
            WHERE p.source_id = ?
              AND p.training_allowed = 0
              AND p.runtime_layer = 'live'
              AND p.city = ?
              AND p.target_date = ?
              AND p.temperature_metric = ?
              {posterior_tradeable_grade_clause}
              AND json_extract(p.dependency_source_run_ids_json, '$.baseline_b0') = ?
              AND json_extract(p.dependency_source_run_ids_json, '$.openmeteo_ifs9_anchor') = ?
            """,
            (
                SOURCE_ID,
                city,
                target_date,
                temperature_metric,
                baseline_source_run_id,
                openmeteo_source_run_id,
            ),
        ).fetchone()[0]
    )
    readiness_count = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM readiness_state r
            WHERE r.strategy_key = ?
              AND json_extract(r.provenance_json, '$.city') = ?
              AND json_extract(r.provenance_json, '$.target_date') = ?
              AND json_extract(r.provenance_json, '$.temperature_metric') = ?
              {readiness_status_clause}
              AND EXISTS (
                  SELECT 1
                  FROM json_each(r.dependency_json, '$.dependencies')
                  WHERE json_extract(value, '$.role') = 'baseline_b0'
                    AND json_extract(value, '$.source_run_id') = ?
              )
              AND EXISTS (
                  SELECT 1
                  FROM json_each(r.dependency_json, '$.dependencies')
                  WHERE json_extract(value, '$.role') = 'openmeteo_ifs9_anchor'
                    AND json_extract(value, '$.source_run_id') = ?
              )
            """,
            (
                SOURCE_ID,
                city,
                target_date,
                temperature_metric,
                baseline_source_run_id,
                openmeteo_source_run_id,
            ),
        ).fetchone()[0]
    )
    return posterior_count, readiness_count


def _blocked_plan(reason_code: str) -> ReplacementForecastCurrentTargetPlan:
    return ReplacementForecastCurrentTargetPlan(
        status="BLOCKED",
        reason_codes=(reason_code,),
        target_count=0,
        covered_count=0,
        missing_coverage_count=0,
        can_seed_count=0,
        missing_openmeteo_manifest_count=0,
        day0_observed_extreme_required_count=0,
        rows=(),
    )


def _status_from_counts(
    *,
    target_count: int,
    missing_coverage_count: int,
    can_seed_count: int,
    missing_openmeteo_manifest_count: int,
    day0_observed_extreme_required_count: int,
) -> tuple[str, tuple[str, ...]]:
    if target_count <= 0:
        return "NO_CURRENT_TARGETS", ("REPLACEMENT_CURRENT_TARGET_PLAN_NO_CURRENT_TARGETS",)
    if missing_coverage_count <= 0:
        return "CURRENT_TARGETS_COVERED", ("REPLACEMENT_CURRENT_TARGET_PLAN_COVERED",)
    reasons: list[str] = ["REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_REPLACEMENT_COVERAGE"]
    if can_seed_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_HAS_SEEDABLE_TARGETS")
    if missing_openmeteo_manifest_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_OPENMETEO_MANIFESTS")
    if day0_observed_extreme_required_count:
        reasons.append("REPLACEMENT_CURRENT_TARGET_PLAN_DAY0_OBSERVED_EXTREME_REQUIRED")
    if (
        can_seed_count <= 0
        and missing_openmeteo_manifest_count <= 0
        and day0_observed_extreme_required_count >= missing_coverage_count
    ):
        return (
            "CURRENT_TARGETS_REQUIRE_DAY0_OBSERVED_EXTREME",
            ("REPLACEMENT_CURRENT_TARGET_PLAN_DAY0_OBSERVED_EXTREME_REQUIRED",),
        )
    return "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE", tuple(reasons)


def _city_timezone_by_name() -> dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "config" / "cities.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cities = payload.get("cities") if isinstance(payload, Mapping) else None
    if not isinstance(cities, list):
        return {}
    out: dict[str, str] = {}
    for row in cities:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip()
        timezone_name = str(row.get("timezone") or "").strip()
        if name and timezone_name:
            out[name] = timezone_name
    return out


def _city_timezone_by_name_from_source_run_coverage(conn: sqlite3.Connection) -> dict[str, str]:
    if "source_run_coverage" not in _table_names(conn):
        return {}
    if not {"city", "city_timezone"}.issubset(_columns(conn, "source_run_coverage")):
        return {}
    out: dict[str, str] = {}
    for row in conn.execute(
        """
        SELECT city, city_timezone, max(recorded_at) AS recorded_at
        FROM source_run_coverage
        WHERE city IS NOT NULL
          AND city != ''
          AND city_timezone IS NOT NULL
          AND city_timezone != ''
        GROUP BY city, city_timezone
        ORDER BY recorded_at DESC
        """
    ).fetchall():
        city = str(row["city"]).strip()
        timezone_name = str(row["city_timezone"]).strip()
        if city and timezone_name and city not in out:
            out[city] = timezone_name
    return out


def _day0_observed_extreme_required(
    *,
    city: str,
    target_date: str,
    timezone_by_city: Mapping[str, str],
    now_utc: datetime,
) -> bool:
    timezone_name = timezone_by_city.get(city)
    if not timezone_name:
        return False
    try:
        return has_city_local_day_started(target_date, timezone_name, now_utc)
    except (ValueError, ZoneInfoNotFoundError):
        return False


def build_replacement_forecast_current_target_plan(
    forecast_db: Path | str,
    *,
    limit: int | None = None,
    min_target_date: date | str | None = None,
    require_raw_artifacts: bool = True,
    now_utc: datetime | None = None,
    required_openmeteo_source_cycle_time: datetime | str | None = None,
) -> ReplacementForecastCurrentTargetPlan:
    """Return current market targets and the replacement artifacts needed for them."""

    db_path = Path(forecast_db)
    # Use now_utc as the reference clock when min_target_date is not explicit — avoids
    # wall-clock drift against fixtures or callers that pass a fixed now_utc.
    _ref_clock = (now_utc or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    minimum_target_date = (
        min_target_date.isoformat()
        if isinstance(min_target_date, date)
        else str(min_target_date or _ref_clock.date().isoformat())
    )
    required_openmeteo_cycle_iso: str | None = None
    if isinstance(required_openmeteo_source_cycle_time, datetime):
        required_openmeteo_cycle_iso = (
            required_openmeteo_source_cycle_time.astimezone(timezone.utc).isoformat()
        )
    elif required_openmeteo_source_cycle_time is not None:
        required_openmeteo_cycle_iso = str(required_openmeteo_source_cycle_time)
    if not db_path.exists():
        return ReplacementForecastCurrentTargetPlan(
            status="BLOCKED",
            reason_codes=("REPLACEMENT_CURRENT_TARGET_PLAN_FORECAST_DB_MISSING",),
            target_count=0,
            covered_count=0,
            missing_coverage_count=0,
            can_seed_count=0,
            missing_openmeteo_manifest_count=0,
            day0_observed_extreme_required_count=0,
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
        try:
            market_event_count = int(conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0])
        except sqlite3.Error:
            market_event_count = -1
        if market_event_count == 0:
            return ReplacementForecastCurrentTargetPlan(
                status="NO_CURRENT_TARGETS",
                reason_codes=("REPLACEMENT_CURRENT_TARGET_PLAN_NO_CURRENT_TARGETS",),
                target_count=0,
                covered_count=0,
                missing_coverage_count=0,
                can_seed_count=0,
                missing_openmeteo_manifest_count=0,
                day0_observed_extreme_required_count=0,
                rows=(),
            )
        if not {"city", "target_date", "temperature_metric", "token_id", "range_label"}.issubset(_columns(conn, "market_events")):
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_MARKET_EVENTS_SCHEMA_MISSING")
        posterior_columns = _columns(conn, "forecast_posteriors")
        if not {"city", "target_date", "temperature_metric", "source_id", "data_version"}.issubset(posterior_columns):
            return _blocked_plan("REPLACEMENT_CURRENT_TARGET_PLAN_POSTERIOR_SCHEMA_MISSING")
        readiness_columns = _columns(conn, "readiness_state")
        raw_artifact_columns: set[str] = set()
        metadata_column = None
        if "raw_forecast_artifacts" in tables:
            raw_artifact_columns = _columns(conn, "raw_forecast_artifacts")
            metadata_column = _raw_artifact_metadata_column(raw_artifact_columns)
            if require_raw_artifacts and (
                metadata_column is None
                or not {"source_id", "data_version", "artifact_path"}.issubset(raw_artifact_columns)
            ):
                raise ValueError("raw_forecast_artifacts schema lacks manifest metadata columns")
        source_run_targets = _supports_source_run_targets(conn)
        posterior_source_run_clause = ""
        readiness_source_run_clause = ""
        readiness_status_clause = ""
        # TRADEABLE-GRADE COVERAGE (2026-06-11, second site of the 2026-06-10 K-decision;
        # basis-predicate fix 2026-06-12): a covering posterior must be CERTIFIED-bootstrap
        # tradeable-grade. The mask-and-starve antibody guards against a capture-missing
        # materialization marking its scope covered at PLAN level and blocking its own fusion repair
        # (observed 2026-06-11: Atlanta/Austin/Beijing 00Z rows self-masked one tick after
        # materializing). The original proxy `p.q_lcb_json IS NOT NULL` broke once the soft-anchor
        # older non-certified paths began carrying q_lcb instead of NULL, so the
        # predicate now keys on the certified bootstrap basis (single authority:
        # cycle_policy). Schema-conditional like the queue clause.
        posterior_tradeable_grade_clause = tradeable_grade_coverage_sql(
            posterior_columns=posterior_columns, alias="p."
        )
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
        if "status" in readiness_columns:
            # Expired readiness must NOT count as coverage (else a city stays "covered"
            # forever after its first posterior and the downloader never re-fetches its
            # raw inputs once the 3h TTL lapses — the stale-after-first-cycle bug). Only
            # a row whose expires_at is still in the future counts as live coverage.
            readiness_status_clause = """
                          AND r.status = 'READY'
                          AND (r.expires_at IS NULL OR r.expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now'))
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
                          AND p.runtime_layer = 'live'
                          AND p.city = targets.city
                          AND p.target_date = targets.target_date
                          AND p.temperature_metric = targets.temperature_metric
                          {posterior_tradeable_grade_clause}
                          {posterior_source_run_clause}
                    ) AS posterior_count,
                    (
                        SELECT COUNT(*)
                        FROM readiness_state r
                        WHERE r.strategy_key = ?
                          AND json_extract(r.provenance_json, '$.city') = targets.city
                          AND json_extract(r.provenance_json, '$.target_date') = targets.target_date
                          AND json_extract(r.provenance_json, '$.temperature_metric') = targets.temperature_metric
                          {readiness_status_clause}
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
                      AND runtime_layer = 'live'
                      {posterior_tradeable_grade_clause.replace("p.q_lcb_json", "q_lcb_json")}
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
                      {readiness_status_clause}
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
        timezone_by_city = {
            **_city_timezone_by_name(),
            **_city_timezone_by_name_from_source_run_coverage(conn),
        }
        evaluation_now_utc = (now_utc or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
        for row in rows:
            metric = str(row["temperature_metric"])
            expected = expected_replacement_dependency_identity_by_role(metric)
            openmeteo_expected = expected["openmeteo_ifs9_anchor"]
            city = str(row["city"])
            target_date = str(row["target_date"])
            baseline_source_run_id = row["baseline_source_run_id"]
            day0_observed_extreme_required = _day0_observed_extreme_required(
                city=city,
                target_date=target_date,
                timezone_by_city=timezone_by_city,
                now_utc=evaluation_now_utc,
            )
            openmeteo_count = 0
            openmeteo_source_run_id = None
            if metadata_column is not None:
                openmeteo_count, openmeteo_source_run_id = _openmeteo_manifest_coverage(
                    conn,
                    raw_artifact_columns=raw_artifact_columns,
                    metadata_column=metadata_column,
                    source_id=openmeteo_expected.source_id,
                    data_version=openmeteo_expected.data_version,
                    city=city,
                    target_date=target_date,
                    city_timezone=timezone_by_city.get(city),
                    required_source_cycle_time=required_openmeteo_cycle_iso,
                )
            elif not require_raw_artifacts:
                openmeteo_count = 1
            posterior_count = int(row["posterior_count"])
            readiness_count = int(row["readiness_count"])
            if source_run_targets and openmeteo_source_run_id:
                posterior_count, readiness_count = _replacement_coverage_counts_for_dependencies(
                    conn,
                    city=city,
                    target_date=target_date,
                    temperature_metric=metric,
                    baseline_source_run_id=str(baseline_source_run_id or ""),
                    openmeteo_source_run_id=openmeteo_source_run_id,
                    posterior_tradeable_grade_clause=posterior_tradeable_grade_clause,
                    readiness_status_clause=readiness_status_clause,
                )
            elif source_run_targets and metadata_column is not None:
                posterior_count = 0
                readiness_count = 0
            out.append(
                ReplacementForecastCurrentTargetPlanRow(
                    city=city,
                    target_date=target_date,
                    temperature_metric=metric,
                    market_bin_count=int(row["market_bin_count"]),
                    posterior_count=posterior_count,
                    readiness_count=readiness_count,
                    openmeteo_manifest_count=openmeteo_count,
                    baseline_source_run_id=baseline_source_run_id,
                    openmeteo_source_run_id=openmeteo_source_run_id,
                    day0_observed_extreme_required=day0_observed_extreme_required,
                )
            )
    finally:
        conn.close()
    target_count = len(out)
    covered_count = sum(1 for row in out if row.covered)
    missing_coverage_count = target_count - covered_count
    can_seed_count = sum(1 for row in out if row.can_seed)
    missing_openmeteo_manifest_count = sum(1 for row in out if row.missing_openmeteo_manifest)
    day0_observed_extreme_required_count = sum(1 for row in out if row.day0_observed_extreme_required and not row.covered)
    status, reasons = _status_from_counts(
        target_count=target_count,
        missing_coverage_count=missing_coverage_count,
        can_seed_count=can_seed_count,
        missing_openmeteo_manifest_count=missing_openmeteo_manifest_count,
        day0_observed_extreme_required_count=day0_observed_extreme_required_count,
    )
    return ReplacementForecastCurrentTargetPlan(
        status=status,
        reason_codes=reasons,
        target_count=target_count,
        covered_count=covered_count,
        missing_coverage_count=missing_coverage_count,
        can_seed_count=can_seed_count,
        missing_openmeteo_manifest_count=missing_openmeteo_manifest_count,
        day0_observed_extreme_required_count=day0_observed_extreme_required_count,
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
        "openmeteo_download_targets": [
            row.as_dict() for row in missing if row.missing_openmeteo_manifest
        ],
        "seedable_targets": [
            row.as_dict() for row in missing if row.can_seed
        ],
    }
