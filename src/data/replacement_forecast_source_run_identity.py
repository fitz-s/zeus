"""Source-run identity guards for replacement forecast live dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import time
from typing import Mapping

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    coordinate_bound_data_version,
    split_coordinate_bound_data_version,
)


NATIVE_COORDINATE_SEMANTIC_ROUTE = "owned_manifest_city_inputs_v1"


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate coordinate manifest field")
        result[key] = value
    return result


def native_coordinate_manifest_compatibility(
    city: str, temperature_metric: str, data_version: str,
    *, manifest_cache: dict | None = None, deadline_monotonic: float | None = None,
) -> dict[str, object] | None:
    """Prove one original native extraction's inputs equal the current city's.

    This is no forecast/ground/LSM permission: their canonical validators remain
    independent. SCOPE is this city and exact v3 product; DRAIN is the existing
    seed loop or a real new extraction when its inputs differ; RESET is a new
    certificate binding this proof, without changing the original source row.
    """
    base = {"high": ECMWF_OPENDATA_HIGH_DATA_VERSION,
            "low": ECMWF_OPENDATA_LOW_DATA_VERSION}.get(temperature_metric)
    identity = split_coordinate_bound_data_version(data_version)
    if base is None or identity is None or identity[0] != base or not isinstance(city, str):
        return None
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise TimeoutError("native coordinate compatibility deadline")
    try:
        from src.config import runtime_coordinate_manifest_json
        from src.data.ecmwf_open_data import _resolve_opendata_paths

        current_entry = None if manifest_cache is None else manifest_cache.get("current_profile")
        if current_entry is None:
            current_bytes = runtime_coordinate_manifest_json().encode("utf-8")
            current = json.loads(current_bytes, object_pairs_hook=_unique_json_object)
            if manifest_cache is not None:
                manifest_cache["current_profile"] = (current_bytes, current)
        else:
            current_bytes, current = current_entry
        root = _resolve_opendata_paths().raw_root.resolve()
        path = root / "docs" / f"opendata_coordinates_{identity[1]}.json"
        if path.is_symlink() or not path.is_file():
            return None
        stat = path.stat()
        if not 0 < stat.st_size <= 1024 * 1024:
            return None
        stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        key = (str(root), identity[1])
        cached = (manifest_cache or {}).get(key)
        if cached is not None and cached[0] == stamp:
            original = cached[1]
        else:
            raw = path.read_bytes()
            if len(raw) != stat.st_size or sha256(raw).hexdigest() != identity[1]:
                return None
            original = json.loads(raw, object_pairs_hook=_unique_json_object)
            if manifest_cache is not None:
                manifest_cache[key] = (stamp, original)

        def entry(manifest):
            if (not isinstance(manifest, dict)
                    or set(manifest) != {"coordinate_basis", "cities"}
                    or manifest["coordinate_basis"] != "runtime_settlement_station"
                    or not isinstance(manifest["cities"], list) or not manifest["cities"]):
                return None
            names = [item.get("city") if isinstance(item, dict) else None
                     for item in manifest["cities"]]
            if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
                return None
            selected = next((item for item in manifest["cities"] if item["city"] == city), None)
            if not isinstance(selected, dict) or set(selected) != {"city", "lat", "lon", "timezone", "unit", "station_geometry"}:
                return None
            geometry = selected["station_geometry"]
            if not isinstance(geometry, dict):
                return None
            fields = {"station_id", "lat", "lon", "validity_reason"}
            legacy = {"elevation_m", "station_surface"}
            if set(geometry) not in (fields, fields | legacy) or geometry["validity_reason"] is not None:
                return None
            if not isinstance(geometry["station_id"], str) or not geometry["station_id"]:
                return None
            if legacy <= set(geometry):
                elevation = geometry["elevation_m"]
                # Historical producer admission, never current land authority.
                if (geometry["station_surface"] != "land" or elevation is not None and (
                        isinstance(elevation, bool) or not isinstance(elevation, (int, float))
                        or not math.isfinite(elevation))):
                    return None
            for row in (selected, geometry):
                for name, bound in (("lat", 90), ("lon", 180)):
                    value = row[name]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > bound:
                        return None
            if selected["unit"] not in {"C", "F"} or not isinstance(selected["timezone"], str) or not selected["timezone"]:
                return None
            return {**selected, "station_geometry": {name: geometry[name] for name in fields}}

        old_city, current_city = entry(original), entry(current)
        if old_city is None or old_city != current_city:
            return None
        semantic_bytes = json.dumps(old_city, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError("native coordinate compatibility deadline")
        return {"revision": NATIVE_COORDINATE_SEMANTIC_ROUTE, "city": city,
                "temperature_metric": temperature_metric, "data_version": data_version,
                "source_manifest_sha256": identity[1],
                "current_manifest_sha256": sha256(current_bytes).hexdigest(),
                "city_inputs_sha256": sha256(semantic_bytes).hexdigest(),
                "original_city_inputs": next(item for item in original["cities"] if item["city"] == city)}
    except TimeoutError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def register_native_coordinate_compatibility_sql(conn, *, deadline_monotonic=None):
    """One query/run-local manifest cache; SQL still selects canonical authority."""
    cache = {}
    current = {metric: expected_replacement_dependency_identity_by_role(metric)["baseline_b0"].data_version
               for metric in ("high", "low")}

    def compatible(city, metric, version):
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError("native coordinate compatibility deadline")
        return int(version is not None and (version == current.get(metric)
            or native_coordinate_manifest_compatibility(city, metric, version,
                manifest_cache=cache, deadline_monotonic=deadline_monotonic) is not None))

    conn.create_function("native_coordinate_inputs_current", 3, compatible)


def native_coordinate_snapshot_compatibility(row: Mapping[str, object], *, metric: str,
        manifest_cache=None, deadline_monotonic=None) -> dict[str, object] | None:
    """Bind original station schema to its owned manifest, never relabel bytes."""
    from src.contracts.ensemble_snapshot_provenance import grid_surface_evidence_identity_hash

    proof = native_coordinate_manifest_compatibility(str(row.get("city") or ""), metric,
        str(row.get("dataset_id") or ""), manifest_cache=manifest_cache,
        deadline_monotonic=deadline_monotonic)
    if proof is None:
        return None
    try:
        provenance = row["provenance_json"]
        provenance = json.loads(provenance) if isinstance(provenance, str) else provenance
        surface = provenance["grid_surface_evidence"]
        original = proof["original_city_inputs"]
        geometry = surface["station_geometry"]
        original_geometry = original["station_geometry"]
        if (set(geometry) - set(original_geometry) - {"source", "registry_sha256"}
                or {key: geometry[key] for key in original_geometry} != original_geometry
                or surface["request_lat"] != original["lat"] or surface["request_lon"] != original["lon"]):
            return None
        legacy = "station_surface" in original["station_geometry"]
        return {key: value for key, value in proof.items()
                if key not in ("original_city_inputs", "current_manifest_sha256")} | {
            "original_station_schema": "reference_height_surface_v1" if legacy else "reference_coordinates_v2",
            "original_grid_surface_evidence_identity_hash": grid_surface_evidence_identity_hash(surface,
                legacy_station_schema=legacy),
        }
    except (KeyError, TypeError, ValueError):
        return None


def native_coordinate_certificate_reason(conn, *, shape, city, target_date, metric) -> str | None:
    """Replay this certificate's native compatibility, including the old hash.

    Structural SQL is not sufficient: an old manifest requires a newly
    constructed binding before either public read or coverage can consume it.
    """
    if not isinstance(shape, Mapping):
        return "REPLACEMENT_CURRENT_COORDINATE_IDENTITY_MISMATCH"
    row = conn.execute("""SELECT es.city, es.target_date, es.temperature_metric, es.dataset_id, es.provenance_json,
        es.source_cycle_time, es.source_available_at, sr.manifest_hash
        FROM ensemble_snapshots es JOIN source_run sr ON sr.source_run_id=es.source_run_id
        WHERE es.snapshot_id=?""", (shape.get("snapshot_id"),)).fetchone()
    if row is None:
        return "REPLACEMENT_CURRENT_COORDINATE_SNAPSHOT_MISSING"
    native = dict(zip(("city", "target_date", "temperature_metric", "dataset_id", "provenance_json",
        "source_cycle_time", "source_available_at", "manifest_hash"), row))
    if (native["city"], native["target_date"], native["temperature_metric"]) != (city, str(target_date), metric):
        return "REPLACEMENT_CURRENT_COORDINATE_IDENTITY_MISMATCH"
    current = expected_replacement_dependency_identity_by_role(metric)["baseline_b0"].data_version
    if native["dataset_id"] == current and "native_coordinate_compatibility" not in shape:
        return None
    from src.data.executable_forecast_reader import grid_surface_evidence_reason
    from src.contracts.ensemble_snapshot_provenance import grid_surface_evidence_identity_hash
    if grid_surface_evidence_reason(native) is not None:
        return "REPLACEMENT_CURRENT_COORDINATE_IDENTITY_MISMATCH"
    compatibility = native_coordinate_snapshot_compatibility(native, metric=metric)
    if (compatibility is None or shape.get("native_coordinate_compatibility") != compatibility
            or native["manifest_hash"] != compatibility["source_manifest_sha256"]):
        return "REPLACEMENT_CURRENT_COORDINATE_COMPATIBILITY_MISSING_OR_INVALID"
    try:
        surface = json.loads(native["provenance_json"])["grid_surface_evidence"]
        if shape.get("grid_surface_evidence_identity_hash") != grid_surface_evidence_identity_hash(surface):
            return "REPLACEMENT_CURRENT_COORDINATE_GRID_IDENTITY_MISMATCH"
    except (KeyError, TypeError, ValueError):
        return "REPLACEMENT_CURRENT_COORDINATE_GRID_IDENTITY_MISMATCH"
    return None


@dataclass(frozen=True)
class ReplacementDependencyExpectedIdentity:
    role: str
    source_id: str
    product_id: str
    data_version: str | None
    physical_quantity: str
    observation_field: str
    expected_members: int | None
    raw_ensemble_eligible: bool


@dataclass(frozen=True)
class ReplacementSourceRunIdentityDecision:
    valid: bool
    reason_codes: tuple[str, ...]
    role: str
    source_run_id: str | None


def _current_coordinate_manifest_sha() -> str | None:
    """Hash the current runtime coordinate profile, or report it unavailable.

    A missing or malformed current profile is deliberately not interchangeable
    with the historical unbound OpenData data version.  Callers turn ``None``
    into their normal dependency-missing result.
    """

    try:
        from src.config import runtime_coordinate_manifest_json

        manifest_json = runtime_coordinate_manifest_json()
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None
    if not isinstance(manifest_json, str) or not manifest_json:
        return None
    return sha256(manifest_json.encode("utf-8")).hexdigest()


def expected_replacement_dependency_identity_by_role(
    temperature_metric: str,
    *, city: str | None = None, baseline_data_version: str | None = None,
    manifest_cache: dict | None = None, deadline_monotonic: float | None = None,
) -> dict[str, ReplacementDependencyExpectedIdentity]:
    """Return the fixed replacement dependency identity map for one metric."""

    if temperature_metric not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
    candidate_data_version = baseline_data_version
    suffix = "max" if temperature_metric == "high" else "min"
    baseline_data_version = (
        ECMWF_OPENDATA_HIGH_DATA_VERSION
        if temperature_metric == "high"
        else ECMWF_OPENDATA_LOW_DATA_VERSION
    )
    coordinate_manifest_sha = _current_coordinate_manifest_sha()
    if coordinate_manifest_sha is not None:
        baseline_data_version = coordinate_bound_data_version(
            baseline_data_version, coordinate_manifest_sha
        )
    else:
        baseline_data_version = None
    if (baseline_data_version is not None and city is not None
            and candidate_data_version is not None and candidate_data_version != baseline_data_version
            and native_coordinate_manifest_compatibility(city, temperature_metric, candidate_data_version,
                manifest_cache=manifest_cache, deadline_monotonic=deadline_monotonic) is not None):
        baseline_data_version = candidate_data_version
    baseline_physical = "mx2t3_local_calendar_day_max" if temperature_metric == "high" else "mn2t3_local_calendar_day_min"
    anchor_physical = f"deterministic_2t_anchor_local_calendar_day_{suffix}"
    posterior_physical = f"openmeteo_ecmwf_ifs9_bayes_fusion_local_calendar_day_{suffix}"
    observation_field = "high_temp" if temperature_metric == "high" else "low_temp"
    return {
        "baseline_b0": ReplacementDependencyExpectedIdentity(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version=baseline_data_version,
            physical_quantity=baseline_physical,
            observation_field=observation_field,
            expected_members=51,
            raw_ensemble_eligible=True,
        ),
        "openmeteo_ifs9_anchor": ReplacementDependencyExpectedIdentity(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version=f"openmeteo_ecmwf_ifs9_anchor_localday_{temperature_metric}",
            physical_quantity=anchor_physical,
            observation_field=observation_field,
            expected_members=None,
            raw_ensemble_eligible=False,
        ),
        "soft_anchor_posterior": ReplacementDependencyExpectedIdentity(
            role="soft_anchor_posterior",
            source_id="openmeteo_ecmwf_ifs9_bayes_fusion",
            product_id="openmeteo_ecmwf_ifs9_bayes_fusion_v1",
            data_version=f"openmeteo_ecmwf_ifs9_bayes_fusion_{temperature_metric}_v1",
            physical_quantity=posterior_physical,
            observation_field=observation_field,
            expected_members=None,
            raw_ensemble_eligible=False,
        ),
    }


def _read(row: Mapping[str, object], key: str) -> object:
    return row.get(key)


def validate_replacement_source_run_identity(
    *,
    role: str,
    temperature_metric: str,
    source_run: Mapping[str, object],
    coverage: Mapping[str, object] | None = None,
) -> ReplacementSourceRunIdentityDecision:
    """Validate source_run/source_run_coverage identity for a replacement role."""

    actual_version = _read(source_run, "dataset_id") or _read(source_run, "data_version")
    expected_by_role = expected_replacement_dependency_identity_by_role(temperature_metric,
        city=str(coverage.get("city") or "") if coverage is not None and role == "baseline_b0" else None,
        baseline_data_version=actual_version if role == "baseline_b0" else None)
    expected = expected_by_role.get(role)
    if expected is None:
        raise ValueError(f"unsupported replacement dependency role: {role!r}")
    reasons: list[str] = []
    source_run_id = None if _read(source_run, "source_run_id") is None else str(_read(source_run, "source_run_id"))
    if not source_run_id:
        reasons.append("REPLACEMENT_SOURCE_RUN_ID_MISSING")
    if _read(source_run, "source_id") != expected.source_id:
        reasons.append("REPLACEMENT_SOURCE_RUN_SOURCE_ID_MISMATCH")
    dataset_id = _read(source_run, "dataset_id") or _read(source_run, "data_version")
    coordinate_profile_missing = expected.data_version is None
    if coordinate_profile_missing:
        reasons.append("REPLACEMENT_SOURCE_RUN_CURRENT_COORDINATE_PROFILE_MISSING")
    elif dataset_id is not None and dataset_id != expected.data_version:
        reasons.append("REPLACEMENT_SOURCE_RUN_DATA_VERSION_MISMATCH")
    if (role == "baseline_b0" and dataset_id != expected_replacement_dependency_identity_by_role(temperature_metric)["baseline_b0"].data_version
            and dataset_id == expected.data_version
            and _read(source_run, "manifest_hash") != split_coordinate_bound_data_version(dataset_id)[1]):
        reasons.append("REPLACEMENT_SOURCE_RUN_COORDINATE_MANIFEST_MISMATCH")
    if _read(source_run, "temperature_metric") not in {None, temperature_metric}:
        reasons.append("REPLACEMENT_SOURCE_RUN_METRIC_MISMATCH")
    if _read(source_run, "physical_quantity") not in {None, expected.physical_quantity}:
        reasons.append("REPLACEMENT_SOURCE_RUN_PHYSICAL_QUANTITY_MISMATCH")
    if _read(source_run, "observation_field") not in {None, expected.observation_field}:
        reasons.append("REPLACEMENT_SOURCE_RUN_OBSERVATION_FIELD_MISMATCH")
    if expected.expected_members is not None:
        expected_members = _read(source_run, "expected_members")
        if expected_members is not None and int(expected_members) != expected.expected_members:
            reasons.append("REPLACEMENT_SOURCE_RUN_EXPECTED_MEMBERS_MISMATCH")
        observed_members = _read(source_run, "observed_members")
        if observed_members is not None and int(observed_members) > expected.expected_members:
            reasons.append("REPLACEMENT_SOURCE_RUN_MEMBER_COUNT_EXCEEDS_PRODUCT")
    else:
        for member_field in ("expected_members", "observed_members"):
            value = _read(source_run, member_field)
            if value not in {None, 0}:
                reasons.append("REPLACEMENT_SOURCE_RUN_NON_ENSEMBLE_HAS_MEMBERS")
                break

    if coverage is not None:
        if _read(coverage, "source_run_id") != source_run_id:
            reasons.append("REPLACEMENT_SOURCE_RUN_COVERAGE_ID_MISMATCH")
        if _read(coverage, "source_id") != expected.source_id:
            reasons.append("REPLACEMENT_SOURCE_RUN_COVERAGE_SOURCE_ID_MISMATCH")
        if not coordinate_profile_missing and _read(coverage, "data_version") != expected.data_version:
            reasons.append("REPLACEMENT_SOURCE_RUN_COVERAGE_DATA_VERSION_MISMATCH")
        if _read(coverage, "temperature_metric") != temperature_metric:
            reasons.append("REPLACEMENT_SOURCE_RUN_COVERAGE_METRIC_MISMATCH")
        if _read(coverage, "physical_quantity") != expected.physical_quantity:
            reasons.append("REPLACEMENT_SOURCE_RUN_COVERAGE_PHYSICAL_QUANTITY_MISMATCH")
        if _read(coverage, "observation_field") != expected.observation_field:
            reasons.append("REPLACEMENT_SOURCE_RUN_COVERAGE_OBSERVATION_FIELD_MISMATCH")

    return ReplacementSourceRunIdentityDecision(
        valid=not reasons,
        reason_codes=tuple(reasons or ("REPLACEMENT_SOURCE_RUN_IDENTITY_VALID",)),
        role=role,
        source_run_id=source_run_id,
    )
