"""Discover replacement forecast materialization seeds from DB and raw manifests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, read_manifest
from src.data.replacement_forecast_current_target_plan import build_replacement_forecast_current_target_plan
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


def _manifest_payload_covers_target_local_day(
    manifest: RawForecastArtifactManifest,
    *,
    city_timezone: str,
    target_date: str,
) -> bool:
    """True iff the manifest's ON-DISK payload contains >=1 hourly sample inside the
    wanted local day.

    EASTWARD-BLACKOUT FIX (2026-06-23): the live downloader writes the Open-Meteo single-
    runs anchor manifest with ``forecast_hours=120`` UNCONDITIONALLY, but the bytes on disk
    can be a PARTIAL-HORIZON capture — when the provider's run was only partly published at
    fetch time the rung-1 single-runs response carried only the launch local day (24h). The
    ``payload_path.exists()`` guard in the downloader then never re-fetches it, so the 24h
    file persists. ``_manifest_horizon_allows_target_date`` TRUSTS the declared 120h and
    admits that 24h file for a LATER target date it physically cannot serve; the materialize
    subprocess then raises "insufficient Open-Meteo hourly samples inside target local day"
    and the whole eastward family produces no posterior (a discovery blackout). Selecting on
    the declared horizon alone is therefore unsafe — the truth is the payload's actual time
    coverage. This reuses the SAME local-day windowing the extractor uses (parse each
    ``hourly.time`` into the city timezone, count samples on ``target_date``), so the
    selector and the extractor can never disagree on coverage. Fail-OPEN: any read/parse
    error returns True so a transient FS/JSON hiccup never makes an otherwise-admissible
    manifest vanish (the extractor remains the fail-closed backstop). NO daily extreme is
    computed or fabricated here — coverage is a count of in-day samples only, so the
    ``require_full_localday`` guard's intent (reject horizon-clipped partial days at
    extraction) is untouched.
    """
    from src.data.openmeteo_ecmwf_ifs9_anchor import _parse_openmeteo_time  # noqa: PLC0415

    payload_text = _manifest_path_value(manifest, "openmeteo_payload_json") or manifest.artifact_path
    if not payload_text:
        return True
    base_dir = _manifest_base_dir(manifest, fallback=Path(manifest.artifact_path).parent)
    payload_path = _resolve_path(payload_text, base_dir=base_dir)
    try:
        wanted = date.fromisoformat(str(target_date).strip())
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        times = payload["hourly"]["time"]
    except Exception:
        return True  # fail-open: extractor stays the fail-closed backstop
    for raw_time in times:
        try:
            if _parse_openmeteo_time(str(raw_time), city_timezone=city_timezone).date() == wanted:
                return True
        except Exception:
            continue
    return False


def _latest_manifest(
    manifests: tuple[RawForecastArtifactManifest, ...],
    *,
    source_id: str,
    data_version: str,
    city: str,
    target_date: str,
    city_timezone: str | None = None,
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
    # PRIMARY selection key: does the payload ACTUALLY cover the wanted local day? A
    # partial-horizon capture admitted only by its (mislabeled) declared forecast_hours sorts
    # BELOW any sibling that genuinely covers the day, so the fresher-but-broken neighbor can
    # never be picked over a covering manifest at the same cycle/availability stamps (the live
    # tie that selected the 24h file over the 120h sibling). When no city timezone is provided
    # (defensive — the live caller always passes it) OR no candidate covers, the recency key
    # alone decides, preserving the prior behaviour. The (source_cycle_time, source_available_at,
    # captured_at) recency key is retained as the secondary discriminator.
    tz_name = city_timezone or str(
        candidates[0].product_metadata.get("city_timezone") or ""
    )

    def _covers(manifest: RawForecastArtifactManifest) -> bool:
        if not tz_name:
            return True  # no tz to check coverage with — neutral, recency decides
        return _manifest_payload_covers_target_local_day(
            manifest, city_timezone=tz_name, target_date=target_date
        )

    return max(
        candidates,
        key=lambda manifest: (
            _covers(manifest),
            manifest.source_cycle_time,
            manifest.source_available_at,
            manifest.captured_at,
        ),
    )


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
        if str(explicit).strip() == target_date:
            return True
    dates = metadata.get("target_dates")
    if isinstance(dates, list) and dates:
        if target_date in {str(item).strip() for item in dates}:
            return True
    return _manifest_horizon_allows_target_date(metadata, target_date=target_date)


def _manifest_horizon_allows_target_date(metadata: Mapping[str, object], *, target_date: str) -> bool:
    """Allow single-runs manifests for any local target date inside their forecast horizon.

    The Open-Meteo single-runs payload is one multi-day hourly file. Its legacy
    manifest metadata records the local date used in the artifact filename, not
    the complete set of target dates materializable from the file. The extracted
    ``raw_model_forecasts`` rows are target-date scoped, so manifest admission
    must use the same multi-day horizon or held day+1/day+2 families can be
    marked stale while cycle-advance incorrectly reports no materializable
    newer cycle.
    """

    artifact_class = str(metadata.get("artifact_class") or "")
    endpoint = str(metadata.get("openmeteo_endpoint") or "")
    if artifact_class != "openmeteo_ecmwf_ifs9_anchor_current_targets":
        return False
    if endpoint and endpoint != "single_runs_api":
        return False
    start_raw = metadata.get("target_date")
    if start_raw is None or not str(start_raw).strip():
        return False
    try:
        start = date.fromisoformat(str(start_raw).strip())
        wanted = date.fromisoformat(str(target_date).strip())
        hours = int(float(metadata.get("forecast_hours") or 0))
    except Exception:
        return False
    if hours <= 0:
        return False
    # A 120h hourly payload spans the start date plus up to five following
    # local calendar dates depending on timezone/run hour. This is an admission
    # bound only; materialization still fails closed if the payload lacks the
    # requested local day.
    max_extra_days = max(0, (hours + 23) // 24)
    return start <= wanted <= start + timedelta(days=max_extra_days)


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
            # City timezone for payload-coverage selection (eastward-blackout fix): resolved
            # from the SAME canonical city registry the seed builder uses, so the selector and
            # the local-day extractor agree on the timezone. Absent -> coverage check is neutral.
            from src.config import cities_by_name  # noqa: PLC0415

            _city_cfg = cities_by_name.get(city)
            _city_tz = str(getattr(_city_cfg, "timezone", "") or "") or None
            openmeteo = _latest_manifest(
                manifests,
                source_id=expected["openmeteo_ifs9_anchor"].source_id,
                data_version=expected["openmeteo_ifs9_anchor"].data_version,
                city=city,
                target_date=target_date,
                city_timezone=_city_tz,
            )
            if openmeteo is None:
                failed.append(target_key)
                reasons.append("REPLACEMENT_SEED_DISCOVERY_REQUIRED_MANIFEST_MISSING")
                continue
            openmeteo_payload = _manifest_path_value(openmeteo, "openmeteo_payload_json") or openmeteo.artifact_path
            precision_metadata = _manifest_path_value(openmeteo, "precision_metadata_json")
            if not openmeteo_payload or not precision_metadata:
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
            openmeteo_base_dir = _manifest_base_dir(openmeteo, fallback=raw_dir)
            seed_result = build_replacement_forecast_materialization_seed(
                city=city,
                target_date=target_date,
                temperature_metric=metric,
                market_bins=bins,
                baseline_coverage=coverage,
                openmeteo_manifest=openmeteo,
                openmeteo_payload_json=_resolve_path(openmeteo_payload, base_dir=openmeteo_base_dir),
                precision_metadata_json=_resolve_path(precision_metadata, base_dir=openmeteo_base_dir),
                computed_at=computed,
                base_dir=seed_path,
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
