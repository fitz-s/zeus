# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T4 ("alias
#   graph hardening in fill_dedup") — consult adjudication requires the
#   trade_id <-> tx_hash <-> child-id alias graph to be a single, queryable home
#   so a future derive-on-read reducer consumes exactly-once economics
#   (fees included) regardless of the order aggregate/child rows are observed in.
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=never
# Purpose: property tests for src.state.fill_dedup's economic_trade_fact_cte /
#   alias_edge_cte / economic_trade_facts_for_command / alias_edges_for_command.
# Reuse: run when fill_dedup.py or the tx-hash-aggregate-vs-child-trade alias
#   rule changes; also exercised indirectly by tests/test_exchange_reconcile.py
#   (which imports the same CTEs under their exchange_reconcile-private names).
"""Alias-graph exactly-once property tests for src.state.fill_dedup."""
from __future__ import annotations

import hashlib
import itertools
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.state.fill_dedup import (
    alias_edges_for_command,
    economic_trade_facts_for_command,
)
from src.state.venue_command_repo import append_trade_fact

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c
    c.close()


def _seed_bare_command(conn: sqlite3.Connection, command_id: str) -> None:
    """Minimal venue_commands row satisfying venue_trade_facts' FK + NOT NULLs.

    fill_dedup's CTEs only key off (command_id, trade_id, tx_hash) — no
    business-rule validation (insert_command's q_version/enum checks) is
    exercised or needed by these alias-graph property tests, so this bypasses
    insert_command and writes the row directly. Idempotent (INSERT OR IGNORE):
    tests may seed the same command_id across multiple _append calls.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (?, 'snap', 'env', 'pos', 'dec', ?, 'ENTRY', 'mkt', 'tok',
                  'BUY', 10.0, 0.5, 'ord-alias-test', 'FILLED', ?, ?)
        """,
        (command_id, f"idem-{command_id}", NOW.isoformat(), NOW.isoformat()),
    )
    conn.commit()


def _append(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    command_id: str,
    filled_size: str,
    fill_price: str,
    tx_hash: str | None,
    fee_paid_micro: int,
    state: str = "CONFIRMED",
    venue_order_id: str = "ord-alias-test",
) -> None:
    _seed_bare_command(conn, command_id)
    append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state=state,
        filled_size=filled_size,
        fill_price=fill_price,
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(
            f"{trade_id}:{command_id}:{filled_size}:{fill_price}".encode()
        ).hexdigest(),
        fee_paid_micro=fee_paid_micro,
        tx_hash=tx_hash,
    )
    conn.commit()


class TestAggregateChildExactlyOnce:
    """One real fill observed as a tx-hash aggregate AND an exact child row."""

    def _seed_permutation(self, conn, order: tuple[str, ...]) -> None:
        rows = {
            "aggregate": dict(
                trade_id="0xaaa",
                tx_hash="0xaaa",
                filled_size="10",
                fill_price="0.50",
                fee_paid_micro=500,
            ),
            "child": dict(
                trade_id="child-1",
                tx_hash="0xaaa",
                filled_size="10",
                fill_price="0.50",
                fee_paid_micro=500,
            ),
        }
        for key in order:
            _append(conn, command_id="cmd-agg-child", **rows[key])

    @pytest.mark.parametrize(
        "order", list(itertools.permutations(["aggregate", "child"]))
    )
    def test_economic_reducer_counts_exactly_once_regardless_of_insertion_order(
        self, conn, order
    ):
        self._seed_permutation(conn, order)
        economic = economic_trade_facts_for_command(conn, "cmd-agg-child")

        assert len(economic) == 1, (
            f"insertion order {order}: expected exactly one economic row, got "
            f"{len(economic)}: {economic}"
        )
        assert economic[0]["trade_id"] == "child-1"
        total_filled = sum(Decimal(row["filled_size"]) for row in economic)
        total_fee = sum(row["fee_paid_micro"] for row in economic)
        assert total_filled == Decimal("10")
        assert total_fee == 500

    @pytest.mark.parametrize(
        "order", list(itertools.permutations(["aggregate", "child"]))
    )
    def test_alias_edges_tag_aggregate_and_child_correctly(self, conn, order):
        self._seed_permutation(conn, order)
        edges = alias_edges_for_command(conn, "cmd-agg-child")
        by_trade_id = {row["trade_id"]: row for row in edges}

        assert by_trade_id["0xaaa"]["alias_role"] == "ALIASED_AGGREGATE"
        assert by_trade_id["child-1"]["alias_role"] == "CHILD_EXACT"


class TestMultiChildAggregateExactlyOnce:
    """Aggregate row summing TWO partial child fills sharing one tx_hash."""

    def _seed_permutation(self, conn, order: tuple[str, ...]) -> None:
        rows = {
            "aggregate": dict(
                trade_id="0xbbb",
                tx_hash="0xbbb",
                filled_size="15",
                fill_price="0.40",
                fee_paid_micro=750,
            ),
            "child_1": dict(
                trade_id="child-b1",
                tx_hash="0xbbb",
                filled_size="10",
                fill_price="0.40",
                fee_paid_micro=500,
            ),
            "child_2": dict(
                trade_id="child-b2",
                tx_hash="0xbbb",
                filled_size="5",
                fill_price="0.40",
                fee_paid_micro=250,
            ),
        }
        for key in order:
            _append(conn, command_id="cmd-multi-child", **rows[key])

    @pytest.mark.parametrize(
        "order",
        list(itertools.permutations(["aggregate", "child_1", "child_2"])),
    )
    def test_economic_reducer_sums_children_once_excludes_aggregate(
        self, conn, order
    ):
        self._seed_permutation(conn, order)
        economic = economic_trade_facts_for_command(conn, "cmd-multi-child")

        trade_ids = {row["trade_id"] for row in economic}
        assert trade_ids == {"child-b1", "child-b2"}, (
            f"insertion order {order}: expected only the two child rows, got "
            f"{trade_ids}"
        )
        total_filled = sum(Decimal(row["filled_size"]) for row in economic)
        total_fee = sum(row["fee_paid_micro"] for row in economic)
        assert total_filled == Decimal("15")
        assert total_fee == 750


class TestStandaloneAggregateContributesOnce:
    """A tx-hash aggregate with no distinct child stays economic (only obs of the fill)."""

    def test_standalone_aggregate_is_economic_and_tagged_standalone(self, conn):
        _append(
            conn,
            command_id="cmd-standalone",
            trade_id="0xccc",
            tx_hash="0xccc",
            filled_size="7",
            fill_price="0.65",
            fee_paid_micro=100,
        )
        economic = economic_trade_facts_for_command(conn, "cmd-standalone")
        assert len(economic) == 1
        assert economic[0]["trade_id"] == "0xccc"
        assert economic[0]["fee_paid_micro"] == 100

        edges = alias_edges_for_command(conn, "cmd-standalone")
        assert edges[0]["alias_role"] == "STANDALONE"


class TestChildWithNoTxHash:
    """An off-chain-matched fill with no tx_hash at all is CHILD_EXACT, included once."""

    def test_fill_without_tx_hash_is_economic_and_tagged_child_exact(self, conn):
        _append(
            conn,
            command_id="cmd-no-tx",
            trade_id="trade-no-tx-1",
            tx_hash=None,
            filled_size="3",
            fill_price="0.33",
            fee_paid_micro=25,
        )
        economic = economic_trade_facts_for_command(conn, "cmd-no-tx")
        assert len(economic) == 1
        assert economic[0]["trade_id"] == "trade-no-tx-1"

        edges = alias_edges_for_command(conn, "cmd-no-tx")
        assert edges[0]["alias_role"] == "CHILD_EXACT"


class TestCommandsAreIsolated:
    """Alias resolution must not leak across commands sharing a tx_hash by accident."""

    def test_two_commands_with_same_tx_hash_are_scored_independently(self, conn):
        # Same tx_hash text appearing under two different commands (e.g. a batched
        # on-chain settlement tx covering two distinct Zeus orders) must not cause
        # cross-command aliasing — the exclusion rule is scoped to fact.command_id.
        _append(
            conn,
            command_id="cmd-A",
            trade_id="0xshared",
            tx_hash="0xshared",
            filled_size="4",
            fill_price="0.5",
            fee_paid_micro=40,
        )
        _append(
            conn,
            command_id="cmd-B",
            trade_id="child-shared",
            tx_hash="0xshared",
            filled_size="4",
            fill_price="0.5",
            fee_paid_micro=40,
        )

        economic_a = economic_trade_facts_for_command(conn, "cmd-A")
        economic_b = economic_trade_facts_for_command(conn, "cmd-B")

        # Neither is excluded — the "child" living under a different command_id
        # does not alias away cmd-A's aggregate row.
        assert len(economic_a) == 1
        assert economic_a[0]["trade_id"] == "0xshared"
        assert len(economic_b) == 1
        assert economic_b[0]["trade_id"] == "child-shared"
