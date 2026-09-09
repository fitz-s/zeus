# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T4 ("alias
#   graph hardening in fill_dedup") — consult adjudication requires the
#   trade_id <-> tx_hash <-> child-id alias graph to be a single, queryable home
#   so a future derive-on-read reducer consumes exactly-once economics
#   (fees included) regardless of the order aggregate/child rows are observed in.
# Lifecycle: created=2026-07-13; last_reviewed=2026-09-09; last_reused=2026-09-09
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
    canonical_trade_fact_cte,
    economic_trade_facts_for_command,
    economic_trade_fact_cte,
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


def _insert_fact(
    conn: sqlite3.Connection,
    *,
    trade_fact_id: int,
    trade_id: str,
    command_id: str,
    filled_size: str,
    state: str,
    tx_hash: str | None = None,
    local_sequence: int = 1,
    raw_payload_json: str = "{}",
    schema: str = "main",
) -> None:
    table = "venue_trade_facts" if schema == "main" else "trades.venue_trade_facts"
    conn.execute(
        f"""
        INSERT INTO {table} (
            trade_fact_id, trade_id, venue_order_id, command_id, state,
            filled_size, fill_price, fee_paid_micro, tx_hash, source,
            observed_at, local_sequence, raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, 'ord-schema', ?, ?, ?, '0.50', 0, ?, 'REST',
                  ?, ?, ?, ?)
        """,
        (
            trade_fact_id,
            trade_id,
            command_id,
            state,
            filled_size,
            tx_hash,
            NOW.isoformat(),
            local_sequence,
            f"hash-{trade_fact_id}",
            raw_payload_json,
        ),
    )


def _schema_query(conn: sqlite3.Connection, *, source_schema: str | None) -> list[sqlite3.Row]:
    sql = f"""
        WITH {canonical_trade_fact_cte(
            source_schema=source_schema,
            source_clause_sql="WHERE fact.command_id = 'cmd-schema'",
        )}, {economic_trade_fact_cte(source_schema=source_schema)}
        SELECT trade_id, filled_size, state
          FROM economic_trade_fact
         ORDER BY trade_id
    """
    return conn.execute(sql).fetchall()


def test_source_schema_selects_attached_trade_facts_for_both_ctes(conn):
    """The explicit source schema scopes canonical and EDLI source-fact reads."""

    assert "FROM venue_trade_facts fact" in canonical_trade_fact_cte()
    assert "FROM main.venue_trade_facts fact" in canonical_trade_fact_cte(
        source_schema="main"
    )
    assert "FROM trades.venue_trade_facts source_fact" in economic_trade_fact_cte(
        source_schema="trades"
    )
    _seed_bare_command(conn, "cmd-schema")
    _insert_fact(
        conn,
        trade_fact_id=1,
        trade_id="schema-main",
        command_id="cmd-schema",
        filled_size="2",
        state="CONFIRMED",
    )
    conn.execute(
        "ATTACH DATABASE ':memory:' AS trades"
    )
    conn.execute(
        "CREATE TABLE trades.venue_trade_facts AS "
        "SELECT * FROM main.venue_trade_facts WHERE 0"
    )
    _insert_fact(
        conn,
        trade_fact_id=1,
        trade_id="schema-trades",
        command_id="cmd-schema",
        filled_size="7",
        state="CONFIRMED",
        schema="trades",
    )

    # CONFIRMED outranks a later MINED revision for the same trade_id.
    _insert_fact(
        conn,
        trade_fact_id=10,
        trade_id="strength",
        command_id="cmd-schema",
        filled_size="4",
        state="CONFIRMED",
        local_sequence=1,
    )
    _insert_fact(
        conn,
        trade_fact_id=11,
        trade_id="strength",
        command_id="cmd-schema",
        filled_size="99",
        state="MINED",
        local_sequence=9,
    )
    conn.execute(
        "INSERT INTO trades.venue_trade_facts SELECT * FROM main.venue_trade_facts "
        "WHERE trade_id = 'strength'"
    )

    # Aggregate rows are excluded once exact children share the tx hash.
    for fact in (
        (20, "0xalias", "10", "CONFIRMED"),
        (21, "child-alias", "10", "CONFIRMED"),
    ):
        _insert_fact(
            conn,
            trade_fact_id=fact[0],
            trade_id=fact[1],
            command_id="cmd-schema",
            filled_size=fact[2],
            state=fact[3],
            tx_hash="0xalias",
        )
    conn.execute(
        "INSERT INTO trades.venue_trade_facts SELECT * FROM main.venue_trade_facts "
        "WHERE trade_fact_id IN (20, 21)"
    )

    # Same source_trade_fact_id deliberately has opposite source-schema truth:
    # only trades has a positive source, so only trades excludes the EDLI alias.
    _insert_fact(
        conn,
        trade_fact_id=30,
        trade_id="edli-source",
        command_id="cmd-schema",
        filled_size="0",
        state="CONFIRMED",
    )
    _insert_fact(
        conn,
        trade_fact_id=31,
        trade_id="edli:source",
        command_id="cmd-schema",
        filled_size="5",
        state="CONFIRMED",
        raw_payload_json='{"raw_fill_payload":{"source_trade_fact_id":30}}',
    )
    conn.execute(
        "INSERT INTO trades.venue_trade_facts SELECT * FROM main.venue_trade_facts "
        "WHERE trade_fact_id IN (30, 31)"
    )
    conn.execute(
        "UPDATE trades.venue_trade_facts SET filled_size = '5' WHERE trade_fact_id = 30"
    )
    conn.commit()

    main_rows = _schema_query(conn, source_schema=None)
    explicit_main_rows = _schema_query(conn, source_schema="main")
    trades_rows = _schema_query(conn, source_schema="trades")
    main_by_id = {row[0]: (row[1], row[2]) for row in main_rows}
    assert main_by_id["schema-main"] == ("2", "CONFIRMED")
    assert "schema-trades" not in main_by_id
    assert main_by_id["strength"] == ("4", "CONFIRMED")
    assert main_by_id["edli:source"] == ("5", "CONFIRMED")
    assert [tuple(row) for row in explicit_main_rows] == [tuple(row) for row in main_rows]
    trades_by_id = {row[0]: (row[1], row[2]) for row in trades_rows}
    assert trades_by_id["schema-trades"] == ("7", "CONFIRMED")
    assert trades_by_id["strength"] == ("4", "CONFIRMED")
    assert trades_by_id["child-alias"] == ("10", "CONFIRMED")
    assert trades_by_id["edli-source"] == ("5", "CONFIRMED")
    assert "0xalias" not in trades_by_id
    assert "edli:source" not in trades_by_id


@pytest.mark.parametrize("builder", (canonical_trade_fact_cte, economic_trade_fact_cte))
def test_source_schema_rejects_non_whitelisted_identifiers_before_sql(builder):
    with pytest.raises(ValueError, match="unsupported source_schema"):
        builder(source_schema="trades; DROP TABLE venue_trade_facts")


def _seed_exit_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    position_id: str,
    venue_order_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (?, 'snap', 'env', ?, 'dec', ?, 'EXIT', 'mkt', 'tok',
                  'SELL', 48.22, 0.18, ?, 'EXPIRED', ?, ?)
        """,
        (
            command_id,
            position_id,
            f"idem-{command_id}",
            venue_order_id,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )


def test_terminal_hkg_exit_scope_keeps_unrelated_history_out_of_deadline(
    conn,
):
    """A bounded terminal EXIT lookup must survive a tight VM-opcode budget."""

    from src.state.fill_dedup import economic_exit_fills_for_position

    target_position = "32d6009c-007"
    target_command = "b3ac5741cdd54511"
    target_order = "ord-hkg-terminal"
    _seed_exit_command(
        conn,
        command_id=target_command,
        position_id=target_position,
        venue_order_id=target_order,
    )

    # Populate enough unrelated history that an unscoped canonical window would
    # hit this deadline, while the target command remains only two revisions.
    for index in range(512):
        command_id = f"unrelated-command-{index}"
        order_id = f"unrelated-order-{index}"
        _seed_exit_command(
            conn,
            command_id=command_id,
            position_id="unrelated-position",
            venue_order_id=order_id,
        )
        _append(
            conn,
            trade_id=f"unrelated-trade-{index}",
            command_id=command_id,
            filled_size="1",
            fill_price="0.20",
            tx_hash=f"0x-unrelated-{index}",
            fee_paid_micro=1,
            venue_order_id=order_id,
        )
    for state in ("MATCHED", "CONFIRMED"):
        append_trade_fact(
            conn,
            trade_id="hkg-trade",
            venue_order_id=target_order,
            command_id=target_command,
            state=state,
            filled_size="5",
            fill_price="0.18",
            source="REST",
            observed_at=NOW,
            raw_payload_hash=hashlib.sha256(state.encode()).hexdigest(),
            tx_hash="0xhkg-terminal",
        )
    conn.commit()

    class _RecordingConnection:
        def __init__(self, delegate: sqlite3.Connection) -> None:
            self.delegate = delegate
            self.lookup_sql = ""
            self.lookup_params: tuple[object, ...] = ()

        def execute(self, sql: str, params=()):
            if "canonical_trade_fact AS" in sql:
                self.lookup_sql = sql
                self.lookup_params = tuple(params)
            return self.delegate.execute(sql, params)

    traced = _RecordingConnection(conn)
    progress_calls = 0

    def _deadline_progress() -> int:
        nonlocal progress_calls
        progress_calls += 1
        return int(progress_calls > 20)

    conn.set_progress_handler(_deadline_progress, 100)
    try:
        fills = economic_exit_fills_for_position(
            traced,
            target_position,
            venue_order_id=target_order,
        )
    finally:
        conn.set_progress_handler(None, 0)

    assert [(fill.command_id, fill.trade_id, fill.quantity, fill.unit_price) for fill in fills] == [
        (target_command, "hkg-trade", Decimal("5"), Decimal("0.18"))
    ]
    assert progress_calls <= 20

    plan = conn.execute(
        "EXPLAIN QUERY PLAN " + traced.lookup_sql,
        traced.lookup_params,
    ).fetchall()
    details = [str(row[3]) for row in plan]
    assert details.count(
        "SEARCH fact USING INDEX idx_trade_facts_command (command_id=?)"
    ) >= 2
    assert not any(
        detail.startswith("SCAN fact USING INDEX idx_trade_facts_command")
        for detail in details
    )


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


def test_edli_source_pointer_alias_is_not_a_second_economic_fill(conn):
    from src.state.venue_command_repo import append_trade_fact

    _seed_bare_command(conn, "cmd-edli-alias")
    source_fact_id = append_trade_fact(
        conn,
        trade_id="venue-trade-1",
        venue_order_id="ord-alias-test",
        command_id="cmd-edli-alias",
        state="CONFIRMED",
        filled_size="31.6",
        fill_price="0.6",
        source="WS_USER",
        observed_at=NOW,
        venue_timestamp=NOW,
        raw_payload_hash="1" * 64,
        raw_payload_json={"status": "CONFIRMED"},
    )
    append_trade_fact(
        conn,
        trade_id="edli:venue-trade-1",
        venue_order_id="ord-alias-test",
        command_id="cmd-edli-alias",
        state="CONFIRMED",
        filled_size="31.6",
        fill_price="0.6",
        source="WS_USER",
        observed_at=NOW,
        raw_payload_hash="2" * 64,
        raw_payload_json={
            "source_module": "src.events.edli_position_bridge",
            "raw_fill_payload": {"source_trade_fact_id": source_fact_id},
        },
    )
    append_trade_fact(
        conn,
        trade_id="venue-trade-1",
        venue_order_id="ord-alias-test",
        command_id="cmd-edli-alias",
        state="CONFIRMED",
        filled_size="31.6",
        fill_price="0.6",
        source="REST",
        observed_at=NOW,
        raw_payload_hash="5" * 64,
        raw_payload_json={"status": "CONFIRMED", "source": "later_rest"},
    )

    economic = economic_trade_facts_for_command(conn, "cmd-edli-alias")

    assert [row["trade_id"] for row in economic] == ["venue-trade-1"]
    assert sum(Decimal(row["filled_size"]) for row in economic) == Decimal("31.6")
