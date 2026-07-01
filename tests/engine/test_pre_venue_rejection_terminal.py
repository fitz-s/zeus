# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: EDLI live-order aggregate event-sourcing law
#   (src/events/live_order_aggregate.py), executor pre-venue depth validation
#   (src/execution/executor.py:1773), live-cap ledger (src/events/live_cap.py),
#   boot readiness gate (src/main.py::_assert_edli_stage_readiness).
#
# RELATIONSHIP under test (Fitz methodology — test the boundary, not the function):
#   When the executor's PRE-VENUE depth validation rejects an order (the order
#   provably never reaches the venue), the EDLI submit boundary MUST classify the
#   result as a TERMINAL pre-submit error (PRE_SUBMIT_ERROR, venue_call_started=
#   False), NOT an indeterminate POST_SUBMIT_UNKNOWN. The downstream cap-transition
#   then RELEASES the LIVE_CAP reservation and the aggregate reaches a terminal
#   state, so no unresolved-submit / held-cap is left behind to deadlock boot.
#
#   A genuinely post-venue unknown (executor raised AFTER the venue call started,
#   side effect indeterminate) MUST still be POST_SUBMIT_UNKNOWN so the boot gate
#   correctly blocks until reconcile.

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import src.engine.event_bound_final_intent as ebfi
from src.engine.event_bound_final_intent import (
    EventBoundExecutorSubmitResult,
    PreVenueSubmitError,
    submit_event_bound_final_intent_via_existing_executor,
)


@pytest.fixture(autouse=True)
def _stub_intent_builder(monkeypatch):
    # Isolate the exception-classification behavior under test from cert payload
    # completeness: the boundary builds an executor-native intent from the certs
    # before calling executor_submit. We stub that builder so the injected
    # executor_submit (the real seam) is reached with minimal certs.
    monkeypatch.setattr(
        ebfi, "_final_execution_intent_from_cert", lambda f, c: object()
    )


class _Cert:
    """Minimal DecisionCertificate stand-in exposing .payload and .certificate_hash."""

    def __init__(self, payload: dict, certificate_hash: str = "cert:hash"):
        self.payload = payload
        self.certificate_hash = certificate_hash


_FINAL = _Cert(
    {
        "event_id": "evt-1",
        "final_intent_id": "fin-1",
        "token_id": "tok-1",
        "direction": "buy_no",
    }
)
_COMMAND = _Cert(
    {
        "event_id": "evt-1",
        "final_intent_id": "fin-1",
        "token_id": "tok-1",
        "direction": "buy_no",
        "execution_command_id": "cmd-1",
    }
)


def _now() -> datetime:
    return datetime(2026, 6, 1, 7, 31, tzinfo=timezone.utc)


def test_pre_venue_depth_rejection_is_terminal_pre_submit_error():
    """RED before fix: a PreVenueSubmitError (depth validation, no venue call)
    must NOT be classified POST_SUBMIT_UNKNOWN. It must be PRE_SUBMIT_ERROR with
    venue_call_started=False so the cap is released and the aggregate terminates."""

    def _executor_submit(intent, conn=None, decision_id="", snapshot_conn=None):
        raise PreVenueSubmitError(
            "FinalExecutionIntent executable depth validation failed: DEPTH_INSUFFICIENT"
        )

    result = submit_event_bound_final_intent_via_existing_executor(
        final_intent_cert=_FINAL,
        execution_command_cert=_COMMAND,
        conn=None,  # type: ignore[arg-type]
        decision_time=_now(),
        executor_submit=_executor_submit,
    )

    assert result.status == "PRE_SUBMIT_ERROR", result.status
    assert result.venue_call_started is False
    assert result.side_effect_known is True
    assert result.reconciliation_followup_required is False
    assert "DEPTH_INSUFFICIENT" in result.reason_code


def test_gate_runtime_block_is_terminal_pre_submit_error():
    """A live_venue_submit runtime gate fires before the venue boundary.

    It must release the EDLI cap as a known no-side-effect pre-submit rejection,
    not create a SubmitUnknown/PENDING_RECONCILE lock.
    """

    def _executor_submit(intent, conn=None, decision_id="", snapshot_conn=None):
        raise RuntimeError(
            "[gate_runtime] BLOCKED cap='live_venue_submit': condition "
            "'deployment_freshness_mismatch' is active (boot_sha=old current_sha=new)"
        )

    result = submit_event_bound_final_intent_via_existing_executor(
        final_intent_cert=_FINAL,
        execution_command_cert=_COMMAND,
        conn=None,  # type: ignore[arg-type]
        decision_time=_now(),
        executor_submit=_executor_submit,
    )

    assert result.status == "PRE_SUBMIT_ERROR", result.status
    assert result.venue_call_started is False
    assert result.side_effect_known is True
    assert result.reconciliation_followup_required is False
    assert "deployment_freshness_mismatch" in result.reason_code


def test_post_venue_unknown_still_blocks_as_post_submit_unknown():
    """Guard not weakened: a generic exception AFTER the venue call started (the
    side effect is genuinely unknown) must still be POST_SUBMIT_UNKNOWN with
    venue_call_started=True so boot readiness keeps blocking until reconcile."""

    def _executor_submit(intent, conn=None, decision_id="", snapshot_conn=None):
        raise RuntimeError("connection reset by peer after POST")

    result = submit_event_bound_final_intent_via_existing_executor(
        final_intent_cert=_FINAL,
        execution_command_cert=_COMMAND,
        conn=None,  # type: ignore[arg-type]
        decision_time=_now(),
        executor_submit=_executor_submit,
    )

    assert result.status == "POST_SUBMIT_UNKNOWN", result.status
    assert result.venue_call_started is True
    assert result.side_effect_known is False
    assert result.reconciliation_followup_required is True


@pytest.mark.parametrize(
    "reason",
    [
        "entry_cooldown:same_token_entry_cooling_down",
        "entries_paused:operator_pause_live_bad_entry_tokyo_005_yes_until_root_fix",
        "duplicate_entry_same_token:open_position_same_token",
    ],
)
def test_executor_designed_pre_submit_rejections_do_not_count_as_venue_rejects(reason):
    """Local executor gates reject before SDK submission and must not emit venue ACK evidence."""

    def _executor_submit(intent, conn=None, decision_id="", snapshot_conn=None):
        return SimpleNamespace(
            status="rejected",
            reason=reason,
            command_state=None,
            order_id=None,
            external_order_id=None,
        )

    result = submit_event_bound_final_intent_via_existing_executor(
        final_intent_cert=_FINAL,
        execution_command_cert=_COMMAND,
        conn=None,  # type: ignore[arg-type]
        decision_time=_now(),
        executor_submit=_executor_submit,
    )

    assert result.status == "PRE_SUBMIT_ERROR"
    assert result.reason_code == reason
    assert result.venue_call_started is False
    assert result.venue_ack_received is False
    assert result.side_effect_known is True
    assert result.reconciliation_followup_required is False
