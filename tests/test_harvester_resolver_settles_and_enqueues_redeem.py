# Created: 2026-06-03
# Last reused or audited: 2026-07-25
# Authority basis: 守護 blocker — settlement_outcomes (VERIFIED truth) -> resolver ->
#   position settled. Relationship test across the
#   settlement_outcomes -> position_current boundary that the
#   "harvester unscheduled in EDLI" bug left dead (memory #56 Shanghai cca68b44).
# Lifecycle: created=2026-06-03; last_reviewed=2026-07-25; last_reused=2026-07-25
# Purpose: Cross-module relationship invariant — when a position's target_date has a
#   VERIFIED settlement_outcomes row, running the resolver marks the position settled.
# Reuse: inspect src/engine/harvest_cycle.py:_resolve_settlements and
#   src/state/db.py settlement_outcomes/position_current tables
#   before re-running; verify zeus-forecasts.db and zeus_trades.db schemas match.
# 2026-07-25 update: on-chain redemption decoupled entirely (Zeus no longer
#   submits redeem transactions; Polymarket settles win/loss on our behalf).
#   test_resolver_settles_position_and_enqueues_redeem_intent asserted a
#   REDEEM_INTENT_CREATED row was enqueued — removed, since enqueue_redeem_command
#   was deleted from src/execution/harvester.py. The remaining tests in this file
#   are independent of redeem/settlement_commands and are unchanged.
"""Relationship test: resolver consumes VERIFIED settlement truth -> settle.

This crosses the exact boundary the scheduling bug broke:
  forecasts.settlement_outcomes (VERIFIED)  ->  trade.position_current (settled)

Without the harvester scheduled, this whole chain never fires in EDLI modes.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from src.state.db import init_schema


@pytest.fixture()
def trade_conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
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


def test_missing_optional_named_column_does_not_fall_through_to_position():
    """Legacy SQLite rows must default fields added only to Gamma dict rows."""
    from src.execution.harvester_pnl_resolver import _row_value

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT 'city' AS city, 'date' AS target_date, 'slug' AS market_slug, "
        "'bin' AS winning_bin, 'high' AS temperature_metric, "
        "'VERIFIED' AS authority, 'wu' AS settlement_source, 27.0 AS settlement_value"
    ).fetchone()
    db.close()

    assert _row_value(row, "settlement_scope", 8, "family") == "family"


def test_resolver_settles_position_when_verified_settlement_present(
    trade_conn, forecasts_conn_with_verified_settlement, monkeypatch
):
    """VERIFIED settlement_outcomes row + matching winning position
    → resolver marks settled.

    RED proof: if the harvester never runs (the scheduling bug), no
    position ever gets settled for a VERIFIED settlement_outcomes row.
    This test fires the resolver directly and asserts the settle side fires.
    """
    monkeypatch.setenv("ZEUS_HARVESTER_LIVE_ENABLED", "1")

    import src.execution.harvester_pnl_resolver as resolver
    import src.execution.harvester as hv

    portfolio, pos = _winning_position()

    # Resolver loads/saves portfolio + tracker via state helpers — stub them so
    # the test isolates the settlement_outcomes -> settle boundary.
    monkeypatch.setattr("src.state.portfolio.load_portfolio", lambda *a, **kw: portfolio)
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.strategy_tracker.get_tracker", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.state.canonical_write.commit_then_export",
        lambda conn, *, db_op, json_exports: db_op(),
    )
    monkeypatch.setattr("src.state.decision_chain.store_settlement_records", lambda *a, **kw: None)

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
    monkeypatch.setattr(hv, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "record_token_suppression", lambda *a, **kw: {"status": "written"})
    # Downstream settlement-event writers persist many position attributes into real
    # tables; with a MagicMock position those bind MagicMock objects into SQL. They
    # are exercised by their own tests — stub them so this relationship test isolates
    # the settlement_outcomes -> settle boundary only.
    monkeypatch.setattr(hv, "log_settlement_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "_dual_write_canonical_settlement_if_available", lambda *a, **kw: None)

    result = resolver.resolve_pnl_for_settled_markets(
        trade_conn, forecasts_conn_with_verified_settlement
    )

    assert result["status"] == "ok", f"resolver did not run cleanly: {result!r}"
    assert result["positions_settled"] >= 1, (
        f"VERIFIED settlement present but no position settled: {result!r}"
    )


def test_exact_venue_resolution_is_economic_truth_when_hourly_obs_disagrees(monkeypatch):
    """Paris Jul-14 regression: Gamma resolved 35C YES while hourly WU peaked at 34C.

    The observation disagreement must remain excluded from calibration, but it
    cannot keep an economically lost NO position open or mark it as a win.
    """
    from src.execution import harvester_pnl_resolver as resolver

    position = MagicMock()
    position.city = "Paris"
    position.target_date = "2026-07-14"
    position.temperature_metric = "high"
    position.condition_id = (
        "0x1c62cc01e6c524b2d16efe080c8c3153a9fb0b13ee0e0133d4e4f5d42dc6bcad"
    )
    portfolio = MagicMock(positions=[position])

    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(
        position.condition_id,
        "highest-temperature-in-paris-on-july-14-2026",
    )]
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = [{
        "slug": "highest-temperature-in-paris-on-july-14-2026",
        "title": "Highest temperature in Paris on July 14?",
        "closed": True,
        "markets": [{
            "conditionId": position.condition_id,
            "question": "Will the highest temperature in Paris be 35°C on July 14?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["1", "0"]',
            "clobTokenIds": '["yes-token", "no-token"]',
            "umaResolutionStatus": "resolved",
        }],
    }]
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: response)

    rows = resolver._read_venue_resolved_settlement_rows(
        conn,
        portfolio,
        {("Paris", "2026-07-14", "high")},
    )

    assert rows == [{
        "city": "Paris",
        "target_date": "2026-07-14",
        "market_slug": "highest-temperature-in-paris-on-july-14-2026",
        "winning_bin": "35°C",
        "temperature_metric": "high",
        "authority": "VENUE_RESOLVED",
        "settlement_source": "polymarket_gamma",
        "settlement_value": None,
    }]


def test_partial_parent_resolution_emits_exact_held_condition_truth(monkeypatch):
    """A resolved child is economic truth even while its parent event stays open.

    Weather events can publish binary child payouts one by one. Requiring the
    parent event to close leaves already-final held conditions in day0_window.
    The resolver may consume the exact held child, but must not invent a family
    winning bin while another child remains unresolved.
    """
    from src.execution import harvester_pnl_resolver as resolver

    condition_id = "0x" + "a" * 64
    unresolved_id = "0x" + "b" * 64
    position = MagicMock(
        city="Cape Town",
        target_date="2026-07-24",
        temperature_metric="high",
        condition_id=condition_id,
    )
    portfolio = MagicMock(positions=[position])

    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(
        condition_id,
        "highest-temperature-in-cape-town-on-july-24-2026",
    )]
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = [{
        "slug": "highest-temperature-in-cape-town-on-july-24-2026",
        "title": "Highest temperature in Cape Town on July 24?",
        "closed": False,
        "markets": [
            {
                "conditionId": condition_id,
                "question": "Will the highest temperature in Cape Town be 17°C on July 24?",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0", "1"]',
                "clobTokenIds": '["yes-token", "no-token"]',
                "umaResolutionStatus": "resolved",
            },
            {
                "conditionId": unresolved_id,
                "question": "Will the highest temperature in Cape Town be 18°C on July 24?",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.5", "0.5"]',
                "clobTokenIds": '["yes-token-2", "no-token-2"]',
                "umaResolutionStatus": "proposed",
            },
        ],
    }]
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: response)

    rows = resolver._read_venue_resolved_settlement_rows(
        conn,
        portfolio,
        {("Cape Town", "2026-07-24", "high")},
    )

    assert rows == [{
        "city": "Cape Town",
        "target_date": "2026-07-24",
        "market_slug": "highest-temperature-in-cape-town-on-july-24-2026",
        "winning_bin": None,
        "temperature_metric": "high",
        "authority": "VENUE_RESOLVED",
        "settlement_source": "polymarket_gamma",
        "settlement_value": None,
        "settlement_scope": "condition",
        "condition_id": condition_id,
        "condition_yes_won": False,
    }]


def test_exact_condition_no_settles_only_matching_position(trade_conn, monkeypatch):
    """A child-NO resolution settles only that condition, never sibling bins."""
    import src.execution.exit_lifecycle as el
    import src.execution.harvester as hv

    portfolio, losing_yes = _winning_position(
        trade_id="cape-17-yes",
        city="Cape Town",
        target_date="2026-07-24",
    )
    losing_yes.condition_id = "0x" + "c" * 64
    losing_yes.bin_label = "17°C"
    losing_yes.direction = "buy_yes"
    losing_yes.has_fill_economics_authority = True
    losing_yes.effective_shares = 2.0
    losing_yes.effective_cost_basis_usd = 1.0

    _, unresolved_no = _winning_position(
        trade_id="cape-19-no",
        city="Cape Town",
        target_date="2026-07-24",
    )
    unresolved_no.condition_id = "0x" + "d" * 64
    unresolved_no.bin_label = "19°C"
    unresolved_no.direction = "buy_no"
    unresolved_no.has_fill_economics_authority = True
    unresolved_no.effective_shares = 2.0
    unresolved_no.effective_cost_basis_usd = 1.0
    portfolio.positions.append(unresolved_no)

    settled_calls = []

    def _mark_settled(_portfolio, trade_id, settlement_price, reason):
        settled_calls.append((trade_id, settlement_price, reason))
        closed = MagicMock()
        closed.trade_id = trade_id
        closed.pnl = -1.0
        closed.bin_label = losing_yes.bin_label
        closed.direction = losing_yes.direction
        closed.p_posterior = losing_yes.p_posterior
        closed.decision_snapshot_id = ""
        closed.edge_source = "model"
        closed.strategy = "default"
        closed.last_exit_at = "2026-07-24T22:00:00Z"
        closed.exit_price = settlement_price
        return closed

    monkeypatch.setattr(el, "mark_settled", _mark_settled)
    monkeypatch.setattr(hv, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "log_settlement_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "_dual_write_canonical_settlement_if_available", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "record_token_suppression", lambda *a, **kw: {"status": "written"})

    settled = hv._settle_positions(
        trade_conn,
        portfolio,
        "Cape Town",
        "2026-07-24",
        "",
        settlement_authority="VENUE_RESOLVED",
        settlement_truth_source="gamma_exact_held_condition",
        settlement_market_slug="highest-temperature-in-cape-town-on-july-24-2026",
        settlement_temperature_metric="high",
        settlement_source="polymarket_gamma",
        settlement_condition_id=losing_yes.condition_id,
        settlement_condition_yes_won=False,
    )

    assert settled == 1
    assert settled_calls == [("cape-17-yes", 0.0, "SETTLEMENT")]
