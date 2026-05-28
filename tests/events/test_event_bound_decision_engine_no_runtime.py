# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R1 no-runtime proof.

import inspect

from src.events import decision_engine
from src.events.candidate_binding import MarketTopologyCandidate
from src.events.decision_engine import EventBoundDecisionEngine, EventBoundDecisionRequest
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.types.market import Bin


def _forecast_event():
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


def _candidate():
    return MarketTopologyCandidate(
        city="Chicago",
        target_date="2026-05-25",
        metric="high",
        condition_id="condition-1",
        yes_token_id="yes-1",
        no_token_id="no-1",
        bin=Bin(low=70, high=71, unit="F", label="70-71°F"),
    )


def test_decision_engine_builds_event_bound_candidate_family_only():
    result = EventBoundDecisionEngine().evaluate(
        EventBoundDecisionRequest(
            event=_forecast_event(),
            market_topology=(_candidate(),),
            decision_time="2026-05-24T12:00:00+00:00",
            market_topology_source="fixture_topology",
        )
    )

    assert result.status == "CANDIDATE_FAMILY_READY"
    assert result.candidate_family is not None
    assert result.candidate_family.event_id == result.event_id
    assert result.candidate_family.causal_snapshot_id == "snapshot-1"


def test_decision_engine_rejects_unbound_event_without_runtime_fallback():
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
    bad_event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-25|high",
        source="forecast",
        observed_at="2026-05-24T09:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        received_at="2026-05-24T10:01:00+00:00",
        payload=payload,
        causal_snapshot_id=None,
    )

    result = EventBoundDecisionEngine().evaluate(
        EventBoundDecisionRequest(
            event=bad_event,
            market_topology=(_candidate(),),
            decision_time="2026-05-24T12:00:00+00:00",
        )
    )

    assert result.status == "NO_TRADE"
    assert result.candidate_family is None
    assert "causal_snapshot_id" in result.rejection_reason


def test_decision_engine_module_has_no_runtime_side_effect_path():
    source = inspect.getsource(decision_engine)

    forbidden_terms = [
        "run_cycle",
        "submit_existing_cycle",
        "execute_final_intent",
        "executor",
        "venue_adapter",
        "websocket",
    ]
    for term in forbidden_terms:
        assert term not in source
