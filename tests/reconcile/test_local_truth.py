# Created: 2026-07-08
"""Golden-row tests for src.reconcile.local_truth's snapshot contract."""
from __future__ import annotations

from src.reconcile.local_truth import load_local_truth_snapshot
from tests.reconcile.conftest import insert_position_current, insert_reservation, insert_venue_command


def test_command_with_no_reservation_has_none_fields(trades_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1")
    trades_conn.commit()

    snapshot = load_local_truth_snapshot(trades_conn)

    cmd = snapshot.commands["cmd-1"]
    assert cmd.position_id == "pos-1"
    assert cmd.reservation_amount is None
    assert cmd.has_reservation is False
    assert cmd.reservation_released is False


def test_command_with_open_reservation(trades_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1")
    insert_reservation(trades_conn, command_id="cmd-1", amount=5_000_000)
    trades_conn.commit()

    snapshot = load_local_truth_snapshot(trades_conn)

    cmd = snapshot.commands["cmd-1"]
    assert cmd.has_reservation is True
    assert cmd.reservation_amount == 5_000_000
    assert cmd.reservation_released is False


def test_command_with_released_reservation(trades_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1")
    insert_reservation(
        trades_conn,
        command_id="cmd-1",
        released_at="2026-07-04T00:05:00+00:00",
        release_reason="EXPIRED",
        converted_amount=0,
    )
    trades_conn.commit()

    snapshot = load_local_truth_snapshot(trades_conn)

    cmd = snapshot.commands["cmd-1"]
    assert cmd.reservation_released is True
    assert cmd.reservation_release_reason == "EXPIRED"
    assert cmd.reservation_converted_amount == 0


def test_position_projection_fields_round_trip(trades_conn):
    insert_position_current(
        trades_conn,
        position_id="pos-1",
        phase="settled",
        realized_pnl_usd=4.5,
        exit_price=1.0,
        settled_at="2026-07-05T00:00:00+00:00",
    )
    trades_conn.commit()

    snapshot = load_local_truth_snapshot(trades_conn)

    position = snapshot.positions["pos-1"]
    assert position.phase == "settled"
    assert position.realized_pnl_usd == 4.5
    assert position.exit_price == 1.0
    assert position.settled_at == "2026-07-05T00:00:00+00:00"
    assert position.held_token_id() == "tok-yes"


def test_held_token_id_uses_no_token_for_buy_no(trades_conn):
    insert_position_current(trades_conn, position_id="pos-1", direction="buy_no")
    trades_conn.commit()

    snapshot = load_local_truth_snapshot(trades_conn)

    assert snapshot.positions["pos-1"].held_token_id() == "tok-no"


def test_commands_for_position_scopes_correctly(trades_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_position_current(trades_conn, position_id="pos-2")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-2", position_id="pos-2")
    trades_conn.commit()

    snapshot = load_local_truth_snapshot(trades_conn)

    assert [c.command_id for c in snapshot.commands_for_position("pos-1")] == ["cmd-1"]
    assert [c.command_id for c in snapshot.commands_for_position("pos-2")] == ["cmd-2"]
