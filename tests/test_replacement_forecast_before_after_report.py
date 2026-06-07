# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Lock multi-day before/after replacement forecast report semantics.
# Reuse: Run before producing Open-Meteo ECMWF IFS 9km + AIFS sampled-2t before/after evidence.
# Authority basis: Operator-directed replacement forecast worktree integration; report evidence cannot promote by itself.
"""Replacement forecast before/after report tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.data.replacement_forecast_before_after_report import (
    REPORT_SCHEMA_VERSION,
    ReplacementForecastBeforeAfterRow,
    build_replacement_forecast_before_after_report,
)


def _row(day: date, *, bucket: str = "standard", repl_pnl: float = 1.0, truth: str = "VERIFIED"):
    return ReplacementForecastBeforeAfterRow(
        official_date=day.isoformat(),
        city="Shanghai",
        temperature_metric="high",
        guardrail_bucket=bucket,
        baseline_brier=0.30,
        replacement_brier=0.20,
        baseline_log_loss=0.70,
        replacement_log_loss=0.50,
        baseline_after_cost_pnl=0.0,
        replacement_after_cost_pnl=repl_pnl,
        truth_authority=truth,
        replay_status="SCORED" if truth == "VERIFIED" else "NOT_RUN",
    )


def test_before_after_report_aggregates_brier_logloss_and_after_cost_delta() -> None:
    start = date(2026, 6, 1)
    rows = [_row(start + timedelta(days=offset), repl_pnl=1.0) for offset in range(5) for _ in range(50)]

    report = build_replacement_forecast_before_after_report(rows)

    assert report.schema_version == REPORT_SCHEMA_VERSION
    assert report.status == "REPORT_READY"
    assert report.official_days == 5
    assert report.official_rows == 250
    assert report.brier_delta == pytest.approx(-0.10)
    assert report.log_loss_delta == pytest.approx(-0.20)
    assert report.after_cost_delta == pytest.approx(250.0)
    assert report.promotion_allowed is False


def test_before_after_report_blocks_single_day_or_small_official_cohort() -> None:
    report = build_replacement_forecast_before_after_report([_row(date(2026, 6, 4))])

    assert report.status == "SHADOW_REPORT_ONLY"
    assert "REPLACEMENT_BEFORE_AFTER_INSUFFICIENT_OFFICIAL_DAYS" in report.reason_codes
    assert "REPLACEMENT_BEFORE_AFTER_INSUFFICIENT_OFFICIAL_ROWS" in report.reason_codes
    assert report.promotion_allowed is False


def test_before_after_report_preserves_provisional_row_exclusion() -> None:
    rows = [_row(date(2026, 6, 1)), _row(date(2026, 6, 2), truth="PROVISIONAL")]

    report = build_replacement_forecast_before_after_report(rows, min_official_days=1, min_official_rows=1)

    assert report.status == "SHADOW_REPORT_ONLY"
    assert report.row_exclusion_count == 1
    assert "REPLACEMENT_BEFORE_AFTER_HAS_ROW_EXCLUSIONS" in report.reason_codes


def test_before_after_report_flags_negative_guardrail_bucket_regression() -> None:
    rows = [
        _row(date(2026, 6, 1), bucket="coastal", repl_pnl=-2.0),
        _row(date(2026, 6, 2), bucket="standard", repl_pnl=3.0),
    ]

    report = build_replacement_forecast_before_after_report(rows, min_official_days=1, min_official_rows=1)

    assert report.status == "SHADOW_REPORT_ONLY"
    assert report.bucket_regressions == {"coastal": -2.0}
    assert "REPLACEMENT_BEFORE_AFTER_BUCKET_REGRESSIONS_PRESENT" in report.reason_codes


def test_before_after_report_rejects_short_alias_in_system_identity_fields() -> None:
    with pytest.raises(ValueError, match="full replacement identity"):
        ReplacementForecastBeforeAfterRow(
            official_date="2026-06-01",
            city="short_" + "h" + "3_alias",
            temperature_metric="high",
            guardrail_bucket="standard",
            baseline_brier=0.3,
            replacement_brier=0.2,
            baseline_log_loss=0.7,
            replacement_log_loss=0.5,
            baseline_after_cost_pnl=0.0,
            replacement_after_cost_pnl=1.0,
        )
