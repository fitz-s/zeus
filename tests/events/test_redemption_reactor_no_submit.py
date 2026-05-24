# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R8/R9 proof.

import inspect

from src.events import reactor
from datetime import datetime, timezone

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor, ReactorConfig
from src.state.schema.edli_live_cap_usage_schema import ensure_table as ensure_edli_live_cap_usage_schema
from src.state.schema.event_dead_letters_schema import ensure_table as ensure_event_dead_letters_schema
from src.state.schema.no_trade_regret_events_schema import ensure_table as ensure_no_trade_regret_events_schema
from src.state.schema.opportunity_event_processing_schema import ensure_table as ensure_opportunity_event_processing_schema
from src.state.schema.opportunity_events_schema import ensure_table as ensure_opportunity_events_schema


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


def test_reactor_does_not_call_run_cycle():
    source = inspect.getsource(reactor)

    assert "run_cycle" not in source


def test_reactor_does_not_import_venue_adapter():
    source = inspect.getsource(reactor)

    assert "venue_adapter" not in source


def test_reactor_blocks_final_intent_when_live_submit_disabled():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    ensure_opportunity_events_schema(conn)
    ensure_opportunity_event_processing_schema(conn)
    ensure_event_dead_letters_schema(conn)
    ensure_no_trade_regret_events_schema(conn)
    ensure_edli_live_cap_usage_schema(conn)
    store = EventStore(conn)
    event = _event()
    store.insert_or_ignore(event)
    rejected = []

    def _submit(_event):
        return EventSubmissionReceipt(
            submitted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-05-25",
            metric="high",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            final_intent_id="intent-1",
            side_effect_status="SUBMITTED",
        )

    result = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=ReactorConfig(reactor_mode="live_no_submit", real_order_submit_enabled=False),
    ).process_pending(
        decision_time=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )

    assert result.rejected == 1
    assert rejected[0][1] == "EXECUTOR_EXPRESSIBILITY"
    assert rejected[0][2] == "EDLI_REAL_ORDER_SIDE_EFFECT_FORBIDDEN"


def test_no_submit_fdr_rejection_is_classified_as_fdr_not_executor_expressibility():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    ensure_opportunity_events_schema(conn)
    ensure_opportunity_event_processing_schema(conn)
    ensure_event_dead_letters_schema(conn)
    ensure_no_trade_regret_events_schema(conn)
    ensure_edli_live_cap_usage_schema(conn)
    store = EventStore(conn)
    event = _event()
    store.insert_or_ignore(event)
    rejected = []

    def _submit(_event):
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-05-25",
            metric="high",
            trade_score_positive=True,
            fdr_pass=False,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            side_effect_status="NO_SUBMIT",
            reason="FDR_REJECTED",
        )

    result = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=ReactorConfig(reactor_mode="live_no_submit", real_order_submit_enabled=False),
    ).process_pending(
        decision_time=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )

    assert result.rejected == 1
    assert rejected[0][1] == "FDR"
    assert rejected[0][2] == "FDR_REJECTED"


def test_no_submit_kelly_rejection_is_classified_as_kelly_not_executor_expressibility():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    ensure_opportunity_events_schema(conn)
    ensure_opportunity_event_processing_schema(conn)
    ensure_event_dead_letters_schema(conn)
    ensure_no_trade_regret_events_schema(conn)
    ensure_edli_live_cap_usage_schema(conn)
    store = EventStore(conn)
    event = _event()
    store.insert_or_ignore(event)
    rejected = []

    def _submit(_event):
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-05-25",
            metric="high",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=False,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            side_effect_status="NO_SUBMIT",
            reason="KELLY_REJECTED",
        )

    result = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=ReactorConfig(reactor_mode="live_no_submit", real_order_submit_enabled=False),
    ).process_pending(
        decision_time=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )

    assert result.rejected == 1
    assert rejected[0][1] == "KELLY"
    assert rejected[0][2] == "KELLY_REJECTED"
