"""A command may end by a GENUINE cancel while carrying a real partial fill.

The venue fills 72 of 72.37 and cancels the 0.37 remainder. Such a terminal
event can carry neither `terminal_no_fill` (a fill did occur) nor the
later-trade proof_class (the trade lands BEFORE the cancel ack), so before this
fix nothing minted `execution_fact` for it. riskguard then refused the position
(`fill-grade loader row missing execution_fact source provenance`), which drove
`portfolio_consistency` to DATA_DEGRADED and latched the whole book reduce-only
with no lane able to clear it.

Fill truth is a property of the venue's order ledger, not of which event arrived
last. These tests pin that, and pin the boundary: no fill on the ledger means no
scheduling.
"""
import hashlib
from datetime import timedelta

import pytest

from tests.test_exchange_reconcile import (  # noqa: E402 - shared fixtures
    NOW,
    conn,  # noqa: F401 - pytest fixture
    seed_command,
    seed_position_baseline,
)


def _terminal_cancel_with_fill(
    c,
    *,
    matched_size: str,
    remaining_size: str,
    trade_size: str | None,
    open_shares: float,
    cancel_payload: dict | None = None,
):
    """Seed: filled on the ledger, then a genuine CANCEL_ACKED of the remainder."""
    from src.state.venue_command_repo import append_event, append_order_fact, append_trade_fact

    seed_command(c, size=10, price=0.50)
    seed_position_baseline(c)
    c.execute(
        "UPDATE position_current SET phase = 'day0_window', shares = ?, "
        "cost_basis_usd = ?, size_usd = ? WHERE position_id = 'pos-m5'",
        (open_shares, open_shares * 0.5, open_shares * 0.5),
    )
    fill_at = NOW + timedelta(seconds=30)
    if trade_size is not None:
        append_trade_fact(
            c,
            trade_id="trade-genuine-cancel",
            venue_order_id="ord-m5",
            command_id="cmd-m5",
            state="CONFIRMED",
            filled_size=trade_size,
            fill_price="0.50",
            source="WS_USER",
            observed_at=fill_at,
            raw_payload_hash=hashlib.sha256(b"genuine-cancel-trade").hexdigest(),
            raw_payload_json={"proof": "authenticated_trade"},
        )
    cancel_at = fill_at + timedelta(minutes=2)
    # The live shape: we request the cancel, the venue acks it. The grammar
    # (venue_command_repo._TRANSITIONS) requires the request first.
    append_event(
        c,
        command_id="cmd-m5",
        event_type="CANCEL_REQUESTED",
        occurred_at=(cancel_at - timedelta(seconds=30)).isoformat(),
        payload={"cancel_reason": "BOOK_MOVED"},
    )
    append_order_fact(
        c,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CANCEL_CONFIRMED",
        remaining_size=remaining_size,
        matched_size=matched_size,
        source="WS_USER",
        observed_at=cancel_at,
        raw_payload_hash=hashlib.sha256(b"genuine-cancel-order").hexdigest(),
        raw_payload_json={"proof": "venue_cancelled_remainder"},
    )
    # A real cancel ack: no terminal_no_fill claim, no late-trade proof_class.
    append_event(
        c,
        command_id="cmd-m5",
        event_type="CANCEL_ACKED",
        occurred_at=cancel_at.isoformat(),
        payload=cancel_payload
        if cancel_payload is not None
        else {"venue_order_id": "ord-m5", "cancel_outcome": {"status": "CANCELED"}},
    )
    return cancel_at


def test_genuine_cancel_of_the_remainder_is_scheduled_from_the_order_ledger(conn):
    from src.execution.exchange_reconcile import (
        persisted_terminal_late_entry_fill_command_ids,
    )

    _terminal_cancel_with_fill(
        conn, matched_size="72", remaining_size="0", trade_size="72", open_shares=72.0
    )
    assert persisted_terminal_late_entry_fill_command_ids(conn) == ["cmd-m5"]


def test_the_reconciler_scans_it_without_error(conn):
    """Scheduling is this change's scope; the MINT still needs one more surface.

    `_terminal_entry_fill_boundary` classifies only a `terminal_no_fill` claim or
    the later-trade `proof_class`, so a plain CANCEL_ACKED yields no boundary and
    the reconciler stays instead of advancing. That is deliberate here: this diff
    makes the row VISIBLE to the lane (it was invisible, and therefore
    unrepairable by anything) and stops short of minting, because the boundary
    classifier decides how much of the fill is already sourced and getting that
    wrong double-counts capital.

    What this pins: the lane accepts the row and completes cleanly — no error, no
    silent crash — so the remaining work is a boundary classification, not a
    plumbing failure.
    """
    from src.execution.exchange_reconcile import (
        reconcile_persisted_terminal_late_entry_fills,
    )

    cancel_at = _terminal_cancel_with_fill(
        conn, matched_size="72", remaining_size="0", trade_size="72", open_shares=72.0
    )
    summary = reconcile_persisted_terminal_late_entry_fills(
        conn, command_id="cmd-m5", observed_at=cancel_at + timedelta(seconds=1)
    )
    assert summary["scanned"] == 1
    assert summary["errors"] == 0
    assert summary["advanced"] + summary["stayed"] == 1


def test_a_terminal_command_with_no_ledger_fill_is_never_scheduled(conn):
    """The boundary: zero matched size means nothing to source."""
    from src.execution.exchange_reconcile import (
        persisted_terminal_late_entry_fill_command_ids,
    )

    _terminal_cancel_with_fill(
        conn, matched_size="0", remaining_size="10", trade_size=None, open_shares=0.0
    )
    assert persisted_terminal_late_entry_fill_command_ids(conn) == []


def test_ledger_fill_without_positive_trade_economics_is_not_scheduled(conn):
    """matched_size alone does not authorize economics.

    The outer CONFIRMED-trade-with-positive-price requirement still gates the
    actual numbers, so a ledger that claims a match with no authenticated trade
    behind it cannot mint a fill.
    """
    from src.execution.exchange_reconcile import (
        persisted_terminal_late_entry_fill_command_ids,
    )

    _terminal_cancel_with_fill(
        conn, matched_size="72", remaining_size="0", trade_size=None, open_shares=72.0
    )
    assert persisted_terminal_late_entry_fill_command_ids(conn) == []


def test_a_closed_position_is_not_reopened_by_an_old_ledger_fill(conn):
    """Only OPEN exposure is scheduled; a settled/closed row stays untouched."""
    from src.execution.exchange_reconcile import (
        persisted_terminal_late_entry_fill_command_ids,
    )

    _terminal_cancel_with_fill(
        conn, matched_size="72", remaining_size="0", trade_size="72", open_shares=72.0
    )
    conn.execute(
        "UPDATE position_current SET phase = 'settled' WHERE position_id = 'pos-m5'"
    )
    assert persisted_terminal_late_entry_fill_command_ids(conn) == []
