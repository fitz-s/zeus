# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R8/R9 proof.

import inspect

from src.events import reactor
from src.events.decision_engine import EventBoundDecisionEngine, EventBoundDecisionRequest, EventBoundDecisionResult
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import OpportunityEventReactor, ReactorConfig


def _event():
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-25",
        metric="high",
        source_id="ecmwf_open_data",
        source_run_id="source-run-1",
        cycle="00",
        track="mx2t6_high_full_horizon",
        snapshot_id="snapshot-1",
        snapshot_hash="hash-1",
        captured_at="2026-05-24T09:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="READY",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-25|high",
        source="forecast",
        observed_at="2026-05-24T09:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        received_at="2026-05-24T10:01:00+00:00",
        payload=payload,
        causal_snapshot_id="snapshot-1",
    )


class ReadyEngine(EventBoundDecisionEngine):
    def evaluate(self, request):  # noqa: D401
        return EventBoundDecisionResult(
            status="FINAL_INTENT_READY",
            event_id=request.event.event_id,
            candidate_family=None,
            final_intent_ready=True,
        )


def test_reactor_does_not_call_run_cycle():
    source = inspect.getsource(reactor)

    assert "run_cycle" not in source


def test_reactor_does_not_import_venue_adapter():
    source = inspect.getsource(reactor)

    assert "venue_adapter" not in source


def test_reactor_blocks_final_intent_when_live_submit_disabled():
    result = OpportunityEventReactor(decision_engine=ReadyEngine(), config=ReactorConfig(live_submit_enabled=False)).process(
        EventBoundDecisionRequest(event=_event(), market_topology=(), decision_time="2026-05-24T12:00:00+00:00")
    )

    assert result.status == "NO_TRADE"
    assert result.rejection_reason == "LIVE_SUBMIT_DISABLED"
