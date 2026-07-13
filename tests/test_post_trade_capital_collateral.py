from __future__ import annotations

import json
import sqlite3

import pytest


def test_post_trade_collateral_refresh_publishes_pusd_without_ctf_enumeration(
    monkeypatch,
    tmp_path,
):
    from src.execution import post_trade_capital

    db_path = tmp_path / "trades.db"
    calls = {"pusd": 0, "full": 0}

    class _Adapter:
        def get_pusd_collateral_payload(self, *, refresh_allowance=True):
            calls["pusd"] += 1
            assert refresh_allowance is True
            return {
                "pusd_balance_micro": 12_000_000,
                "pusd_allowance_micro": 12_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances_units": {},
                "ctf_token_allowances_units": {},
                "authority_tier": "CHAIN",
            }

        def get_collateral_payload(self):  # pragma: no cover - tripwire
            calls["full"] += 1
            raise AssertionError("periodic pUSD heartbeat must not enumerate CTF tokens")

    class _Client:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def _ensure_v2_adapter(self):
            return _Adapter()

    monkeypatch.setattr("src.state.db._zeus_trade_db_path", lambda: db_path)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _Client)

    post_trade_capital.collateral_snapshot_refresh_cycle()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT authority_tier, pusd_balance_micro, pusd_allowance_micro,
                   ctf_token_balances_json, ctf_token_allowances_json
              FROM collateral_ledger_snapshots
             ORDER BY id DESC
             LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert calls == {"pusd": 1, "full": 0}
    assert row is not None
    assert row[0] == "CHAIN"
    assert row[1] == 12_000_000
    assert row[2] == 12_000_000
    assert json.loads(row[3]) == {}
    assert json.loads(row[4]) == {}


def test_post_trade_collateral_timeout_does_not_exit_or_publish_fake_snapshot(
    monkeypatch,
    tmp_path,
):
    from src.execution import post_trade_capital
    from src.runtime import timeout_guard as timeout_guard_module

    db_path = tmp_path / "trades.db"
    exit_calls = []

    def _timeout(*_args, **_kwargs):
        raise TimeoutError("simulated pUSD heartbeat timeout")

    monkeypatch.setattr("src.state.db._zeus_trade_db_path", lambda: db_path)
    monkeypatch.setattr(timeout_guard_module, "run_with_timeout", _timeout)
    monkeypatch.setattr(post_trade_capital.os, "_exit", lambda code: exit_calls.append(code))

    with pytest.raises(TimeoutError, match="simulated pUSD heartbeat timeout"):
        post_trade_capital.collateral_snapshot_refresh_cycle()

    assert exit_calls == []
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()
    finally:
        conn.close()
    assert row[0] == 0


def test_post_trade_collateral_refresh_dual_writes_wallet_balance_head(
    monkeypatch,
    tmp_path,
):
    """LX-T2-a: the SAME refresh cycle upserts wallet_balance_head alongside the
    existing collateral_ledger_snapshots insert -- dual-write until LX-3R."""
    from src.execution import post_trade_capital

    db_path = tmp_path / "trades.db"

    class _Adapter:
        funder_address = "0xFUNDER"

        def get_pusd_collateral_payload(self, *, refresh_allowance=True):
            return {
                "pusd_balance_micro": 5_000_000,
                "pusd_allowance_micro": 6_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances_units": {},
                "ctf_token_allowances_units": {},
                "authority_tier": "CHAIN",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def _ensure_v2_adapter(self):
            return _Adapter()

    monkeypatch.setattr("src.state.db._zeus_trade_db_path", lambda: db_path)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _Client)

    post_trade_capital.collateral_snapshot_refresh_cycle()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT wallet, asset, balance_micro, allowance_micro, source, authority_tier
              FROM wallet_balance_head
             WHERE wallet = ? AND asset = 'PUSD'
            """,
            ("0xFUNDER",),
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM wallet_balance_head").fetchone()[0]
    finally:
        conn.close()

    assert count == 1
    assert row == ("0xFUNDER", "PUSD", 5_000_000, 6_000_000, "CLOB", "CHAIN")


def test_post_trade_collateral_refresh_second_cycle_overwrites_head_in_place(
    monkeypatch,
    tmp_path,
):
    """ONE current row per (wallet, asset) across repeated 30s cycles."""
    from src.execution import post_trade_capital

    db_path = tmp_path / "trades.db"
    balances = {"value": 5_000_000}

    class _Adapter:
        funder_address = "0xFUNDER"

        def get_pusd_collateral_payload(self, *, refresh_allowance=True):
            return {
                "pusd_balance_micro": balances["value"],
                "pusd_allowance_micro": balances["value"],
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances_units": {},
                "ctf_token_allowances_units": {},
                "authority_tier": "CHAIN",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def _ensure_v2_adapter(self):
            return _Adapter()

    monkeypatch.setattr("src.state.db._zeus_trade_db_path", lambda: db_path)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _Client)

    post_trade_capital.collateral_snapshot_refresh_cycle()
    balances["value"] = 9_000_000
    post_trade_capital.collateral_snapshot_refresh_cycle()

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM wallet_balance_head").fetchone()[0]
        balance = conn.execute(
            "SELECT balance_micro FROM wallet_balance_head WHERE wallet = ? AND asset = 'PUSD'",
            ("0xFUNDER",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1
    assert balance == 9_000_000


def test_post_trade_collateral_refresh_head_upsert_failure_does_not_break_cycle(
    monkeypatch,
    tmp_path,
):
    """A wallet_balance_head write failure is logged and swallowed -- the
    collateral_ledger_snapshots dual-write history it stands beside is not
    weakened by this best-effort addition."""
    from src.execution import post_trade_capital

    db_path = tmp_path / "trades.db"

    class _Adapter:
        funder_address = "0xFUNDER"

        def get_pusd_collateral_payload(self, *, refresh_allowance=True):
            return {
                "pusd_balance_micro": 1_000_000,
                "pusd_allowance_micro": 1_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances_units": {},
                "ctf_token_allowances_units": {},
                "authority_tier": "CHAIN",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def _ensure_v2_adapter(self):
            return _Adapter()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated wallet_balance_head write failure")

    monkeypatch.setattr("src.state.db._zeus_trade_db_path", lambda: db_path)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _Client)
    monkeypatch.setattr(post_trade_capital, "_upsert_pusd_wallet_balance_head", _boom)

    # Must not raise -- the head write is best-effort, never fatal.
    post_trade_capital.collateral_snapshot_refresh_cycle()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()
    finally:
        conn.close()
    assert row[0] == 1
