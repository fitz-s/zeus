# Created: 2026-09-16
# Purpose: An ENTRY whose post-ACK persistence died before the pending_entry
#   projection was ever written must still terminalise once the venue proves the
#   order is gone with zero fill. A missing position_current row is zero
#   exposure, not ambiguity.
# Reuse: Run when the already-canceled no-fill recovery lane, its clearance
#   validator, or the position-scope fill-evidence predicate changes.
from __future__ import annotations

import json

import pytest

from tests.test_command_recovery import (  # noqa: F401 — fixtures re-exported below
    _advance_to_acked,
    _get_events,
    _get_state,
    _append_trade_fact,
    _insert,
    conn,
    mock_client,
)


def _arm_already_canceled_no_fill(conn, *, order_id: str) -> None:
    from src.state.venue_command_repo import append_event

    append_event(
        conn,
        command_id="cmd-001",
        event_type="CANCEL_REQUESTED",
        occurred_at="2026-04-26T00:07:00Z",
        payload={"venue_order_id": order_id},
    )
    append_event(
        conn,
        command_id="cmd-001",
        event_type="CANCEL_FAILED",
        occurred_at="2026-04-26T00:08:00Z",
        payload={
            "venue_order_id": order_id,
            "reason": f"{order_id}: order can't be found - already canceled or matched",
            "cancel_outcome": {
                "orderID": order_id,
                "status": "NOT_CANCELED",
                "errorMessage": (
                    f"{order_id}: order can't be found - already canceled or matched"
                ),
            },
        },
    )


class TestMissingProjectionNoFillClearance:
    def test_unprojected_entry_expires_when_venue_proves_zero_fill(
        self,
        conn,
        mock_client,
    ):
        """No position_current row at all must read as zero exposure.

        Reproduces the live shape of command 2294a6e41dd7439e: the ACK write
        lease timed out after the venue side effect, so the pending_entry
        projection was never written. The command is bound to a position_id that
        has no row anywhere, and the venue later reports the order absent with
        no fill. Before the fix, `_dict_row(None)` yielded phase="" and every
        admission branch read False, stranding the command in REVIEW_REQUIRED
        forever and holding the restart obligation gate shut.
        """
        order_id = "ord-never-projected"
        _insert(
            conn,
            size=46.0,
            price=0.10,
            event_slug="lowest-temperature-in-zhengzhou-on-september-16-2026-17c",
        )
        _advance_to_acked(conn, venue_order_id=order_id)
        # Deliberately NO _seed_pending_entry_projection: this is the defect.
        assert conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()[0] == 0

        _arm_already_canceled_no_fill(conn, order_id=order_id)

        mock_client.get_order.return_value = {
            "orderID": order_id,
            "status": "CANCELED",
            "original_size": "46",
            "size_matched": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] >= 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        event = _get_events(conn, "cmd-001")[-1]
        assert event["event_type"] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        payload = json.loads(event["payload_json"])
        assert (
            payload["proof_class"]
            == "cancel_failed_already_canceled_terminal_no_fill"
        )

    def test_unprojected_entry_stays_when_a_sibling_command_filled(
        self,
        conn,
        mock_client,
    ):
        """Absence is only zero exposure when nothing on the position filled.

        A sibling command bound to the same position carries a positive trade
        fact, so the missing projection means "a fill lost its projection", not
        "no exposure was ever created". The lane must fail closed.
        """
        order_id = "ord-never-projected"
        _insert(conn, size=46.0, price=0.10)
        _advance_to_acked(conn, venue_order_id=order_id)
        _insert(conn, command_id="cmd-sibling", position_id="pos-001")
        _append_trade_fact(
            conn,
            command_id="cmd-sibling",
            order_id="ord-sibling",
            trade_id="trade-sibling",
            state="CONFIRMED",
            filled_size="46",
            fill_price="0.10",
        )
        _arm_already_canceled_no_fill(conn, order_id=order_id)

        mock_client.get_order.return_value = {
            "orderID": order_id,
            "status": "CANCELED",
            "original_size": "46",
            "size_matched": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
