# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast shadow report truth-gap and row-exclusion accounting.
# Reuse: Run before using replacement shadow reports for daily monitoring, truth-gap review, or promotion evidence.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast shadow report tests."""

from __future__ import annotations

import pytest

from src.data.replacement_forecast_shadow_report import (
    ReplacementForecastExpectedRow,
    ReplacementForecastShadowReportRow,
    build_replacement_forecast_shadow_report,
)


def _row(
    *,
    city: str = "Shanghai",
    token_id: str = "token-yes",
    truth_authority: str = "VERIFIED",
    replay_status: str = "SCORED",
    exclusion_reason: str | None = None,
    delta: float | None = 0.25,
    veto: bool = True,
    bucket: str = "standard",
) -> ReplacementForecastShadowReportRow:
    return ReplacementForecastShadowReportRow(
        city=city,
        target_date="2026-06-07",
        temperature_metric="high",
        market_snapshot_id="snap-1",
        condition_id="cond-1",
        token_id=token_id,
        baseline_direction="buy_yes:warm",
        replacement_direction="buy_yes:warm",
        veto_applied=veto,
        truth_authority=truth_authority,  # type: ignore[arg-type]
        guardrail_bucket=bucket,
        replay_status=replay_status,
        replacement_delta_after_cost_pnl=delta,
        exclusion_reason=exclusion_reason,
    )


def _expected(city: str, token_id: str) -> ReplacementForecastExpectedRow:
    return ReplacementForecastExpectedRow(
        city=city,
        target_date="2026-06-07",
        temperature_metric="high",
        condition_id="cond-1",
        token_id=token_id,
    )


def test_shadow_report_complete_when_all_rows_are_verified_and_scored() -> None:
    report = build_replacement_forecast_shadow_report([
        _row(city="Shanghai", token_id="token-1", delta=0.40, bucket="coastal"),
        _row(city="Tokyo", token_id="token-2", delta=-0.10, veto=False, bucket="coastal"),
    ])
    payload = report.as_dict()

    assert payload["status"] == "SHADOW_REPORT_COMPLETE"
    assert payload["reason_codes"] == ["REPLACEMENT_SHADOW_REPORT_OFFICIAL_ROWS_COMPLETE"]
    assert payload["official_scored_rows"] == 2
    assert payload["expected_rows"] == 2
    assert payload["absent_expected_rows"] == 0
    assert payload["provisional_rows"] == 0
    assert payload["missing_rows"] == 0
    assert payload["quarantined_rows"] == 0
    assert payload["blocked_replay_rows"] == 0
    assert payload["veto_count"] == 1
    assert payload["net_official_after_cost_delta"] == pytest.approx(0.30)
    assert payload["guardrail_bucket_counts"] == {"coastal": 2}
    assert payload["row_exclusions"] == []
    assert payload["promotion_allowed"] is False


def test_shadow_report_preserves_provisional_missing_and_quarantined_exclusions() -> None:
    report = build_replacement_forecast_shadow_report([
        _row(city="Shanghai", token_id="token-1", truth_authority="VERIFIED", delta=0.25),
        _row(city="New York", token_id="token-2", truth_authority="PROVISIONAL", exclusion_reason="truth_not_official_verified", delta=None),
        _row(city="Madrid", token_id="token-3", truth_authority="MISSING", exclusion_reason="settlement_ingestion_gap", delta=None),
        _row(city="Busan", token_id="token-4", truth_authority="QUARANTINED", exclusion_reason="truth_quarantined", delta=None),
    ])
    payload = report.as_dict()

    assert payload["status"] == "SHADOW_ONLY"
    assert payload["official_scored_rows"] == 1
    assert payload["provisional_rows"] == 1
    assert payload["missing_rows"] == 1
    assert payload["quarantined_rows"] == 1
    assert payload["reason_codes"] == [
        "REPLACEMENT_SHADOW_REPORT_HAS_PROVISIONAL_TRUTH",
        "REPLACEMENT_SHADOW_REPORT_HAS_MISSING_TRUTH",
        "REPLACEMENT_SHADOW_REPORT_HAS_QUARANTINED_TRUTH",
    ]
    assert payload["exclusion_reason_counts"] == {
        "settlement_ingestion_gap": 1,
        "truth_not_official_verified": 1,
        "truth_quarantined": 1,
    }
    exclusions = payload["row_exclusions"]
    assert len(exclusions) == 3
    assert {item["city"] for item in exclusions} == {"New York", "Madrid", "Busan"}


def test_shadow_report_adds_absent_expected_rows_to_exclusion_ledger() -> None:
    report = build_replacement_forecast_shadow_report(
        [_row(city="Shanghai", token_id="token-1", truth_authority="VERIFIED", delta=0.25)],
        expected_rows=[
            _expected("Shanghai", "token-1"),
            _expected("Madrid", "token-2"),
        ],
    )
    payload = report.as_dict()

    assert payload["status"] == "SHADOW_ONLY"
    assert payload["expected_rows"] == 2
    assert payload["total_rows"] == 1
    assert payload["absent_expected_rows"] == 1
    assert payload["missing_rows"] == 1
    assert "REPLACEMENT_SHADOW_REPORT_HAS_ABSENT_EXPECTED_ROWS" in payload["reason_codes"]
    assert payload["exclusion_reason_counts"] == {"expected_row_absent_from_shadow_report": 1}
    assert payload["row_exclusions"][0]["city"] == "Madrid"
    assert payload["row_exclusions"][0]["replay_status"] == "NOT_RUN"


def test_shadow_report_preserves_blocked_replay_rows() -> None:
    report = build_replacement_forecast_shadow_report([
        _row(city="Shanghai", token_id="token-1", truth_authority="VERIFIED", delta=0.25),
        _row(city="Tokyo", token_id="token-2", truth_authority="VERIFIED", replay_status="BLOCKED", exclusion_reason="source_available_after_decision", delta=None),
    ])
    payload = report.as_dict()

    assert payload["status"] == "SHADOW_ONLY"
    assert payload["official_scored_rows"] == 1
    assert payload["blocked_replay_rows"] == 1
    assert "REPLACEMENT_SHADOW_REPORT_HAS_BLOCKED_REPLAY_ROWS" in payload["reason_codes"]
    assert payload["exclusion_reason_counts"] == {"source_available_after_decision": 1}
    assert payload["row_exclusions"][0]["replay_status"] == "BLOCKED"


def test_shadow_report_blocks_when_no_official_scored_rows_exist() -> None:
    report = build_replacement_forecast_shadow_report([
        _row(city="Madrid", token_id="token-1", truth_authority="MISSING", exclusion_reason="settlement_ingestion_gap", delta=None),
    ])

    assert report.status == "BLOCKED"
    assert "REPLACEMENT_SHADOW_REPORT_NO_OFFICIAL_SCORED_ROWS" in report.reason_codes
    assert report.promotion_allowed is False


def test_shadow_report_rejects_empty_input_duplicate_rows_and_unexplained_exclusions() -> None:
    empty = build_replacement_forecast_shadow_report([])
    assert empty.status == "BLOCKED"
    assert empty.reason_codes == ("REPLACEMENT_SHADOW_REPORT_NO_ROWS",)

    empty_expected = build_replacement_forecast_shadow_report([], expected_rows=[_expected("Madrid", "token-missing")])
    assert empty_expected.status == "BLOCKED"
    assert "REPLACEMENT_SHADOW_REPORT_HAS_ABSENT_EXPECTED_ROWS" in empty_expected.reason_codes
    assert empty_expected.missing_rows == 1
    assert empty_expected.row_exclusions[0].exclusion_reason == "expected_row_absent_from_shadow_report"

    with pytest.raises(ValueError, match="unique"):
        build_replacement_forecast_shadow_report([_row(), _row()])
    with pytest.raises(ValueError, match="expected rows must be unique"):
        build_replacement_forecast_shadow_report([_row()], expected_rows=[_expected("Madrid", "token-1"), _expected("Madrid", "token-1")])
    with pytest.raises(ValueError, match="exclusion_reason"):
        _row(truth_authority="MISSING", exclusion_reason=None)
    with pytest.raises(ValueError, match="full replacement identity"):
        ReplacementForecastShadowReportRow(
            city="short_" + "h" + "3_alias",
            target_date="2026-06-07",
            temperature_metric="high",
            market_snapshot_id="snap-1",
            condition_id="cond-1",
            token_id="token-1",
            baseline_direction="buy_yes:warm",
            replacement_direction="buy_yes:warm",
            veto_applied=False,
            truth_authority="VERIFIED",
        )
