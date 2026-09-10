# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement source_run/source_run_coverage identity from cross-product lineage drift.
# Reuse: Run before writing or reading replacement source_run dependencies, readiness rows, or replay provenance.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + Bayes fusion live integration.
"""Replacement source-run identity validator tests."""

from __future__ import annotations

from hashlib import sha256

import pytest

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    coordinate_bound_data_version,
)
from src.data.replacement_forecast_source_run_identity import (
    expected_replacement_dependency_identity_by_role,
    validate_replacement_source_run_identity,
)


_CURRENT_MANIFEST_JSON = '{"coordinate_profile":"test-current"}'


@pytest.fixture(autouse=True)
def _current_coordinate_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.config as config

    monkeypatch.setattr(
        config,
        "runtime_coordinate_manifest_json",
        lambda: _CURRENT_MANIFEST_JSON,
        raising=False,
    )


def _source_run(role: str, metric: str = "high", **overrides):
    expected = expected_replacement_dependency_identity_by_role(metric)[role]
    row = {
        "source_run_id": f"run-{role}",
        "source_id": expected.source_id,
        "dataset_id": expected.data_version,
        "temperature_metric": metric,
        "physical_quantity": expected.physical_quantity,
        "observation_field": expected.observation_field,
        "expected_members": expected.expected_members,
        "observed_members": expected.expected_members,
    }
    row.update(overrides)
    return row


def _coverage(role: str, metric: str = "high", **overrides):
    expected = expected_replacement_dependency_identity_by_role(metric)[role]
    row = {
        "source_run_id": f"run-{role}",
        "source_id": expected.source_id,
        "data_version": expected.data_version,
        "temperature_metric": metric,
        "physical_quantity": expected.physical_quantity,
        "observation_field": expected.observation_field,
    }
    row.update(overrides)
    return row


def test_expected_dependency_identity_map_separates_raw_anchor_and_derived_products() -> None:
    high = expected_replacement_dependency_identity_by_role("high")
    low = expected_replacement_dependency_identity_by_role("low")

    assert set(high) == {"baseline_b0", "openmeteo_ifs9_anchor", "soft_anchor_posterior"}
    assert high["openmeteo_ifs9_anchor"].raw_ensemble_eligible is False
    assert high["soft_anchor_posterior"].raw_ensemble_eligible is False
    assert high["soft_anchor_posterior"].source_id == "openmeteo_ecmwf_ifs9_bayes_fusion"
    assert high["soft_anchor_posterior"].data_version.endswith("_high_v1")
    assert low["soft_anchor_posterior"].data_version.endswith("_low_v1")


@pytest.mark.parametrize(
    ("metric", "base"),
    (("high", ECMWF_OPENDATA_HIGH_DATA_VERSION), ("low", ECMWF_OPENDATA_LOW_DATA_VERSION)),
)
def test_baseline_identity_is_bound_to_the_current_coordinate_manifest(
    metric: str,
    base: str,
) -> None:
    expected = expected_replacement_dependency_identity_by_role(metric)["baseline_b0"]

    assert expected.data_version == coordinate_bound_data_version(
        base, sha256(_CURRENT_MANIFEST_JSON.encode("utf-8")).hexdigest()
    )


def test_baseline_identity_rejects_a_prior_coordinate_manifest() -> None:
    old_data_version = coordinate_bound_data_version(
        ECMWF_OPENDATA_HIGH_DATA_VERSION, "a" * 64
    )
    decision = validate_replacement_source_run_identity(
        role="baseline_b0",
        temperature_metric="high",
        source_run=_source_run("baseline_b0", dataset_id=old_data_version),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_DATA_VERSION_MISMATCH" in decision.reason_codes


def test_baseline_identity_reports_missing_current_coordinate_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.config as config

    monkeypatch.delattr(config, "runtime_coordinate_manifest_json", raising=False)
    expected = expected_replacement_dependency_identity_by_role("high")["baseline_b0"]
    decision = validate_replacement_source_run_identity(
        role="baseline_b0", temperature_metric="high", source_run={}
    )

    assert expected.data_version is None
    assert decision.valid is False
    assert decision.reason_codes == (
        "REPLACEMENT_SOURCE_RUN_ID_MISSING",
        "REPLACEMENT_SOURCE_RUN_SOURCE_ID_MISMATCH",
        "REPLACEMENT_SOURCE_RUN_CURRENT_COORDINATE_PROFILE_MISSING",
    )


def test_source_run_identity_validates_source_run_and_coverage_pair() -> None:
    for role in ("baseline_b0", "openmeteo_ifs9_anchor", "soft_anchor_posterior"):
        decision = validate_replacement_source_run_identity(
            role=role,
            temperature_metric="high",
            source_run=_source_run(role),
            coverage=_coverage(role),
        )
        assert decision.valid is True
        assert decision.reason_codes == ("REPLACEMENT_SOURCE_RUN_IDENTITY_VALID",)
        assert decision.source_run_id == f"run-{role}"


def test_source_run_identity_blocks_wrong_data_version_and_metric() -> None:
    decision = validate_replacement_source_run_identity(
        role="baseline_b0",
        temperature_metric="high",
        source_run=_source_run(
            "baseline_b0",
            dataset_id="ecmwf_opendata_mn2t3_local_calendar_day_min",
            temperature_metric="low",
        ),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_DATA_VERSION_MISMATCH" in decision.reason_codes
    assert "REPLACEMENT_SOURCE_RUN_METRIC_MISMATCH" in decision.reason_codes


def test_source_run_identity_blocks_non_ensemble_members_on_anchor_and_posterior() -> None:
    for role in ("openmeteo_ifs9_anchor", "soft_anchor_posterior"):
        decision = validate_replacement_source_run_identity(
            role=role,
            temperature_metric="high",
            source_run=_source_run(role, expected_members=51, observed_members=51),
        )
        assert decision.valid is False
        assert "REPLACEMENT_SOURCE_RUN_NON_ENSEMBLE_HAS_MEMBERS" in decision.reason_codes


def test_source_run_identity_requires_exact_expected_members_for_ensemble_products() -> None:
    decision = validate_replacement_source_run_identity(
        role="baseline_b0",
        temperature_metric="high",
        source_run=_source_run("baseline_b0", expected_members=50, observed_members=50),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_EXPECTED_MEMBERS_MISMATCH" in decision.reason_codes


def test_source_run_identity_blocks_coverage_mismatch() -> None:
    decision = validate_replacement_source_run_identity(
        role="openmeteo_ifs9_anchor",
        temperature_metric="high",
        source_run=_source_run("openmeteo_ifs9_anchor"),
        coverage=_coverage(
            "openmeteo_ifs9_anchor",
            source_run_id="different-run",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_low",
            physical_quantity="wrong_quantity",
        ),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_COVERAGE_ID_MISMATCH" in decision.reason_codes
    assert "REPLACEMENT_SOURCE_RUN_COVERAGE_DATA_VERSION_MISMATCH" in decision.reason_codes
    assert "REPLACEMENT_SOURCE_RUN_COVERAGE_PHYSICAL_QUANTITY_MISMATCH" in decision.reason_codes


def test_source_run_identity_rejects_unknown_metric_or_role() -> None:
    with pytest.raises(ValueError, match="temperature_metric"):
        expected_replacement_dependency_identity_by_role("mean")

    with pytest.raises(ValueError, match="unsupported replacement dependency role"):
        validate_replacement_source_run_identity(
            role="unknown",
            temperature_metric="high",
            source_run={},
        )
