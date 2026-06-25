# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast evidence from row-IID significance overclaims.
# Reuse: Run before treating replacement blocked-candidate replay deltas as statistically meaningful.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t blocked integration.
"""Replacement forecast block bootstrap diagnostics tests."""

from __future__ import annotations

from src.data.replacement_forecast_block_bootstrap import (
    ReplacementForecastBlockBootstrapRow,
    run_replacement_forecast_block_bootstrap,
)


def _row(
    *,
    city: str = "Shanghai",
    date: str = "2026-06-01",
    metric: str = "high",
    bucket: str = "standard",
    delta: float = 1.0,
    replay_status: str = "SCORED",
    truth_status: str = "VERIFIED",
) -> ReplacementForecastBlockBootstrapRow:
    return ReplacementForecastBlockBootstrapRow(
        city=city,
        target_date=date,
        temperature_metric=metric,  # type: ignore[arg-type]
        guardrail_bucket=bucket,
        replay_status=replay_status,
        truth_status=truth_status,
        replacement_delta_after_cost_pnl=delta,
    )


def test_block_bootstrap_keeps_correlated_rows_in_same_block() -> None:
    rows = [
        _row(city="Shanghai", date="2026-06-01", bucket="coastal", delta=10.0),
        _row(city="Shanghai", date="2026-06-01", bucket="coastal", delta=-8.0),
        _row(city="Tokyo", date="2026-06-01", bucket="coastal", delta=4.0),
        _row(city="Dallas", date="2026-06-02", bucket="inland", delta=3.0),
        _row(city="Madrid", date="2026-06-03", bucket="inland", delta=2.0),
    ]

    report = run_replacement_forecast_block_bootstrap(rows, iterations=200, seed=7, min_blocks=4)

    assert report.status in {"DIAGNOSTIC_PASS", "BLOCKED"}
    assert report.block_axes == ("target_date", "city", "temperature_metric", "guardrail_bucket")
    assert report.block_count == 4
    assert report.scored_rows == 5
    assert report.observed_total_delta_after_cost_pnl == 11.0
    assert report.observed_mean_delta_after_cost_pnl == 2.2
    assert report.iterations == 200
    assert len(report.sampled_block_mean_deltas) == 200
    assert report.promotion_allowed is False


def test_block_bootstrap_excludes_non_verified_or_non_scored_rows() -> None:
    rows = [
        _row(city="Shanghai", date="2026-06-01", delta=3.0),
        _row(city="Tokyo", date="2026-06-02", delta=2.0),
        _row(city="Dallas", date="2026-06-03", delta=1.0),
        _row(city="Madrid", date="2026-06-04", delta=4.0),
        _row(city="Busan", date="2026-06-05", delta=99.0, truth_status="PROVISIONAL"),
        _row(city="Seoul", date="2026-06-06", delta=99.0, replay_status="BLOCKED"),
    ]

    report = run_replacement_forecast_block_bootstrap(rows, iterations=100, seed=11, min_blocks=4)

    assert report.status == "BLOCKED"
    assert report.total_rows == 6
    assert report.scored_rows == 4
    assert report.excluded_rows == 2
    assert report.observed_total_delta_after_cost_pnl == 10.0
    assert "REPLACEMENT_BLOCK_BOOTSTRAP_HAS_EXCLUDED_ROWS" in report.reason_codes
    assert report.excluded_reason_counts == {
        "REPLACEMENT_BLOCK_BOOTSTRAP_EXCLUDED_NON_VERIFIED_TRUTH": 1,
        "REPLACEMENT_BLOCK_BOOTSTRAP_EXCLUDED_NON_SCORED_REPLAY": 1,
    }


def test_block_bootstrap_blocks_when_verified_scored_blocks_are_too_thin() -> None:
    report = run_replacement_forecast_block_bootstrap(
        [
            _row(city="Shanghai", date="2026-06-01", delta=2.0),
            _row(city="Shanghai", date="2026-06-01", delta=1.0),
            _row(city="Tokyo", date="2026-06-01", delta=3.0),
        ],
        iterations=100,
        min_blocks=3,
    )

    assert report.status == "BLOCKED"
    assert report.block_count == 2
    assert report.iterations == 0
    assert report.sampled_block_mean_deltas == ()
    assert "REPLACEMENT_BLOCK_BOOTSTRAP_INSUFFICIENT_BLOCKS" in report.reason_codes
    assert report.promotion_allowed is False


def test_block_bootstrap_blocks_when_no_rows_are_scorable() -> None:
    report = run_replacement_forecast_block_bootstrap([
        _row(city="Shanghai", truth_status="MISSING", replay_status="BLOCKED"),
    ])

    assert report.status == "BLOCKED"
    assert report.scored_rows == 0
    assert report.excluded_rows == 1
    assert "REPLACEMENT_BLOCK_BOOTSTRAP_NO_SCORED_VERIFIED_ROWS" in report.reason_codes


def test_block_bootstrap_as_dict_is_json_ready_and_never_promotion_authority() -> None:
    rows = [
        _row(city="Shanghai", date="2026-06-01", delta=6.0),
        _row(city="Tokyo", date="2026-06-02", delta=5.0),
        _row(city="Dallas", date="2026-06-03", delta=4.0),
        _row(city="Madrid", date="2026-06-04", delta=3.0),
        _row(city="Seoul", date="2026-06-05", delta=2.0),
    ]

    report = run_replacement_forecast_block_bootstrap(rows, iterations=50, seed=13, min_blocks=5)
    payload = report.as_dict()

    assert payload["promotion_allowed"] is False
    assert payload["block_count"] == 5
    assert payload["iterations"] == 50
    assert isinstance(payload["reason_codes"], list)
    assert "sampled_block_mean_deltas" not in payload
