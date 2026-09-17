# Created: 2026-09-17
# Purpose: A post-ACK REVIEW_REQUIRED command whose venue order returns an
#   authenticated 404 must terminalise. The lane collapsed VenueOrderNotFound
#   into a bare `except Exception`, so point_order stayed None, the terminal
#   fact was never appended, and the command sat REVIEW_REQUIRED forever while
#   its venue side was long gone — holding the restart obligation gate shut.
# Reuse: Run when _review_required_post_ack_terminal_no_fill_recovery's venue
#   read or its terminal-fact mapping changes.
from __future__ import annotations

import json

import pytest

from tests.test_command_recovery import (  # noqa: F401 — fixtures re-exported
    _advance_to_acked,
    _get_events,
    _get_state,
    _insert,
    conn,
    mock_client,
)


def _arm_post_ack_review(conn, *, order_id: str) -> None:
    from src.state.venue_command_repo import append_event

    append_event(
        conn,
        command_id="cmd-001",
        event_type="REVIEW_REQUIRED",
        occurred_at="2026-04-26T00:07:00Z",
        payload={
            "reason": "entry_ack_persistence_failed_after_side_effect",
            "detail": "database is locked: post-submit writer deferred",
            "venue_order_id": order_id,
        },
    )


class TestAuthenticated404Terminalises:
    def test_an_authenticated_404_with_a_complete_account_read_expires(
        self, conn, mock_client
    ):
        """404 + complete account read + no local fill = terminal, zero fill."""
        from src.venue.polymarket_v2_adapter import VenueOrderNotFound

        order_id = "ord-gone-at-venue"
        _insert(
            conn,
            size=135.02,
            price=0.15,
            event_slug="lowest-temperature-in-zhengzhou-on-september-16-2026-17c",
        )
        _advance_to_acked(conn, venue_order_id=order_id)
        _arm_post_ack_review(conn, order_id=order_id)

        mock_client.get_order.side_effect = VenueOrderNotFound(order_id)
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        type(mock_client).venue_reads_are_complete = True

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] >= 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        fact = conn.execute(
            "SELECT state, matched_size FROM venue_order_facts "
            "WHERE command_id = 'cmd-001' ORDER BY local_sequence DESC LIMIT 1"
        ).fetchone()
        # No venue response for the order maps to VENUE_WIPED, not a status-
        # derived CANCEL_CONFIRMED the venue never actually reported.
        assert fact["state"] == "VENUE_WIPED"
        assert float(fact["matched_size"]) == 0.0

    def test_the_live_client_shape_also_terminalises(self, conn, mock_client):
        """The live PolymarketClient has no venue_reads_are_complete attribute.

        It declares instead that an authenticated point read reports a missing
        order as absence rather than as an error. That is the same statement
        this branch needs, and keying only on the snapshot client's flag left
        the fix inert on the live path — measured against the real client, which
        returns ABSENT for venue_reads_are_complete and True for
        authenticated_point_absence_returns_none.
        """
        from src.venue.polymarket_v2_adapter import VenueOrderNotFound

        order_id = "ord-gone-at-venue"
        _insert(
            conn,
            size=135.02,
            price=0.15,
            event_slug="lowest-temperature-in-zhengzhou-on-september-16-2026-17c",
        )
        _advance_to_acked(conn, venue_order_id=order_id)
        _arm_post_ack_review(conn, order_id=order_id)

        mock_client.get_order.side_effect = VenueOrderNotFound(order_id)
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        # Exactly the live shape: no completeness flag, absence-returns-none set.
        if hasattr(type(mock_client), "venue_reads_are_complete"):
            delattr(type(mock_client), "venue_reads_are_complete")
        type(mock_client).authenticated_point_absence_returns_none = True

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] >= 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"

    def test_an_incomplete_account_read_does_not_terminalise(
        self, conn, mock_client
    ):
        """Fail closed: absence is only proof when the account read was complete."""
        from src.venue.polymarket_v2_adapter import VenueOrderNotFound

        order_id = "ord-gone-at-venue"
        _insert(conn, size=135.02, price=0.15)
        _advance_to_acked(conn, venue_order_id=order_id)
        _arm_post_ack_review(conn, order_id=order_id)

        mock_client.get_order.side_effect = VenueOrderNotFound(order_id)
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        type(mock_client).venue_reads_are_complete = False
        type(mock_client).authenticated_point_absence_returns_none = False

        from src.execution.command_recovery import reconcile_unresolved_commands

        reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"

    def test_a_transport_failure_is_still_not_absence(self, conn, mock_client):
        """A generic read error must not be read as proof the order is gone."""
        order_id = "ord-unreadable"
        _insert(conn, size=135.02, price=0.15)
        _advance_to_acked(conn, venue_order_id=order_id)
        _arm_post_ack_review(conn, order_id=order_id)

        mock_client.get_order.side_effect = RuntimeError("transport blew up")
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        type(mock_client).venue_reads_are_complete = True

        from src.execution.command_recovery import reconcile_unresolved_commands

        reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
