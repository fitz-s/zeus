# Created: 2026-06-03
# Last reused/audited: 2026-06-04
# Authority basis: SUPERSEDED 2026-06-04 by operator directive (Rule-4 antibody) — mainstream
#   is OBSERVATIONAL / DISPLAY-ONLY and NEVER a decision input. The former
#   ``mainstream_agreement_enforce_on_submit`` submit-time decision branch is DELETED, so a
#   failed/missing mainstream verdict can no longer block a submit. The original PR-2 (B) F1
#   enforce contract (a failed verdict BLOCKS the venue submit) is RETIRED; these tests now
#   assert the INVERSE law — mainstream takes NO part in the submit decision.
"""Relationship test: mainstream verdict -> armed submit boundary == NO EFFECT.

These tests previously proved the (now-deleted) enforce-on-submit branch BLOCKED a
submit on a failed verdict. Under the 2026-06-04 operator law (mainstream is
display-only, never a decision input), the inverse is the contract: a failed / missing /
passing mainstream verdict all reach the IDENTICAL submit decision. No value of the
verdict, and no value of the (now-inert) ``mainstream_agreement_enforce_on_submit``
config key, may produce a MAINSTREAM_AGREEMENT_REQUIRED rejection — that reason no
longer exists. The richer cross-boundary proof lives in
tests/money_path/test_mainstream_display_only_unconstructable.py.
"""
from __future__ import annotations

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
    from tests.money_path.test_edli_live_readiness import _forecast_event

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
        durable_submit_outbox_enabled=True,
        executor_submit=_executor,
    ), _executor


@pytest.mark.parametrize("enforce_posture", [True, False])
@pytest.mark.parametrize("verdict", [True, False, None])
def test_mainstream_verdict_never_blocks_submit(monkeypatch, enforce_posture, verdict):
    """No mainstream verdict value, under any (inert) enforce_on_submit posture, may
    block the submit with MAINSTREAM_AGREEMENT_REQUIRED — the branch is deleted."""
    monkeypatch.setitem(settings["edli"], "mainstream_agreement_enforce_on_submit", enforce_posture)
    event = _event()
    called = {"count": 0}
    submit, _ = _build_submit(monkeypatch, event, mainstream_agreement_pass=verdict, called=called)

    receipt = submit(event, DT)

    assert receipt.reason != "MAINSTREAM_AGREEMENT_REQUIRED", (
        f"mainstream verdict={verdict} (enforce={enforce_posture}) blocked submit — "
        "the display-only law was violated; the enforce branch was not deleted."
    )


def test_failed_and_passing_verdict_reach_identical_decision(monkeypatch):
    """A FAILED verdict and a PASSING verdict reach the byte-identical submit decision:
    mainstream is inert at the submit boundary."""
    monkeypatch.setitem(settings["edli"], "mainstream_agreement_enforce_on_submit", True)
    reasons = {}
    for verdict in (True, False, None):
        event = _event()
        called = {"count": 0}
        submit, _ = _build_submit(monkeypatch, event, mainstream_agreement_pass=verdict, called=called)
        reasons[verdict] = submit(event, DT).reason
    assert reasons[True] == reasons[False] == reasons[None], (
        f"mainstream verdict changed the submit decision: {reasons!r}"
    )
