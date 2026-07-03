# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast guardrail bucket reporting from hiding regression clusters.
# Reuse: Run before using replacement guardrail reports for promotion or blocked-candidate daily summaries.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t blocked integration.
"""Replacement forecast guardrail bucket report tests."""

from __future__ import annotations

from src.data.replacement_forecast_guardrail_report import (
    ReplacementForecastGuardrailReplayRow,
    build_replacement_forecast_guardrail_report,
)


def _row(
    *,
    city: str = "Shanghai",
    metric: str = "high",
    bucket: str = "standard",
    status: str = "SCORED",
    delta: float = 1.0,
    veto: bool = True,
) -> ReplacementForecastGuardrailReplayRow:
    return ReplacementForecastGuardrailReplayRow(
        city=city,
        temperature_metric=metric,  # type: ignore[arg-type]
        guardrail_bucket=bucket,
        replay_status=status,
        replacement_delta_after_cost_pnl=delta,
        veto_applied=veto,
        baseline_after_cost_pnl=-delta if veto and delta > 0 else abs(delta),
        replacement_after_cost_pnl=0.0,
        reason_codes=("REPLACEMENT_REPLAY_SCORED_AFTER_COST_SAME_CLOB",) if status == "SCORED" else ("REPLAY_BLOCKED",),
    )


def test_guardrail_report_blocks_promotion_when_regression_cluster_exists_despite_positive_global_delta() -> None:
    rows = [
        _row(city="Shanghai", bucket="standard", delta=20.0),
        _row(city="Tokyo", bucket="standard", delta=15.0),
        _row(city="New York", bucket="coastal", delta=-5.0),
        _row(city="New York", bucket="coastal", delta=-4.0),
    ]

    report = build_replacement_forecast_guardrail_report(rows, min_scored_rows_per_bucket=2)

    assert report.status == "BLOCKED"
    assert report.net_delta_after_cost_pnl == 26.0
    assert report.veto_avoided_loss_pnl == 35.0
    assert report.veto_regret_pnl == 9.0
    assert "REPLACEMENT_GUARDRAIL_UNRESOLVED_REGRESSION_CLUSTERS" in report.reason_codes
    regression_values = {(bucket.axis, bucket.value) for bucket in report.unresolved_regression_clusters}
    assert ("guardrail_bucket", "coastal") in regression_values
    assert ("city", "New York") in regression_values
    assert report.promotion_allowed is False


def test_guardrail_report_passes_when_every_bucket_has_positive_scored_delta() -> None:
    rows = [
        _row(city="Shanghai", metric="high", bucket="standard", delta=4.0),
        _row(city="Tokyo", metric="high", bucket="standard", delta=3.0),
        _row(city="Madrid", metric="low", bucket="inland", delta=2.0),
        _row(city="Dallas", metric="low", bucket="inland", delta=1.0),
    ]

    report = build_replacement_forecast_guardrail_report(rows, axes=("guardrail_bucket", "temperature_metric"), min_scored_rows_per_bucket=2)

    assert report.status == "PASS"
    assert report.reason_codes == ("REPLACEMENT_GUARDRAIL_REPORT_PASS",)
    assert report.promotion_allowed is False
    assert report.unresolved_regression_clusters == ()
    assert all(bucket.status == "PASS" for bucket in report.buckets)


def test_guardrail_report_preserves_blocked_replay_rows_as_blocked_only() -> None:
    report = build_replacement_forecast_guardrail_report(
        [
            _row(city="Shanghai", bucket="standard", delta=3.0),
            _row(city="Tokyo", bucket="standard", delta=2.0),
            _row(city="Busan", bucket="coastal", status="BLOCKED", delta=0.0),
        ],
        axes=("guardrail_bucket",),
        min_scored_rows_per_bucket=1,
    )

    assert report.status == "BLOCKED"
    assert report.blocked_rows == 1
    assert "REPLACEMENT_GUARDRAIL_REPORT_HAS_BLOCKED_ROWS" in report.reason_codes
    coastal = next(bucket for bucket in report.buckets if bucket.value == "coastal")
    assert coastal.blocked_rows == 1
    assert coastal.status == "BLOCKED"


def test_guardrail_report_blocks_when_no_scored_rows_exist() -> None:
    report = build_replacement_forecast_guardrail_report([
        _row(city="Shanghai", bucket="standard", status="BLOCKED", delta=0.0),
    ])

    assert report.status == "BLOCKED"
    assert "REPLACEMENT_GUARDRAIL_REPORT_NO_SCORED_ROWS" in report.reason_codes
    assert report.promotion_allowed is False


def test_guardrail_report_as_dict_is_json_ready() -> None:
    report = build_replacement_forecast_guardrail_report([
        _row(city="Shanghai", bucket="standard", delta=2.0),
        _row(city="Tokyo", bucket="standard", delta=1.0),
    ], min_scored_rows_per_bucket=1)

    payload = report.as_dict()
    assert payload["status"] == "PASS"
    assert payload["net_delta_after_cost_pnl"] == 3.0
    assert payload["promotion_allowed"] is False
    assert isinstance(payload["buckets"], list)
    assert payload["unresolved_regression_clusters"] == []
