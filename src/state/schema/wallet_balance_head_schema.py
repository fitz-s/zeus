# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T2 verdict
#   ("WRONG-as-stated -> reconstruct: 101k-row snapshot history dies, but its
#   latest-state role does not -- replace with a sync-owned single row per
#   (wallet, asset)"); LX-0R KEEP-spine completeness rider (sync-owned collateral
#   head); attack C ("snapshots stop first -> Kelly reads stale balance").

"""Schema owner for wallet_balance_head (trade DB).

ONE current row per (wallet, asset): the sync-owned latest-known balance/
allowance head that ``collateral_snapshot_refresh_cycle`` (30s cadence,
src/execution/post_trade_capital.py) dual-writes alongside the existing
``collateral_ledger_snapshots`` append-only history. This packet (LX-T2-a)
only stands the head up as a second write target of the SAME refresh —
no reader is cut over yet (that is LX-3R's single fenced activation) and
the snapshot history table is not touched (it retires at LX-5R).

Single-writer law: this table is written ONLY by the post-trade-capital
collateral refresh path. Nothing else may INSERT/UPDATE it — a business
workflow that "corrects" a balance here would recreate the exact parallel-
ledger disease this excision removes elsewhere.

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

TABLE_NAME = "wallet_balance_head"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wallet_balance_head (
    wallet              TEXT NOT NULL,
    asset               TEXT NOT NULL,
    balance_micro       INTEGER NOT NULL,
    allowance_micro     INTEGER NOT NULL,
    source              TEXT NOT NULL CHECK (source IN ('CLOB', 'CHAIN')),
    authority_tier      TEXT NOT NULL CHECK (authority_tier IN ('CHAIN', 'VENUE', 'DEGRADED')),
    block_or_source_ts  TEXT NOT NULL,
    observed_at         TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (wallet, asset)
)
"""

CREATE_UPDATED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_wallet_balance_head_updated_at
    ON wallet_balance_head(updated_at)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for wallet_balance_head.

    INV-37: caller supplies conn; never auto-opens.
    """

    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_UPDATED_INDEX_SQL)


__all__ = ["TABLE_NAME", "CREATE_TABLE_SQL", "ensure_table"]
