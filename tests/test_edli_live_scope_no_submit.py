# Created: 2026-06-09
# Last reused/audited: 2026-06-18
# Authority basis: live-only EDLI adapter law. Production accepts only
# forecast_plus_day0 at the final adapter boundary; former shadow/forecast_only
# scopes are rejected before proof building or executor submit.
"""Relationship tests for the EDLI live adapter scope boundary."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.riskguard.risk_level import RiskLevel


def _day0_event():
    from src.events.opportunity_event import Day0ExtremeUpdatedPayload, make_day0_extreme_updated_event

    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-06-09",
        metric="high",
        settlement_source="opendata",
        station_id="KORD",
        observation_time="2026-06-09T18:00:00+00:00",
        observation_available_at="2026-06-09T18:01:00+00:00",
        raw_value=28.5,
        rounded_value=29,
    )
    return make_day0_extreme_updated_event(
        entity_key="Chicago|2026-06-09|high|live-scope-test",
        source="opendata",
        observed_at="2026-06-09T18:00:00+00:00",
        received_at="2026-06-09T18:01:00+00:00",
        payload=payload,
    )


def _forecast_event():
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event

    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-06-09",
        metric="high",
        source_id="opendata",
        source_run_id="run-live-scope-test",
        cycle="00",
        track="live",
        snapshot_id="snap-live-scope-1",
        snapshot_hash="hash-live-scope-1",
        captured_at="2026-06-09T18:00:00+00:00",
        available_at="2026-06-09T18:01:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0],
        observed_steps=[0],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-06-09|high|live-forecast-test",
        source="forecast_live",
        observed_at="2026-06-09T18:00:00+00:00",
        available_at="2026-06-09T18:01:00+00:00",
        received_at="2026-06-09T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-live-scope-1",
    )


def _unknown_event():
    event = _forecast_event()
    object.__setattr__(event, "event_type", "UNKNOWN_FUTURE_LANE_TYPE")
    return event


def _accepted_no_submit_receipt(event):
    from src.events.reactor import EventSubmissionReceipt

    return EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="family-live-scope-1",
        fdr_hypothesis_count=1,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="cost-live-scope-1",
        final_intent_id="intent-live-scope-1",
        decision_proof_bundle=object(),
    )


def _build_adapter(monkeypatch, event, *, edli_live_scope: str, executor_called: dict[str, bool]):
    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import require_operator_arm

    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: _accepted_no_submit_receipt(event),
    )

    def _executor(_final_intent, _command):
        executor_called["called"] = True
        raise AssertionError("executor_submit must not be reached by scope-boundary tests")

    return adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        durable_submit_outbox_enabled=True,
        executor_submit=_executor,
        operator_arm=require_operator_arm({"edli_live_operator_authorized": True}),
        edli_live_scope=edli_live_scope,
    )


_DT = datetime(2026, 6, 9, 18, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("scope", ["unsupported_scope_a", "forecast_only"])
@pytest.mark.parametrize("event_factory", [_day0_event, _forecast_event])
def test_non_live_scopes_reject_before_submit(monkeypatch, scope: str, event_factory) -> None:
    executor_called = {"called": False}
    event = event_factory()
    submit = _build_adapter(monkeypatch, event, edli_live_scope=scope, executor_called=executor_called)

    receipt = submit(event, _DT)

    assert receipt.proof_accepted is False
    assert receipt.submitted is False
    assert receipt.reason == f"UNSUPPORTED_EDLI_LIVE_SCOPE:{scope}"
    assert executor_called["called"] is False


def test_non_live_scope_writes_no_venue_commands(monkeypatch) -> None:
    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import require_operator_arm

    event = _day0_event()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE IF NOT EXISTS venue_commands (id INTEGER PRIMARY KEY)")
    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: _accepted_no_submit_receipt(event),
    )

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        durable_submit_outbox_enabled=True,
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("executor must not run")),  # type: ignore[arg-type]
        operator_arm=require_operator_arm({"edli_live_operator_authorized": True}),
        edli_live_scope="unsupported_scope_a",
    )

    receipt = submit(event, _DT)

    assert receipt.reason == "UNSUPPORTED_EDLI_LIVE_SCOPE:unsupported_scope_a"
    assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0


@pytest.mark.parametrize("event_factory", [_day0_event, _forecast_event])
def test_forecast_plus_day0_admits_live_event_types_to_proof_build(monkeypatch, event_factory) -> None:
    executor_called = {"called": False}
    event = event_factory()
    submit = _build_adapter(
        monkeypatch,
        event,
        edli_live_scope="forecast_plus_day0",
        executor_called=executor_called,
    )

    receipt = submit(event, _DT)

    assert receipt.reason != "UNSUPPORTED_EDLI_LIVE_SCOPE:forecast_plus_day0"
    assert receipt.reason != "EVENT_TYPE_OUT_OF_LIVE_SCOPE"


def test_default_scope_is_forecast_plus_day0(monkeypatch) -> None:
    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import require_operator_arm

    event = _day0_event()
    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: _accepted_no_submit_receipt(event),
    )

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        durable_submit_outbox_enabled=True,
        executor_submit=lambda *_: None,  # type: ignore[arg-type]
        operator_arm=require_operator_arm({"edli_live_operator_authorized": True}),
    )

    receipt = submit(event, _DT)

    assert receipt.reason != "UNSUPPORTED_EDLI_LIVE_SCOPE:forecast_only"
    assert receipt.reason != "UNSUPPORTED_EDLI_LIVE_SCOPE:unsupported_scope_a"


def test_unknown_event_type_fails_closed_under_live_scope(monkeypatch) -> None:
    executor_called = {"called": False}
    event = _unknown_event()
    submit = _build_adapter(
        monkeypatch,
        event,
        edli_live_scope="forecast_plus_day0",
        executor_called=executor_called,
    )

    receipt = submit(event, _DT)

    assert receipt.proof_accepted is False
    assert receipt.submitted is False
    assert receipt.reason == "EVENT_TYPE_OUT_OF_LIVE_SCOPE"
    assert executor_called["called"] is False
