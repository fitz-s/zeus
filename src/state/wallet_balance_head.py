# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T2 verdict;
#   src/state/schema/wallet_balance_head_schema.py

"""Writer/reader for wallet_balance_head — the sync-owned current-balance head.

Foundation machinery for LX-T2-a: this module provides the single upsert
primitive the 30s collateral refresh cycle calls, plus a read helper for
tests and future LX-3R readers. It is NOT wired into bankroll_provider /
riskguard / Kelly by this packet — cutover of readers is LX-3R.

Single-writer law (docs/rebuild/local_ledger_excision_2026-07-12.md
Attack C): only ``src.execution.post_trade_capital.collateral_snapshot_refresh_cycle``
calls ``upsert_wallet_balance_head``. Nothing else may write this table.

INV-37: every function requires a caller-supplied conn; nothing here
auto-opens or ATTACHes a connection. Schema must already exist on ``conn``
(call src.state.schema.wallet_balance_head_schema.ensure_table first — boot
wiring lives in src.state.db.init_schema_trade_only for the trade DB
instance).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

_SOURCES = frozenset({"CLOB", "CHAIN"})
_AUTHORITY_TIERS = frozenset({"CHAIN", "VENUE", "DEGRADED"})


@dataclass(frozen=True)
class WalletBalanceHead:
    """Read-only view of one wallet_balance_head row."""

    wallet: str
    asset: str
    balance_micro: int
    allowance_micro: int
    source: str
    authority_tier: str
    block_or_source_ts: str
    observed_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_wallet_balance_head(
    conn: sqlite3.Connection,
    *,
    wallet: str,
    asset: str,
    balance_micro: int,
    allowance_micro: int,
    source: str,
    authority_tier: str,
    block_or_source_ts: str,
    observed_at: Optional[str] = None,
) -> None:
    """Upsert the ONE current row for (wallet, asset).

    Idempotent single-row-per-key semantics via ``INSERT ... ON CONFLICT``:
    a second call for the same (wallet, asset) overwrites the row in place —
    this table is a HEAD (latest known state), not an append log. The
    append-only history stays on ``collateral_ledger_snapshots`` until it
    retires at LX-5R.
    """

    wallet = str(wallet or "").strip()
    asset = str(asset or "").strip()
    if not wallet:
        raise ValueError("wallet_balance_head_missing_wallet")
    if not asset:
        raise ValueError("wallet_balance_head_missing_asset")
    if source not in _SOURCES:
        raise ValueError(f"wallet_balance_head_invalid_source:{source!r}")
    if authority_tier not in _AUTHORITY_TIERS:
        raise ValueError(f"wallet_balance_head_invalid_authority_tier:{authority_tier!r}")

    now = observed_at or _now_iso()
    conn.execute(
        """
        INSERT INTO wallet_balance_head (
            wallet, asset, balance_micro, allowance_micro, source,
            authority_tier, block_or_source_ts, observed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(wallet, asset) DO UPDATE SET
            balance_micro = excluded.balance_micro,
            allowance_micro = excluded.allowance_micro,
            source = excluded.source,
            authority_tier = excluded.authority_tier,
            block_or_source_ts = excluded.block_or_source_ts,
            observed_at = excluded.observed_at,
            updated_at = excluded.updated_at
        """,
        (
            wallet,
            asset,
            int(balance_micro),
            int(allowance_micro),
            source,
            authority_tier,
            str(block_or_source_ts),
            now,
            now,
        ),
    )


def read_wallet_balance_head(
    conn: sqlite3.Connection, *, wallet: str, asset: str
) -> Optional[WalletBalanceHead]:
    """Return the current head row for (wallet, asset), or None if absent."""

    row = conn.execute(
        """
        SELECT wallet, asset, balance_micro, allowance_micro, source,
               authority_tier, block_or_source_ts, observed_at, updated_at
        FROM wallet_balance_head
        WHERE wallet = ? AND asset = ?
        """,
        (str(wallet or "").strip(), str(asset or "").strip()),
    ).fetchone()
    if row is None:
        return None
    return WalletBalanceHead(*row)


__all__ = [
    "WalletBalanceHead",
    "upsert_wallet_balance_head",
    "read_wallet_balance_head",
]
