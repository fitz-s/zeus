# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T2 verdict;
#   src/state/schema/wallet_balance_head_schema.py; src/state/wallet_balance_head.py
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=never
# Purpose: single-row-per-(wallet,asset) upsert semantics for wallet_balance_head.

"""Tests for wallet_balance_head schema + writer/reader."""

from __future__ import annotations

import sqlite3

import pytest

from src.state.schema.wallet_balance_head_schema import ensure_table
from src.state.wallet_balance_head import (
    WalletBalanceHead,
    read_wallet_balance_head,
    upsert_wallet_balance_head,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    return conn


def test_upsert_creates_one_row():
    conn = _make_conn()
    upsert_wallet_balance_head(
        conn,
        wallet="0xWALLET",
        asset="PUSD",
        balance_micro=1_000_000,
        allowance_micro=2_000_000,
        source="CLOB",
        authority_tier="CHAIN",
        block_or_source_ts="2026-07-13T00:00:00+00:00",
    )
    count = conn.execute("SELECT COUNT(*) FROM wallet_balance_head").fetchone()[0]
    assert count == 1
    row = read_wallet_balance_head(conn, wallet="0xWALLET", asset="PUSD")
    assert row == WalletBalanceHead(
        wallet="0xWALLET",
        asset="PUSD",
        balance_micro=1_000_000,
        allowance_micro=2_000_000,
        source="CLOB",
        authority_tier="CHAIN",
        block_or_source_ts="2026-07-13T00:00:00+00:00",
        observed_at=row.observed_at,
        updated_at=row.updated_at,
    )


def test_second_upsert_for_same_key_overwrites_in_place_not_appends():
    """ONE current row per (wallet, asset) -- a refresh cycle is a HEAD write, not a log."""
    conn = _make_conn()
    upsert_wallet_balance_head(
        conn,
        wallet="0xWALLET",
        asset="PUSD",
        balance_micro=1_000_000,
        allowance_micro=1_000_000,
        source="CLOB",
        authority_tier="CHAIN",
        block_or_source_ts="2026-07-13T00:00:00+00:00",
        observed_at="2026-07-13T00:00:00+00:00",
    )
    upsert_wallet_balance_head(
        conn,
        wallet="0xWALLET",
        asset="PUSD",
        balance_micro=2_500_000,
        allowance_micro=2_500_000,
        source="CLOB",
        authority_tier="VENUE",
        block_or_source_ts="2026-07-13T00:00:30+00:00",
        observed_at="2026-07-13T00:00:30+00:00",
    )
    count = conn.execute("SELECT COUNT(*) FROM wallet_balance_head").fetchone()[0]
    assert count == 1
    row = read_wallet_balance_head(conn, wallet="0xWALLET", asset="PUSD")
    assert row.balance_micro == 2_500_000
    assert row.allowance_micro == 2_500_000
    assert row.authority_tier == "VENUE"
    assert row.updated_at == "2026-07-13T00:00:30+00:00"


def test_distinct_assets_for_same_wallet_are_distinct_rows():
    conn = _make_conn()
    upsert_wallet_balance_head(
        conn, wallet="0xWALLET", asset="PUSD", balance_micro=1, allowance_micro=1,
        source="CLOB", authority_tier="CHAIN", block_or_source_ts="t0",
    )
    upsert_wallet_balance_head(
        conn, wallet="0xWALLET", asset="TOKEN_ABC", balance_micro=2, allowance_micro=2,
        source="CHAIN", authority_tier="CHAIN", block_or_source_ts="t0",
    )
    count = conn.execute("SELECT COUNT(*) FROM wallet_balance_head").fetchone()[0]
    assert count == 2


def test_distinct_wallets_for_same_asset_are_distinct_rows():
    conn = _make_conn()
    upsert_wallet_balance_head(
        conn, wallet="0xAAA", asset="PUSD", balance_micro=1, allowance_micro=1,
        source="CLOB", authority_tier="CHAIN", block_or_source_ts="t0",
    )
    upsert_wallet_balance_head(
        conn, wallet="0xBBB", asset="PUSD", balance_micro=2, allowance_micro=2,
        source="CLOB", authority_tier="CHAIN", block_or_source_ts="t0",
    )
    count = conn.execute("SELECT COUNT(*) FROM wallet_balance_head").fetchone()[0]
    assert count == 2


def test_read_absent_key_returns_none():
    conn = _make_conn()
    assert read_wallet_balance_head(conn, wallet="0xNOPE", asset="PUSD") is None


@pytest.mark.parametrize("bad_source", ["", "VENUE", "clob", "chain"])
def test_invalid_source_rejected(bad_source):
    conn = _make_conn()
    with pytest.raises(ValueError):
        upsert_wallet_balance_head(
            conn, wallet="0xW", asset="PUSD", balance_micro=1, allowance_micro=1,
            source=bad_source, authority_tier="CHAIN", block_or_source_ts="t0",
        )


@pytest.mark.parametrize("bad_tier", ["", "CLOB", "unknown"])
def test_invalid_authority_tier_rejected(bad_tier):
    conn = _make_conn()
    with pytest.raises(ValueError):
        upsert_wallet_balance_head(
            conn, wallet="0xW", asset="PUSD", balance_micro=1, allowance_micro=1,
            source="CLOB", authority_tier=bad_tier, block_or_source_ts="t0",
        )


def test_missing_wallet_or_asset_rejected():
    conn = _make_conn()
    with pytest.raises(ValueError):
        upsert_wallet_balance_head(
            conn, wallet="", asset="PUSD", balance_micro=1, allowance_micro=1,
            source="CLOB", authority_tier="CHAIN", block_or_source_ts="t0",
        )
    with pytest.raises(ValueError):
        upsert_wallet_balance_head(
            conn, wallet="0xW", asset="", balance_micro=1, allowance_micro=1,
            source="CLOB", authority_tier="CHAIN", block_or_source_ts="t0",
        )
