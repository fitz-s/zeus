# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: K1 DB split (commit eba80d2b9d, 2026-05-11) — executable_market_snapshots
#   is trade-class (zeus_trades.db); the world.* copy is the empty legacy shadow (drops 2026-08-09).
#   Companion: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §6.
"""Antibody: submit_redeem() resolves negRisk from the AUTHORITATIVE trade-class
executable_market_snapshots (its main zeus_trades.db connection) when the legacy
world.executable_market_snapshots shadow is empty — WITHOUT a Gamma network fallback.

Post-K1 executable_market_snapshots is trade-class: the real rows live in zeus_trades.db
(the submit_redeem main connection); the world.* copy is a 0-row legacy shadow. Before the
fix submit_redeem read ONLY world.executable_market_snapshots, always missed the empty
shadow, and fell through to the Gamma CLOB API on every redeem (and fail-closed to
REDEEM_NEGRISK_FACT_MISSING when Gamma was unreachable, despite the answer being local).
The sibling _lookup_market_neg_risk_authoritative already had the trades Tier-2 guard;
submit_redeem was missed.

Contract: seed the MAIN (trade) connection's executable_market_snapshots with a negRisk
row + leave the world shadow EMPTY -> submit_redeem resolves neg_risk locally, calls the
adapter with neg_risk=True, and NEVER calls the Gamma fallback.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

from src.state.db import init_schema

NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
_CONDITION_ID = "0xfeedface" + "b" * 56

_SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS executable_market_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  condition_id TEXT NOT NULL,
  yes_token_id TEXT NOT NULL DEFAULT '',
  no_token_id TEXT NOT NULL DEFAULT '',
  neg_risk INTEGER NOT NULL DEFAULT 0,
  captured_at TEXT NOT NULL DEFAULT (datetime('now')),
  freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
)
"""


class _FakeAdapter:
    def __init__(self):
        self.calls = []

    def redeem(self, condition_id, *, index_sets=None, neg_risk=False, amount_per_slot=None, **_kw):
        self.calls.append({"condition_id": condition_id, "neg_risk": neg_risk, "amount_per_slot": amount_per_slot})
        return {"success": True, "tx_hash": "0xdeadbeef"}


def _insert_command(conn, condition_id):
    from src.execution.settlement_commands import request_redeem
    cmd_id = request_redeem(
        condition_id,
        "USDC",
        market_id=condition_id,
        token_amounts={"yes-token-123": "1.5"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    conn.commit()
    return cmd_id


def test_negrisk_resolved_from_trades_when_world_shadow_empty(monkeypatch):
    import src.execution.settlement_commands as sc
    import src.state.db as _db_mod

    monkeypatch.setattr(
        sc, "redemption_decision",
        lambda: SimpleNamespace(allow_redemption=True, block_reason=None, state="LIVE_ENABLED"),
    )
    monkeypatch.setattr(sc, "require_pusd_redemption_allowed", lambda fx: fx)

    # Gamma network fallback MUST NOT be reached when the local trades snapshot has the row.
    gamma_calls = []

    def _record_gamma(condition_id):
        gamma_calls.append(condition_id)
        return None

    monkeypatch.setattr(sc, "_fetch_neg_risk_from_gamma_for_submitter", _record_gamma)

    # Main (trade) connection = authoritative executable_market_snapshots owner (zeus_trades.db).
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.row_factory = sqlite3.Row
    init_schema(trade_conn)
    # init_schema creates a world-class executable_market_snapshots ghost with NOT-NULL
    # columns lacking defaults; replace it with the minimal trade-class shape for this test.
    trade_conn.execute("DROP TABLE IF EXISTS executable_market_snapshots")
    trade_conn.execute(_SNAPSHOT_DDL)
    trade_conn.execute(
        "INSERT OR REPLACE INTO executable_market_snapshots "
        "(snapshot_id, condition_id, yes_token_id, no_token_id, neg_risk) "
        "VALUES (?, ?, 'yes-token-123', 'no-token-456', 1)",
        (f"snap-{_CONDITION_ID}", _CONDITION_ID),
    )
    trade_conn.commit()

    # Empty world shadow (table present, ZERO rows) — mirrors post-K1 reality.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        world_tmp = f.name
    original_path = _db_mod.ZEUS_WORLD_DB_PATH
    try:
        wc = sqlite3.connect(world_tmp)
        wc.execute(_SNAPSHOT_DDL)  # table exists, no rows
        wc.commit()
        wc.close()
        _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_tmp)

        cmd_id = _insert_command(trade_conn, _CONDITION_ID)
        adapter = _FakeAdapter()

        sc.submit_redeem(cmd_id, adapter, SimpleNamespace(), conn=trade_conn)

        assert adapter.calls, (
            "neg_risk must resolve from the local trade-class executable_market_snapshots "
            "(zeus_trades.db) when the world shadow is empty — adapter was never called, "
            "so submit_redeem missed the local snapshot and fell through to FACT_MISSING"
        )
        assert adapter.calls[0]["neg_risk"] is True
        assert not gamma_calls, (
            "Gamma network fallback must NOT be called when the authoritative trades snapshot "
            f"holds the row; gamma_calls={gamma_calls!r}"
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = original_path
        trade_conn.close()
        try:
            os.unlink(world_tmp)
        except OSError:
            pass
