# Created: 2026-07-08
# Last reused or audited: 2026-07-08
"""Shared fixtures for src/reconcile tests. Same pattern as
tests/test_reconcile_chain_mirror.py's trades_conn/forecasts_conn (real
schema, in-memory DB, no network I/O).
"""
from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema, init_schema_trade_only


@pytest.fixture
def trades_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    yield conn
    conn.close()


@pytest.fixture
def forecasts_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            winning_bin TEXT, authority TEXT, settlement_value REAL,
            settlement_source TEXT, market_slug TEXT
        )
        """
    )
    yield conn
    conn.close()


def insert_position_current(conn: sqlite3.Connection, *, position_id: str, **overrides) -> None:
    defaults = dict(
        phase="active",
        chain_state="synced",
        city="manila",
        target_date="2026-07-04",
        bin_label="33°C",
        direction="buy_yes",
        unit="C",
        size_usd=10.0,
        shares=10.0,
        cost_basis_usd=10.0,
        entry_price=1.0,
        p_posterior=0.5,
        decision_snapshot_id="snap-1",
        entry_method="center_buy",
        strategy_key="edli",
        edge_source="center_buy",
        discovery_mode="opening_hunt",
        token_id="tok-yes",
        no_token_id="tok-no",
        condition_id="cond-1",
        order_id=None,
        order_status="filled",
        updated_at="2026-07-04T00:00:00+00:00",
        temperature_metric="high",
        chain_shares=None,
        realized_pnl_usd=None,
        exit_price=None,
        exit_reason=None,
        settled_at=None,
    )
    defaults.update(overrides)
    columns = ["position_id", *defaults.keys()]
    values = [position_id, *defaults.values()]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO position_current ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def insert_venue_command(conn: sqlite3.Connection, *, command_id: str, position_id: str, **overrides) -> None:
    defaults = dict(
        snapshot_id="snap-1",
        envelope_id="env-1",
        decision_id="dec-1",
        idempotency_key=f"idem-{command_id}",
        intent_kind="ENTRY",
        market_id="market-1",
        token_id="tok-yes",
        side="BUY",
        size=10.0,
        price=1.0,
        venue_order_id=f"vo-{command_id}",
        state="FILLED",
        created_at="2026-07-04T00:00:00+00:00",
        updated_at="2026-07-04T00:00:05+00:00",
    )
    defaults.update(overrides)
    columns = ["command_id", "position_id", *defaults.keys()]
    values = [command_id, position_id, *defaults.values()]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO venue_commands ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def insert_reservation(conn: sqlite3.Connection, *, command_id: str, **overrides) -> None:
    defaults = dict(
        reservation_type="PUSD_BUY",
        token_id=None,
        amount=10_000_000,
        created_at="2026-07-04T00:00:00+00:00",
        released_at=None,
        release_reason=None,
        converted_amount=0,
    )
    defaults.update(overrides)
    columns = ["command_id", *defaults.keys()]
    values = [command_id, *defaults.values()]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO collateral_reservations ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def insert_trade_fact(
    conn: sqlite3.Connection,
    *,
    trade_fact_id: int,
    trade_id: str,
    venue_order_id: str,
    command_id: str,
    state: str = "CONFIRMED",
    filled_size: str = "10",
    fill_price: str = "0.5",
    source: str = "WS_USER",
    observed_at: str = "2026-07-04T00:00:10+00:00",
    local_sequence: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_fact_id, trade_id, venue_order_id, command_id, state,
            filled_size, fill_price, source, observed_at, local_sequence,
            raw_payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_fact_id, trade_id, venue_order_id, command_id, state,
            filled_size, fill_price, source, observed_at, local_sequence,
            f"hash-{trade_fact_id}",
        ),
    )


def insert_order_fact(
    conn: sqlite3.Connection,
    *,
    fact_id: int,
    venue_order_id: str,
    command_id: str,
    state: str,
    source: str = "WS_USER",
    observed_at: str = "2026-07-04T00:00:10+00:00",
    local_sequence: int = 1,
    remaining_size: str | None = None,
    matched_size: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            fact_id, venue_order_id, command_id, state, remaining_size,
            matched_size, source, observed_at, local_sequence, raw_payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact_id, venue_order_id, command_id, state, remaining_size,
            matched_size, source, observed_at, local_sequence, f"hash-{fact_id}",
        ),
    )
