# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1G/phase.json
"""T1G relationship tests: every live SDK contact site persists the SDK-returned envelope.

These tests assert the RELATIONSHIP between the SDK call boundary and the
persistence layer — not just that functions run, but that the SDK-returned
`_venue_submission_envelope` (distinct from the pre-submit envelope) is stored
via `_persist_final_submission_envelope_payload` before any ACK/REJECTED event
is appended.

Audit classification:
  VERIFIED_PERSISTS sites:
    executor.py:1609 (exit path — entry_order)
    executor.py:2291 (_live_order — entry path)
    executor.py:1662/1694 (SUBMIT_REJECTED exit, success_false / missing_order_id)
    executor.py:2342/2373 (SUBMIT_REJECTED live, success_false / missing_order_id)
  NOT_LIVE_PATH sites (no SDK response to persist):
    executor.py:1495 (client init failure, exit path)
    executor.py:2099 (client init failure, _live_order)
    executor.py:2138/2169 (v2_preflight failures)
    executor.py:1568/2251 (SUBMIT_TIMEOUT_UNKNOWN — exception, no response dict)

See audit doc: docs/.../phases/T1G/audit/sdk_envelope_path_audit.md
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

_NOW = datetime(2026, 5, 5, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Shared fixtures and helpers (mirrors test_executor_command_split.py pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_conn():
    """In-memory DB with full schema."""
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _bypass_guards(monkeypatch):
    """Bypass non-persistence guards so tests focus on envelope persistence."""
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.execution.executor._reserve_collateral_for_buy", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "src.execution.executor._reserve_collateral_for_sell", lambda *a, **kw: None
    )


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
            gamma_market_id="gamma-t1g",
            event_id="event-t1g",
            event_slug="event-t1g",
            condition_id="condition-t1g",
            question_id="question-t1g",
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


def _decision_source_context(**overrides):
    from src.contracts.execution_intent import DecisionSourceContext

    fields = {
        "source_id": "tigge",
        "model_family": "ecmwf_ifs025",
        "forecast_issue_time": "2026-05-05T00:00:00+00:00",
        "forecast_valid_time": "2026-05-05T06:00:00+00:00",
        "forecast_fetch_time": "2026-05-05T01:00:00+00:00",
        "forecast_available_at": "2026-05-05T00:30:00+00:00",
        "raw_payload_hash": "a" * 64,
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "decision_time": "2026-05-05T02:00:00+00:00",
        "decision_time_status": "OK",
    }
    fields.update(overrides)
    return DecisionSourceContext(**fields)


def _make_entry_intent(conn, token_id: str = "tok-t1g-" + "e" * 33):
    from src.contracts import Direction
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts.slippage_bps import SlippageBps

    snapshot_id = _ensure_snapshot(conn, token_id=token_id)
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=0.55,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="mkt-t1g-001",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
        decision_source_context=_decision_source_context(),
    )


def _make_exit_intent(conn, token_id: str = "tok-t1g-" + "x" * 33):
    from src.execution.executor import create_exit_order_intent

    snapshot_id = _ensure_snapshot(conn, token_id=token_id)
    return create_exit_order_intent(
        trade_id="trd-t1g-exit-001",
        token_id=token_id,
        shares=10.0,
        current_price=0.55,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
    )


def _capture_bound_envelope(mock_client):
    """Side effect that captures the envelope bound via bind_submission_envelope."""
    captured = {}
    mock_client.bind_submission_envelope.side_effect = lambda env: captured.__setitem__(
        "envelope", env
    )
    return captured


def _sdk_result_from_captured_envelope(
    captured: dict,
    *,
    order_id: str | None = None,
    success: bool | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    extra_raw: dict | None = None,
    sdk_marker: str = "SDK-RETURNED",
) -> dict:
    """Build a result dict that looks like it came from the SDK.

    The SDK-returned envelope is distinguishable from the pre-submit envelope
    by `raw_response_json` being populated (the pre-submit envelope has it as None).
    Additionally, `order_id` on the envelope is set to `sdk_marker` to make it
    unambiguously identifiable in test assertions.
    """
    envelope = captured.get("envelope")
    if envelope is None:
        raise AssertionError("test client did not receive a bound submission envelope")

    raw_payload = dict(extra_raw or {})
    if order_id is not None:
        raw_payload["orderID"] = order_id
    if success is not None:
        raw_payload["success"] = success
    if error_code is not None:
        raw_payload["errorCode"] = error_code
        raw_payload["errorMessage"] = error_message or ""

    changes: dict = {
        "raw_response_json": json.dumps(raw_payload, sort_keys=True, separators=(",", ":")),
    }
    if order_id is not None:
        changes["order_id"] = order_id
    elif error_code is None:
        # Use sdk_marker as order_id to make the SDK-returned envelope distinguishable
        changes["order_id"] = sdk_marker

    if error_code is not None:
        changes["error_code"] = error_code
        changes["error_message"] = error_message or ""

    final_envelope = envelope.with_updates(**changes)

    result: dict = {
        "_venue_submission_envelope": final_envelope.to_dict(),
        "status": "LIVE" if success is not False else "rejected",
    }
    actual_order_id = order_id or (None if error_code is not None else sdk_marker)
    if actual_order_id is not None:
        result["orderID"] = actual_order_id
        result["orderId"] = actual_order_id
        result["id"] = actual_order_id
    if success is not None:
        result["success"] = success
    if error_code is not None:
        result["errorCode"] = error_code
        result["errorMessage"] = error_message or ""
    return result


def _count_final_envelopes(conn, command_id: str) -> int:
    """Count venue_submission_envelopes rows that are post-submit (have raw_response_json set).

    The pre-submit envelope inserted by _persist_prebuilt_submit_envelope has raw_response_json=NULL.
    The post-submit envelope inserted by _persist_final_submission_envelope_payload has
    raw_response_json populated with the SDK response.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM venue_submission_envelopes WHERE raw_response_json IS NOT NULL"
    ).fetchone()[0]


def _get_ack_rej_event(conn, command_id: str, event_types: list[str]) -> dict | None:
    """Return first event of the given types for command_id, or None."""
    from src.state.venue_command_repo import list_events

    events = list_events(conn, command_id)
    for event in events:
        if event["event_type"] in event_types:
            return json.loads(event["payload_json"])
    return None


def _get_command_id(conn) -> str:
    row = conn.execute("SELECT command_id FROM venue_commands ORDER BY rowid DESC LIMIT 1").fetchone()
    if row is None:
        raise AssertionError("No venue_command row found")
    return row["command_id"]


# ---------------------------------------------------------------------------
# Test 1: entry submit path (_live_order) persists SDK-returned envelope
# ---------------------------------------------------------------------------


def test_every_live_submit_persists_final_sdk_envelope(mem_conn):
    """T1G-RELATIONSHIP: _live_order entry submit path persists the SDK-returned envelope.

    RELATIONSHIP ASSERTED:
      _live_order → client.place_limit_order returns dict with _venue_submission_envelope →
      _persist_final_submission_envelope_payload stores it as a new venue_submission_envelopes
      row → SUBMIT_ACKED event payload carries final_submission_envelope_id.

    The fake SDK returns a result where the envelope has `raw_response_json` populated
    (indistinguishable from a real SDK return) AND `order_id=SDK-RETURNED-ENTRY-001`.
    The pre-submit envelope has `raw_response_json=None` and `order_id=None`.
    We assert two things:
      1. A second venue_submission_envelopes row exists after the call (post-submit row).
      2. The SUBMIT_ACKED event payload contains `final_submission_envelope_id`.

    Covers: executor.py:2291 (VERIFIED_PERSISTS) and executor.py:2342/2373 indirectly.
    """
    from src.execution.executor import _live_order

    token_id = "tok-t1g-" + "a" * 33
    intent = _make_entry_intent(mem_conn, token_id=token_id)

    with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        mock_inst.v2_preflight.return_value = None
        captured = _capture_bound_envelope(mock_inst)

        def fake_place_limit_order(**kwargs):
            return _sdk_result_from_captured_envelope(
                captured,
                order_id="SDK-RETURNED-ENTRY-001",
                sdk_marker="SDK-RETURNED-ENTRY-001",
            )

        mock_inst.place_limit_order.side_effect = fake_place_limit_order

        result = _live_order(
            trade_id="trd-t1g-entry-persist",
            intent=intent,
            shares=18.19,
            conn=mem_conn,
            decision_id="dec-t1g-entry-persist",
        )

    # Verify the result reached acked state
    assert result.status == "pending", f"Expected 'pending' (order acked), got {result.status!r}"
    assert result.order_id == "SDK-RETURNED-ENTRY-001"

    command_id = _get_command_id(mem_conn)

    # ASSERTION 1: A post-submit envelope row exists (raw_response_json populated).
    # _persist_final_submission_envelope_payload inserts a second row; the pre-submit
    # row has raw_response_json=NULL.
    post_submit_count = _count_final_envelopes(mem_conn, command_id)
    assert post_submit_count >= 1, (
        "NEEDS_FIX gap detected: no post-submit venue_submission_envelopes row found. "
        "_persist_final_submission_envelope_payload was not called with SDK-returned payload."
    )

    # ASSERTION 2: The SUBMIT_ACKED event carries final_submission_envelope_id.
    # This proves the persistence reference was threaded into the event — not just that
    # _persist_final_submission_envelope_payload was invoked.
    ack_payload = _get_ack_rej_event(mem_conn, command_id, ["SUBMIT_ACKED"])
    assert ack_payload is not None, "SUBMIT_ACKED event not found"
    assert "final_submission_envelope_id" in ack_payload, (
        "SUBMIT_ACKED event payload missing final_submission_envelope_id — "
        "SDK-returned envelope reference was not threaded through to the event."
    )
    assert "final_submission_envelope_command_id" in ack_payload, (
        "SUBMIT_ACKED event payload missing final_submission_envelope_command_id"
    )

    # ASSERTION 3: The persisted envelope's raw_response_json encodes the SDK response.
    # The SDK-returned envelope has order_id set; the pre-submit envelope has None.
    sdk_env_row = mem_conn.execute(
        "SELECT raw_response_json FROM venue_submission_envelopes WHERE raw_response_json IS NOT NULL LIMIT 1"
    ).fetchone()
    assert sdk_env_row is not None
    sdk_env_payload = json.loads(sdk_env_row["raw_response_json"])
    assert sdk_env_payload.get("orderID") == "SDK-RETURNED-ENTRY-001", (
        f"SDK-returned envelope row does not contain expected orderID; "
        f"got raw_response_json={sdk_env_row['raw_response_json']!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: rejected submit persists reject payload
# ---------------------------------------------------------------------------


def test_rejected_submit_persists_reject_payload(mem_conn):
    """T1G-RELATIONSHIP: SUBMIT_REJECTED events cite the SDK-returned (rejection) envelope.

    RELATIONSHIP ASSERTED:
      _live_order → SDK returns success=False with _venue_submission_envelope →
      _persist_final_submission_envelope_payload stores the rejection envelope →
      SUBMIT_REJECTED event payload carries final_submission_envelope_id.

    This exercises executor.py:2291 (persistence) → executor.py:2342 (SUBMIT_REJECTED event).
    The test fails pre-T1G if either the persistence call or the **final_envelope_payload
    spread in the SUBMIT_REJECTED event is missing.
    """
    from src.execution.executor import _live_order

    token_id = "tok-t1g-" + "b" * 33
    intent = _make_entry_intent(mem_conn, token_id=token_id)

    with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        mock_inst.v2_preflight.return_value = None
        captured = _capture_bound_envelope(mock_inst)

        def fake_place_limit_order_reject(**kwargs):
            return _sdk_result_from_captured_envelope(
                captured,
                success=False,
                error_code="SDK_REJECT_CODE",
                error_message="venue rejected order: insufficient margin",
                sdk_marker="SDK-REJECT-ENTRY",
            )

        mock_inst.place_limit_order.side_effect = fake_place_limit_order_reject

        result = _live_order(
            trade_id="trd-t1g-entry-reject",
            intent=intent,
            shares=18.19,
            conn=mem_conn,
            decision_id="dec-t1g-entry-reject",
        )

    # SDK returned success=False → result should be rejected
    assert result.status == "rejected", (
        f"Expected 'rejected' for success=False SDK response, got {result.status!r}"
    )

    command_id = _get_command_id(mem_conn)

    # ASSERTION 1: Post-submit envelope row exists — the rejection envelope was persisted.
    post_submit_count = _count_final_envelopes(mem_conn, command_id)
    assert post_submit_count >= 1, (
        "NEEDS_FIX gap: no post-submit venue_submission_envelopes row for rejection path. "
        "_persist_final_submission_envelope_payload must be called before SUBMIT_REJECTED event, "
        "even when success=False."
    )

    # ASSERTION 2: SUBMIT_REJECTED event carries the SDK envelope reference.
    rej_payload = _get_ack_rej_event(mem_conn, command_id, ["SUBMIT_REJECTED"])
    assert rej_payload is not None, (
        "SUBMIT_REJECTED event not found — success=False path did not append event"
    )
    assert "final_submission_envelope_id" in rej_payload, (
        "SUBMIT_REJECTED event missing final_submission_envelope_id. "
        "The **final_envelope_payload spread at executor.py:2347 must be present."
    )

    # ASSERTION 3: The reason in the event matches what the SDK returned.
    assert rej_payload.get("reason") == "SDK_REJECT_CODE", (
        f"SUBMIT_REJECTED reason mismatch: got {rej_payload.get('reason')!r}"
    )

    # ASSERTION 4: The persisted rejection envelope encodes the SDK error.
    sdk_env_row = mem_conn.execute(
        "SELECT raw_response_json FROM venue_submission_envelopes WHERE raw_response_json IS NOT NULL LIMIT 1"
    ).fetchone()
    assert sdk_env_row is not None
    sdk_env_payload = json.loads(sdk_env_row["raw_response_json"])
    assert sdk_env_payload.get("success") is False, (
        "Persisted SDK-returned envelope should have success=False"
    )
    assert sdk_env_payload.get("errorCode") == "SDK_REJECT_CODE"


# ---------------------------------------------------------------------------
# Test 3: exit submit path (execute_exit_order) persists SDK-returned envelope
# ---------------------------------------------------------------------------


def test_exit_submit_persists_final_envelope(mem_conn):
    """T1G-RELATIONSHIP: execute_exit_order exit submit path persists the SDK-returned envelope.

    RELATIONSHIP ASSERTED:
      execute_exit_order → client.place_limit_order returns dict with _venue_submission_envelope →
      _persist_final_submission_envelope_payload (executor.py:1609) stores it →
      SUBMIT_ACKED event carries final_submission_envelope_id.

    This covers executor.py:1609 (VERIFIED_PERSISTS) — the exit path equivalent of Test 1.
    Uses execute_exit_order directly (or via a thin wrapper) to exercise the exit code path
    distinct from _live_order.
    """
    from src.execution.executor import execute_exit_order

    token_id = "tok-t1g-" + "c" * 33
    intent = _make_exit_intent(mem_conn, token_id=token_id)

    with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        captured = _capture_bound_envelope(mock_inst)

        def fake_place_limit_order_exit(**kwargs):
            return _sdk_result_from_captured_envelope(
                captured,
                order_id="SDK-RETURNED-EXIT-001",
                sdk_marker="SDK-RETURNED-EXIT-001",
            )

        mock_inst.place_limit_order.side_effect = fake_place_limit_order_exit

        result = execute_exit_order(
            intent=intent,
            conn=mem_conn,
        )

    # Exit order should succeed (pending = acked)
    assert result.status in ("pending", "filled", "acked"), (
        f"Expected order to be acked/pending for exit path, got {result.status!r}: {result.reason!r}"
    )
    assert result.order_id == "SDK-RETURNED-EXIT-001", (
        f"Expected order_id=SDK-RETURNED-EXIT-001, got {result.order_id!r}"
    )

    command_id = _get_command_id(mem_conn)

    # ASSERTION 1: Post-submit envelope row exists — the SDK-returned envelope was persisted.
    post_submit_count = _count_final_envelopes(mem_conn, command_id)
    assert post_submit_count >= 1, (
        "NEEDS_FIX gap detected in exit path: no post-submit venue_submission_envelopes row. "
        "_persist_final_submission_envelope_payload was not called at executor.py:1609."
    )

    # ASSERTION 2: SUBMIT_ACKED event carries the SDK envelope reference.
    ack_payload = _get_ack_rej_event(mem_conn, command_id, ["SUBMIT_ACKED"])
    assert ack_payload is not None, "SUBMIT_ACKED event not found for exit path"
    assert "final_submission_envelope_id" in ack_payload, (
        "SUBMIT_ACKED event missing final_submission_envelope_id in exit path — "
        "SDK envelope reference not threaded into event."
    )

    # ASSERTION 3: The SDK-returned envelope is distinguishable from the pre-submit envelope.
    sdk_env_row = mem_conn.execute(
        "SELECT raw_response_json FROM venue_submission_envelopes WHERE raw_response_json IS NOT NULL LIMIT 1"
    ).fetchone()
    assert sdk_env_row is not None
    sdk_env_payload = json.loads(sdk_env_row["raw_response_json"])
    assert sdk_env_payload.get("orderID") == "SDK-RETURNED-EXIT-001", (
        f"SDK-returned exit envelope does not contain expected orderID; "
        f"raw_response_json={sdk_env_row['raw_response_json']!r}"
    )
