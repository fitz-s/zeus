# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Prove replacement forecast EMOS refit uses product-keyed identity, not live B0 EMOS cells.
# Reuse: Run before changing replacement forecast refit, EMOS keying, or calibration block.
# Authority basis: Operator-directed replacement forecast worktree integration; EMOS refit must be product-isolated.
"""Replacement forecast EMOS identity tests."""

from __future__ import annotations

import pytest

from src.data.replacement_forecast_emos_identity import (
    LEGACY_EMOS_KEY_SCHEMA,
    READY_STATUS,
    REPLACEMENT_EMOS_KEY_SCHEMA,
    ReplacementForecastEmosIdentityEvidence,
    evaluate_replacement_forecast_emos_identity,
    replacement_emos_cell_key,
)


def _key() -> str:
    return replacement_emos_cell_key(
        city="Shanghai",
        season="JJA",
        metric="high",
        source_family="derived_posterior",
        source_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        data_version="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
    )


def _evidence(**overrides) -> ReplacementForecastEmosIdentityEvidence:
    params = {
        "cell_key": _key(),
        "key_schema": REPLACEMENT_EMOS_KEY_SCHEMA,
        "city": "Shanghai",
        "season": "JJA",
        "metric": "high",
        "source_family": "derived_posterior",
        "source_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        "product_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        "data_version": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
        "calibration_method": "soft_anchor_product_specific_nested_refit",
    }
    params.update(overrides)
    return ReplacementForecastEmosIdentityEvidence(**params)


def test_replacement_emos_product_key_is_full_lineage() -> None:
    key = _key()

    assert key == (
        "Shanghai|JJA|high|derived_posterior|"
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor|"
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1|"
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1"
    )


def test_product_keyed_replacement_emos_identity_is_ready() -> None:
    decision = evaluate_replacement_forecast_emos_identity(_evidence())

    assert decision.ready is True
    assert decision.status == READY_STATUS
    assert decision.product_keyed is True
    assert decision.as_refit_fields() == {
        "emos_key_includes_product": True,
        "emos_key_schema": REPLACEMENT_EMOS_KEY_SCHEMA,
        "emos_identity_evidence_status": READY_STATUS,
    }


def test_legacy_live_emos_key_is_not_replacement_refit_identity() -> None:
    decision = evaluate_replacement_forecast_emos_identity(
        _evidence(
            cell_key="Shanghai|JJA|high",
            key_schema=LEGACY_EMOS_KEY_SCHEMA,
        )
    )

    assert decision.ready is False
    assert "REPLACEMENT_EMOS_KEY_SCHEMA_NOT_PRODUCT_KEYED" in decision.reason_codes
    assert "REPLACEMENT_EMOS_CELL_KEY_DOES_NOT_MATCH_PRODUCT_DOMAIN" in decision.reason_codes
    assert "REPLACEMENT_EMOS_CELL_KEY_MISSING_PRODUCT_PARTS" in decision.reason_codes


def test_baseline_emos_methods_are_not_replacement_identity() -> None:
    decision = evaluate_replacement_forecast_emos_identity(_evidence(calibration_method="emos"))

    assert decision.ready is False
    assert "REPLACEMENT_EMOS_BASELINE_METHOD_FORBIDDEN" in decision.reason_codes


def test_replacement_emos_identity_rejects_short_alias() -> None:
    with pytest.raises(ValueError, match="full replacement identity"):
        _evidence(product_id="short_" + "h" + "3_alias")
