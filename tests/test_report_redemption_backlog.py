# Lifecycle: created=2026-07-25; last_reviewed=2026-07-25; last_reused=never
# Purpose: Unit-test the redemption-backlog report's aggregation logic against
#   a tiny in-memory sqlite fixture carrying the settlement_commands /
#   position_current schema subset it reads.
# Reuse: Update fixture schema alongside any settlement_commands /
#   position_current column changes this report depends on.
"""Aggregation-logic tests for scripts/report_redemption_backlog.py."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from scripts.report_redemption_backlog import (
    ACTIONABLE_STATE,
    MICRO,
    STALE_STATE,
    aggregate,
    fetch_recent_matched_taker_fills,
    fetch_redemption_rows,
    fetch_wallet_address,
    verify_fee_cash_effect,
)

NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE settlement_commands (
            command_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            market_id TEXT NOT NULL,
            payout_asset TEXT NOT NULL,
            pusd_amount_micro INTEGER,
            token_amounts_json TEXT,
            requested_at TEXT NOT NULL,
            submitted_at TEXT,
            error_payload TEXT
        );
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            temperature_metric TEXT,
            direction TEXT,
            updated_at TEXT
        );
        CREATE TABLE wallet_balance_head (
            wallet TEXT,
            asset TEXT
        );
        CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            filled_size TEXT NOT NULL,
            fill_price TEXT NOT NULL,
            fee_paid_micro INTEGER,
            observed_at TEXT NOT NULL,
            raw_payload_json TEXT
        );
        CREATE TABLE collateral_ledger_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pusd_balance_micro INTEGER NOT NULL,
            captured_at TEXT NOT NULL,
            authority_tier TEXT NOT NULL
        );
        """
    )
    return conn


def _insert_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    state: str,
    condition_id: str,
    pusd_amount_micro: int,
    requested_at: str,
    token_ids: list[str] | None = None,
) -> None:
    token_amounts = {tid: 1.0 for tid in (token_ids or [f"tok-{command_id}"])}
    conn.execute(
        """
        INSERT INTO settlement_commands
            (command_id, state, condition_id, market_id, payout_asset,
             pusd_amount_micro, token_amounts_json, requested_at, submitted_at, error_payload)
        VALUES (?, ?, ?, ?, 'pUSD', ?, ?, ?, NULL, NULL)
        """,
        (command_id, state, condition_id, condition_id, pusd_amount_micro, json.dumps(token_amounts), requested_at),
    )


def test_aggregate_splits_actionable_and_stale_and_sorts_desc() -> None:
    conn = _conn()
    _insert_command(
        conn,
        command_id="a",
        state=ACTIONABLE_STATE,
        condition_id="0xaaa",
        pusd_amount_micro=5_000_000,
        requested_at="2026-07-15T00:00:00+00:00",  # 10.5 days before NOW (12:00 on the 25th)
    )
    _insert_command(
        conn,
        command_id="b",
        state=ACTIONABLE_STATE,
        condition_id="0xbbb",
        pusd_amount_micro=10_000_000,
        requested_at="2026-07-20T00:00:00+00:00",  # 5.5 days before NOW
    )
    _insert_command(
        conn,
        command_id="c",
        state=STALE_STATE,
        condition_id="0xccc",
        pusd_amount_micro=1_000_000,
        requested_at="2026-06-10T00:00:00+00:00",
    )
    conn.execute("INSERT INTO wallet_balance_head (wallet, asset) VALUES ('0xWALLET', 'pUSD')")
    conn.commit()

    rows, warnings = fetch_redemption_rows(conn, now=NOW)
    # No position_current rows exist at all in this fixture -- expect the
    # "no descriptions" warning, not a query failure.
    assert warnings == [
        "position_current lookup returned no rows -- city/bin descriptions may be missing "
        "(query failed or no positions matched)."
    ]
    actionable, stale, totals = aggregate(rows)

    # sorted by amount desc
    assert [r.command_id for r in actionable] == ["b", "a"]
    assert [r.command_id for r in stale] == ["c"]

    assert totals.actionable_count == 2
    assert totals.actionable_total_usd == 15.0
    # dollar-days = 5*10.5 + 10*5.5 = 107.5
    assert totals.actionable_dollar_days == 107.5
    assert totals.stale_count == 1
    assert totals.stale_total_usd == 1.0

    # stale rows never contribute to actionable totals
    assert all(r.state == ACTIONABLE_STATE for r in actionable)
    assert all(r.state == STALE_STATE for r in stale)


def test_position_current_join_supplies_description_and_missing_match_falls_back() -> None:
    conn = _conn()
    _insert_command(
        conn,
        command_id="matched",
        state=ACTIONABLE_STATE,
        condition_id="0xmatched",
        pusd_amount_micro=2_000_000,
        requested_at="2026-07-24T00:00:00+00:00",
    )
    _insert_command(
        conn,
        command_id="unmatched",
        state=ACTIONABLE_STATE,
        condition_id="0xunmatched",
        pusd_amount_micro=1_000_000,
        requested_at="2026-07-24T00:00:00+00:00",
    )
    conn.execute(
        """
        INSERT INTO position_current
            (position_id, condition_id, city, target_date, bin_label, temperature_metric, direction, updated_at)
        VALUES ('p1', '0xmatched', 'Wuhan', '2026-07-20', 'Will the highest temperature in Wuhan be 33C?', 'high', 'buy_no', '2026-07-20T00:00:00+00:00')
        """
    )
    conn.commit()

    rows, warnings = fetch_redemption_rows(conn, now=NOW)
    assert warnings == []
    by_id = {r.command_id: r for r in rows}
    assert "Wuhan" in by_id["matched"].description
    assert "no position_current match" in by_id["unmatched"].description


def test_duplicate_position_current_rows_pick_most_recently_updated() -> None:
    conn = _conn()
    _insert_command(
        conn,
        command_id="dup",
        state=ACTIONABLE_STATE,
        condition_id="0xdup",
        pusd_amount_micro=1_000_000,
        requested_at="2026-07-24T00:00:00+00:00",
    )
    conn.executemany(
        """
        INSERT INTO position_current
            (position_id, condition_id, city, target_date, bin_label, temperature_metric, direction, updated_at)
        VALUES (?, '0xdup', ?, '2026-07-20', ?, 'high', 'buy_no', ?)
        """,
        [
            ("p1", "Wuhan", "old label", "2026-07-20T00:00:00+00:00"),
            ("p2", "Wuhan", "new label", "2026-07-21T00:00:00+00:00"),
        ],
    )
    conn.commit()

    rows, _ = fetch_redemption_rows(conn, now=NOW)
    assert "new label" in rows[0].description
    assert "old label" not in rows[0].description


def test_fetch_wallet_address_single_and_none() -> None:
    conn = _conn()
    assert fetch_wallet_address(conn) == "unknown"
    conn.execute("INSERT INTO wallet_balance_head (wallet, asset) VALUES ('0xWALLET', 'pUSD')")
    conn.execute("INSERT INTO wallet_balance_head (wallet, asset) VALUES ('0xWALLET', 'USDC')")
    conn.commit()
    assert fetch_wallet_address(conn) == "0xWALLET"


def test_token_ids_extracted_from_token_amounts_json() -> None:
    conn = _conn()
    _insert_command(
        conn,
        command_id="t",
        state=ACTIONABLE_STATE,
        condition_id="0xt",
        pusd_amount_micro=1_000_000,
        requested_at="2026-07-24T00:00:00+00:00",
        token_ids=["111", "222"],
    )
    conn.commit()
    rows, _ = fetch_redemption_rows(conn, now=NOW)
    assert sorted(rows[0].token_ids) == ["111", "222"]


def test_fetch_redemption_rows_degrades_when_settlement_commands_query_fails() -> None:
    conn = _conn()
    conn.execute("DROP TABLE settlement_commands")
    rows, warnings = fetch_redemption_rows(conn, now=NOW)
    assert rows == []
    assert any("settlement_commands" in w for w in warnings)


# --- D3: fee cash-effect verification ---------------------------------------

from src.contracts.execution_price import polymarket_fee  # noqa: E402


def _insert_fill(
    conn: sqlite3.Connection,
    *,
    trade_fact_id: int,
    observed_at: str,
    filled_size: float,
    fill_price: float,
    trader_side: str | None = "TAKER",
) -> None:
    payload = json.dumps({"trader_side": trader_side}) if trader_side is not None else None
    conn.execute(
        """
        INSERT INTO venue_trade_facts (trade_fact_id, state, filled_size, fill_price, fee_paid_micro, observed_at, raw_payload_json)
        VALUES (?, 'MATCHED', ?, ?, NULL, ?, ?)
        """,
        (trade_fact_id, str(filled_size), str(fill_price), observed_at, payload),
    )


def _insert_snapshot(conn: sqlite3.Connection, *, balance_micro: int, captured_at: str, tier: str = "CHAIN") -> None:
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots (pusd_balance_micro, captured_at, authority_tier) VALUES (?, ?, ?)",
        (balance_micro, captured_at, tier),
    )


def test_fetch_recent_matched_taker_fills_filters_by_trader_side() -> None:
    conn = _conn()
    _insert_fill(conn, trade_fact_id=1, observed_at="2026-07-25T10:00:00+00:00", filled_size=5, fill_price=0.5, trader_side="MAKER")
    _insert_fill(conn, trade_fact_id=2, observed_at="2026-07-25T10:01:00+00:00", filled_size=5, fill_price=0.5, trader_side="TAKER")
    conn.commit()
    fills, scanned = fetch_recent_matched_taker_fills(conn, limit=5)
    assert scanned == 2
    assert [f["trade_fact_id"] for f in fills] == [2]


def test_verify_fee_cash_effect_matches_fee_inclusive_clean_window() -> None:
    conn = _conn()
    price = 0.60
    size = 10.0
    fee_total = size * polymarket_fee(price)
    _insert_snapshot(conn, balance_micro=1_000_000_000, captured_at="2026-07-25T10:00:00+00:00")
    _insert_fill(conn, trade_fact_id=1, observed_at="2026-07-25T10:00:30+00:00", filled_size=size, fill_price=price)
    after_micro = 1_000_000_000 - round((size * price + fee_total) * MICRO)
    _insert_snapshot(conn, balance_micro=after_micro, captured_at="2026-07-25T10:01:00+00:00")
    conn.commit()

    rows, warnings = verify_fee_cash_effect(conn, limit=1)
    assert warnings == []
    assert len(rows) == 1
    assert rows[0].window_clean is True
    assert abs(rows[0].observed_delta_usd - rows[0].expected_with_fee_usd) < 0.001
    assert abs(rows[0].observed_delta_usd - rows[0].expected_no_fee_usd) > 0.01


def test_verify_fee_cash_effect_matches_zero_fee_clean_window() -> None:
    conn = _conn()
    price = 0.60
    size = 10.0
    _insert_snapshot(conn, balance_micro=1_000_000_000, captured_at="2026-07-25T10:00:00+00:00")
    _insert_fill(conn, trade_fact_id=1, observed_at="2026-07-25T10:00:30+00:00", filled_size=size, fill_price=price)
    after_micro = 1_000_000_000 - round(size * price * MICRO)
    _insert_snapshot(conn, balance_micro=after_micro, captured_at="2026-07-25T10:01:00+00:00")
    conn.commit()

    rows, _ = verify_fee_cash_effect(conn, limit=1)
    assert rows[0].window_clean is True
    assert abs(rows[0].observed_delta_usd - rows[0].expected_no_fee_usd) < 0.001


def test_verify_fee_cash_effect_flags_unclean_window_when_another_fill_overlaps() -> None:
    conn = _conn()
    _insert_snapshot(conn, balance_micro=1_000_000_000, captured_at="2026-07-25T10:00:00+00:00")
    _insert_fill(conn, trade_fact_id=1, observed_at="2026-07-25T10:00:20+00:00", filled_size=5, fill_price=0.5)
    _insert_fill(conn, trade_fact_id=2, observed_at="2026-07-25T10:00:40+00:00", filled_size=5, fill_price=0.5)
    _insert_snapshot(conn, balance_micro=990_000_000, captured_at="2026-07-25T10:01:00+00:00")
    conn.commit()

    rows, _ = verify_fee_cash_effect(conn, limit=2)
    assert all(r.window_clean is False for r in rows)


def test_verify_fee_cash_effect_empty_when_no_taker_fills() -> None:
    conn = _conn()
    rows, warnings = verify_fee_cash_effect(conn, limit=5)
    assert rows == []
    assert any("no MATCHED TAKER" in w for w in warnings)
