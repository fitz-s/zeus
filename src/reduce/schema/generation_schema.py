# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-a "Read-model 诚实不变量" -- "portfolio 发布按完整 generation,不按
#   per-row latest". This schema owner backs src/reduce/generation.py's
#   table-backed GenerationStore.
"""Schema owner for the src.reduce generation-store tables.

NOT wired into src.state.db's init paths (INV-37 scope boundary for this
packet -- see src/reduce/generation.py module docstring). ``ensure_tables``
is idempotent DDL (``CREATE TABLE IF NOT EXISTS``), callable against any
trade-DB-shaped sqlite conn: a fixture conn in tests today, or a live
trade-DB conn once a future LX-2R/LX-3R activation-control packet wires this
store into production (out of this packet's synthetic-fixture-only scope).

TABLE SHAPE
-----------
``reduce_generations``: one row per published generation (the read-model
honesty invariant's publication unit -- "complete generation, never
per-row latest"). ``reduce_position_economics``: one row per (generation_id,
position_id), written ONLY inside the same transaction as its parent
generation row (see ``GenerationStore.publish`` -- all-or-nothing).
"""
from __future__ import annotations

import sqlite3

CREATE_GENERATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS reduce_generations (
    generation_id       TEXT PRIMARY KEY,
    reducer_version     TEXT NOT NULL,
    computed_at         TEXT NOT NULL,
    input_fingerprint   TEXT NOT NULL,
    coverage_json       TEXT NOT NULL,
    position_ids_json   TEXT NOT NULL
)
"""

CREATE_POSITION_ECONOMICS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS reduce_position_economics (
    generation_id               TEXT NOT NULL REFERENCES reduce_generations(generation_id),
    position_id                 TEXT NOT NULL,
    keeper_position_id          TEXT NOT NULL,
    absorbed_position_ids_json  TEXT NOT NULL,
    net_shares                  REAL NOT NULL,
    cost_basis_usd               REAL NOT NULL,
    realized_pnl_usd             REAL NOT NULL,
    fees_usd                     REAL NOT NULL,
    fill_count                   INTEGER NOT NULL,
    payout_status                TEXT NOT NULL CHECK (payout_status IN (
        'CLOSED_VIA_FILLS', 'PENDING', 'RESOLVED_ZERO', 'RESOLVED_NONZERO'
    )),
    payout_pnl_usd                REAL,
    PRIMARY KEY (generation_id, position_id)
)
"""

CREATE_POSITION_ECONOMICS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_reduce_position_economics_position
    ON reduce_position_economics(position_id)
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create the generation-store tables (idempotent).

    INV-37: caller supplies conn; never auto-opens.
    """
    conn.execute(CREATE_GENERATIONS_TABLE_SQL)
    conn.execute(CREATE_POSITION_ECONOMICS_TABLE_SQL)
    conn.execute(CREATE_POSITION_ECONOMICS_INDEX_SQL)


__all__ = [
    "CREATE_GENERATIONS_TABLE_SQL",
    "CREATE_POSITION_ECONOMICS_TABLE_SQL",
    "ensure_tables",
]
