# Lifecycle: created=2026-05-16; last_reviewed=2026-05-21; last_reused=never
# Purpose: Regression coverage for F14 cascade-liveness fix — verifies
#   submit_redeem stub-detect branch transitions to REDEEM_OPERATOR_REQUIRED
#   (not REDEEM_REVIEW_REQUIRED catch-all), atomicity contract that
#   logger.warning fires only on successful transition, and the
#   _atomic_transition WHERE-state-guard primitive used by the operator CLI.
# Reuse: Run on every PR touching src/execution/settlement_commands.py state
#   machine or the submit_redeem function body. Authority basis:
#   docs/archive/2026-Q2/task_2026-05-16_deep_alignment_audit/SCAFFOLD_F14_F16.md §K.5 + §K.3 v5.
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


# _allow_redemption / _seed_intent_row / _StubDeferredAdapter / _UnexpectedErrorAdapter / test_submit_redeem_transitions_to_operator_required_on_stub /
# test_submit_redeem_transitions_to_failed_on_unexpected_error DELETED 2026-07-08 (R6-a):
# submit_redeem itself is deleted (dead redeem-submission machinery, Zeus never submits
# redeem tx, operator law 2026-06-10). The _transition/_atomic_transition primitive tests
# below are unaffected (those helpers are KEPT -- used by reconcile_pending_redeems and
# the operator CLI scripts/operator_record_redeem.py).


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


def test_transition_preserves_autoretry_eligibility_when_unspecified(conn, monkeypatch):
    """Omitting autoretry_eligible preserves the current review flag.

    The primitive's None value means "not part of this transition", while an
    explicit False is the only generic way to clear an existing retry flag.
    """
    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import (
        SettlementState,
        _coerce_time,
        _savepoint,
        _transition,
        request_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    command_id = request_redeem(
        "c-test-preserve",
        "pUSD",
        market_id="m-test-preserve",
        pusd_amount_micro=1,
        conn=conn,
        requested_at=NOW,
    )
    with _savepoint(conn):
        _transition(
            conn,
            command_id,
            SettlementState.REDEEM_OPERATOR_REQUIRED,
            payload={"reason": "seed_autoretry"},
            autoretry_eligible=True,
            recorded_at=_coerce_time(None),
        )
        _transition(
            conn,
            command_id,
            SettlementState.REDEEM_REVIEW_REQUIRED,
            payload={"reason": "preserve_flag"},
            recorded_at=_coerce_time(None),
        )
    row = conn.execute(
        "SELECT state, autoretry_eligible FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_REVIEW_REQUIRED"
    assert row["autoretry_eligible"] == 1

    with _savepoint(conn):
        _transition(
            conn,
            command_id,
            SettlementState.REDEEM_FAILED,
            payload={"reason": "explicit_clear"},
            autoretry_eligible=False,
            terminal=True,
            recorded_at=_coerce_time(None),
        )
    row = conn.execute(
        "SELECT state, autoretry_eligible FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_FAILED"
    assert row["autoretry_eligible"] == 0


def test_atomic_transition_preserves_autoretry_eligibility_when_unspecified(conn, monkeypatch):
    """The cross-process transition helper must not clear review flags by default."""
    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import (
        SettlementState,
        _atomic_transition,
        _coerce_time,
        _savepoint,
        _transition,
        request_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    command_id = request_redeem(
        "c-test-atomic-preserve",
        "pUSD",
        market_id="m-test-atomic-preserve",
        pusd_amount_micro=1,
        conn=conn,
        requested_at=NOW,
    )
    with _savepoint(conn):
        _transition(
            conn,
            command_id,
            SettlementState.REDEEM_OPERATOR_REQUIRED,
            payload={"reason": "seed_autoretry"},
            autoretry_eligible=True,
            recorded_at=_coerce_time(None),
        )
    conn.commit()

    assert _atomic_transition(
        conn,
        command_id,
        from_state=SettlementState.REDEEM_OPERATOR_REQUIRED,
        to_state=SettlementState.REDEEM_REVIEW_REQUIRED,
        payload={"actor": "test"},
        recorded_at=NOW.isoformat(),
    )
    row = conn.execute(
        "SELECT state, autoretry_eligible FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_REVIEW_REQUIRED"
    assert row["autoretry_eligible"] == 1

    assert _atomic_transition(
        conn,
        command_id,
        from_state=SettlementState.REDEEM_REVIEW_REQUIRED,
        to_state=SettlementState.REDEEM_FAILED,
        autoretry_eligible=False,
        terminal_at=NOW.isoformat(),
        payload={"actor": "test"},
        recorded_at=NOW.isoformat(),
    )
    row = conn.execute(
        "SELECT state, autoretry_eligible FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_FAILED"
    assert row["autoretry_eligible"] == 0
