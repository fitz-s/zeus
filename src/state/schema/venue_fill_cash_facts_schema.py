# Created: 2026-09-09
# Last reused or audited: 2026-09-09
# Authority basis: hourly capital gains improvement loop — append-only cash fact companion.
"""Trade DB schema for immutable on-chain fill cash evidence.

The grain is one evidence observation per ``(chain_id, tx_hash, wallet,
proof_hash)``.  A later proof is a new row: existing evidence is never updated
or deleted, and callers retain ownership of the transaction boundary.
"""
from __future__ import annotations

import sqlite3


TABLE_NAME = "venue_fill_cash_facts"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS venue_fill_cash_facts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id         INTEGER NOT NULL,
    tx_hash          TEXT NOT NULL,
    wallet           TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('PROVEN', 'UNKNOWN')),
    reason           TEXT NOT NULL,
    block_number     INTEGER,
    block_hash       TEXT,
    finalized_number INTEGER,
    finalized_hash   TEXT,
    observed_at      TEXT NOT NULL,
    proof_hash       TEXT NOT NULL,
    proof_json       TEXT NOT NULL,
    UNIQUE (chain_id, tx_hash, wallet, proof_hash),
    CHECK (
        status = 'UNKNOWN'
        OR (
            typeof(block_number) = 'integer'
            AND typeof(finalized_number) = 'integer'
            AND block_number >= 0
            AND finalized_number >= block_number
            AND block_hash IS NOT NULL AND length(block_hash) = 66
            AND finalized_hash IS NOT NULL AND length(finalized_hash) = 66
        )
    )
)
"""

CREATE_INDEX_CHAIN_TX_WALLET_TIME_SQL = """
CREATE INDEX IF NOT EXISTS idx_venue_fill_cash_facts_chain_tx_wallet_time
    ON venue_fill_cash_facts(chain_id, tx_hash, wallet, observed_at, id)
"""

CREATE_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS venue_fill_cash_facts_no_update
BEFORE UPDATE ON venue_fill_cash_facts
BEGIN
    SELECT RAISE(ABORT, 'venue_fill_cash_facts rows are append-only');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS venue_fill_cash_facts_no_delete
BEFORE DELETE ON venue_fill_cash_facts
BEGIN
    SELECT RAISE(ABORT, 'venue_fill_cash_facts rows are append-only');
END
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create the companion table, index and immutability triggers on ``conn``.

    The caller owns the connection and transaction.  This function never opens
    another handle and never commits.
    """

    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_CHAIN_TX_WALLET_TIME_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


__all__ = [
    "TABLE_NAME",
    "CREATE_TABLE_SQL",
    "CREATE_INDEX_CHAIN_TX_WALLET_TIME_SQL",
    "CREATE_NO_UPDATE_TRIGGER_SQL",
    "CREATE_NO_DELETE_TRIGGER_SQL",
    "ensure_table",
]
