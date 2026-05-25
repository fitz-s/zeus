# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §13 event reactor no-bypass contract.
from __future__ import annotations

import sqlite3
import json
import hashlib
from dataclasses import replace
from datetime import datetime, timezone

from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle
from src.events.event_store import EventStore
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    MarketBookEventPayload,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)
from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor, ReactorConfig
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger


def _store() -> tuple[sqlite3.Connection, EventStore]:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventStore(conn)


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
        live_authority_status="LIVE_AUTHORITY",
    )
    return make_day0_extreme_updated_event(
        entity_key=f"Chicago|2026-05-24|high|{key_suffix}",
        source="day0_observation",
        observed_at=payload.observation_time,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
    )


def _forecast_event(key_suffix: str = "a"):
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
        entity_key=f"Chicago|2026-05-24|high|{key_suffix}",
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


def test_event_cannot_bypass_source_truth():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    reactor, rejected, submitted = _reactor(store, gates=False)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.rejected == 1
    assert rejected[0][1] == "SOURCE_TRUTH"
    assert submitted == []


def test_market_channel_event_no_direct_stale_trade():
    _conn, store = _store()
    store.insert_or_ignore(_market_event())
    reactor, rejected, submitted = _reactor(store)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.rejection_reasons == ["MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE"]
    assert submitted == []


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
        config=ReactorConfig(tiny_live_max_orders_per_day=1),
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
        config=ReactorConfig(tiny_live_max_orders_per_day=1),
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
        config=ReactorConfig(tiny_live_max_orders_per_day=1),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert submitted == [first.event_id]

    store.insert_or_ignore(second)
    second_reactor, rejected, second_submitted = _reactor(
        store,
        config=ReactorConfig(tiny_live_max_orders_per_day=1),
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
        config=ReactorConfig(tiny_live_max_orders_per_day=2, tiny_live_max_notional_usd=5.0),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert submitted == [first.event_id]

    store.insert_or_ignore(second)
    second_reactor, rejected, second_submitted = _reactor(
        store,
        config=ReactorConfig(tiny_live_max_orders_per_day=2, tiny_live_max_notional_usd=5.0),
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


def test_reactor_rejections_write_no_trade_regret_events():
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

    assert conn.execute("SELECT rejection_reason FROM no_trade_regret_events").fetchone()[0] == "MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE"


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
