"""Discover replacement forecast materialization seeds from DB and raw manifests."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from src.config import cities_by_name
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, read_manifest
from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql
from src.data.replacement_forecast_current_target_plan import (
    _latest_authorized_day0_fact,
    build_replacement_forecast_current_target_plan,
)
from src.data.replacement_forecast_materialization_seed_builder import (
    build_replacement_forecast_materialization_seed,
    latest_baseline_coverage_for_replacement_seed,
    market_bins_for_replacement_seed,
    write_seed,
)
from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
from src.state.db import _connect, _zeus_trade_db_path, get_world_connection_read_only


UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"
# Coverage authority anchor: seed discovery consumes
# build_replacement_forecast_current_target_plan(), whose candidate rows are
# filtered by tradeable_grade_coverage_sql. Keep this explicit import so this
# third coverage site cannot drift from the plan/queue helper silently.
_TRADEABLE_GRADE_COVERAGE_AUTHORITY = tradeable_grade_coverage_sql
_OPEN_POSITION_PHASES = frozenset(
    {
        "pending_entry",
        "active",
        "day0_window",
        "pending_exit",
    }
)
_MANIFEST_CACHE_LOCK = threading.Lock()
_MANIFEST_CACHE: dict[
    Path,
    dict[Path, tuple[tuple[int, int, int], RawForecastArtifactManifest]],
] = {}


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


def _temperature_native_to_c(value: float, *, unit: str) -> float:
    normalized = str(unit or "").strip().upper()
    if normalized == "C":
        return float(value)
    if normalized == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    raise ValueError(f"unsupported Day0 observed-extreme unit: {unit!r}")


def _day0_observed_extreme_seed_payload(
    *,
    city: str,
    target_date: str,
    metric: str,
    computed_at: datetime,
) -> dict[str, object] | None:
    """Canonical Day0 observed-extreme payload for materialization seeds.

    Seed discovery is the producer for replacement materialization requests. Once
    a city local target day has started, the materializer correctly requires the
    observed running high/low so q is conditioned on the settlement-day hard
    fact. The discovery lane must therefore read the same canonical
    ``observation_instants`` surface instead of permanently excluding all Day0
    targets from seeding.
    """

    city_obj = cities_by_name.get(str(city))
    if city_obj is None:
        return None
    metric_norm = str(metric or "").strip().lower()
    if metric_norm not in {"high", "low"}:
        return None
    unit = str(getattr(city_obj, "settlement_unit", "") or "").strip().upper()
    if not unit:
        return None
    try:
        world_conn = get_world_connection_read_only()
    except Exception:  # noqa: BLE001 - discovery remains fail-soft
        return None
    try:
        world_conn.row_factory = sqlite3.Row
        fact = _latest_authorized_day0_fact(
            world_conn,
            city=city,
            target_date=target_date,
            temperature_metric=metric_norm,
            decision_time=computed_at,
            require_settlement_channel=True,
        )
        if fact is None:
            return None
        try:
            observed_c = _temperature_native_to_c(
                float(fact["observed_extreme_native"]),
                unit=unit,
            )
            sample_count = int(fact.get("sample_count") or 0)
            observation_time = str(fact["observation_time"])
        except (KeyError, TypeError, ValueError):
            return None
        if sample_count <= 0 or not observation_time:
            return None
        return {
            "day0_observed_extreme_c": float(observed_c),
            "day0_observed_extreme_source": str(fact.get("source") or "unknown"),
            "day0_observed_extreme_observation_time": observation_time,
            "day0_observed_extreme_sample_count": sample_count,
            "day0_observed_extreme_unit": unit,
        }
    finally:
        try:
            world_conn.close()
        except Exception:  # noqa: BLE001
            pass


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
    root = raw_manifest_dir.resolve()
    paths = tuple(sorted(root.rglob("*.manifest.json")))
    current: dict[
        Path,
        tuple[tuple[int, int, int], RawForecastArtifactManifest],
    ] = {}
    with _MANIFEST_CACHE_LOCK:
        cached = _MANIFEST_CACHE.get(root, {})
        for path in paths:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            entry = cached.get(path)
            if entry is not None and entry[0] == signature:
                manifest = entry[1]
            else:
                loaded = read_manifest(path)
                manifest = RawForecastArtifactManifest(
                    **{
                        **loaded.to_dict(),
                        "product_metadata": {
                            **dict(loaded.product_metadata),
                            "manifest_json": str(path),
                        },
                    }
                )
            current[path] = (signature, manifest)
        _MANIFEST_CACHE[root] = current
    return tuple(
        manifest
        for _, manifest in current.values()
        if manifest.source_available_at <= computed_at
    )


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
    the declared horizon alone is therefore unsafe — the truth is the payload's actual
    extractor-grade coverage. Fail-CLOSED here: if seed discovery cannot prove that the exact
    payload can be extracted for the target local day, it must not enqueue a live request that
    will predictably fail in the materializer.
    """
    from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
        extract_openmeteo_ecmwf_ifs9_localday_anchor,
    )

    payload_text = _manifest_path_value(manifest, "openmeteo_payload_json") or manifest.artifact_path
    if not payload_text:
        return True
    base_dir = _manifest_base_dir(manifest, fallback=Path(manifest.artifact_path).parent)
    payload_path = _resolve_path(payload_text, base_dir=base_dir)
    try:
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


def _json_path_valid(path_text: str | None, *, base_dir: Path | None = None) -> bool:
    if path_text is None or not str(path_text).strip():
        return False
    path = Path(str(path_text))
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except Exception:
        return False


def _manifest_materialization_inputs_valid(manifest: RawForecastArtifactManifest) -> bool:
    """Reject corrupt raw JSON before it becomes a live materialization request.

    Manifest byte/hash checks prove the file matches the manifest; they do not
    prove the bytes are parseable JSON. A corrupt but self-consistent payload
    must be treated as not materializable so held-position reseeds do not loop
    on the same failing request.
    """

    if manifest.source_id != "openmeteo_ecmwf_ifs_9km":
        return True
    base_dir = _manifest_base_dir(manifest, fallback=Path(manifest.artifact_path).parent)
    payload = _manifest_path_value(manifest, "openmeteo_payload_json") or manifest.artifact_path
    precision = _manifest_path_value(manifest, "precision_metadata_json")
    return _json_path_valid(payload, base_dir=base_dir) and _json_path_valid(
        precision,
        base_dir=base_dir,
    )


def _latest_manifest(
    manifests: tuple[RawForecastArtifactManifest, ...],
    *,
    source_id: str,
    data_version: str,
    city: str,
    target_date: str,
    city_timezone: str | None = None,
    cycle_admissible: Callable[[RawForecastArtifactManifest], bool] | None = None,
) -> RawForecastArtifactManifest | None:
    candidates = [
        manifest
        for manifest in manifests
        if manifest.source_id == source_id and manifest.data_version == data_version
        and _manifest_allows_city(manifest, city=city)
        and _manifest_allows_target_date(manifest, target_date=target_date)
        and _manifest_materialization_inputs_valid(manifest)
        and (cycle_admissible is None or cycle_admissible(manifest))
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

    if tz_name:
        covering = [manifest for manifest in candidates if _covers(manifest)]
        if not covering:
            return None
        candidates = covering

    return max(
        candidates,
        key=lambda manifest: (
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
    dates = metadata.get("target_dates")
    if isinstance(dates, list) and dates:
        if target_date in {str(item).strip() for item in dates}:
            return True
        # Meta-stamped current-target artifacts are multi-day payloads even when
        # their manifest retained the filename's start-date list. Let horizon
        # admission decide, then _latest_manifest proves actual payload coverage.
        if str(metadata.get("openmeteo_endpoint") or "") != "standard_api_meta_stamped":
            return False
        return _manifest_horizon_allows_target_date(metadata, target_date=target_date)
    explicit = metadata.get("target_date")
    if explicit is not None and str(explicit).strip():
        if str(explicit).strip() == target_date:
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
    if endpoint and endpoint not in {"single_runs_api", "standard_api_meta_stamped"}:
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


def _fusion_current_values_schema_ready(conn: sqlite3.Connection) -> bool:
    if "raw_model_forecasts" not in _table_names(conn):
        return False
    required = {
        "raw_model_forecast_id",
        "model",
        "forecast_value_c",
        "lead_days",
        "city",
        "metric",
        "target_date",
        "source_cycle_time",
        "endpoint",
    }
    return required.issubset(_columns(conn, "raw_model_forecasts"))


def _manifest_cycle_has_fusion_current_values(
    conn: sqlite3.Connection,
    manifest: RawForecastArtifactManifest,
    *,
    city: str,
    target_date: str,
    metric: str,
) -> bool | None:
    """Return whether this manifest cycle has persisted BPF current values.

    This is telemetry, not an admission filter. Seed discovery owns raw anchor
    materialization freshness; the materializer and reactor own q-mode/live
    eligibility. Blocking seed discovery here freezes held-position belief on
    an older cycle whenever BPF extras are temporarily absent, even though the
    materializer can publish an explicitly non-live-eligible capture-missing
    posterior and the live gate will refuse submit from that q_mode.
    """

    if not _fusion_current_values_schema_ready(conn):
        return None
    from src.data.replacement_current_value_serving import (  # noqa: PLC0415
        read_current_instrument_values,
    )

    try:
        manifest_cycle = manifest.source_cycle_time.astimezone(UTC).isoformat()
        served = read_current_instrument_values(
            conn,
            city=city,
            metric=metric,
            target_date=target_date,
            source_cycle_time_iso=manifest_cycle,
        )
    except Exception:
        return False
    return any(str(value.served_cycle) == manifest_cycle for value in served.values())


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


def held_position_family_priorities() -> dict[tuple[str, str, str], int]:
    """Return live held-family priority from canonical position_current.

    Forecast materialization is both an entry input and the held-position
    redecision input. When a fresh cycle arrives, held families must not wait
    behind alphabetical market discovery; stale beliefs directly affect exit,
    hold, and shift decisions.
    """

    path = _zeus_trade_db_path()
    if not path.exists():
        return {}
    try:
        conn = _connect(path, write_class=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            tables = _table_names(conn)
            if "position_current" not in tables:
                return {}
            cols = _columns(conn, "position_current")
            required = {"city", "target_date", "temperature_metric", "phase"}
            if not required.issubset(cols):
                return {}
            rows = conn.execute(
                """
                SELECT city, target_date, temperature_metric, phase
                FROM position_current
                WHERE city IS NOT NULL AND city != ''
                  AND target_date IS NOT NULL AND target_date != ''
                  AND temperature_metric IS NOT NULL AND temperature_metric != ''
                  AND phase IN ('pending_entry', 'active', 'day0_window', 'pending_exit')
                """
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    priorities: dict[tuple[str, str, str], int] = {}
    for row in rows:
        phase = str(row["phase"] or "")
        key = (
            str(row["city"]),
            str(row["target_date"]),
            str(row["temperature_metric"]),
        )
        priorities[key] = 0 if phase in {"day0_window", "pending_exit"} else 1
    return priorities


def discover_replacement_forecast_materialization_seeds(
    *,
    forecast_db: Path | str,
    raw_manifest_dir: Path | str,
    seed_dir: Path | str,
    computed_at: datetime | str | None = None,
    limit: int = 10,
) -> ReplacementForecastSeedDiscoveryReport:
    """Write seed JSON for DB targets with all required raw inputs available."""

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
        held_family_priority = held_position_family_priorities()
        targets = tuple(
            {
                "city": row.city,
                "target_date": row.target_date,
                "temperature_metric": row.temperature_metric,
                "baseline_source_run_id": row.baseline_source_run_id,
                "baseline_source_cycle_time": row.baseline_source_cycle_time,
                "openmeteo_source_run_id": row.openmeteo_source_run_id,
                "day0_observed_extreme_required": row.day0_observed_extreme_required,
            }
            # SEED-BUDGET STARVATION KILL (2026-06-11): two coupled defects froze the
            # tradeable scopes behind permanently-failing far-date targets.
            #   1. Far-date-first: the plan's order put day-2 non-tradeable scopes ahead
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
                (
                    row for row in target_plan.rows
                    if row.can_seed
                    or (
                        not row.covered
                        and row.day0_observed_extreme_required
                        and row.openmeteo_manifest_count > 0
                    )
                ),
                key=lambda row: (
                    held_family_priority.get(
                        (str(row.city), str(row.target_date), str(row.temperature_metric)),
                        2,
                    ),
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
            openmeteo_source_run_id = str(target.get("openmeteo_source_run_id") or "").strip()
            target_key = f"{city}|{target_date}|{metric}"
            day0_seed_payload: dict[str, object] = {}
            if bool(target.get("day0_observed_extreme_required")):
                payload = _day0_observed_extreme_seed_payload(
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    computed_at=computed,
                )
                if payload is None:
                    reasons.append("REPLACEMENT_SEED_DISCOVERY_DAY0_OBSERVED_EXTREME_MISSING")
                    continue
                day0_seed_payload = payload
            expected = expected_replacement_dependency_identity_by_role(metric)
            # City timezone for payload-coverage selection (eastward-blackout fix): resolved
            # from the SAME canonical city registry the seed builder uses, so the selector and
            # the local-day extractor agree on the timezone. Absent -> coverage check is neutral.
            from src.config import cities_by_name  # noqa: PLC0415

            _city_cfg = cities_by_name.get(city)
            _city_tz = str(getattr(_city_cfg, "timezone", "") or "") or None
            fusion_schema_ready = _fusion_current_values_schema_ready(conn)
            openmeteo = _latest_manifest(
                manifests,
                source_id=expected["openmeteo_ifs9_anchor"].source_id,
                data_version=expected["openmeteo_ifs9_anchor"].data_version,
                city=city,
                target_date=target_date,
                city_timezone=_city_tz,
                cycle_admissible=(
                    (
                        lambda manifest, source_run_id=openmeteo_source_run_id: str(
                            manifest.product_metadata.get("source_run_id") or ""
                        ).strip()
                        == source_run_id
                    )
                    if openmeteo_source_run_id
                    else None
                ),
            )
            if openmeteo is None:
                failed.append(target_key)
                reasons.append("REPLACEMENT_SEED_DISCOVERY_REQUIRED_MANIFEST_MISSING")
                continue
            if fusion_schema_ready:
                fusion_current_ready = _manifest_cycle_has_fusion_current_values(
                    conn,
                    openmeteo,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                )
                if fusion_current_ready is False:
                    reasons.append(
                        "REPLACEMENT_SEED_DISCOVERY_FUSION_CURRENT_VALUES_MISSING_NON_BLOCKING"
                    )
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
                **day0_seed_payload,
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
