# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §6 event model acceptance A03-A07.
from __future__ import annotations

import pytest

from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    OpportunityEventValidationError,
    assert_available_for_decision,
    assert_live_forecast_has_causal_snapshot,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)


def _forecast_payload(member_count: int = 51) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-123",
        cycle="00",
        track="ens",
        snapshot_id="snap-123",
        snapshot_hash="abc123",
        captured_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=member_count,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )


def test_payload_hash_deterministic():
    payload_a = _forecast_payload()
    payload_b = _forecast_payload()
    event_a = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="snap-123",
        payload=payload_a,
    )
    event_b = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="snap-123",
        payload=payload_b,
    )
    assert event_a.payload_hash == event_b.payload_hash
    assert event_a.event_id == event_b.event_id


def test_idempotency_key_changes_when_payload_changes():
    event_a = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="snap-123",
        payload=_forecast_payload(member_count=51),
    )
    event_b = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="snap-123",
        payload=_forecast_payload(member_count=50),
    )
    assert event_a.payload_hash != event_b.payload_hash
    assert event_a.idempotency_key != event_b.idempotency_key


def test_observed_available_received_do_not_alias():
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="snap-123",
        payload=_forecast_payload(),
    )
    assert event.observed_at == "2026-05-24T04:10:00+00:00"
    assert event.available_at == "2026-05-24T04:15:00+00:00"
    assert event.received_at == "2026-05-24T04:16:00+00:00"


def test_available_at_future_rejected():
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="snap-123",
        payload=_forecast_payload(),
    )
    with pytest.raises(OpportunityEventValidationError, match="available_at"):
        assert_available_for_decision(event, "2026-05-24T04:14:59+00:00")


def test_received_at_future_rejected():
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:20:00+00:00",
        causal_snapshot_id="snap-123",
        payload=_forecast_payload(),
    )
    with pytest.raises(OpportunityEventValidationError, match="received_at"):
        assert_available_for_decision(event, "2026-05-24T04:19:59+00:00")


def test_causal_snapshot_id_required_for_live_forecast_decision():
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id=None,
        payload=_forecast_payload(),
    )
    with pytest.raises(OpportunityEventValidationError, match="causal_snapshot_id"):
        assert_live_forecast_has_causal_snapshot(event)


def test_day0_available_at_uses_observation_available_at_not_observation_time():
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        settlement_source="WU",
        station_id="KMDW",
        observation_time="2026-05-24T18:00:00+00:00",
        observation_available_at="2026-05-24T18:07:00+00:00",
        raw_value=74.2,
        rounded_value=74,
    )
    event = make_day0_extreme_updated_event(
        entity_key="Chicago|2026-05-24|high",
        source="day0_observation",
        observed_at=payload.observation_time,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
    )
    assert event.observed_at == payload.observation_time
    assert event.available_at == payload.observation_available_at
