# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md 排序攻击 Attack F
#   ("/positions 漏 token = 幻零仓 -> durable token registry from Zeus
#   commands/fills/topology/transfer obs, /positions only does discovery,
#   absence NEVER proves zero"); KEEP-spine completeness rider (token-discovery
#   history: five sources -- intent/topology/attributed fills/transfer logs/
#   direct balance obs).

"""Schema owner for ctf_token_registry (trade DB).

Durable record of every CTF outcome token Zeus has ever discovered, keyed by
token_id, so the wallet-aggregate data-api ``/positions`` (the only source
that ENUMERATES which tokens exist) can never silently delete a token Zeus
still holds just because one read omitted it.

LAW (Attack F): absence from a later ``/positions`` read never proves the
token is gone — rows in this table are NEVER deleted. A row's continued
presence is what lets a later CTF ``balanceOf`` read be targeted even after
``/positions`` stops mentioning a token (redemption, illiquidity, API lag).

first_source is intentionally NOT the CURRENT authority for a token's
existence — it is provenance only, immutable after first insert.
"""

from __future__ import annotations

import sqlite3

TABLE_NAME = "ctf_token_registry"

# The five KEEP-spine token-discovery sources (docs/rebuild/local_ledger_excision_2026-07-12.md
# KEEP-spine completeness rider). ``transfer_observation`` has no live ingester
# yet (LX-1R payout/transfer observer territory) — the CHECK constraint
# reserves it so a later packet's writes are not a schema migration.
FIRST_SOURCES = frozenset(
    {
        "zeus_command",
        "market_topology",
        "attributed_fill",
        "transfer_observation",
        "positions_api_discovery",
    }
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ctf_token_registry (
    token_id          TEXT PRIMARY KEY,
    condition_id      TEXT NOT NULL,
    first_source      TEXT NOT NULL CHECK (first_source IN (
        'zeus_command', 'market_topology', 'attributed_fill',
        'transfer_observation', 'positions_api_discovery'
    )),
    first_seen_at     TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL
)
"""

CREATE_CONDITION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_ctf_token_registry_condition
    ON ctf_token_registry(condition_id)
"""

# Defense-in-depth mirror of the module-docstring law: application code never
# deletes a row, but a trigger makes an accidental DELETE fail loudly instead
# of silently manufacturing the exact "absence proves zero" bug this table
# exists to prevent.
CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS no_delete_ctf_token_registry
BEFORE DELETE ON ctf_token_registry
BEGIN SELECT RAISE(ABORT, 'ctf_token_registry rows are never deleted (absence never proves zero)'); END
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for ctf_token_registry.

    INV-37: caller supplies conn; never auto-opens.
    """

    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_CONDITION_INDEX_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


__all__ = [
    "TABLE_NAME",
    "FIRST_SOURCES",
    "CREATE_TABLE_SQL",
    "ensure_table",
]
