# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: PR-2 (B) F1 enforce — mainstream_agreement_enforce_on_submit. When the
#   operator turns enforcement ON (default OFF), an armed submit must require the SELECTED
#   candidate's mainstream_agreement_pass to be True before executor_submit, FAIL-CLOSED on a
#   missing/stale verdict. The reference selector stays reference-only (never excludes);
#   enforcement is a SEPARATE, submit-time, opt-in control.
"""Relationship test: mainstream-agreement verdict -> armed submit boundary.

Two executable proofs of the cross-module contract:
  * reference mode (mainstream_agreement_reference_enabled): the selector still picks the
    higher trade_score candidate even when its verdict FAILED (proven in
    tests/test_mainstream_agreement_gate.py::test_mainstream_gate_is_reference_only...).
  * enforce mode (mainstream_agreement_enforce_on_submit): the SAME failed verdict on the
    selected candidate now BLOCKS the venue submit (executor_submit is never called).

The test crosses the verdict->submit boundary that prose cannot guarantee.
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
    # Reuse the canary test's forecast-event builder (same family/condition shape).
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
    ), _executor


def test_enforce_off_does_not_block_failed_verdict(monkeypatch):
    # Default (enforce OFF): a failed verdict does NOT block submit at this gate.
    # (The submit proceeds past the mainstream gate; downstream cert build may still
    # reject, but NOT with MAINSTREAM_AGREEMENT_REQUIRED.)
    monkeypatch.setitem(settings["edli_v1"], "mainstream_agreement_enforce_on_submit", False)
    event = _event()
    called = {"count": 0}
    submit, _ = _build_submit(monkeypatch, event, mainstream_agreement_pass=False, called=called)

    receipt = submit(event, DT)

    assert receipt.reason != "MAINSTREAM_AGREEMENT_REQUIRED"


def test_enforce_on_blocks_failed_verdict_before_executor(monkeypatch):
    # Enforce ON + selected candidate's verdict FAILED -> reject before executor_submit.
    monkeypatch.setitem(settings["edli_v1"], "mainstream_agreement_enforce_on_submit", True)
    event = _event()
    called = {"count": 0}
    submit, _ = _build_submit(monkeypatch, event, mainstream_agreement_pass=False, called=called)

    receipt = submit(event, DT)

    assert receipt.reason == "MAINSTREAM_AGREEMENT_REQUIRED"
    assert receipt.proof_accepted is False
    assert called["count"] == 0, "executor must NOT be called when enforcement rejects"


def test_enforce_on_fail_closed_on_missing_verdict(monkeypatch):
    # FAIL-CLOSED: a missing/stale verdict (mainstream_agreement_pass is None) must reject
    # under enforcement, never silently submit.
    monkeypatch.setitem(settings["edli_v1"], "mainstream_agreement_enforce_on_submit", True)
    event = _event()
    called = {"count": 0}
    submit, _ = _build_submit(monkeypatch, event, mainstream_agreement_pass=None, called=called)

    receipt = submit(event, DT)

    assert receipt.reason == "MAINSTREAM_AGREEMENT_REQUIRED"
    assert called["count"] == 0


def test_enforce_on_passes_verdict_true_through_gate(monkeypatch):
    # Enforce ON + verdict True -> the mainstream gate does NOT block. (It may still die
    # downstream at cert build in this minimal harness; what matters is the reason is NOT
    # MAINSTREAM_AGREEMENT_REQUIRED — the gate let it through.)
    monkeypatch.setitem(settings["edli_v1"], "mainstream_agreement_enforce_on_submit", True)
    event = _event()
    called = {"count": 0}
    submit, _ = _build_submit(monkeypatch, event, mainstream_agreement_pass=True, called=called)

    receipt = submit(event, DT)

    assert receipt.reason != "MAINSTREAM_AGREEMENT_REQUIRED"
