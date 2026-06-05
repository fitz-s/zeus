# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: Operator directive 2026-06-04 (Rule-4 antibody) — mainstream/forecast-bias
#   agreement is OBSERVATIONAL / DISPLAY-ONLY and must NEVER participate in the trade decision.
#   The enforce-on-submit decision branch (event_reactor_adapter submit closure) and the
#   two-key arm boot guard (main._assert_edli_arm_requires_direction_gate) are DELETED so
#   mainstream has NO code path to gate / reject / skip / alter direction, q, q_lcb,
#   trade_score, selection, or submit. This makes "mainstream changes a decision"
#   UNCONSTRUCTABLE (not merely flag-OFF). DIRECTION LAW (feedback_buy_direction_semantic).
"""Relationship test: mainstream verdict -> decision boundary == NO EFFECT.

The structural law the operator wants made unconstructable: the mainstream/bias
agreement value is computed and ANNOTATED on every receipt, but it can never change
ANY decision. These tests cross the verdict->submit and verdict->selection
boundaries that prose cannot guarantee:

  * test_mainstream_pass_value_cannot_change_submit_decision — the SAME armed submit
    closure run with mainstream_agreement_pass in {True, False, None} reaches the
    IDENTICAL downstream reason. Mainstream is byte-identical across the three values.
  * test_no_enforce_branch_in_submit_closure — the enforce decision branch is GONE
    from source (no MAINSTREAM_AGREEMENT_REQUIRED reason anywhere in the adapter).
  * test_no_arm_direction_gate_boot_guard — the two-key arm boot guard is GONE from
    main (mainstream cannot block boot / arming).
"""
from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timezone

import pytest

from src.config import settings
from src.engine import event_reactor_adapter as adapter
from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
from src.events.reactor import EventSubmissionReceipt
from src.riskguard.risk_level import RiskLevel

DT = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)


def _event():
    from tests.money_path.test_edli_live_canary import _forecast_event

    return _forecast_event()


def _stub_receipt(event, *, mainstream_agreement_pass):
    return EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        decision_proof_bundle=object(),
        mainstream_agreement_pass=mainstream_agreement_pass,
    )


def _build_submit(monkeypatch, event, *, mainstream_agreement_pass, called):
    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_a, **_k: _stub_receipt(event, mainstream_agreement_pass=mainstream_agreement_pass),
    )

    def _executor(_final_intent, _command):
        called["count"] += 1
        return EventBoundExecutorSubmitResult(
            status="SUBMITTED",
            reason_code="OK",
            venue_order_id="venue-1",
            submit_started_at="2026-05-24T18:10:00+00:00",
            submit_finished_at="2026-05-24T18:10:01+00:00",
            raw_response={"status": "submitted"},
        )

    return adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        live_canary_enabled=True,
        durable_submit_outbox_enabled=True,
        executor_submit=_executor,
    )


def test_mainstream_pass_value_cannot_change_submit_decision(monkeypatch):
    """DISPLAY-ONLY proof: pass=True / False / None must yield the IDENTICAL downstream
    decision. Mainstream takes NO part — the reason a candidate is held in shadow is
    arm=False / downstream cert build, NEVER the mainstream verdict. No value of the
    mainstream verdict may produce MAINSTREAM_AGREEMENT_REQUIRED (the deleted branch)."""
    # The config key has NO effect now — set both postures to prove value-independence.
    for enforce_posture in (True, False):
        monkeypatch.setitem(settings["edli_v1"], "mainstream_agreement_enforce_on_submit", enforce_posture)
        reasons = {}
        for verdict in (True, False, None):
            event = _event()
            called = {"count": 0}
            submit = _build_submit(monkeypatch, event, mainstream_agreement_pass=verdict, called=called)
            receipt = submit(event, DT)
            reasons[verdict] = receipt.reason
            # The deleted branch can never fire, regardless of the verdict or the
            # (now-inert) config key.
            assert receipt.reason != "MAINSTREAM_AGREEMENT_REQUIRED", (
                f"mainstream verdict={verdict} produced MAINSTREAM_AGREEMENT_REQUIRED "
                f"(enforce_posture={enforce_posture}) — the decision branch was not deleted"
            )
        # Byte-identical decision across the three verdict values: mainstream is inert.
        assert reasons[True] == reasons[False] == reasons[None], (
            "mainstream verdict changed the downstream decision reason: "
            f"True={reasons[True]!r} False={reasons[False]!r} None={reasons[None]!r} "
            f"(enforce_posture={enforce_posture}) — mainstream is NOT display-only"
        )


def test_no_enforce_branch_in_submit_closure():
    """STRUCTURAL: the enforce decision branch is DELETED from the adapter source.
    No MAINSTREAM_AGREEMENT_REQUIRED reason, no enforce_on_submit read in a decision
    path."""
    src = inspect.getsource(adapter)
    assert "MAINSTREAM_AGREEMENT_REQUIRED" not in src, (
        "MAINSTREAM_AGREEMENT_REQUIRED reason still present — the enforce-on-submit "
        "decision branch was not deleted; mainstream can still reject a submit."
    )
    assert "mainstream_agreement_enforce_on_submit" not in src, (
        "mainstream_agreement_enforce_on_submit still read in the adapter — mainstream "
        "must have NO decision-path effect (operator law: observational/display-only)."
    )


def test_no_arm_direction_gate_boot_guard():
    """STRUCTURAL: the two-key arm boot guard is DELETED from main. Arming can no longer
    require the mainstream enforcement flag (mainstream cannot block boot/arm)."""
    import src.main as main

    assert not hasattr(main, "_assert_edli_arm_requires_direction_gate"), (
        "main._assert_edli_arm_requires_direction_gate still exists — the boot guard "
        "still couples arming to the mainstream enforcement flag; mainstream must NOT "
        "be a decision/arm input."
    )
    main_src = inspect.getsource(main)
    assert "EDLI_LIVE_REQUIRES_MAINSTREAM_AGREEMENT_ENFORCEMENT" not in main_src, (
        "the mainstream-enforcement boot RuntimeError still present in main."
    )
    assert "mainstream_agreement_enforce_on_submit" not in main_src, (
        "main still reads mainstream_agreement_enforce_on_submit — mainstream has a "
        "decision/arm-path effect; it must be display-only."
    )
