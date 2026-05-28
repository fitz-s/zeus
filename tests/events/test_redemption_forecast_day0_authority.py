# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R2/R3 proof.

import pytest

from src.contracts.settlement_semantics import SettlementSemantics
from src.events.day0_authority import (
    Day0AuthorityError,
    Day0AuthorityEvidence,
    assert_live_day0_authority,
    observability_row_to_authority,
)
from src.events.forecast_completeness import ForecastSnapshotEvidence, classify_forecast_snapshot


def _forecast(**overrides):
    values = dict(
        cycle_hour=0,
        target_step=6,
        expected_steps=(0, 3, 6),
        observed_steps=(0, 3, 6),
        observed_members=51,
        expected_members=51,
        min_members_floor=40,
        source_available_at="2026-05-24T10:00:00+00:00",
        issue_time="2026-05-24T00:00:00+00:00",
        executable_reader_live_eligible=True,
    )
    values.update(overrides)
    return ForecastSnapshotEvidence(**values)


def _semantics() -> SettlementSemantics:
    return SettlementSemantics(
        resolution_source="WU_KMDW",
        measurement_unit="F",
        precision=1.0,
        rounding_rule="wmo_half_up",
        finalization_time="12:00:00Z",
    )


def _day0(**overrides):
    values = dict(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_match_status="MATCH",
        station_match_status="MATCH",
        local_date_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="LIVE_AUTHORITY",
        observation_available_at="2026-05-24T08:05:00+00:00",
        observation_time="2026-05-24T08:00:00+00:00",
        raw_value=80.2,
        rounded_value=80,
        settlement_semantics=_semantics(),
    )
    values.update(overrides)
    return Day0AuthorityEvidence(**values)


def test_expected_steps_unknown_blocks():
    result = classify_forecast_snapshot(_forecast(cycle_hour=99, expected_steps=()))

    assert result.status == "PARTIAL_BLOCKED"
    assert result.live_eligible is False


def test_issue_time_not_availability():
    result = classify_forecast_snapshot(
        _forecast(source_available_at="2026-05-24T00:00:00+00:00", issue_time="2026-05-24T00:00:00+00:00")
    )

    assert result.reason == "issue_time_cannot_authorize_live"
    assert result.live_eligible is False


def test_partial_allowed_no_live_submit():
    result = classify_forecast_snapshot(_forecast(observed_members=45, expected_members=51))

    assert result.status == "PARTIAL_ALLOWED"
    assert result.live_eligible is False


def test_live_day0_authority_passes_with_settlement_semantics():
    assert_live_day0_authority(_day0(raw_value=80.2, rounded_value=80))


def test_observability_table_row_is_not_live_authority():
    with pytest.raises(Day0AuthorityError, match="not live authority"):
        observability_row_to_authority({"city": "Chicago", "live_authority_status": "OBSERVABILITY_ONLY"})


def test_station_mismatch_blocks():
    with pytest.raises(Day0AuthorityError, match="station_match_status"):
        assert_live_day0_authority(_day0(station_match_status="MISMATCH"))


def test_dst_ambiguous_blocks():
    with pytest.raises(Day0AuthorityError, match="dst_status"):
        assert_live_day0_authority(_day0(dst_status="AMBIGUOUS"))


def test_settlement_semantics_only():
    with pytest.raises(Day0AuthorityError, match="SettlementSemantics"):
        assert_live_day0_authority(_day0(raw_value=80.6, rounded_value=80))
