# Created: 2026-05-24
# Last reused/audited: 2026-07-15
# Authority basis: EDLI v1 implementation prompt §13 event reactor no-bypass contract.
from __future__ import annotations

import sqlite3
import json
import hashlib
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.decision_kernel import claims
from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle
from src.events.event_store import EventStore
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    MarketBookEventPayload,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)
from src.events.reactor import (
    EventSubmissionReceipt,
    GlobalBatchSubmitResult,
    OpportunityEventReactor,
    ReactorConfig,
    ReactorResult,
    TERMINAL_MONEY_PATH_REASONS,
    TRANSIENT_MONEY_PATH_REASONS,
    _rank_forecast_wake_events,
    _is_transient_money_path_reason,
)
from src.state.db import init_schema, world_write_mutex
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger


def _store() -> tuple[sqlite3.Connection, EventStore]:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventStore(conn)


def _processing_status(conn: sqlite3.Connection, event_id: str) -> str:
    return conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]


def test_global_family_ineligible_is_explicitly_transient(caplog):
    reason = (
        "GLOBAL_FAMILY_INELIGIBLE:GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:"
        "REPLACEMENT_RAW_INPUT_HWM"
    )

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "GLOBAL_FAMILY_INELIGIBLE" in TRANSIENT_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is True

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


def test_global_not_selected_is_terminal_for_completed_epoch(caplog):
    reason = "GLOBAL_NOT_SELECTED:winning-actuation-identity"

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "GLOBAL_NOT_SELECTED" in TERMINAL_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is False

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


def test_global_preflight_cash_is_terminal_only_for_complete_action_set(caplog):
    complete_cash = (
        "GLOBAL_PREFLIGHT_HOLD_CASH_OPTIMAL:"
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:families=0:candidates=0"
    )
    candidate_missing = (
        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:families=0:candidates=1"
    )

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "GLOBAL_PREFLIGHT_HOLD_CASH_OPTIMAL" in TERMINAL_MONEY_PATH_REASONS
        assert "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED" in TRANSIENT_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(complete_cash) is False
        assert _is_transient_money_path_reason(candidate_missing) is True

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


# A transient-REQUEUE test must price its decision at a VENUE-OPEN instant so the
# family is genuinely still tradeable (a fresh book is still capturable) and the
# venue-close horizon (reactor._venue_market_closed_horizon, 2026-06-13 zero-order
# reactor-stall fix) does NOT terminalize it. The _forecast_event fixture's snapshot
# becomes available at 2026-05-24T18:01Z, which is AFTER a 2026-05-24 market's
# 12:00Z venue close — so those requeue tests use a 2026-05-25 TARGET (closes 12:00Z
# 05-25) paired with this 05-25T06:10Z decision time (SETTLEMENT_DAY, pre-close,
# after the snapshot is available). The previous 18:10Z-on-05-24 only "requeued"
# because the reactor used to ignore the venue close until local-day-end.
_DT_VENUE_OPEN = datetime(2026, 5, 25, 6, 10, tzinfo=timezone.utc)


def _day0_event(key_suffix: str = "a"):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        settlement_source="WU",
        station_id="KMDW",
        observation_time="2026-05-24T18:00:00+00:00",
        observation_available_at="2026-05-24T18:07:00+00:00",
        raw_value=74.2,
        rounded_value=74,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
    )
    return make_day0_extreme_updated_event(
        entity_key=f"Chicago|2026-05-24|high|{key_suffix}",
        source="day0_observation",
        observed_at=payload.observation_time,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
    )


def _day0_event_for_target(key_suffix: str, target_date: str, available_at: str):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date=target_date,
        metric="high",
        settlement_source="WU",
        station_id="KMDW",
        observation_time=available_at,
        observation_available_at=available_at,
        raw_value=74.2,
        rounded_value=74,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
    )
    return make_day0_extreme_updated_event(
        entity_key=f"Chicago|{target_date}|high|{key_suffix}",
        source="day0_observation",
        observed_at=payload.observation_time,
        received_at=available_at,
        payload=payload,
    )


def _forecast_event(key_suffix: str = "a", target_date: str = "2026-05-24"):
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date=target_date,
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
        entity_key=f"Chicago|{target_date}|high|{key_suffix}",
        source="forecast_live",
        observed_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        received_at="2026-05-24T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-1",
    )


def _market_event():
    payload = MarketBookEventPayload(
        condition_id="0xcondition",
        token_id="token-1",
        outcome_label="YES",
        event_type="BOOK_SNAPSHOT",
        quote_seen_at="2026-05-24T18:07:00+00:00",
        book_hash="hash-1",
    )
    return make_opportunity_event(
        event_type="BOOK_SNAPSHOT",
        entity_key="0xcondition|token-1",
        source="polymarket_market_channel",
        observed_at=payload.quote_seen_at,
        available_at=payload.quote_seen_at,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
        causal_snapshot_id="hash-1",
    )


def test_forecast_wake_events_follow_posterior_reversal_order():
    paris = SimpleNamespace(
        event_id="paris",
        payload_json=json.dumps(
            {"city": "Paris", "target_date": "2026-07-18", "metric": "high"}
        ),
    )
    shanghai = SimpleNamespace(
        event_id="shanghai",
        payload_json=json.dumps(
            {"city": "Shanghai", "target_date": "2026-07-18", "metric": "high"}
        ),
    )
    ordinary = SimpleNamespace(
        event_id="ordinary",
        payload_json=json.dumps(
            {"city": "London", "target_date": "2026-07-18", "metric": "high"}
        ),
    )

    ranked = _rank_forecast_wake_events(
        [ordinary, paris, shanghai],
        [
            ("Shanghai", "2026-07-18", "high"),
            ("Paris", "2026-07-18", "high"),
        ],
    )

    assert [event.event_id for event in ranked] == [
        "shanghai",
        "paris",
        "ordinary",
    ]


def _reactor(store, *, gates=True, config=None):
    rejected = []
    submitted = []
    def _submit(event, _decision_time):
        payload = json.loads(event.payload_json)
        submitted.append(event.event_id)
        receipt = EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
        )
        return replace(
            receipt,
            decision_proof_bundle=build_test_no_submit_proof_bundle(
                event,
                receipt,
                decision_time=_decision_time,
            ),
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: gates,
        executable_snapshot_gate=lambda _event, _decision_time: gates,
        riskguard_gate=lambda _event: gates,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=config or ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    return reactor, rejected, submitted


def _global_batch_probe_reactor(
    store, observations, *, incomplete=False, next_claim_event=None
):
    def _direct_submit(*_args, **_kwargs):
        observations["direct_submit_calls"] += 1
        raise AssertionError("global batch path must not invoke per-event submit")

    def _process_global_batch(
        events,
        _decision_time,
        *,
        claim_unpaged_winner=None,
    ):
        observations["batch_calls"] += 1
        observations["batch_event_ids"] = tuple(event.event_id for event in events)
        observations["mutex_locked_at_batch"] = world_write_mutex().locked()
        observations["world_conn_in_txn_at_batch"] = bool(store.conn.in_transaction)
        observations["claimed_statuses_at_batch"] = tuple(
            _processing_status(store.conn, event.event_id) for event in events
        )
        receipts = {
            event.event_id: EventSubmissionReceipt(
                submitted=False,
                event_id=event.event_id,
                causal_snapshot_id=event.causal_snapshot_id,
                reason="SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_NO_CURRENT_WINNER",
                proof_accepted=False,
            )
            for event in events
        }
        if incomplete:
            receipts.pop(events[-1].event_id)
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=None,
            venue_submit_count=0,
            next_claim_event=next_claim_event,
        )

    observations.update(direct_submit_calls=0, batch_calls=0)
    _direct_submit.process_global_batch = _process_global_batch  # type: ignore[attr-defined]
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_direct_submit,
        reject=lambda *_args: None,
        config=ReactorConfig(reactor_mode="live_no_submit"),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )


def _terminal_surfaces(conn: sqlite3.Connection, event_id: str) -> dict[str, int]:
    verified_no_submit = conn.execute(
        """
        SELECT COUNT(*)
        FROM edli_no_submit_receipts AS receipt
        JOIN decision_certificates AS cert
          ON cert.certificate_type = 'NoSubmitDecisionCertificate'
         AND cert.verifier_status = 'VERIFIED'
         AND json_extract(cert.payload_json, '$.event_id') = receipt.event_id
         AND json_extract(cert.payload_json, '$.projection_hash') = receipt.projection_hash
        WHERE receipt.event_id = ?
        """,
        (event_id,),
    ).fetchone()[0]
    compile_failure = conn.execute(
        "SELECT COUNT(*) FROM decision_compile_failures WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]
    regret = conn.execute(
        "SELECT COUNT(*) FROM no_trade_regret_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]
    dead_letter = conn.execute(
        "SELECT COUNT(*) FROM event_dead_letters WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]
    execution_receipt = conn.execute(
        """
        SELECT COUNT(*)
        FROM decision_certificates
        WHERE certificate_type = 'ExecutionReceiptCertificate'
          AND verifier_status = 'VERIFIED'
          AND json_extract(payload_json, '$.event_id') = ?
        """,
        (event_id,),
    ).fetchone()[0]
    return {
        "verified_no_submit": verified_no_submit,
        "execution_receipt": execution_receipt,
        "compile_failure": compile_failure,
        "regret": regret,
        "dead_letter": dead_letter,
    }


def test_event_cannot_bypass_source_truth():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    reactor, rejected, submitted = _reactor(store, gates=False)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.rejected == 1
    assert rejected[0][1] == "SOURCE_TRUTH"
    assert submitted == []


def test_market_channel_event_not_direct_reactor_input():
    _conn, store = _store()
    event = _market_event()
    store.insert_or_ignore(event)
    reactor, rejected, submitted = _reactor(store)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.rejection_reasons == []
    assert result.processed == 0
    assert result.rejected == 0
    assert rejected == []
    assert submitted == []
    assert _processing_status(_conn, event.event_id) == "ignored"


def test_global_batch_claims_epoch_then_calls_one_lock_free_batch_seam():
    conn, store = _store()
    events = (
        _forecast_event("global-a", target_date="2026-05-25"),
        _forecast_event("global-b", target_date="2026-05-25"),
    )
    for event in events:
        store.insert_or_ignore(event)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=2)

    assert observations["batch_calls"] == 1
    assert observations["direct_submit_calls"] == 0
    assert set(observations["batch_event_ids"]) == {event.event_id for event in events}
    assert observations["mutex_locked_at_batch"] is False
    assert observations["world_conn_in_txn_at_batch"] is False
    assert observations["claimed_statuses_at_batch"] == ("processing", "processing")
    assert result.retried == 2
    assert all(_processing_status(conn, event.event_id) == "pending" for event in events)
    assert {
        row[0]
        for row in conn.execute(
            "SELECT last_error FROM opportunity_event_processing "
            "WHERE event_id IN (?, ?)",
            tuple(event.event_id for event in events),
        )
    } == {"SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_NO_CURRENT_WINNER"}


def test_global_batch_targeted_wake_claims_only_committed_event():
    conn, store = _store()
    ordinary = _forecast_event("global-ordinary", target_date="2026-05-25")
    committed = _forecast_event("global-committed", target_date="2026-05-25")
    store.insert_or_ignore(ordinary)
    store.insert_or_ignore(committed)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    reactor.process_pending(
        decision_time=_DT_VENUE_OPEN,
        limit=12,
        targeted_event_ids=frozenset({committed.event_id}),
        targeted_only=True,
    )

    assert observations["batch_event_ids"] == (committed.event_id,)
    assert _processing_status(conn, ordinary.event_id) == "pending"


@pytest.mark.parametrize("winner_finalized", (True, False))
def test_global_batch_prioritizes_venue_side_effect_and_stops_repeated_waits(
    monkeypatch, winner_finalized
):
    conn, store = _store()
    events = tuple(
        _forecast_event(f"lock-priority-{index}", target_date="2026-05-25")
        for index in range(3)
    )
    for event in events:
        store.insert_or_ignore(event)
    reactor = _global_batch_probe_reactor(store, {})
    winner = events[-1]

    def _batch(events, _decision_time, *, claim_unpaged_winner=None):
        receipts = {
            event.event_id: EventSubmissionReceipt(
                submitted=event.event_id == winner.event_id,
                event_id=event.event_id,
                causal_snapshot_id=event.causal_snapshot_id,
                side_effect_status=(
                    "VENUE_SUBMIT_ACKED"
                    if event.event_id == winner.event_id
                    else "NO_SUBMIT"
                ),
                venue_call_started=event.event_id == winner.event_id,
                venue_ack_received=event.event_id == winner.event_id,
                reason="TEST_RECEIPT",
            )
            for event in events
        }
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=winner.event_id,
            venue_submit_count=1,
        )

    reactor._submit.process_global_batch = _batch
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "123")
    calls = []

    def _finalize(event, receipt, *, decision_time, result, wait_ms=None):
        calls.append((event.event_id, receipt.side_effect_status, wait_ms))
        if event.event_id == winner.event_id and winner_finalized:
            return True
        result.rejection_reasons.append("WORLD_WRITE_LOCK_BUSY_POST_SUBMIT")
        result.retried += 1
        return False

    reactor._finalize_deferred_event_unit = _finalize

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=3)

    assert calls == (
        [
            (winner.event_id, "VENUE_SUBMIT_ACKED", None),
            (events[0].event_id, "NO_SUBMIT", 123),
        ]
        if winner_finalized
        else [(winner.event_id, "VENUE_SUBMIT_ACKED", None)]
    )
    assert result.retried == (2 if winner_finalized else 3)
    assert result.rejection_reasons == [
        "WORLD_WRITE_LOCK_BUSY_POST_SUBMIT"
    ] * result.retried
    assert _processing_status(conn, events[1].event_id) == "processing"


def test_global_batch_incomplete_receipt_coverage_fails_closed_for_whole_epoch():
    conn, store = _store()
    events = (
        _forecast_event("incomplete-a", target_date="2026-05-25"),
        _forecast_event("incomplete-b", target_date="2026-05-25"),
    )
    for event in events:
        store.insert_or_ignore(event)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations, incomplete=True)

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=2)

    assert observations["batch_calls"] == 1
    assert observations["direct_submit_calls"] == 0
    assert result.proof_accepted == 0
    assert result.retried == 2
    assert all(_processing_status(conn, event.event_id) == "pending" for event in events)


def test_global_batch_materializes_unclaimed_winner_as_next_claim():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("claimed", target_date="2026-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="current-batch-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    observations = {}
    reactor = _global_batch_probe_reactor(
        store,
        observations,
        next_claim_event=target,
    )

    first = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert first.retried == 1
    assert _processing_status(conn, target.event_id) == "pending"
    row = conn.execute(
        "SELECT last_error FROM opportunity_event_processing WHERE event_id = ?",
        (target.event_id,),
    ).fetchone()
    assert row[0] == "GLOBAL_WINNER_TARGETED_CLAIM"
    assert store.fetch_pending(
        decision_time=_DT_VENUE_OPEN.isoformat(), limit=1
    )[0].event_id == target.event_id


def test_global_batch_claims_unpaged_winner_inside_same_epoch():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("same-epoch-owner", target_date="2099-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="same-epoch-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    def _same_epoch_batch(events, _decision_time, *, claim_unpaged_winner):
        assert tuple(event.event_id for event in events) == (claimed.event_id,)
        assert claim_unpaged_winner(target) is True
        assert _processing_status(conn, target.event_id) == "processing"
        receipts = {
            event.event_id: EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_RETRY",
                proof_accepted=False,
            )
            for event in (claimed, target)
        }
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=target.event_id,
            venue_submit_count=0,
        )

    reactor._submit.process_global_batch = _same_epoch_batch
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.retried == 2
    assert _processing_status(conn, claimed.event_id) == "pending"
    assert _processing_status(conn, target.event_id) == "pending"


def test_global_batch_recovers_unpaged_claim_when_batch_raises():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("same-epoch-error", target_date="2099-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="same-epoch-error-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    def _raise_after_claim(events, _decision_time, *, claim_unpaged_winner):
        assert claim_unpaged_winner(target) is True
        raise RuntimeError("post-claim test failure")

    reactor._submit.process_global_batch = _raise_after_claim
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.retried == 2
    assert _processing_status(conn, claimed.event_id) == "pending"
    assert _processing_status(conn, target.event_id) == "pending"
    assert conn.execute(
        "SELECT COUNT(*) FROM opportunity_event_processing "
        "WHERE event_id IN (?, ?) AND processing_status='processing'",
        (claimed.event_id, target.event_id),
    ).fetchone()[0] == 0


def test_same_epoch_winner_claim_rejects_changed_batch_generation():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("same-epoch-race", target_date="2099-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="same-epoch-race-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    actual_generation = "2026-05-25T06:10:01+00:00"
    assert store.claim(claimed.event_id, claimed_at=actual_generation)
    conn.commit()
    reactor = _global_batch_probe_reactor(store, {})

    reactor_result = ReactorResult()
    result, lock_bounced = reactor._claim_global_winner_for_actuation(
        target,
        current_batch_claim_generations={
            claimed.event_id: "2026-05-25T06:10:00+00:00"
        },
        result=reactor_result,
    )

    assert result is None
    assert lock_bounced is False
    assert reactor_result.claim_lock_bounces == 0
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() is None
    assert conn.execute(
        "SELECT claimed_at FROM opportunity_event_processing WHERE event_id = ?",
        (claimed.event_id,),
    ).fetchone()[0] == actual_generation


def test_global_winner_claim_mutex_busy_is_bounded(monkeypatch):
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("winner-mutex-busy", target_date="2099-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="winner-mutex-busy-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    actual_generation = "2026-05-25T06:10:00+00:00"
    assert store.claim(claimed.event_id, claimed_at=actual_generation)
    conn.commit()
    reactor = _global_batch_probe_reactor(store, {})
    waits: list[float] = []

    class _BusyMutex:
        def acquire(self, *, timeout):
            waits.append(timeout)
            return False

        def release(self):
            pytest.fail("an unacquired mutex must not be released")

    monkeypatch.setattr(
        "src.events.reactor.world_write_mutex",
        lambda: _BusyMutex(),
    )

    reactor_result = ReactorResult()
    result, lock_bounced = reactor._claim_global_winner_for_actuation(
        target,
        current_batch_claim_generations={claimed.event_id: actual_generation},
        result=reactor_result,
    )

    assert result is None
    assert lock_bounced is True
    assert waits == [pytest.approx(0.75)]
    assert reactor_result.claim_lock_bounces == 1
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() is None


def test_global_claim_lock_bounce_retries_same_winner_before_reauction(
    tmp_path, monkeypatch, caplog
):
    """A bounced claim is queued, then the exact target is evaluated next cycle."""

    from src.engine.global_batch_runtime import _next_claim_carrier

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    init_schema(conn)
    store = EventStore(conn)
    base = _forecast_event("global-composed-lock", target_date="2099-05-25")
    target = _next_claim_carrier(
        base,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="global-composed-lock-economic-identity",
        payload=json.loads(base.payload_json),
    )
    store.insert_or_ignore(base)
    conn.commit()
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "100")
    queue_calls = 0
    queue_waits = []
    real_queue = reactor._queue_global_winner_for_claim

    def _count_queue(*args, **kwargs):
        nonlocal queue_calls
        queue_calls += 1
        queue_waits.append(kwargs.get("wait_ms"))
        return real_queue(*args, **kwargs)

    reactor._queue_global_winner_for_claim = _count_queue
    batch_attempt = 0

    def _locked_then_recovered_batch(
        events, _decision_time, *, claim_unpaged_winner
    ):
        nonlocal batch_attempt
        batch_attempt += 1
        if batch_attempt == 1:
            blocker = sqlite3.connect(str(db_path), timeout=30.0)
            blocker.execute("PRAGMA busy_timeout = 30000")
            blocker.execute("BEGIN IMMEDIATE")
            try:
                claimed = claim_unpaged_winner(target)
            finally:
                blocker.rollback()
                blocker.close()
        else:
            assert tuple(event.event_id for event in events) == (target.event_id,)
            return GlobalBatchSubmitResult(
                receipts={
                    target.event_id: EventSubmissionReceipt(
                        False,
                        target.event_id,
                        target.causal_snapshot_id,
                        reason="TRADE_SCORE_NON_POSITIVE",
                        proof_accepted=False,
                    )
                },
                winner_event_id=None,
                venue_submit_count=0,
            )
        receipt_events = (*events, target) if claimed else events
        reason = "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM"
        return GlobalBatchSubmitResult(
            receipts={
                event.event_id: EventSubmissionReceipt(
                    False,
                    event.event_id,
                    event.causal_snapshot_id,
                    reason=reason,
                    proof_accepted=False,
                )
                for event in receipt_events
            },
            winner_event_id=target.event_id if claimed else None,
            venue_submit_count=0,
            next_claim_event=None if claimed else target,
        )

    reactor._submit.process_global_batch = _locked_then_recovered_batch

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="zeus.events.reactor"):
        first = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"composed global claim bounce waited too long: {elapsed:.3f}s"
    assert first.claim_lock_bounces == 1
    assert first.retried == 1
    assert queue_calls == 1
    assert queue_waits == [None]
    assert observations["direct_submit_calls"] == 0
    assert "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM" in TRANSIENT_MONEY_PATH_REASONS
    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    assert not conn.in_transaction
    assert _processing_status(conn, base.event_id) == "pending"
    assert conn.execute(
        "SELECT last_error FROM opportunity_event_processing WHERE event_id = ?",
        (base.event_id,),
    ).fetchone()[0] == "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM"
    assert _processing_status(conn, target.event_id) == "pending"
    assert conn.execute(
        "SELECT last_error FROM opportunity_event_processing WHERE event_id = ?",
        (target.event_id,),
    ).fetchone()[0] == "GLOBAL_WINNER_TARGETED_CLAIM"

    second = reactor.process_pending(
        decision_time=_DT_VENUE_OPEN + timedelta(minutes=1),
        limit=1,
    )

    assert batch_attempt == 2
    assert second.processed == 1
    assert second.rejected == 1
    assert second.proof_accepted == 0
    assert second.retried == 0
    assert _processing_status(conn, target.event_id) == "processed"


def test_global_batch_defers_target_when_claim_is_reclaimed_during_solve():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("claimed-then-reclaimed", target_date="2026-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="reclaimed-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    observations = {}
    reactor = _global_batch_probe_reactor(
        store,
        observations,
        next_claim_event=target,
    )
    process_batch = reactor._submit.process_global_batch

    def _reclaim_then_solve(events, decision_time, **kwargs):
        assert store.claim(
            events[0].event_id,
            claimed_at="2026-05-25T06:16:00+00:00",
        )
        conn.commit()
        return process_batch(events, decision_time, **kwargs)

    reactor._submit.process_global_batch = _reclaim_then_solve

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.proof_accepted == 0
    assert result.retried == 1
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() is None
    assert conn.execute(
        "SELECT last_error FROM opportunity_event_processing WHERE event_id = ?",
        (claimed.event_id,),
    ).fetchone()[0] == "SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_NO_CURRENT_WINNER"


def test_global_target_keeps_claim_priority_after_transient_epoch():
    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    base = _forecast_event("target-retry", target_date="2026-05-25")
    target = _next_claim_carrier(
        base,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="test-economic-identity",
        payload=json.loads(base.payload_json),
    )
    assert store.prioritize_global_winner(target)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.retried == 1
    assert result.rejection_reasons == [
        "SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_NO_CURRENT_WINNER"
    ]
    row = conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (target.event_id,),
    ).fetchone()
    assert tuple(row) == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")
    assert store.fetch_pending(
        decision_time=_DT_VENUE_OPEN.isoformat(), limit=1
    )[0].event_id == target.event_id


def test_global_target_is_visible_beyond_old_pending_active_scan_window():
    """A fresh targeted winner cannot starve behind more than 20k old rows."""

    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    base = _forecast_event("target-large-backlog", target_date="2026-05-25")
    target = _next_claim_carrier(
        base,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="large-backlog-economic-identity",
        payload=json.loads(base.payload_json),
    )
    assert store.prioritize_global_winner(target)

    target_row = tuple(
        conn.execute(
            "SELECT * FROM opportunity_events WHERE event_id = ?", (target.event_id,)
        ).fetchone()
    )
    event_rows = []
    processing_rows = []
    for index in range(20_002):
        event_id = f"old-backlog-{index:05d}"
        row = list(target_row)
        row[0] = event_id
        row[2] = f"Chicago|2026-05-25|high|old-{index:05d}"
        row[3] = f"old-backlog-source-{index:05d}"
        row[8] = f"old-payload-{index:05d}"
        row[9] = f"old-idempotency-{index:05d}"
        event_rows.append(tuple(row))
        processing_rows.append(
            (
                store.consumer_name,
                event_id,
                "pending",
                0,
                None,
                None,
                None,
                "2026-01-01T00:00:00+00:00",
            )
        )
    conn.executemany(
        "INSERT INTO opportunity_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        event_rows,
    )
    conn.executemany(
        "INSERT INTO opportunity_event_processing VALUES (?,?,?,?,?,?,?,?)",
        processing_rows,
    )
    conn.commit()

    fetched = store.fetch_pending(
        decision_time=_DT_VENUE_OPEN.isoformat(), limit=1
    )

    assert [event.event_id for event in fetched] == [target.event_id]


def test_latest_global_target_crosses_a_full_page_of_older_family_targets():
    """Only the latest global winner owns the next-claim lane across families."""

    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    older = []
    for index in range(13):
        base = _forecast_event(
            f"older-global-target-{index}",
            target_date=f"2026-06-{index + 1:02d}",
        )
        target = _next_claim_carrier(
            base,
            targeted_at=_DT_VENUE_OPEN + timedelta(seconds=index),
            economic_identity=f"older-economic-identity-{index}",
            payload=json.loads(base.payload_json),
        )
        assert store.prioritize_global_winner(target)
        older.append(target)

    base = _forecast_event("latest-global-target", target_date="2026-05-25")
    latest = _next_claim_carrier(
        base,
        targeted_at=_DT_VENUE_OPEN + timedelta(seconds=13),
        economic_identity="latest-economic-identity",
        payload=json.loads(base.payload_json),
    )
    assert store.prioritize_global_winner(latest)

    # Mirror the live ordering: finalization touches a full claimed page after
    # materializing the new target, making the older targets newer by updated_at.
    for index, target in enumerate(older[:12], start=1):
        conn.execute(
            "UPDATE opportunity_event_processing SET updated_at = ? WHERE event_id = ?",
            (
                (_DT_VENUE_OPEN + timedelta(minutes=1, seconds=index)).isoformat(),
                target.event_id,
            ),
        )

    fetched = store.fetch_pending(
        decision_time=(_DT_VENUE_OPEN + timedelta(minutes=2)).isoformat(),
        limit=12,
    )

    assert fetched[0].event_id == latest.event_id
    assert latest.event_id not in {event.event_id for event in fetched[1:]}


def test_global_target_does_not_preempt_stale_processing_recovery():
    conn, store = _store()
    stale = _day0_event("stale")
    target = _forecast_event("target", target_date="2026-05-24")
    store.insert_or_ignore(target)
    assert store.prioritize_global_winner(target)
    store.insert_or_ignore(stale)
    assert store.claim(
        stale.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )

    first = store.fetch_pending(
        decision_time=_DT_VENUE_OPEN.isoformat(),
        limit=1,
    )

    assert [event.event_id for event in first] == [stale.event_id]


def test_global_reactor_keeps_stale_day0_ahead_of_targeted_forecast():
    conn, store = _store()
    stale = _day0_event("stale-reactor")
    target = _forecast_event("target-reactor", target_date="2026-05-24")
    assert store.prioritize_global_winner(target)
    store.insert_or_ignore(stale)
    assert store.claim(
        stale.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert observations["batch_event_ids"] == (stale.event_id,)


def test_global_target_atomically_supersedes_only_older_pending_targets():
    conn, store = _store()
    old = _day0_event("old-target")
    new = _forecast_event("new-target", target_date="2026-05-24")
    unrelated = _forecast_event("unrelated", target_date="2026-05-24")
    assert store.prioritize_global_winner(old)
    store.insert_or_ignore(unrelated)

    assert store.prioritize_global_winner(new)

    states = {
        event_id: (status, reason)
        for event_id, status, reason in conn.execute(
            "SELECT event_id, processing_status, last_error "
            "FROM opportunity_event_processing"
        )
    }
    assert states[old.event_id] == (
        "expired",
        "GLOBAL_WINNER_TARGET_SUPERSEDED",
    )
    assert states[new.event_id] == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")
    assert states[unrelated.event_id] == ("pending", None)


def test_global_target_processing_lease_blocks_new_target_materialization():
    conn, store = _store()
    inflight = _forecast_event("inflight-target", target_date="2026-05-25")
    new = _forecast_event("new-target", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:09:00+00:00",
    )

    assert store.prioritize_global_winner(new) is False

    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (inflight.event_id,),
    ).fetchone() == ("processing", None)
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (new.event_id,),
    ).fetchone() is None


def test_boot_generation_requeues_only_prior_runtime_claims():
    conn, store = _store()
    old = _forecast_event("prior-runtime", target_date="2026-05-25")
    current = _forecast_event("current-runtime", target_date="2026-05-26")
    target = _forecast_event("prior-target", target_date="2026-05-27")
    for event in (old, current, target):
        store.insert_or_ignore(event)
    assert store.claim(old.event_id, claimed_at="2026-05-24T18:00:00+00:00")
    assert store.claim(current.event_id, claimed_at="2026-05-24T18:10:00+00:00")
    store.requeue_pending(target.event_id, last_error="GLOBAL_WINNER_TARGETED_CLAIM")
    assert store.claim(target.event_id, claimed_at="2026-05-24T18:01:00+00:00")

    assert (
        store.requeue_processing_before_boot(
            boot_at="2026-05-24T18:05:00+00:00"
        )
        == 2
    )

    states = {
        event_id: (status, claimed_at, reason)
        for event_id, status, claimed_at, reason in conn.execute(
            "SELECT event_id, processing_status, claimed_at, last_error "
            "FROM opportunity_event_processing"
        )
    }
    assert states[old.event_id] == ("pending", None, "PROCESS_OWNER_RESTARTED")
    assert states[target.event_id] == (
        "pending",
        None,
        "GLOBAL_WINNER_TARGETED_CLAIM",
    )
    assert states[current.event_id] == (
        "processing",
        "2026-05-24T18:10:00+00:00",
        None,
    )


def test_global_target_allows_only_current_batch_processing_lease():
    conn, store = _store()
    inflight = _forecast_event("inflight-current-batch", target_date="2026-05-25")
    new = _forecast_event("new-current-batch", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:09:00+00:00",
    )
    conn.commit()
    generations = {inflight.event_id: "2026-05-24T18:09:00+00:00"}

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        new,
        current_batch_claim_generations=generations,
    )
    conn.commit()
    states = {
        event_id: (status, reason)
        for event_id, status, reason in conn.execute(
            "SELECT event_id, processing_status, last_error "
            "FROM opportunity_event_processing"
        )
    }
    assert states[inflight.event_id][0] == "processing"
    assert states[new.event_id] == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")


def test_global_target_rejects_unowned_processing_lease_beside_current_batch():
    conn, store = _store()
    owned = _forecast_event("owned-current-batch", target_date="2026-05-25")
    external = _forecast_event("external-worker", target_date="2026-05-25")
    new = _forecast_event("new-mixed-lease", target_date="2026-05-25")
    for event in (owned, external):
        store.insert_or_ignore(event)
        assert store.claim(
            event.event_id,
            claimed_at="2026-05-24T18:09:00+00:00",
        )
    conn.commit()
    owned_generation = {owned.event_id: "2026-05-24T18:09:00+00:00"}

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        new,
        current_batch_claim_generations=owned_generation,
    ) is False
    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (new.event_id,),
    ).fetchone() is None


def test_global_target_rejects_stale_claim_generation_after_aba_reclaim():
    conn, store = _store()
    inflight = _forecast_event("inflight-aba", target_date="2026-05-25")
    new = _forecast_event("new-aba", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:06:00+00:00",
    )
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        new,
        current_batch_claim_generations={
            inflight.event_id: "2026-05-24T18:00:00+00:00"
        },
    ) is False
    conn.rollback()
    assert conn.execute(
        "SELECT claimed_at FROM opportunity_event_processing WHERE event_id = ?",
        (inflight.event_id,),
    ).fetchone()[0] == "2026-05-24T18:06:00+00:00"
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (new.event_id,),
    ).fetchone() is None


def test_global_target_commit_before_finalize_is_side_effect_free_and_reclaimable():
    conn, store = _store()
    inflight = _forecast_event("inflight-crash", target_date="2026-05-25")
    target = _forecast_event("target-after-crash", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-25T06:09:00+00:00",
    )
    conn.commit()
    generations = {inflight.event_id: "2026-05-25T06:09:00+00:00"}
    no_submit = GlobalBatchSubmitResult(
        receipts={
            inflight.event_id: EventSubmissionReceipt(
                False,
                inflight.event_id,
                inflight.causal_snapshot_id,
                reason="GLOBAL_TARGET_HANDOFF",
                proof_accepted=False,
            )
        },
        winner_event_id=None,
        venue_submit_count=0,
        next_claim_event=target,
    )

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        target,
        current_batch_claim_generations=generations,
    )
    conn.commit()
    fetched = store.fetch_pending(
        decision_time="2026-05-25T06:10:00+00:00",
        limit=1,
    )

    assert no_submit.venue_submit_count == 0
    assert not any(receipt.submitted for receipt in no_submit.receipts.values())
    assert _processing_status(conn, inflight.event_id) == "processing"
    assert [event.event_id for event in fetched] == [target.event_id]


def test_global_target_rejects_capability_that_left_processing():
    conn, store = _store()
    inflight = _forecast_event("inflight-disappeared", target_date="2026-05-25")
    target = _forecast_event("target-after-disappearance", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )
    old_capability = {inflight.event_id: "2026-05-24T18:00:00+00:00"}
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:06:00+00:00",
    )
    store.requeue_pending(inflight.event_id, last_error="TRANSIENT_NEW_OWNER")
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        target,
        current_batch_claim_generations=old_capability,
    ) is False
    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() is None


def test_global_batch_result_rejects_more_than_one_submit_or_wrong_winner():
    first = EventSubmissionReceipt(True, "first", side_effect_status="ACKED")
    second = EventSubmissionReceipt(True, "second", side_effect_status="ACKED")
    with pytest.raises(ValueError, match="at most one venue submit"):
        GlobalBatchSubmitResult(
            receipts={"first": first, "second": second},
            winner_event_id="first",
            venue_submit_count=2,
        )
    with pytest.raises(ValueError, match="submitted receipt must be the one global winner"):
        GlobalBatchSubmitResult(
            receipts={"first": first},
            winner_event_id=None,
            venue_submit_count=0,
        )


def _retry_reactor(store, snapshot_present: dict):
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: snapshot_present["v"],
        riskguard_gate=lambda _e: True,
        final_intent_submit=lambda _e, _dt: None,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )


def test_executable_snapshot_block_is_retryable_not_consumed_then_processes_after_capture():
    """A snapshot-block is TRANSIENT: the event is requeued (stays 'pending') rather than
    marked processed, so once the family's snapshots are captured a later cycle re-evaluates
    it instead of losing it. This is the #42b fix for the live reactor never running the kernel.
    """
    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    present = {"v": False}
    reactor = _retry_reactor(store, present)
    dt = _DT_VENUE_OPEN

    def _status():
        return conn.execute(
            "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]

    # 12 timely retries (well past the old cap of 8): the event requeues, never
    # consumed by an attempt count (operator law 2026-06-12: no caps). Snapshot
    # blocks use a retry floor, so advance to the stored not_before each cycle.
    for _ in range(12):
        result = reactor.process_pending(decision_time=dt)
        assert result.processed == 0
        assert result.dead_lettered == 0
        assert result.retried == 1
        assert _status() == "pending"  # retryable, NOT consumed, NO cap
        retry_floor = conn.execute(
            "SELECT claimed_at FROM opportunity_event_processing WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]
        dt = datetime.fromisoformat(retry_floor).astimezone(timezone.utc) + timedelta(seconds=1)

    present["v"] = True
    result = reactor.process_pending(decision_time=dt)
    assert result.processed == 1
    assert _status() == "processed"


def test_executable_snapshot_block_terminalizes_at_timeliness_horizon():
    """REWRITTEN 2026-06-12 (operator law "no caps"): an uncapturable snapshot is
    NOT dead-lettered by attempt count. While the event is timely it requeues
    indefinitely; it terminalizes only when its EVENT HORIZON (timeliness floor)
    has passed — labeled MONEY_PATH_HORIZON_EXPIRED."""
    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    present = {"v": False}  # never captured
    reactor = _retry_reactor(store, present)
    dt = _DT_VENUE_OPEN  # venue-open (SETTLEMENT_DAY): requeues while still tradeable

    def _status():
        return conn.execute(
            "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]

    # Many timely cycles: never dead-letters by count.
    for _ in range(12):
        reactor.process_pending(decision_time=dt)
        assert _status() == "pending"

    # Market horizon passes (Chicago 2026-05-24: the F1 12:00-UTC venue close is the
    # earliest horizon; the local-day floor is 2026-05-25T05:00Z). Drive the requeue
    # disposition at a past time to assert the explicit horizon terminal (in
    # production the read floor + archive sweep also reclaim it).
    from src.events.reactor import ReactorResult

    reactor._transient_requeue_reasons[event.event_id] = "EXECUTABLE_SNAPSHOT_BLOCKED"
    horizon_past = datetime(2026, 5, 26, 6, 0, tzinfo=timezone.utc)
    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=horizon_past,
        result=res,
    )
    assert res.dead_lettered == 1
    assert _status() == "dead_letter"
    row = conn.execute(
        "SELECT failure_stage FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row[0] == "MONEY_PATH_HORIZON_EXPIRED"


def test_source_captured_after_decision_time_is_retryable_not_consumed():
    """The forecast-source re-ingestion race (SOURCE_CAPTURED_AFTER_DECISION_TIME) is TRANSIENT:
    the event is requeued and retried next cycle (decision_time advances past the source's
    available time) rather than consumed at the money-path stage. Mirrors the snapshot retry.
    """
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            reason="LIVE_INFERENCE_INPUTS_MISSING:FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:SOURCE_CAPTURED_AFTER_DECISION_TIME",
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.retried == 1
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "pending"


def test_stale_executable_snapshot_receipt_is_retryable_not_consumed():
    """A selected executable price can expire between pre-submit identity gating and JIT scoring.
    That is a transient market-data freshness race, not a terminal trade-score failure.
    """
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            reason=(
                "EXECUTABLE_SNAPSHOT_STALE:"
                "freshness_deadline=2026-05-24T06:09:59+00:00:"
                "decision_time=2026-05-24T06:10:00+00:00"
            ),
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    assert result.rejection_reasons == [
        "EXECUTABLE_SNAPSHOT_STALE:"
        "freshness_deadline=2026-05-24T06:09:59+00:00:"
        "decision_time=2026-05-24T06:10:00+00:00"
    ]
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "pending"


def test_sqlite_lock_during_live_certificate_build_is_retryable_not_consumed():
    """SQLite writer contention during live certificate construction is transient.

    The event must stay pending for the next cycle; non-lock certificate failures
    remain terminal through the existing rejection path.
    """
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            reason="EDLI_LIVE_CERTIFICATE_BUILD_FAILED:database is locked",
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    assert _processing_status(conn, event.event_id) == "pending"


def test_live_book_authority_gap_requeues_with_selected_leg_identity():
    """A pre-submit book authority gap is a retryable execution-expression deferral.

    The adapter may have already selected a qkernel/Kelly leg before the final
    command certificate fails. The reactor must keep the event pending, while
    writing a token-bearing regret/deferral row so the price sidecar can pin and
    seed exactly that token before the next attempt.
    """
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="token-selected",
            outcome_label="YES",
            executable_snapshot_id="exec-selected",
            family_id="family-1",
            bin_label="80F",
            direction="buy_yes",
            q_live=0.71,
            q_lcb_5pct=0.62,
            c_fee_adjusted=0.40,
            c_cost_95pct=0.42,
            p_fill_lcb=0.55,
            trade_score=0.22,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=3,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=4.0,
            kelly_cost_basis_id="cost-1",
            final_intent_id="intent-1",
            reason="EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_MISSING",
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    row = conn.execute(
        """
        SELECT rejection_stage, rejection_reason, token_id, bin_label, direction
        FROM no_trade_regret_events
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert row == (
        "EXECUTOR_EXPRESSIBILITY",
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_MISSING",
        "token-selected",
        "80F",
        "buy_yes",
    )
    assert _processing_status(conn, event.event_id) == "pending"


def test_sqlite_lock_during_post_submit_begin_is_retryable_not_dead_lettered(
    tmp_path, monkeypatch
):
    """A Window-B BEGIN IMMEDIATE lock is transient and cannot write evidence.

    The event is already claimed/committed as ``processing`` after Window A.
    When another writer holds the WAL write lock before Window B starts, the
    reactor must not try to write dead-letter/ledger rows through the same lock.
    Leaving the processing lease in place lets fetch_pending retry it once the
    lease is stale.
    """
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path, timeout=0)
    init_schema(conn)
    store = EventStore(conn)
    event = _forecast_event()
    store.insert_or_ignore(event)
    locker_holder: dict[str, sqlite3.Connection] = {}
    payload = json.loads(event.payload_json)
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "100")
    from src.events import reactor as reactor_module

    real_scoped_timeout = reactor_module._scoped_sqlite_busy_timeout
    observed_timeouts = []

    @contextmanager
    def _tracked_timeout(conn, timeout_ms):
        observed_timeouts.append(timeout_ms)
        with real_scoped_timeout(conn, timeout_ms):
            yield

    monkeypatch.setattr(reactor_module, "_scoped_sqlite_busy_timeout", _tracked_timeout)

    def _submit(_event, decision_time):
        locker = sqlite3.connect(db_path, timeout=0)
        locker.execute("BEGIN IMMEDIATE")
        locker_holder["conn"] = locker
        receipt = EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
        )
        return replace(
            receipt,
            decision_proof_bundle=build_test_no_submit_proof_bundle(
                event,
                receipt,
                decision_time=decision_time,
            ),
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    locker_holder["conn"].rollback()
    locker_holder["conn"].close()

    assert result.processed == 0
    assert result.dead_lettered == 0
    assert result.retried == 1
    assert result.rejection_reasons == ["WORLD_WRITE_LOCK_BUSY_POST_SUBMIT"]
    assert observed_timeouts[-2:] == [100, 0]
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    assert _processing_status(conn, event.event_id) == "processing"


def test_sqlite_lock_during_pre_submit_gate_is_retryable_not_dead_lettered():
    """A Window-A lock before any venue submit must leave the event retryable."""

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)

    def _locked_source_truth(_event):
        raise sqlite3.OperationalError("database is locked")

    def _submit(_event, _decision_time):
        raise AssertionError("pre-submit lock must not reach submit")

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=_locked_source_truth,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.dead_lettered == 0
    assert result.retried == 1
    assert result.rejection_reasons == ["WORLD_WRITE_LOCK_BUSY_PRE_SUBMIT"]
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    assert _processing_status(conn, event.event_id) == "pending"


def test_stale_unbound_executable_snapshot_receipt_is_retryable_not_consumed():
    """Stale JIT price failures may return before the adapter can build a bound final intent."""
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id="stale-exec-failed-before-bound-proof",
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=True,
            reason=(
                "EXECUTABLE_SNAPSHOT_STALE:"
                "freshness_deadline=2026-05-24T06:09:59+00:00:"
                "decision_time=2026-05-24T06:10:00+00:00"
            ),
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "pending"


def test_stale_bound_receipt_on_executor_reject_path_is_retryable_not_consumed():
    """Any post-submit stale executable-price reason is transient, independent of reject branch."""
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
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
            side_effect_status="PRE_SUBMIT_ERROR",
            reason=(
                "EXECUTABLE_SNAPSHOT_STALE:"
                "freshness_deadline=2026-05-24T18:09:59+00:00:"
                "decision_time=2026-05-24T18:10:00+00:00"
            ),
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(real_order_submit_enabled=False),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "pending"


def test_processed_event_terminal_surface_includes_execution_receipt_certificate():
    from src.decision_kernel.certificates.execution import (
        build_execution_command_certificate_from_final_intent,
        build_execution_receipt_certificate,
        build_executor_expressibility_certificate,
        build_final_intent_certificate_from_actionable,
    )
    from src.engine.event_bound_final_intent import validate_final_intent_cert_for_existing_executor
    from tests.decision_kernel.test_actionable_trade_certificate import actionable_graph
    from tests.decision_kernel.test_execution_command_certificate import _cert, _live_cap_payload, _pre_submit_cert
    from src.decision_kernel import claims

    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    decision_time = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    action_parents, action = actionable_graph(
        action_payload={
            "event_id": event.event_id,
            "causal_snapshot_id": event.causal_snapshot_id,
            "min_entry_price": 0.05,
            "min_submit_edge_density": 0.02,
        },
        parent_overrides={
            claims.CAUSAL_EVENT: {"event_id": event.event_id, "causal_snapshot_id": event.causal_snapshot_id},
            claims.SOURCE_TRUTH: {"event_id": event.event_id},
            claims.LIVE_CAP: {"event_id": event.event_id},
        },
    )
    parents_by_type = {cert.certificate_type: cert for cert in action_parents}
    action_executable_snapshot = parents_by_type[claims.EXECUTABLE_SNAPSHOT]
    quote_feasibility = parents_by_type[claims.QUOTE_FEASIBILITY]
    action_cost_model = parents_by_type[claims.COST_MODEL]
    forecast_authority = parents_by_type[claims.FORECAST_AUTHORITY]
    live_cap = parents_by_type[claims.LIVE_CAP]
    executable_snapshot = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {
            "executable_snapshot_hash": "a" * 64,
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "neg_risk": False,
        },
    )
    cost_model = _cert(
        claims.COST_MODEL,
        "cost:1",
        {
            "cost_basis_hash": "b" * 64,
            "cost_basis_id": "cost_basis:" + ("b" * 16),
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
            "execution_price_type": "ExecutionPrice",
        },
    )
    final_intent = build_final_intent_certificate_from_actionable(
        actionable_cert=action,
        executable_snapshot_cert=executable_snapshot,
        quote_feasibility_cert=quote_feasibility,
        cost_model_cert=cost_model,
        forecast_authority_cert=forecast_authority,
        decision_source_context=forecast_authority.payload,
        passive_maker_context={
            "spread_usd": 0.02,
            "quote_age_ms": 0,
            "expected_fill_probability": "0.1",
            "queue_depth_ahead": None,
            "adverse_selection_score": None,
            "orderbook_hash_age_ms": 0,
        },
        decision_time=decision_time,
    )
    executable = executable_snapshot
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable,
        live_cap_cert=live_cap,
        decision_time=decision_time,
        executor_native_intent_hash=validate_final_intent_cert_for_existing_executor(final_intent),
    )
    pre_submit = _pre_submit_cert(final_intent, live_cap)
    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=action,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=decision_time,
    )
    receipt_cert = build_execution_receipt_certificate(execution_command_cert=command, decision_time=decision_time)
    cert_bundle = action_parents + (action, final_intent, executable, cost_model, expressibility, pre_submit, command, receipt_cert)

    def _submit(_event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=1,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=3.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
            side_effect_status="SUBMIT_DISABLED",
            proof_accepted=True,
            decision_proof_bundle=cert_bundle,
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_args: None,
        config=ReactorConfig(reactor_mode="live_no_submit", real_order_submit_enabled=False),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    # Drive the single event through the reactor's per-event processing unit
    # directly. This test verifies the terminal execution-receipt CERTIFICATE
    # surface, not queue admission. The event's fixture is rigidly anchored to
    # an already-settled (2026-05-24) target at a next-day decision; the STEP-3
    # fetch_pending timeliness floor (a QUEUE-admission concern) would correctly
    # drop such a strictly-past event before processing. Bypassing fetch_pending
    # keeps this test focused on the cert surface while the dedicated
    # fetch_pending timeliness tests own the queue-floor behavior.
    result = ReactorResult()
    reactor._process_event_unit(event, decision_time=decision_time, result=result)

    assert result.processed == 1
    assert _terminal_surfaces(conn, event.event_id)["execution_receipt"] == 1


def test_live_submitted_execution_receipt_certificate_is_terminal_when_submit_enabled():
    from src.decision_kernel.certificates.execution import (
        build_execution_command_certificate_from_final_intent,
        build_execution_receipt_certificate,
        build_executor_expressibility_certificate,
        build_final_intent_certificate_from_actionable,
    )
    from src.engine.event_bound_final_intent import validate_final_intent_cert_for_existing_executor
    from tests.decision_kernel.test_actionable_trade_certificate import actionable_graph
    from tests.decision_kernel.test_execution_command_certificate import _cert, _live_cap_payload, _pre_submit_cert
    from src.decision_kernel import claims

    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    decision_time = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    action_parents, action = actionable_graph(
        action_payload={
            "event_id": event.event_id,
            "causal_snapshot_id": event.causal_snapshot_id,
            "min_entry_price": 0.05,
            "min_submit_edge_density": 0.02,
        },
        parent_overrides={
            claims.CAUSAL_EVENT: {"event_id": event.event_id, "causal_snapshot_id": event.causal_snapshot_id},
            claims.SOURCE_TRUTH: {"event_id": event.event_id},
            claims.LIVE_CAP: {"event_id": event.event_id},
        },
    )
    parents_by_type = {cert.certificate_type: cert for cert in action_parents}
    action_executable_snapshot = parents_by_type[claims.EXECUTABLE_SNAPSHOT]
    quote_feasibility = parents_by_type[claims.QUOTE_FEASIBILITY]
    action_cost_model = parents_by_type[claims.COST_MODEL]
    forecast_authority = parents_by_type[claims.FORECAST_AUTHORITY]
    live_cap = parents_by_type[claims.LIVE_CAP]
    executable_snapshot = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {
            "executable_snapshot_hash": "a" * 64,
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "neg_risk": False,
        },
    )
    cost_model = _cert(
        claims.COST_MODEL,
        "cost:1",
        {
            "cost_basis_hash": "b" * 64,
            "cost_basis_id": "cost_basis:" + ("b" * 16),
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
            "execution_price_type": "ExecutionPrice",
        },
    )
    final_intent = build_final_intent_certificate_from_actionable(
        actionable_cert=action,
        executable_snapshot_cert=executable_snapshot,
        quote_feasibility_cert=quote_feasibility,
        cost_model_cert=cost_model,
        forecast_authority_cert=forecast_authority,
        decision_source_context=forecast_authority.payload,
        passive_maker_context={
            "spread_usd": 0.02,
            "quote_age_ms": 0,
            "expected_fill_probability": "0.1",
            "queue_depth_ahead": None,
            "adverse_selection_score": None,
            "orderbook_hash_age_ms": 0,
        },
        decision_time=decision_time,
    )
    executable = executable_snapshot
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable,
        live_cap_cert=live_cap,
        decision_time=decision_time,
        executor_native_intent_hash=validate_final_intent_cert_for_existing_executor(final_intent),
    )
    pre_submit = _pre_submit_cert(final_intent, live_cap)
    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=action,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=decision_time,
    )
    receipt_cert = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=decision_time,
        status="SUBMITTED",
        reason_code="OK",
        submit_started_at=decision_time.isoformat(),
        submit_finished_at=decision_time.isoformat(),
        venue_order_id="venue-1",
        raw_response={"status": "submitted"},
    )
    cert_bundle = action_parents + (action, final_intent, executable, cost_model, expressibility, pre_submit, command, receipt_cert)

    def _submit(_event, _decision_time):
        return EventSubmissionReceipt(
            submitted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=1,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=3.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
            side_effect_status="SUBMITTED",
            proof_accepted=True,
            decision_proof_bundle=cert_bundle,
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_args: None,
        config=ReactorConfig(reactor_mode="live", real_order_submit_enabled=True),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    # Drive the single event through the reactor's per-event processing unit
    # directly. This test verifies the terminal execution-receipt CERTIFICATE
    # surface, not queue admission. The event's fixture is rigidly anchored to
    # an already-settled (2026-05-24) target at a next-day decision; the STEP-3
    # fetch_pending timeliness floor (a QUEUE-admission concern) would correctly
    # drop such a strictly-past event before processing. Bypassing fetch_pending
    # keeps this test focused on the cert surface while the dedicated
    # fetch_pending timeliness tests own the queue-floor behavior.
    result = ReactorResult()
    reactor._process_event_unit(event, decision_time=decision_time, result=result)

    assert result.processed == 1
    assert _terminal_surfaces(conn, event.event_id)["execution_receipt"] == 1


def test_processed_event_without_execution_receipt_or_no_submit_or_failure_rejected():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    rejected = []

    def _submit(_event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=1,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=3.0,
            kelly_cost_basis_id="cost-1",
            final_intent_id="intent-1",
            side_effect_status="SUBMIT_DISABLED",
            proof_accepted=True,
            decision_proof_bundle=(),
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=ReactorConfig(real_order_submit_enabled=False),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    assert rejected[0][1] == "EXECUTION_RECEIPT"
    surfaces = _terminal_surfaces(conn, event.event_id)
    assert surfaces["execution_receipt"] == 0
    assert surfaces["compile_failure"] == 1


def test_duplicate_event_not_double_counted():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    store.insert_or_ignore(event)
    reactor, _rejected, submitted = _reactor(store)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.processed == 1


def test_reactor_persists_no_submit_certificate_before_processed():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.processed == 1
    cert_row = conn.execute(
        """
        SELECT certificate_hash, verifier_status
        FROM decision_certificates
        WHERE certificate_type = 'NoSubmitDecisionCertificate'
        """
    ).fetchone()
    assert cert_row is not None
    assert cert_row[1] == "VERIFIED"
    processing = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert processing[0] == "processed"
    assert len(_submitted) == 1


def test_source_truth_block_writes_decision_compile_failure():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store, gates=False)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    failure = conn.execute(
        """
        SELECT stage, reason_code
        FROM decision_compile_failures
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert failure is not None
    assert failure[0] == "SOURCE_TRUTH"
    assert failure[1] == "SOURCE_TRUTH_BLOCKED"


def test_rejection_regret_uses_reactor_decision_time():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store, gates=False)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    reactor.process_pending(decision_time=decision_time)

    row = conn.execute(
        "SELECT decision_time FROM no_trade_regret_events WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == decision_time.isoformat()


def test_payload_decision_time_cannot_override_reactor_decision_time():
    conn, store = _store()
    event = _day0_event()
    payload = json.loads(event.payload_json)
    payload["decision_time"] = "2099-01-01T00:00:00+00:00"
    event = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store, gates=False)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    reactor.process_pending(decision_time=decision_time)

    row = conn.execute(
        "SELECT decision_time FROM no_trade_regret_events WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == decision_time.isoformat()
    assert row[0] != payload["decision_time"]


def test_all_candidates_rejected_regret_is_family_level_only():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    payload = json.loads(event.payload_json)
    payload.update(
        {
            "family_id": "family-chicago",
            "bin_label": "74F",
            "direction": "buy_yes",
            "condition_id": "condition-1",
            "token_id": "token-1",
            "q_live": 0.61,
            "q_lcb_5pct": 0.57,
            "c_fee_adjusted": 0.56,
            "trade_score": -0.01,
        }
    )
    event = replace(
        event,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor._write_regret(
        event,
        "TRADE_SCORE",
        "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22; best_rejected=73F buy_no",
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
    )

    row = conn.execute(
        """
        SELECT family_id, bin_label, direction, condition_id, token_id,
               q_live, q_lcb_5pct, c_fee_adjusted, trade_score
          FROM no_trade_regret_events
         WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "family-chicago"
    assert row[1:] == (None, None, None, None, None, None, None, None)


def test_all_candidates_rejected_writes_structured_candidate_rows_from_receipt_book():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    payload = json.loads(event.payload_json)
    payload.update(
        {
            "family_id": "family-shanghai",
            "city": "Shanghai",
            "target_date": "2026-06-25",
            "metric": "high",
        }
    )
    event = replace(
        event,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        city="Shanghai",
        target_date="2026-06-25",
        metric="high",
        family_id="family-shanghai",
        executable_snapshot_id="exec-1",
        opportunity_book={
            "candidates": [
                {
                    "candidate_id": "candidate-buy-yes",
                    "family_id": "family-shanghai",
                    "condition_id": "condition-25",
                    "token_id": "yes-token-25",
                    "direction": "buy_yes",
                    "bin_label": "Will the highest temperature in Shanghai be 25°C on June 25?",
                    "execution_price": 0.6712,
                    "q_posterior": 0.9720,
                    "q_lcb_5pct": 0.9616,
                    "c_cost_95pct": 0.6712,
                    "p_fill_lcb": 1.0,
                    "trade_score": 0.4327,
                    "native_quote_available": True,
                    "missing_reason": "OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED:position_id=held-1",
                },
                {
                    "candidate_id": "candidate-no-edge",
                    "family_id": "family-shanghai",
                    "condition_id": "condition-27",
                    "token_id": "yes-token-27",
                    "direction": "buy_yes",
                    "bin_label": "Will the highest temperature in Shanghai be 27°C on June 25?",
                    "execution_price": 0.90,
                    "q_posterior": 0.10,
                    "q_lcb_5pct": 0.08,
                    "c_cost_95pct": 0.90,
                    "p_fill_lcb": 1.0,
                    "trade_score": -0.82,
                    "native_quote_available": True,
                    "missing_reason": "TRADE_SCORE_NON_POSITIVE",
                },
            ]
        },
    )
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor._write_regret(
        event,
        "TRADE_SCORE",
        "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22; best_rejected=25C buy_yes",
        receipt=receipt,
        decision_time=datetime(2026, 6, 25, 4, 19, tzinfo=timezone.utc),
    )

    rows = conn.execute(
        """
        SELECT rejection_reason, family_id, bin_label, direction, condition_id,
               token_id, q_live, q_lcb_5pct, c_fee_adjusted, p_fill_lcb,
               trade_score, native_quote_available, executable_snapshot_id
          FROM no_trade_regret_events
         WHERE event_id = ?
         ORDER BY rejection_reason
        """,
        (event.event_id,),
    ).fetchall()
    assert len(rows) == 2
    family_summary = next(
        row for row in rows if row[0].startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
    )
    candidate = next(row for row in rows if row[0].startswith("EVENT_BOUND_CANDIDATE_REJECTED:"))
    assert family_summary[0].startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
    assert family_summary[2:11] == (None, None, None, None, None, None, None, None, None)
    assert candidate[0].startswith(
        "EVENT_BOUND_CANDIDATE_REJECTED:OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED:"
    )
    assert candidate[1] == "family-shanghai"
    assert candidate[2] == "Will the highest temperature in Shanghai be 25°C on June 25?"
    assert candidate[3] == "buy_yes"
    assert candidate[4] == "condition-25"
    assert candidate[5] == "yes-token-25"
    assert candidate[6:11] == (0.972, 0.9616, 0.6712, 1.0, 0.4327)
    assert candidate[11] == 1
    assert candidate[12] == "exec-1"


def test_qkernel_no_trade_writes_structured_candidate_rows_from_receipt_book():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    payload = json.loads(event.payload_json)
    payload.update(
        {
            "family_id": "family-beijing",
            "city": "Beijing",
            "target_date": "2026-06-26",
            "metric": "high",
        }
    )
    event = replace(
        event,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        city="Beijing",
        target_date="2026-06-26",
        metric="high",
        family_id="family-beijing",
        executable_snapshot_id="exec-qkernel",
        opportunity_book={
            "candidates": [
                {
                    "candidate_id": "candidate-buy-no-33c",
                    "family_id": "family-beijing",
                    "condition_id": "condition-33",
                    "token_id": "no-token-33",
                    "direction": "buy_no",
                    "bin_label": "Will the highest temperature in Beijing be 33°C on June 26?",
                    "execution_price": 0.74962,
                    "q_posterior": 0.8054,
                    "q_lcb_5pct": 0.773718,
                    "c_cost_95pct": 0.74962,
                    "p_fill_lcb": 1.0,
                    "trade_score": 0.0084,
                    "native_quote_available": True,
                    "missing_reason": None,
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "candidate_id": "NO:bin-33:DIRECT_NO:bin-33@proof",
                        "route_id": "DIRECT_NO:bin-33@proof",
                        "payoff_q_point": 0.779,
                        "payoff_q_lcb": 0.748,
                        "edge_lcb": -0.00162,
                        "point_ev": 0.031,
                        "delta_u_at_min": -0.0004,
                        "optimal_stake_usd": "0",
                        "optimal_delta_u": 0.0,
                        "q_dot_payoff": 0.779,
                        "cost": 0.74962,
                    },
                }
            ]
        },
    )
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor._write_regret(
        event,
        "TRADE_SCORE",
        "QKERNEL_SPINE_NO_TRADE:NO_POSITIVE_EDGE_CANDIDATE",
        receipt=receipt,
        decision_time=datetime(2026, 6, 25, 5, 24, tzinfo=timezone.utc),
    )

    rows = conn.execute(
        """
        SELECT rejection_reason, bin_label, direction, condition_id, token_id,
               q_live, q_lcb_5pct, c_fee_adjusted, c_cost_95pct, trade_score
          FROM no_trade_regret_events
         WHERE event_id = ?
         ORDER BY rejection_reason
        """,
        (event.event_id,),
    ).fetchall()
    assert len(rows) == 2
    family_summary = next(row for row in rows if row[0].startswith("QKERNEL_SPINE_NO_TRADE:"))
    candidate = next(row for row in rows if row[0].startswith("EVENT_BOUND_CANDIDATE_REJECTED:"))
    assert family_summary[0] == "QKERNEL_SPINE_NO_TRADE:NO_POSITIVE_EDGE_CANDIDATE"
    assert family_summary[1:] == (None, None, None, None, None, None, None, None, None)
    assert candidate[0].startswith(
        "EVENT_BOUND_CANDIDATE_REJECTED:QKERNEL_SPINE_NO_TRADE:NO_POSITIVE_EDGE_CANDIDATE:"
    )
    assert candidate[1] == "Will the highest temperature in Beijing be 33°C on June 26?"
    assert candidate[2] == "buy_no"
    assert candidate[3] == "condition-33"
    assert candidate[4] == "no-token-33"
    assert candidate[5:10] == (0.779, 0.748, 0.74962, 0.74962, -0.00162)


def test_reactor_rejects_no_submit_receipt_without_decision_proof_bundle():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    rejected = []

    def _submit(submitted_event, _decision_time):
        payload = json.loads(submitted_event.payload_json)
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=submitted_event.event_id,
            causal_snapshot_id=submitted_event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    assert rejected[0][1] == "DECISION_CERTIFICATE"
    assert rejected[0][2] == "NO_SUBMIT_PROOF_BUNDLE_REQUIRED"
    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 0
    failure = conn.execute(
        "SELECT stage, reason_code FROM decision_compile_failures WHERE event_id = ?",
        (event.event_id,),
    ).fetchall()
    assert ("NO_SUBMIT_COMPILER", "NO_SUBMIT_PROOF_BUNDLE_REQUIRED") in failure


def test_transition_proof_bundle_builder_not_used_in_runtime_reactor():
    _conn, store = _store()
    reactor, _rejected, _submitted = _reactor(store)

    assert not hasattr(reactor, "_build_transition_proof_bundle")


def test_receipt_insert_failure_does_not_leave_verified_orphan_certificate_graph():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("projection insert failed")

    reactor._no_submit_receipt_ledger.insert_idempotent = _raise  # type: ignore[method-assign]

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 0
    failure = conn.execute(
        "SELECT reason_code FROM decision_compile_failures WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert failure is not None
    assert "projection insert failed" in failure[0]


def test_certificate_insert_failure_rolls_back_event_processing():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("certificate graph insert failed")

    reactor._decision_certificate_ledger.persist_all = _raise  # type: ignore[method-assign]

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 0
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "dead_letter"
    surfaces = _terminal_surfaces(conn, event.event_id)
    assert surfaces["verified_no_submit"] == 0
    assert surfaces["compile_failure"] == 1
    assert surfaces["regret"] == 1
    assert surfaces["dead_letter"] == 1


def test_successful_no_submit_receipt_is_persisted_before_processed():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.proof_accepted == 1
    receipt_row = conn.execute(
        """
        SELECT event_id, side_effect_status, receipt_json, receipt_hash,
               kelly_decision_id, risk_decision_id
        FROM edli_no_submit_receipts
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert receipt_row is not None
    assert receipt_row[0] == event.event_id
    assert receipt_row[1] == "NO_SUBMIT"
    assert '"proof_accepted":true' in receipt_row[2]
    assert len(receipt_row[3]) == 64
    assert receipt_row[4] == "kelly-1"
    assert receipt_row[5] == "risk-1"
    status = conn.execute(
        """
        SELECT processing_status
        FROM opportunity_event_processing
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()[0]
    assert status == "processed"


def test_terminal_trade_score_no_submit_receipt_is_persisted_before_rejection():
    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    payload = json.loads(event.payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            bin_label="80F",
            direction="buy_yes",
            q_live=0.51,
            q_lcb_5pct=0.47,
            c_fee_adjusted=0.56,
            c_cost_95pct=0.56,
            p_fill_lcb=1.0,
            trade_score=-0.09,
            trade_score_positive=False,
            reason="TRADE_SCORE_NON_POSITIVE",
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.rejected == 1
    assert _processing_status(conn, event.event_id) == "processed"
    receipt_row = conn.execute(
        """
        SELECT side_effect_status, receipt_json, trade_score
        FROM edli_no_submit_receipts
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert receipt_row is not None
    assert receipt_row[0] == "NO_SUBMIT"
    assert '"reason":"TRADE_SCORE_NON_POSITIVE"' in receipt_row[1]
    assert receipt_row[2] == -0.09
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 1,
        "regret": 1,
        "dead_letter": 0,
    }


def test_submit_disabled_live_receipt_bridges_to_no_submit_receipt_table():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    def _submit(event, _decision_time):
        payload = json.loads(event.payload_json)
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
            side_effect_status="SUBMIT_DISABLED",
            reason="real_order_submit_disabled",
            decision_proof_bundle=(
                SimpleNamespace(certificate_type=claims.EXECUTION_RECEIPT),
            ),
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _event, _stage, _reason: None,
        config=ReactorConfig(reactor_mode="live_no_submit", real_order_submit_enabled=False),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    reactor._decision_certificate_ledger.persist_all = lambda _certs: None  # type: ignore[method-assign]

    result = reactor.process_pending(decision_time=decision_time)

    assert result.proof_accepted == 1
    row = conn.execute(
        """
        SELECT side_effect_status, receipt_json
        FROM edli_no_submit_receipts
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "NO_SUBMIT"
    assert '"side_effect_status":"NO_SUBMIT"' in row[1]
    assert '"reason":"real_order_submit_disabled"' in row[1]


def test_no_submit_projection_rows_require_verified_decision_certificate():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    from src.events.no_submit_projection import no_submit_projection_rows

    reactor.process_pending(decision_time=decision_time)

    assert len(no_submit_projection_rows(conn)) == 1
    conn.execute("DELETE FROM decision_certificates WHERE certificate_type = 'NoSubmitDecisionCertificate'")
    assert no_submit_projection_rows(conn) == []


def test_no_submit_receipt_ledger_is_idempotent_for_duplicate_event():
    conn, _event_store = _store()
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger

    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id="event-1",
        causal_snapshot_id="snapshot-1",
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        condition_id="condition-1",
        token_id="yes-1",
        candidate_id="candidate-1",
        executable_snapshot_id="exec-1",
        family_id="family-1",
        bin_label="70-71F",
        direction="buy_yes",
        q_live=0.8,
        q_lcb_5pct=0.7,
        c_fee_adjusted=0.4,
        c_cost_95pct=0.41,
        p_fill_lcb=0.05,
        trade_score=0.1,
        native_quote_available=True,
        source_status="MATCH",
        family_complete=True,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fdr-family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="kelly-cost-1",
        kelly_decision_id="kelly-decision-1",
        risk_decision_id="risk-decision-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    ledger = EdliNoSubmitReceiptLedger(conn)

    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 1
    row = conn.execute(
        "SELECT kelly_decision_id, risk_decision_id FROM edli_no_submit_receipts WHERE event_id = 'event-1'"
    ).fetchone()
    assert row == ("kelly-decision-1", "risk-decision-1")


def test_no_submit_receipt_ledger_backfills_missing_projection_hash_on_idempotent_insert():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger, _receipt_json

    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id="event-1",
        causal_snapshot_id="snapshot-1",
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        executable_snapshot_id="exec-1",
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fdr-family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="kelly-cost-1",
        kelly_decision_id="kelly-decision-1",
        risk_decision_id="risk-decision-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    receipt_json = _receipt_json(receipt)
    conn.execute(
        """
        CREATE TABLE edli_no_submit_receipts (
            receipt_id TEXT NOT NULL PRIMARY KEY,
            event_id TEXT NOT NULL,
            final_intent_id TEXT,
            receipt_hash TEXT NOT NULL,
            projection_hash TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO edli_no_submit_receipts (
            receipt_id, event_id, final_intent_id, receipt_hash, projection_hash
        ) VALUES (?, ?, ?, ?, NULL)
        """,
        (
            "legacy-receipt-1",
            receipt.event_id,
            receipt.final_intent_id,
            hashlib.sha256(receipt_json.encode("utf-8")).hexdigest(),
        ),
    )
    ledger = EdliNoSubmitReceiptLedger(conn)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    ledger.insert_idempotent(receipt, decision_time=decision_time)

    projection_hash = conn.execute(
        "SELECT projection_hash FROM edli_no_submit_receipts WHERE event_id = 'event-1'"
    ).fetchone()[0]
    assert projection_hash


def test_no_submit_receipt_schema_backfills_projection_hash_for_existing_rows():
    from src.events.no_submit_receipts import _receipt_json
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id="event-1",
        causal_snapshot_id="snapshot-1",
        executable_snapshot_id="exec-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    conn.execute(
        """
        CREATE TABLE edli_no_submit_receipts (
            receipt_id TEXT NOT NULL PRIMARY KEY,
            event_id TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            final_intent_id TEXT,
            side_effect_status TEXT NOT NULL,
            executable_snapshot_id TEXT,
            receipt_json TEXT NOT NULL,
            receipt_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO edli_no_submit_receipts (
            receipt_id, event_id, decision_time, final_intent_id, side_effect_status,
            executable_snapshot_id, receipt_json, receipt_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "receipt-1",
            receipt.event_id,
            "2026-05-24T18:10:00+00:00",
            receipt.final_intent_id,
            receipt.side_effect_status,
            receipt.executable_snapshot_id,
            _receipt_json(receipt),
            "receipt-hash",
        ),
    )

    ensure_table(conn)

    projection_hash = conn.execute(
        "SELECT projection_hash FROM edli_no_submit_receipts WHERE receipt_id = 'receipt-1'"
    ).fetchone()[0]
    assert projection_hash


def test_no_submit_receipt_ledger_rejects_duplicate_hash_drift():
    conn, _event_store = _store()
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger, EdliReceiptHashDriftError

    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id="event-1",
        causal_snapshot_id="snapshot-1",
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fdr-family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="kelly-cost-1",
        kelly_decision_id="kelly-decision-1",
        risk_decision_id="risk-decision-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    ledger = EdliNoSubmitReceiptLedger(conn)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    ledger.insert_idempotent(receipt, decision_time=decision_time)
    drifted = replace(receipt, kelly_size_usd=2.0)

    try:
        ledger.insert_idempotent(drifted, decision_time=decision_time)
    except EdliReceiptHashDriftError as exc:
        assert "EDLI_RECEIPT_HASH_DRIFT" in str(exc)
    else:
        raise AssertionError("receipt hash drift must not be silently ignored")
    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 1


def test_receipt_hash_drift_dead_letters_event_before_processed():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger

    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    payload = json.loads(event.payload_json)
    existing = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        city=payload.get("city"),
        target_date=payload.get("target_date"),
        metric=payload.get("metric"),
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=2.0,
        kelly_cost_basis_id="cost-1",
        kelly_decision_id="kelly-old",
        risk_decision_id="risk-old",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(existing, decision_time=decision_time)
    reactor, rejected, _submitted = _reactor(store)

    result = reactor.process_pending(decision_time=decision_time)

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 1
    dead = conn.execute(
        "SELECT failure_stage, error_message FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert dead is not None
    assert dead[0] == "UNKNOWN_REVIEW_REQUIRED"
    assert "EDLI_RECEIPT_HASH_DRIFT" in dead[1]
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "dead_letter"
    assert rejected[0][1] == "UNKNOWN_REVIEW_REQUIRED"


def test_pr332_db_concurrency_smoke_reactor_world_writes(tmp_path):
    db_path = tmp_path / "pr332-world.db"
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    forecast_events = [_forecast_event(str(index)) for index in range(6)]
    book_event = _market_event()
    for event in [*forecast_events, book_event]:
        store.insert_or_ignore(event)
    conn.commit()
    reactor, _rejected, _submitted = _reactor(store)
    writer_ready = threading.Event()
    writer_done = threading.Event()
    writer_errors = []

    def _concurrent_world_writer() -> None:
        try:
            writer_ready.set()
            writer_conn = sqlite3.connect(db_path, timeout=5.0)
            writer_conn.row_factory = sqlite3.Row
            try:
                writer_store = EventStore(writer_conn)
                future_event = _forecast_event("concurrent-future")
                future_payload = json.loads(future_event.payload_json)
                future_payload["available_at"] = "2026-05-24T18:15:00+00:00"
                future_event = replace(
                    future_event,
                    available_at="2026-05-24T18:15:00+00:00",
                    received_at="2026-05-24T18:15:01+00:00",
                    payload_json=json.dumps(future_payload, sort_keys=True, separators=(",", ":")),
                )
                writer_store.insert_or_ignore(future_event)
                writer_conn.commit()
            finally:
                writer_conn.close()
        except Exception as exc:  # pragma: no cover - assertion below reports exact failure.
            writer_errors.append(exc)
        finally:
            writer_done.set()

    thread = threading.Thread(target=_concurrent_world_writer)
    thread.start()
    assert writer_ready.wait(timeout=2.0)

    result = reactor.process_pending(decision_time=decision_time, limit=10)
    conn.commit()
    assert writer_done.wait(timeout=5.0)
    thread.join(timeout=5.0)

    assert writer_errors == []
    assert result.processed == len(forecast_events)
    assert result.rejected == 0
    rows = conn.execute(
        """
        SELECT event_id, processing_status
        FROM opportunity_event_processing
        WHERE event_id IN ({})
        """.format(",".join("?" for _ in [*forecast_events, book_event])),
        tuple(event.event_id for event in [*forecast_events, book_event]),
    ).fetchall()
    statuses = {row["event_id"]: row["processing_status"] for row in rows}
    assert {statuses[event.event_id] for event in forecast_events} == {"processed"}
    assert statuses[book_event.event_id] == "ignored"
    for event in forecast_events:
        cert_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM decision_certificates
            WHERE certificate_type = 'NoSubmitDecisionCertificate'
              AND json_extract(payload_json, '$.event_id') = ?
            """,
            (event.event_id,),
        ).fetchone()[0]
        receipt_count = conn.execute(
            "SELECT COUNT(*) FROM edli_no_submit_receipts WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]
        assert cert_count == 1
        assert receipt_count == 1
    regret_count = conn.execute(
        "SELECT COUNT(*) FROM no_trade_regret_events WHERE event_id = ?",
        (book_event.event_id,),
    ).fetchone()[0]
    assert regret_count == 0
    future_pending = conn.execute(
        """
        SELECT COUNT(*)
        FROM opportunity_event_processing
        WHERE processing_status = 'pending'
        """
    ).fetchone()[0]
    assert future_pending == 1


def test_processed_event_has_verified_certificate_or_failure_or_regret_or_dead_letter():
    conn, store = _store()
    accepted = _forecast_event("accepted")
    source_rejected = _forecast_event("source-rejected")
    market_rejected = _market_event()
    for event in (accepted, source_rejected, market_rejected):
        store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)
    reactor._source_truth_gate = lambda event: event.event_id != source_rejected.event_id  # type: ignore[method-assign]

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc), limit=10)

    assert result.processed == 2
    assert result.proof_accepted == 1
    assert result.rejected == 1
    rows = conn.execute(
        """
        SELECT event_id, processing_status
        FROM opportunity_event_processing
        WHERE event_id IN (?, ?, ?)
        """,
        (accepted.event_id, source_rejected.event_id, market_rejected.event_id),
    ).fetchall()
    statuses = {row[0]: row[1] for row in rows}
    assert statuses[accepted.event_id] == "processed"
    assert statuses[source_rejected.event_id] == "processed"
    assert statuses[market_rejected.event_id] == "ignored"
    expected = {
        accepted.event_id: {"verified_no_submit": 1, "execution_receipt": 0, "compile_failure": 0, "regret": 0, "dead_letter": 0},
        source_rejected.event_id: {"verified_no_submit": 0, "execution_receipt": 0, "compile_failure": 1, "regret": 1, "dead_letter": 0},
        market_rejected.event_id: {"verified_no_submit": 0, "execution_receipt": 0, "compile_failure": 0, "regret": 0, "dead_letter": 0},
    }
    for event_id, expected_surfaces in expected.items():
        assert _terminal_surfaces(conn, event_id) == expected_surfaces


def test_reactor_passes_decision_time_to_submit():
    _conn, store = _store()
    event = _day0_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    seen = []
    store.insert_or_ignore(event)

    def _submit(submitted_event, submitted_decision_time):
        seen.append((submitted_event.event_id, submitted_decision_time))
        payload = json.loads(submitted_event.payload_json)
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=submitted_event.event_id,
            causal_snapshot_id=submitted_event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
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
        )

    OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _event, _stage, _reason: None,
    ).process_pending(decision_time=decision_time)

    assert seen == [(event.event_id, decision_time)]


def test_sibling_family_logged_once():
    _conn, store = _store()
    store.insert_or_ignore(_day0_event("bin-a"))
    store.insert_or_ignore(_day0_event("bin-b"))
    reactor, _rejected, _submitted = _reactor(store)
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert reactor.family_log_count() == 1


def test_receipt_without_money_path_proof_is_rejected():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    rejected = []
    submitted = []

    def _submit(event, _decision_time):
        payload = json.loads(event.payload_json)
        submitted.append(event.event_id)
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=False,
            kelly_execution_price_type="float",
            kelly_price_fee_deducted=False,
            kelly_size_usd=0.0,
            final_intent_id="intent-1",
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=ReactorConfig(),
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert submitted == [event.event_id]
    assert result.rejected == 1
    assert rejected[0][1] == "KELLY"
    assert rejected[0][2] == "EDLI_KELLY_PROOF_MISSING"


def test_reactor_blocks_real_order_side_effect_when_no_submit_mode():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    rejected = []

    def _submit(event, _decision_time):
        payload = json.loads(event.payload_json)
        return EventSubmissionReceipt(
            submitted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
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

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=ReactorConfig(reactor_mode="live_no_submit", real_order_submit_enabled=False),
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    assert rejected[0][1] == "EXECUTOR_EXPRESSIBILITY"
    assert rejected[0][2] == "EDLI_REAL_ORDER_SIDE_EFFECT_FORBIDDEN"


def test_no_submit_day0_does_not_consume_tiny_cap():
    conn, store = _store()
    store.insert_or_ignore(_forecast_event("bin-a"))
    store.insert_or_ignore(_forecast_event("bin-b"))
    reactor, rejected, submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert len(submitted) == 2
    assert result.rejected == 0
    assert rejected == []
    assert conn.execute("SELECT COUNT(*) FROM edli_live_cap_usage").fetchone()[0] == 0


def test_no_submit_day0_tiny_cap_does_not_persist_across_reactor_instances():
    conn, store = _store()
    first = _forecast_event("bin-a")
    second = _forecast_event("bin-b")
    store.insert_or_ignore(first)
    reactor, _rejected, submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert submitted == [first.event_id]

    store.insert_or_ignore(second)
    second_reactor, rejected, second_submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    result = second_reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc))

    assert second_submitted == [second.event_id]
    assert result.rejected == 0
    assert rejected == []
    assert conn.execute("SELECT COUNT(*) FROM edli_live_cap_usage").fetchone()[0] == 0


def test_no_submit_day0_tiny_notional_cap_does_not_persist_across_reactor_instances():
    conn, store = _store()
    first = _forecast_event("bin-a")
    second = _forecast_event("bin-b")
    store.insert_or_ignore(first)
    reactor, _rejected, submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert submitted == [first.event_id]

    store.insert_or_ignore(second)
    second_reactor, rejected, second_submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    result = second_reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc))

    assert second_submitted == [second.event_id]
    assert result.rejected == 0
    assert rejected == []
    assert conn.execute("SELECT COUNT(*) FROM edli_live_cap_usage").fetchone()[0] == 0


def test_day0_source_mismatch_blocks_before_trade_score_path():
    _conn, store = _store()
    event = _day0_event()
    import json
    from dataclasses import replace

    payload = json.loads(event.payload_json)
    payload["source_match_status"] = "MISMATCH"
    mismatched = replace(
        event,
        event_id="event-source-mismatch",
        idempotency_key="idem-source-mismatch",
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    store.insert_or_ignore(mismatched)
    reactor, rejected, submitted = _reactor(store)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    assert rejected[0][1] == "SOURCE_TRUTH"
    assert rejected[0][2] == "DAY0_HARD_FACT_AUTHORITY_BLOCKED"
    assert submitted == []


def test_reactor_does_not_write_regret_for_channel_cache_events():
    conn, store = _store()
    store.insert_or_ignore(_market_event())
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

    rejected = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert conn.execute("SELECT COUNT(*) FROM no_trade_regret_events").fetchone()[0] == 0


def test_reactor_exception_dead_letters_event():
    conn, store = _store()
    store.insert_or_ignore(_day0_event())
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: (_ for _ in ()).throw(RuntimeError("boom")),
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM event_dead_letters").fetchone()[0] == 1


def _fsr_event(key_suffix: str, completeness: str, available_at: str, received_at: str):
    """Build a FORECAST_SNAPSHOT_READY event with the given completeness status."""
    import json as _json
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="opendata",
        source_run_id=f"run-{key_suffix}",
        cycle="00",
        track="live",
        snapshot_id=f"snap-{key_suffix}",
        snapshot_hash=f"hash-{key_suffix}",
        captured_at="2026-05-24T04:00:00+00:00",
        available_at=available_at,
        required_fields_present=True,
        required_steps_present=True,
        member_count=51 if completeness == "COMPLETE" else 10,
        min_members_floor=40,
        completeness_status=completeness,
        required_steps=[0],
        observed_steps=[0],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status=completeness,
        coverage_completeness_status=completeness,
        coverage_readiness_status="LIVE_ELIGIBLE" if completeness == "COMPLETE" else "NOT_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-05-24|high|{key_suffix}",
        source="forecast_live",
        observed_at="2026-05-24T04:00:00+00:00",
        available_at=available_at,
        received_at=received_at,
        payload=payload,
        causal_snapshot_id=f"snap-{key_suffix}",
    )


def test_partial_coverage_fsr_passes_gate_complete_fsr_dequeued_first():
    """SERVE-FRESHEST-ELIGIBLE RECONCILIATION (2026-06-11, twin-authority #8).

    The event's coverage statuses are ADVISORY — the serving authority is the
    bundle reader (tradeable-latest, 没有新的就用老的), which the adapter consults
    at proof time. A coverage-PARTIAL/BLOCKED event therefore passes the
    SOURCE_TRUTH intake gate and reaches the adapter (which rejects honestly
    when nothing eligible is servable). Live incident: 16:33:51Z six low-metric
    families dead-lettered in one second on branded PARTIAL/BLOCKED coverage
    while an eligible replacement posterior was servable.

    Ordering still holds: the COMPLETE/LIVE_ELIGIBLE event (claim Tier 1) is
    dequeued BEFORE the PARTIAL one (Tier 2) even when PARTIAL has an older
    available_at.
    """
    conn, store = _store()

    # PARTIAL event has older available_at (would sort first under naive priority+available_at order)
    partial_event = _fsr_event(
        key_suffix="partial",
        completeness="PARTIAL",
        available_at="2026-05-24T04:00:00+00:00",
        received_at="2026-05-24T04:01:00+00:00",
    )
    # COMPLETE event has newer available_at (would sort second under naive order)
    complete_event = _fsr_event(
        key_suffix="complete",
        completeness="COMPLETE",
        available_at="2026-05-24T05:00:00+00:00",
        received_at="2026-05-24T05:01:00+00:00",
    )

    store.insert_or_ignore(partial_event)
    store.insert_or_ignore(complete_event)

    submitted_order = []

    def _submit(event, _dt):
        submitted_order.append(event.event_id)
        return None  # no receipt — terminal consume downstream of the gate under test

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    dt = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    reactor.process_pending(decision_time=dt, limit=100)

    # ANTIBODY: the PARTIAL-coverage event must NOT be dead-lettered at intake —
    # it flows to the adapter, whose tradeable-latest bundle read decides.
    partial_status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (partial_event.event_id,),
    ).fetchone()[0]
    assert partial_status != "dead_letter", (
        f"PARTIAL-coverage FSR must pass the intake gate (serving authority decides), "
        f"got {partial_status}"
    )
    partial_dl = conn.execute(
        "SELECT COUNT(*) FROM event_dead_letters WHERE event_id = ?",
        (partial_event.event_id,),
    ).fetchone()[0]
    assert partial_dl == 0, "PARTIAL-coverage FSR must NOT have a dead_letter entry"

    # Both reach the adapter; the COMPLETE (Tier 1) one FIRST.
    assert complete_event.event_id in submitted_order, "COMPLETE FSR must reach submit"
    assert partial_event.event_id in submitted_order, (
        "PARTIAL-coverage FSR must reach the adapter (the serving authority, not the "
        "event payload, owns eligibility)"
    )
    assert submitted_order.index(complete_event.event_id) < submitted_order.index(partial_event.event_id), (
        "COMPLETE/LIVE_ELIGIBLE (claim Tier 1) must be dequeued before PARTIAL (Tier 2)"
    )


def test_junk_src_completeness_fsr_still_dead_letters():
    """ANTIBODY (the kept half of the intake gate): a STRUCTURALLY JUNK payload —
    source_run_completeness_status outside {COMPLETE, PARTIAL} (malformed/unknown
    producer state) — still dead-letters at intake. The serving-authority deferral
    applies only to honest, branded coverage statuses."""
    import dataclasses as _dc

    conn, store = _store()
    base = _fsr_event(
        key_suffix="junk",
        completeness="COMPLETE",
        available_at="2026-05-24T04:00:00+00:00",
        received_at="2026-05-24T04:01:00+00:00",
    )
    # Corrupt the run-identity field only (coverage stays honest).
    payload = json.loads(base.payload_json)
    payload["source_run_completeness_status"] = "GARBAGE_STATE"
    junk = _dc.replace(base, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    store.insert_or_ignore(junk)

    submitted_order = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda event, _dt: submitted_order.append(event.event_id),
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc), limit=10)

    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (junk.event_id,),
    ).fetchone()[0]
    assert status == "dead_letter", f"junk src_completeness must dead-letter, got {status}"
    assert junk.event_id not in submitted_order


def test_source_run_partial_window_complete_fsr_reaches_submit():
    """Run-level PARTIAL must not veto a COMPLETE/LIVE_ELIGIBLE target window."""
    conn, store = _store()
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="opendata",
        source_run_id="run-partial-window-complete",
        cycle="00",
        track="live",
        snapshot_id="snap-partial-window-complete",
        snapshot_hash="hash-partial-window-complete",
        captured_at="2026-05-24T04:00:00+00:00",
        available_at="2026-05-24T04:00:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="PARTIAL",
        source_run_completeness_status="PARTIAL",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high|run-partial-window-complete",
        source="forecast_live",
        observed_at="2026-05-24T04:00:00+00:00",
        available_at="2026-05-24T04:00:00+00:00",
        received_at="2026-05-24T04:01:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-partial-window-complete",
    )
    store.insert_or_ignore(event)
    submitted_order = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda event, _dt: submitted_order.append(event.event_id),
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc), limit=1)

    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status != "dead_letter"
    assert event.event_id in submitted_order


def _market_channel_event(event_type: str, key_suffix: str, available_at: str = "2026-05-24T04:00:00+00:00"):
    """Build a market-channel cache-hydration event (BEST_BID_ASK_CHANGED / BOOK_SNAPSHOT)."""
    payload = MarketBookEventPayload(
        condition_id="0xcondition",
        token_id=f"token-{key_suffix}",
        outcome_label="YES",
        event_type=event_type,
        quote_seen_at=available_at,
        book_hash=f"hash-{key_suffix}",
    )
    return make_opportunity_event(
        event_type=event_type,
        entity_key=f"0xcondition|token-{key_suffix}",
        source="polymarket_market_channel",
        observed_at=available_at,
        available_at=available_at,
        received_at="2026-05-24T04:01:00+00:00",
        payload=payload,
        causal_snapshot_id=f"hash-{key_suffix}",
    )


def test_market_channel_events_do_not_starve_decision_triggers():
    """Relationship test: a large backlog of market-channel events (BEST_BID_ASK_CHANGED /
    BOOK_SNAPSHOT / NEW_MARKET_DISCOVERED) must not starve decision-trigger events
    (FORECAST_SNAPSHOT_READY, DAY0_EXTREME_UPDATED) even when market-channel events have
    an older available_at and would normally sort first within the same priority level.

    The fetch_pending ORDER BY must assign market-channel events to a lower tier (tier 2)
    than decision-trigger events (tier 0 / tier 1), so the per-cycle budget (limit) is
    consumed by decision events first.

    Invariant tested: with N_MC > limit market-channel events older than a DAY0_EXTREME_UPDATED
    event, fetch_pending(limit=10) must include the DAY0 event and NOT fill all 10 slots with
    market-channel events.

    RED (without fix): BEST_BID_ASK_CHANGED events have older available_at → sort first at
    tier=1 (same as DAY0); all 10 limit slots consumed by MC; DAY0 never fetched.
    GREEN (with fix): MC events demoted to tier=2; DAY0 at tier=1 fetched before any MC event.
    """
    conn, store = _store()

    # Insert N_MC BEST_BID_ASK_CHANGED events with older available_at (tier 2 with fix)
    N_MC = 30  # exceeds limit=10; without MC demotion, all 10 slots go to MC
    for i in range(N_MC):
        ev = _market_channel_event(
            "BEST_BID_ASK_CHANGED",
            key_suffix=str(i),
            available_at="2026-05-24T01:00:00+00:00",  # older than DAY0 event
        )
        store.insert_or_ignore(ev)

    # Insert one DAY0_EXTREME_UPDATED with newer available_at (tier 1 — must not be starved)
    day0 = _day0_event(key_suffix="starvation-test")
    store.insert_or_ignore(day0)

    dt = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    fetched = store.fetch_pending(decision_time=dt.isoformat(), limit=10)

    fetched_types = [e.event_type for e in fetched]
    day0_fetched = any(e.event_id == day0.event_id for e in fetched)
    mc_count = fetched_types.count("BEST_BID_ASK_CHANGED")

    assert day0_fetched, (
        f"DAY0_EXTREME_UPDATED was not fetched: market-channel events starved it. "
        f"fetched event_types={fetched_types[:10]}"
    )
    assert mc_count < 10, (
        f"All 10 fetch slots consumed by BEST_BID_ASK_CHANGED ({mc_count}); "
        f"decision-trigger event starved."
    )


def test_market_channel_events_do_not_starve_fsr():
    """Relationship test: COMPLETE FSR events must be fetched before market-channel events
    due to tier-0 priority, even with a large MC backlog of older events.

    This test covers the COMPLETE FSR path (tier 0, unconditionally first).
    See test_market_channel_events_do_not_starve_decision_triggers for the tier-1 starvation
    case (DAY0_EXTREME_UPDATED / other decision events vs MC).
    """
    conn, store = _store()

    # 30 MC events older than FSR
    N_MC = 30
    for i in range(N_MC):
        ev = _market_channel_event(
            "BEST_BID_ASK_CHANGED",
            key_suffix=str(i),
            available_at="2026-05-24T01:00:00+00:00",
        )
        store.insert_or_ignore(ev)

    # 1 COMPLETE FSR, newer available_at
    fsr = _fsr_event(
        key_suffix="sole-fsr",
        completeness="COMPLETE",
        available_at="2026-05-24T05:00:00+00:00",
        received_at="2026-05-24T05:01:00+00:00",
    )
    store.insert_or_ignore(fsr)

    dt = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    fetched = store.fetch_pending(decision_time=dt.isoformat(), limit=10)

    fetched_ids = [e.event_id for e in fetched]
    assert fsr.event_id in fetched_ids, (
        f"COMPLETE FSR not in top-10 fetch despite tier-0 priority. "
        f"Fetched types: {[e.event_type for e in fetched][:10]}"
    )


def test_reactor_overfetches_before_lane_interleave_under_day0_flood():
    """A small process limit must not truncate the forecast lane before interleave.

    Live regression: ``reactor_process_limit`` was ~10 while fetch_pending returned
    12+ tradeable Day0 rows before the first FORECAST/REDECISION row.  The reactor
    interleave never saw the forecast lane, so ordinary entry/redecision work
    starved even though the fairness helper was correct for a full page.
    """

    conn, store = _store()
    available_at = "2026-05-25T06:00:00+00:00"
    for i in range(12):
        store.insert_or_ignore(
            _day0_event_for_target(
                key_suffix=f"day0-{i}",
                target_date="2026-05-25",
                available_at=available_at,
            )
        )
    fsr = _forecast_event(key_suffix="fsr-behind-day0", target_date="2026-05-25")
    store.insert_or_ignore(fsr)

    requested_limits: list[int] = []
    original_fetch = store.fetch_pending

    def _recording_fetch(**kwargs):
        requested_limits.append(int(kwargs["limit"]))
        return original_fetch(**kwargs)

    store.fetch_pending = _recording_fetch  # type: ignore[method-assign]
    reactor, _rejected, submitted = _reactor(
        store,
        config=ReactorConfig(day0_is_tradeable=True),
    )

    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert requested_limits and requested_limits[0] > 1
    assert submitted == [fsr.event_id], (
        "forecast/redecision lane must receive the guaranteed first processed "
        f"slot even when Day0 occupies the first small fetch page; submitted={submitted}"
    )


# --- antibody: reactor._build_regret_envelope_json must not mutate store.conn.row_factory --------
# Task #42 (2026-06-11): same footgun as the PRAGMA busy_timeout leak in the claim storm — a
# connection-global attribute mutated inside a shared-conn path is visible to every concurrent
# reader.  The cursor-local row_factory approach removes the mutation entirely.


def _sentinel_row_factory(cursor, row):  # noqa: ARG001
    """Detectable sentinel factory — identity observable with 'is'."""
    return row


def test_build_regret_envelope_json_does_not_mutate_store_conn_row_factory():
    """ANTIBODY (Task #42): reactor._build_regret_envelope_json snapshot fetch must not set
    store.conn.row_factory.  A sentinel factory pinned before the call must survive after it,
    including through the sqlite3.Error exception path (table absent)."""
    conn, store = _store()
    # Pin a sentinel — not sqlite3.Row, not None; identity is the assertion.
    conn.row_factory = _sentinel_row_factory

    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    reactor, rejected, _submitted = _reactor(store, gates=False)

    event = _day0_event()
    store.insert_or_ignore(event)

    # Process; the rejection writes a regret row and calls _build_regret_envelope_json,
    # which tries to fetch from executable_market_snapshots (absent in the in-memory schema =>
    # the query either returns None or raises — in both cases conn.row_factory must be untouched).
    reactor.process_pending(decision_time=decision_time, limit=1)

    assert conn.row_factory is _sentinel_row_factory, (
        "reactor._build_regret_envelope_json mutated store.conn.row_factory — "
        "cursor-local row_factory must be used instead of conn-level save/restore"
    )
