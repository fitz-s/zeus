# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Attack F
#   ("/positions 漏 token = 幻零仓 -> durable token registry ... /positions only
#   does discovery"); src/state/chain_mirror_reconciler.py::run_cycle
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=never
# Purpose: proves the LX-T2-a discovery hook wired into run_cycle registers
#   every token a data-api /positions read reports, and that a registry write
#   failure never aborts the reconcile pass that already has fresh chain facts.

"""Tests for the ctf_token_registry discovery hook in chain_mirror_reconciler.run_cycle."""

from __future__ import annotations

import sqlite3

import pytest

from src.state.ctf_token_registry import get_token_registry_row
from src.state.db import init_schema, init_schema_trade_only


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_positions_from_api(self):
        return [
            {
                "token_id": "tokY",
                "condition_id": "cond1",
                "size": 10.0,
                "redeemable": False,
                "current_value": 5.0,
                "side": "BUY",
                "title": "yes",
            },
            {
                "token_id": "tokZ",
                "condition_id": "cond2",
                "size": 3.0,
                "redeemable": False,
                "current_value": 1.5,
                "side": "BUY",
                "title": "yes",
            },
        ]


@pytest.fixture
def trades_db_path(tmp_path):
    path = tmp_path / "trades.db"
    conn = sqlite3.connect(str(path))
    init_schema(conn)
    init_schema_trade_only(conn)
    conn.close()
    return path


def _patch_common(monkeypatch, trades_db_path):
    monkeypatch.setattr("src.config.get_mode", lambda: "live")
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _FakeClient)
    monkeypatch.setattr(
        "src.state.db.get_trade_connection", lambda **_kw: sqlite3.connect(str(trades_db_path))
    )

    def _no_forecasts(*_a, **_kw):
        raise RuntimeError("forecasts unavailable in this test")

    monkeypatch.setattr("src.state.db.get_forecasts_connection_read_only", _no_forecasts)


def test_run_cycle_registers_every_positions_token(monkeypatch, trades_db_path):
    from src.state.chain_mirror_reconciler import run_cycle

    _patch_common(monkeypatch, trades_db_path)

    run_cycle()

    conn = sqlite3.connect(str(trades_db_path))
    try:
        tok_y = get_token_registry_row(conn, token_id="tokY")
        tok_z = get_token_registry_row(conn, token_id="tokZ")
    finally:
        conn.close()

    assert tok_y is not None
    assert tok_y.condition_id == "cond1"
    assert tok_y.first_source == "positions_api_discovery"
    assert tok_z is not None
    assert tok_z.condition_id == "cond2"
    assert tok_z.first_source == "positions_api_discovery"


def test_run_cycle_survives_registry_write_failure(monkeypatch, trades_db_path):
    """A registry write failure must never abort the reconcile pass (best-effort)."""
    from src.state.chain_mirror_reconciler import run_cycle

    _patch_common(monkeypatch, trades_db_path)

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated ctf_token_registry write failure")

    monkeypatch.setattr("src.state.ctf_token_registry.record_token_seen", _boom)

    # Must not raise.
    run_cycle()


def test_run_cycle_registers_tokens_already_confirmed_on_rerun(monkeypatch, trades_db_path):
    from src.state.chain_mirror_reconciler import run_cycle

    _patch_common(monkeypatch, trades_db_path)

    run_cycle()
    run_cycle()

    conn = sqlite3.connect(str(trades_db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM ctf_token_registry").fetchone()[0]
    finally:
        conn.close()

    # Never duplicates a row for a token already registered.
    assert count == 2
