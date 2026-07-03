# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Purpose: CI gate closing the fingerprint blind-spot around _TRADE_CLASS_DDL
#          (SCH-W1.2-ORDER-STATE critic ruling 1). architecture/_schema_fingerprint.txt
#          only hashes init_schema (world) + init_schema_forecasts; the trade-DB literal
#          DDL copies in db.py's _TRADE_CLASS_DDL can silently drift from their
#          world/module-schema counterparts with zero CI coverage otherwise.
# Reuse: Run when editing venue_order_facts DDL (either copy) or the collateral
#        DDL (either copy). Guards W1.1's collateral edits too — the whole
#        _TRADE_CLASS_DDL block is fingerprint-blind.
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
"""Normalized (not byte-equal) DDL lockstep guard for the two fingerprint-blind pairs.

Critic ruling 1: a byte-equality assertion false-positives because the two copies
of each table differ by indentation (one is nested inside init_schema's big
executescript block at Python-source indentation; the other lives in the
module-level _TRADE_CLASS_DDL string at column 0). Comparison must be over
PARSED/NORMALIZED structure: PRAGMA table_info, PRAGMA index_list, and the
CREATE TABLE text with comments stripped and whitespace collapsed (which
captures CHECK expressions, since sqlite's table_info does not surface them).

This test compares the two CURRENT copies against each other (self-consistent),
not against a frozen snapshot, so lockstep edits (e.g. W1.1 extending the
collateral DDL identically in both places) pass and non-lockstep drift fails.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from src.state.db import init_schema, init_schema_trade_only


def _normalize_sql(sql: str) -> str:
    """Strip `--` line comments and collapse all whitespace to single spaces."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"\s+", " ", sql).strip()
    return sql


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    assert row is not None and row[0], f"table {table!r} not found in schema"
    return row[0]


def _table_info(conn: sqlite3.Connection, table: str) -> list:
    # (cid, name, type, notnull, dflt_value, pk) — canonical column structure.
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _index_list(conn: sqlite3.Connection, table: str) -> list:
    indexes = []
    for idx in conn.execute(f"PRAGMA index_list({table})").fetchall():
        idx_name = idx[1]
        columns = conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
        indexes.append((idx_name, idx[2], idx[3], tuple(columns)))
    return sorted(indexes)


@pytest.fixture(scope="module")
def world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


@pytest.fixture(scope="module")
def trade_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema_trade_only(conn)
    return conn


def _assert_ddl_copies_normalized_identical(
    world_conn: sqlite3.Connection,
    trade_conn: sqlite3.Connection,
    table: str,
) -> None:
    world_info = _table_info(world_conn, table)
    trade_info = _table_info(trade_conn, table)
    assert world_info == trade_info, (
        f"{table}: column structure (PRAGMA table_info) differs between the "
        f"world/module copy and the trade copy.\n  world: {world_info}\n  trade: {trade_info}"
    )

    world_indexes = _index_list(world_conn, table)
    trade_indexes = _index_list(trade_conn, table)
    assert world_indexes == trade_indexes, (
        f"{table}: index list differs between the world/module copy and the "
        f"trade copy.\n  world: {world_indexes}\n  trade: {trade_indexes}"
    )

    world_sql = _normalize_sql(_table_sql(world_conn, table))
    trade_sql = _normalize_sql(_table_sql(trade_conn, table))
    assert world_sql == trade_sql, (
        f"{table}: normalized CREATE TABLE text differs (this is where CHECK "
        f"expression drift would show up — table_info does not surface CHECK "
        f"clauses).\n  world: {world_sql}\n  trade: {trade_sql}"
    )


class TestVenueOrderFactsDDLLockstep:
    """Pair 1 (packet): venue_order_facts world-ghost copy (db.py init_schema,
    fingerprint-covered) vs trade-authoritative copy (_TRADE_CLASS_DDL,
    fingerprint-blind)."""

    def test_venue_order_facts_ddl_copies_normalized_identical(self, world_conn, trade_conn):
        _assert_ddl_copies_normalized_identical(world_conn, trade_conn, "venue_order_facts")


class TestCollateralDDLLockstep:
    """Pair 2 (packet): collateral literal DDL in _TRADE_CLASS_DDL (db.py) vs the
    module schema (collateral_ledger.COLLATERAL_LEDGER_SCHEMA, applied to the
    world DB via init_collateral_schema inside init_schema). Guards W1.1's
    collateral edits too — both pairs must move together."""

    def test_collateral_ledger_snapshots_ddl_copies_normalized_identical(
        self, world_conn, trade_conn
    ):
        _assert_ddl_copies_normalized_identical(
            world_conn, trade_conn, "collateral_ledger_snapshots"
        )

    def test_collateral_reservations_ddl_copies_normalized_identical(
        self, world_conn, trade_conn
    ):
        _assert_ddl_copies_normalized_identical(
            world_conn, trade_conn, "collateral_reservations"
        )


# venue_commands has the same world/trade literal-copy split and this packet
# adds q_version to both copies identically, but the pair carries PRE-EXISTING
# index drift unrelated to this packet (world has idx_venue_commands_envelope /
# idx_venue_commands_snapshot that the trade copy lacks). A full-structure
# lockstep guard on this pair is therefore out of scope here: the packet only
# mandates the venue_order_facts and collateral pairs above, and widening the
# guard to a pair with pre-existing drift would fail on landing for reasons
# unrelated to this packet. Recorded as a deferred hygiene item, not fixed.
