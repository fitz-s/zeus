# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: 守護 blocker — settlement_outcomes (VERIFIED truth) -> resolver ->
#   position settled + REDEEM_INTENT enqueued. Relationship test across the
#   settlement_outcomes -> position_current -> settlement_commands boundary that the
#   "harvester unscheduled in EDLI" bug left dead (memory #56 Shanghai cca68b44).
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Cross-module relationship invariant — when a position's target_date has a
#   VERIFIED settlement_outcomes row, running the resolver marks the position settled
#   AND enqueues a REDEEM_INTENT_CREATED row (a winning position is claimable).
# Reuse: inspect src/engine/harvest_cycle.py:_resolve_settlements and
#   src/state/db.py settlement_outcomes/position_current/settlement_commands tables
#   before re-running; verify zeus-forecasts.db and zeus_trades.db schemas match.
"""Relationship test: resolver consumes VERIFIED settlement truth -> settle + redeem.

This crosses the exact boundary the scheduling bug broke:
  forecasts.settlement_outcomes (VERIFIED)  ->  trade.position_current (settled)
                                            ->  trade.settlement_commands (REDEEM_INTENT_CREATED)

Without the harvester scheduled, this whole chain never fires in EDLI modes: the
redeem pollers (consumers) sit idle because their producer never enqueues.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from src.state.db import init_schema
from src.execution.settlement_commands import (
    SettlementState,
    init_settlement_command_schema,
)


@pytest.fixture()
def trade_conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_settlement_command_schema(db)
    yield db
    db.close()


@pytest.fixture()
def forecasts_conn_with_verified_settlement():
    """In-memory forecasts conn holding ONE VERIFIED settlement_outcomes row."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT,
            target_date TEXT,
            market_slug TEXT,
            winning_bin TEXT,
            temperature_metric TEXT,
            authority TEXT,
            settlement_source TEXT,
            settlement_value REAL,
            settled_at TEXT
        )
        """
    )
    db.execute(
        "INSERT INTO settlement_outcomes "
        "(city, target_date, market_slug, winning_bin, temperature_metric, authority, "
        " settlement_source, settlement_value, settled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "Shanghai", "2026-05-29", "shanghai-high-2026-05-29",
            "27-28°C", "high", "VERIFIED", "wu_icao", 27.0,
            "2026-06-03T18:46:00Z",
        ),
    )
    db.commit()
    yield db
    db.close()


def _winning_position(trade_id="cca68b44", city="Shanghai", target_date="2026-05-29"):
    """A winning buy_yes position on the settled bin → claimable → redeem enqueued."""
    pos = MagicMock()
    pos.trade_id = trade_id
    pos.city = city
    pos.target_date = target_date
    pos.direction = "buy_yes"
    pos.condition_id = "0xshanghai_cond_" + "a" * 40
    pos.token_id = "tok-yes-shanghai"
    pos.no_token_id = None
    pos.entry_price = 0.5
    pos.size_usd = 1.0
    pos.cost_basis_usd = 1.0
    pos.shares = 2.0
    pos.p_posterior = 0.7
    pos.bin_label = "27-28°C"          # matches winning_bin → won
    pos.exit_price = None
    pos.entry_method = "model"
    pos.selected_method = "model"
    pos.decision_snapshot_id = ""
    pos.edge_source = "model"
    pos.strategy = "default"
    pos.last_exit_at = "2026-05-29T18:00:00Z"
    pos.market_id = pos.condition_id
    pos.state = "active"
    pos.exit_state = ""
    pos.chain_state = ""
    pos.temperature_metric = "high"
    # _settlement_economics_for_position guard: keep the clean shares/cost_basis path.
    # MagicMock auto-attrs would read truthy and trip the non-fill-economics guard,
    # so every checked attribute is pinned to a falsy/empty value here.
    pos.has_fill_economics_authority = False
    pos.entry_economics_authority = ""
    pos.fill_authority = ""
    pos.corrected_executable_economics_eligible = False
    pos.pricing_semantics_id = ""
    pos.entry_cost_basis_hash = ""
    pos.execution_cost_basis_version = ""
    portfolio = MagicMock()
    portfolio.positions = [pos]
    portfolio.ignored_tokens = []
    return portfolio, pos


def test_resolver_settles_position_and_enqueues_redeem_intent(
    trade_conn, forecasts_conn_with_verified_settlement, monkeypatch
):
    """VERIFIED settlement_outcomes row + matching winning position
    → resolver marks settled AND enqueues REDEEM_INTENT_CREATED.

    RED proof: if the harvester never runs (the scheduling bug), no
    REDEEM_INTENT_CREATED row ever exists for the position — the redeem
    pollers have nothing to consume. This test fires the resolver directly
    and asserts the producer side emits the intent.
    """
    monkeypatch.setenv("ZEUS_HARVESTER_LIVE_ENABLED", "1")

    import src.execution.harvester_pnl_resolver as resolver
    import src.execution.harvester as hv

    portfolio, pos = _winning_position()

    # Resolver loads/saves portfolio + tracker via state helpers — stub them so
    # the test isolates the settlement_outcomes -> settle -> redeem boundary.
    monkeypatch.setattr("src.state.portfolio.load_portfolio", lambda *a, **kw: portfolio)
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.strategy_tracker.get_tracker", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.state.canonical_write.commit_then_export",
        lambda conn, *, db_op, json_exports: db_op(),
    )
    monkeypatch.setattr("src.state.decision_chain.store_settlement_records", lambda *a, **kw: None)

    # enqueue_redeem_command looks up an anchor from world.decision_events; in this
    # isolated test there is no world DB → make that lookup a clean no-op so the
    # redeem command is enqueued via the real request_redeem path on trade_conn.
    monkeypatch.setattr(
        hv, "get_world_connection",
        lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("no world db in test")),
    )

    # Canonical exit path uses mark_settled; stub to a deterministic closed record.
    closed = MagicMock()
    closed.trade_id = pos.trade_id
    closed.pnl = 1.0
    closed.bin_label = pos.bin_label
    closed.direction = pos.direction
    closed.p_posterior = pos.p_posterior
    closed.decision_snapshot_id = ""
    closed.edge_source = "model"
    closed.strategy = "default"
    closed.last_exit_at = pos.last_exit_at
    closed.exit_price = 1.0
    import src.execution.exit_lifecycle as el
    monkeypatch.setattr(el, "mark_settled", lambda *a, **kw: closed)
    monkeypatch.setattr(hv, "_get_canonical_exit_flag", lambda: True)
    monkeypatch.setattr(hv, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "record_token_suppression", lambda *a, **kw: {"status": "written"})
    # Downstream settlement-event writers persist many position attributes into real
    # tables; with a MagicMock position those bind MagicMock objects into SQL. They
    # are exercised by their own tests — stub them so this relationship test isolates
    # the settlement_outcomes -> settle -> REDEEM_INTENT boundary only.
    monkeypatch.setattr(hv, "log_settlement_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "_dual_write_canonical_settlement_if_available", lambda *a, **kw: None)

    result = resolver.resolve_pnl_for_settled_markets(
        trade_conn, forecasts_conn_with_verified_settlement
    )

    assert result["status"] == "ok", f"resolver did not run cleanly: {result!r}"
    assert result["positions_settled"] >= 1, (
        f"VERIFIED settlement present but no position settled: {result!r}"
    )

    # The producer-side invariant the bug killed: a REDEEM_INTENT_CREATED row
    # now exists in settlement_commands for the winning position.
    rows = trade_conn.execute(
        "SELECT state, condition_id FROM settlement_commands WHERE condition_id = ?",
        (pos.condition_id,),
    ).fetchall()
    assert rows, "no settlement_commands row enqueued for the winning settled position"
    assert any(r["state"] == SettlementState.REDEEM_INTENT_CREATED.value for r in rows), (
        f"expected a REDEEM_INTENT_CREATED row; got states "
        f"{[r['state'] for r in rows]!r}"
    )
