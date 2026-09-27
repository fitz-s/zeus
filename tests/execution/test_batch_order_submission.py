# Created: 2026-07-02
# Last reused/audited: 2026-09-27
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "batch submit + safe prefixes" + architecture/invariants.yaml INV-28
#   -- W2.1 batch journal and active C3 cancel boundary.
"""W2.1 batch cancel orchestrator: INV-28 persist-before-side-effect
discipline at batch shape, chunking, mapping precedence, and partial-batch
failure semantics for cancel_commands_batch.

The batch SUBMIT orchestrator (``submit_orders_batch``) this file used to
also cover was deleted as dead code in the gate-stack simplification
(Phase 1, 2026-07-06) -- zero live callers."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.execution.batch_order_submission import cancel_commands_batch

_NOW = datetime(2026, 7, 2, tzinfo=timezone.utc)


@pytest.fixture
def mem_conn():
    from src.state.db import init_schema, init_schema_trade_only
    from src.state.collateral_ledger import init_collateral_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_schema_trade_only(c)
    init_collateral_schema(c)
    yield c
    c.close()


def _ensure_snapshot(conn, *, token_id: str = "yes-token", snapshot_id: str = "snap-1") -> str:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    if get_snapshot(conn, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        conn,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug="event-test",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={
                "source": "test",
                "token_id": token_id,
                "fee_rate_fraction": 0.0,
                "fee_rate_bps": 0.0,
                "fee_rate_source_field": "fee_rate_fraction",
                "fee_rate_raw_unit": "fraction",
            },
            token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.56"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


class FakeGatewayClient:
    """Duck-typed gateway fake: place_limit_orders_batch / cancel_orders_batch.

    ``submit_responses`` is a list of per-call response lists (one entry
    consumed per invocation) OR a single list reused for every call.
    ``fail_on_call_index`` (0-based) makes that specific call raise instead.
    """

    def __init__(
        self,
        submit_responses=None,
        cancel_responses=None,
        fail_submit_on_call_index: int | None = None,
        fail_cancel_on_call_index: int | None = None,
        submit_exception: BaseException | None = None,
        cancel_exception: BaseException | None = None,
    ):
        self.submit_responses = submit_responses or []
        self.cancel_responses = cancel_responses or []
        self.fail_submit_on_call_index = fail_submit_on_call_index
        self.fail_cancel_on_call_index = fail_cancel_on_call_index
        self.submit_exception = submit_exception or TimeoutError("submit_batch timed out")
        self.cancel_exception = cancel_exception or TimeoutError("cancel_batch timed out")
        self.submit_calls: list[list[Any]] = []
        self.cancel_calls: list[list[str]] = []

    def place_limit_orders_batch(self, envelopes):
        call_index = len(self.submit_calls)
        self.submit_calls.append(list(envelopes))
        if call_index == self.fail_submit_on_call_index:
            raise self.submit_exception
        return self.submit_responses[call_index]

    def cancel_orders_batch(self, order_ids):
        call_index = len(self.cancel_calls)
        self.cancel_calls.append(list(order_ids))
        if call_index == self.fail_cancel_on_call_index:
            raise self.cancel_exception
        return self.cancel_responses[call_index]


class TestInv24Allowlist:
    def test_inv24_allowlist_includes_batch_orchestrator(self):
        import src.data.polymarket_client as pc

        allowed_rel = {
            p.replace(str(pc._INV24_REPO_ROOT) + "/", "") for p in pc._INV24_ALLOWED_CALLER_ABS_PATHS
        }
        assert "src/execution/batch_order_submission.py" in allowed_rel


# ---------------------------------------------------------------------------
# cancel_commands_batch
# ---------------------------------------------------------------------------


def _seed_ackable_command(conn, *, command_id: str, token_id: str = "yes-token", venue_order_id: str = "vord-0") -> None:
    """Persist a command through ACKED state with a venue_order_id, the
    precondition cancel_commands_batch requires (mirrors
    request_cancel_for_command's own precondition)."""
    from src.execution.command_bus import IntentKind as _IntentKind
    from src.state.venue_command_repo import append_event, insert_command, insert_submission_envelope
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    snapshot_id = _ensure_snapshot(conn, token_id=token_id, snapshot_id=f"snap-{command_id}")
    envelope_id = f"env-{command_id}"
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2", sdk_version="test", host="https://clob-v2.polymarket.com",
            chain_id=137, funder_address="0xfunder", condition_id="condition-test", question_id="question-test",
            yes_token_id=token_id, no_token_id=f"{token_id}-no", selected_outcome_token_id=token_id,
            outcome_label="YES", side="SELL", price=Decimal("0.50"), size=Decimal("10"), order_type="GTC",
            post_only=True, tick_size=Decimal("0.01"), min_order_size=Decimal("0.01"), neg_risk=False,
            fee_details={"source": "test", "token_id": token_id, "fee_rate_fraction": 0.0, "fee_rate_bps": 0.0,
                         "fee_rate_source_field": "fee_rate_fraction", "fee_rate_raw_unit": "fraction"},
            canonical_pre_sign_payload_hash="a" * 64, signed_order=None, signed_order_hash=None,
            raw_request_hash="b" * 64, raw_response_json=None, order_id=None, trade_ids=(), transaction_hashes=(),
            error_code=None, error_message=None, captured_at=_NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    insert_command(
        conn, command_id=command_id, snapshot_id=snapshot_id, envelope_id=envelope_id, position_id="pos-0",
        decision_id="decision-cancel", idempotency_key=command_id.ljust(32, "0")[:32],
        intent_kind=_IntentKind.EXIT.value, market_id="market-123", token_id=token_id, side="SELL",
        size=10.0, price=0.50, created_at=_NOW.isoformat(), snapshot_checked_at=_NOW.isoformat(),
    )
    now = _NOW.isoformat()
    append_event(conn, command_id=command_id, event_type="SUBMIT_REQUESTED", occurred_at=now, payload={"batch": True})
    append_event(
        conn, command_id=command_id, event_type="SUBMIT_ACKED", occurred_at=now,
        payload={"order_id": venue_order_id, "batch": True},
    )
    conn.commit()


def _acked(venue_order_id: str) -> dict:
    return {"canceled": True, "orderID": venue_order_id}


def _not_canceled(venue_order_id: str, reason: str = "already filled") -> dict:
    return {"not_canceled": reason, "orderID": venue_order_id}


class TestCancelCommandsBatchPersistBeforeCall:
    def test_cancel_requested_committed_before_sdk_call(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-cancel-0", venue_order_id="vord-0")
        seen = {}

        class SpyClient(FakeGatewayClient):
            def cancel_orders_batch(self, order_ids):
                rows = mem_conn.execute(
                    "SELECT state FROM venue_commands WHERE command_id = 'cmd-cancel-0'"
                ).fetchall()
                seen["state_at_call_time"] = rows[0][0]
                return super().cancel_orders_batch(order_ids)

        client = SpyClient(cancel_responses=[[_acked("vord-0")]])
        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-cancel-0"])

        assert outcomes[0].status == "acked"
        assert seen["state_at_call_time"] == "CANCEL_PENDING"


class TestCancelCommandsBatchMapping:
    def test_acked_and_not_canceled_map_correctly(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-0", venue_order_id="vord-0")
        _seed_ackable_command(mem_conn, command_id="cmd-1", venue_order_id="vord-1")
        client = FakeGatewayClient(cancel_responses=[[_acked("vord-0"), _not_canceled("vord-1")]])

        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-0", "cmd-1"])

        assert [o.status for o in outcomes] == ["acked", "not_canceled"]

    def test_ambiguous_already_canceled_or_matched_does_not_ack_cancel(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-live-shape", venue_order_id="vord-live")
        client = FakeGatewayClient(
            cancel_responses=[
                [
                    _not_canceled(
                        "vord-live",
                        "order can't be found - already canceled or matched",
                    )
                ]
            ]
        )

        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-live-shape"])

        assert outcomes[0].status == "not_canceled"
        command = mem_conn.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'cmd-live-shape'"
        ).fetchone()
        event_types = [
            row["event_type"]
            for row in mem_conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = 'cmd-live-shape' ORDER BY sequence_no"
            )
        ]
        assert command["state"] == "REVIEW_REQUIRED"
        assert event_types[-2:] == ["CANCEL_REQUESTED", "CANCEL_FAILED"]
        assert "CANCEL_ACKED" not in event_types

    def test_not_requestable_command_skipped_without_blocking_chunk(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-good", venue_order_id="vord-good")
        client = FakeGatewayClient(cancel_responses=[[_acked("vord-good")]])

        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-missing", "cmd-good"])

        assert outcomes[0].status == "not_requestable"
        assert outcomes[1].status == "acked"
        assert client.cancel_calls == [["vord-good"]]


class TestCancelCommandsBatchPartialFailure:
    def test_sdk_exception_marks_ambiguous_and_halts_later_chunks(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-a", venue_order_id="vord-a")
        _seed_ackable_command(mem_conn, command_id="cmd-b", venue_order_id="vord-b")
        client = FakeGatewayClient(cancel_responses=[None], fail_cancel_on_call_index=0)

        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-a", "cmd-b"])

        # Both requestable commands are in the SAME chunk (well under
        # MAX_ORDERS_PER_BATCH) -- exercised together to prove ambiguous
        # failure applies to the whole chunk uniformly.
        assert all(o.status == "unknown" for o in outcomes)
        events = mem_conn.execute(
            "SELECT command_id FROM venue_command_events WHERE event_type = 'CANCEL_REPLACE_BLOCKED'"
        ).fetchall()
        assert sorted(r[0] for r in events) == ["cmd-a", "cmd-b"]


def _wal_cancel_fixture(tmp_path, monkeypatch):
    from src.state import db, write_coordinator
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema, init_schema_trade_only

    path = tmp_path / "trades.db"
    seed = sqlite3.connect(path)
    seed.row_factory = sqlite3.Row
    seed.execute("PRAGMA journal_mode=WAL")
    seed.execute("PRAGMA foreign_keys=ON")
    init_schema(seed)
    init_schema_trade_only(seed)
    init_collateral_schema(seed)
    _seed_ackable_command(seed, command_id="cmd-wal", venue_order_id="vord-wal")
    seed.close()

    coordinator = write_coordinator.WriteCoordinator({write_coordinator.DBIdentity.TRADE: path})
    monkeypatch.setattr(db, "_zeus_trade_db_path", lambda: path)
    monkeypatch.setattr(write_coordinator, "default_runtime_write_coordinator", lambda: coordinator)
    conn = sqlite3.connect(path, timeout=0.1)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=100")
    return path, conn, coordinator


def test_wal_writer_contention_defers_before_sdk_then_retries(tmp_path, monkeypatch):
    path, conn, _coordinator = _wal_cancel_fixture(tmp_path, monkeypatch)
    holder = sqlite3.connect(path)
    client = FakeGatewayClient(cancel_responses=[[_acked("vord-wal")]])
    try:
        holder.execute("BEGIN IMMEDIATE")
        first = cancel_commands_batch(conn, client, ["cmd-wal"])
        assert first[0].status == "not_attempted"
        assert first[0].error_message.startswith("batch_cancel_persist_failed:")
        assert client.cancel_calls == []
        assert conn.execute("SELECT state FROM venue_commands WHERE command_id='cmd-wal'").fetchone()[0] == "ACKED"
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_command_events WHERE command_id='cmd-wal' AND event_type='CANCEL_REQUESTED'"
        ).fetchone()[0] == 0

        holder.rollback()
        second = cancel_commands_batch(conn, client, ["cmd-wal"])
        assert second[0].status == "acked"
        assert client.cancel_calls == [["vord-wal"]]
    finally:
        holder.rollback()
        holder.close()
        conn.close()


def test_sdk_is_outside_lease_and_request_is_committed(tmp_path, monkeypatch):
    path, conn, coordinator = _wal_cancel_fixture(tmp_path, monkeypatch)

    class SpyClient(FakeGatewayClient):
        def cancel_orders_batch(self, order_ids):
            assert coordinator.current_owner_snapshot() == ()
            with sqlite3.connect(path) as reader:
                assert reader.execute(
                    "SELECT state FROM venue_commands WHERE command_id='cmd-wal'"
                ).fetchone()[0] == "CANCEL_PENDING"
            return super().cancel_orders_batch(order_ids)

    client = SpyClient(cancel_responses=[[_acked("vord-wal")]])
    try:
        result = cancel_commands_batch(conn, client, ["cmd-wal"])
        assert result[0].status == "acked"
        assert client.cancel_calls == [["vord-wal"]]
    finally:
        conn.close()


def test_ack_write_failure_marks_uncertain_without_second_sdk(tmp_path, monkeypatch):
    from src.execution import batch_order_submission
    from src.state import venue_command_repo

    _path, conn, _coordinator = _wal_cancel_fixture(tmp_path, monkeypatch)
    original = venue_command_repo.append_event
    failed = False

    def fail_ack_once(*args, **kwargs):
        nonlocal failed
        if kwargs.get("event_type") == "CANCEL_ACKED" and not failed:
            failed = True
            raise sqlite3.OperationalError("simulated ack write interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(venue_command_repo, "append_event", fail_ack_once)
    client = FakeGatewayClient(cancel_responses=[[_acked("vord-wal")]])
    try:
        first = batch_order_submission.cancel_commands_batch(conn, client, ["cmd-wal"])
        assert first[0].status == "unknown"
        assert first[0].error_message.startswith("batch_cancel_ack_persist_failed:")
        assert conn.execute("SELECT state FROM venue_commands WHERE command_id='cmd-wal'").fetchone()[0] == "REVIEW_REQUIRED"
        second = batch_order_submission.cancel_commands_batch(conn, client, ["cmd-wal"])
        assert second[0].status == "not_requestable"
        assert client.cancel_calls == [["vord-wal"]]
    finally:
        conn.close()


def test_second_request_failure_rolls_back_the_whole_chunk(tmp_path, monkeypatch):
    from src.state import venue_command_repo

    path, conn, _coordinator = _wal_cancel_fixture(tmp_path, monkeypatch)
    _seed_ackable_command(conn, command_id="cmd-wal2", venue_order_id="vord-wal2")
    original = venue_command_repo.append_event

    def fail_second_request(*args, **kwargs):
        if kwargs.get("command_id") == "cmd-wal2" and kwargs.get("event_type") == "CANCEL_REQUESTED":
            raise sqlite3.OperationalError("second request interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(venue_command_repo, "append_event", fail_second_request)
    client = FakeGatewayClient(cancel_responses=[[_acked("vord-wal"), _acked("vord-wal2")]])
    try:
        outcomes = cancel_commands_batch(conn, client, ["cmd-wal", "cmd-wal2"])
        assert [outcome.status for outcome in outcomes] == ["not_attempted", "not_attempted"]
        assert client.cancel_calls == []
        with sqlite3.connect(path) as reader:
            assert reader.execute(
                "SELECT command_id, state FROM venue_commands WHERE command_id IN ('cmd-wal', 'cmd-wal2') ORDER BY command_id"
            ).fetchall() == [("cmd-wal", "ACKED"), ("cmd-wal2", "ACKED")]
            assert reader.execute(
                "SELECT COUNT(*) FROM venue_command_events WHERE event_type='CANCEL_REQUESTED'"
            ).fetchone()[0] == 0
    finally:
        conn.close()


def test_second_ack_failure_rolls_back_ack_then_marks_both_uncertain(tmp_path, monkeypatch):
    from src.state import venue_command_repo

    path, conn, _coordinator = _wal_cancel_fixture(tmp_path, monkeypatch)
    _seed_ackable_command(conn, command_id="cmd-wal2", venue_order_id="vord-wal2")
    original = venue_command_repo.append_event

    def fail_second_ack(*args, **kwargs):
        if kwargs.get("command_id") == "cmd-wal2" and kwargs.get("event_type") == "CANCEL_ACKED":
            raise sqlite3.OperationalError("second ack interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(venue_command_repo, "append_event", fail_second_ack)
    client = FakeGatewayClient(cancel_responses=[[_acked("vord-wal"), _acked("vord-wal2")]])
    try:
        outcomes = cancel_commands_batch(conn, client, ["cmd-wal", "cmd-wal2"])
        assert [outcome.status for outcome in outcomes] == ["unknown", "unknown"]
        assert client.cancel_calls == [["vord-wal", "vord-wal2"]]
        with sqlite3.connect(path) as reader:
            assert reader.execute(
                "SELECT command_id, state FROM venue_commands WHERE command_id IN ('cmd-wal', 'cmd-wal2') ORDER BY command_id"
            ).fetchall() == [("cmd-wal", "REVIEW_REQUIRED"), ("cmd-wal2", "REVIEW_REQUIRED")]
            assert reader.execute(
                "SELECT COUNT(*) FROM venue_command_events WHERE event_type='CANCEL_ACKED'"
            ).fetchone()[0] == 0
    finally:
        conn.close()


def test_batch_never_commits_an_ambient_transaction(mem_conn):
    _seed_ackable_command(mem_conn, command_id="cmd-outer", venue_order_id="vord-outer")
    mem_conn.execute("CREATE TABLE caller_work (value TEXT)")
    mem_conn.execute("INSERT INTO caller_work VALUES ('uncommitted')")
    client = FakeGatewayClient(cancel_responses=[[_acked("vord-outer")]])
    result = cancel_commands_batch(mem_conn, client, ["cmd-outer"])
    assert result[0].status == "not_attempted"
    assert result[0].error_message == "batch_cancel_persist_failed:RuntimeError"
    assert mem_conn.in_transaction
    assert mem_conn.execute("SELECT value FROM caller_work").fetchone()[0] == "uncommitted"
    assert client.cancel_calls == []
