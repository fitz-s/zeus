# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast EMOS/data-refit promotion from baseline calibration reuse.
# Reuse: Run before any product-specific replacement forecast refit or EMOS key migration.
# Authority basis: Operator-directed replacement forecast worktree integration; refit requires heldout official evidence.
"""Replacement forecast refit gate tests."""

from __future__ import annotations

import pytest

from src.data.replacement_forecast_refit_gate import (
    REQUIRED_REFIT_EVIDENCE,
    ReplacementForecastRefitEvidence,
    evaluate_replacement_forecast_refit_gate,
)
from src.data.replacement_forecast_emos_identity import READY_STATUS, REPLACEMENT_EMOS_KEY_SCHEMA


def _evidence(**overrides) -> ReplacementForecastRefitEvidence:
    params = {
        "official_days": 5,
        "official_rows": 250,
        "temperature_metric": "high",
        "source_family": "derived_posterior",
        "product_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        "calibration_method": "soft_anchor_product_specific_nested_refit",
        "enabled_evidence": tuple(REQUIRED_REFIT_EVIDENCE),
        "min_guardrail_bucket_rows": 20,
        "high_low_mixed": False,
        "baseline_calibration_reused": False,
        "emos_key_includes_product": True,
        "emos_key_schema": REPLACEMENT_EMOS_KEY_SCHEMA,
        "emos_identity_evidence_status": READY_STATUS,
        "data_refit_requested": True,
        "live_promotion_requested": False,
    }
    params.update(overrides)
    return ReplacementForecastRefitEvidence(**params)


def test_refit_gate_allows_product_specific_training_only_after_full_evidence() -> None:
    decision = evaluate_replacement_forecast_refit_gate(_evidence())

    assert decision.status == "PRODUCT_SPECIFIC_REFIT_READY"
    assert decision.product_specific_training_allowed is True
    assert decision.emos_replacement_ready is True
    assert decision.live_promotion_allowed is False
    assert decision.reason_codes == ("REPLACEMENT_REFIT_PRODUCT_SPECIFIC_EVIDENCE_READY",)
    assert decision.data_refit_required is True


def test_refit_gate_blocks_single_day_small_sample_and_bucket_sparse_evidence() -> None:
    decision = evaluate_replacement_forecast_refit_gate(
        _evidence(official_days=1, official_rows=57, min_guardrail_bucket_rows=5)
    )

    assert decision.status == "BLOCKED"
    assert decision.product_specific_training_allowed is False
    assert "REPLACEMENT_REFIT_INSUFFICIENT_OFFICIAL_DAYS" in decision.reason_codes
    assert "REPLACEMENT_REFIT_INSUFFICIENT_OFFICIAL_ROWS" in decision.reason_codes
    assert "REPLACEMENT_REFIT_GUARDRAIL_BUCKET_INSUFFICIENT_ROWS" in decision.reason_codes


def test_refit_gate_blocks_baseline_emos_platt_and_high_low_mixing() -> None:
    decision = evaluate_replacement_forecast_refit_gate(
        _evidence(
            calibration_method="emos",
            baseline_calibration_reused=True,
            high_low_mixed=True,
        )
    )

    assert decision.status == "BLOCKED"
    assert "REPLACEMENT_REFIT_HIGH_LOW_MIXING_BLOCKED" in decision.reason_codes
    assert "REPLACEMENT_REFIT_BASELINE_CALIBRATION_REUSED" in decision.reason_codes
    assert "REPLACEMENT_REFIT_BASELINE_METHOD_FORBIDDEN" in decision.reason_codes


def test_refit_gate_requires_product_keyed_emos_before_data_refit() -> None:
    decision = evaluate_replacement_forecast_refit_gate(_evidence(emos_key_includes_product=False))

    assert decision.status == "BLOCKED"
    assert "REPLACEMENT_REFIT_EMOS_KEY_MUST_INCLUDE_PRODUCT" in decision.reason_codes
    assert decision.emos_replacement_ready is False


def test_refit_gate_requires_ready_emos_identity_evidence_before_data_refit() -> None:
    decision = evaluate_replacement_forecast_refit_gate(
        _evidence(
            emos_key_schema="legacy_city_season_metric",
            emos_identity_evidence_status="BLOCKED",
        )
    )

    assert decision.status == "BLOCKED"
    assert "REPLACEMENT_REFIT_EMOS_KEY_SCHEMA_NOT_PRODUCT_KEYED" in decision.reason_codes
    assert "REPLACEMENT_REFIT_EMOS_IDENTITY_EVIDENCE_NOT_READY" in decision.reason_codes
    assert decision.emos_replacement_ready is False


def test_refit_gate_reports_missing_required_evidence_and_rejects_short_alias() -> None:
    decision = evaluate_replacement_forecast_refit_gate(
        _evidence(enabled_evidence=("official_verified_truth_only",))
    )

    assert "REPLACEMENT_REFIT_MISSING_REQUIRED_EVIDENCE" in decision.reason_codes
    assert "same_clob_after_cost_replay_positive" in decision.missing_evidence

    with pytest.raises(ValueError, match="full replacement identity"):
        _evidence(product_id="short_" + "h" + "3_alias")
