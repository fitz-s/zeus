# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: R3 M2 unknown-side-effect semantics for post-POST submit uncertainty.
# Reuse: Run when executor submit exception handling, venue command recovery,
#        or idempotency/economic-intent duplicate blocking changes.
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M2.yaml
"""M2: post-side-effect submit uncertainty must not become semantic rejection."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

NOW = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(monkeypatch):
    """In-memory trades DB with live-money gates neutralized for unit tests."""
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)
    yield c
    c.close()


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
            gamma_market_id="gamma-m2",
            event_id="event-m2",
            event_slug="weather-m2",
            condition_id="condition-m2",
            question_id="question-m2",
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
            captured_at=NOW,
            freshness_deadline=NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


def _decision_source_context():
    from src.contracts.execution_intent import DecisionSourceContext

    return DecisionSourceContext(
        source_id="tigge",
        model_family="ecmwf_ifs025",
        forecast_issue_time="2026-04-27T09:00:00+00:00",
        forecast_valid_time="2026-04-27T18:00:00+00:00",
        forecast_fetch_time="2026-04-27T10:00:00+00:00",
        forecast_available_at="2026-04-27T09:30:00+00:00",
        raw_payload_hash="f" * 64,
        degradation_level="OK",
        forecast_source_role="entry_primary",
        authority_tier="FORECAST",
        decision_time=NOW.isoformat(),
        decision_time_status="OK",
    )


def _make_entry_intent(conn, *, token_id: str = "tok-m2", price: float = 0.55):
    from src.contracts import Direction
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts.slippage_bps import SlippageBps

    snapshot_id = _ensure_snapshot(conn, token_id=token_id)
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="condition-m2",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
        decision_source_context=_decision_source_context(),
    )


def _insert_unknown_side_effect(
    conn,
    *,
    command_id: str = "cmd-m2",
    token_id: str = "tok-m2",
    idem: str = "1" * 32,
    created_at: datetime | None = None,
    price: float = 0.55,
    size: float = 18.19,
    final_event: str = "SUBMIT_TIMEOUT_UNKNOWN",
) -> None:
    from src.state.venue_command_repo import append_event, insert_command, insert_submission_envelope
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    created = created_at or NOW
    snapshot_id = _ensure_snapshot(conn, token_id=token_id)
    envelope_id = f"env-{command_id}"
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-m2",
            question_id="question-m2",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            side="BUY",
            price=Decimal(str(price)),
            size=Decimal(str(size)),
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
            captured_at=created.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    insert_command(
        conn,
        command_id=command_id,
        snapshot_id=snapshot_id,
        envelope_id=envelope_id,
        position_id="trade-m2",
        decision_id="decision-m2",
        idempotency_key=idem,
        intent_kind="ENTRY",
        market_id="condition-m2",
        token_id=token_id,
        side="BUY",
        size=size,
        price=price,
        created_at=created.isoformat(),
        snapshot_checked_at=created.isoformat(),
    )
    append_event(conn, command_id=command_id, event_type="SUBMIT_REQUESTED", occurred_at=created.isoformat())
    append_event(conn, command_id=command_id, event_type=final_event, occurred_at=created.isoformat())
    conn.commit()


def _command(conn):
    return conn.execute("SELECT * FROM venue_commands ORDER BY created_at DESC LIMIT 1").fetchone()


def _events(conn, command_id: str) -> list[str]:
    return [
        row["event_type"]
        for row in conn.execute(
            "SELECT event_type FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
            (command_id,),
        )
    ]


def _capture_bound_submission_envelope(mock_client):
    bound = {}
    mock_client.bind_submission_envelope.side_effect = lambda envelope: bound.__setitem__("envelope", envelope)
    return bound


def _final_submit_result(
    bound: dict,
    *,
    success: bool,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    envelope = bound.get("envelope")
    if envelope is None:
        raise AssertionError("test client did not receive a bound submission envelope")
    raw_payload = {"success": success, "status": status}
    changes = {
        "raw_response_json": json.dumps(raw_payload, sort_keys=True, separators=(",", ":")),
    }
    if error_code is not None:
        changes["error_code"] = error_code
        changes["error_message"] = error_message or ""
    final = envelope.with_updates(**changes)
    result = {
        "success": success,
        "status": status,
        "_venue_submission_envelope": final.to_dict(),
    }
    if error_code is not None:
        result["errorCode"] = error_code
        result["errorMessage"] = error_message or ""
    return result


def test_network_timeout_after_POST_creates_unknown_not_rejected(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.return_value = None
    mock_client.place_limit_order.side_effect = TimeoutError("post timed out")

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-timeout", intent, shares=18.19, conn=conn, decision_id="dec-m2-timeout")

    cmd = _command(conn)
    assert result.status == "unknown_side_effect"
    assert result.command_state == "SUBMIT_UNKNOWN_SIDE_EFFECT"
    assert "submit_unknown_side_effect" in (result.reason or "")
    assert cmd["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT"
    assert "SUBMIT_TIMEOUT_UNKNOWN" in _events(conn, cmd["command_id"])
    assert "SUBMIT_REJECTED" not in _events(conn, cmd["command_id"])


def test_typed_venue_rejection_creates_SUBMIT_REJECTED(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.return_value = None
    bound = _capture_bound_submission_envelope(mock_client)
    mock_client.place_limit_order.side_effect = lambda **kwargs: _final_submit_result(
        bound,
        success=False,
        status="rejected",
        error_code="INVALID_ORDER",
        error_message="bad tick",
    )

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-reject", intent, shares=18.19, conn=conn, decision_id="dec-m2-reject")

    cmd = _command(conn)
    assert result.status == "rejected"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])


def test_pre_post_signing_exception_safe_to_retry(conn):
    from src.data.polymarket_client import V2PreflightError
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.side_effect = V2PreflightError("pre-post gate failed")

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-prepost", intent, shares=18.19, conn=conn, decision_id="dec-m2-prepost")

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "v2_preflight_failed" in (result.reason or "")
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])
    mock_client.place_limit_order.assert_not_called()


def test_generic_pre_post_preflight_exception_safe_to_retry(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.side_effect = RuntimeError("credential setup failed")

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-generic-prepost", intent, shares=18.19, conn=conn, decision_id="dec-m2-generic-prepost")

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "v2_preflight_exception" in (result.reason or "")
    assert result.command_state == "REJECTED"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])
    mock_client.place_limit_order.assert_not_called()


def test_exit_client_construction_exception_safe_to_retry(conn):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    token_id = "tok-m2-exit-init"
    _ensure_snapshot(conn, token_id=token_id)

    with patch(
        "src.data.polymarket_client.PolymarketClient",
        side_effect=RuntimeError("missing credentials"),
    ):
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-m2-exit-init",
                token_id=token_id,
                shares=18.19,
                current_price=0.55,
                executable_snapshot_id=f"snap-{token_id}",
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="dec-m2-exit-init",
        )

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "pre_submit_client_init_failed" in (result.reason or "")
    assert result.command_state == "REJECTED"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])


def test_exit_lazy_adapter_preflight_exception_safe_to_retry(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    token_id = "tok-m2-exit-lazy"
    _ensure_snapshot(conn, token_id=token_id)

    def _raise_lazy_adapter_failure(self):
        raise RuntimeError("lazy adapter credential failure")

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient._ensure_v2_adapter",
        _raise_lazy_adapter_failure,
    )

    result = execute_exit_order(
        create_exit_order_intent(
            trade_id="trade-m2-exit-lazy",
            token_id=token_id,
            shares=18.19,
            current_price=0.55,
            executable_snapshot_id=f"snap-{token_id}",
            executable_snapshot_min_tick_size=Decimal("0.01"),
            executable_snapshot_min_order_size=Decimal("0.01"),
            executable_snapshot_neg_risk=False,
        ),
        conn=conn,
        decision_id="dec-m2-exit-lazy",
    )

    cmd = _command(conn)
    assert result.status == "rejected"
    assert result.reason == "V2_PREFLIGHT_EXCEPTION"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])
    rejected_event = conn.execute(
        """
        SELECT payload_json
        FROM venue_command_events
        WHERE command_id = ? AND event_type = 'SUBMIT_REJECTED'
        """,
        (cmd["command_id"],),
    ).fetchone()
    rejected_payload = json.loads(rejected_event["payload_json"])
    final_envelope_id = rejected_payload["final_submission_envelope_id"]
    final_envelope = conn.execute(
        "SELECT error_code FROM venue_submission_envelopes WHERE envelope_id = ?",
        (final_envelope_id,),
    ).fetchone()
    assert final_envelope["error_code"] == "V2_PREFLIGHT_EXCEPTION"


def test_exit_adapter_submit_pre_snapshot_failure_safe_to_retry(conn, tmp_path):
    from src.data.polymarket_client import PolymarketClient
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class FakePreflightOnlyClient:
        def __init__(self):
            self.calls = []

        def get_ok(self):
            self.calls.append(("get_ok",))
            return {"ok": True}

    token_id = "tok-m2-exit-submit-pre"
    _ensure_snapshot(conn, token_id=token_id)
    q1_evidence = tmp_path / "q1_egress.txt"
    q1_evidence.write_text(
        "Q1 Zeus egress evidence sentinel\n"
        "authority_basis: test\n"
        "operator_attestation: test current egress accepted\n"
        "live_side_effects: none; HTTPS GET probes only\n"
        "raw_secrets_or_signed_payloads: none\n"
        "probe_results:\n"
        "[{\"effective_url\":\"https://clob.polymarket.com/ok\",\"status_code\":200}]\n",
        encoding="utf-8",
    )
    fake_sdk = FakePreflightOnlyClient()
    adapter = PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=q1_evidence,
        client_factory=lambda **_kwargs: fake_sdk,
    )
    client = PolymarketClient()
    client._v2_adapter = adapter

    with patch("src.data.polymarket_client.PolymarketClient", return_value=client):
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-m2-exit-submit-pre",
                token_id=token_id,
                shares=18.19,
                current_price=0.55,
                executable_snapshot_id=f"snap-{token_id}",
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="dec-m2-exit-submit-pre",
        )

    cmd = _command(conn)
    assert result.status == "rejected"
    assert result.reason == "V2_SUBMIT_UNSUPPORTED"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])
    assert fake_sdk.calls == [("get_ok",)]


def test_duplicate_retry_blocked_during_unknown(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    first_client = MagicMock()
    first_client.v2_preflight.return_value = None
    first_client.place_limit_order.side_effect = TimeoutError("post timed out")
    with patch("src.data.polymarket_client.PolymarketClient", return_value=first_client):
        first = _live_order("trade-m2-dupe", intent, shares=18.19, conn=conn, decision_id="dec-m2-dupe")
    assert first.status == "unknown_side_effect"

    second_client = MagicMock()
    second_client.v2_preflight.return_value = None
    with patch("src.data.polymarket_client.PolymarketClient", return_value=second_client):
        second = _live_order("trade-m2-dupe", intent, shares=18.19, conn=conn, decision_id="dec-m2-dupe")

    assert second.status == "unknown_side_effect"
    assert "idempotency_collision" in (second.reason or "")
    second_client.place_limit_order.assert_not_called()


def test_strategy_cannot_submit_replacement_with_different_idempotency_key_for_same_economic_intent(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    first_client = MagicMock()
    first_client.v2_preflight.return_value = None
    first_client.place_limit_order.side_effect = TimeoutError("post timed out")
    with patch("src.data.polymarket_client.PolymarketClient", return_value=first_client):
        first = _live_order("trade-m2-economic", intent, shares=18.19, conn=conn, decision_id="dec-m2-a")
    assert first.status == "unknown_side_effect"

    second_client = MagicMock()
    second_client.v2_preflight.return_value = None
    with patch("src.data.polymarket_client.PolymarketClient", return_value=second_client):
        second = _live_order("trade-m2-economic-replacement", intent, shares=18.19, conn=conn, decision_id="dec-m2-b")

    assert second.status == "unknown_side_effect"
    assert "economic_intent_duplication" in (second.reason or "")
    second_client.place_limit_order.assert_not_called()


def test_economic_intent_duplicate_uses_idempotency_precision(conn):
    """0.3 and 0.1 + 0.2 must compare as the same order economics."""
    from src.execution.executor import _live_order

    token_id = "tok-m2-float"
    first_intent = _make_entry_intent(conn, token_id=token_id, price=0.3)
    second_intent = _make_entry_intent(conn, token_id=token_id, price=0.1 + 0.2)

    first_client = MagicMock()
    first_client.v2_preflight.return_value = None
    first_client.place_limit_order.side_effect = TimeoutError("post timed out")
    with patch("src.data.polymarket_client.PolymarketClient", return_value=first_client):
        first = _live_order("trade-m2-float-a", first_intent, shares=18.19, conn=conn, decision_id="dec-m2-float-a")
    assert first.status == "unknown_side_effect"

    second_client = MagicMock()
    second_client.v2_preflight.return_value = None
    with patch("src.data.polymarket_client.PolymarketClient", return_value=second_client):
        second = _live_order("trade-m2-float-b", second_intent, shares=18.19, conn=conn, decision_id="dec-m2-float-b")

    assert second.status == "unknown_side_effect"
    assert "economic_intent_duplication" in (second.reason or "")
    second_client.place_limit_order.assert_not_called()


def test_reconciliation_finding_order_converts_unknown_to_acked_or_partial_until_confirmed(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands

    _insert_unknown_side_effect(conn, idem="2" * 32)
    client = MagicMock()
    client.find_order_by_idempotency_key.return_value = {
        "orderID": "ord-m2-acked",
        "status": "LIVE",
    }

    summary = reconcile_unresolved_commands(conn, client)

    cmd = _command(conn)
    assert summary["advanced"] == 1
    assert cmd["state"] == "ACKED"
    assert cmd["venue_order_id"] == "ord-m2-acked"
    assert "SUBMIT_ACKED" in _events(conn, cmd["command_id"])

    _insert_unknown_side_effect(conn, command_id="cmd-m2-filled", idem="3" * 32, token_id="tok-m2-filled")
    client.find_order_by_idempotency_key.return_value = {
        "orderID": "ord-m2-filled",
        "status": "FILLED",
    }
    summary = reconcile_unresolved_commands(conn, client)
    filled = conn.execute("SELECT * FROM venue_commands WHERE command_id = ?", ("cmd-m2-filled",)).fetchone()
    assert summary["advanced"] >= 1
    assert filled["state"] == "PARTIAL"
    assert filled["venue_order_id"] == "ord-m2-filled"
    assert "PARTIAL_FILL_OBSERVED" in _events(conn, "cmd-m2-filled")
    assert "FILL_CONFIRMED" not in _events(conn, "cmd-m2-filled")

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-confirmed",
        idem="9" * 32,
        token_id="tok-m2-confirmed",
    )
    client.find_order_by_idempotency_key.return_value = {
        "orderID": "ord-m2-confirmed",
        "status": "CONFIRMED",
    }
    summary = reconcile_unresolved_commands(conn, client)
    confirmed = conn.execute("SELECT * FROM venue_commands WHERE command_id = ?", ("cmd-m2-confirmed",)).fetchone()
    assert summary["advanced"] >= 1
    assert confirmed["state"] == "REVIEW_REQUIRED"
    assert confirmed["venue_order_id"] == "ord-m2-confirmed"
    assert "REVIEW_REQUIRED" in _events(conn, "cmd-m2-confirmed")
    assert "FILL_CONFIRMED" not in _events(conn, "cmd-m2-confirmed")


def test_review_required_side_effect_still_blocks_same_economic_intent(conn):
    from src.execution.executor import _live_order
    from src.state.venue_command_repo import append_event, find_unknown_command_by_economic_intent

    token_id = "tok-m2-review-block"
    _insert_unknown_side_effect(conn, command_id="cmd-m2-review-block", idem="5" * 32, token_id=token_id)
    append_event(
        conn,
        command_id="cmd-m2-review-block",
        event_type="REVIEW_REQUIRED",
        occurred_at=NOW.isoformat(),
        payload={"reason": "recovery_confirmed_requires_trade_fact"},
    )
    conn.commit()

    unresolved = find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id=token_id,
        side="BUY",
        price=0.55,
        size=18.19,
    )
    assert unresolved is not None
    assert unresolved["state"] == "REVIEW_REQUIRED"

    intent = _make_entry_intent(conn, token_id=token_id)
    second_client = MagicMock()
    second_client.v2_preflight.return_value = None
    with patch("src.data.polymarket_client.PolymarketClient", return_value=second_client):
        second = _live_order("trade-m2-review-replacement", intent, shares=18.19, conn=conn, decision_id="dec-m2-review-b")

    assert second.status == "unknown_side_effect"
    assert "economic_intent_duplication" in (second.reason or "")
    assert second.command_state == "REVIEW_REQUIRED"
    second_client.place_limit_order.assert_not_called()


def test_unknown_state_side_effect_still_blocks_same_economic_intent(conn):
    from src.state.venue_command_repo import find_unknown_command_by_economic_intent

    token_id = "tok-m2-unknown-block"
    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-unknown-block",
        token_id=token_id,
        idem="6" * 32,
        final_event="SUBMIT_UNKNOWN",
    )

    unresolved = find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id=token_id,
        side="BUY",
        price=0.55,
        size=18.19,
    )
    assert unresolved is not None
    assert unresolved["state"] == "UNKNOWN"


def test_reconciliation_finding_no_order_within_window_permits_safe_replay(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands
    from src.state.venue_command_repo import find_unknown_command_by_economic_intent

    old = NOW - timedelta(minutes=30)
    _insert_unknown_side_effect(conn, idem="4" * 32, created_at=old)
    client = MagicMock()
    client.find_order_by_idempotency_key.return_value = None

    summary = reconcile_unresolved_commands(conn, client)

    cmd = _command(conn)
    assert summary["advanced"] == 1
    assert cmd["state"] == "SUBMIT_REJECTED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        (cmd["command_id"],),
    ).fetchall()
    assert [row["event_type"] for row in events][-1] == "SUBMIT_REJECTED"
    payload = json.loads(events[-1]["payload_json"])
    assert payload["reason"] == "safe_replay_permitted_no_order_found"
    assert payload["safe_replay_permitted"] is True
    assert payload["previous_unknown_command_id"] == cmd["command_id"]
    assert payload["idempotency_key"] == "4" * 32
    assert find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id="tok-m2",
        side="BUY",
        price=0.55,
        size=18.19,
    ) is None
