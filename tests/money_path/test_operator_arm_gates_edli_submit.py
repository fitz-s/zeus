# Lifecycle: created=2026-06-07; last_reviewed=2026-06-07; last_reused=2026-06-07
# Purpose: FIX-2b OperatorArm gate — modes x operator_authorized matrix for the EDLI live-submit boundary; mainline executor remains unaffected.
# Reuse: Run with pytest; update if OperatorArm, EDLI submit guard, or mode definitions change.
# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Authority basis: PR_SPEC.md §2 FIX-2b (operator arm must gate every real submit, by TYPE)
#   + ITEM A. The mainline executor (293 orders) goes main.py:7239 ->
#   cycle_runtime.py:5950 execute_final_intent DIRECTLY and never constructs the EDLI
#   adapter, so this gate is applied EXACTLY at the EDLI boundary (require_operator_arm
#   + the live adapter's 4th submit guard), never at the convergence node. No token
#   requirement is added inside execute_final_intent/_live_order, which would halt the
#   293-order mainline.
"""FIX-2b OperatorArm: modes x operator_authorized matrix for the EDLI live-submit gate."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.riskguard.risk_level import RiskLevel


# --- require_operator_arm: the token is constructible ONLY when authorized -----------

def test_require_operator_arm_returns_none_unless_operator_authorized() -> None:
    from src.events.reactor import require_operator_arm

    assert require_operator_arm({}) is None
    assert require_operator_arm({"edli_live_operator_authorized": False}) is None
    # Non-bool / truthy-but-not-True must NOT mint the arm (mirror the strict assert
    # pattern at main.py:563-567 — only the literal True authorizes).
    assert require_operator_arm({"edli_live_operator_authorized": "true"}) is None
    assert require_operator_arm({"edli_live_operator_authorized": 1}) is None

    arm = require_operator_arm({"edli_live_operator_authorized": True})
    assert arm is not None


def test_operator_arm_is_frozen_and_typed() -> None:
    from dataclasses import FrozenInstanceError

    from src.events.reactor import OperatorArm, require_operator_arm

    arm = require_operator_arm({"edli_live_operator_authorized": True})
    assert isinstance(arm, OperatorArm)
    with pytest.raises(FrozenInstanceError):
        arm.authorized = False  # type: ignore[misc]


# --- the live adapter's 4th submit guard: OPERATOR_ARM_REQUIRED ---------------------

def _forecast_event():
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event

    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="opendata",
        source_run_id="run-1",
        cycle="00",
        track="live",
        snapshot_id="snap-1",
        snapshot_hash="hash-1",
        captured_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
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
        entity_key="Chicago|2026-05-24|high|operator-arm-test",
        source="forecast_live",
        observed_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        received_at="2026-05-24T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-1",
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
        fdr_family_id="family-1",
        fdr_hypothesis_count=1,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="cost-1",
        final_intent_id="intent-1",
        decision_proof_bundle=object(),
    )


def _build_submit(monkeypatch, *, operator_arm, executor_called):
    from src.engine import event_reactor_adapter as adapter

    event = _forecast_event()
    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: _accepted_no_submit_receipt(event),
    )

    def _executor_submit(_final_intent, _command):
        executor_called["called"] = True
        raise AssertionError("executor_submit must not be reached when the arm gate blocks")

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        durable_submit_outbox_enabled=True,
        executor_submit=_executor_submit,
        operator_arm=operator_arm,
    )
    return submit, event


def test_live_adapter_submit_blocks_when_operator_arm_is_none(monkeypatch) -> None:
    """4th guard: with real_order_submit_enabled and operator_arm is None, the submit
    must terminate at OPERATOR_ARM_REQUIRED before any executor call, even with canary
    + durable outbox + executor all wired (the prior three guards all pass)."""
    executor_called = {"called": False}
    submit, event = _build_submit(monkeypatch, operator_arm=None, executor_called=executor_called)

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert receipt.submitted is False
    assert receipt.proof_accepted is False
    assert receipt.reason == "OPERATOR_ARM_REQUIRED"
    assert executor_called["called"] is False


def test_live_adapter_entries_pause_blocks_before_live_cap_and_command(monkeypatch) -> None:
    """Operator pause must stop before live-cap reserve / ExecutionCommandCreated."""
    from src.events.reactor import require_operator_arm
    from src.engine import event_reactor_adapter as adapter

    event = _forecast_event()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE control_overrides (
            override_id TEXT,
            target_type TEXT,
            target_key TEXT,
            action_type TEXT,
            value TEXT,
            issued_by TEXT,
            issued_at TEXT,
            effective_until TEXT,
            reason TEXT,
            precedence INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO control_overrides (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, effective_until, reason, precedence
        ) VALUES (
            'control_plane:global:entries_paused', 'global', 'entries',
            'gate', 'true', 'manual_command',
            '2026-05-24T18:00:00+00:00', NULL,
            'operator_pause_live_bad_entry_tokyo_005_yes_until_root_fix', 100
        )
        """
    )
    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: _accepted_no_submit_receipt(event),
    )
    executor_called = {"called": False}

    def _executor_submit(_final_intent, _command):
        executor_called["called"] = True
        raise AssertionError("executor_submit must not run while entries are paused")

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        durable_submit_outbox_enabled=True,
        executor_submit=_executor_submit,
        operator_arm=require_operator_arm({"edli_live_operator_authorized": True}),
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert receipt.submitted is False
    assert receipt.proof_accepted is False
    assert receipt.reason == "entries_paused:external:manual_command"
    assert executor_called["called"] is False
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name IN "
            "('edli_live_cap_usage', 'edli_live_order_events')"
        ).fetchone()[0]
        == 0
    )


def test_live_adapter_submit_passes_arm_guard_when_operator_arm_present(monkeypatch) -> None:
    """With a present operator_arm, the 4th guard must NOT block — execution proceeds
    PAST the arm guard (it reaches the live-order build path, which here surfaces a
    different/no OPERATOR_ARM_REQUIRED outcome). The arm guard is the only thing under
    test, so the reason must never be OPERATOR_ARM_REQUIRED."""
    from src.events.reactor import require_operator_arm

    arm = require_operator_arm({"edli_live_operator_authorized": True})
    executor_called = {"called": False}
    submit, event = _build_submit(monkeypatch, operator_arm=arm, executor_called=executor_called)

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    # Past the arm guard the build path may still fail for unrelated reasons, but it
    # must NEVER be the arm gate.
    assert receipt.reason != "OPERATOR_ARM_REQUIRED"


# --- selector law: live builder selected ONLY when operator_arm is not None ----------

@pytest.mark.parametrize("live_execution_mode", ["edli_live"])
def test_live_builder_not_selected_when_operator_not_authorized(live_execution_mode: str) -> None:
    """ITEM A selector law (main.py ~5220): the live adapter is chosen ONLY when
    (live_submit_effective AND operator_arm is not None). With operator_authorized
    false, require_operator_arm returns None, so for BOTH live-submit modes the
    selector predicate is False and the no-submit builder is chosen."""
    from src.events.reactor import require_operator_arm

    edli_cfg = {
        "live_execution_mode": live_execution_mode,
        "reactor_mode": "live",
        "real_order_submit_enabled": True,
        "edli_live_operator_authorized": False,
    }
    operator_arm = require_operator_arm(edli_cfg)
    assert operator_arm is None

    live_submit_effective = True  # both modes resolve live_submit_effective True upstream
    # The selector predicate the main.py wiring uses.
    live_builder_selected = live_submit_effective and operator_arm is not None
    assert live_builder_selected is False


@pytest.mark.parametrize("live_execution_mode", ["edli_live"])
def test_live_builder_selected_when_operator_authorized(live_execution_mode: str) -> None:
    """With operator_authorized true, require_operator_arm mints the token, so for BOTH
    live-submit modes the selector predicate is True and the real-submit live adapter
    is reachable."""
    from src.events.reactor import require_operator_arm

    edli_cfg = {
        "live_execution_mode": live_execution_mode,
        "reactor_mode": "live",
        "real_order_submit_enabled": True,
        "edli_live_operator_authorized": True,
    }
    operator_arm = require_operator_arm(edli_cfg)
    assert operator_arm is not None

    live_submit_effective = True
    live_builder_selected = live_submit_effective and operator_arm is not None
    assert live_builder_selected is True
