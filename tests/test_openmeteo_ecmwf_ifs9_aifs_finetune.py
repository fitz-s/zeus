# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement soft-anchor fine-tune from single-day/post-selection leakage.
# Reuse: Run before using Open-Meteo ECMWF IFS 9km + AIFS sampled-2t parameters as promotion evidence.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement soft-anchor nested fine-tune tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.strategy.openmeteo_ecmwf_ifs9_aifs_finetune import (
    SoftAnchorFineTuneRow,
    SoftAnchorParameter,
    evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune,
    predeclared_soft_anchor_parameter_grid,
)


PARAM_A = SoftAnchorParameter(anchor_weight=0.80, anchor_sigma_c=3.0)
PARAM_B = SoftAnchorParameter(anchor_weight=0.60, anchor_sigma_c=4.0)
GRID = (PARAM_A, PARAM_B)


def _prob(win_param: SoftAnchorParameter, settled: str = "warm") -> dict[SoftAnchorParameter, dict[str, float]]:
    strong = {settled: 0.80, "miss": 0.20}
    weak = {settled: 0.40, "miss": 0.60}
    return {PARAM_A: strong if win_param == PARAM_A else weak, PARAM_B: strong if win_param == PARAM_B else weak}


def _row(
    day: date,
    *,
    win_param: SoftAnchorParameter,
    truth_authority: str = "VERIFIED",
    city: str = "Shanghai",
    metric: str = "high",
    guardrail_bucket: str = "standard",
) -> SoftAnchorFineTuneRow:
    return SoftAnchorFineTuneRow(
        official_date=day,
        city=city,
        temperature_metric=metric,
        bin_id="warm",
        truth_authority=truth_authority,
        probabilities_by_parameter=_prob(win_param),
        settled_bin_id="warm",
        guardrail_bucket=guardrail_bucket,
    )


def test_predeclared_soft_anchor_grid_is_explicit_and_bounded() -> None:
    grid = predeclared_soft_anchor_parameter_grid(weights=(0.70, 0.80), sigmas_c=(3.0, 4.0))

    assert grid == (
        SoftAnchorParameter(0.70, 3.0),
        SoftAnchorParameter(0.70, 4.0),
        SoftAnchorParameter(0.80, 3.0),
        SoftAnchorParameter(0.80, 4.0),
    )
    with pytest.raises(ValueError, match="non-empty"):
        predeclared_soft_anchor_parameter_grid(weights=(), sigmas_c=(3.0,))


def test_finetune_blocks_unverified_truth_rows() -> None:
    result = evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(
        [_row(date(2026, 6, 1), win_param=PARAM_A, truth_authority="UNVERIFIED")],
        candidate_grid=GRID,
    )

    assert result.status == "BLOCKED"
    assert result.reason_codes == ("REPLACEMENT_FINETUNE_REQUIRES_VERIFIED_TRUTH",)
    assert result.folds == ()


def test_finetune_requires_every_row_to_cover_predeclared_grid() -> None:
    row = SoftAnchorFineTuneRow(
        official_date=date(2026, 6, 1),
        city="Shanghai",
        temperature_metric="high",
        bin_id="warm",
        truth_authority="VERIFIED",
        probabilities_by_parameter={PARAM_A: {"warm": 0.8, "miss": 0.2}},
        settled_bin_id="warm",
    )

    with pytest.raises(ValueError, match="predeclared candidate_grid"):
        evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune([row], candidate_grid=GRID)


def test_leave_day_out_selection_uses_training_days_not_holdout_day() -> None:
    rows = [
        _row(date(2026, 6, 1), win_param=PARAM_A),
        _row(date(2026, 6, 2), win_param=PARAM_A),
        _row(date(2026, 6, 3), win_param=PARAM_B),
    ]

    result = evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(
        rows,
        candidate_grid=GRID,
        min_official_days=3,
        min_official_rows=3,
        min_rows_per_guardrail_bucket=1,
    )

    assert result.status == "PROMOTION_EVIDENCE_READY"
    folds_by_day = {fold.holdout_day: fold for fold in result.folds}
    assert folds_by_day[date(2026, 6, 3)].selected_parameter == PARAM_A
    assert folds_by_day[date(2026, 6, 3)].holdout_log_loss == pytest.approx(-__import__("math").log(0.40))
    assert folds_by_day[date(2026, 6, 3)].train_row_count == 2
    assert result.selected_parameter == PARAM_A
    assert result.mean_holdout_log_loss is not None


def test_single_day_and_small_sample_finetune_stays_shadow_only() -> None:
    result = evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(
        [_row(date(2026, 6, 4), win_param=PARAM_A)],
        candidate_grid=GRID,
    )

    assert result.status == "SHADOW_EVIDENCE_ONLY"
    assert "REPLACEMENT_FINETUNE_INSUFFICIENT_OFFICIAL_DAYS" in result.reason_codes
    assert "REPLACEMENT_FINETUNE_INSUFFICIENT_OFFICIAL_ROWS" in result.reason_codes
    assert "REPLACEMENT_FINETUNE_INCOMPLETE_FOLDS" in result.reason_codes
    assert "REPLACEMENT_FINETUNE_GUARDRAIL_BUCKET_COVERAGE_INSUFFICIENT" in result.reason_codes
    assert result.promotion_ready is False


def test_finetune_blocks_high_low_metric_mixing_in_parameter_selection() -> None:
    rows = [
        _row(date(2026, 6, 1), win_param=PARAM_A, metric="high"),
        _row(date(2026, 6, 2), win_param=PARAM_A, metric="low"),
    ]

    result = evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(
        rows,
        candidate_grid=GRID,
        min_official_days=1,
        min_official_rows=1,
        min_rows_per_guardrail_bucket=1,
    )

    assert result.status == "BLOCKED"
    assert "REPLACEMENT_FINETUNE_HIGH_LOW_METRIC_MIXING_BLOCKED" in result.reason_codes
    assert result.promotion_ready is False


def test_finetune_requires_guardrail_bucket_row_coverage_before_promotion_ready() -> None:
    start = date(2026, 6, 1)
    rows = []
    for offset in range(5):
        for city_idx in range(50):
            rows.append(
                _row(
                    start + timedelta(days=offset),
                    win_param=PARAM_A,
                    city=f"City{city_idx}",
                    guardrail_bucket="coastal" if city_idx == 0 else "standard",
                )
            )

    result = evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(
        rows,
        candidate_grid=GRID,
        min_rows_per_guardrail_bucket=20,
    )

    assert result.status == "SHADOW_EVIDENCE_ONLY"
    assert "REPLACEMENT_FINETUNE_GUARDRAIL_BUCKET_COVERAGE_INSUFFICIENT" in result.reason_codes
    coverage = {bucket.guardrail_bucket: bucket for bucket in result.guardrail_bucket_coverage}
    assert coverage["coastal"].row_count == 5
    assert coverage["coastal"].status == "SHADOW_ONLY"
    assert coverage["standard"].status == "PASS"
    assert result.promotion_ready is False


def test_finetune_promotion_gate_requires_enough_official_days_and_rows() -> None:
    start = date(2026, 6, 1)
    rows = []
    for offset in range(5):
        for city_idx in range(50):
            rows.append(_row(start + timedelta(days=offset), win_param=PARAM_A, city=f"City{city_idx}"))

    result = evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(rows, candidate_grid=GRID)

    assert result.status == "PROMOTION_EVIDENCE_READY"
    assert result.promotion_ready is True
    assert result.official_days == 5
    assert result.official_rows == 250
    assert len(result.folds) == 5
    assert result.guardrail_bucket_coverage[0].status == "PASS"
    assert result.reason_codes == ("REPLACEMENT_FINETUNE_NESTED_WALK_FORWARD_READY",)
    assert result.selected_parameter == PARAM_A
