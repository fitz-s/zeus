# Created: 2026-06-09
# Last reused/audited: 2026-06-09
# Authority basis: FIX-3 (P1) — day0_shadow scope must not permit real submit
#   for day0-lane events, enforced at the final adapter/submit BOUNDARY.
#   Relationship tests: scope × event_type × real_order_submit_enabled → outcome.
#   2026-06-09 extension (operator directive '全部打开'): forecast_plus_day0 scope —
#   day0-lane events PASS the boundary (NOT blocked by DAY0_SCOPE_SHADOW_ONLY);
#   day0_shadow regression-pinned.
"""FIX-3 relationship tests: edli_live_scope=day0_shadow + real_order_submit_enabled=true
+ day0 event → DAY0_SCOPE_SHADOW_ONLY rejection (no-submit); forecast-lane event in the same
config is NOT blocked by DAY0_SCOPE_SHADOW_ONLY (it may fail for other reasons).

forecast_plus_day0 scope: day0-lane events are NOT blocked by the scope gate (they may
be blocked downstream by other proofs/gates/arm — that is fine); the specific reason
DAY0_SCOPE_SHADOW_ONLY must not appear."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.riskguard.risk_level import RiskLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        entity_key="Chicago|2026-06-09|high|shadow-test",
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
        source_run_id="run-shadow-test",
        cycle="00",
        track="live",
        snapshot_id="snap-shadow-1",
        snapshot_hash="hash-shadow-1",
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
        entity_key="Chicago|2026-06-09|high|shadow-forecast-test",
        source="forecast_live",
        observed_at="2026-06-09T18:00:00+00:00",
        available_at="2026-06-09T18:01:00+00:00",
        received_at="2026-06-09T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-shadow-1",
    )


def _accepted_no_submit_receipt(event):
    from src.events.reactor import EventSubmissionReceipt

    return EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="family-shadow-1",
        fdr_hypothesis_count=1,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="cost-shadow-1",
        final_intent_id="intent-shadow-1",
        decision_proof_bundle=object(),
    )


def _build_live_adapter_day0_shadow(monkeypatch, event, *, executor_called):
    """Build a live adapter with real_order_submit_enabled=True and edli_live_scope='day0_shadow'.

    The underlying no-submit receipt is patched to return proof_accepted=True so that
    WITHOUT the scope gate the adapter would proceed to the live order build. This
    isolates the scope gate as the gating mechanism under test.
    """
    from src.engine import event_reactor_adapter as adapter
    from src.main import require_operator_arm

    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: _accepted_no_submit_receipt(event),
    )

    def _executor(_final_intent, _command):
        executor_called["called"] = True
        raise AssertionError("executor_submit must not be reached under day0_shadow scope for day0 events")

    arm = require_operator_arm({"edli_live_operator_authorized": True})

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        durable_submit_outbox_enabled=True,
        executor_submit=_executor,
        operator_arm=arm,
        edli_live_scope="day0_shadow",
    )
    return submit


_DT = datetime(2026, 6, 9, 18, 10, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# FIX-3 core: day0 event + day0_shadow → DAY0_SCOPE_SHADOW_ONLY rejection
# ---------------------------------------------------------------------------

def test_day0_shadow_scope_blocks_day0_event_no_submit(monkeypatch) -> None:
    """day0_shadow scope + real_order_submit_enabled=True + DAY0_EXTREME_UPDATED event
    → receipt with DAY0_SCOPE_SHADOW_ONLY and no actual submit."""
    executor_called = {"called": False}
    event = _day0_event()
    submit = _build_live_adapter_day0_shadow(monkeypatch, event, executor_called=executor_called)

    receipt = submit(event, _DT)

    # The scope gate must fire before any other gate.
    assert receipt.proof_accepted is False, f"proof_accepted should be False, got {receipt.proof_accepted}"
    assert receipt.submitted is False, f"submitted should be False, got {receipt.submitted}"
    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY", (
        f"Expected DAY0_SCOPE_SHADOW_ONLY, got: {receipt.reason!r}"
    )
    assert executor_called["called"] is False, "executor_submit must not be called for day0 events under day0_shadow scope"


def test_day0_shadow_scope_zero_venue_commands_written(monkeypatch) -> None:
    """Complementary to the receipt check: with day0_shadow scope and a day0 event,
    no venue command rows are written to the DB (the in-memory trade_conn stays clean)."""
    executor_called = {"called": False}
    event = _day0_event()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE IF NOT EXISTS venue_commands (id INTEGER PRIMARY KEY)")

    from src.engine import event_reactor_adapter as adapter
    from src.main import require_operator_arm

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
        executor_submit=lambda *_: executor_called.update({"called": True}),  # type: ignore[arg-type]
        operator_arm=require_operator_arm({"edli_live_operator_authorized": True}),
        edli_live_scope="day0_shadow",
    )

    submit(event, _DT)

    row_count = conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0]
    assert row_count == 0, f"Expected 0 venue_commands rows, got {row_count}"
    assert executor_called.get("called") is not True


# ---------------------------------------------------------------------------
# FIX-3: forecast-lane event under day0_shadow is NOT blocked by scope gate
# ---------------------------------------------------------------------------

def test_day0_shadow_scope_does_not_block_forecast_lane_event(monkeypatch) -> None:
    """A FORECAST_SNAPSHOT_READY event with edli_live_scope='day0_shadow' must NOT
    get DAY0_SCOPE_SHADOW_ONLY. The event may be blocked by other gates (live-order
    build, operator arm, etc.) — that is fine — but the specific gate
    DAY0_SCOPE_SHADOW_ONLY must not appear in the receipt reason."""
    executor_called = {"called": False}
    event = _forecast_event()
    submit = _build_live_adapter_day0_shadow(monkeypatch, event, executor_called=executor_called)

    receipt = submit(event, _DT)

    # DAY0_SCOPE_SHADOW_ONLY must NOT be the reason for any rejection.
    assert receipt.reason != "DAY0_SCOPE_SHADOW_ONLY", (
        "Forecast-lane event must not be blocked by DAY0_SCOPE_SHADOW_ONLY gate"
    )


# ---------------------------------------------------------------------------
# FIX-3: scope=forecast_only is unaffected (unchanged behaviour)
# ---------------------------------------------------------------------------

def test_forecast_only_scope_does_not_add_day0_scope_gate(monkeypatch) -> None:
    """With edli_live_scope='forecast_only', the DAY0_SCOPE_SHADOW_ONLY gate must
    NOT fire — the existing forecast_only behaviour is byte-identical to pre-fix."""
    from src.engine import event_reactor_adapter as adapter
    from src.main import require_operator_arm

    event = _forecast_event()
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
        edli_live_scope="forecast_only",
    )

    receipt = submit(event, _DT)

    assert receipt.reason != "DAY0_SCOPE_SHADOW_ONLY", (
        "forecast_only scope must not introduce DAY0_SCOPE_SHADOW_ONLY gate"
    )


# ---------------------------------------------------------------------------
# forecast_plus_day0 (operator directive 2026-06-09 '全部打开'): day0-lane event
# PASSES the DAY0_SCOPE_SHADOW_ONLY boundary. Real submit is then subject to all
# OTHER downstream proofs/gates/arm — the receipt may be rejected for some other
# reason, but NOT for DAY0_SCOPE_SHADOW_ONLY.
# ---------------------------------------------------------------------------

def test_forecast_plus_day0_scope_does_not_block_day0_event(monkeypatch) -> None:
    """edli_live_scope='forecast_plus_day0' + DAY0_EXTREME_UPDATED event must NOT
    be blocked by the scope gate. The event may be rejected by another gate
    downstream — that is acceptable — but the specific reason
    DAY0_SCOPE_SHADOW_ONLY must not appear (the shadow-only purgatory gate is
    opened for day0-lane events under this scope)."""
    from src.engine import event_reactor_adapter as adapter
    from src.main import require_operator_arm

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
        edli_live_scope="forecast_plus_day0",
    )

    receipt = submit(event, _DT)

    # The scope gate must NOT be the blocking reason for a day0 event under
    # forecast_plus_day0. This is the antibody that the purgatory gate is opened.
    assert receipt.reason != "DAY0_SCOPE_SHADOW_ONLY", (
        "forecast_plus_day0 scope must let day0-lane events PASS the "
        "DAY0_SCOPE_SHADOW_ONLY boundary (got it as the rejection reason)"
    )


def test_forecast_plus_day0_scope_admits_at_boot(monkeypatch) -> None:
    """forecast_plus_day0 is an admissible edli_live_scope and, like day0_shadow,
    permits day0 flags to be enabled (no DAY0_OUT_OF_SCOPE_FOR_PR332 crash)."""
    from src.main import EDLI_LIVE_SCOPES, _assert_edli_live_scope

    assert "forecast_plus_day0" in EDLI_LIVE_SCOPES

    # day0 flags ON under forecast_plus_day0 must NOT crash.
    _assert_edli_live_scope(
        {
            "edli_live_scope": "forecast_plus_day0",
            "day0_extreme_trigger_enabled": True,
            "day0_hard_fact_live_enabled": True,
        }
    )

    # Regression: day0 flags ON under forecast_only still crash.
    import pytest

    with pytest.raises(RuntimeError, match="DAY0_OUT_OF_SCOPE_FOR_PR332"):
        _assert_edli_live_scope(
            {
                "edli_live_scope": "forecast_only",
                "day0_extreme_trigger_enabled": True,
            }
        )


# ===========================================================================
# MAJOR 5 — default scope ("forecast_only") explicitly rejects day0-lane and
#            unknown event types at the FINAL ADAPTER BOUNDARY (PR#404).
#            No reliance on the caller passing only the right event types.
# ===========================================================================

def _build_forecast_only_adapter(monkeypatch, event):
    """Build a default-scope (forecast_only) adapter with real_order_submit_enabled=True.
    The no-submit receipt is patched to proof_accepted=True so that WITHOUT the scope
    gate the adapter would proceed — isolating the boundary gate under test."""
    from src.engine import event_reactor_adapter as adapter
    from src.main import require_operator_arm

    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: _accepted_no_submit_receipt(event),
    )
    arm = require_operator_arm({"edli_live_operator_authorized": True})

    return adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("executor_submit must not be reached")),  # type: ignore[arg-type]
        operator_arm=arm,
        # edli_live_scope defaults to "forecast_only" — the default constructor
    )


def test_default_forecast_only_adapter_rejects_day0_event(monkeypatch) -> None:
    """MAJOR 5: default-constructed adapter (forecast_only scope) + DAY0_EXTREME_UPDATED
    → deterministic rejection with reason DAY0_OUT_OF_SCOPE_AT_BOUNDARY.
    No reliance on the caller filtering events — the boundary gate fires regardless."""
    event = _day0_event()
    submit = _build_forecast_only_adapter(monkeypatch, event)

    receipt = submit(event, _DT)

    assert receipt.proof_accepted is False
    assert receipt.submitted is False
    assert receipt.reason == "DAY0_OUT_OF_SCOPE_AT_BOUNDARY", (
        f"forecast_only default scope must reject day0 event with DAY0_OUT_OF_SCOPE_AT_BOUNDARY, "
        f"got: {receipt.reason!r}"
    )


def test_default_forecast_only_adapter_rejects_unknown_event_type(monkeypatch) -> None:
    """MAJOR 5: unknown event_type under forecast_only default scope → rejected
    with DAY0_OUT_OF_SCOPE_AT_BOUNDARY (fail-closed — unknown is never forecast-lane)."""
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event

    # Build a structurally valid event but with an unrecognized event_type.
    # We reuse the forecast payload (irrelevant — the boundary gate fires on event_type).
    payload = ForecastSnapshotReadyPayload(
        city="Chicago", target_date="2026-06-09", metric="high",
        source_id="opendata", source_run_id="run-unk", cycle="00", track="live",
        snapshot_id="snap-unk", snapshot_hash="hash-unk",
        captured_at="2026-06-09T18:00:00+00:00", available_at="2026-06-09T18:01:00+00:00",
        required_fields_present=True, required_steps_present=True,
        member_count=51, min_members_floor=40, completeness_status="COMPLETE",
        required_steps=[0], observed_steps=[0], expected_members=51,
        source_run_status="SUCCESS", source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE", coverage_readiness_status="LIVE_ELIGIBLE",
    )
    unknown_event = make_opportunity_event(
        event_type="UNKNOWN_FUTURE_LANE_TYPE",
        entity_key="Chicago|2026-06-09|high|unknown-test",
        source="forecast_live",
        observed_at="2026-06-09T18:00:00+00:00",
        available_at="2026-06-09T18:01:00+00:00",
        received_at="2026-06-09T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-unk",
    )
    submit = _build_forecast_only_adapter(monkeypatch, unknown_event)

    receipt = submit(unknown_event, _DT)

    assert receipt.proof_accepted is False
    assert receipt.reason == "DAY0_OUT_OF_SCOPE_AT_BOUNDARY", (
        f"forecast_only must reject unknown event types fail-closed, got: {receipt.reason!r}"
    )


def test_default_forecast_only_adapter_admits_forecast_event(monkeypatch) -> None:
    """Regression guard: FORECAST_SNAPSHOT_READY must still pass the scope gate
    under forecast_only — it is the only lane this scope admits."""
    event = _forecast_event()

    from src.engine import event_reactor_adapter as adapter
    from src.main import require_operator_arm

    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: _accepted_no_submit_receipt(event),
    )
    arm = require_operator_arm({"edli_live_operator_authorized": True})

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=False,  # no-submit mode: avoids live-cert build
        operator_arm=arm,
        # default forecast_only scope
    )

    receipt = submit(event, _DT)

    assert receipt.reason != "DAY0_OUT_OF_SCOPE_AT_BOUNDARY", (
        "FORECAST_SNAPSHOT_READY must not be rejected by the forecast_only scope gate"
    )
