"""Source-run identity guards for replacement forecast live dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    coordinate_bound_data_version,
)


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
) -> dict[str, ReplacementDependencyExpectedIdentity]:
    """Return the fixed replacement dependency identity map for one metric."""

    if temperature_metric not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
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

    expected_by_role = expected_replacement_dependency_identity_by_role(temperature_metric)
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
