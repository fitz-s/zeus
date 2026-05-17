# Lifecycle: created=2026-05-16; last_reviewed=2026-05-16; last_reused=never
# Purpose: Regression coverage for F14 cascade-liveness fix — verifies
#   submit_redeem stub-detect branch transitions to REDEEM_OPERATOR_REQUIRED
#   (not REDEEM_REVIEW_REQUIRED catch-all), atomicity contract that
#   logger.warning fires only on successful transition, and the
#   _atomic_transition WHERE-state-guard primitive used by the operator CLI.
# Reuse: Run on every PR touching src/execution/settlement_commands.py state
#   machine or the submit_redeem function body. Authority basis:
#   docs/operations/task_2026-05-16_deep_alignment_audit/SCAFFOLD_F14_F16.md §K.5 + §K.3 v5.
#
# F14 redeem cascade liveness — verifies submit_redeem's stub-detect branch
# routes to REDEEM_OPERATOR_REQUIRED (NOT REDEEM_REVIEW_REQUIRED) when the
# adapter returns REDEEM_DEFERRED_TO_R1, and verifies the atomicity contract
# that logger.warning fires only when transition succeeds.

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.contracts.fx_classification import FXClassification
from src.state.db import init_schema

NOW = datetime(2026, 5, 16, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    yield db
    db.close()


def _allow_redemption(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: SimpleNamespace(allow_redemption=True, block_reason=None, state="LIVE_ENABLED"),
    )


def _seed_intent_row(conn, monkeypatch, condition_id: str = "c30f28a5-d4e-test") -> str:
    """Seed one REDEEM_INTENT_CREATED row, return command_id (str).

    Uses pUSD payout (NOT USDC_E) because USDC_E payouts auto-classify into
    REDEEM_REVIEW_REQUIRED at request time per src/execution/settlement_commands.py
    `request_redeem` docstring — not what we want for testing the
    INTENT→SUBMITTED→OPERATOR_REQUIRED cascade.
    """
    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    return request_redeem(
        condition_id,
        "pUSD",
        market_id="market-Karachi-2026-05-17",
        pusd_amount_micro=590_000,
        conn=conn,
        requested_at=NOW,
    )


class _StubDeferredAdapter:
    """Mimics current PolymarketV2Adapter.redeem stub returning REDEEM_DEFERRED_TO_R1."""
    def __init__(self):
        self.calls = []

    def redeem(self, condition_id: str):
        self.calls.append(condition_id)
        return {
            "success": False,
            "errorCode": "REDEEM_DEFERRED_TO_R1",
            "errorMessage": "R1 settlement command ledger must own pUSD redemption side effects",
            "condition_id": condition_id,
        }


class _UnexpectedErrorAdapter:
    def __init__(self, error_code: str = "NETWORK_TIMEOUT"):
        self.error_code = error_code
        self.calls = []

    def redeem(self, condition_id: str):
        self.calls.append(condition_id)
        return {
            "success": False,
            "errorCode": self.error_code,
            "errorMessage": f"simulated {self.error_code}",
        }


def test_submit_redeem_transitions_to_operator_required_on_stub(conn, monkeypatch, caplog):
    """SCAFFOLD §K.3 v5: stub-deferred adapter → state lands in OPERATOR_REQUIRED
    (NOT generic REVIEW_REQUIRED). Logger.warning fires with [REDEEM_OPERATOR_REQUIRED] prefix.
    """
    from src.execution.settlement_commands import (
        SettlementState,
        submit_redeem,
    )

    _allow_redemption(monkeypatch)
    command_id = _seed_intent_row(conn, monkeypatch)
    adapter = _StubDeferredAdapter()

    with caplog.at_level(logging.WARNING, logger="src.execution.settlement_commands"):
        result = submit_redeem(
            command_id,
            adapter,
            object(),  # ledger stub (R1 keeps the seam; not asserted here)
            conn=conn,
            submitted_at=NOW,
        )

    assert result.state == SettlementState.REDEEM_OPERATOR_REQUIRED, (
        f"stub-deferred should land in OPERATOR_REQUIRED, got {result.state}"
    )
    row = conn.execute(
        "SELECT state, terminal_at FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_OPERATOR_REQUIRED"
    assert row["terminal_at"] is None, "OPERATOR_REQUIRED is NOT terminal (operator CLI exits it)"

    # Atomicity contract: logger.warning emitted with the expected prefix
    matching = [r for r in caplog.records if "[REDEEM_OPERATOR_REQUIRED]" in r.getMessage()]
    assert len(matching) == 1, (
        f"expected exactly one [REDEEM_OPERATOR_REQUIRED] warning, "
        f"got {len(matching)}: {[r.getMessage() for r in caplog.records]}"
    )
    msg = matching[0].getMessage()
    assert command_id in msg
    assert "operator_record_redeem" in msg


def test_submit_redeem_transitions_to_failed_on_unexpected_error(conn, monkeypatch, caplog):
    """SCAFFOLD §K.3 v5: non-stub adapter errors still route to REDEEM_FAILED
    (not OPERATOR_REQUIRED — semantic distinction load-bearing).
    """
    from src.execution.settlement_commands import (
        SettlementState,
        submit_redeem,
    )

    _allow_redemption(monkeypatch)
    command_id = _seed_intent_row(conn, monkeypatch)
    adapter = _UnexpectedErrorAdapter(error_code="NETWORK_TIMEOUT")

    with caplog.at_level(logging.WARNING, logger="src.execution.settlement_commands"):
        result = submit_redeem(
            command_id,
            adapter,
            object(),
            conn=conn,
            submitted_at=NOW,
        )

    assert result.state == SettlementState.REDEEM_FAILED
    row = conn.execute(
        "SELECT state, terminal_at FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_FAILED"
    assert row["terminal_at"] is not None, "REDEEM_FAILED is terminal; terminal_at must be set"

    # Atomicity: NO [REDEEM_OPERATOR_REQUIRED] alert for non-stub errors
    assert not any("[REDEEM_OPERATOR_REQUIRED]" in r.getMessage() for r in caplog.records), (
        "unexpected adapter error must NOT trigger operator-required alert"
    )


def test_atomic_transition_returns_false_when_state_guard_fails(conn, monkeypatch):
    """SCAFFOLD §K.3 v5 atomicity contract: _atomic_transition with mismatched
    from_state must return False, leave row unchanged, NOT append event.
    """
    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import (
        SettlementState,
        _atomic_transition,
    )
    from src.execution.settlement_commands import request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    command_id = request_redeem(
        "c-test",
        "pUSD",
        market_id="m-test",
        pusd_amount_micro=1,
        conn=conn,
        requested_at=NOW,
    )
    # row is in REDEEM_INTENT_CREATED; try to transition from OPERATOR_REQUIRED → TX_HASHED
    transitioned = _atomic_transition(
        conn,
        command_id,
        from_state=SettlementState.REDEEM_OPERATOR_REQUIRED,
        to_state=SettlementState.REDEEM_TX_HASHED,
        tx_hash="0x" + "a" * 64,
        payload={"actor": "operator"},
        recorded_at=NOW.isoformat(),
    )

    assert transitioned is False, (
        "rowcount-0 transition must return False (state guard rejected mismatch)"
    )
    row = conn.execute(
        "SELECT state, tx_hash FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_INTENT_CREATED", "row state must be unchanged"
    assert row["tx_hash"] is None, "tx_hash must not be set on failed transition"

    events = conn.execute(
        "SELECT event_type FROM settlement_command_events WHERE command_id = ?",
        (command_id,),
    ).fetchall()
    # Only the initial REDEEM_INTENT_CREATED event from request_redeem
    assert all(e["event_type"] != "REDEEM_TX_HASHED" for e in events), (
        "no TX_HASHED event should be appended on failed transition"
    )


def test_atomic_transition_succeeds_when_state_guard_matches(conn, monkeypatch):
    """Happy path: _atomic_transition with matching from_state → True, row updated, event appended."""
    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import (
        SettlementState,
        _atomic_transition,
        _transition,
        _savepoint,
        _coerce_time,
        request_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    command_id = request_redeem(
        "c-test2",
        "pUSD",
        market_id="m-test2",
        pusd_amount_micro=1,
        conn=conn,
        requested_at=NOW,
    )
    # Force into OPERATOR_REQUIRED via existing _transition (simulating
    # the stub-detect branch in submit_redeem)
    with _savepoint(conn):
        _transition(
            conn,
            command_id,
            SettlementState.REDEEM_OPERATOR_REQUIRED,
            payload={"reason": "stub_deferred"},
            recorded_at=_coerce_time(None),
        )
    conn.commit()

    # Now operator CLI atomically transitions to TX_HASHED
    transitioned = _atomic_transition(
        conn,
        command_id,
        from_state=SettlementState.REDEEM_OPERATOR_REQUIRED,
        to_state=SettlementState.REDEEM_TX_HASHED,
        tx_hash="0x" + "b" * 64,
        submitted_at=NOW.isoformat(),
        payload={"actor": "operator", "actor_override": False},
        recorded_at=NOW.isoformat(),
    )

    assert transitioned is True
    row = conn.execute(
        "SELECT state, tx_hash, submitted_at FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_TX_HASHED"
    assert row["tx_hash"] == "0x" + "b" * 64
    assert row["submitted_at"] == NOW.isoformat()
