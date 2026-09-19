# Created: 2026-09-19
# Lifecycle: created=2026-09-19; last_reviewed=2026-09-19; last_reused=never
# Purpose: Lock the pairing invariant between a recovery SUBMIT_ACKED and its order fact.
# Reuse: Run when command recovery ACK paths or venue_order_facts consumers change.
# Authority basis: AGENTS.md execution proof gates; every ACKED-advancing lane keys on venue_order_facts.
"""A recovery ACK must persist the order fact its own authenticated read proved.

INCIDENT (2026-09-18 19:07Z): command a8c36967289b4416 advanced SUBMITTING ->
ACKED through recovery, which wrote the SUBMIT_ACKED event but no order fact.
Every lane able to terminalize an ACKED command keys on venue_order_facts, so
the row became invisible to all of them: it held a $22.40 PUSD_BUY reservation
and refused every loaded-daemon restart for 21 hours. The executor's own submit
path writes the event and the fact as one pair; these tests hold recovery to the
same contract and cover the repair lane for rows already stranded.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.test_command_recovery import _insert  # noqa: F401 - shared inserter


@pytest.fixture
def conn():
    from src.state.db import init_schema, init_schema_trade_only
    from src.state.collateral_ledger import init_collateral_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    init_schema_trade_only(c)
    init_collateral_schema(c)
    yield c
    c.close()


def _order_facts(conn, command_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM venue_order_facts WHERE command_id = ? "
            "ORDER BY local_sequence",
            (command_id,),
        ).fetchall()
    ]


def _point_client(*, order=None, not_found=False):
    from src.venue.response_contracts import VenueOrderNotFound

    client = MagicMock(spec_set=["get_order", "get_open_orders", "get_trades"])
    if not_found:
        client.get_order.side_effect = VenueOrderNotFound("no such order")
    else:
        client.get_order.return_value = order
    client.get_order.authenticated_point_reads_are_complete = True
    client.get_open_orders.return_value = []
    client.get_open_orders.venue_reads_are_complete = True
    client.get_trades.return_value = []
    client.get_trades.venue_reads_are_complete = True
    return client


def test_submitting_to_acked_recovery_persists_the_resting_fact(conn):
    """The SUBMITTING -> ACKED branch pairs its event with a LIVE order fact."""
    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-ack-live", size=20.0, price=0.54)
    conn.execute(
        "UPDATE venue_commands SET state = 'SUBMITTING', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xlive", command_id),
    )
    from src.execution.command_bus import VenueCommand

    cmd = VenueCommand.from_row(
        dict(
            conn.execute(
                "SELECT * FROM venue_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        )
    )
    client = _point_client(
        order={"orderID": "0xlive", "status": "LIVE", "size": "20", "price": "0.54"}
    )

    outcome = recovery._reconcile_row(conn, cmd, client)

    assert outcome == "advanced"
    facts = _order_facts(conn, command_id)
    assert len(facts) == 1, facts
    assert facts[0]["state"] == "LIVE"
    assert facts[0]["venue_order_id"] == "0xlive"
    assert facts[0]["matched_size"] == "0"


def test_recovery_ack_fact_is_visible_to_the_lane_that_must_close_it(conn):
    """The persisted fact puts the row back inside its owning lane's candidates."""
    from src.execution import command_recovery as recovery
    from src.execution.command_bus import VenueCommand

    command_id = _insert(conn, command_id="cmd-ack-visible", size=20.0, price=0.54)
    conn.execute(
        "UPDATE venue_commands SET state = 'SUBMITTING', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xvisible", command_id),
    )
    cmd = VenueCommand.from_row(
        dict(
            conn.execute(
                "SELECT * FROM venue_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        )
    )
    before = {
        str(row.get("command_id"))
        for row in recovery._latest_unprojected_live_entry_candidates(conn)
    }
    assert command_id not in before

    recovery._reconcile_row(
        conn,
        cmd,
        _point_client(
            order={
                "orderID": "0xvisible",
                "status": "LIVE",
                "size": "20",
                "price": "0.54",
            }
        ),
    )

    after = {
        str(row.get("command_id"))
        for row in recovery._latest_unprojected_live_entry_candidates(conn)
    }
    assert command_id in after


def test_acked_row_without_a_fact_is_selected_for_repair(conn):
    """An ACKED command with a bound order and no fact is a repair candidate."""
    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-stranded")
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xstranded", command_id),
    )

    ids = [
        str(row.get("command_id"))
        for row in recovery._acked_command_missing_order_fact_candidates(conn)
    ]

    assert ids == [command_id]


def test_acked_row_with_a_fact_is_not_a_repair_candidate(conn):
    """A row its own lanes can already see is never re-derived."""
    from src.execution import command_recovery as recovery
    from src.state.venue_command_repo import append_order_fact

    command_id = _insert(conn, command_id="cmd-has-fact")
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xhasfact", command_id),
    )
    append_order_fact(
        conn,
        venue_order_id="0xhasfact",
        command_id=command_id,
        state="LIVE",
        remaining_size="10",
        matched_size="0",
        source="REST",
        observed_at="2026-09-19T00:00:00+00:00",
        raw_payload_hash="0" * 64,
    )

    assert recovery._acked_command_missing_order_fact_candidates(conn) == []


def test_repair_derives_a_terminal_fact_from_an_authenticated_404(conn):
    """An authenticated absence becomes a terminal no-fill fact, not a guess."""
    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-repair-404")
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0x404", command_id),
    )

    summary = recovery.reconcile_acked_commands_missing_order_facts(
        conn, _point_client(not_found=True)
    )

    assert summary["scanned"] == 1
    assert summary["advanced"] == 1
    facts = _order_facts(conn, command_id)
    assert len(facts) == 1, facts
    assert facts[0]["state"] in {"VENUE_WIPED", "EXPIRED", "CANCEL_CONFIRMED"}
    assert facts[0]["matched_size"] in {"0", "0.0", None}


def test_repair_derives_a_resting_fact_when_the_order_still_lives(conn):
    """A live point read yields the resting fact, never a terminal one."""
    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-repair-live", size=20.0, price=0.54)
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xstillhere", command_id),
    )

    summary = recovery.reconcile_acked_commands_missing_order_facts(
        conn,
        _point_client(
            order={
                "orderID": "0xstillhere",
                "status": "LIVE",
                "size": "20",
                "price": "0.54",
            }
        ),
    )

    assert summary["advanced"] == 1
    facts = _order_facts(conn, command_id)
    assert len(facts) == 1
    assert facts[0]["state"] == "LIVE"


def test_repair_leaves_the_row_alone_when_the_read_is_incomplete(conn):
    """An unverified read is not absence: the row stays for the next cadence."""
    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-repair-unverified")
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xunverified", command_id),
    )
    # A client that returns None without declaring complete point reads: the
    # shipped contract treats that as an unverified read, never as absence.
    client = SimpleNamespace(
        get_order=lambda order_id: None,
        authenticated_point_reads_are_complete=False,
    )

    summary = recovery.reconcile_acked_commands_missing_order_facts(conn, client)

    assert summary["scanned"] == 1
    assert summary["advanced"] == 0
    assert summary["stayed"] == 1
    assert _order_facts(conn, command_id) == []


def test_repair_is_idempotent(conn):
    """A second pass finds nothing to do and writes no duplicate fact."""
    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-repair-twice", size=20.0, price=0.54)
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xtwice", command_id),
    )
    client = _point_client(
        order={"orderID": "0xtwice", "status": "LIVE", "size": "20", "price": "0.54"}
    )

    first = recovery.reconcile_acked_commands_missing_order_facts(conn, client)
    second = recovery.reconcile_acked_commands_missing_order_facts(conn, client)

    assert first["advanced"] == 1
    assert second["scanned"] == 0
    assert len(_order_facts(conn, command_id)) == 1


def test_repair_without_a_point_reader_advances_nothing(conn):
    """A client that cannot read orders never fabricates a fact."""
    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-repair-noclient")
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xnoclient", command_id),
    )

    summary = recovery.reconcile_acked_commands_missing_order_facts(
        conn, SimpleNamespace()
    )

    assert summary["advanced"] == 0
    assert summary["stayed"] == 1
    assert _order_facts(conn, command_id) == []


def test_repair_leaves_a_freshly_acked_row_to_its_own_writer(conn):
    """A row ACKed moments ago still has a writer completing its pair."""
    from datetime import datetime, timezone

    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-fresh-ack")
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ?, "
        "created_at = ?, updated_at = ? WHERE command_id = ?",
        (
            "0xfresh",
            datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat(),
            command_id,
        ),
    )

    assert recovery._acked_command_missing_order_fact_candidates(conn) == []


def test_repair_defers_a_matched_point_order_to_the_fill_lanes(conn):
    """Fill economics belong to the trade-fact lanes, not to this repair."""
    from src.execution import command_recovery as recovery

    command_id = _insert(conn, command_id="cmd-repair-matched", size=20.0)
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ? "
        "WHERE command_id = ?",
        ("0xmatched", command_id),
    )

    summary = recovery.reconcile_acked_commands_missing_order_facts(
        conn,
        _point_client(
            order={
                "orderID": "0xmatched",
                "status": "MATCHED",
                "size_matched": "20",
            }
        ),
    )

    assert summary["scanned"] == 1
    assert summary["advanced"] == 0
    assert summary["stayed"] == 1
    assert _order_facts(conn, command_id) == []


def test_the_404_repair_reaches_terminal_and_releases_its_collateral(conn):
    """The whole point: the derived fact must end in a released reservation.

    The repair only earns its existence if the fact it writes carries the row
    through the terminal-fact lane to collateral release -- that release is what
    the stranded row was holding hostage.
    """
    from src.execution import command_recovery as recovery

    # The live row this reproduces is a buy_no on a real weather event; the
    # void projection the terminal lane emits derives its identity from that
    # slug, so the fixture must carry one.
    command_id = _insert(
        conn,
        command_id="cmd-chain",
        size=41.48,
        price=0.54,
        selected_token_id="tok-chain-no",
        no_token_id="tok-chain-no",
        event_slug="highest-temperature-in-guangzhou-on-september-19-2026-36c",
    )
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ?, "
        "created_at = '2026-09-01T00:00:00+00:00', "
        "updated_at = '2026-09-01T00:00:00+00:00' WHERE command_id = ?",
        ("0xchain", command_id),
    )
    conn.execute(
        "INSERT INTO collateral_reservations("
        "  command_id, reservation_type, token_id, amount, created_at"
        ") VALUES (?, 'PUSD_BUY', NULL, 22399200, '2026-09-01T00:00:00+00:00')",
        (command_id,),
    )
    assert _unreleased(conn, command_id) == 1

    repaired = recovery.reconcile_acked_commands_missing_order_facts(
        conn, _point_client(not_found=True)
    )
    assert repaired["advanced"] == 1

    recovery.reconcile_terminal_order_facts(conn)

    state = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = ?", (command_id,)
    ).fetchone()[0]
    assert state in {"EXPIRED", "CANCELLED", "REJECTED"}, state
    assert _unreleased(conn, command_id) == 0


def _unreleased(conn, command_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM collateral_reservations "
        "WHERE command_id = ? AND released_at IS NULL",
        (command_id,),
    ).fetchone()[0]
