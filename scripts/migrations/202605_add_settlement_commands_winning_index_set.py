# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: Add winning_index_set column to settlement_commands.
#   winning_index_set is a JSON-encoded uint256[] (as string array) that
#   encodes the CTF outcome bin to redeem via redeemPositions(indexSets).
#   For binary markets: '["2"]' = YES outcome won, '["1"]' = NO outcome won.
#   Uses simple ALTER TABLE ... ADD COLUMN (additive, NULL for existing rows).
# Reuse: Run BEFORE deploying PR-I.5.a code. Existing rows remain NULL until
#   manual SQL UPDATE (out of scope for this PR). Safe to run with daemon up
#   because ALTER TABLE ADD COLUMN is non-blocking in SQLite.
# Authority basis: PR-I.5.a / autonomous redeem prep (PR_I5_WEB3_WIRE.md)
# Limitation: V1 assumes binary markets only. Multi-bin indexSet encoding
#   is documented in PR_I5_WEB3_WIRE.md §3 but not implemented here.
from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Add winning_index_set column and sparse index to settlement_commands.

    Idempotent: checks PRAGMA table_info before altering.
    JSON-encoded array of uint256 strings, e.g. '["2"]' for YES single-bin
    redeem. NULL is valid for rows enqueued before this migration.
    """
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(settlement_commands)")}
    if "winning_index_set" not in existing_cols:
        conn.execute(
            "ALTER TABLE settlement_commands ADD COLUMN winning_index_set TEXT"
        )
    # Sparse index: only rows where winning_index_set is populated need fast lookup.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_settlement_commands_winning_index_set "
        "ON settlement_commands(winning_index_set) "
        "WHERE winning_index_set IS NOT NULL"
    )
