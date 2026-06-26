from __future__ import annotations

import json
import sqlite3


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
            assert refresh_allowance is False
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
