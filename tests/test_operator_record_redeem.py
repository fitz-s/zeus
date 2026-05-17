# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: SCAFFOLD_F14_F16.md §K.5 + §K.4 v5

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.contracts.fx_classification import FXClassification
from src.state.db import init_schema

NOW = datetime(2026, 5, 16, 20, 0, tzinfo=timezone.utc)
GOOD_HASH = "0x" + "a" * 64
OTHER_HASH = "0x" + "b" * 64


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    yield db
    db.close()


def _seed_operator_required(conn, monkeypatch, condition_id="c-test") -> str:
    """Seed a row in REDEEM_OPERATOR_REQUIRED state."""
    from src.execution.settlement_commands import (
        SettlementState,
        _savepoint,
        _transition,
        _coerce_time,
        request_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="m-test",
        pusd_amount_micro=1,
        conn=conn,
        requested_at=NOW,
    )
    with _savepoint(conn):
        _transition(
            conn,
            command_id,
            SettlementState.REDEEM_OPERATOR_REQUIRED,
            payload={"reason": "test_setup"},
            recorded_at=_coerce_time(None),
        )
    conn.commit()
    return command_id


def _seed_state(conn, monkeypatch, state_str: str, condition_id="c-test") -> str:
    """Seed a row in any given state via _transition."""
    from src.execution.settlement_commands import (
        SettlementState,
        _savepoint,
        _transition,
        _coerce_time,
        request_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="m-test",
        pusd_amount_micro=1,
        conn=conn,
        requested_at=NOW,
    )
    if state_str != "REDEEM_INTENT_CREATED":
        with _savepoint(conn):
            _transition(
                conn,
                command_id,
                SettlementState(state_str),
                payload={"reason": "test_setup"},
                recorded_at=_coerce_time(None),
            )
        conn.commit()
    return command_id


def test_operator_record_advances_state_to_tx_hashed(conn, monkeypatch):
    from scripts.operator_record_redeem import _do_record

    command_id = _seed_operator_required(conn, monkeypatch, condition_id="c-1")
    outcome = _do_record(
        conn, condition_id="c-1", tx_hash=GOOD_HASH, force=False, notes=None,
    )
    assert outcome["result"] == "recorded"
    assert outcome["command_id"] == command_id
    row = conn.execute(
        "SELECT state, tx_hash FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row["state"] == "REDEEM_TX_HASHED"
    assert row["tx_hash"] == GOOD_HASH

    # Audit event appended with actor + actor_override=false
    events = conn.execute(
        "SELECT event_type, payload_json FROM settlement_command_events "
        "WHERE command_id = ? ORDER BY id",
        (command_id,),
    ).fetchall()
    last = events[-1]
    assert last["event_type"] == "REDEEM_TX_HASHED"
    import json as _json
    payload = _json.loads(last["payload_json"])
    assert payload["actor"] == "operator"
    assert payload["actor_override"] is False
    assert payload["prior_state"] == "REDEEM_OPERATOR_REQUIRED"


def test_operator_record_rejects_wrong_state(conn, monkeypatch):
    """NORMAL mode against REDEEM_INTENT_CREATED → exit 2."""
    from scripts.operator_record_redeem import _do_record

    _seed_state(conn, monkeypatch, "REDEEM_INTENT_CREATED", condition_id="c-2")
    with pytest.raises(SystemExit) as exc:
        _do_record(
            conn, condition_id="c-2", tx_hash=GOOD_HASH, force=False, notes=None,
        )
    assert exc.value.code == 2
    row = conn.execute("SELECT state FROM settlement_commands WHERE condition_id='c-2'").fetchone()
    assert row["state"] == "REDEEM_INTENT_CREATED", "row unchanged on reject"


def test_operator_record_rejects_malformed_tx_hash(conn, monkeypatch):
    """exit 3 on malformed hash; no DB touch."""
    from scripts.operator_record_redeem import _do_record

    _seed_operator_required(conn, monkeypatch, condition_id="c-3")
    with pytest.raises(SystemExit) as exc:
        _do_record(
            conn, condition_id="c-3", tx_hash="0xabc", force=False, notes=None,
        )
    assert exc.value.code == 3
    row = conn.execute("SELECT state FROM settlement_commands WHERE condition_id='c-3'").fetchone()
    assert row["state"] == "REDEEM_OPERATOR_REQUIRED"


def test_operator_record_is_idempotent_with_same_hash(conn, monkeypatch):
    """NORMAL mode against existing TX_HASHED with SAME hash → no-op, exit 0."""
    from scripts.operator_record_redeem import _do_record

    _seed_operator_required(conn, monkeypatch, condition_id="c-4")
    # First record
    _do_record(conn, condition_id="c-4", tx_hash=GOOD_HASH, force=False, notes=None)
    # Re-run with same hash → no-op
    outcome = _do_record(
        conn, condition_id="c-4", tx_hash=GOOD_HASH, force=False, notes=None,
    )
    assert outcome["result"] == "already_recorded_no_op"


def test_operator_record_rejects_conflicting_hash(conn, monkeypatch):
    """NORMAL mode against TX_HASHED with DIFFERENT hash → exit 6."""
    from scripts.operator_record_redeem import _do_record

    _seed_operator_required(conn, monkeypatch, condition_id="c-5")
    _do_record(conn, condition_id="c-5", tx_hash=GOOD_HASH, force=False, notes=None)
    with pytest.raises(SystemExit) as exc:
        _do_record(
            conn, condition_id="c-5", tx_hash=OTHER_HASH, force=False, notes=None,
        )
    assert exc.value.code == 6


def test_force_overwrites_conflicting_hash(conn, monkeypatch):
    """--force on TX_HASHED with different hash → overwrites + audit override."""
    from scripts.operator_record_redeem import _do_record

    _seed_operator_required(conn, monkeypatch, condition_id="c-6")
    _do_record(conn, condition_id="c-6", tx_hash=GOOD_HASH, force=False, notes=None)
    outcome = _do_record(
        conn, condition_id="c-6", tx_hash=OTHER_HASH,
        force=True, notes="operator-deliberate-overwrite-test",
    )
    assert outcome["result"] == "recorded_force"
    row = conn.execute("SELECT tx_hash FROM settlement_commands WHERE condition_id='c-6'").fetchone()
    assert row["tx_hash"] == OTHER_HASH

    import json as _json
    last_event = conn.execute(
        "SELECT payload_json FROM settlement_command_events "
        "WHERE command_id = (SELECT command_id FROM settlement_commands WHERE condition_id='c-6') "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = _json.loads(last_event["payload_json"])
    assert payload["actor_override"] is True
    assert payload["prior_tx_hash"] == GOOD_HASH


def test_force_re_records_after_failure(conn, monkeypatch):
    """--force on REDEEM_FAILED → recovery to TX_HASHED."""
    from scripts.operator_record_redeem import _do_record

    _seed_state(conn, monkeypatch, "REDEEM_FAILED", condition_id="c-7")
    outcome = _do_record(
        conn, condition_id="c-7", tx_hash=GOOD_HASH,
        force=True, notes="recover-from-failed-after-manual-investigation",
    )
    assert outcome["result"] == "recorded_force"
    row = conn.execute("SELECT state FROM settlement_commands WHERE condition_id='c-7'").fetchone()
    assert row["state"] == "REDEEM_TX_HASHED"


def test_force_rejects_no_notes(conn, monkeypatch):
    """--force without --notes → exit 7."""
    from scripts.operator_record_redeem import _do_record

    _seed_operator_required(conn, monkeypatch, condition_id="c-8")
    with pytest.raises(SystemExit) as exc:
        _do_record(
            conn, condition_id="c-8", tx_hash=GOOD_HASH, force=True, notes=None,
        )
    assert exc.value.code == 7


def test_force_rejects_short_notes(conn, monkeypatch):
    """--force with <10-char notes → exit 7."""
    from scripts.operator_record_redeem import _do_record

    _seed_operator_required(conn, monkeypatch, condition_id="c-9")
    with pytest.raises(SystemExit) as exc:
        _do_record(
            conn, condition_id="c-9", tx_hash=GOOD_HASH, force=True, notes="short",
        )
    assert exc.value.code == 7


def test_force_rejects_submitted_state(conn, monkeypatch):
    """--force MUST NOT allow REDEEM_SUBMITTED (round-3 critic P2-v4 fix:
    SUBMITTED is in-flight adapter window; operator override = double-redeem hazard).
    """
    from scripts.operator_record_redeem import _do_record

    _seed_state(conn, monkeypatch, "REDEEM_SUBMITTED", condition_id="c-10")
    with pytest.raises(SystemExit) as exc:
        _do_record(
            conn, condition_id="c-10", tx_hash=GOOD_HASH,
            force=True, notes="attempting-disallowed-submitted-override",
        )
    assert exc.value.code == 2  # wrong-state rejection
    row = conn.execute("SELECT state FROM settlement_commands WHERE condition_id='c-10'").fetchone()
    assert row["state"] == "REDEEM_SUBMITTED", "SUBMITTED row must remain unchanged"


def test_zero_rows_rejection(conn):
    """No active row for condition_id → exit 5."""
    from scripts.operator_record_redeem import _do_record

    # don't seed any row
    with pytest.raises(SystemExit) as exc:
        _do_record(
            conn, condition_id="c-nonexistent", tx_hash=GOOD_HASH,
            force=False, notes=None,
        )
    assert exc.value.code == 5
