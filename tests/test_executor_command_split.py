# Lifecycle: created=2026-04-26; last_reviewed=2026-04-29; last_reused=2026-04-29
# Purpose: Lock executor command split phase ordering and ACK invariants.
# Reuse: Run when venue command persistence, live order submission, or ACK handling changes.
# Created: 2026-04-26
# Last reused/audited: 2026-04-29
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S3
"""INV-30 relationship tests: executor split build/persist/submit/ack.

Each test names the relationship it locks, not just the function.
See implementation_plan.md §P1.S3 for the full phase-order spec.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch, call
import json

import pytest

_NOW = datetime(2026, 4, 27, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn():
    """In-memory DB with full schema (venue_commands + venue_command_events)."""
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _cutover_guard_live_enabled(monkeypatch):
    """This file tests command-journal ordering, not cutover gating."""
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)


def _ensure_snapshot(conn, *, token_id: str, snapshot_id: str | None = None) -> str:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = snapshot_id or f"snap-{token_id}"
    if get_snapshot(conn, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
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


def _ensure_envelope(
    conn,
    *,
    token_id: str,
    envelope_id: str | None = None,
    side: str = "BUY",
    price: float | Decimal = Decimal("0.50"),
    size: float | Decimal = Decimal("10"),
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
    envelope_id = envelope_id or f"env-{token_id}-{side}-{price_dec}-{size_dec}"
    if conn.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            side=side,
            price=price_dec,
            size=size_dec,
            order_type="GTC",
            post_only=False,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            neg_risk=False,
            fee_details={
                "source": "test",
                "token_id": token_id,
                "fee_rate_fraction": 0.0,
                "fee_rate_bps": 0.0,
                "fee_rate_source_field": "fee_rate_fraction",
                "fee_rate_raw_unit": "fraction",
            },
            canonical_pre_sign_payload_hash="d" * 64,
            signed_order=None,
            signed_order_hash=None,
            raw_request_hash="e" * 64,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=_NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


_DEFAULT_CONTEXT = object()


def _decision_source_context(**overrides):
    from src.contracts.execution_intent import DecisionSourceContext

    fields = {
        "source_id": "tigge",
        "model_family": "ecmwf_ifs025",
        "forecast_issue_time": "2026-04-26T00:00:00+00:00",
        "forecast_valid_time": "2026-04-26T06:00:00+00:00",
        "forecast_fetch_time": "2026-04-26T01:00:00+00:00",
        "forecast_available_at": "2026-04-26T00:30:00+00:00",
        "raw_payload_hash": "a" * 64,
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "decision_time": "2026-04-26T02:00:00+00:00",
        "decision_time_status": "OK",
    }
    fields.update(overrides)
    return DecisionSourceContext(**fields)


def _make_entry_intent(
    conn=None,
    limit_price: float = 0.55,
    token_id: str = "tok-" + "0" * 36,
    decision_source_context=_DEFAULT_CONTEXT,
) -> object:
    """Build a minimal ExecutionIntent that passes the ExecutionPrice guard."""
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts import Direction
    from src.contracts.slippage_bps import SlippageBps

    snapshot_id = f"snap-{token_id}"
    if conn is not None:
        _ensure_snapshot(conn, token_id=token_id, snapshot_id=snapshot_id)
    if decision_source_context is _DEFAULT_CONTEXT:
        decision_source_context = _decision_source_context()
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="mkt-test-001",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
        decision_source_context=decision_source_context,
    )


def _make_exit_intent(
    conn=None,
    trade_id: str = "trd-exit-001",
    token_id: str = "tok-" + "1" * 36,
    shares: float = 10.0,
    current_price: float = 0.55,
) -> object:
    """Build a minimal ExitOrderIntent."""
    from src.execution.executor import create_exit_order_intent

    snapshot_id = f"snap-{token_id}"
    if conn is not None:
        _ensure_snapshot(conn, token_id=token_id, snapshot_id=snapshot_id)
    return create_exit_order_intent(
        trade_id=trade_id,
        token_id=token_id,
        shares=shares,
        current_price=current_price,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
    )


def _capture_bound_submission_envelope(mock_client):
    bound = {}
    mock_client.bind_submission_envelope.side_effect = lambda envelope: bound.__setitem__("envelope", envelope)
    return bound


def _final_submit_result(
    bound: dict,
    *,
    order_id: str | None = None,
    status: str = "LIVE",
    success: bool | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    envelope = bound.get("envelope")
    if envelope is None:
        raise AssertionError("test client did not receive a bound submission envelope")
    raw_payload = {"status": status}
    if order_id is not None:
        raw_payload["orderID"] = order_id
    if success is not None:
        raw_payload["success"] = success
    changes = {
        "raw_response_json": json.dumps(raw_payload, sort_keys=True, separators=(",", ":")),
        "order_id": order_id,
    }
    if error_code is not None:
        changes["error_code"] = error_code
        changes["error_message"] = error_message or ""
    final = envelope.with_updates(**changes)
    result = {
        "status": status,
        "_venue_submission_envelope": final.to_dict(),
    }
    if order_id is not None:
        result["orderID"] = order_id
    if success is not None:
        result["success"] = success
    if error_code is not None:
        result["errorCode"] = error_code
        result["errorMessage"] = error_message or ""
    return result


# ---------------------------------------------------------------------------
# TestLiveOrderCommandSplit — entry path (_live_order / IntentKind.ENTRY)
# ---------------------------------------------------------------------------

class TestLiveOrderCommandSplit:
    """INV-30: _live_order must persist before it submits."""

    def test_persist_precedes_submit(self, mem_conn):
        """insert_command must run before place_limit_order.

        Uses a call-order spy: both insert_command and place_limit_order are
        wrapped; the spy records the order of calls. Assert insert_command
        index < place_limit_order index.
        """
        from src.execution.executor import _live_order

        call_log: list[str] = []

        real_insert = None
        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def spy_insert(*args, **kwargs):
            call_log.append("insert_command")
            return _real_insert(*args, **kwargs)

        intent = _make_entry_intent(mem_conn)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=spy_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)

            def spy_place(**kwargs):
                call_log.append("place_limit_order")
                return _final_submit_result(bound, order_id="ord-test-001")

            mock_inst.place_limit_order.side_effect = spy_place

            _live_order(
                trade_id="trd-001",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-001",
            )

        assert "insert_command" in call_log, "insert_command must have been called"
        assert "place_limit_order" in call_log, "place_limit_order must have been called"
        assert call_log.index("insert_command") < call_log.index("place_limit_order"), (
            f"INV-30: insert_command must precede place_limit_order; call order was {call_log}"
        )
        bound_envelope = mock_inst.bind_submission_envelope.call_args.args[0]
        assert bound_envelope.condition_id == "condition-test"
        assert bound_envelope.selected_outcome_token_id == intent.token_id

    def test_entry_submit_requested_persists_execution_capability_proof(self, mem_conn, monkeypatch):
        """Entry SUBMIT_REQUESTED carries one pre-submit capability proof."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-entry-capability")
            )

            result = _live_order(
                trade_id="trd-entry-capability",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-capability",
            )

        assert result.status == "pending"
        row = mem_conn.execute(
            "SELECT command_id FROM venue_commands WHERE position_id = ?",
            ("trd-entry-capability",),
        ).fetchone()
        events = list_events(mem_conn, row["command_id"])
        requested = [event for event in events if event["event_type"] == "SUBMIT_REQUESTED"]
        payload = json.loads(requested[0]["payload_json"])
        capability = payload["execution_capability"]

        assert capability["action"] == "ENTRY"
        assert capability["intent_kind"] == "ENTRY"
        assert capability["allowed"] is True
        assert len(capability["capability_id"]) == 32
        assert capability["command_id"] == row["command_id"]
        assert capability["executable_snapshot_id"] == intent.executable_snapshot_id
        components_by_name = {component["component"]: component for component in capability["components"]}
        assert {component["component"] for component in capability["components"]} >= {
            "cutover_guard",
            "risk_allocator",
            "order_type_selection",
            "heartbeat_supervisor",
            "ws_gap_guard",
            "collateral_ledger",
            "decision_source_integrity",
            "executable_snapshot_gate",
        }
        assert components_by_name["decision_source_integrity"]["allowed"] is True
        assert components_by_name["decision_source_integrity"]["details"]["source_id"] == "tigge"
        assert components_by_name["decision_source_integrity"]["details"]["degradation_level"] == "OK"

    @pytest.mark.parametrize(
        ("context", "expected_reason"),
        [
            (None, "missing_decision_source_context"),
            (
                _decision_source_context(degradation_level="DEGRADED_FORECAST_FALLBACK"),
                "invalid_decision_source_context:forecast_degraded:DEGRADED_FORECAST_FALLBACK",
            ),
            (
                _decision_source_context(forecast_source_role="monitor_fallback"),
                "invalid_decision_source_context:forecast_role_not_entry_primary:monitor_fallback",
            ),
            (
                _decision_source_context(forecast_fetch_time="2026-04-26T03:00:00+00:00"),
                "invalid_decision_source_context:forecast_fetch_after_decision",
            ),
            (
                _decision_source_context(raw_payload_hash="not-a-valid-hash"),
                "invalid_decision_source_context:invalid_raw_payload_hash",
            ),
        ],
    )
    def test_entry_rejects_missing_decision_source_context_before_command_persistence(
        self,
        mem_conn,
        context,
        expected_reason,
    ):
        """Entry source evidence must fail closed before command persistence."""
        from src.execution.executor import _live_order

        intent = _make_entry_intent(mem_conn, decision_source_context=context)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-entry-source-missing",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-source-missing",
            )

        command_count = mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0]
        assert result.status == "rejected"
        assert result.reason is not None
        assert result.reason.startswith("decision_source_integrity:")
        assert expected_reason in result.reason
        assert command_count == 0
        mock_inst.place_limit_order.assert_not_called()

    def test_own_connection_commits_pre_submit_rows_before_sdk_submit(self, tmp_path, monkeypatch):
        """Own-connection live entry must make command/envelope durable before SDK contact."""
        import src.execution.executor as executor_module
        from src.execution.executor import execute_intent
        from src.state.db import get_connection, init_schema

        token_id = "tok-" + "2" * 36
        db_path = tmp_path / "entry-pre-submit-durable.db"
        setup_conn = get_connection(db_path)
        init_schema(setup_conn)
        intent = _make_entry_intent(setup_conn, token_id=token_id)
        setup_conn.commit()
        setup_conn.close()

        def _trade_conn():
            conn = get_connection(db_path)
            init_schema(conn)
            return conn

        monkeypatch.setattr(executor_module, "get_trade_connection_with_world", _trade_conn)
        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )
        monkeypatch.setattr(executor_module, "alert_trade", lambda *args, **kwargs: None)

        observed = {}

        class DurableVisibilityClient:
            def v2_preflight(self):
                observed["preflight"] = True

            def bind_submission_envelope(self, envelope):
                observed["bound_envelope"] = envelope

            def place_limit_order(self, **kwargs):
                read_conn = get_connection(db_path)
                init_schema(read_conn)
                try:
                    command_rows = read_conn.execute(
                        """
                        SELECT command_id, snapshot_id, envelope_id, state
                        FROM venue_commands
                        WHERE snapshot_id = ?
                        """,
                        (intent.executable_snapshot_id,),
                    ).fetchall()
                    envelope_count = read_conn.execute(
                        "SELECT COUNT(*) FROM venue_submission_envelopes"
                    ).fetchone()[0]
                    requested_count = read_conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM venue_command_events
                        WHERE command_id = ? AND event_type = 'SUBMIT_REQUESTED'
                        """,
                        (command_rows[0]["command_id"] if command_rows else "",),
                    ).fetchone()[0]
                finally:
                    read_conn.close()

                observed["submit_kwargs"] = kwargs
                observed["durable_command_count"] = len(command_rows)
                observed["durable_command_state"] = command_rows[0]["state"] if command_rows else None
                observed["durable_envelope_count"] = envelope_count
                observed["durable_submit_requested_count"] = requested_count
                final = observed["bound_envelope"].with_updates(
                    raw_response_json='{"orderID":"ord-entry-durable"}',
                    order_id="ord-entry-durable",
                )
                return {
                    "orderID": "ord-entry-durable",
                    "status": "LIVE",
                    "_venue_submission_envelope": final.to_dict(),
                }

        with patch("src.data.polymarket_client.PolymarketClient", return_value=DurableVisibilityClient()):
            result = execute_intent(
                intent,
                edge_vwmp=0.35,
                label="39-40°F",
                decision_id="dec-entry-durable",
            )

        assert result.status == "pending"
        assert observed["preflight"] is True
        assert observed["durable_command_count"] == 1
        assert observed["durable_command_state"] == "SUBMITTING"
        assert observed["durable_envelope_count"] == 1
        assert observed["durable_submit_requested_count"] == 1
        assert observed["submit_kwargs"]["token_id"] == token_id

    def test_entry_persists_final_submit_envelope_as_append_only_row(self, mem_conn, monkeypatch):
        """Entry ACK keeps command on pre-submit envelope and appends final SDK envelope."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn)

        class FinalEnvelopeClient:
            def __init__(self):
                self.bound_envelope = None

            def v2_preflight(self):
                return None

            def bind_submission_envelope(self, envelope):
                self.bound_envelope = envelope

            def place_limit_order(self, **kwargs):
                assert self.bound_envelope is not None
                final = self.bound_envelope.with_updates(
                    raw_response_json='{"orderID":"ord-entry-final"}',
                    order_id="ord-entry-final",
                )
                return {
                    "orderID": "ord-entry-final",
                    "status": "LIVE",
                    "_venue_submission_envelope": final.to_dict(),
                }

        with patch("src.data.polymarket_client.PolymarketClient", return_value=FinalEnvelopeClient()):
            result = _live_order(
                trade_id="trd-entry-final-envelope",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-final-envelope",
            )

        assert result.status == "pending"
        command = mem_conn.execute(
            "SELECT command_id, envelope_id FROM venue_commands WHERE position_id = ?",
            ("trd-entry-final-envelope",),
        ).fetchone()
        rows = mem_conn.execute(
            "SELECT envelope_id, order_id FROM venue_submission_envelopes ORDER BY captured_at, envelope_id"
        ).fetchall()
        assert len(rows) == 2
        pre_ids = {row["envelope_id"] for row in rows if row["order_id"] is None}
        final_rows = [row for row in rows if row["order_id"] == "ord-entry-final"]
        assert command["envelope_id"] in pre_ids
        assert len(final_rows) == 1
        assert final_rows[0]["envelope_id"] != command["envelope_id"]

        ack = [e for e in list_events(mem_conn, command["command_id"]) if e["event_type"] == "SUBMIT_ACKED"][0]
        payload = json.loads(ack["payload_json"])
        assert payload["final_submission_envelope_id"] == final_rows[0]["envelope_id"]
        assert payload["final_submission_envelope_stage"] == "post_submit_result"

    def test_entry_missing_final_submit_envelope_goes_review_required(self, mem_conn, monkeypatch):
        """A post-submit ACK-shaped result without final envelope cannot become ACKED."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.return_value = {"orderID": "ord-entry-no-envelope", "status": "LIVE"}

            result = _live_order(
                trade_id="trd-entry-no-final-envelope",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-no-final-envelope",
            )

        assert result.status == "unknown_side_effect"
        assert result.command_state == "REVIEW_REQUIRED"
        command = mem_conn.execute(
            "SELECT command_id, state FROM venue_commands WHERE position_id = ?",
            ("trd-entry-no-final-envelope",),
        ).fetchone()
        assert command["state"] == "REVIEW_REQUIRED"
        events = list_events(mem_conn, command["command_id"])
        event_types = [event["event_type"] for event in events]
        assert "REVIEW_REQUIRED" in event_types
        assert "SUBMIT_ACKED" not in event_types
        assert "SUBMIT_REJECTED" not in event_types
        review = [event for event in events if event["event_type"] == "REVIEW_REQUIRED"][0]
        payload = json.loads(review["payload_json"])
        assert payload["reason"] == "final_submission_envelope_persistence_failed"
        assert payload["venue_order_id"] == "ord-entry-no-envelope"

    def test_submit_unknown_writes_event_with_side_effect_unknown(self, mem_conn):
        """Crash-injection drill: place_limit_order raises RuntimeError.

        M2: once place_limit_order may have crossed the venue side-effect
        boundary, the row must reach SUBMIT_UNKNOWN_SIDE_EFFECT via
        SUBMIT_TIMEOUT_UNKNOWN. The row is the recovery anchor.
        """
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import find_unresolved_commands, list_events

        intent = _make_entry_intent(mem_conn)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.side_effect = RuntimeError("simulated venue timeout")

            result = _live_order(
                trade_id="trd-002",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-002",
            )

        # The OrderResult reflects the unknown side-effect outcome.
        assert result.status == "unknown_side_effect", (
            f"Expected status=unknown_side_effect, got {result.status!r}"
        )
        assert result.reason is not None and "submit_unknown_side_effect" in result.reason, (
            f"Expected reason to contain 'submit_unknown_side_effect', got {result.reason!r}"
        )
        assert result.command_state == "SUBMIT_UNKNOWN_SIDE_EFFECT"

        # The durable record must show SUBMIT_UNKNOWN_SIDE_EFFECT (recovery can resolve).
        unresolved = find_unresolved_commands(mem_conn)
        assert len(unresolved) == 1, (
            f"Expected 1 unresolved command (SUBMIT_UNKNOWN_SIDE_EFFECT), found {len(unresolved)}: {unresolved}"
        )
        assert unresolved[0]["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT", (
            f"Expected state=SUBMIT_UNKNOWN_SIDE_EFFECT in journal, got {unresolved[0]['state']!r}"
        )

        # Check the event chain
        events = list_events(mem_conn, unresolved[0]["command_id"])
        event_types = [e["event_type"] for e in events]
        assert "INTENT_CREATED" in event_types
        assert "SUBMIT_REQUESTED" in event_types
        assert "SUBMIT_TIMEOUT_UNKNOWN" in event_types

    def test_submit_rejected_writes_event_with_state_rejected(self, mem_conn):
        """place_limit_order returns None -> REVIEW_REQUIRED, not normal rejected."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        real_insert = None
        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.return_value = None

            result = _live_order(
                trade_id="trd-003",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-003",
            )

        assert result.status == "unknown_side_effect"
        assert result.command_state == "REVIEW_REQUIRED"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REVIEW_REQUIRED", (
            f"Expected state=REVIEW_REQUIRED after None return, got {cmd['state']!r}"
        )

    def test_submit_missing_order_id_rejects_without_submit_acked(self, mem_conn):
        """place_limit_order dict without order id -> REJECTED, not ACKED."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command, list_events

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, success=True, status="LIVE")
            )

            result = _live_order(
                trade_id="trd-missing-order-id",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-missing-order-id",
            )

        assert result.status == "rejected"
        assert result.reason == "missing_order_id"
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REJECTED"
        event_types = [event["event_type"] for event in list_events(mem_conn, command_ids_seen[0])]
        assert "SUBMIT_REJECTED" in event_types
        assert "SUBMIT_ACKED" not in event_types

    def test_submit_success_false_rejects_without_submit_acked(self, mem_conn):
        """place_limit_order success=false -> REJECTED with venue error code."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command, list_events

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = lambda **kwargs: _final_submit_result(
                bound,
                success=False,
                status="rejected",
                error_code="INSUFFICIENT_BALANCE",
                error_message="not enough funds",
            )

            result = _live_order(
                trade_id="trd-success-false",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-success-false",
            )

        assert result.status == "rejected"
        assert result.reason == "INSUFFICIENT_BALANCE"
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REJECTED"
        event_types = [event["event_type"] for event in list_events(mem_conn, command_ids_seen[0])]
        assert "SUBMIT_REJECTED" in event_types
        assert "SUBMIT_ACKED" not in event_types
        rejected = [event for event in list_events(mem_conn, command_ids_seen[0]) if event["event_type"] == "SUBMIT_REJECTED"][0]
        payload = json.loads(rejected["payload_json"])
        assert payload["final_submission_envelope_id"]

    def test_submit_acked_writes_event_with_state_acked(self, mem_conn):
        """place_limit_order returns orderID -> state=ACKED, venue_order_id set."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-acked-001")
            )

            result = _live_order(
                trade_id="trd-004",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-004",
            )

        assert result.status == "pending"  # OrderResult status is 'pending' until fill
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "ACKED", (
            f"Expected state=ACKED after successful ack, got {cmd['state']!r}"
        )

    def test_idempotency_key_collision_raises_before_submit(self, mem_conn):
        """Duplicate idempotency key: place_limit_order must NOT be called.

        Insert a command with a known idempotency_key first, then run a second
        _live_order with inputs that hash to the same key. The second call must
        return a rejected OrderResult without calling place_limit_order.
        """
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command

        intent = _make_entry_intent(mem_conn, token_id="tok-idem" + "0" * 33)

        # Pre-insert a command with the key that _live_order will derive
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-collision",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-existing-cmd",
            snapshot_id=intent.executable_snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=intent.token_id,
                price=intent.limit_price,
                size=18.19,
            ),
            position_id="trd-pre",
            decision_id="dec-collision",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-005",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-collision",  # same decision_id => same key
            )

        assert result.status == "rejected", (
            f"Expected rejected on idempotency collision, got {result.status!r}"
        )
        assert result.reason is not None and "idempotency_collision" in result.reason, (
            f"Expected reason containing 'idempotency_collision', got {result.reason!r}"
        )
        # Most importantly: place_limit_order was never reached
        mock_inst.place_limit_order.assert_not_called()

    def test_v2_preflight_failure_writes_rejected_event(self, mem_conn):
        """V2 preflight raises V2PreflightError -> state=REJECTED, place_limit_order not called.

        The command is already persisted (SUBMITTING) when preflight runs.
        On preflight failure, a SUBMIT_REJECTED event must be appended so
        the row reaches a terminal state.
        """
        from src.execution.executor import _live_order
        from src.data.polymarket_client import V2PreflightError
        from src.state.venue_command_repo import get_command

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.side_effect = V2PreflightError("endpoint down")

            result = _live_order(
                trade_id="trd-006",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-006",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "v2_preflight_failed" in result.reason
        mock_inst.place_limit_order.assert_not_called()

        # Command row must be REJECTED (not stuck in SUBMITTING)
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REJECTED", (
            f"Expected state=REJECTED after v2_preflight failure, got {cmd['state']!r}"
        )

    def test_executionprice_validation_runs_before_persist(self, mem_conn):
        """NaN limit_price: ExecutionPrice rejects before any DB write.

        No venue_commands row should be inserted when the price is malformed.
        """
        from src.execution.executor import _live_order
        import math

        # Build an intent with NaN limit_price; bypass the constructor if needed
        # by constructing manually via dataclass replace equivalent
        from src.contracts.execution_intent import ExecutionIntent
        from src.contracts import Direction
        import dataclasses

        base_intent = _make_entry_intent(mem_conn)
        nan_intent = dataclasses.replace(base_intent, limit_price=float("nan"))

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst

            result = _live_order(
                trade_id="trd-007",
                intent=nan_intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-007",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "malformed_limit_price" in result.reason

        # No command row should have been persisted
        row_count = mem_conn.execute(
            "SELECT COUNT(*) FROM venue_commands"
        ).fetchone()[0]
        assert row_count == 0, (
            f"Expected no venue_commands rows after ExecutionPrice rejection, found {row_count}"
        )
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# TestExitOrderCommandSplit — exit path (execute_exit_order / IntentKind.EXIT)
# ---------------------------------------------------------------------------

class TestExitOrderCommandSplit:
    """INV-30: execute_exit_order must persist before it submits."""

    def test_exit_persist_precedes_submit(self, mem_conn):
        """insert_command must run before place_limit_order (exit path)."""
        from src.execution.executor import execute_exit_order

        call_log: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def spy_insert(*args, **kwargs):
            call_log.append("insert_command")
            return _real_insert(*args, **kwargs)

        intent = _make_exit_intent(mem_conn)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=spy_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)

            def spy_place(**kwargs):
                call_log.append("place_limit_order")
                return _final_submit_result(bound, order_id="ord-exit-001")

            mock_inst.place_limit_order.side_effect = spy_place

            execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-001",
            )

        assert "insert_command" in call_log, "insert_command must have been called"
        assert "place_limit_order" in call_log, "place_limit_order must have been called"
        assert call_log.index("insert_command") < call_log.index("place_limit_order"), (
            f"INV-30: insert_command must precede place_limit_order; call order was {call_log}"
        )

    def test_exit_submit_requested_persists_execution_capability_proof(self, mem_conn, monkeypatch):
        """Exit SUBMIT_REQUESTED carries one pre-submit capability proof."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-capability")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-exit-capability")
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-capability",
            )

        assert result.status == "pending"
        row = mem_conn.execute(
            "SELECT command_id FROM venue_commands WHERE position_id = ?",
            ("trd-exit-capability",),
        ).fetchone()
        events = list_events(mem_conn, row["command_id"])
        requested = [event for event in events if event["event_type"] == "SUBMIT_REQUESTED"]
        payload = json.loads(requested[0]["payload_json"])
        capability = payload["execution_capability"]

        assert capability["action"] == "EXIT"
        assert capability["intent_kind"] == "EXIT"
        assert capability["order_type"] == "FOK"
        assert capability["allowed"] is True
        assert len(capability["capability_id"]) == 32
        assert capability["command_id"] == row["command_id"]
        assert capability["executable_snapshot_id"] == intent.executable_snapshot_id
        components_by_name = {component["component"]: component for component in capability["components"]}
        assert {component["component"] for component in capability["components"]} >= {
            "cutover_guard",
            "risk_allocator",
            "order_type_selection",
            "heartbeat_supervisor",
            "ws_gap_guard",
            "collateral_ledger",
            "replacement_sell_guard",
            "decision_source_integrity",
            "executable_snapshot_gate",
        }
        assert components_by_name["decision_source_integrity"]["allowed"] is True
        assert components_by_name["decision_source_integrity"]["reason"] == "not_applicable_reduce_only"

    def test_exit_binds_pre_submit_and_persists_final_submit_envelope(self, mem_conn, monkeypatch):
        """Exit ACK uses the U1 pre-submit envelope and appends final SDK facts."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-final-envelope")

        class FinalEnvelopeClient:
            def __init__(self):
                self.bound_envelope = None

            def bind_submission_envelope(self, envelope):
                self.bound_envelope = envelope

            def place_limit_order(self, **kwargs):
                assert self.bound_envelope is not None
                assert self.bound_envelope.side == "SELL"
                final = self.bound_envelope.with_updates(
                    raw_response_json='{"orderID":"ord-exit-final"}',
                    order_id="ord-exit-final",
                )
                return {
                    "orderID": "ord-exit-final",
                    "status": "LIVE",
                    "_venue_submission_envelope": final.to_dict(),
                }

        with patch("src.data.polymarket_client.PolymarketClient", return_value=FinalEnvelopeClient()):
            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-final-envelope",
            )

        assert result.status == "pending"
        command = mem_conn.execute(
            "SELECT command_id, envelope_id FROM venue_commands WHERE position_id = ?",
            ("trd-exit-final-envelope",),
        ).fetchone()
        rows = mem_conn.execute(
            "SELECT envelope_id, order_id, side FROM venue_submission_envelopes"
        ).fetchall()
        assert len(rows) == 2
        final_rows = [row for row in rows if row["order_id"] == "ord-exit-final"]
        assert len(final_rows) == 1
        assert final_rows[0]["side"] == "SELL"
        assert final_rows[0]["envelope_id"] != command["envelope_id"]

        ack = [e for e in list_events(mem_conn, command["command_id"]) if e["event_type"] == "SUBMIT_ACKED"][0]
        payload = json.loads(ack["payload_json"])
        assert payload["final_submission_envelope_id"] == final_rows[0]["envelope_id"]
        assert payload["final_submission_envelope_stage"] == "post_submit_result"

    def test_exit_submit_rejected_persists_final_submit_envelope(self, mem_conn, monkeypatch):
        """Exit SUBMIT_REJECTED must cite the final SDK envelope row."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-final-rejected")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = lambda **kwargs: _final_submit_result(
                bound,
                success=False,
                status="rejected",
                error_code="POST_ONLY_REJECTED",
                error_message="would cross spread",
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-final-rejected",
            )

        assert result.status == "rejected"
        assert result.reason == "POST_ONLY_REJECTED"
        command = mem_conn.execute(
            "SELECT command_id, state FROM venue_commands WHERE position_id = ?",
            ("trd-exit-final-rejected",),
        ).fetchone()
        assert command["state"] == "REJECTED"
        rejected = [
            event for event in list_events(mem_conn, command["command_id"])
            if event["event_type"] == "SUBMIT_REJECTED"
        ][0]
        payload = json.loads(rejected["payload_json"])
        assert payload["final_submission_envelope_id"]
        row = mem_conn.execute(
            "SELECT error_code FROM venue_submission_envelopes WHERE envelope_id = ?",
            (payload["final_submission_envelope_id"],),
        ).fetchone()
        assert row["error_code"] == "POST_ONLY_REJECTED"

    def test_exit_submit_unknown_writes_event_with_side_effect_unknown(self, mem_conn):
        """Crash-injection drill (exit path): place_limit_order raises.

        M2: state must reach SUBMIT_UNKNOWN_SIDE_EFFECT via
        SUBMIT_TIMEOUT_UNKNOWN.
        """
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import find_unresolved_commands, list_events

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-002")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.place_limit_order.side_effect = RuntimeError("simulated exit crash")

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-002",
            )

        assert result.status == "unknown_side_effect"
        assert result.reason is not None and "submit_unknown_side_effect" in result.reason
        assert result.command_state == "SUBMIT_UNKNOWN_SIDE_EFFECT"

        unresolved = find_unresolved_commands(mem_conn)
        assert len(unresolved) == 1, (
            f"Expected 1 unresolved command (SUBMIT_UNKNOWN_SIDE_EFFECT exit), found {len(unresolved)}"
        )
        assert unresolved[0]["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT"
        assert unresolved[0]["intent_kind"] == "EXIT"

        events = list_events(mem_conn, unresolved[0]["command_id"])
        event_types = [e["event_type"] for e in events]
        assert "SUBMIT_TIMEOUT_UNKNOWN" in event_types

    def test_exit_submit_rejected_writes_event_with_state_rejected(self, mem_conn):
        """place_limit_order returns None (exit path) -> REVIEW_REQUIRED."""
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import get_command

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-003")
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.place_limit_order.return_value = None

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-003",
            )

        assert result.status == "unknown_side_effect"
        assert result.command_state == "REVIEW_REQUIRED"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REVIEW_REQUIRED"

    def test_exit_submit_acked_writes_event_with_state_acked(self, mem_conn):
        """place_limit_order returns orderID (exit path) -> state=ACKED."""
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import get_command

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-004")
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-exit-acked-001")
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-004",
            )

        assert result.status == "pending"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "ACKED"

    def test_exit_idempotency_key_collision_raises_before_submit(self, mem_conn):
        """Duplicate idempotency key (exit path): place_limit_order not called."""
        from src.execution.executor import execute_exit_order, create_exit_order_intent
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command
        from src.contracts.tick_size import TickSize

        token_id = "tok-exit-idem" + "0" * 27
        shares = 10.0
        current_price = 0.55

        # Derive the limit_price the same way execute_exit_order will
        tick = TickSize.for_market(token_id=token_id)
        limit_price = tick.clamp_to_valid_range(current_price - tick.value)
        effective_shares = __import__("math").floor(shares * 100 + 1e-9) / 100.0

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-exit-collision",
            token_id=token_id,
            side="SELL",
            price=limit_price,
            size=effective_shares,
            intent_kind=IntentKind.EXIT,
        )
        snapshot_id = _ensure_snapshot(mem_conn, token_id=token_id)
        insert_command(
            mem_conn,
            command_id="pre-exit-cmd",
            snapshot_id=snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=token_id,
                side="SELL",
                price=limit_price,
                size=effective_shares,
            ),
            position_id="trd-exit-pre",
            decision_id="dec-exit-collision",
            idempotency_key=idem.value,
            intent_kind="EXIT",
            market_id=token_id,
            token_id=token_id,
            side="SELL",
            size=effective_shares,
            price=limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        mem_conn.commit()

        intent = create_exit_order_intent(
            trade_id="trd-exit-005",
            token_id=token_id,
            shares=shares,
            current_price=current_price,
        )

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-collision",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "idempotency_collision" in result.reason
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotency collision retry (MEDIUM-1) — both paths
# ---------------------------------------------------------------------------

class TestIdempotencyCollisionRetry:
    """Collision retry: second call with same key returns existing state, not raw exc."""

    def test_idempotency_collision_returns_existing_state_acked(self, mem_conn):
        """Insert command in ACKED state, attempt second insert with same key.

        OrderResult.status must be 'pending' and reason includes 'prior attempt acked'.
        """
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(mem_conn, token_id="tok-coll-acked" + "0" * 27)

        # Pre-insert a command and advance it to ACKED state
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-acked",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-acked",
            snapshot_id=intent.executable_snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=intent.token_id,
                price=intent.limit_price,
                size=18.19,
            ),
            position_id="trd-pre-acked",
            decision_id="dec-coll-acked",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        # Advance to ACKED via SUBMIT_REQUESTED -> SUBMIT_ACKED
        append_event(
            mem_conn,
            command_id="pre-cmd-acked",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-acked",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"venue_order_id": "acked-ord-001"},
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-acked",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-acked",
            )

        assert result.status == "pending", (
            f"Expected pending for ACKED collision, got {result.status!r}"
        )
        assert result.order_id == "acked-ord-001"
        assert result.external_order_id == "acked-ord-001"
        assert result.reason is not None and "prior attempt acked" in result.reason, (
            f"Expected reason to contain 'prior attempt acked', got {result.reason!r}"
        )
        mock_inst.place_limit_order.assert_not_called()

    def test_idempotency_collision_with_filled_state_returns_pending(self, mem_conn):
        """Insert command in FILLED state; collision must return status=pending."""
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(mem_conn, token_id="tok-coll-filled" + "0" * 25)

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-filled",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-filled",
            snapshot_id=intent.executable_snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=intent.token_id,
                price=intent.limit_price,
                size=18.19,
            ),
            position_id="trd-pre-filled",
            decision_id="dec-coll-filled",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"venue_order_id": "fill-ord-001"},
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:00:02+00:00",
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-filled",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-filled",
            )

        assert result.status == "pending", (
            f"Expected pending for FILLED collision, got {result.status!r}"
        )
        assert result.order_id == "fill-ord-001"
        assert result.external_order_id == "fill-ord-001"
        assert result.reason is not None and "prior attempt filled" in result.reason
        mock_inst.place_limit_order.assert_not_called()

    def test_idempotency_collision_with_rejected_state_returns_rejected(self, mem_conn):
        """Insert command in REJECTED state; collision must return status=rejected."""
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(mem_conn, token_id="tok-coll-rejected" + "0" * 23)

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-rejected",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-rejected",
            snapshot_id=intent.executable_snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=intent.token_id,
                price=intent.limit_price,
                size=18.19,
            ),
            position_id="trd-pre-rejected",
            decision_id="dec-coll-rejected",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-rejected",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-rejected",
            event_type="SUBMIT_REJECTED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"reason": "v2_preflight_failed"},
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-rejected",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-rejected",
            )

        assert result.status == "rejected", (
            f"Expected rejected for REJECTED collision, got {result.status!r}"
        )
        assert result.reason is not None and "prior attempt" in result.reason
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Max slippage budget enforcement
# ---------------------------------------------------------------------------

def test_create_execution_intent_rejects_reprice_above_max_slippage():
    from src.contracts import EdgeContext, EntryMethod
    from src.execution.executor import create_execution_intent
    from src.types.market import Bin, BinEdge
    import numpy as np

    edge = BinEdge(
        bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
        direction="buy_yes",
        edge=0.20,
        ci_lower=0.03,
        ci_upper=0.31,
        p_model=0.70,
        p_market=0.50,
        p_posterior=0.70,
        entry_price=0.50,
        p_value=0.01,
        vwmp=0.50,
        forward_edge=0.20,
    )
    edge_context = EdgeContext(
        p_raw=np.array([0.70]),
        p_cal=np.array([0.70]),
        p_market=np.array([0.50]),
        p_posterior=0.70,
        forward_edge=0.20,
        alpha=1.0,
        confidence_band_upper=0.31,
        confidence_band_lower=0.03,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="test-snap",
        n_edges_found=1,
        n_edges_after_fdr=1,
    )

    with pytest.raises(ValueError, match="MAX_SLIPPAGE_EXCEEDED"):
        create_execution_intent(
            edge_context=edge_context,
            edge=edge,
            size_usd=5.0,
            mode="opening_hunt",
            market_id="m1",
            token_id="yes-token",
            no_token_id="no-token",
            repriced_limit_price=0.511,
            executable_snapshot_id="snap-limit",
            executable_snapshot_min_tick_size=Decimal("0.01"),
            executable_snapshot_min_order_size=Decimal("0.01"),
            executable_snapshot_neg_risk=False,
        )


# ---------------------------------------------------------------------------
# MAJOR-2 WARNING: synthetic decision_id emits warning
# ---------------------------------------------------------------------------

def test_synthetic_decision_id_emits_warning(mem_conn, caplog):
    """When decision_id is empty, executor emits WARNING about synthetic id."""
    from src.execution.executor import _live_order
    import logging

    intent = _make_entry_intent(mem_conn)

    with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        mock_inst.v2_preflight.return_value = None
        bound = _capture_bound_submission_envelope(mock_inst)
        mock_inst.place_limit_order.side_effect = (
            lambda **kwargs: _final_submit_result(bound, order_id="ord-synth-001")
        )

        with patch("src.execution.executor.alert_trade", lambda **kw: None):
            with caplog.at_level(logging.WARNING, logger="src.execution.executor"):
                result = _live_order(
                    trade_id="trd-synth",
                    intent=intent,
                    shares=18.19,
                    conn=mem_conn,
                    decision_id="",  # empty => synthetic
                )

    assert result.status == "pending"
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    synth_warnings = [m for m in warning_messages if "synthetic decision_id" in m]
    assert len(synth_warnings) >= 1, (
        f"Expected WARNING about synthetic decision_id, got: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# MEDIUM-3 payload shape: v2_preflight SUBMIT_REJECTED payload
# ---------------------------------------------------------------------------

def test_v2_preflight_payload_shape(mem_conn):
    """V2 preflight failure must write SUBMIT_REJECTED with payload {{reason: v2_preflight_failed}}."""
    from src.execution.executor import _live_order
    from src.data.polymarket_client import V2PreflightError
    from src.state.venue_command_repo import get_command, list_events

    intent = _make_entry_intent(mem_conn)
    command_ids_seen: list[str] = []

    import src.state.venue_command_repo as _repo
    _real_insert = _repo.insert_command

    def capturing_insert(*args, **kwargs):
        command_ids_seen.append(kwargs["command_id"])
        return _real_insert(*args, **kwargs)

    with patch(
        "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
    ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        mock_inst.v2_preflight.side_effect = V2PreflightError("endpoint down")

        result = _live_order(
            trade_id="trd-v2pf-payload",
            intent=intent,
            shares=18.19,
            conn=mem_conn,
            decision_id="dec-v2pf-payload",
        )

    assert result.status == "rejected"
    assert len(command_ids_seen) == 1

    events = list_events(mem_conn, command_ids_seen[0])
    rejected_events = [e for e in events if e["event_type"] == "SUBMIT_REJECTED"]
    assert len(rejected_events) == 1

    import json
    payload = rejected_events[0].get("payload_json") or "{}"
    payload_dict = json.loads(payload)
    assert payload_dict.get("reason") == "v2_preflight_failed", (
        f"Expected payload {{\"reason\": \"v2_preflight_failed\"}}, got {payload_dict}"
    )


# ---------------------------------------------------------------------------
# INV-30 manifest test — all enforced_by.tests entries must be collect-able
# ---------------------------------------------------------------------------

def test_inv30_manifest_registered():
    """INV-30 must be in architecture/invariants.yaml with non-empty enforced_by.tests."""
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "architecture/invariants.yaml").read_text())
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-30" in by_id, "INV-30 missing from architecture/invariants.yaml"
    enforced_tests = (by_id["INV-30"].get("enforced_by") or {}).get("tests") or []
    assert len(enforced_tests) >= 5, (
        f"INV-30 must cite at least 5 enforcing tests, found {len(enforced_tests)}"
    )
