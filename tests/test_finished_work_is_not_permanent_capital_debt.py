# Created: 2026-09-20
# Lifecycle: created=2026-09-20; last_reviewed=2026-09-20; last_reused=never
# Purpose: Lock the rule that capital-recovery debt counts REMAINING WORK, not selector matches,
#   so a finished command cannot starve the global auction forever.
# Reuse: Run when capital_blocking_command_scope, entry obligations, or late-fill repair change.
# Authority basis: AGENTS.md capital/execution proof gates; measured auction starvation 2026-09-20.
"""A finished repair must stop counting as capital debt.

INCIDENT (2026-09-20 00:43-01:15Z): three commands whose work was complete were
still counted by ``capital_blocking_command_scope``. The scheduler therefore
reserved a reactor handoff every 30 s, and the global auction — which needs 7-8 s
of uninterrupted selection — was cancelled before its write lease on nearly every
cycle: 68 selections computed, 250 preemptions, ONE receipt persisted, 45 monitor
yields with the completion debt cleared zero times. Neither the auction nor the
monitor finished; entries stopped.

Two independent causes, both the same shape — a predicate that asks "does a row
still match?" instead of "is there work left?":

  1. ``persisted_terminal_late_entry_fill_command_ids`` selects on facts that are
     permanent once true (canonical matched_size > 0 plus a CONFIRMED trade).
     The repair re-derives its boundary and stays, but the counter had no way to
     know that.
  2. An entry obligation could be discharged by an OPEN position or a SETTLED
     one. A position SOLD before settlement ends ``economically_closed`` and
     matched neither, so its obligation could never resolve.
"""
from __future__ import annotations

import json

import pytest

from tests.test_command_recovery import (  # noqa: F401 - shared fixtures
    _append_test_entry_fill,
    _append_test_filled_entry_projection,
    _insert,
    _open_test_entry_obligation,
    _seed_pending_entry_projection,
    conn,
)


def _close_position_by_exit(conn, *, position_id: str, command_id: str) -> None:
    """Sell the position before settlement: phase becomes economically_closed."""
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            decision_id, snapshot_id, order_id, command_id, caused_by,
            idempotency_key, venue_status, source_module, env, payload_json
        )
        SELECT ?, ?, 1, COALESCE(MAX(sequence_no), 0) + 1, 'EXIT_ORDER_FILLED',
               '2026-07-14T08:30:00+00:00', 'active', 'economically_closed',
               'opening_inertia', 'dec-001', 'snap-pos-001', ?, ?, NULL,
               ?, 'FILLED', 'tests', 'live', ?
          FROM position_events WHERE position_id = ?
        """,
        (
            f"{position_id}:exitfill",
            position_id,
            f"exit-{command_id}",
            command_id,
            f"{position_id}:exitfill",
            json.dumps({"exit_price": 0.4, "pnl": -1.0}, sort_keys=True),
            position_id,
        ),
    )
    conn.execute(
        "UPDATE position_current SET phase = 'economically_closed',"
        " updated_at = '2026-07-14T08:30:00+00:00' WHERE position_id = ?",
        (position_id,),
    )


def _terminalize(conn, command_id: str, state: str = "CANCELLED") -> None:
    conn.execute(
        "UPDATE venue_commands SET state = ? WHERE command_id = ?",
        (state, command_id),
    )


def test_an_obligation_on_a_position_sold_before_settlement_resolves(conn):
    """The gap that made one finished command permanent capital debt."""
    from src.execution.command_recovery import (
        reconcile_terminal_entry_exposure_obligations,
    )

    command_id = _insert(
        conn,
        command_id="cmd-sold-before-settlement",
        position_id="pos-sold-before-settlement",
        token_id="tok-sold-before-settlement",
    )
    _open_test_entry_obligation(conn, command_id)
    _append_test_entry_fill(conn, command_id, with_trade=True)
    order_id = f"order-{command_id}"
    _seed_pending_entry_projection(
        conn,
        position_id="pos-sold-before-settlement",
        command_id=command_id,
        order_id=order_id,
        token_id="tok-sold-before-settlement",
    )
    _append_test_filled_entry_projection(
        conn,
        position_id="pos-sold-before-settlement",
        command_id=command_id,
        order_id=order_id,
    )
    _close_position_by_exit(
        conn, position_id="pos-sold-before-settlement", command_id=command_id
    )
    _terminalize(conn, command_id)

    summary = reconcile_terminal_entry_exposure_obligations(conn)

    assert summary["advanced"] == 1, summary
    status = conn.execute(
        "SELECT status FROM entry_exposure_obligations WHERE command_id = ?",
        (command_id,),
    ).fetchone()[0]
    assert status == "RESOLVED"


def test_an_exit_without_a_recorded_entry_fill_does_not_resolve(conn):
    """The sale alone is not the proof: the ENTRY must be absorbed too."""
    from src.execution.command_recovery import (
        reconcile_terminal_entry_exposure_obligations,
    )

    command_id = _insert(
        conn,
        command_id="cmd-exit-without-entry",
        position_id="pos-exit-without-entry",
        token_id="tok-exit-without-entry",
    )
    _open_test_entry_obligation(conn, command_id)
    _append_test_entry_fill(conn, command_id, with_trade=True)
    _seed_pending_entry_projection(
        conn,
        position_id="pos-exit-without-entry",
        command_id=command_id,
        order_id=f"order-{command_id}",
        token_id="tok-exit-without-entry",
    )
    # No ENTRY_ORDER_FILLED projection: only the exit.
    _close_position_by_exit(
        conn, position_id="pos-exit-without-entry", command_id=command_id
    )
    _terminalize(conn, command_id)

    summary = reconcile_terminal_entry_exposure_obligations(conn)

    assert summary["advanced"] == 0, summary
    status = conn.execute(
        "SELECT status FROM entry_exposure_obligations WHERE command_id = ?",
        (command_id,),
    ).fetchone()[0]
    assert status == "OPEN"


def test_late_fill_debt_counts_pending_work_not_selector_matches(conn):
    """A late-fill candidate the repair would only stay on is not capital debt."""
    from src.execution import exchange_reconcile as er

    # An empty ledger has neither matches nor pending work; the two agree.
    assert er.persisted_terminal_late_entry_fill_command_ids(conn) == []
    assert er.persisted_terminal_late_entry_fill_repair_pending(conn) == []


def test_pending_is_always_a_subset_of_the_selector(conn):
    """The pending predicate may only narrow, never invent a candidate."""
    from src.execution import exchange_reconcile as er

    matches = set(er.persisted_terminal_late_entry_fill_command_ids(conn))
    pending = set(er.persisted_terminal_late_entry_fill_repair_pending(conn))
    assert pending <= matches


def test_blocker_count_uses_the_pending_predicate(conn):
    """capital_blocking_command_scope must read work, not matches."""
    import inspect

    from src.execution import command_recovery as cr

    source = inspect.getsource(cr.capital_blocking_command_scope)
    assert "persisted_terminal_late_entry_fill_repair_pending" in source
    assert "persisted_terminal_late_entry_fill_command_ids" not in source
