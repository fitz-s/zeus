"""Discover replacement forecast materialization seeds from DB and raw manifests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, read_manifest
from src.data.replacement_forecast_current_target_plan import build_replacement_forecast_current_target_plan
from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql
from src.data.replacement_forecast_materialization_seed_builder import (
    build_replacement_forecast_materialization_seed,
    latest_baseline_coverage_for_replacement_seed,
    market_bins_for_replacement_seed,
    write_seed,
)
from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
from src.state.db import _connect


UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastSeedDiscoveryReport:
    status: str
    reason_codes: tuple[str, ...]
    discovered_count: int
    skipped_count: int
    failed_count: int
    written_seed_files: tuple[str, ...] = ()
    failed_targets: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {"NO_ELIGIBLE_TARGETS", "DISCOVERED"}

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "discovered_count": self.discovered_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "written_seed_files": list(self.written_seed_files),
            "failed_targets": list(self.failed_targets),
            "ok": self.ok,
        }


def _reject_alias(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if _FORBIDDEN_TRANSCRIPT_ALIAS in text.lower():
        raise ValueError(f"{field_name} must use full replacement identity")
    return text


def _dt(value: datetime | str | None, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _manifest_path_value(manifest: RawForecastArtifactManifest, key: str) -> str | None:
    value = manifest.product_metadata.get(key)
    if value is None or not str(value).strip():
        return None
    return str(value)


def _resolve_path(path_text: str, *, base_dir: Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else base_dir / path


def _manifest_base_dir(manifest: RawForecastArtifactManifest, *, fallback: Path) -> Path:
    manifest_json = _manifest_path_value(manifest, "manifest_json")
    if not manifest_json:
        return fallback
    return Path(manifest_json).parent


def _load_manifests(raw_manifest_dir: Path, *, computed_at: datetime) -> tuple[RawForecastArtifactManifest, ...]:
    if not raw_manifest_dir.exists():
        return ()
    manifests: list[RawForecastArtifactManifest] = []
    for path in sorted(raw_manifest_dir.rglob("*.manifest.json")):
        manifest = read_manifest(path)
        manifest = RawForecastArtifactManifest(
            **{
                **manifest.to_dict(),
                "product_metadata": {
                    **dict(manifest.product_metadata),
                    "manifest_json": str(path),
                },
            }
        )
        if manifest.source_available_at <= computed_at:
            manifests.append(manifest)
    return tuple(manifests)


def _latest_manifest(
    manifests: tuple[RawForecastArtifactManifest, ...],
    *,
    source_id: str,
    data_version: str,
    city: str,
    target_date: str,
) -> RawForecastArtifactManifest | None:
    candidates = [
        manifest
        for manifest in manifests
        if manifest.source_id == source_id and manifest.data_version == data_version
        and _manifest_allows_city(manifest, city=city)
        and _manifest_allows_target_date(manifest, target_date=target_date)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda manifest: (manifest.source_cycle_time, manifest.source_available_at, manifest.captured_at))


def _manifest_allows_city(manifest: RawForecastArtifactManifest, *, city: str) -> bool:
    metadata = manifest.product_metadata
    explicit_city = metadata.get("city")
    if explicit_city is not None and str(explicit_city).strip():
        return str(explicit_city).strip() == city
    cities = metadata.get("cities")
    if isinstance(cities, list) and cities:
        return city in {str(item).strip() for item in cities}
    return False


def _manifest_allows_target_date(manifest: RawForecastArtifactManifest, *, target_date: str) -> bool:
    metadata = manifest.product_metadata
    explicit = metadata.get("target_date")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip() == target_date
    dates = metadata.get("target_dates")
    if isinstance(dates, list) and dates:
        return target_date in {str(item).strip() for item in dates}
    return False


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    }


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _source_run_coverage_schema_ready(conn: sqlite3.Connection) -> bool:
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


def _coverage_skip_schema_ready(conn: sqlite3.Connection, tables: set[str]) -> bool:
    if not {"forecast_posteriors", "readiness_state"}.issubset(tables):
        return False
    posterior_columns = _columns(conn, "forecast_posteriors")
    readiness_columns = _columns(conn, "readiness_state")
    return (
        "dependency_source_run_ids_json" in posterior_columns
        and "dependency_json" in readiness_columns
    )


def _candidate_targets(
    conn: sqlite3.Connection,
    *,
    limit: int,
    min_target_date: str,
) -> tuple[Mapping[str, object], ...]:
    tables = _table_names(conn)
    skip_covered_sql = ""
    if _coverage_skip_schema_ready(conn, tables):
        # TRADEABLE-GRADE COVERAGE (2026-06-11, third site of the 2026-06-10 K-decision;
        # basis-predicate fix 2026-06-12): only a CERTIFIED-bootstrap-bounded posterior counts as
        # coverage. The old proxy `p.q_lcb_json IS NOT NULL` broke once the soft-anchor path began
        # carrying a promoted Wilson q_lcb (basis="wilson_aifs_member_votes") instead of NULL — a
        # CAPTURE_MISSING row would then mask its own fusion repair (the mask-and-starve category).
        # Now keyed on the certified bootstrap basis. Single authority: cycle_policy. Same clause as
        # the queue antibody and the plan builder.
        _tradeable = tradeable_grade_coverage_sql(
            posterior_columns=_columns(conn, "forecast_posteriors"), alias="p."
        )
        skip_covered_sql = f"""
          AND (
              NOT EXISTS (
                  SELECT 1
                  FROM forecast_posteriors p
                  WHERE p.source_id = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
                    AND p.city = c.city
                    AND p.target_date = c.target_local_date
                    AND p.temperature_metric = c.temperature_metric
                    AND p.training_allowed = 0
                    AND p.trade_authority_status = 'LIVE_AUTHORITY'
                    {_tradeable}
                    AND json_extract(p.dependency_source_run_ids_json, '$.baseline_b0') = c.source_run_id
              )
              OR NOT EXISTS (
                  SELECT 1
                  FROM readiness_state r
                  WHERE r.strategy_key = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
                    AND json_extract(r.provenance_json, '$.city') = c.city
                    AND json_extract(r.provenance_json, '$.target_date') = c.target_local_date
                    AND json_extract(r.provenance_json, '$.temperature_metric') = c.temperature_metric
                    -- An EXPIRED readiness row must NOT count as coverage, else a city is
                    -- "covered" forever after its first posterior and never re-seeds once
                    -- its 3h TTL lapses (the stale-after-first-cycle bug). Only a row whose
                    -- expires_at is still in the future counts as live coverage.
                    AND (r.expires_at IS NULL OR r.expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now'))
                    AND EXISTS (
                        SELECT 1
                        FROM json_each(r.dependency_json, '$.dependencies')
                        WHERE json_extract(value, '$.role') = 'baseline_b0'
                          AND json_extract(value, '$.source_run_id') = c.source_run_id
                    )
              )
          )
        """
    rows = conn.execute(
        f"""
        SELECT c.city, c.target_local_date AS target_date, c.temperature_metric, max(c.computed_at) AS computed_at
        FROM source_run_coverage c
        WHERE c.source_id = 'ecmwf_open_data'
          AND c.target_local_date >= ?
          AND EXISTS (
              SELECT 1
              FROM market_events m
              WHERE m.city = c.city
                AND m.target_date = c.target_local_date
                AND m.temperature_metric = c.temperature_metric
                AND m.token_id IS NOT NULL
                AND m.range_label IS NOT NULL
          )
          {skip_covered_sql}
        GROUP BY c.city, c.target_local_date, c.temperature_metric
        ORDER BY c.target_local_date DESC, c.city, c.temperature_metric
        LIMIT ?
        """,
        (min_target_date, int(limit)),
    ).fetchall()
    return tuple(dict(row) for row in rows)


def _seed_name(target: Mapping[str, object], *, computed_at: datetime) -> str:
    city = _reject_alias(str(target["city"]), field_name="city").replace("/", "_").replace(" ", "_")
    target_date = _reject_alias(str(target["target_date"]), field_name="target_date")
    metric = _reject_alias(str(target["temperature_metric"]), field_name="temperature_metric")
    stamp = computed_at.strftime("%Y%m%dT%H%M%SZ")
    return f"{city}.{target_date}.{metric}.{stamp}.json"


def discover_replacement_forecast_materialization_seeds(
    *,
    forecast_db: Path | str,
    raw_manifest_dir: Path | str,
    seed_dir: Path | str,
    computed_at: datetime | str | None = None,
    limit: int = 10,
) -> ReplacementForecastSeedDiscoveryReport:
    """Write seed JSON for DB targets that have all shadow raw inputs available."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    computed = _dt(computed_at or datetime.now(tz=UTC), field_name="computed_at")
    raw_dir = Path(raw_manifest_dir)
    seed_path = Path(seed_dir)
    manifests = _load_manifests(raw_dir, computed_at=computed)
    if not manifests:
        return ReplacementForecastSeedDiscoveryReport(
            status="NO_ELIGIBLE_TARGETS",
            reason_codes=("REPLACEMENT_SEED_DISCOVERY_RAW_MANIFESTS_MISSING",),
            discovered_count=0,
            skipped_count=0,
            failed_count=0,
        )

    conn = _connect(Path(forecast_db), write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        if not _source_run_coverage_schema_ready(conn):
            return ReplacementForecastSeedDiscoveryReport(
                status="BLOCKED",
                reason_codes=("REPLACEMENT_SEED_DISCOVERY_SOURCE_RUN_COVERAGE_SCHEMA_MISSING",),
                discovered_count=0,
                skipped_count=0,
                failed_count=0,
            )
        target_plan = build_replacement_forecast_current_target_plan(
            forecast_db,
            min_target_date=computed.date().isoformat(),
            require_raw_artifacts=False,
            now_utc=computed,
        )
        if target_plan.status == "BLOCKED":
            return ReplacementForecastSeedDiscoveryReport(
                status="BLOCKED",
                reason_codes=tuple(
                    f"REPLACEMENT_SEED_DISCOVERY_CURRENT_TARGET_PLAN_{reason}"
                    for reason in target_plan.reason_codes
                ),
                discovered_count=0,
                skipped_count=0,
                failed_count=0,
            )
        targets = tuple(
            {
                "city": row.city,
                "target_date": row.target_date,
                "temperature_metric": row.temperature_metric,
                "baseline_source_run_id": row.baseline_source_run_id,
            }
            # SEED-BUDGET STARVATION KILL (2026-06-11): two coupled defects froze the
            # tradeable scopes behind permanently-failing far-date targets.
            #   1. Far-date-first: the plan's order put day-2 shadow scopes (06-13) ahead
            #      of the tradeable day0/day1 scopes. Nearest target date = the money —
            #      sort target_date ASC.
            #   2. Head-of-line budget burn: [:limit] sliced BEFORE the per-target
            #      manifest check, so the SAME ten manifest-missing targets (cities the
            #      rung-3 bucket whitelist cannot serve at the fresh cycle) consumed the
            #      WHOLE per-tick budget every tick and seedable scopes behind them were
            #      never reached (observed 2026-06-11: every 5-min tick = the same 10
            #      06-13 failures, zero seeds written, fusion-grade 06-11/06-12 starved).
            #      The budget now counts only WRITTEN seeds (enforced in the loop below);
            #      manifest-missing targets are recorded as failures for observability
            #      but consume no budget.
            for row in sorted(
                (row for row in target_plan.rows if row.can_seed),
                key=lambda row: (
                    str(row.target_date),
                    str(row.city),
                    str(row.temperature_metric),
                ),
            )
        )
        if not targets:
            return ReplacementForecastSeedDiscoveryReport(
                status="NO_ELIGIBLE_TARGETS",
                reason_codes=("REPLACEMENT_SEED_DISCOVERY_DB_TARGETS_MISSING",),
                discovered_count=0,
                skipped_count=0,
                failed_count=0,
            )
        written: list[str] = []
        failed: list[str] = []
        reasons: list[str] = []
        for target in targets:
            if len(written) >= max(1, int(limit)):
                break
            city = str(target["city"])
            target_date = str(target["target_date"])
            metric = str(target["temperature_metric"])
            target_key = f"{city}|{target_date}|{metric}"
            expected = expected_replacement_dependency_identity_by_role(metric)
            aifs = _latest_manifest(
                manifests,
                source_id=expected["aifs_sampled_2t"].source_id,
                data_version=expected["aifs_sampled_2t"].data_version,
                city=city,
                target_date=target_date,
            )
            openmeteo = _latest_manifest(
                manifests,
                source_id=expected["openmeteo_ifs9_anchor"].source_id,
                data_version=expected["openmeteo_ifs9_anchor"].data_version,
                city=city,
                target_date=target_date,
            )
            if aifs is None or openmeteo is None:
                failed.append(target_key)
                reasons.append("REPLACEMENT_SEED_DISCOVERY_REQUIRED_MANIFEST_MISSING")
                continue
            aifs_samples = _manifest_path_value(aifs, "aifs_samples_json") or _manifest_path_value(aifs, "sample_points_json")
            aifs_grib = None if aifs_samples else aifs.artifact_path
            openmeteo_payload = _manifest_path_value(openmeteo, "openmeteo_payload_json") or openmeteo.artifact_path
            precision_metadata = _manifest_path_value(openmeteo, "precision_metadata_json")
            if not (aifs_samples or aifs_grib) or not openmeteo_payload or not precision_metadata:
                failed.append(target_key)
                reasons.append("REPLACEMENT_SEED_DISCOVERY_MANIFEST_METADATA_INCOMPLETE")
                continue
            coverage = latest_baseline_coverage_for_replacement_seed(
                conn,
                city=city,
                target_date=target_date,
                temperature_metric=metric,
            )
            bins = market_bins_for_replacement_seed(
                conn,
                city=city,
                target_date=target_date,
                temperature_metric=metric,
            )
            if coverage is None or not bins:
                failed.append(target_key)
                reasons.append("REPLACEMENT_SEED_DISCOVERY_DB_CONTEXT_MISSING")
                continue
            aifs_base_dir = _manifest_base_dir(aifs, fallback=raw_dir)
            openmeteo_base_dir = _manifest_base_dir(openmeteo, fallback=raw_dir)
            seed_result = build_replacement_forecast_materialization_seed(
                city=city,
                target_date=target_date,
                temperature_metric=metric,
                market_bins=bins,
                baseline_coverage=coverage,
                aifs_manifest=aifs,
                openmeteo_manifest=openmeteo,
                openmeteo_payload_json=_resolve_path(openmeteo_payload, base_dir=openmeteo_base_dir),
                precision_metadata_json=_resolve_path(precision_metadata, base_dir=openmeteo_base_dir),
                computed_at=computed,
                base_dir=seed_path,
                aifs_samples_json=None if aifs_samples is None else _resolve_path(aifs_samples, base_dir=aifs_base_dir),
                aifs_grib_path=None if aifs_grib is None else _resolve_path(aifs_grib, base_dir=aifs_base_dir),
            )
            if not seed_result.ok or seed_result.seed is None:
                failed.append(target_key)
                reasons.extend(seed_result.reason_codes)
                continue
            seed_file = seed_path / _seed_name(target, computed_at=computed)
            write_seed(seed_file, seed_result.seed)
            written.append(str(seed_file))
    finally:
        conn.close()

    if written:
        reasons.append("REPLACEMENT_SEED_DISCOVERY_WRITTEN")
    status = "DISCOVERED" if written else "NO_ELIGIBLE_TARGETS"
    return ReplacementForecastSeedDiscoveryReport(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons or ("REPLACEMENT_SEED_DISCOVERY_NOOP",))),
        discovered_count=len(written),
        skipped_count=max(len(targets) - len(written) - len(failed), 0),
        failed_count=len(failed),
        written_seed_files=tuple(written),
        failed_targets=tuple(failed),
    )
