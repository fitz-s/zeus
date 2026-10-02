# Created: 2026-10-01
# Last audited: 2026-10-01
# Authority basis: adverse-selection study capture request 2026-10-01 -- every
#   top-of-book transition of every subscribed token; capture only, no gate.
"""TRADE-owned top-of-book transition log and its token dictionary.

``market_tob_transitions`` holds one append-only row per change of a token's
best bid/ask price or size. It is clustered on ``(observed_ms, token_ref, seq)``
so appends land at the end of a single B-tree (no rowid, no secondary index) and
time-bound retention is a head range delete. ``token_ref`` names a row of
``market_tob_tokens`` so the 77-digit token id is stored once, not per row.

Fixed point: prices are integer 1e-4 units (0.4100 -> 4100) and sizes integer
1e-2 shares. A NULL price is an empty side of the book (its size is then NULL).
``seq`` orders transitions of one token inside one ``observed_ms``; the venue
market channel carries no source sequence, so a row proves a transition was
seen, not that none was missed.
"""

from __future__ import annotations

import sqlite3

TOKENS_TABLE = "market_tob_tokens"
TRANSITIONS_TABLE = "market_tob_transitions"

CREATE_TOKENS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TOKENS_TABLE} (
    token_ref INTEGER PRIMARY KEY,
    token_id TEXT NOT NULL UNIQUE
)
"""

CREATE_TRANSITIONS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TRANSITIONS_TABLE} (
    observed_ms INTEGER NOT NULL,
    token_ref INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    best_bid INTEGER,
    best_bid_size INTEGER,
    best_ask INTEGER,
    best_ask_size INTEGER,
    PRIMARY KEY (observed_ms, token_ref, seq)
) WITHOUT ROWID
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TOKENS_SQL)
    conn.execute(CREATE_TRANSITIONS_SQL)
