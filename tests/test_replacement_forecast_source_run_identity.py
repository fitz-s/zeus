# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement source_run/source_run_coverage identity from cross-product lineage drift.
# Reuse: Run before writing or reading replacement source_run dependencies, readiness rows, or replay provenance.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement source-run identity validator tests."""

from __future__ import annotations

import pytest

from src.data.replacement_forecast_source_run_identity import (
    expected_replacement_dependency_identity_by_role,
    validate_replacement_source_run_identity,
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

    assert high["aifs_sampled_2t"].product_id == "ecmwf_aifs_ens_sampled_2t_6h_v1"
    assert high["aifs_sampled_2t"].data_version.endswith("_max")
    assert low["aifs_sampled_2t"].data_version.endswith("_min")
    assert high["aifs_sampled_2t"].raw_ensemble_eligible is True
    assert high["openmeteo_ifs9_anchor"].raw_ensemble_eligible is False
    assert high["soft_anchor_posterior"].raw_ensemble_eligible is False
    assert high["soft_anchor_posterior"].source_id == "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"


def test_source_run_identity_validates_source_run_and_coverage_pair() -> None:
    for role in ("baseline_b0", "aifs_sampled_2t", "openmeteo_ifs9_anchor", "soft_anchor_posterior"):
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
        role="aifs_sampled_2t",
        temperature_metric="high",
        source_run=_source_run(
            "aifs_sampled_2t",
            dataset_id="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_min",
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
        role="aifs_sampled_2t",
        temperature_metric="high",
        source_run=_source_run("aifs_sampled_2t", expected_members=50, observed_members=50),
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
